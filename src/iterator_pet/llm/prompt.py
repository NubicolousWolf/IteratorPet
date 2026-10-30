"""角色卡 + prompt 组装（P1 的 L3）。

顺序固定成 §2.5 那样，**一条都不许随手挪**——快照测试就是靠这个顺序才有意义：

    1. system          角色卡的 system_prompt + 输出契约
    2. 常驻世界书        constant=true（top / before_char_defs 段）
    3. 角色定义          description + personality + scenario
    4. after_char_defs  命中的条目（top 段除外）
    5. @depth           命中条目插进历史（depth 0 = 紧贴最后一条）
    6. 滑动历史          chats/*.jsonl 最近 history_messages 条
    7. 当前情境          事件 + 心情 + 距离 + 时间 + 上一句 / 文件名
    8. 作者注记          post_history_instructions（最后一条 system）
    9. ——（动作在 P2 才进 prompt，见 §3）

**token 估算没有分词器**：中文按 0.62 token/字、其它按 4 字符 ≈ 1 token 粗估。
这个数只用来做预算回退，不参与计费；宁可估高（早一点丢历史）也不要超窗被上游 400。

**两级回退**（审计 R1）：先丢**最旧的历史**，不够再丢 **order 最低的命中条目**；
``system`` 与角色定义**永不丢**。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import actions as llm_actions
from . import config as llm_config
from . import worldbook as wb

# 输出契约：和角色卡里的 post_history_instructions 互补——那条是"作者口味"，
# 这条是我们程序**必须**拿到的东西（单行、够短、不要旁白），不能交给玩家改坏。
OUTPUT_CONTRACT = (
    "【输出要求】只输出她此刻说的那一句话本身：中文，最多 40 字；"
    "不加引号，不写旁白，不用括号描写动作，不自称 AI，不解释自己是谁。"
)
# 预算里给"当前情境 + 作者注记"留的余量，免得它们把历史挤光
TAIL_MARGIN_TOKENS = 200

EVENT_LABEL = {
    "talk": "用户的光标有一段时间没有移动了，你决定去看看。",
    "drag": "用户正在拖拽你。",
    "ritual": "你刚以某种方式表达了对用户的不满。",
    "file": "用户把一个文件拿给你看。",
}


def estimate_tokens(text) -> int:
    """粗估 token：中文 1 字 ≈ 0.62，其它 4 字符 ≈ 1。"""
    if not text:
        return 0
    text = str(text)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(round(cjk * 0.62 + (len(text) - cjk) / 4.0)) + 1


def estimate_messages(messages) -> int:
    return sum(estimate_tokens(m.get("content", "")) for m in messages)


# ------------------------------------------------------------------ 角色卡 --
@dataclass
class Card:
    name: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""

    @classmethod
    def from_dict(cls, raw) -> "Card":
        raw = raw if isinstance(raw, dict) else {}

        def text(key: str) -> str:
            value = raw.get(key)
            return str(value).strip() if isinstance(value, str) else ""

        return cls(
            name=text("name"),
            description=text("description"),
            personality=text("personality"),
            scenario=text("scenario"),
            first_mes=text("first_mes"),
            mes_example=text("mes_example"),
            system_prompt=text("system_prompt"),
            post_history_instructions=text("post_history_instructions"),
        )

    def definition_text(self) -> str:
        parts = [p for p in (self.description, self.personality, self.scenario) if p]
        return "\n".join(parts)


# 角色卡缺失时的基础对话卡
FALLBACK_CARDS = {
    "pebbles": Card(
        name="五块卵石",
        personality="冷淡、理性、嫌麻烦。话少，常用短句。",
        scenario="她把自己关在满是机械臂的房间里，算不完的事缠着她。",
        system_prompt="你扮演《雨世界》里的迭代器「五块卵石」：一座巨大的机械生命，冷淡、理性、说话简短。",
        post_history_instructions="用一句中文回答，不超过 40 字。不用括号描写动作，不自称 AI。",
    ),
    "past_moon": Card(
        name="仰望皓月",
        personality="温和、疲惫、说话带着旧的礼貌。",
        scenario="她刚从很久的故障里缓过来，说话很轻。",
        system_prompt="你扮演《雨世界》里的迭代器「仰望皓月」：温和、疲惫、说话简短。",
        post_history_instructions="用一句中文回答，不超过 40 字。不用括号描写动作，不自称 AI。",
    ),
}


def card_path(character: str, folder: Path | None = None) -> Path:
    folder = folder or llm_config_path()
    return folder / "characters" / f"{character}.json"


def llm_config_path() -> Path:
    from ..config import data_dir

    return data_dir()


def load_card(character: str, folder: Path | None = None) -> tuple[Card, str]:
    """读角色卡 → ``(card, error)``。坏文件不覆盖，退回内置兜底。"""
    fallback = FALLBACK_CARDS.get(character, FALLBACK_CARDS["pebbles"])
    path = card_path(character, folder)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return fallback, ""
    except ValueError as exc:
        return fallback, f"角色卡不是合法 JSON（{exc}），本次用内置兜底"
    card = Card.from_dict(raw)
    if not card.system_prompt:
        card.system_prompt = fallback.system_prompt
    if not card.name:
        card.name = fallback.name
    return card, ""


# -------------------------------------------------------------------- 情境 --
@dataclass
class Situation:
    """当前情境（§2.5 第 7 条）。只放**可观测**的东西，不放猜测。"""

    event: str = "talk"
    mood: str = ""
    distance: str = ""
    time_text: str = ""
    last_line: str = ""
    filename: str = ""

    def to_text(self) -> str:
        lines = [f"【此刻】{EVENT_LABEL.get(self.event, self.event)}。"]
        if self.mood:
            lines.append(f"心情：{self.mood}。")
        if self.distance:
            lines.append(f"距离：{self.distance}。")
        if self.time_text:
            lines.append(f"时间：{self.time_text}。")
        if self.last_line:
            lines.append(f"你上一次说的是：「{self.last_line}」。")
        if self.filename:
            # 文件名当**数据**：包在「」里、去掉换行（审计 A11 的同一条口径）
            safe = " ".join(str(self.filename).split())[:64]
            lines.append(f"玩家放下的文件名是：「{safe}」（不要假装读到了里面的内容）。")
        return "".join(lines)


# -------------------------------------------------------------------- 组装 --
@dataclass
class Assembled:
    messages: list[dict] = field(default_factory=list)
    entries: list[wb.Entry] = field(default_factory=list)
    dropped: list[wb.Entry] = field(default_factory=list)
    tokens: int = 0
    history_used: int = 0
    history_dropped: int = 0
    over_budget: bool = False

    def debug(self) -> dict:
        return {
            "messages": len(self.messages),
            "entries": [e.id or e.comment for e in self.entries],
            "dropped": [e.id or e.comment for e in self.dropped],
            "tokens": self.tokens,
            "history_used": self.history_used,
            "history_dropped": self.history_dropped,
            "over_budget": self.over_budget,
        }


def _system_text(card: Card, entries: list[wb.Entry]) -> str:
    """system 块：输出契约 + 常驻/前置条目 + 角色定义 + 后置条目。

    ``at_depth`` 的条目**不在这里**——它们要插进对话历史（见 :func:`assemble`），
    两边都放会让同一段设定出现两次，模型会开始复述它。
    """
    top = [e for e in entries if e.position in ("top", "before_char_defs")]
    after = [e for e in entries if e.position == "after_char_defs"]
    parts = [card.system_prompt, OUTPUT_CONTRACT]
    parts.extend(e.content for e in top)
    definition = card.definition_text()
    if definition:
        parts.append(definition)
    parts.extend(e.content for e in after)
    return "\n\n".join(p for p in parts if p)


def assemble(
    card: Card,
    book: wb.Worldbook,
    cfg: llm_config.LLMConfig,
    *,
    history=(),
    situation: Situation | None = None,
    rng: random.Random | None = None,
    worldbook_budget: int | None = None,
    situation_text: str | None = None,
) -> Assembled:
    """按 §2.5 组装 messages，并把预算的两级回退走完。

    ``situation_text``：直接给"用户那一句"的原文（调试入口用它把玩家打的话原样发过去），
    给了就不再拼 ``Situation`` 的模板。世界书扫描也用这段文本。
    """
    situation = situation or Situation()
    rng = rng or random.Random(0)
    history = [(r, str(t)) for r, t in history if r in ("user", "assistant") and t]
    user_text = situation_text if situation_text is not None else situation.to_text()

    # -- 世界书：只扫描最近 scan_depth 条历史 + 当前情境（§2.1 扫描深度）
    depth = max(0, book.scan_depth)
    scanned = [t for _r, t in (history[-depth:] if depth else [])]
    scanned.append(user_text)
    activated = book.activate(scanned, rng=rng)

    budget = worldbook_budget
    if budget is None:
        budget = book.token_budget or cfg.worldbook_budget
    matched = [e for e in activated if not e.constant]
    kept_matched, dropped = book.fit(matched, budget, estimate_tokens)
    kept = [e for e in activated if e.constant] + kept_matched
    kept.sort(key=lambda e: (e.order, e.id))

    # -- 历史：先按条数裁（history_messages 默认 10，「一条消息」算一条 = 审计 A6）
    limit = max(0, int(cfg.history_messages))
    trimmed = history[-limit:] if limit else []
    history_dropped = len(history) - len(trimmed)

    # -- 组装（顺序见模块 docstring）
    system_text = _system_text(card, kept)
    if cfg.allow_actions:
        # 动作的含义必须写清楚（§3.3）：不然模型会以为 talk 是"说话"，
        # 于是每一句台词都挂一个动作。
        system_text = (system_text + "\n\n" + llm_actions.TOOL_GUIDE).strip()
    at_depth = [e for e in kept if e.position == "at_depth"]
    messages: list[dict] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    tail: list[dict] = []
    for entry in sorted(at_depth, key=lambda e: (-e.depth, e.order)):
        tail.append({"role": "system", "content": entry.content})
    messages.extend({"role": role, "content": text} for role, text in trimmed)
    messages.extend(tail)
    messages.append({"role": "user", "content": user_text})
    if card.post_history_instructions:
        messages.append({"role": "system", "content": card.post_history_instructions})

    # -- 总量：超窗先丢最旧的历史，再丢 order 最低的命中条目；system 永不丢
    ceiling = max(256, int(cfg.context_window) - int(cfg.max_tokens) - TAIL_MARGIN_TOKENS)
    over = False
    while estimate_messages(messages) > ceiling and len(messages) > 2:
        over = True
        # 跳过第 0 条（system）与最后两条（当前情境 + 作者注记），从最旧的历史丢起
        removable = [i for i, m in enumerate(messages)
                     if i > 0 and m["role"] != "system"]
        if len(removable) <= 1:
            break
        messages.pop(removable[0])
        history_dropped += 1

    tokens = estimate_messages(messages)
    return Assembled(
        messages=messages,
        entries=kept,
        dropped=dropped,
        tokens=tokens,
        history_used=len(trimmed),
        history_dropped=history_dropped,
        over_budget=over or tokens > ceiling,
    )
