"""人偶外观页：调色盘 + 三个像素画板（头顶 / 天线 / 衣袍）+ 右侧特写预览。

    ┌ 基底 [五块卵石 ▾]              画板 [头顶部件 ▾]  ┌ 特写预览 ┐
    │ 颜色 身体[■] 眼睛[■] 衣袍上[■]                    │          │
    │      衣袍下[■] 光环[■] 电光[■]                    │  真渲染器 │
    │ 天线 耳麦[■] 内圈[■] 触角[■]                      │   6×     │
    │ 头顶部件 (•)无 ( )原版圆点 ( )自定义               │          │
    │ [x] 自定义天线  [x] 自定义衣袍                     │          │
    │ 画笔 [■] 最近：□□□□□□□□   笔刷[2▾] [x]吸管       └──────────┘
    │ ┌ 画板（当前那个）─────────────┐ [撤销][清空]                   │
    │ │                               │ [导入 PNG…][导出 PNG…]        │
    │ └───────────────────────────────┘                              │
    └ 提示：配色与画板会存进角色卡；应用到桌面人偶与新建角色在 v0.2.0 ─┘

三个画板共用一套工具（画笔颜色 / 笔刷 / 吸管 / 撤销），各自保留自己的撤销历史。
**画笔颜色是必须有的**：第一版漏了它，画板只有一个写死的默认色，玩家根本没法选
（用户 2026-10-31 报）。
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
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..llm import cards as llm_cards
from ..rendering import renderer as R
from ..rendering.pixel_art import DEFAULT_H, DEFAULT_W, PixelArt, normalize_colour
from ..simulation import appearance as ap
from ..simulation import constants as C
from ..simulation.state import SimulationState

#: 六个配色：(字段, 显示名, 提示)
COLOUR_FIELDS = (
    ("skin", "身体", "躯干 / 脖子 / 头 / 下巴 / 手脚端盖"),
    ("eye", "眼睛", "源码里是 1×1 的 pixel 精灵，靠 scale 撑成小方块"),
    ("gown_top", "衣袍上", "袍子靠脖子那一端"),
    ("gown_bottom", "衣袍下", "下摆；中间是这两色的线性插值"),
    ("halo", "光环", "环 + 100 个 bit"),
    ("halo_spark", "电光", "20 根电光"),
)

#: 三个侧边/袍子配色（原来的写死值现在可改）
ANTENNA_FIELDS = (
    ("pod_colour", "耳麦", "头两侧的两个圆（PhoneSprite）"),
    ("pod_rim_colour", "内圈", "叠在外圈上的那个小圆"),
    ("antenna_colour", "触角", "从耳麦朝外的一根（LizardScaleA1）"),
)

#: 两个画板：(键, 标题, 宽, 高, 每格像素, 参考线类型)
#:
#: 衣袍画板（11×11 采样到袍身网格）做过又删了（用户 2026-10-31：效果不好）。
#: 原因是袍身是一个**会变形的布网格**，玩家画的方块贴上去会跟着扭曲、还被两只袖子
#: 盖掉大半，画什么也看不出原样。留一块画不动的画板比没有更糟。
BOARDS = (
    ("headpiece", "头顶部件 24×20", DEFAULT_W, DEFAULT_H, 11, "head"),
    ("antenna", "天线 12×29（含耳麦，左右镜像）", 12, 29, 9, "antenna"),
)

#: 每个画板该有多大。**控件尺寸、画布数据尺寸、参考线都从这一个表来**——
#: 2026-10-31 用户报"天线轮廓只露出最左边一部分"，根因就是构造时给了个 24×20 的空白画布、
#: 却把控件设成 8×24 的尺寸：控件只有 88 px 宽，而 paintEvent 按 24 格 × 11 px = 264 px 画，
#: 右边三分之二直接被裁掉。尺寸必须只有一个来源。
BOARD_SIZES = {board[0]: (board[2], board[3]) for board in BOARDS}


def fit_art(key: str, art: PixelArt | None) -> PixelArt:
    """把任意尺寸的像素画塞进这个画板：尺寸不对就**左上对齐**裁剪/补透明。

    存档里可能留着旧版本的尺寸（或玩家手改过 w/h），不能让它们把画板撑变形。
    """
    width, height = BOARD_SIZES.get(key, (DEFAULT_W, DEFAULT_H))
    out = PixelArt.blank(width, height)
    if art is None:
        return out
    for y in range(min(height, art.h)):
        for x in range(min(width, art.w)):
            out.pixels[y][x] = art.pixels[y][x]
    return out

PREVIEW_W, PREVIEW_H = 250, 300
PREVIEW_SCALE = 6.0


def _hex(colour) -> str:
    return normalize_colour(colour) or "#000000"


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    text = _hex(hex_colour)
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


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
        # 深色文字压在浅色块上、浅色文字压在深色块上：别让玩家在深色皮肤上读不到色号
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
    """像素画板：左键画、右键擦、Alt/吸管模式取色、对称开关、撤销栈。"""

    changed = Signal()
    picked = Signal(str)

    def __init__(self, art: PixelArt | None = None, cell: int = 11,
                 reference: str = "head", parent=None):
        super().__init__(parent)
        self.art = art.copy() if art is not None else PixelArt.blank()
        self.cell = max(4, int(cell))
        self.reference = reference          # "head" / "antenna"
        self.colour = "#ff66cb"
        self.brush = 1
        self.erasing = False
        self.symmetric = False
        self.pick_mode = False
        self._undo: list[list[list[str | None]]] = []
        self._painting = False
        self._stroke_dirty = False
        self.setFixedSize(self.art.w * self.cell, self.art.h * self.cell)
        self.setCursor(Qt.CursorShape.CrossCursor)

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

    def _pick(self, x: int, y: int) -> None:
        colour = self.art.get(x, y)
        if colour:
            self.colour = colour
            self.picked.emit(colour)

    # -- 事件 ---------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        cell = self._cell_at(event.position())
        picking = self.pick_mode or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if picking and event.button() == Qt.MouseButton.LeftButton:
            self._pick(*cell)
            return
        self.push_undo()
        self._painting = True
        self._stroke_dirty = False
        self.erasing = event.button() == Qt.MouseButton.RightButton
        self._paint(*cell)
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
        elif self._undo:
            self._undo.pop()                # 空笔画不留撤销点

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
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
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        for x in range(self.art.w + 1):
            painter.drawLine(x * self.cell, 0, x * self.cell, self.height())
        for y in range(self.art.h + 1):
            painter.drawLine(0, y * self.cell, self.width(), y * self.cell)
        self._paint_reference(painter)
        painter.end()

    def _paint_reference(self, painter: QPainter) -> None:
        """参考线：**照源码算的位置**，不是随手画的示意。"""
        if self.reference == "head":
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
        elif self.reference == "antenna":
            # **整条侧边部件的外轮廓**：耳麦外圈（5.0 px ⌀）+ 内圈（×0.8）+ 触角
            # （3.0 × 11.2 px）。1 仿真 px = 2 格，所以半径/宽度都 ×2。
            # 朝向与屏幕一致：**触角尖在上、耳麦在底**（圆心行由 antenna_pod_center_row 给）。
            cell = self.cell
            cx = self.art.w * 0.5 * cell
            cy = ap.antenna_pod_center_row(self.art.h) * cell
            painter.setPen(QPen(QColor(255, 255, 255, 95), 1, Qt.PenStyle.DashLine))
            painter.drawEllipse(QPointF(cx, cy),
                                ap.ANTENNA_POD_DIAMETER_PX * cell,
                                ap.ANTENNA_POD_DIAMETER_PX * cell)
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DotLine))
            painter.drawEllipse(QPointF(cx, cy),
                                ap.ANTENNA_POD_DIAMETER_PX * 0.8 * cell,
                                ap.ANTENNA_POD_DIAMETER_PX * 0.8 * cell)
            painter.setPen(QPen(QColor(255, 200, 120, 150), 1, Qt.PenStyle.DashLine))
            stalk_w = ap.ANTENNA_STALK_WIDTH_PX * 2.0 * cell
            stalk_h = ap.ANTENNA_STALK_LENGTH_PX * 2.0 * cell
            painter.drawRect(QRectF(cx - stalk_w * 0.5, cy - stalk_h, stalk_w, stalk_h))
        else:
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class PuppetPreview(QWidget):
    """人偶特写：**真渲染器**画在离屏 pixmap 上。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.appearance = ap.PEBBLES
        self._snapshot = None
        self._sim = None
        self._camera: tuple[float, float, float] | None = None
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
        snapshot = sim.interpolate(1.0)
        # 正面正脸（用户 2026-10-31）：look 归零 = 正对镜头。
        # `face_look(0,0)` 得 (0,0) → 眼距对称（`lateral = ±2.5`）、两只眼与第三只眼都落在
        # 中轴上，正是"证件照"要的朝向；不归零的话预览会跟着当时的鼠标视线歪。
        self._snapshot = dataclasses.replace(snapshot, look_dir=(0.0, 0.0))
        self._camera = None
        self.set_appearance(appearance)

    def set_appearance(self, appearance) -> None:
        self.appearance = appearance
        self.update()

    def _ensure_camera(self) -> tuple[float, float, float]:
        if self._camera is None:
            head = self._snapshot.head
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
        self._headpiece_scale = 0.5
        self._antenna_scale = 0.5
        self._recent: list[str] = []
        self.buttons: dict[str, ColourButton] = {}
        self.canvases: dict[str, PixelCanvas] = {}
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

        left.addWidget(QLabel("颜色"))
        grid = QGridLayout()
        grid.setSpacing(4)
        for index, (field, caption, tip) in enumerate(COLOUR_FIELDS):
            button = ColourButton(caption, "#000000", tip)
            button.picked.connect(self._on_colour)
            self.buttons[field] = button
            grid.addWidget(button, index // 2, index % 2)
        left.addLayout(grid)

        left.addWidget(QLabel("天线配色（不用自定义画板时生效）"))
        antenna_grid = QGridLayout()
        antenna_grid.setSpacing(4)
        for index, (field, caption, tip) in enumerate(ANTENNA_FIELDS):
            button = ColourButton(caption, "#000000", tip)
            button.picked.connect(self._on_colour)
            self.buttons[field] = button
            antenna_grid.addWidget(button, index // 3, index % 3)
        left.addLayout(antenna_grid)

        left.addWidget(QLabel("头顶部件"))
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        for mode, label in ap.HEADPIECE_MODES.items():
            radio = QRadioButton(label)
            radio.toggled.connect(self._on_mode)
            self.mode_group.addButton(radio)
            self.mode_buttons[mode] = radio
            left.addWidget(radio)

        self.antenna_enabled = QCheckBox("使用自定义天线（左右镜像同一张）")
        self.antenna_enabled.toggled.connect(self._on_mode)
        left.addWidget(self.antenna_enabled)

        left.addSpacing(4)
        left.addWidget(QLabel("画笔"))
        brush_row = QHBoxLayout()
        self.brush_colour = ColourButton("画笔", "#ff66cb", "画板当前落笔颜色")
        self.brush_colour.picked.connect(self._on_brush_colour)
        brush_row.addWidget(self.brush_colour, 1)
        left.addLayout(brush_row)

        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(3)
        left.addLayout(self.recent_row)

        tools = QHBoxLayout()
        self.brush_box = QComboBox()
        for size, label in ((1, "1 像素"), (2, "2 像素"), (3, "3 像素")):
            self.brush_box.addItem(label, size)
        self.brush_box.currentIndexChanged.connect(self._on_brush)
        self.pick_box = QCheckBox("吸管")
        self.pick_box.setToolTip("勾上后左键取色；不勾也可以按住 Alt 取色")
        self.pick_box.toggled.connect(self._on_pick_mode)
        self.symmetric = QCheckBox("左右对称")
        self.symmetric.toggled.connect(self._on_symmetric)
        for widget in (self.brush_box, self.pick_box, self.symmetric):
            tools.addWidget(widget)
        left.addLayout(tools)
        left.addStretch(1)

        # ---- 中间：三个画板 ----
        centre = QVBoxLayout()
        centre.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(QLabel("画板"))
        self.board_box = QComboBox()
        for key, title, _w, _h, _cell, _ref in BOARDS:
            self.board_box.addItem(title, key)
        self.board_box.currentIndexChanged.connect(self._on_board)
        head.addWidget(self.board_box, 1)
        self.undo_button = QPushButton("撤销")
        self.clear_button = QPushButton("清空")
        head.addWidget(self.undo_button)
        head.addWidget(self.clear_button)
        centre.addLayout(head)

        self.stack = QStackedWidget()
        for key, _title, width, height, cell, reference in BOARDS:
            # 一开始就要给**对尺寸**的空白画布：控件尺寸由 art 尺寸推出来（见 BOARD_SIZES 注释）
            canvas = PixelCanvas(PixelArt.blank(width, height), cell=cell, reference=reference)
            canvas.changed.connect(self._on_draw)
            canvas.picked.connect(self._on_picked)
            self.canvases[key] = canvas
            # **必须套滚动区**：窗口一矮，布局会把画布往下裁（用户 2026-10-31 报
            # "画板下半部分截断"）。固定尺寸的子控件塞进小容器，Qt 只会把它裁掉，
            # 不会报错也不会缩——套上 QScrollArea 之后，空间不够就出滚动条，一个像素不丢。
            page = QScrollArea()
            page.setFrameShape(QFrame.Shape.NoFrame)
            page.setWidgetResizable(False)          # 画布保持 1:1，绝不为布局让路
            page.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            page.setWidget(canvas)
            self.stack.addWidget(page)
        centre.addWidget(self.stack, 1)

        io = QHBoxLayout()
        self.import_button = QPushButton("导入 PNG…")
        self.export_button = QPushButton("导出 PNG…")
        self.default_button = QPushButton("恢复默认外观")
        for widget in (self.import_button, self.export_button, self.default_button):
            io.addWidget(widget)
        io.addStretch(1)
        centre.addLayout(io)

        sizes = "　".join(
            f"{title.split(' ')[0]} {width}×{height}"
            for _k, title, width, height, _c, _r in BOARDS
        )
        self.hint = QLabel(
            f"画板：{sizes}（每格像素固定，窗口再小也只会出滚动条，不会裁掉）。"
            "左键画、右键擦、Alt 取色。"
            "配色与画板会存进角色卡；应用到桌面人偶与新建角色在 v0.2.0。"
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#888; font-size:11px;")
        centre.addWidget(self.hint)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(QLabel("特写预览"))
        self.preview = PuppetPreview()
        right.addWidget(self.preview)
        right.addStretch(1)

        outer.addLayout(left)
        outer.addLayout(centre, 1)
        outer.addLayout(right)

        self.undo_button.clicked.connect(lambda: self.current_canvas().undo())
        self.clear_button.clicked.connect(lambda: self.current_canvas().clear())
        self.import_button.clicked.connect(self.import_png)
        self.export_button.clicked.connect(self.export_png)
        self.default_button.clicked.connect(self.reset_appearance)
        self.base_box.currentIndexChanged.connect(self._on_base)
        self._refresh_recent()

    # -- 画板选择 -----------------------------------------------------------
    @property
    def canvas(self) -> PixelCanvas:
        """当前画板（兼容旧名字：第一版只有头顶一个画板）。"""
        return self.current_canvas()

    def current_canvas(self) -> PixelCanvas:
        return self.canvases.get(self.board_box.currentData() or "headpiece",
                                 self.canvases["headpiece"])

    def _on_board(self, *_args) -> None:
        self.stack.setCurrentIndex(self.board_box.currentIndex())

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
                "pod_colour": draft.get("pod_colour") or template.pod_colour,
                "pod_rim_colour": draft.get("pod_rim_colour") or template.pod_rim_colour,
                "antenna_colour": draft.get("antenna_colour") or template.antenna_colour,
            }
            for field, value in colours.items():
                self.buttons[field].set_colour(_hex(value))
            mode = str(draft.get("headpiece_mode") or "none")
            if mode not in self.mode_buttons:
                mode = "none"
            self.mode_buttons[mode].setChecked(True)

            self.canvases["headpiece"].set_art(
                fit_art("headpiece", PixelArt.from_dict(draft.get("headpiece_pixels"))))
            self.canvases["antenna"].set_art(
                fit_art("antenna", PixelArt.from_dict(draft.get("antenna_pixels"))))
            self.antenna_enabled.setChecked(bool(draft.get("antenna_pixels")))
            scale = draft.get("headpiece_scale")
            self._headpiece_scale = float(scale) if isinstance(scale, (int, float)) else 0.5
            antenna_scale = draft.get("antenna_scale")
            self._antenna_scale = (
                float(antenna_scale) if isinstance(antenna_scale, (int, float)) else 0.5
            )
            self._refresh_preview()
        finally:
            self._loading = False
        self._dirty = False

    @property
    def headpiece_scale(self) -> float:
        return self._headpiece_scale

    @property
    def antenna_scale(self) -> float:
        return self._antenna_scale

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
        for field, _caption, _tip in ANTENNA_FIELDS:
            draft[field] = list(_rgb(self.buttons[field].colour()))
        draft["headpiece_mode"] = self.headpiece_mode()
        for key, field, scale in (("headpiece", "headpiece_pixels", self._headpiece_scale),
                                  ("antenna", "antenna_pixels", self._antenna_scale)):
            art = self.canvases[key].art
            if art.is_empty():
                continue
            if key == "antenna" and not self.antenna_enabled.isChecked():
                continue
            if key == "headpiece" and self.headpiece_mode() != "pixel":
                continue
            try:
                draft[field] = art.to_dict()
            except ValueError:
                pass                      # 颜色太多：save() 里会明确报错，不静默丢
            if scale is not None:
                draft["headpiece_scale" if key == "headpiece" else "antenna_scale"] = scale
        return draft

    def _pristine_draft(self) -> dict:
        """"什么都没改"时该长什么样：色值等于基底、三个画板都不参与。"""
        base = self.base_template()
        template = ap.CHARACTERS.get(base) or ap.PEBBLES
        return {
            "base_template": base,
            "skin": list(template.skin),
            "eye": list(template.eye),
            "gown_top": list(template.gown(0.0)),
            "gown_bottom": list(template.gown(1.0)),
            "halo": list(template.halo),
            "halo_spark": list(template.halo_spark),
            "pod_colour": list(template.pod_colour),
            "pod_rim_colour": list(template.pod_rim_colour),
            "antenna_colour": list(template.antenna_colour),
            "headpiece_mode": "none",
        }

    def save(self) -> str:
        draft = self.collect()
        for key, field in (("headpiece", "headpiece_pixels"),
                           ("antenna", "antenna_pixels")):
            if field not in draft:
                continue
            try:
                self.canvases[key].art.to_dict()
            except ValueError as exc:
                return f"{key} 画板没存下来：{exc}"
        # 一个色都没改、画板也没用 → **删掉覆盖层**，别在卡里留一份"和源码一模一样"的
        # 外观块。内置两位的默认值必须永远来自 `appearance.py`（那里有源码行号）。
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
            "把这个角色的配色与画板草稿删掉，回到源码默认值？\n（角色卡的文字内容不会动）",
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

    def _on_picked(self, colour: str) -> None:
        self.brush_colour.blockSignals(True)
        self.brush_colour.set_colour(colour)
        self.brush_colour.blockSignals(False)
        for canvas in self.canvases.values():
            canvas.colour = colour

    def _on_brush_colour(self, colour: str) -> None:
        for canvas in self.canvases.values():
            canvas.colour = colour
        self._remember_colour(colour)

    def _remember_colour(self, colour: str) -> None:
        colour = _hex(colour)
        if colour in self._recent:
            self._recent.remove(colour)
        self._recent.insert(0, colour)
        del self._recent[8:]
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        while self.recent_row.count():
            item = self.recent_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        seen: list[str] = list(self._recent)
        for canvas in self.canvases.values():
            for colour in canvas.art.used_colours():
                if colour not in seen:
                    seen.append(colour)
        seen = seen[:8]
        for colour in seen:
            swatch = QPushButton()
            swatch.setFixedSize(20, 20)
            swatch.setToolTip(colour)
            swatch.setStyleSheet(
                f"QPushButton{{background:{colour}; border:1px solid rgba(0,0,0,0.5);"
                f" border-radius:3px;}}"
            )
            swatch.clicked.connect(lambda _c=False, value=colour: self._use_colour(value))
            self.recent_row.addWidget(swatch)
        if not seen:
            hint = QLabel("最近用过的颜色会出现在这里")
            hint.setStyleSheet("color:#777; font-size:11px;")
            self.recent_row.addWidget(hint)

    def _use_colour(self, colour: str) -> None:
        self.brush_colour.set_colour(colour)
        self._on_brush_colour(colour)

    def _on_base(self, *_args) -> None:
        if self._loading:
            return
        self._mark_dirty()
        self._load_colours_from_template()
        self._refresh_preview(rebuild=True)

    def _load_colours_from_template(self) -> None:
        template = ap.CHARACTERS.get(self.base_template()) or ap.PEBBLES
        for field, value in (("skin", template.skin), ("eye", template.eye),
                             ("gown_top", template.gown(0.0)),
                             ("gown_bottom", template.gown(1.0)),
                             ("halo", template.halo),
                             ("halo_spark", template.halo_spark),
                             ("pod_colour", template.pod_colour),
                             ("pod_rim_colour", template.pod_rim_colour),
                             ("antenna_colour", template.antenna_colour)):
            self.buttons[field].set_colour(_hex(value))

    def _on_mode(self, *_args) -> None:
        if self._loading:
            return
        self._mark_dirty()
        self._refresh_preview()

    def _on_brush(self, *_args) -> None:
        self.current_canvas().brush = int(self.brush_box.currentData() or 1)

    def _on_pick_mode(self, checked: bool) -> None:
        for canvas in self.canvases.values():
            canvas.pick_mode = bool(checked)

    def _on_symmetric(self, checked: bool) -> None:
        for canvas in self.canvases.values():
            canvas.symmetric = bool(checked)

    def _on_draw(self) -> None:
        self._remember_colour(self.brush_colour.colour())
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
        self._refresh_preview()
        self.changed.emit()

    def _refresh_preview(self, rebuild: bool = False) -> None:
        base = self.base_template()
        if rebuild or getattr(self, "_built_key", None) != base:
            self._built_key = base
            self.preview._rebuild(ap.build_appearance(base, base, None))
        self.preview.set_appearance(ap.build_appearance(self.key, base, self.collect()))

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
        canvas = self.current_canvas()
        target = canvas.art
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
        canvas.set_art(art)
        self._on_draw()

    def export_png(self) -> None:
        canvas = self.current_canvas()
        art = canvas.art
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出像素画", f"{self.key}-{self.board_box.currentData()}.png",
            "PNG 图片 (*.png)"
        )
        if not path:
            return
        image = QImage(art.w, art.h, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        for y in range(art.h):
            for x in range(art.w):
                colour = art.pixels[y][x]
                if colour is not None:
                    image.setPixelColor(x, y, QColor(colour))
        if not image.save(path, "PNG"):
            QMessageBox.warning(self, "导出失败", "写不进这个位置。")
