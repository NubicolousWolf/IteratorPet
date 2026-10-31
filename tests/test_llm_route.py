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
def test_chat_log_appends_skips_broken_lines_trims_and_clears(tmp_path):
    """`chats/<角色>.jsonl` 的四件事：追加/读回、坏行只丢一行、超限才重写、清空。

    合并理由：同一个类的四个侧面，分开只是让报告长四倍。
    """
    log = llm_history.ChatLog("pebbles", folder=tmp_path)
    assert log.append("user", "在吗")
    assert log.append("assistant", "在。")
    assert log.recent(10) == [("user", "在吗"), ("assistant", "在。")]
    assert log.path == tmp_path / "chats" / "pebbles.jsonl"

    # 坏行只丢那一行，别的照读；不认识的 role 也跳过
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text(
        json.dumps({"role": "user", "text": "好"}, ensure_ascii=False) + "\n"
        + "{ 断在半行\n"
        + json.dumps({"role": "nobody", "text": "忽略"}, ensure_ascii=False) + "\n"
        + json.dumps({"role": "assistant", "text": "嗯"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    texts = [line[1] for line in log.recent(10)]
    assert texts == ["好", "嗯"], f"坏行/陌生 role 要跳过，实际 {texts}"

    # 超过 limit + slack 才重写，重写后不会超过 limit
    trimming = llm_history.ChatLog("pebbles", folder=tmp_path / "t", limit=4)
    for i in range(4 + llm_history.TRIM_SLACK + 2):
        trimming.append("user", f"第{i}句")
    lines = trimming.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 4 + llm_history.TRIM_SLACK + 1, "超限后要原子重写到 limit 附近"

    # 缺文件读回空、clear 之后也空
    empty = llm_history.ChatLog("past_moon", folder=tmp_path / "e")
    assert empty.recent(5) == []
    empty.append("assistant", "嗯。")
    assert empty.clear() and empty.recent(5) == []
    assert empty.state()["path"].endswith("past_moon.jsonl")


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


# --------------------------------------------------- 动作进队列（P2 接线） --
def _act_reply(action: str, text: str = "") -> dict:
    return {"choices": [{"message": {"content": text, "tool_calls": [
        {"function": {"name": "act", "arguments": json.dumps({"action": action})}}
    ]}}]}


def test_router_pushes_an_action_into_the_intent_queue(tmp_path):
    transport = FakeTransport(_act_reply("talk", "过来。"))
    router = _router(tmp_path, transport=transport, allow_actions=True)
    assert router.request("talk") is True
    reply = router.poll()
    assert reply is not None and reply.action == "talk" and reply.text == "过来。"
    intent = router.take_intent()
    assert intent is not None and intent.action == "talk"
    assert router.take_intent() is None, "一次只给一条"


def test_router_refuses_to_hand_out_actions_when_the_switch_is_off(tmp_path):
    transport = FakeTransport(_act_reply("tremble"))
    router = _router(tmp_path, transport=transport, allow_actions=True)
    router.request("talk")
    router.poll()
    # 关掉"允许控制行动"后，队列里就算有也不给
    router.store.config.allow_actions = False
    assert router.take_intent() is None

    router.store.config.allow_actions = True
    router.store.config.enabled = False          # 总开关关掉同样一条都不给
    assert router.take_intent() is None


def test_the_action_also_reaches_the_simulation(tmp_path):
    """端到端（假 transport）：模型说 talk → 队列 → 仿真真的把行为切成 talk。"""
    transport = FakeTransport(_act_reply("talk"))
    router = _router(tmp_path, transport=transport, allow_actions=True)
    sim = _sim()
    sim.llm = router
    sim.cursor = (sim.creature.chunk0[0] + 30.0, sim.creature.chunk0[1])
    sim.selector.talk_cooldown = 0

    router.request("talk")
    router.poll()
    assert sim._apply_intents() is False
    assert sim.selector.behaviour == "talk"
    assert sim.intent_accepted == 1

def test_debug_ask_sends_the_typed_text_verbatim(tmp_path):
    """菜单里的"直接给她发一句话"：用户那一侧必须是玩家原文，不是情境模板。"""
    transport = FakeTransport({"choices": [{"message": {"content": "我在。"}}]})
    router = _router(tmp_path, transport=transport)
    assert router.ask_text("你在吗？") is True

    reply = router.poll()
    assert reply is not None and reply.event == "debug" and reply.text == "我在。"
    messages = transport.calls[0][1]["messages"]
    # §2.5：最后一条永远是作者注记（system），玩家那句话在它前面
    assert messages[-1]["role"] == "system"
    assert messages[-2]["role"] == "user"
    assert messages[-2]["content"] == "你在吗？", "原样发，不加情境包装"
    assert router.chat.recent(4)[-2] == ("user", "你在吗？"), "调试这句也要进历史"


def test_debug_ask_refuses_when_disabled_or_empty(tmp_path):
    store = _store(tmp_path)
    store.config.api_key = ""
    router = llm_route.LLMRouter(store, folder=tmp_path, transport=FakeTransport({}),
                                 job_factory=InlineJob)
    assert router.ask_text("在吗") is False, "关着就不发"
    assert router.ask_text("   ") is False, "空白也不发"

    router = _router(tmp_path)
    router._job_factory = lambda *a, **k: type("S", (), {"start": lambda s: s,
                                                         "poll": lambda s: None})()
    assert router.request("talk") is True
    assert router.ask_text("在吗") is False, "上一条还没回来就不许插队"

def test_the_reply_sequence_tells_new_replies_from_stale_ones(tmp_path):
    """调试入口的 bug 现场：上一次失败的回执会一直躺在 last_reply 里。

    光看 ``event == "debug"`` 分不出新旧，所以每条回执都带请求序号；
    调用方只认 ``last_reply_seq == seq``。
    """
    router = _router(tmp_path, transport=FakeTransport({"choices": [{"message": {"content": "在。"}}]}))
    assert router.seq == 0 and router.last_reply_seq == 0

    assert router.ask_text("第一次") is True
    first = router.seq
    assert first == 1
    assert router.last_reply_seq == 0, "还没回来，上一次的回执仍是 0"
    router.poll()
    assert router.last_reply_seq == first

    assert router.ask_text("第二次") is True
    second = router.seq
    assert second == first + 1
    assert router.last_reply_seq == first, "上一次的回执还挂着——这正是要防的"
    router.poll()
    assert router.last_reply_seq == second
