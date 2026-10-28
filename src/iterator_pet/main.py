"""Entry point: run the desktop pet, or render a still preview for QA."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from .config import Config, load, save
from .platform.hotkey import GlobalHotkey
from .platform.pet_window import create_window
from .platform.tray import PetTray
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


def render_preview(
    path: str | Path,
    size: tuple[int, int] = (1280, 720),
    seconds: float = 4.0,
    scale: float = 1.0,
    background: bool = True,
) -> Path:
    """Render the scene offscreen at a fixed time, for visual inspection/tests."""
    app = QApplication.instance() or QApplication([])  # noqa: F841 (needed for QPainter)
    from .assets_extractor import prepare

    prepare(load().install or None)          # 只在画之前准备好素材，绘制路径里不再提取
    width, height = size
    zoom = max(0.3, min(4.0, float(scale))) if scale else 1.0
    sim = SimulationState((0.0, 0.0, float(width) / zoom, float(height) / zoom))
    for _ in range(max(0, int(seconds / PHYSICS_DT))):
        sim.step(PHYSICS_DT)

    image = QImage(int(width), int(height), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    if background:
        from PySide6.QtGui import QColor

        painter.fillRect(image.rect(), QColor("#171216"))
    painter.scale(zoom, zoom)
    draw_scene(painter, sim.interpolate(1.0))
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
    app.setApplicationName("Iterator Pet")
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
    window.show()
    window.raise_()

    # The panel talks to the same simulation the canvas draws. It is a Tool
    # window: it never takes focus and never feeds anything back.
    panel = None
    if args.dev_panel:
        from .platform.devpanel import DevPanel

        panel = DevPanel(window.simulation)
        panel.refresh()
        panel.show()

    tray = None
    if not args.no_tray:
        from PySide6.QtWidgets import QSystemTrayIcon

        if QSystemTrayIcon.isSystemTrayAvailable():
            tray = PetTray(window, cfg, on_config_changed=lambda: None)
            tray.attach_dev_panel(lambda: _toggle_panel(window))
            tray.show()
            window.closed.connect(tray.hide)
            # Windows 11 默认把新应用的托盘图标折进溢出区（^），玩家会以为程序没法退。
            # 第一次运行时说明一次，之后不再打扰。
            if not cfg.hint_shown:
                tray.showMessage(
                    "五块卵石已经在你的桌面上",
                    "退出：右键托盘图标（可能折在 ^ 里）→ 退出；也可以直接按 Ctrl+Alt+Q；"
                    "右键人偶本身也有菜单（开了鼠标穿透时不可用）。",
                    QSystemTrayIcon.MessageIcon.Information,
                    8000,
                )
                cfg.hint_shown = True
                save(cfg)
        else:
            # 托盘不存在（远程会话 / 精简系统）：右键人偶 + Ctrl+Alt+Q 仍然可用，
            # 但要留一条痕迹，免得下次排查"为什么没有托盘"。
            _log("系统托盘不可用，只保留右键菜单与 Ctrl+Alt+Q 两个退出口")

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
