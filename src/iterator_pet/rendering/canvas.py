"""Transparent widget that paints the simulation each frame.

The simulation runs in *game pixels*; ``scale`` maps those onto screen pixels so
the pet keeps the original proportions at any resolution.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        state = self.simulation.interpolate(self.alpha_provider())
        draw_scene(
            painter,
            state,
            scale=self.scale,
            sprites=self.sprites,
        )
        painter.end()
