"""Standalone developer panel -- run the simulation and watch its numbers.

    python tools/devpanel.py                       # live window, no mouse
    python tools/devpanel.py --cursor sweep        # exercises `investigate`
    python tools/devpanel.py --cursor parked       # exercises `talk`
    python tools/devpanel.py --grab ../_work/panel.png --seconds 20

No pet window, no tray: just the simulation and the panel, so a developer can
watch the numbers next to the code. ``--grab`` renders the panel offscreen to a
PNG (works headless, useful for reviews and for CI-ish checks).

Inside the real pet the same panel is available with ``--dev-panel`` or the tray
menu; this script exists so the panel is usable without deploying the pet.
"""

from __future__ import annotations

import argparse
import sys
from math import cos, sin
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from iterator_pet.platform.devpanel import DevPanel, dev_lines
from iterator_pet.platform.fixed_loop import GAME_DT
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

DEFAULT_SIZE = (1707, 912)
DEFAULT_ZOOM = 2.2


def build_sim(size: tuple[int, int], zoom: float) -> SimulationState:
    width, height = size
    return SimulationState((0.0, 0.0, width / zoom, height / zoom))


def drive_cursor(sim: SimulationState, mode: str, tick: int) -> None:
    """Same three modes as ``tools/roam_report.py``, so numbers are comparable."""
    if mode == "none":
        sim.cursor = None
        return
    x, y, w, h = sim._inset()
    if mode == "parked":
        sim.cursor = (x + w * 0.5, y + h * 0.5)
    elif mode == "sweep":
        sim.cursor = (x + w * 0.5 + w * 0.28 * cos(tick * 0.05),
                      y + h * 0.5 + h * 0.22 * sin(tick * 0.05))
    else:
        raise SystemExit(f"unknown cursor mode {mode!r} (none|parked|sweep)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iterator Pet developer panel")
    parser.add_argument("--cursor", default="none", choices=("none", "parked", "sweep"))
    parser.add_argument("--size", type=int, nargs=2, default=list(DEFAULT_SIZE),
                        metavar=("W", "H"), help="logical screen size")
    parser.add_argument("--zoom", type=float, default=DEFAULT_ZOOM)
    parser.add_argument("--grab", metavar="PNG", help="render the panel to a PNG and exit")
    parser.add_argument("--seconds", type=float, default=20.0,
                        help="simulated seconds before grabbing (or before printing)")
    parser.add_argument("--print", action="store_true",
                        help="print the panel text to stdout and exit (no Qt window)")
    args = parser.parse_args(argv)

    sim = build_sim(tuple(args.size), args.zoom)
    ticks = max(1, int(args.seconds / GAME_DT))
    if args.print:
        # Qt is still needed for font metrics; the text itself does not need a window.
        QApplication.instance() or QApplication(sys.argv[:1])
        for tick in range(ticks):
            drive_cursor(sim, args.cursor, tick)
            sim.step(GAME_DT)
        print("\n".join(dev_lines(sim)))
        return 0

    app = QApplication.instance() or QApplication(sys.argv[:1])
    for tick in range(ticks):
        drive_cursor(sim, args.cursor, tick)
        sim.step(GAME_DT)

    panel = DevPanel(sim)
    panel.refresh()

    if args.grab:
        panel.resize(430, 330)
        image = panel.grab()
        out = Path(args.grab)
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(out))
        print(f"wrote {out}  ({image.width()}x{image.height()})  cursor={args.cursor}")
        return 0

    # Live mode: this tool owns the clock (the real pet has a fixed-step loop).
    panel.show()
    state = {"tick": ticks}

    def advance() -> None:
        for _ in range(2):      # 25 ms timer -> 40 TPS
            drive_cursor(sim, args.cursor, state["tick"])
            sim.step(GAME_DT)
            state["tick"] += 1
        panel.refresh()

    timer = QTimer()
    timer.timeout.connect(advance)
    timer.start(50)
    app._devpanel_timer = timer  # keep a reference alive for the app's lifetime
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
