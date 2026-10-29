"""气泡的排版/绘制测试。

分两层，故意分开：
* **纯逻辑**（折行、超行截断、圆角矩形几何）用**假的 measure**，完全不碰 Qt
  —— 离屏平台下量长中文串会让 FreeType abort（实测），逻辑测试不该被这个拖累；
* **真的画**只留一条短文本冒烟（证明"到底有没有画出像素"），并且全程离屏。

离屏平台没有字体目录（`QFontDatabase` 0 族），所以在导入 Qt 之前先指到系统字体目录，
否则量出来的全是豆腐块的宽度。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.rendering.bubble import (  # noqa: E402
    bubble_rect,
    draw_bubble,
    fit_lines,
    layout,
)
from iterator_pet.simulation import constants as C  # noqa: E402

APP = QApplication.instance() or QApplication([])


def _fixed(char_width: float = 10.0):
    """假的 measure：每个字符 10 px。中文/英文一视同仁，够用。"""
    return lambda text: char_width * len(text)


# --------------------------------------------------------------------------
# 纯逻辑：折行与截断（无 Qt）
# --------------------------------------------------------------------------
def test_short_text_stays_on_one_line():
    assert fit_lines("你还在这里吗？", _fixed(), 200.0) == ["你还在这里吗？"]


def test_text_wraps_to_the_width():
    lines = fit_lines("请不要打扰我。" * 5, _fixed(10.0), 70.0)   # 每行 7 个字
    assert len(lines) > 1
    assert all(len(line) <= 7 for line in lines), lines


def test_overlong_text_is_truncated_with_an_ellipsis():
    lines = fit_lines("请不要打扰我。" * 12, _fixed(10.0), 70.0)
    assert len(lines) == C.DIALOGUE_MAX_LINES
    assert lines[-1].endswith("…"), f"超行必须用省略号收尾，实际 {lines[-1]!r}"
    assert len(lines[-1]) <= 7


def test_explicit_newlines_are_respected():
    lines = fit_lines("第一行\n第二行", _fixed(), 500.0)
    assert lines == ["第一行", "第二行"]


# --------------------------------------------------------------------------
# 位置（无 Qt）
# --------------------------------------------------------------------------
def test_bubble_rect_sits_above_the_anchor_and_is_centred():
    rect = bubble_rect((400.0, 500.0), 200.0, 60.0, 1.0, (800.0, 600.0))
    assert abs((rect.left() + rect.right()) * 0.5 - 400.0) < 0.01
    assert abs(rect.bottom() - 500.0) < 0.01


def test_bubble_rect_is_clamped_into_the_screen():
    rect = bubble_rect((10.0, 20.0), 300.0, 80.0, 1.0, (800.0, 600.0))
    assert rect.left() >= 0.0 and rect.top() >= 0.0
    assert rect.right() <= 800.0 and rect.bottom() <= 600.0


def test_size_fac_shrinks_the_box_without_moving_the_anchor():
    small = bubble_rect((400.0, 500.0), 200.0, 60.0, 0.0, (800.0, 600.0))
    full = bubble_rect((400.0, 500.0), 200.0, 60.0, 1.0, (800.0, 600.0))
    assert small.width() < full.width()
    assert abs(small.bottom() - full.bottom()) < 0.01


# --------------------------------------------------------------------------
# 真的画（Qt，短文本）
# --------------------------------------------------------------------------
def _ink(image: QImage) -> int:
    count = 0
    for y in range(0, image.height(), 4):
        for x in range(0, image.width(), 4):
            if image.pixelColor(x, y).alpha() > 8:
                count += 1
    return count


def test_layout_measures_a_short_line():
    image = QImage(400, 120, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("transparent"))
    painter = QPainter(image)
    lines, width, height = layout(painter, "你还在这里吗？", max_width=360.0)
    painter.end()
    assert lines == ["你还在这里吗？"]
    assert width > 0 and height > 0


def test_draw_bubble_paints_something():
    image = QImage(800, 600, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("transparent"))
    painter = QPainter(image)
    rect = draw_bubble(painter, text="它还在这里吗？", size_fac=1.0, anchor=(400.0, 520.0),
                       screen=(800.0, 600.0))
    painter.end()
    assert rect is not None
    assert _ink(image) > 50, "气泡应该画出可见像素"


def test_draw_bubble_skips_empty_or_closed():
    image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("transparent"))
    painter = QPainter(image)
    assert draw_bubble(painter, text="", size_fac=1.0, anchor=(160.0, 180.0),
                       screen=(320.0, 200.0)) is None
    assert draw_bubble(painter, text="滚开。", size_fac=0.0, anchor=(160.0, 180.0),
                       screen=(320.0, 200.0)) is None
    painter.end()
    assert _ink(image) == 0, "什么都不该画"
