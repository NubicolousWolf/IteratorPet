"""历史持久化 + 路由器接线（L4 及它的收口）。

三条要求：

1. ``chats/<角色>.jsonl`` 一行一条、坏行只丢那一行、超过上限才重写；
2. **关掉 LLM 时路由器一次请求都不发**，仿真照旧抽本地池（"关掉 = v0.1.7"）；
3. LLM 失败时退回本地台词——玩家不该因为断网就永远看不到气泡。

这里起了一个真的 ``SimulationState``（不起窗口），但只用 ``warmup=0``，
所以一条测试不到 10 ms。
"""

from __future__ import annotations

import json

import pytest

from iterator_pet.llm import client as llm_client
from iterator_pet.llm import config as llm_config
from iterator_pet.llm import history as llm_history
from iterator_pet.llm import models as llm_models
from iterator_pet.llm import route as llm_route

SECRET = "sk-1234567890abcdefghijklmn"


class InlineJob:
    """把后台任务变成同步：构造时就算完，第一次 ``poll()`` 交出结果。"""

    def __init__(self, fn, *args, **kwargs):
        self._value = fn(*args, **kwargs)

    def start(self):
        return self

    def poll(self):
        value, self._value = self._value, None
        return value


class FakeTransport:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple] = []

    def post_json(self, url, payload, headers, timeout=3.0):
        self.calls.append((url, payload))
        if self.error:
            raise self.error
        return self.payload


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


def _router(tmp_path, transport=None, **overrides) -> llm_route.LLMRouter:
    store = _store(tmp_path, **overrides)
    return llm_route.LLMRouter(
        store,
        character="pebbles",
        folder=tmp_path,
        transport=transport or FakeTransport({"choices": [{"message": {"content": "我在这里。"}}]}),
        job_factory=InlineJob,
    )


# ------------------------------------------------------------------ 历史 --
def test_chat_log_appends_and_reads_back_in_order(tmp_path):
    log = llm_history.ChatLog("pebbles", folder=tmp_path)
    assert log.append("user", "在吗")
    assert log.append("assistant", "在。")
    assert log.recent(10) == [("user", "在吗"), ("assistant", "在。")]
    assert log.path == tmp_path / "chats" / "pebbles.jsonl"


def test_chat_log_skips_broken_lines(tmp_path):
    log = llm_history.ChatLog("pebbles", folder=tmp_path)
    log.path.parent.mkdir(parents=True)
    log.path.write_text(
        json.dumps({"role": "user", "text": "好"}, ensure_ascii=False) + "\n"
        + "{ 断在半行\n"
        + json.dumps({"role": "nobody", "text": "忽略"}, ensure_ascii=False) + "\n"
        + json.dumps({"role": "assistant", "text": "嗯"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert log.recent(10) == [("user", "好"), ("assistant", "嗯")]


def test_chat_log_trims_to_the_limit(tmp_path):
    log = llm_history.ChatLog("pebbles", folder=tmp_path, limit=4)
    total = 4 + llm_history.TRIM_SLACK + 2
    for i in range(total):
        log.append("user", f"第{i}句")
    texts = [json.loads(line)["text"] for line in log.path.read_text(encoding="utf-8").splitlines()]
    # 超过 limit+slack 才重写；重写后还剩 4 条，再追加几条 → 最多 limit+1
    assert len(texts) <= 4 + 1
    assert texts[-1] == f"第{total - 1}句", "最新的那句永远在最后"
    assert f"第0句" not in texts, "旧的一定被丢掉了"


def test_chat_log_handles_missing_and_clear(tmp_path):
    log = llm_history.ChatLog("past_moon", folder=tmp_path)
    assert log.recent(5) == []
    log.append("assistant", "嗯。")
    assert log.clear() and log.recent(5) == []
    assert log.state()["path"].endswith("past_moon.jsonl")


# ------------------------------------------------------------- 路由器 --
def test_router_disabled_falls_back_without_a_request(tmp_path):
    store = _store(tmp_path)
    store.config.api_key = ""
    transport = FakeTransport({"choices": [{"message": {"content": "不该说话"}}]})
    router = llm_route.LLMRouter(store, folder=tmp_path, transport=transport,
                                 job_factory=InlineJob)

    assert router.enabled is False
    assert router.request("talk", last_line="在。") is False
    assert transport.calls == []
    assert not (tmp_path / "chats" / "pebbles.jsonl").exists(), "没发请求就不该写历史"


def test_router_asks_and_records_both_sides(tmp_path):
    router = _router(tmp_path)
    router.chat.append("user", "上一次的话")
    assert router.request("talk", last_line="在。") is True

    reply = router.poll()
    assert reply is not None and reply.source == "llm" and reply.text == "我在这里。"
    roles = [role for role, _text in router.chat.recent(10)]
    assert roles[-2:] == ["user", "assistant"], "情境与回复都要落盘"
    assert router.poll() is None, "一条回复只交一次"


def test_router_reports_a_failure_and_keeps_the_reason(tmp_path):
    transport = FakeTransport(error=llm_models.TransportError("HTTP 401：invalid key", 401))
    router = _router(tmp_path, transport=transport)
    assert router.request("drag") is True
    reply = router.poll()
    assert reply is not None and reply.text == "" and "HTTP 401" in reply.reason
    assert router.state()["last_reason"].startswith("HTTP 401")


def test_router_wont_stack_two_requests(tmp_path):
    class StuckJob:
        def __init__(self, _fn, *_args, **_kwargs):
            pass

        def start(self):
            return self

        def poll(self):
            return None

    router = _router(tmp_path)
    router._job_factory = StuckJob
    assert router.request("talk") is True
    assert router.request("talk") is False, "上一个还没回来就不许再发"


def test_router_uses_the_card_and_worldbook_from_disk(tmp_path):
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "pebbles.json").write_text(
        json.dumps({"name": "五块卵石", "system_prompt": "你是她。", "post_history_instructions": "一句话。"},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "worldbook.json").write_text(
        json.dumps({"entries": [{"keys": [], "constant": True, "content": "常驻设定。"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    transport = FakeTransport({"choices": [{"message": {"content": "嗯。"}}]})
    router = _router(tmp_path, transport=transport)
    assert router.state()["worldbook"]["entries"] == 1

    router.request("talk")
    router.poll()
    messages = transport.calls[0][1]["messages"]
    system = messages[0]["content"]
    assert "你是她。" in system and "常驻设定。" in system, "角色卡与世界书要真的进 prompt"
    assert messages[-1]["content"] == "一句话。", "作者注记永远最后一条"


# ------------------------------------------------------- 接进仿真（接线） --
def _sim(character="pebbles"):
    from iterator_pet.simulation.state import SimulationState

    return SimulationState((0.0, 0.0, 776.0, 415.0), character=character, warmup=0)


def test_simulation_without_a_router_behaves_like_v017():
    sim = _sim()
    assert sim.llm is None
    for _ in range(300):
        sim._step_dialogue()
    assert sim.status()["llm"]["enabled"] is False


class StubRouter:
    def __init__(self, reply=None, answer=True):
        self.enabled = True
        self.answer = answer
        self.reply = reply
        self.requests: list[str] = []

    def request(self, event, **_kwargs):
        self.requests.append(event)
        return self.answer

    def poll(self):
        reply, self.reply = self.reply, None
        return reply

    def state(self):
        return {"enabled": self.enabled}


def test_simulation_shows_the_llm_reply():
    sim = _sim()
    sim.llm = StubRouter(reply=llm_route.Reply(text="我在这里。", source="llm", event="talk"))
    sim._step_dialogue()
    assert sim.dialogue.text == "我在这里。"
    assert sim.last_spoken == "我在这里。"


def test_simulation_falls_back_to_the_pool_when_the_llm_fails():
    sim = _sim()
    sim.llm = StubRouter(
        reply=llm_route.Reply(text="", source="llm", event="talk", reason="HTTP 401")
    )
    sim._step_dialogue()
    assert sim.dialogue.text in sim.lines.pool("pebbles", "talk"), "失败要退回本地台词"


def test_simulation_prefers_the_pool_when_the_router_says_no():
    sim = _sim()
    stub = StubRouter(answer=False)
    sim.llm = stub
    for _ in range(200):
        sim._step_dialogue()
    # 路由说"不用 LLM"时，本地池照旧；这条只要求"没有因为路由而不说话"
    assert sim.dialogue_enabled is True
