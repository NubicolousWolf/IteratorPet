"""``LLMClient`` 的测试：组请求、摘话、熔断、限流，以及最硬的一条——**关掉时零请求**。

全部离线（假 transport + 假时钟），不联网、不起窗口。

对应 ``docs/PLAN-llm.md`` L2 验收：假 transport 覆盖超时/429/空/超长/换行；
"关掉 LLM 与 v0.1.7 一致"在这里落成一条可执行的断言：
``enabled=False`` 时 transport 的调用列表必须是空的。
"""

from __future__ import annotations

import json

import pytest

from iterator_pet.llm import client as llm_client
from iterator_pet.llm import config as llm_config
from iterator_pet.llm import models as llm_models

SECRET = "sk-1234567890abcdefghijklmn"


class FakeTransport:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple] = []

    def post_json(self, url, payload, headers, timeout=3.0):
        self.calls.append((url, payload, headers, timeout))
        if self.error:
            raise self.error
        return self.payload

    def get_json(self, url, headers, timeout=3.0):        # pragma: no cover - 不该被调用
        raise AssertionError("聊天不该走 GET")


def _store(tmp_path, **overrides) -> llm_config.LLMStore:
    store = llm_config.LLMStore(path=tmp_path / "llm.json")
    store.install_default_file()
    cfg = store.load()
    cfg.api_key = SECRET
    cfg.model = "deepseek-chat"
    for name, value in overrides.items():
        setattr(cfg, name, value)
    store.config = cfg
    return store


def _reply(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ------------------------------------------------------------------ 摘话 --
def test_clean_line_removes_what_a_bubble_cannot_show():
    assert llm_client.clean_line("我在这里。\n怎么了？") == "我在这里。 怎么了？"
    assert llm_client.clean_line("五块卵石：滚开。") == "滚开。"
    assert llm_client.clean_line("“滚开。”") == "滚开。"
    assert llm_client.clean_line("*叹了口气*") == "叹了口气"
    assert llm_client.clean_line("（叹气）你还在。") == "你还在。"
    assert llm_client.clean_line("   \n\t ") == ""


def test_clean_line_clamps_a_runaway_reply():
    text = llm_client.clean_line("啊" * 500)
    assert len(text) == llm_client.MAX_LINE_CHARS
    assert text.endswith("…")


def test_clean_line_returns_empty_when_only_stage_direction():
    assert llm_client.clean_line("（沉默）") == ""
    assert llm_client.clean_line('""') == ""


# ------------------------------------------------------------- 返回体 --
def test_parse_completion_knows_both_protocols():
    assert llm_client.parse_completion(_reply("hi"), "openai") == "hi"
    assert llm_client.parse_completion({"choices": [{"text": "hi"}]}, "openai") == "hi"
    anthropic = {"content": [{"type": "text", "text": "hi"}]}
    assert llm_client.parse_completion(anthropic, "anthropic") == "hi"


def test_parse_completion_rejects_a_body_without_text():
    with pytest.raises(ValueError):
        llm_client.parse_completion({"choices": []}, "openai")
    with pytest.raises(ValueError):
        llm_client.parse_completion({"content": []}, "anthropic")


def test_build_messages_keeps_system_history_then_the_new_line():
    messages = llm_client.build_messages(
        "你是她", [("user", "在吗"), ("assistant", "在"), ("tool", "忽略我")], "今天几号"
    )
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "今天几号"


# --------------------------------------------------- 关掉 = 零请求（A1） --
def test_disabled_client_never_touches_the_network(tmp_path):
    store = _store(tmp_path)
    transport = FakeTransport(_reply("不该出现"))
    client = llm_client.LLMClient(store, transport=transport)

    store.config.api_key = ""
    assert not client.enabled
    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and "没有 API Key" in result.reason

    store.config.api_key = SECRET
    store.config.model = ""
    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and "还没选模型" in result.reason

    store.config.model = "deepseek-chat"
    store.config.base_url = "ftp://nope"
    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and "Base URL" in result.reason

    assert transport.calls == [], "关掉的时候一次请求都不许发"
    assert client.calls == 0


# --------------------------------------------------------------- 正常态 --
def test_ask_sends_the_configured_sampling_and_strips_the_reply(tmp_path):
    store = _store(tmp_path, temperature=1.1, max_tokens=120, timeout_s=8.0)
    transport = FakeTransport(_reply("“我在这里。”\n"))
    client = llm_client.LLMClient(store, transport=transport)

    result = client.ask([{"role": "user", "content": "在吗"}])
    assert result.ok and result.text == "我在这里。"
    assert result.status == 200 and result.latency_s > 0

    url, payload, headers, timeout = transport.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert payload["temperature"] == 1.1 and payload["max_tokens"] == 120
    assert payload["stream"] is False
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert timeout == 8.0


def test_ask_uses_the_anthropic_endpoint_and_header(tmp_path):
    store = _store(tmp_path, protocol="anthropic", base_url="https://api.anthropic.com")
    transport = FakeTransport({"content": [{"type": "text", "text": "在。"}]})
    client = llm_client.LLMClient(store, transport=transport)

    assert client.ask([{"role": "user", "content": "在吗"}]).text == "在。"
    url, _payload, headers, _timeout = transport.calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == SECRET


# --------------------------------------------------------------- 失败态 --
def test_a_401_counts_as_a_failure_and_never_raises(tmp_path):
    store = _store(tmp_path)
    transport = FakeTransport(error=llm_models.TransportError("HTTP 401：invalid key", 401))
    client = llm_client.LLMClient(store, transport=transport)

    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and "HTTP 401" in result.reason and result.status == 401
    assert client.breaker.failures == 1


def test_a_timeout_is_reported_in_words(tmp_path):
    store = _store(tmp_path)
    transport = FakeTransport(error=llm_models.TransportError("超时（8 s 内没有响应）"))
    client = llm_client.LLMClient(store, transport=transport)
    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and "超时" in result.reason


def test_an_empty_or_unusable_reply_is_a_failure(tmp_path):
    store = _store(tmp_path)
    client = llm_client.LLMClient(store, transport=FakeTransport({"choices": []}))
    assert not client.ask([{"role": "user", "content": "hi"}]).ok

    client = llm_client.LLMClient(store, transport=FakeTransport(_reply("（沉默）")))
    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and "空" in result.reason


def test_a_429_body_is_reported_as_http_429(tmp_path):
    store = _store(tmp_path)
    transport = FakeTransport(error=llm_models.TransportError("HTTP 429：rate limited", 429))
    client = llm_client.LLMClient(store, transport=transport)
    result = client.ask([{"role": "user", "content": "hi"}])
    assert not result.ok and result.status == 429 and "429" in result.reason


# --------------------------------------------------------------- 熔断 --
def test_three_failures_open_the_breaker_and_stop_the_traffic(tmp_path):
    clock = Clock()
    store = _store(tmp_path)
    transport = FakeTransport(error=llm_models.TransportError("网络错误：断网"))
    breaker = llm_client.CircuitBreaker(threshold=3, cooldown_s=60.0, clock=clock)
    client = llm_client.LLMClient(
        store, transport=transport, breaker=breaker,
        limiter=llm_client.RateLimiter(min_interval_s=0.0, clock=clock),
    )

    for _ in range(3):
        clock.advance(1.0)
        assert not client.ask([{"role": "user", "content": "hi"}]).ok
    assert breaker.open

    clock.advance(1.0)
    result = client.ask([{"role": "user", "content": "hi"}])
    assert "冷却中" in result.reason
    assert len(transport.calls) == 3, "熔断期间不许再发"

    clock.advance(61.0)                     # 冷却过去 → 允许再试一次
    clock.advance(1.0)
    assert breaker.allow()
    client.ask([{"role": "user", "content": "hi"}])
    assert len(transport.calls) == 4


def test_one_success_resets_the_breaker(tmp_path):
    clock = Clock()
    store = _store(tmp_path)
    breaker = llm_client.CircuitBreaker(threshold=2, cooldown_s=60.0, clock=clock)
    client = llm_client.LLMClient(
        store, transport=FakeTransport(_reply("在。")), breaker=breaker,
        limiter=llm_client.RateLimiter(min_interval_s=0.0, clock=clock),
    )
    breaker.record(False)
    clock.advance(1.0)
    assert client.ask([{"role": "user", "content": "hi"}]).ok
    assert breaker.failures == 0 and not breaker.open


# --------------------------------------------------------------- 限流 --
def test_two_calls_in_the_same_second_are_rate_limited(tmp_path):
    clock = Clock()
    store = _store(tmp_path)
    transport = FakeTransport(_reply("在。"))
    client = llm_client.LLMClient(
        store, transport=transport,
        limiter=llm_client.RateLimiter(min_interval_s=1.0, clock=clock),
    )
    assert client.ask([{"role": "user", "content": "hi"}]).ok
    second = client.ask([{"role": "user", "content": "hi"}])
    assert not second.ok and "太快" in second.reason
    assert len(transport.calls) == 1

    clock.advance(1.5)
    assert client.ask([{"role": "user", "content": "hi"}]).ok
    assert len(transport.calls) == 2


# --------------------------------------------------------------- 面板 --
def test_state_reports_health_without_leaking_the_key(tmp_path):
    store = _store(tmp_path)
    client = llm_client.LLMClient(store, transport=FakeTransport(_reply("在。")))
    client.ask([{"role": "user", "content": "hi"}])
    state = client.state()
    assert state["enabled"] is True and state["calls"] == 1 and state["last_ok"] is True
    assert state["model"] == "deepseek-chat"
    assert SECRET not in json.dumps(state, ensure_ascii=False)
