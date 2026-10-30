"""LLM 控制行动的测试（P2 / L5）。

四层各钉几条：

1. **白名单**：只认 investigate / talk / tremble / idle，别的丢；
2. **两种协议的返回体**：OpenAI 的 ``tool_calls`` 与 Anthropic 的 ``tool_use`` 都能抽出来，
   不支持函数调用时甩一段 JSON 也能认（§3.4）；
3. **意图队列**：一次一条、后到覆盖先到、两条之间至少 ``action_cooldown_s``；
4. **落地闸门**：仪式中 / 拖拽中 / 说话中一律拒绝；接受的动作真的改变行为；
   ``allow_actions=false`` 时一条都不取。
"""

from __future__ import annotations

import json

import pytest

from iterator_pet.llm import actions as llm_actions
from iterator_pet.llm import client as llm_client
from iterator_pet.llm import config as llm_config
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

SECRET = "sk-1234567890abcdefghijklmn"


class FakeTransport:
    def __init__(self, payload=None):
        self.payload = payload
        self.calls: list[tuple] = []

    def post_json(self, url, payload, headers, timeout=3.0):
        self.calls.append((url, payload, headers, timeout))
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


# ------------------------------------------------------------ 工具描述 --
def test_tool_specs_match_the_two_protocols():
    openai = llm_actions.tool_specs("openai")
    assert [t["function"]["name"] for t in openai] == ["speak", "act"]
    assert openai[1]["function"]["parameters"]["properties"]["action"]["enum"] == list(
        llm_actions.WHITELIST
    )

    anthropic = llm_actions.tool_specs("anthropic")
    assert [t["name"] for t in anthropic] == ["speak", "act"]
    assert "input_schema" in anthropic[1]


def test_the_tool_guide_explains_what_each_action_means():
    for action in llm_actions.WHITELIST:
        assert action in llm_actions.TOOL_GUIDE
    assert "说话用 speak" in llm_actions.TOOL_GUIDE


# ------------------------------------------------------------ 返回体解析 --
def test_parse_reply_reads_openai_tool_calls():
    speak = {"choices": [{"message": {"content": "",
                                       "tool_calls": [{"function": {
                                           "name": "speak",
                                           "arguments": json.dumps({"text": "我在这里。"}),
                                       }}]}}]}
    parsed = llm_actions.parse_reply(speak, "openai")
    assert parsed.text == "我在这里。"

    act = {"choices": [{"message": {"content": "",
                                    "tool_calls": [{"function": {
                                        "name": "act",
                                        "arguments": json.dumps({"action": "talk",
                                                                 "reason": "他停下了"}),
                                    }}]}}]}
    parsed = llm_actions.parse_reply(act, "openai")
    assert parsed.action == "talk" and parsed.reason == "他停下了"


def test_parse_reply_reads_anthropic_blocks():
    payload = {"content": [
        {"type": "text", "text": "嗯。"},
        {"type": "tool_use", "name": "act", "input": {"action": "tremble"}},
    ]}
    parsed = llm_actions.parse_reply(payload, "anthropic")
    assert parsed.text == "嗯。" and parsed.action == "tremble"


def test_parse_reply_drops_an_action_outside_the_whitelist():
    payload = {"choices": [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "act", "arguments": json.dumps({"action": "fly"})}}
    ]}}]}
    assert llm_actions.parse_reply(payload, "openai").action == ""


def test_json_fallback_only_reads_the_two_fields():
    parsed = llm_actions.parse_json_fallback('{"action": "investigate", "text": "看这边。"}')
    assert parsed.action == "investigate" and parsed.text == "看这边。"

    # 不是 JSON 就只当台词
    assert llm_actions.parse_json_fallback("滚开。").text == "滚开。"
    assert llm_actions.parse_json_fallback("滚开。").action == ""

    # 动作不在白名单 → 丢动作，正文留着
    parsed = llm_actions.parse_json_fallback('{"action": "fly", "text": "我飞。"}')
    assert parsed.action == "" and parsed.text == "我飞。"


# ------------------------------------------------------------ 意图队列 --
def test_intent_queue_whitelists_and_cools_down():
    clock = [100.0]
    queue = llm_actions.IntentQueue(clock=lambda: clock[0])
    assert queue.push("fly") is False
    assert queue.push("talk", min_interval_s=2.0) is True
    assert queue.push("investigate", min_interval_s=2.0) is False, "2 s 内不许第二条"
    assert queue.state() == {"pushed": 1, "dropped": 2}

    clock[0] += 2.5
    assert queue.push("investigate", min_interval_s=2.0) is True


def test_intent_queue_keeps_only_the_latest():
    clock = [0.0]
    queue = llm_actions.IntentQueue(clock=lambda: clock[0])
    queue.push("talk", min_interval_s=0.0)
    clock[0] += 1.0
    queue.push("idle", min_interval_s=0.0)     # 覆盖掉还没消费的 talk
    assert queue.pop().action == "idle"
    assert queue.pop() is None


# ------------------------------------------------------------- 客户端 --
def test_ask_attaches_tools_only_when_actions_are_on(tmp_path):
    reply = {"choices": [{"message": {"content": "", "tool_calls": [
        {"function": {"name": "act", "arguments": json.dumps({"action": "idle"})}}
    ]}}]}
    transport = FakeTransport(reply)
    client = llm_client.LLMClient(_store(tmp_path, allow_actions=True), transport=transport,
                                  limiter=llm_client.RateLimiter(min_interval_s=0.0))

    result = client.ask([{"role": "user", "content": "hi"}])
    payload = transport.calls[0][1]
    assert [t["function"]["name"] for t in payload["tools"]] == ["speak", "act"]
    assert payload["tool_choice"] == "auto"
    assert result.ok and result.action == "idle"

    client.ask([{"role": "user", "content": "hi"}], allow_actions=False)
    assert "tools" not in transport.calls[1][1]


def test_anthropic_payload_hoists_the_system_prompt(tmp_path):
    transport = FakeTransport({"content": [{"type": "text", "text": "在。"}]})
    store = _store(tmp_path, protocol="anthropic", base_url="https://api.anthropic.com")
    client = llm_client.LLMClient(store, transport=transport)
    client.ask([
        {"role": "system", "content": "你是她。"},
        {"role": "user", "content": "在吗"},
    ])
    payload = transport.calls[0][1]
    assert payload["system"] == "你是她。", "Anthropic 的 system 是顶层参数"
    assert all(m["role"] != "system" for m in payload["messages"])


def test_a_json_only_reply_becomes_an_action(tmp_path):
    transport = FakeTransport({"choices": [{"message": {
        "content": '{"action": "investigate", "text": "看这边。"}'
    }}]})
    client = llm_client.LLMClient(_store(tmp_path, allow_actions=True), transport=transport)
    result = client.ask([{"role": "user", "content": "hi"}])
    assert result.ok and result.action == "investigate" and result.text == "看这边。"


# ------------------------------------------------------------- 选择器 --
def test_force_segment_keeps_the_action_alive():
    from iterator_pet.simulation.behavior import BehaviourSelector

    selector = BehaviourSelector(seed=1)
    body = (400.0, 300.0)
    cursor = (420.0, 300.0)
    assert selector.force_segment("investigate", 5) is True
    # 光标速度 0（没动）：抽签的 gate 会关掉 investigate，但强切段不看这个
    for _ in range(5):
        assert selector.choose(body, cursor, 0.0) == "investigate"
    assert selector.forced == 0


def test_force_segment_refuses_an_unknown_behaviour():
    from iterator_pet.simulation.behavior import BehaviourSelector

    selector = BehaviourSelector(seed=1)
    assert selector.force_segment("fly", 10) is False


# --------------------------------------------------------------- 落地 --
class StubRouter:
    enabled = True

    def __init__(self, intents, allow_actions=True):
        self._intents = list(intents)
        self.allow_actions = allow_actions
        self.taken = 0

    def take_intent(self):
        self.taken += 1
        return self._intents.pop(0) if self._intents else None

    def poll(self):
        return None

    def state(self):
        return {"enabled": True, "allow_actions": self.allow_actions}


def _sim() -> SimulationState:
    return SimulationState((0.0, 0.0, 776.0, 415.0), character="pebbles", warmup=0)


def test_an_intent_can_switch_the_behaviour():
    sim = _sim()
    sim.cursor = (sim.creature.chunk0[0] + 20, sim.creature.chunk0[1])
    sim.llm = StubRouter([llm_actions.Intent("investigate")])

    assert sim._apply_intents() is False
    assert sim.selector.behaviour == "investigate"
    assert sim.intent_accepted == 1 and sim.intent_last == "investigate"


def test_an_intent_can_start_the_ritual():
    sim = _sim()
    sim.llm = StubRouter([llm_actions.Intent("tremble")])

    assert sim._apply_intents() is True, "起了仪式就要告诉调用方 return"
    assert sim.kill.active is True
    assert sim.intent_accepted == 1


@pytest.mark.parametrize("blocker", ["dragging", "talking", "ritual"])
def test_intents_are_refused_while_busy(blocker):
    sim = _sim()
    if blocker == "dragging":
        sim.drag_mode = "body"
        sim.drag_point = (sim.creature.chunk0[0] + 10, sim.creature.chunk0[1])
    elif blocker == "talking":
        sim.dialogue.start("在。")
    else:
        sim.kill.begin((100.0, 100.0), sim.ticks, False)
    sim.llm = StubRouter([llm_actions.Intent("talk")])

    assert sim._apply_intents() is False
    assert sim.intent_accepted == 0 and sim.intent_rejected == 1
    assert sim.intent_last.startswith("talk: ")


def test_a_forced_action_is_not_taken_when_actions_are_off():
    sim = _sim()
    stub = StubRouter([llm_actions.Intent("talk")], allow_actions=False)
    # 路由器自己的 take_intent 会清空队列并返回 None（真实实现的行为）
    stub.take_intent = lambda: None
    sim.llm = stub
    assert sim._apply_intents() is False
    assert sim.intent_accepted == 0
