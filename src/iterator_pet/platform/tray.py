"""Tray icon and menu so the pet can be controlled without ever taking focus."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..rendering.theme import DEFAULT_PALETTE


def make_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p = DEFAULT_PALETTE
    # Robe.
    grad = QLinearGradient(0.0, size * 0.35, 0.0, size)
    grad.setColorAt(0.0, QColor(p.robe_light))
    grad.setColorAt(0.55, QColor(p.robe_mid))
    grad.setColorAt(1.0, QColor(p.robe_dark))
    painter.setBrush(grad)
    painter.setPen(QPen(QColor("#5c2b3b"), max(1.0, size * 0.02)))
    painter.drawRoundedRect(QRectF(size * 0.30, size * 0.34, size * 0.40, size * 0.50), size * 0.16, size * 0.16)
    # Head.
    painter.setBrush(QColor(p.head_mid))
    painter.setPen(QPen(QColor("#6d3345"), max(1.0, size * 0.02)))
    painter.drawRoundedRect(QRectF(size * 0.31, size * 0.10, size * 0.38, size * 0.28), size * 0.14, size * 0.14)
    # Eyes.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.eye))
    painter.drawEllipse(QPointF(size * 0.42, size * 0.24), size * 0.045, size * 0.055)
    painter.drawEllipse(QPointF(size * 0.58, size * 0.24), size * 0.045, size * 0.055)
    # Rail hint.
    painter.setPen(QPen(QColor(p.rail_glow), max(1.0, size * 0.03)))
    painter.drawLine(QPointF(0.0, size * 0.06), QPointF(size, size * 0.06))
    painter.end()
    return QIcon(pixmap)


class PetTray(QSystemTrayIcon):
    def __init__(self, window, config, on_config_changed, parent=None):
        super().__init__(make_icon(), parent)
        self.window = window
        self.config = config
        self._on_config_changed = on_config_changed
        self.setToolTip("Iterator Pet")

        menu = QMenu()
        self.action_pause = QAction("暂停 / 继续", menu)
        self.action_pause.triggered.connect(self.window.toggle_pause)
        menu.addAction(self.action_pause)

        self.action_click = QAction("鼠标穿透", menu)
        self.action_click.setCheckable(True)
        self.action_click.setChecked(config.click_through)
        self.action_click.triggered.connect(self.window.toggle_click_through)
        menu.addAction(self.action_click)

        self.action_rail = QAction("显示滑轨", menu)
        self.action_rail.setCheckable(True)
        self.action_rail.setChecked(config.show_rail)
        self.action_rail.triggered.connect(self.window.toggle_rail)
        menu.addAction(self.action_rail)

        speed_menu = menu.addMenu("移动速度")
        for label, value in (("慢", 42.0), ("中", 74.0), ("快", 130.0)):
            action = QAction(label, speed_menu)
            action.setCheckable(True)
            action.setChecked(abs(config.speed - value) < 1.0)
            action.triggered.connect(lambda _checked=False, v=value: self.window.set_speed(v))
            speed_menu.addAction(action)
        self._speed_menu = speed_menu

        menu.addSeparator()
        action_quit = QAction("退出", menu)
        action_quit.triggered.connect(self.window.close)
        menu.addAction(action_quit)

        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window.toggle_click_through()

    def sync(self) -> None:
        self.action_click.setChecked(self.window.click_through)
        self.action_rail.setChecked(self.window.show_rail)
        for action in self._speed_menu.actions():
            expected = {"慢": 42.0, "中": 74.0, "快": 130.0}.get(action.text(), -1.0)
            action.setChecked(abs(self.window.simulation.base_speed - expected) < 1.0)
