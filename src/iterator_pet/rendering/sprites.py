"""Sprite bank: the handful of atlas primitives, pre-tinted for the palette.

The game composes everything from ``Circle20``, ``pixel``, ``CentipedeSegment``
etc. and tints them at draw time. Tinting per frame would be wasteful, so each
(name, colour) pair is rendered once into a cached ``QPixmap``.

**这里绝不能触发素材提取。** 早先 ``_raw`` 在缺图时直接调 ``ensure_sprites()``，
也就是在**绘制路径里**跑 UnityPy 解包整个 resources.assets —— 全新环境下第一次
paintEvent 就卡住/抛异常（实测 exe 报 WinError 5 直接"启动失败"）。现在缺图就退化成
一个纯白小方块，真正的提取只在启动器的初始化那一步做。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

_RAW: dict[str, QPixmap] = {}
_TINTED: dict[tuple[str, tuple[int, int, int, int]], QPixmap] = {}
_MISSING: set[str] = set()


def sprite_dir() -> Path:
    """缓存的精灵目录（不创建、不提取）。"""
    from ..assets_extractor import cache_root

    return cache_root() / "sprites"


def _fallback(name: str) -> QPixmap:
    """缺图时的占位：1:1 白块，尺寸按几个已知图元的原尺寸，避免整只人偶缩成一团。"""
    sizes = {
        "Circle20": 20,
        "pixel": 1,
        "CentipedeSegment": 18,
        "MirosLegSmallPart": 4,
        "haloGlyph-1": 10,
        "LizardScaleA1": 6,
        "deerEyeB": 20,
        "KarmaPetal": 12,
    }
    side = sizes.get(name, 8)
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.white)
    return pixmap


def _raw(name: str) -> QPixmap:
    if name not in _RAW:
        path = sprite_dir() / f"{name}.png"
        pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
        if pixmap.isNull():
            if name not in _MISSING:
                _MISSING.add(name)
                # 只记一次；不写日志也不提取，绘制路径必须便宜且不会抛。
                print(f"[sprites] 缺少 {name}.png，用占位方块（请先跑一次启动器初始化）")
            pixmap = _fallback(name)
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
    _MISSING.clear()
