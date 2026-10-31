"""Full-screen transparent overlay window that hosts the pet."""

from __future__ import annotations

import ctypes
import os
import sys
import time

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from ..config import SIZE_PRESETS, Config, load, save, scale_for
from ..llm import config as llm_config
from ..llm import templates as llm_templates
from ..rendering.hit import hit_test
from ..rendering.canvas import PetCanvas
from ..simulation.state import SimulationState
from .fixed_loop import DEFAULT_MAX_SUBSTEPS, GAME_DT, FixedStepLoop
from .workspace import ScreenWorkspace
from . import cursorfx, windows_overlay


def cursor_sim_point(
    global_pos,
    origin,
    screen_rect,
    scale: float,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    """Map a global cursor position into SIMULATION coordinates, or ``None``.

    The simulation runs in game pixels and ``scale`` maps those onto screen
    pixels (``rendering/canvas.py``), so a screen offset has to be DIVIDED by
    ``scale`` before ``SimulationState.set_cursor`` sees it. Feeding screen
    pixels straight in -- which this used to do -- inflates every cursor
    distance by ``scale`` (1.5x on this machine): ``SCORE_TALK_RANGE`` only
    caught a cursor that was physically 1.5x closer than intended, and
    ``investigate`` aimed at ``cursor + 150 px`` measured in the wrong unit.

    Containment is tested against the SCREEN, not the window. The pet window is
    ``availableGeometry()``, so the taskbar strip (48 px tall here) read as "no
    cursor at all" even with the mouse sitting in it; that forced the selector
    into its no-cursor branch on every trip past the bottom edge. The point is
    clamped into the chamber, which is where the game would put it as well.
    """
    # QMouseEvent 给的是 QPointF，QRect.contains 只吃 QPoint → 统一成分数坐标再比
    if screen_rect is not None and not screen_rect.contains(
        global_pos.toPoint() if hasattr(global_pos, "toPoint") else global_pos
    ):
        return None
    zoom = max(1e-6, float(scale))
    x = (global_pos.x() - origin.x()) / zoom
    y = (global_pos.y() - origin.y()) / zoom
    bx, by, bw, bh = bounds
    return (min(max(x, bx), bx + bw), min(max(y, by), by + bh))


class PetWindow(PetCanvas):
    closed = Signal()

    def __init__(self, simulation: SimulationState, config: Config, parent=None, sprites=None):
        # Rain World's native cadence: every oracle constant is per 40 Hz tick.
        self._loop = FixedStepLoop(dt=GAME_DT, max_substeps=DEFAULT_MAX_SUBSTEPS)
        super().__init__(
            simulation,
            alpha_provider=lambda: self._loop.alpha,
            parent=parent,
            scale=config.scale,
            sprites=sprites,
        )
        self.config = config
        # LLM 的连接与开关在独立的 llm.json（见 llm/config.py）；这里只持有读写器，
        # P1 阶段还没有任何 tick 级调用 —— 关掉时对仿真完全无影响（审计 A1 的前提）。
        self.llm = llm_config.LLMStore()
        # 审计 A8：玩家要"自己改 JSON"，那就得第一次启动就能看见这些文件
        llm_templates.ensure_template_files()
        self.llm.install_default_file()   # 先补模板再读，state() 的 exists 才不会骗人
        self.llm.load()
        self.llm.upgrade()                # 老文件缺的新字段补上（只加不改）
        self.dialogue_enabled = bool(getattr(config, 'dialogue_enabled', True))
        # LLM 路由器交给仿真持有（仿真每 tick 用；这里只负责造出来和换角色）。
        # 没 key / 没模型时 `router.enabled` 为假，仿真会走本地台词池，一行不差。
        from ..llm.route import LLMRouter

        self.simulation.llm = LLMRouter(
            self.llm, character=self.simulation.appearance.key
        )
        self.simulation.dialogue_enabled = self.dialogue_enabled
        self.apply_llm_settings()
        self.click_through = bool(config.click_through)
        self._paused = False
        self._press_global = None
        self._cursor_fx_fired = False
        self._press_at = 0.0
        self._last = time.perf_counter()
        self._origin = (0.0, 0.0)

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if config.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setWindowTitle("Iterator Pet")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        workspace = ScreenWorkspace()
        snapshot = workspace.snapshot()
        geo = snapshot.available
        self._origin = (float(geo.x()), float(geo.y()))
        self.setGeometry(geo)
        zoom = max(0.3, min(4.0, float(config.scale)))
        # 心情的"屏幕速度"要有 scale 才能算（仿真 px/tick → 屏幕 px/s）
        self.simulation.scale = zoom
        self.simulation.resize(
            (0.0, 0.0, float(geo.width()) / zoom, float(geo.height()) / zoom)
        )

        # 每秒重提一次 topmost：WS_EX_TOPMOST 只保证"在置顶带里"，带内的前后顺序会被
        # 别的置顶窗口（聊天/IDE/浏览器）抢走，人偶就被挡住。SWP_NOACTIVATE 不抢焦点。
        self._top_timer = QTimer(self)
        self._top_timer.setInterval(1000)
        self._top_timer.timeout.connect(self._keep_on_top)
        if config.always_on_top:
            self._top_timer.start()

        interval = max(4, int(1000 / max(15, config.fps)))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(interval)

    # -- lifecycle ---------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        windows_overlay.prevent_activation(self)
        if self.click_through:
            windows_overlay.set_click_through(self, True)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.simulation.end_drag(fling=False)
        self.simulation.kill.cancel()
        cursorfx.release()
        self._timer.stop()
        # NO save() HERE. Every mutator (toggle_click_through / toggle_rail)
        # already persists, so closing wrote nothing new -- but a TOOL
        # that builds a window from a default ``Config`` and then closes it
        # overwrote the user's real config. That happened: ``tools/smoke_window.py``
        # reset ``scale`` from the user's 1.5 to the 2.2 default, which silently
        # changed the chamber size and made the pet 1.5x bigger on screen.
        self.closed.emit()
        super().closeEvent(event)

    # -- per-frame ---------------------------------------------------------
    def _tick(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last
        self._last = now
        if not self._paused and elapsed > 0.0:
            # The cursor must be sampled once per SIMULATION TICK, not once per
            # frame. ``advance`` can run up to 8 sub-steps for one frame, and the
            # behaviour gates are per tick: sampling per frame made every repeat
            # tick read a speed of exactly 0, which shuts ``investigate`` and
            # opens ``talk`` -- measured through the real simulation with a cursor
            # moving 3 px/frame: 1 tick/frame -> investigate 24 %, 2+ ticks/frame
            # -> investigate 2 % and talk starts while the mouse is MOVING. Worse,
            # every segment length is even, so the parity of boundary ticks is
            # conserved and a boundary that lands on a duplicate sample stays
            # duplicate forever.
            self._loop.advance(elapsed, self._step_once)
        self._track_cursor()
        self.update()

    def _step_once(self, dt: float) -> None:
        """One 40 TPS tick: look at the pointer, then step the physics."""
        if getattr(self.simulation, "dragging", False):
            # 必须是**每 tick**采一次（不是每帧）：一帧能跑 1-8 个 tick，重复采样会让
            # 第 2..N 个 tick 拿到同一个点（和 AGENTS §6.2 的"每帧采光标"同类缺陷）。
            point = self._sim_point(QCursor.pos())
            if point is not None:
                self.simulation.drag_to(point)
            if not self._left_button_down():
                self.simulation.end_drag()
        else:
            self._track_cursor()
        self.simulation.step(dt)
        cursorfx.tick()
        # 杀鼠标的 flash 段：换忙光标 0.3 s（档 2）。用"进 flash 的第一 tick"当触发沿
        if getattr(self.simulation, "kill", None) is not None:
            if self.simulation.kill.phase == "flash":
                if not self._cursor_fx_fired and getattr(self.config, "cursor_fx", True):
                    anchor = self._drawn_state.kill_anchor if self._drawn_state else None
                    if anchor is not None:
                        geo = self.geometry()
                        sx = geo.x() + anchor[0] * self.scale
                        sy = geo.y() + anchor[1] * self.scale
                        # 掉到工作区底部（留 2 px 免得压到任务栏边沿）
                        cursorfx.start(sx, sy, geo.y() + geo.height() - 2.0)
                self._cursor_fx_fired = True
            else:
                self._cursor_fx_fired = False

    def _track_cursor(self) -> None:
        """Feed the global cursor position to the puppet so it can look at it."""
        screen = self.screen()
        self.simulation.set_cursor(
            cursor_sim_point(
                QCursor.pos(),
                self.pos(),
                screen.geometry() if screen is not None else None,
                self.scale,
                self.simulation.bounds,
            )
        )

    def _keep_on_top(self) -> None:
        if self.config.always_on_top:
            windows_overlay.keep_on_top(self)

    # -- mouse drag (P0) ----------------------------------------------------
    def _sim_point(self, global_pos) -> tuple[float, float] | None:
        screen = self.screen()
        return cursor_sim_point(
            global_pos,
            self.pos(),
            screen.geometry() if screen is not None else None,
            self.scale,
            self.simulation.bounds,
        )

    def _left_button_down(self) -> bool:
        """Windows 下查物理按键状态：光标移出窗口后 Qt 收不到 release，靠它兜底。"""
        if sys.platform != "win32":
            return True
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            return True

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        # 穿透模式收不到事件（这里只是兜底）；暂停时禁止拖拽 —— 暂停不推进 tick，
        # drag_point 永远不会被消费，画面上人偶不会动（审计 P0-6）。
        if self.click_through or self._paused:
            event.ignore()
            return
        point = self._sim_point(event.globalPosition())
        kind = hit_test(self._drawn_state, point, self.scale)
        if kind is None:
            event.ignore()
            return
        # 头/躯干都是拖 chunk0（头被 _weld 焊在 chunk0 上）；底座走 P1 的沿轨道拖拽
        mode = "base" if kind == "base" else "body"
        if not self.simulation.begin_drag(mode, point):
            event.ignore()
            return
        self._press_global = event.globalPosition()
        self._press_at = time.perf_counter()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self.simulation.end_drag()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # 拖拽位置由 _step_once 里**每 tick** 采一次 QCursor.pos()；这里只兜住
        # "按下后还没到下一个 tick"的那一下移动。
        if getattr(self.simulation, "dragging", False):
            point = self._sim_point(event.globalPosition())
            if point is not None:
                self.simulation.drag_to(point)
        super().mouseMoveEvent(event)

    def trigger_kill(self) -> None:
        """调试入口：把心情直接拉到闸值以下，下一 tick 就会强制打断并进仪式。"""
        from ..simulation import constants as C
        self.simulation.temper.value = C.TEMPER_KILL_GATE - 0.01

    # -- size ---------------------------------------------------------------
    @property
    def size_preset(self) -> str:
        return getattr(self.config, "size", "full")

    def set_size(self, key: str) -> None:
        """切换尺寸档（托盘菜单）：portable 40% / youth 75% / full 100% / pro 150%。

        换尺寸 = 换渲染倍率 = 换**仿真腔室**的大小，所以整个仿真要按新腔室重建
        （rail / wander / 缆线 / 光环锚点全在仿真坐标里，就地缩放会留下旧坐标）。
        重建是一次性的（构造时自带 320 tick 预热），代价就是一下轻微的"重摆"。
        """
        if key not in SIZE_PRESETS:
            return
        self.config.size = key
        self.config.scale = scale_for(key)
        self.apply_scale()
        save(self.config)

    def apply_scale(self) -> None:
        """按当前 config.scale 重建腔室与仿真。"""
        zoom = max(0.3, min(4.0, float(self.config.scale)))
        self.scale = zoom
        geo = self.geometry()
        bounds = (0.0, 0.0, float(geo.width()) / zoom, float(geo.height()) / zoom)
        self.simulation.end_drag(fling=False)   # 拖拽中切尺寸：先结束旧会话（不甩）
        carried_temper = self.simulation.temper.value
        seed = getattr(self.simulation, "seed", 1234)
        # **不能带预热同步构造**：构造里的 320 tick 预热随腔室面积膨胀得非常厉害
        # （1138x608 约 1.6 s，40% 档的 2845x1520 要 11.7 s —— 这就是"切尺寸卡一下"）。
        # warmup=0 只要 0.4 ms，之后让 40 TPS 的固定步长循环自然把关节/缆线跑稳，
        # 用户看到的是一秒左右"刚站住"的过渡，而不是整个界面冻住。
        self.simulation = SimulationState(bounds, seed=seed, warmup=0,
                                          character=self.character)
        self.simulation.scale = zoom
        # 心情跨尺寸保留（否则换一下大小就"消气"），仪式状态机复位（不许留半个仪式）
        self.simulation.temper.value = carried_temper
        cursorfx.release()
        self._last = time.perf_counter()
        self.update()

    # -- controls ----------------------------------------------------------
    def toggle_pause(self) -> None:
        if not self._paused:
            self.simulation.end_drag(fling=False)   # 进入暂停：结束拖拽（暂停不消费 drag_point）
            self.simulation.kill.cancel()           # 暂停时取消仪式，否则白闪会冻在屏幕上
            self.simulation.cancel_dialogue()       # 暂停时收起气泡（审计 A2：tick 不动会冻在半截）
        cursorfx.release()
        self._paused = not self._paused
        if self._paused:
            self._loop.pause()
        else:
            self._loop.resume()
            self._last = time.perf_counter()
        self.update()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_spark_rate(self, name: str) -> None:
        """切换电光频率档位（off / eco / computing），并写回配置。"""
        self.simulation.halo.set_spark_rate(name)
        self.config.spark_rate = name
        save(self.config)

    @property
    def spark_rate(self) -> str:
        return getattr(self.config, "spark_rate", "eco")

    # -- 数据目录 -----------------------------------------------------------
    def open_dialogue_editor(self) -> None:
        """打开「自定义对话」GUI。

        旧实现是 os.startfile(lines.json)：没有 .json 文件关联的机器会抛 WinError 1155，
        又被 except OSError 静默吞掉 —— 用户看到的就是"点了没反应"。现在编辑全在程序里，
        不依赖系统文件关联。
        """
        self.simulation.lines.install_default_file()
        from .dialogue_editor import show_editor

        self._dialogue_editor = show_editor(self.simulation.lines)

    def open_workshop(self) -> None:
        """打开「迭代器自定义」工作室（人偶外观 / 角色卡 / 世界书 三合一）。

        v0.1.9 的范围：三个页签都能**真读写**磁盘上的 `characters/*.json` 与
        `worldbook.json`；人偶页的配色与像素头饰存进角色的 `extensions.rainworld.appearance`
        并在页内预览，**但还不会应用到桌面人偶**（那是 v0.2.0）。

        保存后调 `_reload_llm()`：世界书与角色卡都是路由器下一句话就要用的东西，
        不重载就等于"改了没生效"。
        """
        from .workshop import show_workshop

        router = self.simulation.llm
        book = getattr(router, "book", None) if router is not None else None
        self._workshop = show_workshop(book=book, folder=None, on_saved=self._reload_llm)

    def open_data_folder(self) -> None:
        """打开数据文件夹（打包版 = 启动.exe 旁边；只读介质下才是 `%LOCALAPPDATA%`）。

        玩家找不到 `lines.json` 时最容易走的一条路：点菜单 → Explorer 打开那个文件夹。
        失败（没有桌面/被策略禁）就什么都不做，绝不抛异常打断桌宠。
        """
        try:
            from ..config import config_dir

            folder = config_dir()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))            # noqa: S606 (Windows 专用)
        except (OSError, AttributeError):
            pass

    # -- dialogue -----------------------------------------------------------
    def open_llm_settings(self):
        """打开「大语言模型」设置页（只读写 ``llm.json``，不碰仿真）。

        P1 只到"连得上、选得对模型"为止：设置页保存后不需要重建任何东西。
        等 L2 把 ``LLMClient`` 接进 tick 时，这里再负责把新配置交给它。
        """
        from .llm_settings import show_settings

        self.llm.install_default_file()
        self.llm.load()
        dialog = show_settings(self.llm)
        # 保存后要把新的连接/开关交给路由器，并重读角色卡（玩家可能刚改过 JSON）
        dialog.accepted.connect(self._reload_llm)
        return dialog

    def _reload_llm(self) -> None:
        if self.simulation.llm is None:
            return
        self.simulation.llm.store = self.llm
        self.simulation.llm.client.store = self.llm
        self.simulation.llm.reload()
        self.apply_llm_settings()

    def apply_llm_settings(self) -> None:
        """把 ``llm.json`` 里**影响仿真**的两样东西同步过去：总开关 + 三档冷却。"""
        cfg = self.llm.config
        self.simulation.dialogue_enabled = bool(self.dialogue_enabled)
        self.simulation.set_dialogue_cooldowns(
            cfg.dialogue_cooldown_drag_s,
            cfg.dialogue_cooldown_talk_s,
            cfg.dialogue_cooldown_ritual_s,
        )
        self._sync_accept_drops()

    # -- 拖文件（P3，审计 A2/A15）-------------------------------------------
    def _sync_accept_drops(self) -> None:
        """只有"总开关 + 拖文件开关"都开着，才把自己注册成拖放接收者。

        审计 A2（P0）：整屏透明窗口一旦 `setAcceptDrops(True)` 就是**整个桌面**的拖放
        接收者。所以默认根本不注册；打开后命中区外一律 `ignore()`，并且每次
        `dragEnter` 都往 `launcher.log` 写一行——A2 的实测就靠这行日志判断
        "桌面上的拖放有没有被吃掉"。
        """
        want = bool(self.llm.config.enabled and self.llm.config.file_drop)
        if self.acceptDrops() != want:
            self.setAcceptDrops(want)
            self._drop_log(f"acceptDrops={want}")

    def _drop_log(self, message: str) -> None:
        try:
            from ..config import config_dir
            with (config_dir() / "launcher.log").open("a", encoding="utf-8") as fh:
                print(f"[drop] {message}", file=fh)
        except Exception:                      # noqa: BLE001 —— 日志失败不许影响拖放
            pass

    def _drop_path(self, mime) -> str | None:
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                return url.toLocalFile()
        return None

    def _drop_hit(self, event) -> tuple[str | None, str | None]:
        """返回 ``(路径, 命中的部位)``。A15：拖放与"点一下搭话"共用 `hit_test`。"""
        path = self._drop_path(event.mimeData())
        local = event.position().toPoint()
        point = self._sim_point(QPointF(self.mapToGlobal(local)))
        return path, hit_test(self._drawn_state, point, self.scale)

    def dragEnterEvent(self, event) -> None:
        if not self.acceptDrops() or self.click_through:
            event.ignore()
            return
        path, kind = self._drop_hit(event)
        ok = path is not None and kind is not None
        self._drop_moved = False
        self._drop_log(f"enter kind={kind} path={'yes' if path else 'no'} accept={ok}")
        if ok:
            event.acceptProposedAction()
        else:
            event.ignore()                     # 命中区外交还给系统（A2）

    def dragMoveEvent(self, event) -> None:
        """**必须实现**：`QWidget::dragMoveEvent` 默认 `ignore()`，等于告诉 Qt"这次拖放作废"，
        于是永远到不了 `dropEvent`（实测：有 `dragEnter` 的 "+"，松手却没反应）。

        这里和 `dragEnterEvent` 用同一套命中测试：在她身上 accept（光标显示 +），
        离开她立刻 ignore（光标变回禁止），顺带让 A2 更保守。
        """
        if not self.acceptDrops() or self.click_through:
            event.ignore()
            return
        path, kind = self._drop_hit(event)
        ok = path is not None and kind is not None
        if getattr(self, "_drop_moved", False) is not True:
            self._drop_moved = True
            self._drop_log(f"move kind={kind} accept={ok}")
        if ok:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drop_moved = False
        self._drop_log("leave（拖出窗口）")

    def dropEvent(self, event) -> None:
        # 第一行就落日志：上一次实测只有 enter 没有 drop，说明它可能根本没进来，
        # 或者进来第一句就抛了（pythonw 没有 stderr，异常是静默的）。
        self._drop_moved = False
        self._drop_log(
            f"drop 到了 acceptDrops={self.acceptDrops()} "
            f"click_through={self.click_through} urls={event.mimeData().hasUrls()}"
        )
        try:
            if not self.acceptDrops() or self.click_through:
                self._drop_log("drop 被开关挡住 -> 忽略")
                event.ignore()
                return
            path, kind = self._drop_hit(event)
            if path is None or kind is None:
                self._drop_log(f"drop kind={kind} path={'yes' if path else 'no'} -> 忽略")
                event.ignore()
                return
            event.acceptProposedAction()
            # 注意：`self.llm` 是 LLMStore（配置文件那层），路由器在仿真里。
            # 上一版写成 self.llm.ask_file() → AttributeError，拖放静默失败。
            router = self.simulation.llm
            result = (router.ask_file(path) if router is not None
                      else "没发出去：没有接 LLM 路由器")
            self._drop_log(f"drop kind={kind} -> {result}")
            if result.startswith("没"):
                # 失败时用气泡说一句（成功时回复会自己来，别抢气泡）
                self.simulation.dialogue.start(result)
        except Exception as exc:               # noqa: BLE001 —— 任何异常都要留证
            import traceback
            self._drop_log(f"drop 抛异常 {type(exc).__name__}: {exc}")
            self._drop_log("trace: " + traceback.format_exc().replace(chr(10), " | ")[:700])
            event.ignore()

    def set_llm_enabled(self, enabled: bool) -> None:
        """右键/托盘菜单的总开关：只改 ``enabled``，key 原样留着（用户 2026-10-30）。

        关掉后 ``LLMClient.enabled`` 立刻为假，下一次 `request()` 就不会发包；
        正在飞的那一个请求仍然会把结果收下来（收完就不再发新的）。
        """
        self.llm.config.enabled = bool(enabled)
        self.llm.save()
        self.apply_llm_settings()
        self.update()

    # -- 调试：直接给她发一句话（菜单里的测试入口）--------------------------
    def debug_send_to_llm(self) -> None:
        """菜单「给 LLM 发一句话…」：把玩家打的字原样发给模型。

        为什么需要它：正常路径只在拖拽/搭话/仪式时才会请求，想验"她怎么回我"就得
        等事件；这个入口直接发一条，回复照样走气泡（以及一个弹窗，避免只回动作时
        屏幕上什么都没有）。
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        router = self.simulation.llm
        if router is None:
            QMessageBox.information(self, "给 LLM 发消息", "这个窗口没有接 LLM。")
            return
        if not router.enabled:
            reason = router.client.state().get("last_reason") or "未启用"
            QMessageBox.information(
                self, "给 LLM 发消息",
                f"现在发不出去：{reason}\n（设置页里勾「启用大语言模型」、填 key、选模型）",
            )
            return
        if router.busy:
            QMessageBox.information(self, "给 LLM 发消息", "正在等上一条回复，稍后再试。")
            return
        text, ok = QInputDialog.getText(self, "给 LLM 发消息", "你想对她说：")
        if not ok or not text.strip():
            return
        if not router.ask_text(text.strip()):
            QMessageBox.information(self, "给 LLM 发消息", "没发出去（关着或正在等回复）。")
            return
        self._start_debug_watch(router.seq)

    # 等回执的上限。`llm.json` 的 timeout_s 默认 8 s，加上排队/重试留足余量。
    DEBUG_WAIT_S = 35.0

    def _start_debug_watch(self, seq: int) -> None:
        """开始等**这一次**请求的回执。

        两件必须做的事（都踩过）：
        * 先把上一个计时器**停掉并销毁**，否则旧计时器会把后来的回执当成自己的，
          一次发送弹出好几个框；
        * 记住请求序号 ``seq``，只认 ``last_reply_seq == seq`` 的那条——
          上一次失败的回执会一直躺在 ``last_reply`` 里，光看 event 认不出新旧。
        """
        old = getattr(self, "_debug_timer", None)
        if old is not None:
            old.stop()
            old.deleteLater()
        self._debug_seq = int(seq)
        self._debug_deadline = time.monotonic() + self.DEBUG_WAIT_S
        self._debug_timer = QTimer(self)
        self._debug_timer.setInterval(120)
        self._debug_timer.timeout.connect(self._debug_reply_ready)
        self._debug_timer.start()

    def _stop_debug_watch(self) -> None:
        timer = getattr(self, "_debug_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._debug_timer = None

    def _debug_reply_ready(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        # 计时器已经被停掉（正在弹框/已经收尾）→ 排队里的 timeout 直接忽略，
        # 否则模态框的嵌套事件循环会把同一个回执再弹一次。
        if getattr(self, "_debug_timer", None) is None:
            return
        router = self.simulation.llm
        if router is None:
            self._stop_debug_watch()
            return
        expired = time.monotonic() >= getattr(self, "_debug_deadline", 0.0)
        if router.busy or router.last_reply_seq != getattr(self, "_debug_seq", -1):
            if not expired:
                return
            self._stop_debug_watch()
            QMessageBox.information(
                self, "给 LLM 发消息",
                f"等了 {self.DEBUG_WAIT_S:.0f} 秒还没收到这次的回执。\n"
                f"（模型={router.store.config.model or '未选'}，"
                f"最近一次原因：{router.last_reply.reason or '无'}）",
            )
            return
        self._stop_debug_watch()
        reply = router.last_reply
        lines = [reply.text or "（她没有说话）"]
        if reply.action:
            lines.append(
                f"动作：{reply.action}（已进意图队列；若当时正在说话/拖拽/仪式中，"
                f"会在 5 秒内等闸门打开后执行）"
            )
        if reply.reason:
            lines.append(f"原因：{reply.reason}")
        QMessageBox.information(self, "她的回复", "\n".join(lines))

    def set_dialogue_enabled(self, enabled: bool) -> None:
        """托盘「对话」开关。关掉立刻收起当前这句。"""
        self.dialogue_enabled = bool(enabled)
        self.config.dialogue_enabled = bool(enabled)
        self.simulation.dialogue_enabled = bool(enabled)   # 关掉后连 LLM 都不问
        if not enabled:
            self.simulation.cancel_dialogue()
        self.update()
        save(self.config)

    # -- character ----------------------------------------------------------
    @property
    def character(self) -> str:
        return getattr(self.config, "character", "pebbles")

    def set_character(self, key: str) -> None:
        """切换角色（托盘「角色」菜单）。外观参数在 simulation/appearance.py。

        只换外观，**仿真一行不动**：所以不重建 SimulationState，只把 appearance
        换掉再刷一帧。换角色时结束拖拽与仪式（避免"她的白光"这种跨角色的残留状态）。
        """
        from ..simulation.appearance import CHARACTERS

        if key not in CHARACTERS or key == self.character:
            return
        self.simulation.end_drag(fling=False)
        self.simulation.kill.cancel()
        self.simulation.cancel_dialogue()        # 换角色不许串台词（审计 A3）
        self.config.character = key
        self.simulation.appearance = CHARACTERS[key]
        if self.simulation.llm is not None:
            self.simulation.llm.set_character(key)      # 换角色 → 换角色卡与历史
        self.update()
        save(self.config)

    def toggle_click_through(self) -> None:
        self.simulation.end_drag(fling=False)
        self.simulation.kill.cancel()          # 穿透后就收不到事件了，先结束拖拽
        cursorfx.release()
        self.click_through = not self.click_through
        self._apply_click_through()
        self.config.click_through = self.click_through
        save(self.config)

    def _apply_click_through(self) -> None:
        applied = windows_overlay.set_click_through(self, self.click_through)
        # Qt-level fallback (also keeps behaviour sane on non-Windows).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.click_through)
        self._click_through_native = applied

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_pause()
        elif key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_C:
            self.toggle_click_through()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键菜单：托盘图标被 Windows 折进溢出区时，这是最直接的退出入口。"""
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        action_pause = QAction("继续" if self._paused else "暂停", menu)
        action_pause.triggered.connect(self.toggle_pause)
        menu.addAction(action_pause)

        action_click = QAction("鼠标穿透", menu)
        action_click.setCheckable(True)
        action_click.setChecked(bool(self.click_through))
        action_click.triggered.connect(self.toggle_click_through)
        menu.addAction(action_click)

        menu.addSeparator()
        action_cursor = QAction("光标特效（杀鼠标时）", menu)
        action_cursor.setCheckable(True)
        action_cursor.setChecked(bool(getattr(self.config, "cursor_fx", True)))

        def _toggle_cursor(checked: bool) -> None:
            self.config.cursor_fx = bool(checked)
            cursorfx.set_enabled(bool(checked))
            save(self.config)

        action_cursor.triggered.connect(_toggle_cursor)
        menu.addAction(action_cursor)

        action_kill = QAction("杀鼠标（调试）", menu)
        action_kill.triggered.connect(self.trigger_kill)
        menu.addAction(action_kill)

        size_menu = menu.addMenu("尺寸")
        for key, (factor, label) in SIZE_PRESETS.items():
            action_size = QAction(label, size_menu)
            action_size.setCheckable(True)
            action_size.setChecked(self.size_preset == key)
            action_size.triggered.connect(
                lambda _checked=False, name=key: self.set_size(name)
            )
            size_menu.addAction(action_size)

        spark_menu = menu.addMenu("电光频率")
        for key, label in (("off", "无"), ("eco", "节能"), ("computing", "computing（1.5×）")):
            action_rate = QAction(label, spark_menu)
            action_rate.setCheckable(True)
            action_rate.setChecked(self.spark_rate == key)
            action_rate.triggered.connect(
                lambda _checked=False, name=key: self.set_spark_rate(name)
            )
            spark_menu.addAction(action_rate)

        character_menu = menu.addMenu("角色")
        for key, label in (("pebbles", "五块卵石"), ("past_moon", "仰望皓月")):
            action_character = QAction(label, character_menu)
            action_character.setCheckable(True)
            action_character.setChecked(self.character == key)
            action_character.triggered.connect(
                lambda _checked=False, name=key: self.set_character(name)
            )
            character_menu.addAction(action_character)

        action_workshop = QAction("迭代器自定义…", menu)
        action_workshop.triggered.connect(self.open_workshop)
        menu.addAction(action_workshop)

        action_edit_lines = QAction("自定义对话", menu)
        action_edit_lines.triggered.connect(self.open_dialogue_editor)
        menu.addAction(action_edit_lines)

        action_llm = QAction("大语言模型…", menu)
        action_llm.triggered.connect(self.open_llm_settings)
        menu.addAction(action_llm)

        action_llm_on = QAction("使用大语言模型", menu)
        action_llm_on.setCheckable(True)
        action_llm_on.setChecked(bool(getattr(self.llm.config, "enabled", True)))
        action_llm_on.triggered.connect(
            lambda checked: self.set_llm_enabled(bool(checked))
        )
        menu.addAction(action_llm_on)

        action_llm_debug = QAction("给 LLM 发一句话…（调试）", menu)
        action_llm_debug.triggered.connect(self.debug_send_to_llm)
        menu.addAction(action_llm_debug)

        action_open_data = QAction("打开数据文件夹", menu)
        action_open_data.triggered.connect(self.open_data_folder)
        menu.addAction(action_open_data)
        menu.addSeparator()

        action_dialogue = QAction("对话", menu)
        action_dialogue.setCheckable(True)
        action_dialogue.setChecked(bool(self.dialogue_enabled))
        action_dialogue.triggered.connect(
            lambda checked: self.set_dialogue_enabled(bool(checked))
        )
        menu.addAction(action_dialogue)

        menu.addSeparator()
        action_quit = QAction("退出", menu)
        action_quit.triggered.connect(self.close)
        menu.addAction(action_quit)

        menu.exec(event.globalPos())
        event.accept()


def create_window(config: Config | None = None, sprites=None) -> PetWindow:
    app = QApplication.instance() or QApplication([])
    cfg = config or load()
    workspace = ScreenWorkspace()
    snapshot = workspace.snapshot()
    geo = snapshot.available
    zoom = max(0.3, min(4.0, float(cfg.scale)))
    sim = SimulationState(
        (0.0, 0.0, float(geo.width()) / zoom, float(geo.height()) / zoom),
        character=getattr(cfg, "character", "pebbles"),
    )
    window = PetWindow(sim, cfg, sprites=sprites)
    window.setGeometry(geo)
    return window
