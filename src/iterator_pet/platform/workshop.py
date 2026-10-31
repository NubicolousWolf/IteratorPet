"""「迭代器自定义」工作室：一个窗口三个标签页（v0.1.9）。

    ┌ 迭代器自定义 ───────────────────────────────────────────────────────┐
    │ 角色 [ 五块卵石 (pebbles) ▾ ]  [新建…][复制…][删除]（v0.2.0 开放）  │
    │ ┌ 人偶外观 │ 角色卡 │ 世界书 ┐                                      │
    │ │                                                              │   │
    │ │  (当前标签页)                                                │   │
    │ └──────────────────────────────────────────────────────────────┘   │
    │ 状态：…                                        [保存]  [关闭]        │
    └────────────────────────────────────────────────────────────────────┘

三条设计取舍（都是踩过坑才定下来的）：

* **不挂父窗口**：主窗口是整屏透明 `Tool`，挂上去会继承它的事件/焦点属性，
  对话框会跟着变成点不到的样子（`dialogue_editor.py` / `llm_settings.py` 都用 `None`）。
* **保存是一次性的**：三个标签页各自持草稿，点「保存」才一起落盘。
  切角色时若有未保存改动，先问一句，**不自动保存**——玩家可能只是想看看别人的卡。
* **世界书是全局一份**（v0.1.9）：切角色不重载它。按角色分文件是 v0.2.0 的事，
  那时候这里会多一个作用域下拉。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from ..llm import cards as llm_cards
from ..llm import worldbook as llm_worldbook
from .card_tab import CardTab
from .puppet_tab import PuppetTab
from .worldbook_tab import WorldbookTab

_OPEN: list["Workshop"] = []


class Workshop(QDialog):
    def __init__(self, book: llm_worldbook.Worldbook | None = None,
                 folder: Path | str | None = None, on_saved=None, config=None):
        super().__init__(None)
        self.folder = Path(folder) if folder is not None else None
        self.on_saved = on_saved
        self.config = config
        self.book = book or llm_worldbook.Worldbook(
            self.folder / "worldbook.json" if self.folder else None
        )
        self._refs: list[llm_cards.CardRef] = []

        self.setWindowTitle("迭代器自定义")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(1120, 720)
        self._build()
        self._reload_cards()

    # -- 构建 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(9)

        top = QHBoxLayout()
        top.addWidget(QLabel("角色"))
        self.character_box = QComboBox()
        self.character_box.setMinimumWidth(280)
        self.character_box.currentIndexChanged.connect(self._on_character)
        top.addWidget(self.character_box, 1)
        self.new_button = QPushButton("新建…")
        self.copy_button = QPushButton("复制…")
        self.delete_button = QPushButton("删除")
        for button in (self.new_button, self.copy_button, self.delete_button):
            button.setEnabled(False)
            button.setToolTip("v0.2.0 开放：创建 / 复制 / 删除自定义迭代器会有角色注册表参与")
            top.addWidget(button)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        self.puppet_tab = PuppetTab(self.folder, config=self.config)
        self.card_tab = CardTab(self.folder)
        self.worldbook_tab = WorldbookTab(self.book)
        self.tabs.addTab(self.puppet_tab, "人偶外观")
        self.tabs.addTab(self.card_tab, "角色卡")
        self.tabs.addTab(self.worldbook_tab, "世界书")
        outer.addWidget(self.tabs, 1)

        for tab in (self.puppet_tab, self.card_tab, self.worldbook_tab):
            tab.changed.connect(self._on_changed)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.save_button = QPushButton("保存")
        self.close_button = QPushButton("关闭")
        bottom.addWidget(self.save_button)
        bottom.addWidget(self.close_button)
        outer.addLayout(bottom)

        self.save_button.clicked.connect(self.save)
        self.close_button.clicked.connect(self.close)

    # -- 角色列表 -----------------------------------------------------------
    def _reload_cards(self, keep: str | None = None) -> None:
        current = keep or getattr(self, "_key", "pebbles")
        self._refs = llm_cards.list_cards(self.folder)
        self.character_box.blockSignals(True)
        self.character_box.clear()
        index = 0
        for row, ref in enumerate(self._refs):
            mark = "内置" if ref.builtin else "自定义"
            self.character_box.addItem(f"{ref.name}（{ref.key} · {mark}）", ref.key)
            if ref.key == current:
                index = row
        self.character_box.setCurrentIndex(index)
        self.character_box.blockSignals(False)
        self._load_character(self.character_box.currentData() or "pebbles")

    def _load_character(self, key: str) -> None:
        self._key = key
        ref = next((item for item in self._refs if item.key == key), None)
        if ref is not None and not ref.builtin:
            self.delete_button.setEnabled(False)   # v0.2.0 才真的能删
            self.delete_button.setToolTip("v0.2.0 开放：删除会和历史记录/本地台词一起处理")
        self.puppet_tab.load(key)
        self.card_tab.load(key)
        self.status.setText(f"正在编辑：{key}")

    def _on_character(self, _index: int) -> None:
        target = self.character_box.currentData()
        if not target or target == getattr(self, "_key", None):
            return
        if self._any_dirty():
            answer = QMessageBox.question(
                self, "有未保存的改动",
                "当前这一页还有没保存的改动，切换角色会丢掉。要继续吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.character_box.blockSignals(True)
                keys = [item.key for item in self._refs]
                if getattr(self, "_key", None) in keys:
                    self.character_box.setCurrentIndex(keys.index(self._key))
                self.character_box.blockSignals(False)
                return
        self._load_character(target)

    # -- 保存 ---------------------------------------------------------------
    def _any_dirty(self) -> bool:
        return any(
            tab.is_dirty() for tab in (self.puppet_tab, self.card_tab, self.worldbook_tab)
        )

    def _on_changed(self) -> None:
        self.save_button.setEnabled(True)
        self.status.setText("有未保存的改动")

    def save(self) -> None:
        """只写**有改动**的页：没动过的世界书不该被我们顺手重写一遍。

        这条不是洁癖——一份 27 条的 `worldbook.json` 每次点保存都重排一次缩进，
        玩家手改的排版会莫名其妙变样；而"世界书坏了"也不该拦住"我只想改个颜色"。
        """
        errors: list[str] = []
        touched = 0
        for name, tab in (("人偶外观", self.puppet_tab), ("角色卡", self.card_tab),
                          ("世界书", self.worldbook_tab)):
            if not tab.is_dirty():
                continue
            touched += 1
            error = tab.save()
            if error:
                errors.append(f"{name}：{error}")
        if errors:
            self.status.setText("保存失败：" + "；".join(errors))
            QMessageBox.warning(self, "保存失败", "\n".join(errors))
            return
        if not touched:
            self.status.setText("没有改动")
            return
        self.save_button.setEnabled(False)
        self.status.setText("已保存")
        self._reload_cards(keep=getattr(self, "_key", "pebbles"))
        if self.on_saved is not None:
            self.on_saved()

    # -- 关闭 ---------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._any_dirty():
            answer = QMessageBox.question(
                self, "有未保存的改动", "还有没保存的改动，直接关掉吗？"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()


def show_workshop(book=None, folder=None, on_saved=None, config=None) -> Workshop:
    """打开（或复用）一个工作室窗口；引用留在模块里，防止被 Python 回收。"""
    global _OPEN
    workshop = Workshop(book=book, folder=folder, on_saved=on_saved, config=config)
    _OPEN.append(workshop)

    def _forget(*_args) -> None:
        if workshop in _OPEN:
            _OPEN.remove(workshop)

    workshop.finished.connect(_forget)
    workshop.show()
    workshop.raise_()
    workshop.activateWindow()
    return workshop
