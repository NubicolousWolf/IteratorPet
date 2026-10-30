"""图标绘制 + 托盘菜单。

托盘**图标本体**改用 `platform/win_tray.py`（Win32 `Shell_NotifyIconW`）：`QSystemTrayIcon`
在这个环境里打包后注册不上（连最小 exe 都复现）。这里只留两件 Qt 该管的事：
1. `make_icon()` —— 给窗口/任务栏用的 QIcon；
2. `tray_menu()` —— 右键托盘时弹出的菜单。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu

from ..config import SIZE_PRESETS
from ..rendering.theme import DEFAULT_PALETTE

TRAY_ICON_NAME = "tray.png"
ICON_SIZE = 64


def _asset_path(name: str) -> Path:
    """包里 / PyInstaller 解包目录里找 assets。"""
    if hasattr(sys, "_MEIPASS"):                      # 打包后
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


def _draw_fallback(painter: QPainter, size: int) -> None:
    """没有 assets 时的手绘图标（旧版那套：袍子 + 头 + 眼睛）。"""
    p = DEFAULT_PALETTE
    grad = QLinearGradient(0.0, size * 0.35, 0.0, size)
    grad.setColorAt(0.0, QColor(p.robe_light))
    grad.setColorAt(0.55, QColor(p.robe_mid))
    grad.setColorAt(1.0, QColor(p.robe_dark))
    painter.setBrush(grad)
    painter.setPen(QPen(QColor("#5c2b3b"), max(1.0, size * 0.02)))
    painter.drawRoundedRect(
        QRectF(size * 0.30, size * 0.34, size * 0.40, size * 0.50), size * 0.16, size * 0.16
    )
    painter.setBrush(QColor(p.head_mid))
    painter.setPen(QPen(QColor("#6d3345"), max(1.0, size * 0.02)))
    painter.drawRoundedRect(
        QRectF(size * 0.31, size * 0.10, size * 0.38, size * 0.28), size * 0.14, size * 0.14
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.eye))
    painter.drawEllipse(QPointF(size * 0.42, size * 0.24), size * 0.045, size * 0.055)
    painter.drawEllipse(QPointF(size * 0.58, size * 0.24), size * 0.045, size * 0.055)


def make_icon(size: int = ICON_SIZE) -> QIcon:
    """单张 pixmap 的 QIcon（窗口图标用）。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    portrait = _portrait_pixmap(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if portrait is not None:
        painter.drawPixmap((size - portrait.width()) // 2, (size - portrait.height()) // 2, portrait)
    else:
        _draw_fallback(painter, size)
    painter.end()
    return QIcon(pixmap)


def tray_menu(window, on_dev_panel=None) -> QMenu:
    """右键托盘时弹出的菜单（不持有引用：每次现建，避免状态过期）。"""
    menu = QMenu()
    action_pause = QAction("继续" if getattr(window, "paused", False) else "暂停", menu)
    action_pause.triggered.connect(window.toggle_pause)
    menu.addAction(action_pause)

    action_click = QAction("鼠标穿透", menu)
    action_click.setCheckable(True)
    action_click.setChecked(bool(getattr(window, "click_through", False)))
    action_click.triggered.connect(window.toggle_click_through)
    menu.addAction(action_click)

    menu.addSeparator()
    action_dev = QAction("开发者面板", menu)
    action_dev.setCheckable(True)
    action_dev.setEnabled(on_dev_panel is not None)
    if on_dev_panel is not None:
        action_dev.triggered.connect(lambda: action_dev.setChecked(bool(on_dev_panel())))
    menu.addAction(action_dev)

    menu.addSeparator()
    action_cursor = QAction("光标特效（杀鼠标时）", menu)
    action_cursor.setCheckable(True)
    action_cursor.setChecked(bool(getattr(window.config, "cursor_fx", True)))
    def _toggle_cursor(checked: bool) -> None:
        window.config.cursor_fx = bool(checked)
        from . import cursorfx
        cursorfx.set_enabled(bool(checked))
        save(window.config)
    action_cursor.triggered.connect(_toggle_cursor)
    menu.addAction(action_cursor)

    size_menu = menu.addMenu("尺寸")
    current_size = getattr(window, "size_preset", "full")
    for key, (factor, label) in SIZE_PRESETS.items():
        action_size = QAction(label, size_menu)
        action_size.setCheckable(True)
        action_size.setChecked(current_size == key)
        action_size.triggered.connect(lambda _checked=False, name=key: window.set_size(name))
        size_menu.addAction(action_size)

    spark_menu = menu.addMenu("电光频率")
    current_rate = getattr(window, "spark_rate", "eco")
    for key, label in (("off", "无"), ("eco", "节能"), ("computing", "computing（1.5×）")):
        action_rate = QAction(label, spark_menu)
        action_rate.setCheckable(True)
        action_rate.setChecked(current_rate == key)
        action_rate.triggered.connect(
            lambda _checked=False, name=key: window.set_spark_rate(name)
        )
        spark_menu.addAction(action_rate)

    character_menu = menu.addMenu("角色")
    current_character = getattr(window, "character", "pebbles")
    for key, label in (("pebbles", "五块卵石"), ("past_moon", "仰望皓月")):
        action_character = QAction(label, character_menu)
        action_character.setCheckable(True)
        action_character.setChecked(current_character == key)
        action_character.triggered.connect(
            lambda _checked=False, name=key: window.set_character(name)
        )
        character_menu.addAction(action_character)

    action_edit_lines = QAction("自定义对话", menu)
    action_edit_lines.triggered.connect(window.open_dialogue_editor)
    menu.addAction(action_edit_lines)

    action_open_data = QAction("打开数据文件夹", menu)
    action_open_data.triggered.connect(window.open_data_folder)
    menu.addAction(action_open_data)
    menu.addSeparator()

    action_dialogue = QAction("对话", menu)
    action_dialogue.setCheckable(True)
    action_dialogue.setChecked(bool(getattr(window.config, "dialogue_enabled", True)))
    action_dialogue.triggered.connect(
        lambda checked: window.set_dialogue_enabled(bool(checked))
    )
    menu.addAction(action_dialogue)

    menu.addSeparator()
    action_quit = QAction("退出", menu)
    action_quit.triggered.connect(window.close)
    menu.addAction(action_quit)
    return menu
