"""Tray icon and menu so the pet can be controlled without ever taking focus."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..rendering.theme import DEFAULT_PALETTE

# 缩略图：`assets/tray.png` 是自有渲染管线画出来的人偶全身像（左起第一个，
# 与 `tools/puppet_portrait.py` 的联络表同源），发布版用它代替早期那个
# 纯手绘的"袍子 + 头 + 眼睛 + 一条轨道线"图标。
TRAY_ICON_NAME = "tray.png"


def _asset_path(name: str) -> Path:
    """包里 / PyInstaller 解包目录里找 assets。"""
    if hasattr(sys, "_MEIPASS"):                      # onefile 打包后
        return Path(sys._MEIPASS) / "iterator_pet" / "assets" / name
    return Path(__file__).resolve().parent.parent / "assets" / name


def _portrait_pixmap(size: int) -> QPixmap | None:
    path = _asset_path(TRAY_ICON_NAME)
    if not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    return pixmap.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def make_icon(size: int = 64) -> QIcon:
    portrait = _portrait_pixmap(size)
    if portrait is not None:
        icon = QIcon()
        for s in (16, 24, 32, 48, 64, 128, 256):
            scaled = _portrait_pixmap(s)
            if scaled is not None:
                icon.addPixmap(scaled)
        return icon

    # 兜底：没有 assets 时仍画一个能认出来的东西（也保证测试环境不炸）。
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p = DEFAULT_PALETTE
    grad = QLinearGradient(0.0, size * 0.35, 0.0, size)
    grad.setColorAt(0.0, QColor(p.robe_light))
    grad.setColorAt(0.55, QColor(p.robe_mid))
    grad.setColorAt(1.0, QColor(p.robe_dark))
    painter.setBrush(grad)
    painter.setPen(QPen(QColor("#5c2b3b"), max(1.0, size * 0.02)))
    painter.drawRoundedRect(QRectF(size * 0.30, size * 0.34, size * 0.40, size * 0.50), size * 0.16, size * 0.16)
    painter.setBrush(QColor(p.head_mid))
    painter.setPen(QPen(QColor("#6d3345"), max(1.0, size * 0.02)))
    painter.drawRoundedRect(QRectF(size * 0.31, size * 0.10, size * 0.38, size * 0.28), size * 0.14, size * 0.14)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.eye))
    painter.drawEllipse(QPointF(size * 0.42, size * 0.24), size * 0.045, size * 0.055)
    painter.drawEllipse(QPointF(size * 0.58, size * 0.24), size * 0.045, size * 0.055)
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

        menu.addSeparator()
        self.action_dev = QAction("开发者面板", menu)
        self.action_dev.setCheckable(True)
        self._on_dev_panel = None
        self.action_dev.triggered.connect(self._toggle_dev_panel)
        menu.addAction(self.action_dev)

        menu.addSeparator()
        action_quit = QAction("退出", menu)
        action_quit.triggered.connect(self.window.close)
        menu.addAction(action_quit)

        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def attach_dev_panel(self, callback) -> None:
        """``callback() -> bool`` (visible after the toggle). Without it the item is inert."""
        self._on_dev_panel = callback

    def _toggle_dev_panel(self) -> None:
        if self._on_dev_panel is None:
            self.action_dev.setChecked(False)
            return
        self.action_dev.setChecked(bool(self._on_dev_panel()))

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window.toggle_click_through()

    def sync(self) -> None:
        self.action_click.setChecked(self.window.click_through)

