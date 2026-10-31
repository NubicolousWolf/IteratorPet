"""角色卡编辑页（酒馆 V2 的 8 个人类可读字段）。

写回时**只动这 8 个键**：`extensions`（外观草稿、`oracle_id`、`room`）和未来字段
一律原样保留 —— 否则玩家在人偶页捏好的配色会被"保存角色卡"顺手抹掉。
这条由 `llm/cards.py::save_card` 保证，本页只负责收集文本。

`system_prompt` 留空时右侧会有黄字提示：空着会用内置兜底人设（`prompt.FALLBACK_CARDS`），
对自定义角色来说那就是"串台成五块卵石"，必须让玩家看见。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..llm import cards as llm_cards
from ..llm import prompt as llm_prompt

TEXT_FIELDS = (
    ("name", "显示名", "菜单与气泡都用它"),
    ("description", "描述", "世界观与身份；和下面两项一起拼成角色定义"),
    ("personality", "性格", ""),
    ("scenario", "场景", ""),
    ("system_prompt", "系统提示词", "空着会用内置兜底（自定义角色会串台成五块卵石）"),
    ("post_history_instructions", "回复约束", "句式/字数要求，放在对话历史之后"),
    ("first_mes", "开场白", "目前不自动播放，留给玩家参考"),
    ("mes_example", "对话示例", "带 <START> 的示例片段"),
)


class CardTab(QWidget):
    """读写 ``characters/<key>.json`` 的 8 个文本字段。"""

    changed = Signal()

    def __init__(self, folder=None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.key = "pebbles"
        self._dirty = False
        self._editors: dict[str, QWidget] = {}
        self._error = ""
        self._build()

    # -- 构建 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(8)

        header = QHBoxLayout()
        self.path_label = QLabel("")
        self.path_label.setStyleSheet("color:#777; font-size:11px;")
        header.addWidget(self.path_label, 1)
        self.fallback_button = QPushButton("用内置兜底填满")
        self.fallback_button.setToolTip("把当前系统提示词/性格/场景换成内置兜底（自定义角色会变成五块卵石）")
        header.addWidget(self.fallback_button)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        for name, label, hint in TEXT_FIELDS:
            if name in ("name",):
                editor = QLineEdit()
                editor.textChanged.connect(self._mark_dirty)
            else:
                editor = QPlainTextEdit()
                editor.setFixedHeight(96 if name != "name" else 60)
                editor.textChanged.connect(self._mark_dirty)
            self._editors[name] = editor
            caption = QLabel(label)
            if hint:
                caption.setToolTip(hint)
            row = editor
            if hint:
                holder = QVBoxLayout()
                holder.setContentsMargins(0, 0, 0, 0)
                holder.setSpacing(2)
                holder.addWidget(editor)
                note = QLabel(hint)
                note.setStyleSheet("color:#888; font-size:11px;")
                note.setWordWrap(True)
                holder.addWidget(note)
                wrapper = QWidget()
                wrapper.setLayout(holder)
                row = wrapper
            form.addRow(caption, row)

        # 「消息算几条」是玩家最容易踩的坑：一条消息 = 她一条、你一条各算一条
        self.estimate = QLabel("")
        self.estimate.setStyleSheet("color:#777; font-size:11px;")
        form.addRow("", self.estimate)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.fallback_button.clicked.connect(self.fill_fallback)

    # -- 载入 / 保存 ---------------------------------------------------------
    def load(self, key: str) -> None:
        self.key = key
        card, error = llm_cards.read_card(key, self.folder)
        self._error = error
        self.block_all(True)
        for name, _label, _hint in TEXT_FIELDS:
            editor = self._editors[name]
            if isinstance(editor, QLineEdit):
                editor.setText(getattr(card, name))
            else:
                editor.setPlainText(getattr(card, name))
        self.block_all(False)
        raw, _error = llm_cards.read_document(key, self.folder)
        oracle = ""
        if isinstance(raw, dict):
            extensions = raw.get("extensions")
            if isinstance(extensions, dict) and isinstance(extensions.get("rainworld"), dict):
                oracle = str(extensions["rainworld"].get("oracle_id") or "")
        note = f"文件：{llm_cards.card_path(key, self.folder)}"
        if oracle:
            note += f"　·　oracle_id = {oracle}（保存时原样保留）"
        if error:
            note += f"　·　读取问题：{error}"
        self.path_label.setText(note)
        self._dirty = False
        self._update_estimate()

    def block_all(self, blocked: bool) -> None:
        for editor in self._editors.values():
            editor.blockSignals(blocked)

    def collect(self) -> llm_prompt.Card:
        card = llm_prompt.Card()
        for name, _label, _hint in TEXT_FIELDS:
            editor = self._editors[name]
            value = editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
            setattr(card, name, value.strip())
        return card

    def save(self) -> str:
        error = llm_cards.save_card(self.key, self.collect(), self.folder)
        if error:
            return error
        self._dirty = False
        return ""

    def is_dirty(self) -> bool:
        return self._dirty

    # -- 交互 ---------------------------------------------------------------
    def _mark_dirty(self, *_args) -> None:
        if not self._dirty:
            self._dirty = True
        self._update_estimate()
        self.changed.emit()

    def _update_estimate(self) -> None:
        card = self.collect()
        tokens = llm_prompt.estimate_tokens(card.definition_text())
        note = f"角色定义约 {tokens} token"
        if not card.system_prompt:
            note += "　·　系统提示词为空 → 会用内置兜底"
        self.estimate.setText(note)

    def fill_fallback(self) -> None:
        """把内置兜底人设填进空着的字段（不覆盖玩家已经写过的内容）。"""
        source = llm_prompt.FALLBACK_CARDS.get(self.key)
        if source is None:
            source = llm_prompt.Card(
                name=self._editors["name"].text() or self.key,
                system_prompt="你扮演《雨世界》里的一个迭代器。冷淡、理性、说话简短。",
                post_history_instructions="用一句中文回答，不超过 40 字。不用括号描写动作，不自称 AI。",
            )
        pairs = (("system_prompt", source.system_prompt),
                 ("post_history_instructions", source.post_history_instructions),
                 ("personality", source.personality),
                 ("scenario", source.scenario))
        for name, value in pairs:
            if not value:
                continue
            editor = self._editors[name]
            current = editor.toPlainText().strip()
            if current:
                continue
            editor.setPlainText(value)
        self._mark_dirty()
