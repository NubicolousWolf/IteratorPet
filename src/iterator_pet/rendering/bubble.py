"""对话框气泡：形状照游戏本体的 ``HUD.DialogBox``，颜色/位置按用户拍板。

从 IL 与资源里拿到的**几何**（`docs/PLAN-dialogue.md` §1）：

* 四角是 ``UIroundedCorner``，**7×7** → 圆角半径 7；
* 边框是 ``pixel`` 条、``scaleX/scaleY = 2`` → **2 px 描边**；
* 弹出/收起是 ``sizeFac`` 每帧逼近 **1/6** → 由 ``DialogueState`` 提供 0→1 的进度。

用户 2026-10-29 拍板的部分（不是游戏的）：**深色半透明底**、**固定 27 屏幕 px**、
**不写角色名**、位置 = 脐带缆插口往上两格。

**为什么逐字量宽度**：离屏平台下 `horizontalAdvance(长中文串)` 会让 FreeType abort；
逐字测量既能避开它，也让折行逻辑可以脱离 Qt 单测（见 `tests/test_bubble.py`）。

**为什么用矢量圆角而不是贴 7×7 角图**：那四个角就是一块白色圆角瓦片，形状等于半径 7 的
圆角矩形；用 ``QPainterPath`` 画在任何 dpr 下都不糊（贴图在 1.5 dpr 下会被重采样）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QBrush

from ..simulation import constants as C
from .font import bubble_font


def _qc(rgb: tuple[int, int, int], alpha: float) -> QColor:
    colour = QColor(rgb[0], rgb[1], rgb[2])
    colour.setAlpha(int(max(0.0, min(1.0, alpha)) * 255))
    return colour


def char_measure(metrics) -> "callable":
    """返回 ``measure(text) -> float``：**逐字**累加 advance，并缓存单字宽度。

    不用 `horizontalAdvance(整串)`：离屏平台下量长中文串会让 FreeType 直接 abort
    （实测 `Fatal Python error: Aborted`），而且整串 shaping 比逐字慢。
    """
    cache: dict[str, float] = {}

    def measure(text: str) -> float:
        total = 0.0
        for char in text:
            width = cache.get(char)
            if width is None:
                width = float(metrics.horizontalAdvance(char))
                cache[char] = width
            total += width
        return total

    return measure


def wrap_text(text: str, measure, max_width: float) -> list[str]:
    """按像素宽度折行。中文逐字断，英文尽量在空格处断。

    ``measure`` 是"字符串 → 像素宽"的可注入函数（生产用字体度量，测试用假的）。
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        width = 0.0
        for char in paragraph:
            char_w = measure(char)
            if current and width + char_w > max_width:
                if char.isascii() and not char.isspace() and " " in current:
                    head, _, tail = current.rpartition(" ")
                    lines.append(head)
                    current = tail
                    width = measure(current)
                else:
                    lines.append(current)
                    current = ""
                    width = 0.0
            current += char
            width += char_w
        lines.append(current)
    return lines or [""]


def fit_lines(text: str, measure, max_width: float,
               max_lines: int = C.DIALOGUE_MAX_LINES) -> list[str]:
    """折行 + 超行截断（加 ``…``）。**纯函数**：``measure`` 由调用方给。"""
    lines = wrap_text(text, measure, max(1.0, max_width))
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    while last and measure(last + "…") > max_width:
        last = last[:-1]
    kept[-1] = (last or "") + "…"
    return kept


def layout(painter: QPainter, text: str, *, font_px: float = C.DIALOGUE_FONT_PX,
           max_width: float | None = None) -> tuple[list[str], float, float]:
    """算出行、宽、高（不含内边距）。超出行数上限就用 ``…`` 截断。"""
    font = bubble_font(int(font_px))
    measure = char_measure(QFontMetricsF(font))
    limit = max_width if max_width is not None else C.DIALOGUE_MAX_CHARS * font_px
    lines = fit_lines(text, measure, limit)
    width = max((measure(line) for line in lines), default=1.0)
    line_height = font_px * C.DIALOGUE_LINE_SPACING
    height = line_height * len(lines)
    return lines, width, height


def bubble_rect(anchor_px: tuple[float, float], width: float, height: float,
                size_fac: float, screen: tuple[float, float]) -> QRectF:
    """锚点是气泡**底边中点**；``size_fac`` 让它从 0.92 长到 1。"""
    scale = 0.92 + 0.08 * max(0.0, min(1.0, size_fac))
    w = width * scale
    h = height * scale
    x = anchor_px[0] - w * 0.5
    y = anchor_px[1] - h
    margin = 6.0
    x = max(margin, min(screen[0] - margin - w, x))
    y = max(margin, min(screen[1] - margin - h, y))
    return QRectF(x, y, w, h)


def draw_bubble(painter: QPainter, *, text: str, size_fac: float,
                anchor: tuple[float, float],
                screen: tuple[float, float]) -> QRectF | None:
    """在**屏幕像素**坐标系里画一个气泡（调用方不要预先 scale）。

    ``anchor`` 是屏幕 px 的底边中点；``text`` 是**已经按打字机截断过**的可见文本。
    返回画出来的矩形（给测试/调试用）。
    """
    if not text or size_fac <= 0.01:
        return None
    visible = max(0.0, min(1.0, size_fac))
    font = bubble_font(int(C.DIALOGUE_FONT_PX))
    painter.save()
    painter.setFont(font)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    avail = max(80.0, min(C.DIALOGUE_MAX_CHARS * C.DIALOGUE_FONT_PX,
                          screen[0] * C.DIALOGUE_MAX_WIDTH_RATIO)) - 2 * C.DIALOGUE_PAD_X
    lines, text_w, text_h = layout(painter, text, max_width=avail)
    box_w = text_w + 2 * C.DIALOGUE_PAD_X
    box_h = text_h + 2 * C.DIALOGUE_PAD_Y
    rect = bubble_rect(anchor, box_w, box_h, size_fac, screen)

    path = QPainterPath()
    path.addRoundedRect(rect, C.DIALOGUE_CORNER, C.DIALOGUE_CORNER)

    painter.setBrush(QBrush(_qc(C.DIALOGUE_BG, C.DIALOGUE_BG_ALPHA * visible)))
    painter.setPen(QPen(_qc(C.DIALOGUE_BORDER_COLOR, C.DIALOGUE_BORDER_ALPHA * visible),
                        C.DIALOGUE_BORDER))
    painter.drawPath(path)

    painter.setPen(_qc(C.DIALOGUE_TEXT, visible))
    line_height = C.DIALOGUE_FONT_PX * C.DIALOGUE_LINE_SPACING
    baseline_box = QRectF(
        rect.left() + C.DIALOGUE_PAD_X,
        rect.top() + C.DIALOGUE_PAD_Y,
        max(1.0, rect.width() - 2 * C.DIALOGUE_PAD_X),
        max(1.0, rect.height() - 2 * C.DIALOGUE_PAD_Y),
    )
    for index, line in enumerate(lines):
        painter.drawText(
            QRectF(baseline_box.left(), baseline_box.top() + index * line_height,
                   baseline_box.width(), line_height),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            line,
        )
    painter.restore()
    return rect


def draw_bubble_world(painter: QPainter, *, text: str, size_fac: float,
                      anchor_sim: tuple[float, float], scale: float,
                      screen: tuple[float, float]) -> QRectF | None:
    """``anchor_sim`` 是仿真 px；**这里是唯一一次坐标转换**（审计 A1）。"""
    anchor_px = (anchor_sim[0] * scale, anchor_sim[1] * scale)
    return draw_bubble(painter, text=text, size_fac=size_fac, anchor=anchor_px,
                       screen=screen)
