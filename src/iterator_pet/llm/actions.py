"""LLM 控制行动（P1 之后的一步）：白名单动作、工具 schema、意图队列。

四个动作，**不多给一个**（用户 2026-10-30：开了行动就都能触发，没开就全拒）：

|动作|仿真里是什么|闸门（不满足就只说话）|
|-|-|-|
|``investigate``|把行为切成 investigate，姿态朝光标|光标在腔内、距离 ≤ 520、光标在动|
|``talk``|走近光标并定向（"在光标旁边悬停"）|同上，且选择器的 60 s 冷却过了|
|``tremble``|起仪式（挣脱 / 白光）|**不看 temper**（用户拍板）；仪式中/拖拽中/说话中拒绝|
|``idle``|回到 roaming|无|

三条硬约束：

* **白名单**：只认这四个字符串，别的直接丢；
* **一次一条**：`IntentQueue` 一次只留一个，`SimulationState` 每 tick 最多消费一条；
* **防连发**：两条意图之间至少 ``action_cooldown_s``（默认 2 s），只防手抖，不是安全机制。
"""

from __future__ import annotations

import json
import queue
import time
from dataclasses import dataclass

WHITELIST = ("investigate", "talk", "tremble", "idle")
# 动作维持多久（tick，40 TPS）。investigate/talk 用现有段长；idle 用 roaming 段长。
# 动作在队列里最多活 5 s：闸门关着时等它开，但不能过很久突然执行（仪式/拖拽可能持续很久）
INTENT_TTL_S = 5.0

ACTION_TICKS = {"investigate": 240, "talk": 900, "idle": 450}
BEHAVIOUR_OF = {"investigate": "investigate", "talk": "talk", "idle": "roaming"}

# 给模型看的说明（§3.3）。**必须写清"动作到底意味着什么"**，否则模型会以为
# talk 是"说话"，于是每句话都挂一个动作。
TOOL_GUIDE = (
    "【动作的含义】act 会真的改变你的姿态，所以只有在你确实想做点什么时才调用：\n"
    "- investigate：跟随光标移动一段时间。\n"
    "- talk：走到玩家光标旁边、悬停在那里看着光标（不是\"说话\"；说话用 speak）\n"
    "- tremble：起身、发光、散出电光，用于表达你对用户的强烈不满。\n"
    "- idle：什么也不做，回到平时的漫游\n"
    "玩家只是路过、你只想回一句时，用 speak，不要附带 act。"
)

_TOOL_SPEAK = {
    "name": "speak",
    "description": "说一句话（唯一可以直接产生台词的方式）",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 60}},
        "required": ["text"],
    },
}
_TOOL_ACT = {
    "name": "act",
    "description": "做一个动作。只有确实符合情境时才调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"enum": list(WHITELIST)},
            "reason": {"type": "string"},
        },
        "required": ["action"],
    },
}


def tool_specs(protocol: str = "openai") -> list[dict]:
    """两种协议的工具描述（结构不同，字段含义一样）。"""
    if protocol == "anthropic":
        return [
            {"name": "speak", "description": _TOOL_SPEAK["description"],
             "input_schema": _TOOL_SPEAK["parameters"]},
            {"name": "act", "description": _TOOL_ACT["description"],
             "input_schema": _TOOL_ACT["parameters"]},
        ]
    return [
        {"type": "function", "function": _TOOL_SPEAK},
        {"type": "function", "function": _TOOL_ACT},
    ]


@dataclass(frozen=True)
class ParsedReply:
    text: str = ""
    action: str = ""
    reason: str = ""


def _clean_action(value) -> str:
    name = str(value or "").strip().lower()
    return name if name in WHITELIST else ""


def _as_args(raw) -> dict:
    """OpenAI 的 ``arguments`` 是**字符串**，Anthropic 的 ``input`` 已经是对象。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def parse_reply(payload, protocol: str = "openai") -> ParsedReply:
    """从返回体里抽出 ``(文本, 动作, 理由)``。取不到文本不算错，交给上层判断。

    两种协议 + 两种调用形态（``speak`` / ``act``）都在这里收口，别处不要再解析返回体。
    """
    text = ""
    action = ""
    reason = ""
    if not isinstance(payload, dict):
        return ParsedReply()

    if protocol == "anthropic":
        blocks = payload.get("content")
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                text += block["text"]
            elif kind == "tool_use":
                args = _as_args(block.get("input"))
                if block.get("name") == "speak" and isinstance(args.get("text"), str):
                    text = text or args["text"]
                elif block.get("name") == "act":
                    action = action or _clean_action(args.get("action"))
                    reason = reason or str(args.get("reason") or "")
        return ParsedReply(text=text.strip(), action=action, reason=reason)

    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
            for call in message.get("tool_calls") or ():
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                name = function.get("name")
                args = _as_args(function.get("arguments"))
                if name == "speak" and isinstance(args.get("text"), str):
                    text = text or args["text"]
                elif name == "act":
                    action = action or _clean_action(args.get("action"))
                    reason = reason or str(args.get("reason") or "")
        if not text and isinstance(choices[0].get("text"), str):
            text = choices[0]["text"]
    return ParsedReply(text=text.strip(), action=action, reason=reason)


def parse_json_fallback(text: str) -> ParsedReply:
    """不支持 function calling 的模型（§3.4）：只认 ``{"action":…,"text":…}``。

    严格到"只读这两个字段"，解析不出来就**只保留文本**（当普通台词）。
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedReply()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return ParsedReply(text=raw)
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return ParsedReply(text=raw)
    if not isinstance(data, dict):
        return ParsedReply(text=raw)
    action = _clean_action(data.get("action"))
    spoken = data.get("text")
    if not isinstance(spoken, str):
        spoken = ""
    if not action and not spoken:
        return ParsedReply(text=raw)
    return ParsedReply(text=spoken.strip(), action=action,
                       reason=str(data.get("reason") or ""))


@dataclass(frozen=True)
class Intent:
    action: str
    reason: str = ""
    at: float = 0.0            # 入队时刻（monotonic），用来判过期


class IntentQueue:
    """动作队列：一次只留一个，消费就没了。

    **故意做成"后到的覆盖先到的"**：模型在 2 秒里说两次"investigate"没有意义，
    留最后一个才是它最新的意图。
    """

    def __init__(self, clock=time.monotonic, ttl_s: float = INTENT_TTL_S):
        self._clock = clock
        self._slot: queue.Queue = queue.Queue(maxsize=1)
        self.last_action_at = -1e9
        self.ttl_s = max(0.5, float(ttl_s))
        self.pushed = 0
        self.dropped = 0
        self.expired = 0

    def push(self, action: str, reason: str = "", min_interval_s: float = 2.0) -> bool:
        name = _clean_action(action)
        if not name:
            self.dropped += 1
            return False
        now = self._clock()
        if now - self.last_action_at < max(0.0, float(min_interval_s)):
            self.dropped += 1
            return False
        self.last_action_at = now
        while True:                       # 覆盖掉还没被消费的那条
            try:
                self._slot.get_nowait()
            except queue.Empty:
                break
        try:
            self._slot.put_nowait(Intent(name, reason, now))
        except queue.Full:                # pragma: no cover - maxsize=1 且已清空
            self.dropped += 1
            return False
        self.pushed += 1
        return True

    def pop(self) -> Intent | None:
        """取一条；**过期的直接丢**（否则闸门一直不开时会迟到执行）。"""
        while True:
            item = self._take()
            if item is None:
                return None
            if self._too_old(item):
                self.expired += 1
                continue
            return item

    def peek(self) -> Intent | None:
        """看一眼**不消费**。

        为什么需要它：闸门（仪式中/拖拽中/正在说话）关着的时候不能把动作扔掉——
        用户实测过"回复进了队列，桌宠却没动"：回执带台词 → 气泡占着 → 拒一次就没了。
        正确做法是留在队里等下一 tick，过期（``ttl_s``）才丢。
        """
        item = self._take()
        if item is None:
            return None
        if self._too_old(item):
            self.expired += 1
            return None
        try:
            self._slot.put_nowait(item)      # 原样放回去（单线程：poll 在主线程）
        except queue.Full:                   # pragma: no cover - maxsize=1 且刚取出
            pass
        return item

    def _take(self) -> Intent | None:
        try:
            return self._slot.get_nowait()
        except queue.Empty:
            return None

    def _too_old(self, item: Intent) -> bool:
        return (self._clock() - float(getattr(item, "at", 0.0))) > self.ttl_s

    def clear(self) -> None:
        while self.pop() is not None:
            pass

    def state(self) -> dict:
        return {"pushed": self.pushed, "dropped": self.dropped,
                "expired": self.expired, "ttl_s": self.ttl_s}
