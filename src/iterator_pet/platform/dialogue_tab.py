"""「本地对话」页：把原来的独立「自定义对话」窗口收进工坊（v0.2.1）。

为什么从独立窗口改成工坊里的一个页签：菜单里同时有「自定义对话」和「迭代器自定义」，
玩家要先记住"哪类东西在哪个窗口"。台词也是角色的一部分，收进同一个窗口的第二个页签
之后，"改她"只需要打开一个地方。

顺序是**人偶外观 → 本地对话 → 角色卡 → 世界书**：从"看起来什么样"到"说什么"到
"她是谁"到"她知道什么"，是一个从外往里的顺序。

顶部那颗勾选框「不接入LLM时，启用本地对话」原来在托盘/右键菜单里叫「对话」——
那个名字有歧义（是"跟谁对话"？"开不开启对话"？），而且和「大语言模型」挨在一起时
更像是在开关模型。现在它只有一个位置、一个说得清的名字。

数据流（和世界书页一致）：打开时把"全部角色 × 三个事件"读进草稿；切分组时先把列表刷回
草稿；点工坊的「保存」才一次性交给 ``Lines.save_pools()``（只动这几组，保留文件里其它键）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import characters
from ..simulation import constants as C
from ..simulation.lines import Lines

#: 六个事件；``ritual`` 是仪式（杀鼠标成功 / 挣脱完成）那一 tick 说的话。
#: 2026-11-01 第一组加了三个"外部要求说一句"的：启动 / 戳一下 / 空闲自语
#: （详见 ``simulation/events.py`` 的 ``PENDING_EVENTS``）。
EVENTS = (
    ("drag", "拖拽"),
    ("talk", "长时间静止"),
    ("ritual", "仪式"),
    ("startup", "启动时"),
    ("poke", "戳一下"),
    ("idle_talk", "空闲自语"),
)
SILENT = C.DIALOGUE_SILENT_ENTRY


class DialogueTab(QWidget):
    """本地台词池的编辑器（工坊第二个页签）。"""

    changed = Signal()

    def __init__(self, lines: Lines, parent=None, on_enabled=None, enabled: bool = True):
        super().__init__(parent)
        self.lines = lines
        self.on_enabled = on_enabled
        self._draft: dict[tuple[str, str], list[str]] = {}
        self._current: tuple[str, str] | None = None
        self._keys: list[str] = []
        self._dirty = False

        self.enabled_box = QCheckBox("不接入LLM时，启用本地对话")
        self.enabled_box.setChecked(bool(enabled))
        self.enabled_box.setToolTip(
            "勾上：没有大模型（或没启用）时，她照样会用这里的台词说话。"
        )
        self.enabled_box.toggled.connect(self._on_enabled)

        self.character_box = QComboBox()
        self.event_box = QComboBox()
        for key, label in EVENTS:
            self.event_box.addItem(label, key)

        top = QHBoxLayout()
        top.addWidget(self.character_box, 1)
        top.addSpacing(8)
        top.addWidget(QLabel("事件"))
        top.addWidget(self.event_box, 1)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemChanged.connect(self._on_item_changed)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("添加")
        self.silent_button = QPushButton(f"添加「{SILENT[1:-1]}」")
        self.remove_button = QPushButton("删除")
        for button in (self.add_button, self.silent_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        self.hint = QLabel(
            f"双击编辑。「{SILENT[1:-1]}」这一条会被跳过，多填几条可以降低说话频率。"
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#888; font-size:11px;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)
        outer.addWidget(self.enabled_box)
        outer.addLayout(top)
        outer.addWidget(self.list, 1)
        outer.addLayout(buttons)
        outer.addWidget(self.hint)

        self.add_button.clicked.connect(self.add_line)
        self.silent_button.clicked.connect(self.add_silent)
        self.remove_button.clicked.connect(self.remove_line)
        self.character_box.currentIndexChanged.connect(self._reload_list)
        self.event_box.currentIndexChanged.connect(self._reload_list)

        self.refresh()

    # -- 角色清单 -----------------------------------------------------------
    def refresh(self, keep: str | None = None) -> None:
        """重扫注册表（新建/删除角色之后调）。**有未保存改动时不重载草稿**。"""
        current = keep or self.character_box.currentData() or "pebbles"
        rows = characters.names(self.lines.path.parent)
        self._keys = [key for key, _label in rows]
        self.character_box.blockSignals(True)
        self.character_box.clear()
        for key, label in rows:
            self.character_box.addItem(label, key)
        index = self.character_box.findData(current)
        self.character_box.setCurrentIndex(max(0, index))
        self.character_box.blockSignals(False)
        if not self._dirty:
            self._load_draft()
        self._reload_list()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled_box.blockSignals(True)
        self.enabled_box.setChecked(bool(enabled))
        self.enabled_box.blockSignals(False)

    # -- 数据 ---------------------------------------------------------------
    def _load_draft(self) -> None:
        self._draft = {}
        for character in self._keys:
            for event, _ in EVENTS:
                pool = self.lines.pool(character, event)
                self._draft[(character, event)] = list(pool) if pool else []

    def _flush(self) -> None:
        if self._current is None:
            return
        self._draft[self._current] = [
            self.list.item(i).text() for i in range(self.list.count())
        ]

    def _reload_list(self, *_args) -> None:
        self._flush()
        key = (self.character_box.currentData(), self.event_box.currentData())
        self._current = key
        self.list.blockSignals(True)
        self.list.clear()
        for text in self._draft.get(key, []):
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.list.addItem(item)
        self.list.blockSignals(False)

    def _on_item_changed(self, *_args) -> None:
        self._flush()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
        self.changed.emit()

    # -- 勾选框 -------------------------------------------------------------
    def _on_enabled(self, checked: bool) -> None:
        """立刻生效（和台词不同：它是个开关，不跟着「保存」走）。"""
        if callable(self.on_enabled):
            self.on_enabled(bool(checked))

    # -- 按钮 ---------------------------------------------------------------
    def add_line(self) -> None:
        self.list.blockSignals(True)
        item = QListWidgetItem("")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)
        self.list.blockSignals(False)
        self.list.setCurrentItem(item)
        self.list.editItem(item)
        self._flush()
        self._mark_dirty()

    def add_silent(self) -> None:
        self.list.blockSignals(True)
        item = QListWidgetItem(SILENT)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)
        self.list.blockSignals(False)
        self.list.setCurrentItem(item)
        self._flush()
        self._mark_dirty()

    def remove_line(self) -> None:
        selected = self.list.selectedItems()
        if not selected:
            return
        self.list.blockSignals(True)
        for item in selected:
            self.list.takeItem(self.list.row(item))
        self.list.blockSignals(False)
        self._flush()
        self._mark_dirty()

    # -- 保存 ---------------------------------------------------------------
    def is_dirty(self) -> bool:
        return self._dirty

    def save(self) -> str:
        """写回 ``lines.json``。返回 ``""`` = 成功，否则是给玩家看的错误串。"""
        self._flush()
        if not self.lines.save_pools(dict(self._draft)):
            return self.lines.last_error or "保存失败（原因未知）"
        self._dirty = False
        self._load_draft()
        return ""
