"""自定义对话编辑器（GUI）：玩家不用碰 JSON 也能增删台词。

为什么要它：`os.startfile("lines.json")` 在**没有 .json 关联**的机器上会直接抛
`WinError 1155`，被 `except OSError: pass` 吞掉 —— 用户看到的就是"点了没反应"。
干脆自己在程序里编辑，完全不依赖系统文件关联。

界面：

    ┌ 角色 [五块卵石 ▾]   事件 [拖拽 ▾] ───────────────┐
    │ 台词列表（双击就地改）                            │
    │   滚开。                                          │
    │   别烦我。                                        │
    │   （不触发对话）                                  │
    │ [添加] [添加「不触发」] [删除]                    │
    │ 提示：空行保存时会被丢掉 │
    │ 状态：已保存，约 2 秒内生效                        │
    │                        [保存] [关闭]              │
    └───────────────────────────────────────────────────┘

数据流：打开时把 4 个组全部读进 `_draft`；切分组时先把当前列表刷回 draft；
保存时一次性交给 `Lines.save_pools()`（保留文件里的其他键，原子写）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..simulation import constants as C
from ..simulation.lines import Lines

CHARACTERS = (("pebbles", "五块卵石"), ("past_moon", "仰望皓月"))
# 事件是下拉里的三项；`ritual` 是 2026-10-30 按用户要求补的（仪式成功时说话）
EVENTS = (("drag", "拖拽"), ("talk", "长时间静止"), ("ritual", "仪式"))
SILENT = C.DIALOGUE_SILENT_ENTRY

_OPEN: list["DialogueEditor"] = []


class DialogueEditor(QDialog):
    def __init__(self, lines: Lines, parent=None):
        # 不挂父窗口：主窗口是整屏透明 Tool，挂上去会继承它的事件/焦点属性
        super().__init__(None)
        self.lines = lines
        self._draft: dict[tuple[str, str], list[str]] = {}
        self._current: tuple[str, str] | None = None

        self.setWindowTitle("自定义对话")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(560, 470)

        self.character_box = QComboBox()
        for key, label in CHARACTERS:
            self.character_box.addItem(label, key)
        self.event_box = QComboBox()
        for key, label in EVENTS:
            self.event_box.addItem(label, key)

        top = QHBoxLayout()
        top.addWidget(QLabel("角色"))
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
            "双击编辑。可以填充大量不触发对话来降低对话频率。"
            f"「{SILENT[1:-1]}」。"
        )
        self.hint.setWordWrap(True)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.save_button = QPushButton("保存")
        self.close_button = QPushButton("关闭")
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.save_button)
        bottom.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.hint)
        layout.addLayout(bottom)

        self.add_button.clicked.connect(self.add_line)
        self.silent_button.clicked.connect(self.add_silent)
        self.remove_button.clicked.connect(self.remove_line)
        self.save_button.clicked.connect(self.save)
        self.close_button.clicked.connect(self.close)
        self.character_box.currentIndexChanged.connect(self._reload_list)
        self.event_box.currentIndexChanged.connect(self._reload_list)

        self._load_draft()
        self._reload_list()

    # -- 数据 ---------------------------------------------------------------
    def _load_draft(self) -> None:
        self._draft = {}
        for character, _ in CHARACTERS:
            for event, _ in EVENTS:
                pool = self.lines.pool(character, event)
                self._draft[(character, event)] = list(pool) if pool else []

    def _flush(self) -> None:
        """把列表里的当前内容刷回 draft（切分组 / 保存前都要刷）。"""
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
        self.status.setText("")

    def _on_item_changed(self, *_args) -> None:
        self._flush()

    # -- 按钮 ---------------------------------------------------------------
    def add_line(self) -> None:
        self.list.blockSignals(True)
        item = QListWidgetItem("")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)
        self.list.blockSignals(False)
        self.list.setCurrentItem(item)
        self.list.editItem(item)

    def add_silent(self) -> None:
        self.list.blockSignals(True)
        item = QListWidgetItem(SILENT)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.list.addItem(item)
        self.list.blockSignals(False)
        self.list.setCurrentItem(item)

    def remove_line(self) -> None:
        selected = self.list.selectedItems()
        if not selected:
            return
        self.list.blockSignals(True)
        for item in selected:
            self.list.takeItem(self.list.row(item))
        self.list.blockSignals(False)
        self._flush()

    def save(self) -> None:
        self._flush()
        ok = self.lines.save_pools(dict(self._draft))
        if ok:
            self.status.setText(f"已保存：{self.lines.path}")
            self._load_draft()
            self._reload_list()
        else:
            self.status.setText(f"保存失败：{self.lines.last_error or '未知原因'}")


def show_editor(lines: Lines) -> DialogueEditor:
    """打开（或重新聚焦）一个编辑器窗口；引用留在模块里，防止被 Python 回收。"""
    global _OPEN
    editor = DialogueEditor(lines)
    _OPEN.append(editor)

    def _forget(*_args) -> None:
        if editor in _OPEN:
            _OPEN.remove(editor)

    editor.finished.connect(_forget)
    editor.show()
    editor.raise_()
    editor.activateWindow()
    return editor
