"""Entry point: run the desktop pet, or render a still preview for QA."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from .config import Config, load, save
from .platform.hotkey import GlobalHotkey
from .platform import cursorfx
from .platform.pet_window import create_window
from .platform.tray import make_icon
from .rendering.renderer import draw_scene
from .rendering.spritepack import SpritePack, default_sprite_dir
from .simulation.state import SimulationState

from .platform.fixed_loop import GAME_DT as PHYSICS_DT  # 40 TPS, matches Rain World


def _log(message: str) -> None:
    """noconsole 打包时 print 看不见；写进和启动器同一个日志。"""
    try:
        from .config import config_dir

        path = config_dir() / "launcher.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[pet] {message}\n")
    except OSError:
        pass


def _dbg(message: str) -> None:
    """``ITERATORPET_DEBUG=1`` 时把检查点打到 stdout（打包版也能用 `> log.txt 2>&1` 抓）。"""
    import os

    if os.environ.get("ITERATORPET_DEBUG"):
        print(f"[dbg] {message}", flush=True)


def render_preview(
    path: str | Path,
    size: tuple[int, int] = (1280, 720),
    seconds: float = 4.0,
    scale: float = 1.0,
    background: bool = True,
    character: str = "pebbles",
) -> Path:
    """Render the scene offscreen at a fixed time, for visual inspection/tests."""
    app = QApplication.instance() or QApplication([])  # noqa: F841 (needed for QPainter)
    from .assets_extractor import prepare

    prepare(load().install or None)          # 只在画之前准备好素材，绘制路径里不再提取
    width, height = size
    zoom = max(0.3, min(4.0, float(scale))) if scale else 1.0
    sim = SimulationState((0.0, 0.0, float(width) / zoom, float(height) / zoom),
                          character=character)
    for _ in range(max(0, int(seconds / PHYSICS_DT))):
        sim.step(PHYSICS_DT)

    image = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    if background:
        from PySide6.QtGui import QColor

        painter.fillRect(image.rect(), QColor("#171216"))
    state = sim.interpolate(1.0)
    painter.save()
    painter.scale(zoom, zoom)
    draw_scene(painter, state)
    painter.restore()
    # 气泡在**屏幕像素**画（坐标转换在 draw_bubble_world 里做一次），
    # 所以必须跳出 zoom 变换 —— 否则 27 px 会被再乘一个 zoom（审计 A1）。
    view = sim.dialogue_view()
    if view:
        from .rendering.bubble import draw_bubble_world

        draw_bubble_world(
            painter,
            text=view["text"],
            size_fac=view["size_fac"],
            anchor_sim=view["anchor"],
            scale=zoom,
            screen=(float(width), float(height)),
        )
    painter.end()

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(out))
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rain World iterator desktop pet")
    parser.add_argument("--preview", metavar="PNG", help="render one frame to a PNG and exit")
    parser.add_argument("--seconds", type=float, default=4.0, help="preview simulation time")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--fps", type=int, default=None, help="render frame rate")
    parser.add_argument("--click-through", action="store_true", help="start with OS click-through")
    parser.add_argument("--no-click-through", action="store_true")
    parser.add_argument("--no-tray", action="store_true", help="do not create a tray icon")
    parser.add_argument(
        "--dev-panel", action="store_true",
        help="open the developer diagnostics panel (off by default)",
    )
    parser.add_argument(
        "--sprites",
        metavar="DIR",
        default=None,
        help="use body.png/head.png from DIR instead of the drawn puppet",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.preview:
        out = render_preview(
            args.preview,
            size=(args.width, args.height),
            seconds=args.seconds,
            scale=args.scale,
        )
        print(f"wrote {out}")
        return 0

    app = QApplication.instance() or QApplication(sys.argv[:1])
    # 上一次硬崩可能留下"忙"光标（atexit 在硬崩下不执行）→ 启动先自愈一次
    cursorfx.self_heal()
    app.setApplicationName("Iterator Pet")
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)

    cfg = load()
    if args.fps:
        cfg.fps = max(15, args.fps)
    if args.click_through:
        cfg.click_through = True
    if args.no_click_through:
        cfg.click_through = False

    # 素材在开窗之前准备好；缺游戏/切失败都用占位图元继续（绝不拦启动）。
    from .assets_extractor import prepare

    if prepare(cfg.install or None) is None:
        _log("素材准备失败（缺游戏或解包失败），本次用占位图元")

    pack = SpritePack.load(args.sprites or default_sprite_dir())
    window = create_window(cfg, sprites=pack)
    # 玩家问"lines.json 在哪 / 有没有生效"时，日志里要能直接看到答案
    pool = window.simulation.lines.state()
    _log(
        "对话池: {path} 存在={exists} 条目={entries} 来源={source}".format(
            path=pool["path"],
            exists=pool["exists"],
            entries=pool["entries"],
            source="文件" if pool["loaded_from_file"] else "内置默认",
        )
    )
    if pool.get("last_error"):
        _log(f"对话池警告: {pool['last_error']}")
    # 玩家问"我的 key 生效没 / llm.json 在哪"时日志里要有答案。
    # 只写"有没有 key"，key 本身永远不进日志（审计 A9）。
    llm_state = window.llm.state()
    _log(
        "LLM: {path} 存在={exists} 模型={model} 有key={key} 行动={actions} 拖文件={drop}".format(
            path=llm_state["path"],
            exists=llm_state["exists"],
            model=llm_state["model"] or "（未选）",
            key="是" if llm_state["has_key"] else "否",
            actions="开" if llm_state["allow_actions"] else "关",
            drop="开" if llm_state["file_drop"] else "关",
        )
    )
    if llm_state["last_error"]:
        _log(f"LLM 配置警告: {llm_state['last_error']}")
    # 电光频率档位：off / eco / computing（托盘或右键菜单里能改，写回 config）
    window.simulation.halo.set_spark_rate(getattr(cfg, "spark_rate", "eco"))
    window.show()
    window.raise_()

    # ITERATORPET_DUMP=<path>：3 秒后把"活窗口"里的关键坐标写一份 JSON。
    # 用途：比对"源码跑 vs 打包跑"到底哪个量不一样（预览渲染是一致的，所以
    # 差异只可能出现在屏幕几何/窗口这条路上）。
    dump_target = os.environ.get("ITERATORPET_DUMP")
    if dump_target:
        from PySide6.QtCore import QTimer

        def _dump_live_state() -> None:
            import json

            sim = window.simulation
            state = sim.interpolate(1.0)
            screen = window.screen()
            payload = {
                "screen_geometry": [screen.geometry().x(), screen.geometry().y(),
                                    screen.geometry().width(), screen.geometry().height()],
                "screen_available": [screen.availableGeometry().x(), screen.availableGeometry().y(),
                                     screen.availableGeometry().width(), screen.availableGeometry().height()],
                "dpr": screen.devicePixelRatio(),
                "window_geometry": [window.x(), window.y(), window.width(), window.height()],
                "canvas_scale": window.scale,
                "config_scale": cfg.scale,
                "sim_bounds": list(sim.bounds),
                "base_point": list(state.base_point),
                "body0": list(state.body0),
                "head": list(state.head),
                "joints": [list(j) for j in state.joints],
                "cord_first": list(state.cord[0]) if state.cord else None,
                "cord_mid": list(state.cord[len(state.cord) // 2]) if state.cord else None,
                "cord_last": list(state.cord[-1]) if state.cord else None,
                "cord_len": len(state.cord),
            }
            try:
                Path(dump_target).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except OSError:
                pass

        QTimer.singleShot(3000, _dump_live_state)

    # The panel talks to the same simulation the canvas draws. It is a Tool
    # window: it never takes focus and never feeds anything back.
    panel = None
    if args.dev_panel:
        from .platform.devpanel import DevPanel

        panel = DevPanel(window.simulation)
        panel.refresh()
        panel.show()

    tray = None
    _dbg("进入托盘段")
    if not args.no_tray:
        # 托盘图标走 Win32 Shell_NotifyIcon（QSystemTrayIcon 打包后压根注册不上，
        # 连最小 exe 都复现，见 platform/win_tray.py 顶部说明）。
        from .platform.tray import tray_menu
        from .platform.win_tray import WinTray

        _dbg("托盘模块导入完成")
        tray = WinTray(
            "五块卵石",
            lambda: tray_menu(window, lambda: _toggle_panel(window)),
            fallback_hwnd=int(window.winId()),
        )
        _dbg(f"WinTray 构造完成 hwnd={tray._hwnd} icon={tray._icon}")
        shown = tray.show()
        _dbg(f"Shell_NotifyIcon 返回 {shown}")
        if shown:
            window.closed.connect(tray.hide)
        _log(f"托盘: Shell_NotifyIcon 注册 {'成功' if shown else '失败'}")
        _dbg("已写日志")

    # 不依赖任何可见 UI 的退出口（理由见 platform/hotkey.py）。
    hotkey = GlobalHotkey(window.close)
    if not hotkey.install(app):
        _log("Ctrl+Alt+Q 注册失败（可能被别的程序占用），请改用右键菜单或托盘退出。")
    window._hotkey = hotkey  # 保活：注册在 Qt 事件循环上

    window.closed.connect(app.quit)
    if panel is not None:
        window.closed.connect(panel.close)
    return app.exec()


def _toggle_panel(window):
    """Show/hide the developer panel from the tray menu."""
    from .platform.devpanel import DevPanel

    panel = getattr(window, "_dev_panel", None)
    if panel is None:
        panel = DevPanel(window.simulation)
        setattr(window, "_dev_panel", panel)
    panel.refresh()
    if panel.isVisible():
        panel.hide()
    else:
        panel.show()
    return panel.isVisible()


if __name__ == "__main__":
    raise SystemExit(main())
