"""Transparent widget that paints the simulation each frame.

The simulation runs in *game pixels*; ``scale`` maps those onto screen pixels so
the pet keeps the original proportions at any resolution.

**为什么要缓存一帧**：这个窗口是"整屏大小 + 分层透明"的。任何盖在它上面的窗口
（托盘菜单、开发者面板、别的置顶窗口）一走开，Windows 就会让整屏区域重绘一次。
每次重绘都从头画 2 个环 + 100 个 bit + 20 根电光 + 机械臂 + 缆线（Python 侧几毫秒），
用户看到的就是"关掉菜单/面板卡一下"。所以：

* 仿真 tick 没变（关菜单、暂停、面板只是被拖开）→ 直接把上一帧的 pixmap 贴回去；
* tick 变了 → 正常画一遍，并顺手把这一帧存下来。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .renderer import draw_scene


class PetCanvas(QWidget):
    def __init__(
        self,
        simulation,
        alpha_provider=None,
        parent=None,
        scale: float = 1.5,
        sprites=None,
    ):
        super().__init__(parent)
        self.simulation = simulation
        self.alpha_provider = alpha_provider or (lambda: 0.0)
        self.sprites = sprites
        self.scale = max(0.3, min(4.0, float(scale)))
        self._frame: QPixmap | None = None
        self._frame_key: tuple | None = None
        # 命中测试要用的"屏幕上真正画出去的那一份"（帧缓存/alpha 会让重新插值的
        # 结果和屏幕上差一个 tick，拿它去命中会点不准）
        self._drawn_state = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)

    # -- 帧缓存 -------------------------------------------------------------
    def _frame_stamp(self) -> tuple:
        """这一帧画的是什么：仿真 tick + 画布尺寸 + 缩放 + 当前仿真对象。"""
        return (
            id(self.simulation),
            int(getattr(self.simulation, "ticks", 0)),
            self.width(),
            self.height(),
            self.scale,
        )

    def _ensure_frame(self) -> QPixmap:
        """按需分配/复用整屏 pixmap（每 tick 重新分配 14 MB 会自己把自己卡住）。"""
        ratio = self.devicePixelRatioF() or 1.0
        wanted = (int(self.width() * ratio), int(self.height() * ratio))
        if (
            self._frame is None
            or self._frame.width() != wanted[0]
            or self._frame.height() != wanted[1]
        ):
            pixmap = QPixmap(wanted[0], wanted[1])
            pixmap.setDevicePixelRatio(ratio)
            self._frame = pixmap
        return self._frame

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        key = self._frame_stamp()
        if self._frame_key != key:
            pixmap = self._ensure_frame()
            pixmap.fill(Qt.GlobalColor.transparent)     # 复用同一块，只清内容
            frame_painter = QPainter(pixmap)
            frame_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            frame_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            state = self.simulation.interpolate(self.alpha_provider())
            self._drawn_state = state
            draw_scene(
                frame_painter,
                state,
                scale=self.scale,
                sprites=self.sprites,
            )
            frame_painter.end()
            self._frame_key = key

        painter.drawPixmap(0, 0, self._frame)
        painter.end()
