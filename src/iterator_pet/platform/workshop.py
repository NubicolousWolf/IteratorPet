"""「迭代器自定义」工作室：一个窗口三个标签页（v0.1.9 起）。

    ┌ 迭代器自定义 ───────────────────────────────────────────────────────┐
    │ 角色 [ 五块卵石 (pebbles · 内置) ▾ ] [新建…][复制…][删除][设为当前角色]│
    │ ┌ 人偶外观 │ 本地对话 │ 角色卡 │ 世界书 ┐                          │
    │ │                                                              │   │
    │ │  (当前标签页)                                                │   │
    │ └──────────────────────────────────────────────────────────────┘   │
    │ 状态：…                                        [保存]  [关闭]        │
    └────────────────────────────────────────────────────────────────────┘

四条设计取舍（都是踩过坑才定下来的）：

* **不挂父窗口**：主窗口是整屏透明 `Tool`，挂上去会继承它的事件/焦点属性，
  对话框会跟着变成点不到的样子（`dialogue_editor.py` / `llm_settings.py` 都用 `None`）。
* **保存是一次性的**：三个标签页各自持草稿，点「保存」才一起落盘。
  切角色时若有未保存改动，先问一句，**不自动保存**——玩家可能只是想看看别人的卡。
* **角色清单来自注册表**（`characters.py`），v0.2.0 起不再是写死的两项：
  新建/复制/删除之后本窗口与桌面菜单同时生效，不需要重开。
* **删除角色会顺手把桌面切走**：删掉的要是正在跑的那位，不切走就会留下
  "配置里点着一个不存在的角色、人偶还穿着她的外观"的孤儿状态。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from .. import characters
from ..llm import worldbook as llm_worldbook
from ..simulation import appearance as ap
from .card_tab import CardTab
from .dialogue_tab import DialogueTab
from .puppet_tab import PuppetTab
from .worldbook_tab import WorldbookTab

_OPEN: list["Workshop"] = []


class _CharacterDialog(QDialog):
    """新建 / 复制角色的小表单：``key``（文件名）+ 显示名 +（新建时）骨骼基底。"""

    def __init__(self, parent, *, copying: str = "", key: str = "", name: str = "",
                 base: str = "pebbles", show_base: bool = True):
        super().__init__(parent)
        self.setWindowTitle("复制角色" if copying else "新建角色")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(420)

        self.key_edit = QLineEdit(key)
        self.key_edit.setPlaceholderText("小写字母/数字/下划线/减号，例如 seven_red_suns")
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("菜单里显示的名字，例如 七轮红日")
        self.base_box = QComboBox()
        for token, label in ap.BASE_TEMPLATES.items():
            self.base_box.addItem(f"{label}（{token}）", token)
        self.base_box.setCurrentIndex(max(0, self.base_box.findData(base)))
        self.base_box.setEnabled(show_base)

        form = QFormLayout()
        form.addRow("key（文件名）", self.key_edit)
        form.addRow("显示名", self.name_edit)
        form.addRow("骨骼基底", self.base_box)
        if copying:
            hint = QLabel(f"会把「{copying}」的整张卡（含外观与未知字段）原样复制一份。")
        else:
            hint = QLabel(
                "骨骼基底决定眼睛高度、躯干高度与生气了会不会用神秘小技能（这个是早期架构遗留物，也许有一天会解绑）；颜色稍后在人偶外观页调。"
            )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777; font-size:11px;")

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#c0392b;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(hint)
        outer.addWidget(self.error)
        outer.addWidget(buttons)

    def _on_accept(self) -> None:
        key = self.key_edit.text().strip()
        error = characters.validate_key(key)
        if error:
            self.error.setText(error)      # 就地报错，不关窗
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self.key_edit.text().strip(),
            self.name_edit.text().strip(),
            self.base_box.currentData() or "pebbles",
        )


class Workshop(QDialog):
    def __init__(self, book: llm_worldbook.Worldbook | None = None,
                 folder: Path | str | None = None, on_saved=None, config=None,
                 current: str | None = None, on_activate=None,
                 lines=None, dialogue_enabled: bool = True, on_dialogue_enabled=None):
        super().__init__(None)
        self.folder = Path(folder) if folder is not None else None
        self.on_saved = on_saved
        self.on_activate = on_activate
        self.on_dialogue_enabled = on_dialogue_enabled
        self.dialogue_enabled = bool(dialogue_enabled)
        self.config = config
        # 台词池：窗口层把仿真的那份传进来（它带热重载状态）；单独构造工坊时兜底一份。
        if lines is not None:
            self.lines = lines
        else:
            from ..simulation.lines import Lines, default_path

            self.lines = Lines(path=(self.folder / "lines.json") if self.folder
                               else default_path())
        self._desktop = current or "pebbles"     # 桌面正在跑哪一位（删角色时要跟着切）
        self.book = book or llm_worldbook.Worldbook(
            llm_worldbook.resolve_path(self._desktop, self.folder)
        )
        self._refs: list[characters.Character] = []

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
        self.character_box.setMinimumWidth(300)
        self.character_box.currentIndexChanged.connect(self._on_character)
        top.addWidget(self.character_box, 1)
        self.new_button = QPushButton("新建…")
        self.new_button.setToolTip("从空卡开始")
        self.copy_button = QPushButton("复制…")
        self.copy_button.setToolTip("以当前角色为底复制一份")
        self.delete_button = QPushButton("删除")
        self.delete_button.setToolTip("只删卡文件；聊天记录与本地台词默认保留")
        self.activate_button = QPushButton("设为当前角色")
        self.activate_button.setToolTip("让桌面上的人偶换成这一位")
        for button in (self.new_button, self.copy_button, self.delete_button,
                       self.activate_button):
            top.addWidget(button)
        outer.addLayout(top)

        self.tabs = QTabWidget()
        self.puppet_tab = PuppetTab(self.folder, config=self.config)
        # 顺序：看起来什么样 → 说什么 → 她是谁 → 她知道什么（由外往里）
        self.dialogue_tab = DialogueTab(
            self.lines, on_enabled=self._on_dialogue_enabled,
            enabled=self.dialogue_enabled,
        )
        self.card_tab = CardTab(self.folder)
        self.worldbook_tab = WorldbookTab(
            self.book, folder=self.folder, character=self._desktop
        )
        self.tabs.addTab(self.puppet_tab, "人偶外观")
        self.tabs.addTab(self.dialogue_tab, "本地对话")
        self.tabs.addTab(self.card_tab, "角色卡")
        self.tabs.addTab(self.worldbook_tab, "世界书")
        outer.addWidget(self.tabs, 1)

        for tab in (self.puppet_tab, self.dialogue_tab, self.card_tab,
                    self.worldbook_tab):
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
        self.new_button.clicked.connect(self.new_character)
        self.copy_button.clicked.connect(self.copy_character)
        self.delete_button.clicked.connect(self.delete_character)
        self.activate_button.clicked.connect(self.activate_character)

    def _on_dialogue_enabled(self, enabled: bool) -> None:
        """「不接入LLM时，启用本地对话」：立刻交给窗口层（写 config + 生效）。"""
        self.dialogue_enabled = bool(enabled)
        if callable(self.on_dialogue_enabled):
            self.on_dialogue_enabled(bool(enabled))

    # -- 角色列表 -----------------------------------------------------------
    def _reload_cards(self, keep: str | None = None) -> None:
        current = keep or getattr(self, "_key", self._desktop)
        self._refs = characters.characters(self.folder)
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
        builtin = bool(ref.builtin) if ref is not None else False
        self.delete_button.setEnabled(not builtin)
        self.delete_button.setToolTip(
            "内置角色不能删除（默认外观写在源码里）" if builtin
            else "只删卡文件；聊天记录与本地台词默认保留"
        )
        self.puppet_tab.load(key)
        self.card_tab.load(key)
        self.worldbook_tab.set_character(key)
        self._refresh_activate()
        self.status.setText(f"正在编辑：{key}")

    def _refresh_activate(self) -> None:
        """「设为当前角色」在"本来就是当前角色"时置灰，免得玩家以为点了没反应。"""
        same = getattr(self, "_key", "") == self._desktop
        self.activate_button.setEnabled(not same)
        self.activate_button.setToolTip(
            "桌面上的就是这一位" if same else "让桌面上的人偶换成这一位"
        )

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

    # -- 生命周期 -----------------------------------------------------------
    def _perform_create(self, key: str, name: str, base: str) -> tuple[str, str]:
        """真建一个角色 → ``(新 key, 错误)``。对话框之外的逻辑都在这儿（可测）。"""
        ref, error = characters.create(key, name, base, self.folder)
        if error or ref is None:
            return "", error or "原因未知"
        self._reload_cards(keep=ref.key)
        self.dialogue_tab.refresh(keep=ref.key)
        return ref.key, ""

    def new_character(self) -> None:
        dialog = _CharacterDialog(self, show_base=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key, name, base = dialog.values()
        new_key, error = self._perform_create(key, name, base)
        if error:
            QMessageBox.warning(self, "新建失败", error)
            return
        self.status.setText(
            f"已新建「{new_key}」——接着改卡和颜色，点保存才写进文件"
        )

    def _perform_duplicate(self, source: str, key: str, new_name: str) -> tuple[str, str]:
        ref, error = characters.duplicate(source, key, new_name, self.folder)
        if error or ref is None:
            return "", error or "原因未知"
        self._reload_cards(keep=ref.key)
        self.dialogue_tab.refresh(keep=ref.key)
        return ref.key, ""

    def copy_character(self) -> None:
        source = getattr(self, "_key", "")
        if not source:
            return
        name = next((r.name for r in self._refs if r.key == source), source)
        dialog = _CharacterDialog(
            self, copying=name, key=f"{source}_copy", name=f"{name}（副本）", show_base=False
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key, new_name, _base = dialog.values()
        new_key, error = self._perform_duplicate(source, key, new_name)
        if error:
            QMessageBox.warning(self, "复制失败", error)
            return
        self.status.setText(f"已复制成「{new_key}」")

    def _perform_delete(self, key: str, drop_chats: bool = False) -> str:
        """真删：删卡 → 如果删的是桌面上那位，**先把桌面切走**。返回错误串。

        "先切走"这段不是贴心，是防孤儿状态：配置里点着一个不存在的角色时，
        人偶还穿着她的外观，而菜单里一项都不打勾——玩家会以为"我删了个假的"。
        """
        error = characters.delete(key, self.folder, drop_chats=drop_chats)
        if error:
            return error
        if key == self._desktop:
            self.activate_character(characters.BUILTIN_KEYS[0], silent=True)
        self._reload_cards(keep=self._desktop)
        self.dialogue_tab.refresh(keep=self._desktop)
        return ""

    def delete_character(self) -> None:
        key = getattr(self, "_key", "")
        ref = next((item for item in self._refs if item.key == key), None)
        if ref is None or ref.builtin:
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除角色")
        box.setText(
            f"删除「{ref.name}」（{ref.key}）？\n\n"
            f"卡文件 characters/{ref.key}.json 会被移除。"
        )
        keep = box.addButton("删除（保留聊天记录）", QMessageBox.ButtonRole.AcceptRole)
        wipe = box.addButton("连同聊天记录一起删", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (keep, wipe):
            return
        error = self._perform_delete(key, drop_chats=(clicked is wipe))
        if error:
            QMessageBox.warning(self, "删除失败", error)
            return
        self.status.setText(f"已删除「{ref.name}」（{ref.key}）")

    def activate_character(self, key: str | None = None, silent: bool = False) -> None:
        """把这一位设为桌面角色（顺带记住，`删除` 用它判断要不要切走）。"""
        target = key or getattr(self, "_key", "")
        if not target or target == self._desktop:
            return
        if callable(self.on_activate):
            self.on_activate(target)
        self._desktop = target
        self._refresh_activate()
        if not silent:
            name = next((r.name for r in self._refs if r.key == target), target)
            self.status.setText(f"桌面角色已切换到「{name}」")

    # -- 保存 ---------------------------------------------------------------
    def _any_dirty(self) -> bool:
        return any(
            tab.is_dirty()
            for tab in (self.puppet_tab, self.dialogue_tab, self.card_tab,
                        self.worldbook_tab)
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
        for name, tab in (("人偶外观", self.puppet_tab), ("本地对话", self.dialogue_tab),
                          ("角色卡", self.card_tab), ("世界书", self.worldbook_tab)):
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


def show_workshop(book=None, folder=None, on_saved=None, config=None,
                  current: str | None = None, on_activate=None, lines=None,
                  dialogue_enabled: bool = True, on_dialogue_enabled=None) -> Workshop:
    """打开（或复用）一个工作室窗口；引用留在模块里，防止被 Python 回收。"""
    global _OPEN
    workshop = Workshop(book=book, folder=folder, on_saved=on_saved, config=config,
                        current=current, on_activate=on_activate, lines=lines,
                        dialogue_enabled=dialogue_enabled,
                        on_dialogue_enabled=on_dialogue_enabled)
    _OPEN.append(workshop)

    def _forget(*_args) -> None:
        if workshop in _OPEN:
            _OPEN.remove(workshop)

    workshop.finished.connect(_forget)
    workshop.show()
    workshop.raise_()
    workshop.activateWindow()
    return workshop
