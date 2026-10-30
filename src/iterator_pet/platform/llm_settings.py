"""「大语言模型」设置页（一屏，不套多层标签，见 ``docs/PLAN-llm.md`` §1.1）。

    ┌ 大语言模型 ────────────────────────────────────────────────┐
    │ 服务   (•) DeepSeek 官方   ( ) 自定义                       │
    │ Base URL [ https://api.deepseek.com          ] [拉取模型]    │
    │ API Key  [ ****                              ] [测试连接]    │
    │ 协议   (•) OpenAI Chat Completions   ( ) Anthropic Messages  │
    │ 模型   [ deepseek-chat ▾ ]  上下文 64k · 输出上限 8k         │
    │ 采样   温度 [1.1]  最大输出 [120] tokens  超时 [8] s         │
    │ 历史   最近 [10] 条（0 = 不带历史；一条消息算一条）           │
    │ 开关   [ ] 允许控制行动   [ ] 拖文件时把文件名交给 LLM       │
    │ 状态：…                                                      │
    │                                        [保存]  [取消]        │
    └──────────────────────────────────────────────────────────────┘

三条交互上的取舍：

* **模型框一直可编辑**：拉不到列表（401 / 超时 / 中转返回体不认识）时玩家
  直接手打模型名就能用，不必等我们适配他的中转（审计 A10）。
* **拉取与测试连接都在线程里**（``llm/models.py::BackgroundJob``），页面用
  50 ms 定时器轮询 —— 卡住的网络不该冻住设置页；worker 不碰任何 Qt 对象（A3）。
* **key 一直是掩码输入**，且框下面常驻一行说明"明文存在 llm.json / 可用环境变量"
  —— 比"保存时提醒一次"更不容易被划过去（审计 A9：日志里永远只有 ``sk-xxxx***``）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..llm import config as llm_config
from ..llm import models as llm_models

OPEN: list["LLMSettingsDialog"] = []
KEY_HINT = "明文存在 llm.json（本机可见）；也可以用环境变量 ITERATORPET_LLM_KEY，优先级更高。"


def format_context(tokens: int) -> str:
    """65536 → ``64k``（按 1024 算，和"64k 上下文"的说法一致）；不知道就 ``?``。"""
    if tokens <= 0:
        return "?"
    if tokens >= 1024 * 1024:
        return f"{tokens / (1024 * 1024):.1f}M".replace(".0M", "M")
    if tokens >= 1024:
        return f"{tokens / 1024:.0f}k"
    return str(tokens)


class LLMSettingsDialog(QDialog):
    def __init__(self, store: llm_config.LLMStore, transport=None):
        # 不挂父窗口：主窗口是整屏透明 Tool，挂上去会继承它的事件/焦点属性
        super().__init__(None)
        self.store = store
        self.transport = transport or llm_models.UrllibTransport()
        self.result_config: llm_config.LLMConfig | None = None     # 保存后才有
        self._models: dict[str, llm_models.ModelInfo] = {}
        self._jobs: list[tuple[llm_models.BackgroundJob, str]] = []

        self.setWindowTitle("大语言模型")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(640, 540)
        self._build()
        self._load(store.config)

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll_jobs)

    # -- 构建 ---------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(9)

        self.provider_deepseek = QRadioButton("DeepSeek 官方")
        self.provider_custom = QRadioButton("自定义")
        self._provider_group = QButtonGroup(self)
        self._provider_group.addButton(self.provider_deepseek)
        self._provider_group.addButton(self.provider_custom)
        self.provider_deepseek.toggled.connect(self._on_provider_changed)
        outer.addLayout(self._row("服务", self.provider_deepseek, self.provider_custom))

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.deepseek.com")
        self.fetch_button = QPushButton("拉取模型")
        self.fetch_button.setToolTip("GET /models：拉到列表才能从下拉里选")
        self.fetch_button.clicked.connect(self.fetch_models)
        outer.addLayout(self._row("Base URL", self.base_url, self.fetch_button))

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-…")
        self.probe_button = QPushButton("测试连接")
        self.probe_button.setToolTip("发一次 max_tokens=1 的最小请求")
        self.probe_button.clicked.connect(self.test_connection)
        outer.addLayout(self._row("API Key", self.api_key, self.probe_button))
        key_hint = QLabel(KEY_HINT)
        key_hint.setStyleSheet("color:#777; font-size:11px;")
        key_hint.setWordWrap(True)
        outer.addWidget(self._indent_widget(key_hint))

        self.protocol_openai = QRadioButton("OpenAI Chat Completions")
        self.protocol_anthropic = QRadioButton("Anthropic Messages")
        self._protocol_group = QButtonGroup(self)
        self._protocol_group.addButton(self.protocol_openai)
        self._protocol_group.addButton(self.protocol_anthropic)
        outer.addLayout(self._row("协议", self.protocol_openai, self.protocol_anthropic))

        self.model_box = QComboBox()
        self.model_box.setEditable(True)               # 拉不到列表也能手打（A10）
        self.model_box.setMinimumWidth(220)
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
        self.model_box.editTextChanged.connect(self._on_model_changed)
        self.context_label = QLabel("上下文 ?")
        self.context_label.setStyleSheet("color:#666")
        outer.addLayout(self._row("模型", self.model_box, self.context_label))

        sampling = QHBoxLayout()
        sampling.setContentsMargins(0, 0, 0, 0)
        sampling.setSpacing(8)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setDecimals(2)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(1, 32768)
        self.timeout_s = QDoubleSpinBox()
        self.timeout_s.setRange(1.0, 120.0)
        self.timeout_s.setDecimals(1)
        self.timeout_s.setSuffix(" s")
        for widget in (QLabel("温度"), self.temperature, QLabel("最大输出"),
                       self.max_tokens, QLabel("tokens"), QLabel("超时"), self.timeout_s):
            sampling.addWidget(widget)
        sampling.addStretch(1)
        outer.addLayout(self._row("采样", sampling))

        self.history = QSpinBox()
        self.history.setRange(0, 50)
        self.history.setSuffix(" 条")
        history_hint = QLabel("0 = 不带历史；一条消息算一条（她一条、玩家一条各算一条）")
        history_hint.setStyleSheet("color:#777; font-size:11px;")
        outer.addLayout(self._row("历史", self.history, history_hint))

        self.allow_actions = QCheckBox("允许控制行动（investigate / talk / 起仪式）")
        self.file_drop = QCheckBox("把文件拖到人偶上时，把文件名交给 LLM")
        toggles = QVBoxLayout()
        toggles.setContentsMargins(0, 0, 0, 0)
        toggles.setSpacing(4)
        toggles.addWidget(self.allow_actions)
        toggles.addWidget(self.file_drop)
        outer.addWidget(self._indent_widget(toggles))

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(46)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.status.setStyleSheet(
            "background:#f2f2f2; border:1px solid #dcdcdc; border-radius:4px; padding:6px;"
        )
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)

        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.save_button.clicked.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @staticmethod
    def _row(label: str, *widgets, label_width: int = 70) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        caption = QLabel(label)
        caption.setFixedWidth(label_width)
        caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(caption)
        for widget in widgets:
            if isinstance(widget, QLayout):       # 采样那一行整块塞进来
                row.addLayout(widget, 1)
            else:
                row.addWidget(widget, 0 if isinstance(widget, (QPushButton, QLabel)) else 1)
        return row

    @staticmethod
    def _indent_widget(inner) -> QWidget:
        """把一行/一块内容缩进到"字段列"，和上面的输入框左对齐。

        必须返回 **widget**（``addWidget`` 不收 layout），所以外面套一层透明容器。
        """
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        spacer = QLabel("")
        spacer.setFixedWidth(78)                 # = label_width(70) + spacing(8)
        row.addWidget(spacer)
        if isinstance(inner, QLabel):
            row.addWidget(inner, 1)
        else:
            row.addLayout(inner, 1)
        return host

    # -- 装载 / 收集 ---------------------------------------------------------
    def _load(self, cfg: llm_config.LLMConfig) -> None:
        self.provider_deepseek.setChecked(cfg.provider != "custom")
        self.provider_custom.setChecked(cfg.provider == "custom")
        self.base_url.setText(cfg.base_url)
        self.api_key.setText(cfg.api_key)
        protocol_radio = self.protocol_anthropic if cfg.protocol == "anthropic" else self.protocol_openai
        protocol_radio.setChecked(True)
        self._set_models(
            [llm_models.ModelInfo(cfg.model or "", context_window=cfg.context_window)],
            keep=cfg.model,
        )
        self.temperature.setValue(float(cfg.temperature))
        self.max_tokens.setValue(int(cfg.max_tokens))
        self.timeout_s.setValue(float(cfg.timeout_s))
        self.history.setValue(int(cfg.history_messages))
        self.allow_actions.setChecked(bool(cfg.allow_actions))
        self.file_drop.setChecked(bool(cfg.file_drop))
        self._on_provider_changed()

        hints = []
        if not llm_config.has_key(cfg):
            hints.append("还没有 API Key：填上（或设环境变量）才能拉模型。")
        if self.store.last_error:
            hints.append(self.store.last_error)
        self._set_status("；".join(hints) or "填好 key → 拉取模型 → 选一个 → 保存。")

    def collect(self) -> llm_config.LLMConfig:
        """UI → :class:`LLMConfig`（不落盘，``_save`` 才落盘）。"""
        base = self.store.config
        cfg = llm_config.normalize(
            {
                "provider": "deepseek" if self.provider_deepseek.isChecked() else "custom",
                "protocol": "anthropic" if self.protocol_anthropic.isChecked() else "openai",
                "base_url": self.base_url.text(),
                "api_key": self.api_key.text(),
                "model": self.model_box.currentText().strip(),
                "temperature": self.temperature.value(),
                "max_tokens": self.max_tokens.value(),
                "timeout_s": self.timeout_s.value(),
                "history_messages": self.history.value(),
                "allow_actions": self.allow_actions.isChecked(),
                "file_drop": self.file_drop.isChecked(),
                "context_window": self._current_context() or base.context_window,
            }
        )
        # 设置页不管的字段原样带回去（别在保存时悄悄改掉它们）
        cfg.version = base.version
        cfg.api_key_env = base.api_key_env
        cfg.worldbook_budget = base.worldbook_budget
        cfg.action_cooldown_s = base.action_cooldown_s
        return cfg

    def _current_context(self) -> int:
        info = self._models.get(self.model_box.currentText().strip())
        return info.context_window if info else 0

    # -- 交互 ---------------------------------------------------------------
    def _on_provider_changed(self, _checked: bool = False) -> None:
        official = self.provider_deepseek.isChecked()
        self.base_url.setReadOnly(official)
        if official:
            url, protocol = llm_config.PRESETS["deepseek"]
            self.base_url.setText(url)
            radio = self.protocol_anthropic if protocol == "anthropic" else self.protocol_openai
            radio.setChecked(True)

    def _on_model_changed(self, _value=None) -> None:
        info = self._models.get(self.model_box.currentText().strip())
        if not info:
            self.context_label.setText("上下文 ?（手填的模型没有元信息）")
            return
        text = f"上下文 {format_context(info.context_window)}"
        if info.max_output_tokens:
            text += f" · 输出上限 {format_context(info.max_output_tokens)}"
        self.context_label.setText(text)

    def fetch_models(self) -> None:
        """拉模型列表（线程 + 轮询，不冻界面）。"""
        key = llm_config.resolve_key(self.collect())
        if not key:
            self._set_status("先填 API Key，或者设一个 ITERATORPET_LLM_KEY 环境变量。")
            return
        cfg = self.collect()
        self.fetch_button.setEnabled(False)
        self._set_status("正在拉取模型…")
        self._start_job(
            "models",
            llm_models.run_fetch,
            self.transport,
            cfg.base_url,
            key,
            cfg.protocol,
            llm_models.INTERACTIVE_TIMEOUT,
        )

    def test_connection(self) -> None:
        key = llm_config.resolve_key(self.collect())
        if not key:
            self._set_status("先填 API Key，或者设一个 ITERATORPET_LLM_KEY 环境变量。")
            return
        cfg = self.collect()
        if not cfg.model:
            self._set_status("先选一个模型，再点「测试连接」。")
            return
        self.probe_button.setEnabled(False)
        self._set_status("正在测试连接…")
        self._start_job(
            "probe",
            llm_models.run_probe,
            self.transport,
            cfg.base_url,
            key,
            cfg.model,
            cfg.protocol,
            llm_models.INTERACTIVE_TIMEOUT,
        )

    def _start_job(self, kind: str, fn, *args) -> None:
        self._jobs.append((llm_models.BackgroundJob(fn, *args).start(), kind))
        self._timer.start()

    def _poll_jobs(self) -> None:
        if not self._jobs:
            self._timer.stop()
            return
        still: list[tuple[llm_models.BackgroundJob, str]] = []
        for job, kind in self._jobs:
            result = job.poll()
            if result is None:
                still.append((job, kind))
            elif kind == "models":
                self._on_models(result)
            else:
                self._on_probe(result)
        self._jobs = still
        if not still:
            self._timer.stop()

    def _on_models(self, result: llm_models.FetchResult) -> None:
        self.fetch_button.setEnabled(True)
        if not result.ok:
            # 退化成手填：下拉框本来就是可编辑的，这里只把原因说清楚（A10）
            self._set_status(f"拉取模型失败：{result.reason}。可以直接在「模型」里手填名字。")
            return
        keep = self.model_box.currentText().strip()
        self._set_models(list(result.models), keep=keep)
        self._set_status(f"已拉取 {len(result.models)} 个模型，选一个再「保存」。")

    def _on_probe(self, result: llm_models.FetchResult) -> None:
        self.probe_button.setEnabled(True)
        if result.ok:
            self._set_status("连接正常（HTTP 200）。可以点「保存」了。")
        else:
            self._set_status(f"连接失败：{result.reason}")

    def _set_models(self, models: list[llm_models.ModelInfo], keep: str = "") -> None:
        self._models = {info.id: info for info in models if info.id}
        blocked = self.model_box.blockSignals(True)
        self.model_box.clear()
        for info in models:
            if info.id:
                self.model_box.addItem(info.id, info.id)
        if keep:
            if keep not in self._models:        # 玩家手填的，帮他留着
                self.model_box.insertItem(0, keep, keep)
            self.model_box.setCurrentText(keep)
        self.model_box.blockSignals(blocked)
        self._on_model_changed()

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    # -- 保存 ---------------------------------------------------------------
    def _save(self) -> None:
        cfg = self.collect()
        if not cfg.base_url.startswith(("http://", "https://")):
            self._set_status("Base URL 要以 http:// 或 https:// 开头。")
            return
        if not cfg.model:
            self._set_status("先选一个模型（拉列表，或者手填名字）。")
            return
        if not self.store.save(cfg):
            self._set_status(f"保存失败：{self.store.last_error}")
            return
        self.result_config = cfg
        self.accept()


def show_settings(store: llm_config.LLMStore, transport=None) -> LLMSettingsDialog:
    """打开（或复用）设置页。模块级引用防止窗口被 GC 掉。"""
    for dialog in list(OPEN):
        if dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return dialog
    OPEN.clear()
    dialog = LLMSettingsDialog(store, transport=transport)
    OPEN.append(dialog)

    def _forget(_result: int) -> None:
        if dialog in OPEN:
            OPEN.remove(dialog)

    dialog.finished.connect(_forget)
    dialog.show()
    return dialog
