"""世界书编辑页（工作室的第一个真读写页）。

界面：

    ┌ 扫描深度 [4]  Token 预算 [1200]  [ ] 允许 1 层递归  [ ] 整词匹配  ┐
    │ 搜索 [        ]                                                   │
    │ ┌ 条目列表 ────────┐ ┌ 选中条目 ─────────────────────────────┐   │
    │ │ [常驻] 基础设定   │ │ 备注 [     ]   id [     ]  [ ] 常驻   │   │
    │ │ [关键词] 五块卵石 │ │ 关键词（一行一个）                     │   │
    │ │ ...               │ │ ------------------------------------- │   │
    │ └───────────────────┘ │ 次级逻辑 [AND_ANY ▾]  次级关键词        │   │
    │ [+新增][复制][删除]   │ 位置 [after_char_defs ▾] 深度 [0]      │   │
    │                       │ order [100]  概率 [100] %              │   │
    │                       │ 正文（约 123 token）                    │   │
    │                       └────────────────────────────────────────┘   │
    └────────────────────────────────────────────────────────────────────┘

三条实现上的取舍：

* **草稿模型**：左列表和右表单各存一半，切换选中前先 `_flush()` 把右表单刷回草稿
  （照 `dialogue_editor.py` 验证过的写法）。点「保存」才落盘。
* **`keys` 一行一个**：源码侧是子串匹配，中文里逗号有全角半角两种，用逗号分隔迟早
  被玩家写出一个"含逗号的关键词"。一行一个就没有这个歧义。
* **正则 key 的护栏复用 `worldbook.looks_catastrophic`**：玩家写 `/（a+)+/` 会当场红字警告，
  而不是等他保存完、桌宠每次说话都卡住。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..llm import prompt as llm_prompt
from ..llm import worldbook as wb


def _lines_to_keys(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _keys_to_text(keys) -> str:
    return "\n".join(keys or ())


class WorldbookTab(QWidget):
    """读写 ``worldbook.json``（v0.1.9 是**全局一份**；按角色分文件在 v0.2.0）。"""

    changed = Signal()

    def __init__(self, book: wb.Worldbook, parent=None):
        super().__init__(parent)
        self.book = book
        self._entries: list[wb.Entry] = []
        self._current: int = -1
        self._dirty = False
        self._build()
        self.reload()

    # -- 构建 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)

        top = QHBoxLayout()
        self.scan_depth = QSpinBox()
        self.scan_depth.setRange(0, wb.SCAN_DEPTH_LIMIT)
        self.scan_depth.setSuffix(" 条")
        self.scan_depth.setToolTip("每次请求往前翻多少条历史来匹配关键词（4 = 最近 4 条 + 当前这句）")
        self.token_budget = QSpinBox()
        self.token_budget.setRange(0, wb.BUDGET_LIMIT)
        self.token_budget.setSuffix(" token")
        self.token_budget.setToolTip("世界书正文最多占多少预算；超了就按 order 从小到大丢")
        self.recursive = QCheckBox("允许 1 层递归")
        self.recursive.setToolTip("命中条目的正文里再出现别的关键词时，再扫一层（源码硬限 1 层）")
        self.whole_words = QCheckBox("整词匹配")
        self.whole_words.setToolTip("中文请保持关闭：开着它「卵石」匹配不上「五块卵石」")
        for widget in (QLabel("扫描深度"), self.scan_depth,
                       QLabel("预算"), self.token_budget,
                       self.recursive, self.whole_words):
            top.addWidget(widget)
        top.addStretch(1)
        outer.addLayout(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索备注 / 关键词 / 正文…")
        self.search.textChanged.connect(self._filter)
        outer.addWidget(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(6)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.currentRowChanged.connect(self._on_select)
        left_box.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("新增")
        self.dup_button = QPushButton("复制")
        self.del_button = QPushButton("删除")
        for button in (self.add_button, self.dup_button, self.del_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        left_box.addLayout(buttons)
        self.summary = QLabel("")
        self.summary.setStyleSheet("color:#777; font-size:11px;")
        left_box.addWidget(self.summary)
        splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.comment = QLineEdit()
        self.entry_id = QLineEdit()
        self.entry_id.setPlaceholderText("留空也行（保存时按顺序对齐）")
        self.constant = QCheckBox("常驻（不看关键词，永远带上）")
        head = QHBoxLayout()
        head.addWidget(self.comment, 3)
        head.addWidget(QLabel("id"))
        head.addWidget(self.entry_id, 2)
        form.addRow("备注", head)
        form.addRow("", self.constant)

        self.keys = QPlainTextEdit()
        self.keys.setPlaceholderText("一行一个关键词。子串匹配；/正则/i 也支持")
        self.keys.setFixedHeight(66)
        form.addRow("关键词", self.keys)

        secondary = QHBoxLayout()
        self.logic = QComboBox()
        for name in wb.LOGICS:
            self.logic.addItem(name, name)
        secondary.addWidget(QLabel("次级逻辑"))
        secondary.addWidget(self.logic)
        secondary.addStretch(1)
        form.addRow("", secondary)

        self.secondary_keys = QPlainTextEdit()
        self.secondary_keys.setPlaceholderText("一行一个；留空表示不看次级关键词")
        self.secondary_keys.setFixedHeight(56)
        form.addRow("次级关键词", self.secondary_keys)

        numbers = QHBoxLayout()
        self.position = QComboBox()
        for name in wb.POSITIONS:
            self.position.addItem(name, name)
        self.depth = QSpinBox()
        self.depth.setRange(0, wb.DEPTH_LIMIT)
        self.order = QSpinBox()
        self.order.setRange(-wb.ORDER_LIMIT, wb.ORDER_LIMIT)
        self.order.setToolTip("越小越靠前；预算不够时从 order 最小（最靠前）的开始丢")
        self.probability = QSpinBox()
        self.probability.setRange(0, 100)
        self.probability.setSuffix(" %")
        self.probability.setToolTip("每次请求掷一次骰子；100 = 必定插入")
        for caption, widget in (("位置", self.position), ("深度", self.depth),
                                ("order", self.order), ("概率", self.probability)):
            numbers.addWidget(QLabel(caption))
            numbers.addWidget(widget)
        numbers.addStretch(1)
        form.addRow("", numbers)

        self.content = QPlainTextEdit()
        self.content.setPlaceholderText("这条设定要写进 system 提示词的正文")
        form.addRow("正文", self.content)

        self.token_label = QLabel("")
        self.token_label.setStyleSheet("color:#777; font-size:11px;")
        self.content.textChanged.connect(self._update_tokens)
        form.addRow("", self.token_label)

        splitter.addWidget(right)
        splitter.setSizes([240, 560])
        outer.addWidget(splitter, 1)

        self.add_button.clicked.connect(self.add_entry)
        self.dup_button.clicked.connect(self.duplicate_entry)
        self.del_button.clicked.connect(self.delete_entry)

        for widget in (self.scan_depth, self.token_budget, self.order, self.depth,
                       self.probability):
            widget.valueChanged.connect(self._mark_dirty)
        for widget in (self.recursive, self.whole_words, self.constant):
            widget.toggled.connect(self._mark_dirty)
        for widget, signal in ((self.logic, self.logic.currentIndexChanged),
                               (self.position, self.position.currentIndexChanged),
                               (self.comment, self.comment.textChanged),
                               (self.entry_id, self.entry_id.textChanged)):
            signal.connect(self._mark_dirty)
        for widget in (self.keys, self.secondary_keys, self.content):
            widget.textChanged.connect(self._mark_dirty)

    # -- 载入 / 保存 ---------------------------------------------------------
    def reload(self) -> None:
        """从磁盘重读（切换角色时不动它：v0.1.9 的世界书是全局一份）。"""
        self.book.load()
        self._entries = list(self.book.entries)
        self.scan_depth.blockSignals(True)
        self.token_budget.blockSignals(True)
        self.recursive.blockSignals(True)
        self.whole_words.blockSignals(True)
        self.scan_depth.setValue(self.book.scan_depth)
        self.token_budget.setValue(self.book.token_budget)
        self.recursive.setChecked(bool(self.book.recursive))
        self.whole_words.setChecked(bool(self.book.whole_words))
        self.scan_depth.blockSignals(False)
        self.token_budget.blockSignals(False)
        self.recursive.blockSignals(False)
        self.whole_words.blockSignals(False)
        self._current = -1
        self._dirty = False
        self._refresh_list()
        if self._entries:
            self.list.setCurrentRow(0)

    def is_dirty(self) -> bool:
        return self._dirty

    def save(self, force: bool = False) -> str:
        """落盘。返回 ``""`` = 成功。"""
        self._flush()
        self.book.scan_depth = self.scan_depth.value()
        self.book.token_budget = self.token_budget.value()
        self.book.recursive = 1 if self.recursive.isChecked() else 0
        self.book.whole_words = self.whole_words.isChecked()
        self.book.entries = list(self._entries)
        if not self.book.save(force=force):
            return self.book.last_error or "保存失败（原因未知）"
        self._dirty = False
        return ""

    def last_error(self) -> str:
        return self.book.last_error

    # -- 列表 ---------------------------------------------------------------
    def _mark_dirty(self, *_args) -> None:
        if not self._dirty:
            self._dirty = True
        self.changed.emit()

    def _label(self, entry: wb.Entry) -> str:
        kind = "常驻" if entry.constant else "关键词"
        name = entry.comment.strip() or (entry.keys[0] if entry.keys else "（未命名）")
        return f"[{kind}] {name}  ·  order {entry.order}"

    def _refresh_list(self) -> None:
        self.list.blockSignals(True)
        row = self._current
        self.list.clear()
        for entry in self._entries:
            self.list.addItem(QListWidgetItem(self._label(entry)))
        self.list.blockSignals(False)
        self.summary.setText(
            f"共 {len(self._entries)} 条，其中可用 "
            f"{sum(1 for e in self._entries if e.usable)} 条（占位/空正文算不可用）"
        )
        if 0 <= row < self.list.count():
            self.list.blockSignals(True)
            self.list.setCurrentRow(row)
            self.list.blockSignals(False)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            entry = self._entries[row] if row < len(self._entries) else None
            haystack = ""
            if entry is not None:
                haystack = " ".join(
                    (entry.comment, entry.id, " ".join(entry.keys),
                     " ".join(entry.secondary_keys), entry.content)
                ).lower()
            item.setHidden(bool(needle) and needle not in haystack)

    # -- 选中 → 表单 ---------------------------------------------------------
    def _on_select(self, row: int) -> None:
        self._flush()
        if not (0 <= row < len(self._entries)):
            self._current = -1
            return
        self._current = row
        entry = self._entries[row]
        for widget in (self.comment, self.entry_id, self.keys, self.secondary_keys,
                       self.content):
            widget.blockSignals(True)
        self.constant.blockSignals(True)
        self.logic.blockSignals(True)
        self.position.blockSignals(True)
        for spin in (self.depth, self.order, self.probability):
            spin.blockSignals(True)

        self.comment.setText(entry.comment)
        self.entry_id.setText(entry.id)
        self.constant.setChecked(bool(entry.constant))
        self.keys.setPlainText(_keys_to_text(entry.keys))
        self.secondary_keys.setPlainText(_keys_to_text(entry.secondary_keys))
        self.logic.setCurrentIndex(max(0, self.logic.findData(entry.logic)))
        self.position.setCurrentIndex(max(0, self.position.findData(entry.position)))
        self.depth.setValue(entry.depth)
        self.order.setValue(entry.order)
        self.probability.setValue(entry.probability)
        self.content.setPlainText(entry.content)

        for widget in (self.comment, self.entry_id, self.keys, self.secondary_keys,
                       self.content):
            widget.blockSignals(False)
        self.constant.blockSignals(False)
        self.logic.blockSignals(False)
        self.position.blockSignals(False)
        for spin in (self.depth, self.order, self.probability):
            spin.blockSignals(False)
        self._update_tokens()

    def _update_tokens(self) -> None:
        text = self.content.toPlainText()
        tokens = llm_prompt.estimate_tokens(text)
        note = ""
        if not text.strip():
            note = "　（正文为空 → 这条永远不会生效）"
        elif text.strip().startswith(wb.PLACEHOLDER):
            note = "　（占位条目不会插进提示词）"
        elif any(key.startswith("/") for key in _lines_to_keys(self.keys.toPlainText())):
            note = "　（含正则关键词）"
        self.token_label.setText(f"约 {tokens} token{note}")
        for key in _lines_to_keys(self.keys.toPlainText()):
            if key.startswith("/") and wb.looks_catastrophic(key):
                self.token_label.setText(
                    f"约 {tokens} token　这串正则可能很慢（嵌套量词），建议改写：{key}"
                )
                break

    def _flush(self) -> None:
        if not (0 <= self._current < len(self._entries)):
            return
        entry = self._entries[self._current]
        entry.comment = self.comment.text()
        entry.id = self.entry_id.text().strip()
        entry.constant = self.constant.isChecked()
        entry.keys = _lines_to_keys(self.keys.toPlainText())
        entry.secondary_keys = _lines_to_keys(self.secondary_keys.toPlainText())
        entry.logic = self.logic.currentData() or "AND_ANY"
        entry.position = self.position.currentData() or "after_char_defs"
        entry.depth = self.depth.value()
        entry.order = self.order.value()
        entry.probability = self.probability.value()
        entry.content = self.content.toPlainText()
        item = self.list.item(self._current)
        if item is not None:
            item.setText(self._label(entry))

    # -- 增删 ---------------------------------------------------------------
    def add_entry(self) -> None:
        self._flush()
        entry = wb.Entry(id="", comment="新条目", order=100, content="")
        self._entries.append(entry)
        self._current = len(self._entries) - 1
        self._refresh_list()
        self.list.setCurrentRow(self._current)
        self._mark_dirty()

    def duplicate_entry(self) -> None:
        self._flush()
        if not (0 <= self._current < len(self._entries)):
            return
        source = self._entries[self._current]
        clone = wb.Entry(
            id="", comment=(source.comment + " 副本").strip(), keys=source.keys,
            secondary_keys=source.secondary_keys, logic=source.logic,
            constant=source.constant, position=source.position, depth=source.depth,
            order=source.order, probability=source.probability, content=source.content,
        )
        self._entries.insert(self._current + 1, clone)
        self._current += 1
        self._refresh_list()
        self.list.setCurrentRow(self._current)
        self._mark_dirty()

    def delete_entry(self) -> None:
        self._flush()
        if not (0 <= self._current < len(self._entries)):
            return
        self._entries.pop(self._current)
        self._current = min(self._current, len(self._entries) - 1)
        self._refresh_list()
        if self._entries:
            self.list.setCurrentRow(max(0, self._current))
        else:
            self.content.setPlainText("")
        self._mark_dirty()
