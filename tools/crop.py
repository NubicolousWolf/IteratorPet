"""Crop + upscale a region of a PNG so a 40 px puppet can be judged at pixel level.

    python tools/crop.py in.png out.png X Y W H [ZOOM]
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage


def main() -> int:
    if len(sys.argv) < 7:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    x, y, w, h = (int(v) for v in sys.argv[3:7])
    zoom = float(sys.argv[7]) if len(sys.argv) > 7 else 3.0
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    img = QImage(src)
    if img.isNull():
        print(f"cannot read {src}")
        return 1
    crop = img.copy(QRect(x, y, w, h))
    if zoom != 1.0:
        crop = crop.scaled(int(w * zoom), int(h * zoom),
                           Qt.IgnoreAspectRatio, Qt.FastTransformation)
    crop.save(dst)
    print(f"{dst}  {crop.width()}x{crop.height()}  (from {x},{y} {w}x{h} x{zoom})")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
