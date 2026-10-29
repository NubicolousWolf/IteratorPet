"""Full-screen transparent overlay window that hosts the pet."""

from __future__ import annotations

import ctypes
import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from ..config import SIZE_PRESETS, Config, load, save, scale_for
from ..rendering.hit import hit_test
from ..rendering.canvas import PetCanvas
from ..simulation.state import SimulationState
from .fixed_loop import DEFAULT_MAX_SUBSTEPS, GAME_DT, FixedStepLoop
from .workspace import ScreenWorkspace
from . import windows_overlay


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
        self.click_through = bool(config.click_through)
        self._paused = False
        self._press_global = None
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
        self.simulation = SimulationState(bounds, seed=seed, warmup=0)
        self.simulation.scale = zoom
        # 心情跨尺寸保留（否则换一下大小就"消气"），仪式状态机复位（不许留半个仪式）
        self.simulation.temper.value = carried_temper
        self._last = time.perf_counter()
        self.update()

    # -- controls ----------------------------------------------------------
    def toggle_pause(self) -> None:
        if not self._paused:
            self.simulation.end_drag(fling=False)   # 进入暂停：结束拖拽（暂停不消费 drag_point）
            self.simulation.kill.cancel()           # 暂停时取消仪式，否则白闪会冻在屏幕上
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

    def toggle_click_through(self) -> None:
        self.simulation.end_drag(fling=False)
        self.simulation.kill.cancel()          # 穿透后就收不到事件了，先结束拖拽
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
        (0.0, 0.0, float(geo.width()) / zoom, float(geo.height()) / zoom)
    )
    window = PetWindow(sim, cfg, sprites=sprites)
    window.setGeometry(geo)
    return window
