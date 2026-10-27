"""Short real-window smoke test: create the overlay, run it briefly, save a grab.

Usage: python tools/smoke_window.py [seconds] [out.png]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication

from iterator_pet.config import Config
from iterator_pet.platform.pet_window import create_window


def main(argv: list[str]) -> int:
    seconds = float(argv[1]) if len(argv) > 1 else 2.5
    out = Path(argv[2]) if len(argv) > 2 else Path("window-grab.png")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    config = Config(click_through=False, fps=60)
    window = create_window(config)
    window.show()
    end = time.perf_counter() + seconds
    frames = 0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)
        frames += 1
    grab = window.grab()
    grab.save(str(out))
    print(f"window ok: {window.width()}x{window.height()} {window.windowTitle()!r} frames={frames}")
    print(f"grab: {out} ({grab.width()}x{grab.height()})")
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
