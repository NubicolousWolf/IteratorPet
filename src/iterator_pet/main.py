"""Entry point: run the desktop pet, or render a still preview for QA."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from .config import Config, load
from .platform.pet_window import create_window
from .platform.tray import PetTray
from .rendering.renderer import draw_scene
from .rendering.spritepack import SpritePack, default_sprite_dir
from .simulation.state import SimulationState

from .platform.fixed_loop import GAME_DT as PHYSICS_DT  # 40 TPS, matches Rain World


def render_preview(
    path: str | Path,
    size: tuple[int, int] = (1280, 720),
    seconds: float = 4.0,
    scale: float = 1.0,
    background: bool = True,
) -> Path:
    """Render the scene offscreen at a fixed time, for visual inspection/tests."""
    app = QApplication.instance() or QApplication([])  # noqa: F841 (needed for QPainter)
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
