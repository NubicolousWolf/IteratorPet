"""人偶外观页：调色盘 + 24×20 像素头饰画板 + 右侧特写预览（v0.1.9）。

    ┌ 基底 [五块卵石 ▾]            头顶部件 (•)无 ( )原版圆点 ( )自定义像素画 ┐
    │ 身体 [■]  眼睛 [■]  衣袍上 [■] │ ┌ 24×20 画板（带头部/双眼参考线） ┐   │
    │ 衣袍下 [■]  光环 [■]  电光 [■] │ │                                │   │
    │ [笔刷1][笔刷2][橡皮][吸管]      │ │                                │   │
    │ [x] 左右对称  [撤销][清空]      │ └────────────────────────────────┘   │
    │ [导入 PNG…][导出 PNG…]          │ ┌ 特写预览（真渲染器，6×） ─────┐   │
    │                                 │ │                                │   │
    │                                 │ └────────────────────────────────┘   │
    └ 提示：配色与头饰会存进角色卡；应用到桌面人偶与新建角色在 v0.2.0 ────────┘

**预览为什么是真的**：它构造一个小小的 `SimulationState`（`warmup=0`，260×300），
先跑 140 tick 让它自己站住，然后每帧只 `dataclasses.replace(snapshot, appearance=…)`
再调**同一批** `draw_puppet_*` 函数。所以预览和桌面用的是同一套几何，
不是"另画一个长得像的"——那种预览会在 v0.2.0 接线时当场打脸。
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..llm import cards as llm_cards
from ..rendering import renderer as R
from ..rendering.pixel_art import DEFAULT_H, DEFAULT_W, PixelArt, normalize_colour
from ..simulation import appearance as ap
from ..simulation import constants as C
from ..simulation.state import SimulationState

#: 六个色块：(字段, 显示名, 提示)
COLOUR_FIELDS = (
    ("skin", "身体", "躯干 / 脖子 / 头 / 下巴 / 手脚端盖"),
    ("eye", "眼睛", "源码里是 1×1 的 pixel 精灵，靠 scale 撑成小方块"),
    ("gown_top", "衣袍上", "袍子靠脖子那一端"),
    ("gown_bottom", "衣袍下", "下摆；中间是这两色的线性插值"),
    ("halo", "光环", "环 + 100 个 bit"),
    ("halo_spark", "电光", "20 根电光；v0.1.9 起才真正独立于光环"),
)

PREVIEW_W, PREVIEW_H = 250, 300
PREVIEW_SCALE = 6.0


def _hex(colour) -> str:
    return normalize_colour(colour) or "#000000"


class ColourButton(QPushButton):
    """一块颜色按钮：显示色块 + ``#rrggbb``，点开取色器。"""

    picked = Signal(str)

    def __init__(self, caption: str, colour: str, tip: str = "", parent=None):
        super().__init__(parent)
        self.caption = caption
        self._colour = _hex(colour)
        self.setToolTip(tip or caption)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._choose)
        self._apply()

    def colour(self) -> str:
        return self._colour

    def set_colour(self, value) -> None:
        self._colour = _hex(value)
        self._apply()

    def _apply(self) -> None:
        # 深色文字压在浅色块上、浅色文字压在深色块上：别让玩家在深色皮肤上读不到自己写的色号
        r, g, b = (int(self._colour[i:i + 2], 16) for i in (1, 3, 5))
        light = 0.299 * r + 0.587 * g + 0.114 * b
        ink = "#111" if light > 140 else "#eee"
        self.setText(f"{self.caption}  {self._colour}")
        self.setStyleSheet(
            f"QPushButton{{background:{self._colour}; color:{ink};"
            f" border:1px solid rgba(0,0,0,0.35); border-radius:4px;"
            f" font-size:12px; text-align:left; padding-left:8px;}}"
        )

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self._colour), self, f"选择「{self.caption}」的颜色"
        )
        if chosen.isValid():
            self.set_colour(chosen.name())
            self.picked.emit(self._colour)


class PixelCanvas(QWidget):
    """24×20 像素画板：左键画、右键擦、对称开关、撤销栈。

    参考线是**照源码算的**：头精灵 11.1×9.1 仿真 px（`renderer.py:974`）在 24×20 画布上
    是 22.2×18.2 格；两只眼在 `head + perp·(±2.5)` 再往体轴方向 1 px
    （`renderer.py:999` 的 `eye_offset`），也就是中线左右各 5 格、往下 2 格。
    玩家照着虚线画，画出来的东西落在脸上就是对的。
    """

    changed = Signal()

    def __init__(self, art: PixelArt | None = None, cell: int = 11, parent=None):
        super().__init__(parent)
        self.art = art.copy() if art is not None else PixelArt.blank()
        self.cell = max(4, int(cell))
        self.colour = "#ff66cb"
        self.brush = 1
        self.erasing = False
        self.symmetric = False
        self._undo: list[list[list[str | None]]] = []
        self._painting = False
        self._stroke_dirty = False
        self.setFixedSize(self.art.w * self.cell, self.art.h * self.cell)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(False)

    # -- 外部接口 -----------------------------------------------------------
    def set_art(self, art: PixelArt | None) -> None:
        had = self.art
        self.art = art.copy() if art is not None else PixelArt.blank()
        self._undo.clear()
        if (self.art.w, self.art.h) != (had.w, had.h):
            self.setFixedSize(self.art.w * self.cell, self.art.h * self.cell)
        self.update()

    def push_undo(self) -> None:
        self._undo.append([row[:] for row in self.art.pixels])
        if len(self._undo) > 30:
            self._undo.pop(0)

    def undo(self) -> None:
        if not self._undo:
            return
        self.art.pixels = self._undo.pop()
        self.update()
        self.changed.emit()

    def clear(self) -> None:
        if self.art.is_empty():
            return
        self.push_undo()
        self.art.clear()
        self.update()
        self.changed.emit()

    # -- 坐标 ---------------------------------------------------------------
    def _cell_at(self, pos) -> tuple[int, int]:
        return int(pos.x()) // self.cell, int(pos.y()) // self.cell

    def _paint(self, x: int, y: int) -> None:
        targets = [(x, y)]
        if self.symmetric:
            mirror = self.art.w - 1 - x
            if mirror != x:
                targets.append((mirror, y))
        colour = None if self.erasing else self.colour
        radius = self.brush // 2
        for tx, ty in targets:
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if self.art.set(tx + dx, ty + dy, colour):
                        self._stroke_dirty = True

    # -- 事件 ---------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        self.push_undo()
        self._painting = True
        self._stroke_dirty = False
        self.erasing = event.button() == Qt.MouseButton.RightButton
        self._paint(*self._cell_at(event.position()))
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._painting:
            return
        self._paint(*self._cell_at(event.position()))
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._painting:
            return
        self._painting = False
        if self._stroke_dirty:
            self.changed.emit()
        else:
            self._undo.pop() if self._undo else None      # 空笔画不留撤销点

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # 透明底：棋盘格
        painter.fillRect(self.rect(), QColor("#202024"))
        for y in range(self.art.h):
            for x in range(self.art.w):
                if (x + y) % 2 == 0:
                    painter.fillRect(
                        x * self.cell, y * self.cell, self.cell, self.cell, QColor("#26262a")
                    )
        for y in range(self.art.h):
            for x in range(self.art.w):
                colour = self.art.pixels[y][x]
                if colour is None:
                    continue
                painter.fillRect(
                    x * self.cell, y * self.cell, self.cell, self.cell, QColor(colour)
                )
        # 网格
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for x in range(self.art.w + 1):
            painter.drawLine(x * self.cell, 0, x * self.cell, self.height())
        for y in range(self.art.h + 1):
            painter.drawLine(0, y * self.cell, self.width(), y * self.cell)
        # 参考线：头的椭圆 + 两只眼
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
        cx = self.art.w * 0.5
        cy = self.art.h * 0.5
        head_w = 20.0 * R.HEAD_RAD / 9.0 * 2.0        # 11.11 仿真 px -> 22.2 格
        head_h = 20.0 * R.HEAD_RAD / 11.0 * 2.0
        painter.drawEllipse(
            QPointF(cx * self.cell, cy * self.cell),
            head_w * 0.5 * self.cell,
            head_h * 0.5 * self.cell,
        )
        painter.setPen(QPen(QColor(255, 255, 255, 130), 1))
        for sign in (-1, 1):
            ex = cx + sign * 2.5 * 2.0               # perp 方向 ±2.5 仿真 px
            ey = cy + 1.0 * 2.0                      # 体轴方向 1 仿真 px（朝下）
            painter.drawRect(
                QRectF((ex - 0.5) * self.cell, (ey - 1.0) * self.cell,
                       self.cell, self.cell * 2.0)
            )
        painter.end()


class PuppetPreview(QWidget):
    """人偶特写：**真渲染器**画在离屏 pixmap 上。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.appearance = ap.PEBBLES
        self._snapshot = None
        self._sim = None
        self._camera: tuple[float, float, float] | None = None
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._rebuild(ap.PEBBLES)

    def _rebuild(self, appearance) -> None:
        """换基底才需要重跑仿真；只换颜色时走 `set_appearance` 的便宜路径。"""
        sim = SimulationState((0.0, 0.0, 260.0, 300.0), warmup=0,
                              character=appearance.key if appearance.key in ap.CHARACTERS
                              else "pebbles")
        # 40 TPS 固定步长（AGENTS §1 硬规则 4）：`1 / GAME_HZ`，不要自己编 dt。
        step = 1.0 / C.GAME_HZ
        for _ in range(140):
            sim.step(step)
        self._sim = sim
        self._snapshot = sim.interpolate(1.0)
        self._camera = None
        self.set_appearance(appearance)

    def set_appearance(self, appearance) -> None:
        self.appearance = appearance
        self.update()

    def _ensure_camera(self) -> tuple[float, float, float]:
        if self._camera is None:
            snap = self._snapshot
            head = snap.head
            # 头放在偏上 1/3 处，zoom 固定：人偶整体约 40 仿真 px 高，6× 刚好占满 300 px
            self._camera = (
                PREVIEW_W * 0.5 - head[0] * PREVIEW_SCALE,
                PREVIEW_H * 0.30 - head[1] * PREVIEW_SCALE,
                PREVIEW_SCALE,
            )
        return self._camera

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), QColor("#171216"))
        if self._snapshot is None:
            painter.end()
            return
        state = dataclasses.replace(self._snapshot, appearance=self.appearance)
        dx, dy, zoom = self._ensure_camera()
        painter.save()
        painter.translate(dx, dy)
        painter.scale(zoom, zoom)
        R.draw_halo(painter, state)
        R.draw_puppet_body(painter, state)
        R.draw_gown(painter, state)
        R.draw_puppet_hands(painter, state)
        R.draw_puppet_head(painter, state)
        painter.restore()
        painter.setPen(QColor(255, 255, 255, 60))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class PuppetTab(QWidget):
    """读写卡里的 ``extensions.rainworld.appearance`` 草稿。"""

    changed = Signal()

    def __init__(self, folder=None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.key = "pebbles"
        self._dirty = False
        self._loading = False
        self.buttons: dict[str, ColourButton] = {}
        self._build()
        self.load("pebbles")

    # -- 构建 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(6)

        self.base_box = QComboBox()
        for name, label in ap.BASE_TEMPLATES.items():
            self.base_box.addItem(label, name)
        self.base_box.setToolTip("骨骼/仪式差异来自基底：眼高、躯干高度、是否杀鼠标")
        left.addWidget(QLabel("骨骼基底"))
        left.addWidget(self.base_box)

        left.addSpacing(4)
        left.addWidget(QLabel("颜色"))
        for field, caption, tip in COLOUR_FIELDS:
            button = ColourButton(caption, "#000000", tip)
            button.picked.connect(self._on_colour)
            self.buttons[field] = button
            left.addWidget(button)

        left.addSpacing(6)
        left.addWidget(QLabel("头顶部件"))
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        for mode, label in ap.HEADPIECE_MODES.items():
            radio = QRadioButton(label)
            radio.toggled.connect(self._on_mode)
            self.mode_group.addButton(radio)
            self.mode_buttons[mode] = radio
            left.addWidget(radio)

        tools = QHBoxLayout()
        self.brush_box = QComboBox()
        for size, label in ((1, "1 像素"), (2, "2 像素"), (3, "3 像素")):
            self.brush_box.addItem(label, size)
        self.brush_box.currentIndexChanged.connect(self._on_brush)
        self.symmetric = QCheckBox("左右对称")
        self.symmetric.toggled.connect(self._on_symmetric)
        self.undo_button = QPushButton("撤销")
        self.clear_button = QPushButton("清空")
        for widget in (self.brush_box, self.symmetric, self.undo_button, self.clear_button):
            tools.addWidget(widget)
        left.addLayout(tools)

        io = QHBoxLayout()
        self.import_button = QPushButton("导入 PNG…")
        self.export_button = QPushButton("导出 PNG…")
        self.default_button = QPushButton("恢复默认外观")
        for widget in (self.import_button, self.export_button, self.default_button):
            io.addWidget(widget)
        left.addLayout(io)
        left.addStretch(1)

        self.canvas = PixelCanvas(None, cell=11)
        self.canvas.changed.connect(self._on_draw)
        centre = QVBoxLayout()
        centre.setSpacing(6)
        centre.addWidget(QLabel(f"头饰画板（{DEFAULT_W}×{DEFAULT_H}，1 格 = 0.5 仿真像素）"))
        centre.addWidget(self.canvas)
        self.hint = QLabel(
            "左键画、右键擦。虚线是头与双眼的位置。"
            "配色与头饰会存进角色卡；应用到桌面人偶与新建角色在 v0.2.0。"
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#888; font-size:11px;")
        centre.addWidget(self.hint)
        centre.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(QLabel("特写预览"))
        self.preview = PuppetPreview()
        right.addWidget(self.preview)
        right.addStretch(1)

        outer.addLayout(left)
        outer.addLayout(centre)
        outer.addLayout(right)

        self.undo_button.clicked.connect(self.canvas.undo)
        self.clear_button.clicked.connect(self.canvas.clear)
        self.import_button.clicked.connect(self.import_png)
        self.export_button.clicked.connect(self.export_png)
        self.default_button.clicked.connect(self.reset_appearance)
        self.base_box.currentIndexChanged.connect(self._on_base)

    # -- 载入 / 保存 ---------------------------------------------------------
    def load(self, key: str) -> None:
        self.key = key
        self._loading = True
        try:
            draft = llm_cards.read_appearance(key, self.folder)
            base = str(draft.get("base_template") or "")
            if base not in ap.BASE_TEMPLATES:
                base = key if key in ap.BASE_TEMPLATES else "pebbles"
            self.base_box.setCurrentIndex(max(0, self.base_box.findData(base)))
            template = ap.CHARACTERS.get(base) or ap.PEBBLES
            colours = {
                "skin": draft.get("skin") or template.skin,
                "eye": draft.get("eye") or template.eye,
                "gown_top": draft.get("gown_top") or template.gown(0.0),
                "gown_bottom": draft.get("gown_bottom") or template.gown(1.0),
                "halo": draft.get("halo") or template.halo,
                "halo_spark": draft.get("halo_spark") or template.halo_spark,
            }
            for field, value in colours.items():
                self.buttons[field].set_colour(_hex(value))
            mode = str(draft.get("headpiece_mode") or ("builtin_dot" if template.third_eye else "none"))
            if mode not in self.mode_buttons:
                mode = "none"
            self.mode_buttons[mode].setChecked(True)
            art = PixelArt.from_dict(draft.get("headpiece_pixels"))
            self.canvas.set_art(art)
            scale = draft.get("headpiece_scale")
            self._headpiece_scale = float(scale) if isinstance(scale, (int, float)) else 0.5
            self._refresh_preview()
        finally:
            self._loading = False
        self._dirty = False

    @property
    def headpiece_scale(self) -> float:
        return getattr(self, "_headpiece_scale", 0.5)

    def base_template(self) -> str:
        return self.base_box.currentData() or "pebbles"

    def headpiece_mode(self) -> str:
        for mode, radio in self.mode_buttons.items():
            if radio.isChecked():
                return mode
        return "none"

    def collect(self) -> dict:
        """→ 写进卡里的 appearance 字典。"""
        draft: dict = {"base_template": self.base_template()}
        for field, _caption, _tip in COLOUR_FIELDS:
            draft[field] = list(_rgb(self.buttons[field].colour()))
        draft["headpiece_mode"] = self.headpiece_mode()
        if self.headpiece_mode() == "pixel" and not self.canvas.art.is_empty():
            try:
                draft["headpiece_pixels"] = self.canvas.art.to_dict()
            except ValueError:
                pass                          # 颜色太多：这次先不存像素图，保存时会提示
            draft["headpiece_scale"] = self.headpiece_scale
        return draft

    def _pristine_draft(self) -> dict:
        """"什么都没改"时该长什么样：色值等于基底、头饰模式等于基底的默认。"""
        base = self.base_template()
        template = ap.CHARACTERS.get(base) or ap.PEBBLES
        draft = {
            "base_template": base,
            "skin": list(template.skin),
            "eye": list(template.eye),
            "gown_top": list(template.gown(0.0)),
            "gown_bottom": list(template.gown(1.0)),
            "halo": list(template.halo),
            "halo_spark": list(template.halo_spark),
            "headpiece_mode": "builtin_dot" if template.third_eye else "none",
        }
        return draft

    def save(self) -> str:
        draft = self.collect()
        if self.headpiece_mode() == "pixel" and not self.canvas.art.is_empty():
            try:
                self.canvas.art.to_dict()
            except ValueError as exc:
                return f"头饰没存下来：{exc}"
        # 一个色都没改、头饰也没开 → **删掉覆盖层**，别在卡里留一份"和源码一模一样"的
        # 外观块。内置两位的默认值必须永远来自 `appearance.py`（那里有源码行号），
        # 写进卡里就成了第二份真相，以后改默认值会被玩家文件盖住。
        payload = None if draft == self._pristine_draft() else draft
        error = llm_cards.save_appearance(self.key, payload, self.folder)
        if error:
            return error
        self._dirty = False
        return ""

    def is_dirty(self) -> bool:
        return self._dirty

    def reset_appearance(self) -> None:
        """删掉覆盖层 = 回到源码数值（内置两位就是 `appearance.py` 里那套）。"""
        answer = QMessageBox.question(
            self, "恢复默认外观",
            "把这个角色的配色与头饰草稿删掉，回到源码默认值？\n（角色卡的文字内容不会动）",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        error = llm_cards.save_appearance(self.key, None, self.folder)
        if error:
            QMessageBox.warning(self, "恢复默认失败", error)
            return
        self.load(self.key)

    # -- 交互 ---------------------------------------------------------------
    def _on_colour(self, *_args) -> None:
        self._mark_dirty()
        self._refresh_preview()

    def _on_base(self, *_args) -> None:
        if self._loading:
            return
        self._mark_dirty()
        # 换基底要把没改过的色块跟过去，否则玩家会拿着卵石的皮肤看皓月的骨骼
        self.load_colours_from_template()
        self._refresh_preview(rebuild=True)

    def load_colours_from_template(self) -> None:
        template = ap.CHARACTERS.get(self.base_template()) or ap.PEBBLES
        for field, value in (("skin", template.skin), ("eye", template.eye),
                             ("gown_top", template.gown(0.0)),
                             ("gown_bottom", template.gown(1.0)),
                             ("halo", template.halo),
                             ("halo_spark", template.halo_spark)):
            self.buttons[field].set_colour(_hex(value))

    def _on_mode(self, *_args) -> None:
        if self._loading:
            return
        self._mark_dirty()

    def _on_brush(self, *_args) -> None:
        self.canvas.brush = int(self.brush_box.currentData() or 1)

    def _on_symmetric(self, checked: bool) -> None:
        self.canvas.symmetric = bool(checked)

    def _on_draw(self) -> None:
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
        self.changed.emit()

    def _refresh_preview(self, rebuild: bool = False) -> None:
        base = self.base_template()
        if rebuild or getattr(self, "_built_key", None) != base:
            self._built_key = base
            self.preview._rebuild(ap.build_appearance(base, base, None))
        appearance = ap.build_appearance(self.key, base, self.collect())
        self.preview.set_appearance(appearance)

    # -- PNG ----------------------------------------------------------------
    def import_png(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "导入像素画", "", "PNG 图片 (*.png)"
        )
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            QMessageBox.warning(self, "导入失败", "这个文件读不出来。")
            return
        target = self.canvas.art
        scaled = image.scaled(
            target.w, target.h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        art = PixelArt.blank(target.w, target.h)
        for y in range(target.h):
            for x in range(target.w):
                colour = scaled.pixelColor(x, y)
                if colour.alpha() < 40:
                    continue
                art.set(x, y, colour.name())
        self.canvas.set_art(art)
        self._on_draw()

    def export_png(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出像素画", f"{self.key}-headpiece.png", "PNG 图片 (*.png)"
        )
        if not path:
            return
        art = self.canvas.art
        image = QImage(art.w, art.h, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        for y in range(art.h):
            for x in range(art.w):
                colour = art.pixels[y][x]
                if colour is not None:
                    image.setPixelColor(x, y, QColor(colour))
        if not image.save(path, "PNG"):
            QMessageBox.warning(self, "导出失败", "写不进这个位置。")


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    text = _hex(hex_colour)
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
