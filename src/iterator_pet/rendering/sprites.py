"""Sprite bank: the handful of atlas primitives, pre-tinted for the palette.

The game composes everything from ``Circle20``, ``pixel``, ``CentipedeSegment``
etc. and tints them at draw time. Tinting per frame would be wasteful, so each
(name, colour) pair is rendered once into a cached ``QPixmap``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from ..assets_extractor import ensure_sprites

_RAW: dict[str, QPixmap] = {}
_TINTED: dict[tuple[str, tuple[int, int, int, int]], QPixmap] = {}


def _raw(name: str) -> QPixmap:
    if name not in _RAW:
        report = ensure_sprites()
        path: Path = report.sprites.get(name, Path())
        pixmap = QPixmap(str(path)) if path and path.is_file() else QPixmap()
        if pixmap.isNull():
            pixmap = QPixmap(8, 8)
            pixmap.fill(Qt.GlobalColor.white)
        _RAW[name] = pixmap
    return _RAW[name]


def tinted(name: str, colour: tuple[int, int, int], alpha: int = 255) -> QPixmap:
    key = (name, (colour[0], colour[1], colour[2], alpha))
    cached = _TINTED.get(key)
    if cached is not None:
        return cached

    source = _raw(name)
    out = QPixmap(source.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), QColor(colour[0], colour[1], colour[2], alpha))
    painter.end()
    _TINTED[key] = out
    return out


def clear_cache() -> None:
    _RAW.clear()
    _TINTED.clear()
