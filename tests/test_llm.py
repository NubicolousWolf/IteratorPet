"""LLM 第一层（``llm.json`` + ``/models`` + 设置页）的测试。

对应 ``docs/PLAN-llm.md`` 的 L1 验收：

* **假 transport 注入 401 / 超时 / 非法 JSON** —— 三种都必须退化成"手填模型名"，
  并且状态栏写出原因（审计 A10）。这里注入的是真的 :class:`UrllibTransport`
  底下的 ``urlopen``，所以测的是我们自己的错误翻译，不是假货的复读。
* ``llm.json`` 的三种补文件情形、非空坏 JSON 不覆盖、未知键保留、越界夹取（A7/审计）。
* ``api_key`` 只经 ``redacted()`` 出现在任何可打印的地方（A9）。

设置页那部分离屏跑（和 ``test_editor.py`` 同一套写法），不弹真窗口。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.llm import config as llm_config  # noqa: E402
from iterator_pet.llm import models as llm_models  # noqa: E402
from iterator_pet.platform.llm_settings import LLMSettingsDialog, format_context  # noqa: E402

APP = QApplication.instance() or QApplication([])
SECRET = "sk-1234567890abcdefghijklmn"


# ------------------------------------------------------------------ 工具 --
class FakeTransport:
    """测试替身：要成功就给 payload，要失败就给一个 :class:`TransportError`。"""

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple] = []

    def get_json(self, url, headers, timeout=3.0):
        self.calls.append(("GET", url, headers))
        if self.error:
            raise self.error
        return self.payload

    def post_json(self, url, payload, headers, timeout=3.0):
        self.calls.append(("POST", url, payload))
        if self.error:
            raise self.error
        return self.payload if self.payload is not None else {}


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _store(tmp_path) -> llm_config.LLMStore:
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.install_default_file()
    store.load()
    return store


def _wait(dialog: LLMSettingsDialog, timeout: float = 3.0) -> None:
    """把设置页的 50 ms 轮询手动跑起来（离屏测试里没有事件循环在转）。"""
    deadline = time.time() + timeout
    while dialog._jobs and time.time() < deadline:
        dialog._poll_jobs()
        time.sleep(0.01)
    assert not dialog._jobs, "后台任务没有在超时内结束"


# ---------------------------------------------------------- 返回体解析 --
def test_parse_models_accepts_the_three_shapes():
    by_id = llm_models.parse_models(
        {"data": [{"id": "deepseek-chat", "context_window": 65536}]}
    )
    assert [m.id for m in by_id] == ["deepseek-chat"]
    assert by_id[0].context_window == 65536

    by_model = llm_models.parse_models({"data": [{"model": "qwen-max"}]})
    assert [m.id for m in by_model] == ["qwen-max"]

    bare = llm_models.parse_models(["m1", {"name": "m2"}])
    assert [m.id for m in bare] == ["m1", "m2"]


def test_parse_models_refuses_a_body_it_does_not_understand():
    with pytest.raises(ValueError):
        llm_models.parse_models({"object": "list"})
    with pytest.raises(ValueError):
        llm_models.parse_models({"data": [{"nope": 1}]})


# ------------------------------------------------------- 真 transport 错 --
def test_urllib_transport_turns_401_into_a_status(monkeypatch):
    def boom(*_args, **_kwargs):
        raise urllib.error.HTTPError("http://x/models", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(llm_models.urllib.request, "urlopen", boom)
    with pytest.raises(llm_models.TransportError) as info:
        llm_models.UrllibTransport().get_json("http://x/models", {})
    assert info.value.status == 401
    assert "HTTP 401" in str(info.value)


def test_urllib_transport_turns_a_timeout_into_words(monkeypatch):
    def boom(*_args, **_kwargs):
        raise socket.timeout("timed out")

    monkeypatch.setattr(llm_models.urllib.request, "urlopen", boom)
    with pytest.raises(llm_models.TransportError) as info:
        llm_models.UrllibTransport().get_json("http://x/models", {})
    assert "超时" in str(info.value)


def test_urllib_transport_rejects_a_non_json_body(monkeypatch):
    monkeypatch.setattr(
        llm_models.urllib.request, "urlopen", lambda *_a, **_k: _Response(b"<html>502</html>")
    )
    with pytest.raises(llm_models.TransportError) as info:
        llm_models.UrllibTransport().get_json("http://x/models", {})
    assert "不是 JSON" in str(info.value)


def test_endpoints_match_the_two_protocols():
    assert llm_models.models_url("https://api.deepseek.com/", "openai") == "https://api.deepseek.com/models"
    assert llm_models.models_url("https://api.anthropic.com", "anthropic") == "https://api.anthropic.com/v1/models"
    assert llm_models.chat_url("https://api.deepseek.com", "openai") == "https://api.deepseek.com/chat/completions"
    assert llm_models.chat_url("https://api.anthropic.com", "anthropic") == "https://api.anthropic.com/v1/messages"


def test_run_fetch_needs_a_key_and_reports_success():
    assert not llm_models.run_fetch(FakeTransport({}), "https://x", "").ok

    fake = FakeTransport({"data": [{"id": "deepseek-chat"}]})
    result = llm_models.run_fetch(fake, "https://x", SECRET)
    assert result.ok and [m.id for m in result.models] == ["deepseek-chat"]
    assert fake.calls[0][2]["Authorization"] == f"Bearer {SECRET}"


# --------------------------------------------------------------- llm.json --
def test_install_default_file_covers_missing_empty_and_blank(tmp_path):
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    assert store.install_default_file() is True
    written = json.loads(store.path.read_text(encoding="utf-8"))
    assert written["history_messages"] == 10 and written["allow_actions"] is False

    store.path.write_text("", encoding="utf-8")
    assert store.install_default_file() is True, "0 字节也要补"
    store.path.write_text(" \n\t\n", encoding="utf-8")
    assert store.install_default_file() is True, "只有空白也要补"


def test_install_default_file_never_overwrites_a_broken_one(tmp_path):
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.path.write_text("{ 玩家还在改", encoding="utf-8")
    assert store.install_default_file() is False
    assert store.path.read_text(encoding="utf-8") == "{ 玩家还在改"


def test_load_keeps_defaults_and_reports_a_broken_file(tmp_path):
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.path.write_text("{ nope", encoding="utf-8")
    cfg = store.load()
    assert cfg.model == "" and cfg.history_messages == 10
    assert "不是合法 JSON" in store.last_error
    assert not store.loaded_from_file


def test_default_model_is_empty_on_purpose(tmp_path):
    """用户 2026-10-30 拍板：默认不填 deepseek-chat，免得玩家忘改就发错模型。"""
    store = _store(tmp_path)
    assert store.config.model == ""
    assert json.loads(store.path.read_text(encoding="utf-8"))["model"] == ""
    # 没选模型时一次请求都不该发出去
    fake = FakeTransport({})
    result = llm_models.run_probe(fake, "https://x", SECRET, "")
    assert not result.ok and "先选一个模型" in result.reason
    assert fake.calls == []


def test_save_preserves_unknown_keys_and_clamps_out_of_range(tmp_path):
    store = _store(tmp_path)
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["我的扩展"] = {"keep": True}
    store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg = store.load()
    cfg.history_messages = 999            # 越界 → 夹到 50
    cfg.temperature = 5.0                 # 越界 → 夹到 2.0
    assert store.save(cfg) is True

    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["我的扩展"] == {"keep": True}, "未知键不能丢"
    assert saved["history_messages"] == 50
    assert saved["temperature"] == 2.0


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = _store(tmp_path)
    assert store.save(store.config) is True
    assert [p.name for p in tmp_path.iterdir()] == ["llm.json"]


# ----------------------------------------------------------- 密钥脱敏 --
def test_redacted_never_shows_the_key():
    cfg = llm_config.LLMConfig(api_key=SECRET)
    shown = llm_config.redacted(cfg)
    assert SECRET not in json.dumps(shown, ensure_ascii=False)
    assert shown["api_key"] == SECRET[:4] + "***"


def test_state_answers_has_key_but_never_the_key(tmp_path):
    store = _store(tmp_path)
    store.config.api_key = SECRET
    state = store.state()
    assert state["has_key"] is True
    assert SECRET not in json.dumps(state, ensure_ascii=False)


def test_environment_key_wins_over_the_file(monkeypatch):
    cfg = llm_config.LLMConfig(api_key="from-file")
    monkeypatch.setenv(llm_config.KEY_ENV, "from-env")
    assert llm_config.resolve_key(cfg) == "from-env"
    monkeypatch.delenv(llm_config.KEY_ENV)
    assert llm_config.resolve_key(cfg) == "from-file"
    # api_key_env 空串 → 退回默认环境变量名，不会"读不到任何环境变量"
    assert llm_config.resolve_key(
        llm_config.LLMConfig(api_key="from-file", api_key_env="")
    ) == "from-file"


# ------------------------------------------------------------- 后台任务 --
def test_background_job_returns_its_result_exactly_once():
    job = llm_models.BackgroundJob(lambda: "done").start()
    deadline = time.time() + 3.0
    while job.poll() is None:
        assert time.time() < deadline
        time.sleep(0.01)
    assert job.poll() is None, "结果只交一次，第二次要看到 None"


# --------------------------------------------------------------- 设置页 --
@pytest.mark.parametrize(
    "error,expected",
    [
        (llm_models.TransportError("HTTP 401：invalid key", 401), "HTTP 401"),
        (llm_models.TransportError("超时（3 s 内没有响应）"), "超时"),
        (llm_models.TransportError("返回不是 JSON"), "不是 JSON"),
    ],
)
def test_settings_degrades_to_typing_when_fetch_fails(tmp_path, error, expected):
    dialog = LLMSettingsDialog(_store(tmp_path), transport=FakeTransport(error=error))
    dialog.api_key.setText(SECRET)
    dialog.fetch_models()
    _wait(dialog)

    assert dialog.model_box.isEditable(), "拉不到列表必须能手填（审计 A10）"
    text = dialog.status.text()
    assert expected in text and "手填" in text
    assert SECRET not in text
    dialog.close()


def test_settings_fills_the_combo_and_the_context_label(tmp_path):
    transport = FakeTransport(
        {"data": [{"id": "deepseek-chat", "context_window": 65536, "max_output_tokens": 8192}]}
    )
    dialog = LLMSettingsDialog(_store(tmp_path), transport=transport)
    dialog.api_key.setText(SECRET)
    dialog.fetch_models()
    _wait(dialog)

    assert dialog.model_box.count() == 1
    assert dialog.context_label.text() == "上下文 64k · 输出上限 8k"
    dialog.close()


def test_settings_saves_everything_the_page_owns(tmp_path):
    store = _store(tmp_path)
    dialog = LLMSettingsDialog(store, transport=FakeTransport({"data": [{"id": "m1"}]}))
    dialog.provider_custom.setChecked(True)
    dialog.base_url.setText("https://relay.example.com/v1")
    dialog.api_key.setText(SECRET)
    dialog.protocol_anthropic.setChecked(True)
    dialog.model_box.setEditText("relay-model")
    dialog.history.setValue(4)
    dialog.allow_actions.setChecked(True)
    dialog._save()

    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved["provider"] == "custom"
    assert saved["base_url"] == "https://relay.example.com/v1"
    assert saved["protocol"] == "anthropic"
    assert saved["model"] == "relay-model"
    assert saved["history_messages"] == 4
    assert saved["allow_actions"] is True
    assert saved["api_key"] == SECRET
    dialog.close()


def test_settings_presets_deepseek_and_locks_the_url(tmp_path):
    dialog = LLMSettingsDialog(_store(tmp_path))
    dialog.provider_custom.setChecked(True)
    assert dialog.base_url.isReadOnly() is False
    dialog.provider_deepseek.setChecked(True)
    assert dialog.base_url.text() == "https://api.deepseek.com"
    assert dialog.base_url.isReadOnly() is True
    assert dialog.protocol_openai.isChecked() is True
    dialog.close()


def test_settings_refuses_an_empty_model_and_a_bad_url(tmp_path):
    store = _store(tmp_path)
    dialog = LLMSettingsDialog(store, transport=FakeTransport({"data": []}))
    dialog.provider_custom.setChecked(True)
    dialog.base_url.setText("api.example.com")
    dialog.model_box.setEditText("m")
    dialog._save()
    assert "http://" in dialog.status.text()
    assert dialog.result_config is None

    dialog.base_url.setText("https://api.example.com")
    dialog.model_box.setEditText("")
    dialog._save()
    assert "先选一个模型" in dialog.status.text()
    assert dialog.result_config is None
    dialog.close()


def test_format_context_is_readable():
    assert format_context(65536) == "64k"
    assert format_context(8192) == "8k"
    assert format_context(0) == "?"
