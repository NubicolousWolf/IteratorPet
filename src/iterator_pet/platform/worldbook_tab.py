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

import shutil
from pathlib import Path

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
    QMessageBox,
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
    """读写世界书。

    v0.1.9 只有**全局一份** ``worldbook.json``；v0.2.0 起每个角色可以有自己的
    ``worldbooks/<key>.json``，作用域下拉框就在最上面那行。**判定只有一条**：
    独立那份文件存在 → 用它；不存在 → 无缝回落全局（和 ``llm/worldbook.resolve_path``
    同一口径，所以"界面上看到的作用域"永远等于"下一句话实际用的那份"）。
    """

    changed = Signal()

    def __init__(self, book: wb.Worldbook, folder: Path | str | None = None,
                 character: str = "pebbles", parent=None):
        super().__init__(parent)
        self.book = book
        self.folder = Path(folder) if folder is not None else None
        self.character = character
        self._entries: list[wb.Entry] = []
        self._current: int = -1
        self._dirty = False
        self._build()
        self._refresh_scope()
        self.reload()

    # -- 构建 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)

        # 作用域放在最上面：它决定下面这一屏改的是哪个文件，藏起来就会出现
        # "我明明改了，她怎么还用旧的"。
        scope = QHBoxLayout()
        scope.addWidget(QLabel("作用域"))
        self.scope_box = QComboBox()
        self.scope_box.setMinimumWidth(320)
        self.scope_box.currentIndexChanged.connect(self._on_scope)
        scope.addWidget(self.scope_box, 1)
        self.fork_button = QPushButton("复制全局到该角色")
        self.fork_button.setToolTip(
            "把 worldbook.json 原样复制成 worldbooks/<角色>.json，作为独立世界书的起点"
        )
        self.fork_button.clicked.connect(self.fork_from_global)
        scope.addWidget(self.fork_button)
        # 2026-11-01：对不上任何角色 key 的独立世界书（角色改名/手改文件名之后
        # 就这样静默失效）。只在对**当前角色**还没有独立世界书时提示，一键迁过来。
        self.orphan_button = QPushButton("")
        self.orphan_button.setToolTip(
            "worldbooks/ 里有一份对不上当前角色 key 的世界书（角色改过名？）。"
            "点一下把它复制成当前角色的独立世界书。"
        )
        self.orphan_button.clicked.connect(self.migrate_orphan)
        self.orphan_button.hide()
        scope.addWidget(self.orphan_button)
        self._orphan: Path | None = None
        outer.addLayout(scope)

        top = QHBoxLayout()
        self.scan_depth = QSpinBox()
        self.scan_depth.setRange(0, wb.SCAN_DEPTH_LIMIT)
        self.scan_depth.setSuffix(" 条")
        self.scan_depth.setToolTip("每次请求往前翻多少条历史来匹配关键词（4 = 最近 4 条 + 当前这句）")
        self.token_budget = QSpinBox()
        self.token_budget.setRange(0, wb.BUDGET_LIMIT)
        self.token_budget.setSuffix(" token")
        self.token_budget.setToolTip("世界书正文最多占多少预算；超过预算则按 order 从小到大丢弃")
        self.recursive = QCheckBox("允许 1 层递归")
        self.recursive.setToolTip("命中条目的正文里再出现别的关键词时，扫描一层")
        self.whole_words = QCheckBox("整词匹配")
        self.whole_words.setToolTip("中文请保持关闭")
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
        self.entry_id.setPlaceholderText("留空时按顺序对齐")
        self.constant = QCheckBox("常驻")
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
        self.order.setToolTip("越小越靠前；预算不够时从 order 最小的开始丢弃")
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

    # -- 作用域（全局 / 该角色）------------------------------------------------
    def _own_path(self) -> Path:
        return wb.character_path(self.character, self.folder)

    def _refresh_scope(self) -> None:
        """把下拉框刷成"现在这一屏改的是哪个文件"。

        选中项按 ``self.book.path`` 判断（不是按文件在不在）：玩家可能刚选到
        "该角色独立"、还没点保存，文件确实不存在，但这一屏的内容就是冲着它去的。
        文件在不在由标签里的「已有 / 还没有」明说——那句话同时也是
        ``resolve_path`` 的判据，所以"标签"和"下一句话实际用哪份"永远一致。
        """
        own = self._own_path()
        exists = own.is_file()
        self.scope_box.blockSignals(True)
        self.scope_box.clear()
        self.scope_box.addItem("共用全局世界书（worldbook.json）", "global")
        mark = "已有" if exists else "还没有（保存后才有）"
        self.scope_box.addItem(
            f"「{self.character}」独立世界书（worldbooks/{own.name} · {mark}）", "own"
        )
        on_own = Path(self.book.path) == own
        self.scope_box.setCurrentIndex(1 if on_own else 0)
        self.scope_box.blockSignals(False)
        self.fork_button.setEnabled(not exists)
        self._refresh_orphan(exists)

    def _refresh_orphan(self, own_exists: bool) -> None:
        """没有独立那份时，看看有没有"对不上角色"的书可以迁过来。"""
        self._orphan = None
        if own_exists:
            self.orphan_button.hide()
            return
        try:
            from .. import characters as registry

            known = [key for key, _label in registry.names()]
        except Exception:                 # noqa: BLE001 —— 注册表坏了不该挡住世界书页
            known = [self.character]
        found = wb.orphans(known, self.folder)
        if not found:
            self.orphan_button.hide()
            return
        self._orphan = found[0]
        self.orphan_button.setText(f"把 {found[0].name} 迁到「{self.character}」")
        self.orphan_button.show()

    def migrate_orphan(self) -> None:
        """把 ``worldbooks/<别的key>.json`` 复制成当前角色的独立世界书。"""
        source = self._orphan
        target = self._own_path()
        if source is None or not Path(source).is_file():
            return
        if target.is_file():
            QMessageBox.information(
                self, "已经有独立世界书",
                f"worldbooks/{target.name} 已经存在了，直接编辑它就行。",
            )
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 复制文件而不是 load→save：玩家的缩进、未知键一点都不该变
            shutil.copyfile(source, target)
        except OSError as exc:
            QMessageBox.warning(self, "迁移失败", f"{type(exc).__name__}: {exc}")
            return
        self._switch_book(target)

    def set_character(self, key: str) -> None:
        """换角色：重算作用域，并切到"她实际在用的那份"。

        调用方（工作室）负责在切角色前问过"还有没保存的改动"；这里不再问一次。
        """
        if not key or key == self.character:
            return
        self.character = key
        self._switch_book(wb.resolve_path(key, self.folder))

    def _on_scope(self, _index: int) -> None:
        if not self._maybe_lose_changes():
            self._refresh_scope()          # 玩家取消 → 选择退回"文件真实状态"
            return
        if (self.scope_box.currentData() or "global") == "global":
            self._switch_book(wb.global_path(self.folder))
            return
        own = self._own_path()
        if not own.is_file():
            answer = QMessageBox.question(
                self, "还没有独立世界书",
                f"「{self.character}」还没有自己的世界书。\n"
                "要不要把现在的全局世界书复制一份作为起点？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.fork_from_global()
                return
        self._switch_book(own)

    def fork_from_global(self) -> None:
        """把全局世界书**原样复制**成这个角色的独立一份。

        复制文件而不是 load→save：玩家手改的缩进、未知顶层键、条目顺序一点都不该变
        （那正是"我们只搬位置、不改内容"的意思）。
        """
        source = wb.global_path(self.folder)
        target = self._own_path()
        if target.is_file():
            QMessageBox.information(
                self, "已经有独立世界书",
                f"worldbooks/{target.name} 已经存在了，直接编辑它就行（作用域切过去即可）。",
            )
            return
        if not source.is_file():
            QMessageBox.information(
                self, "全局世界书还不存在",
                "根目录还没有 worldbook.json，所以没东西可复制。\n"
                "先切到「共用全局世界书」建几条，或者就在这里从空白开始写。",
            )
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        except OSError as exc:
            QMessageBox.warning(self, "复制失败", f"{type(exc).__name__}: {exc}")
            return
        self._switch_book(target)

    def _switch_book(self, path: Path) -> None:
        path = Path(path)
        if self.book.path != path:
            self.book = wb.Worldbook(path)
            self.reload()                 # reload() 内部 book.load()
        self._refresh_scope()

    def _maybe_lose_changes(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self, "有未保存的世界书改动",
            "世界书还有没保存的改动，切换作用域会丢掉。要继续吗？",
        )
        return answer == QMessageBox.StandardButton.Yes

    # -- 载入 / 保存 ---------------------------------------------------------
    def reload(self) -> None:
        """从磁盘重读当前作用域那份文件。"""
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
        self._refresh_scope()          # 第一次保存独立世界书后，标签从「还没有」变「已有」
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
