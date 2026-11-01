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
from dataclasses import dataclass, field as dataclass_field

WHITELIST = ("investigate", "talk", "move_to", "hold", "look_at",
             "sleep", "wake", "tremble", "idle")
# 动作维持多久（tick，40 TPS）。investigate/talk 用现有段长；idle 用 roaming 段长。
# 动作在队列里最多活 5 s：闸门关着时等它开，但不能过很久突然执行（仪式/拖拽可能持续很久）
INTENT_TTL_S = 10.0

ACTION_TICKS = {"investigate": 240, "talk": 900, "move_to": 600, "idle": 450}
BEHAVIOUR_OF = {"investigate": "investigate", "talk": "talk",
                "move_to": "roaming", "idle": "roaming"}
# 带坐标/时长的动作（2026-11-01 第三组）。参数在 `_clean_args` 里统一夹取，
# 状态层不再重复校验——**一处收口**，免得 schema 说 0–1、代码却收 0–100。
COORD_ACTIONS = ("move_to", "look_at")
SECONDS_ACTIONS = ("hold",)
HOLD_MIN_S = 3
HOLD_MAX_S = 120

# 给模型看的说明（§3.3）。**必须写清"动作到底意味着什么"**，否则模型会以为
# talk 是"说话"，于是每句话都挂一个动作。
TOOL_GUIDE = (
    "【动作的含义】act 会真的改变你在桌面腔室里的位置与姿态，只有在你确实想做动作时才调用：\n"
    "- investigate：跟随用户的光标移动一段时间。\n"
    "- talk：走到用户光标旁边、悬停在那里看着光标（不是「说话」；说话用 speak）。\n"
    "- move_to：移动到屏幕腔室的指定位置，需提供归一化坐标 x, y（0.0~1.0，左上角为 0,0，"
    "中心为 0.5,0.5，右下角为 1,1）。\n"
    "- hold：在原地闭眼打坐、保持静止不动，需提供持续秒数 seconds（3~120）。\n"
    "- look_at：待在原地不走，只把视线转向指定坐标 x, y（0.0~1.0）。\n"
    "- sleep：在原地闭眼休眠、调暗光环（适合深夜或想休息时）。\n"
    "- wake：从休眠或打坐中醒来，恢复平时的漫游。\n"
    "- tremble：起身、发光、散出电光，用于表达你对用户的强烈不满。\n"
    "- idle：什么也不做，回到平时的漫游。\n"
    "如果只想回一句话、不需要改变姿态或位置，只调用 speak，不要附带 act。\n"
    "【例子】用户说「走到右上角去」→ act(action=\\\"move_to\\\", x=0.85, y=0.15)；"
    "「闭眼坐五秒」→ act(action=\\\"hold\\\", seconds=5)；"
    "「睡吧」→ act(action=\\\"sleep\\\")；「醒醒」→ act(action=\\\"wake\\\")；"
    "「看看左上角」→ act(action=\\\"look_at\\\", x=0.1, y=0.1)。"
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
    "description": "做一个动作（改变姿态、移动到某点或原地静止）。只有确实符合情境时才调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(WHITELIST),
                       "description": "要执行的动作名称。"},
            "x": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                  "description": "目标水平位置（0.0=最左，0.5=居中，1.0=最右）。"
                                 "仅 move_to / look_at 需要。"},
            "y": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                  "description": "目标垂直位置（0.0=最上，0.5=居中，1.0=最下）。"
                                 "仅 move_to / look_at 需要。"},
            "seconds": {"type": "integer", "minimum": HOLD_MIN_S, "maximum": HOLD_MAX_S,
                        "description": "原地静止不动（闭眼打坐）的持续秒数（3~120）。"
                                       "仅 hold 需要。"},
            "reason": {"type": "string", "description": "简短说明为什么做这个动作。"},
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
    # 第三组起：动作的坐标/时长（`move_to` 的 x/y、`hold` 的 seconds）。
    # 已由 `_clean_args` 夹好，状态层直接信它，别再各夹一次。
    args: dict = dataclass_field(default_factory=dict)


#: 模型自己发明动作名的同义词表（2026-11-01 实测："闭眼" 被叫成 close_eyes /
#: meditate / rest）。**只做同义映射，不扩白名单**：映射后仍然要过 WHITELIST。
ACTION_ALIASES = {
    "meditate": "hold", "close_eyes": "hold", "close_eye": "hold",
    "eyes_closed": "hold", "rest": "hold", "sit": "hold", "hold_still": "hold",
    "move": "move_to", "goto": "move_to", "go_to": "move_to",
    "walk_to": "move_to", "walk": "move_to",
    "look": "look_at", "watch": "look_at", "look_towards": "look_at",
    "wake_up": "wake", "sleep_mode": "sleep", "nap": "sleep",
}


def _clean_action(value) -> str:
    name = str(value or "").strip().lower()
    name = ACTION_ALIASES.get(name, name)
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


def _clean_args(action: str, raw) -> dict:
    """把模型给的参数收成"仿真能直接用"的形状（第三组）。

    只有 ``COORD_ACTIONS`` 收 ``x/y``、``SECONDS_ACTIONS`` 收 ``seconds``；
    其余字段一律丢。坐标夹到 0–1（归一化，与分辨率/腔室大小无关），秒数夹到
    3–120。**非法值不报错、直接夹**：模型偶尔会把 `x` 写成 100 或字符串，
    这时候"不动"比"抛异常"好——上面已经有闸门，不差这一个。
    """
    data = _as_args(raw)
    cleaned: dict = {}
    if action in COORD_ACTIONS:
        for key in ("x", "y"):
            try:
                value = float(data.get(key))
            except (TypeError, ValueError):
                continue
            cleaned[key] = max(0.0, min(1.0, value))
    if action in SECONDS_ACTIONS:
        try:
            seconds = int(round(float(data.get("seconds"))))
        except (TypeError, ValueError):
            seconds = None
        if seconds is not None:
            cleaned["seconds"] = max(HOLD_MIN_S, min(HOLD_MAX_S, seconds))
    return cleaned


def parse_reply(payload, protocol: str = "openai") -> ParsedReply:
    """从返回体里抽出 ``(文本, 动作, 理由, 参数)``。取不到文本不算错，交给上层判断。

    两种协议 + 两种调用形态（``speak`` / ``act``）都在这里收口，别处不要再解析返回体。
    """
    text = ""
    action = ""
    reason = ""
    action_args: dict = {}
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
                tool = str(block.get("name") or "")
                if tool == "speak" and isinstance(args.get("text"), str):
                    text = text or args["text"]
                elif tool == "act" or tool in WHITELIST:
                    # 两种形态都认（2026-11-01 用户实测）：模型有时不套 act，
                    # 直接把动作名当工具调用（`sleep()` / `hold(seconds=8)`）。
                    # 只认 act 时这类回复 action/text 都为空 → 玩家看到"空回复"。
                    name = (_clean_action(args.get("action")) if tool == "act"
                            else _clean_action(tool))
                    if name and not action:
                        action = name
                        action_args = _clean_args(name, args)
                    reason = reason or str(args.get("reason") or "")
        return ParsedReply(text=text.strip(), action=action, reason=reason,
                           args=action_args)

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
                tool = str(function.get("name") or "")
                args = _as_args(function.get("arguments"))
                if tool == "speak" and isinstance(args.get("text"), str):
                    text = text or args["text"]
                elif tool == "act" or tool in WHITELIST:
                    # 同 Anthropic 分支：`act(action="sleep")` 与直接 `sleep()` 都认。
                    name = (_clean_action(args.get("action")) if tool == "act"
                            else _clean_action(tool))
                    if name and not action:
                        action = name
                        action_args = _clean_args(name, args)
                    reason = reason or str(args.get("reason") or "")
        if not text and isinstance(choices[0].get("text"), str):
            text = choices[0]["text"]
    return ParsedReply(text=text.strip(), action=action, reason=reason,
                       args=action_args)


def parse_json_fallback(text: str) -> ParsedReply:
    """不支持 function calling 的模型（§3.4）：只认 ``{"action":…,"text":…}``。

    严格到"只读这几个字段"，解析不出来就**只保留文本**（当普通台词）。
    第三组起同时认 ``x`` / ``y`` / ``seconds``，夹取规则与 ``_clean_args`` 同一份。

    **容忍代码围栏与前后废话**（2026-11-01 实测）：弱模型会把 JSON 包在
    ```json ... ``` 里，或者前面先写一句"好的，我这就去"，原实现只认
    "整段以 { 开头"，于是模型明明给了动作却被当成普通台词，玩家看到的是
    "加入意图队列"时有时无。现在先剥围栏，再在整段里找第一个 ``{...}``。
    """
    raw = (text or "").strip()
    if not raw:
        return ParsedReply()
    body = raw
    if "```" in body:                     # ```json\n{...}\n``` → {...}
        body = body.replace("```json", "```").replace("```JSON", "```")
        parts = body.split("```")
        body = max(parts, key=len).strip() if len(parts) > 1 else body
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return ParsedReply(text=raw)
    try:
        data = json.loads(body[start:end + 1])
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
    # 只有 JSON 里的动作：台词留空，但 `clean_line` 会把原文当台词——所以这里
    # 明确把"没给 text"的 JSON 结果与"普通台词"区分开（上层按 action 判空）。
    return ParsedReply(text=spoken.strip(), action=action,
                       reason=str(data.get("reason") or ""),
                       args=_clean_args(action, data) if action else {})


@dataclass(frozen=True)
class Intent:
    action: str
    reason: str = ""
    at: float = 0.0            # 入队时刻（monotonic），用来判过期
    args: dict = dataclass_field(default_factory=dict)   # move_to 的 x/y、hold 的 seconds


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

    def push(self, action: str, reason: str = "", min_interval_s: float = 2.0,
             args: dict | None = None) -> bool:
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
            self._slot.put_nowait(Intent(name, reason, now, dict(args or {})))
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
