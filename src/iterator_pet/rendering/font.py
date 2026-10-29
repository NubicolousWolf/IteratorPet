"""气泡用的字体：**微软雅黑 → 系统默认字体**（用户 2026-10-29 拍板）。

游戏的对话框用的是烘焙位图字体（`fontChi` = Noto Sans SC Medium 45，见
`docs/PLAN-dialogue.md` §9），但用户拍板桌面版用系统字体，所以这里不做位图字体。

**为什么用 ``QFontInfo`` 判命中**（审计 A8）：``QFontDatabase.families()`` 在离屏平台
（``QT_QPA_PLATFORM=offscreen``）下返回空列表，拿它判断"有没有雅黑"会误判；而
``QFontInfo(font).family()`` 是"Qt 实际会用的族"，请求没命中时它会返回替换后的族名。
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

PREFERRED_FAMILY = "Microsoft YaHei"


@lru_cache(maxsize=8)
def bubble_font(pixel_size: int = 27) -> QFont:
    """返回一个**固定像素高**的字体（用户拍板：27 屏幕 px，不随 scale 变）。"""
    font = QFont(PREFERRED_FAMILY)
    font.setPixelSize(max(8, int(pixel_size)))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    if QFontInfo(font).family() == PREFERRED_FAMILY:
        return font
    fallback = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    fallback.setPixelSize(max(8, int(pixel_size)))
    fallback.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return fallback


def resolved_family(pixel_size: int = 27) -> str:
    """当前实际会用的字体族名（面板/日志用）。"""
    return QFontInfo(bubble_font(pixel_size)).family()


def clear_font_cache() -> None:
    bubble_font.cache_clear()
