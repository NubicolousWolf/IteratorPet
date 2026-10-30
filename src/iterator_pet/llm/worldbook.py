"""世界书的**子集**实现（酒馆那套的简化版），以及它的容错加载。

取哪些机制、为什么取，见 ``docs/PLAN-llm.md`` §2.1。这里只重申三条最容易被改坏的：

1. **整词匹配默认关**。酒馆文档自己写着"整词匹配对中日文有害"——开着它，
   ``卵石`` 就匹配不上 ``五块卵石``。这是中文桌宠，默认必须关。
2. **不做任何 UI，所以加载端必须容错**（审计 A7）：坏 JSON 用内置兜底且**不覆盖**
   玩家文件；缺字段补默认；``constant=false`` 且 ``keys`` 为空 → 跳过；正则编译失败只
   跳过那一条 key；全部失败只影响"要不要带世界书"，不影响说话。
3. **占位条目不许进 prompt**：模板里写着 ``（待填：…）`` 的条目在被玩家填好之前
   是噪音，直接跳过（否则模型会开始复述"待填"）。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

LOGICS = ("AND_ANY", "AND_ALL", "NOT_ANY", "NOT_ALL")
POSITIONS = ("before_char_defs", "after_char_defs", "at_depth", "top")
PLACEHOLDER = "（待填"

# 正则 key 的三道护栏（审计：正则 key 的 ReDoS）
MAX_PATTERN = 120
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*{]")


def looks_catastrophic(pattern: str) -> bool:
    """粗判"嵌套量词"（``(a+)+`` 这种）。宁可误杀一条 key，也不让界面卡住。"""
    return bool(_NESTED_QUANTIFIER.search(pattern))


@dataclass
class Entry:
    id: str = ""
    comment: str = ""
    keys: tuple[str, ...] = ()
    secondary_keys: tuple[str, ...] = ()
    logic: str = "AND_ANY"
    constant: bool = False
    position: str = "after_char_defs"
    depth: int = 0
    order: int = 100
    probability: int = 100
    content: str = ""

    @property
    def usable(self) -> bool:
        """能不能参与匹配：得有正文，且不是模板里的占位。"""
        text = (self.content or "").strip()
        return bool(text) and not text.startswith(PLACEHOLDER)


def _as_list(value) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_entries(raw) -> list[Entry]:
    """任意 list → ``Entry`` 列表（坏条目跳过，不抛异常）。"""
    entries: list[Entry] = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue
        logic = str(item.get("selective_logic") or item.get("logic") or "AND_ANY").upper()
        if logic not in LOGICS:
            logic = "AND_ANY"
        position = str(item.get("position") or "after_char_defs")
        if position not in POSITIONS:
            position = "after_char_defs"
        entries.append(
            Entry(
                id=str(item.get("id") or ""),
                comment=str(item.get("comment") or ""),
                keys=_as_list(item.get("keys")),
                secondary_keys=_as_list(item.get("secondary_keys")),
                logic=logic,
                constant=bool(item.get("constant")),
                position=position,
                depth=max(0, _as_int(item.get("depth"), 0)),
                order=_as_int(item.get("order"), 100),
                probability=min(100, max(0, _as_int(item.get("probability"), 100))),
                content=str(item.get("content") or ""),
            )
        )
    return entries


def _compile_pattern(key: str):
    """``/pattern/flags`` → 编译好的正则；不合法就 ``None``（跳过这一条 key）。"""
    end = key.rfind("/")
    if end <= 0:
        return None
    body, flags = key[1:end], key[end + 1:]
    if not body or len(body) > MAX_PATTERN or looks_catastrophic(body):
        return None
    try:
        return re.compile(body, re.IGNORECASE if "i" in flags else 0)
    except re.error:
        return None


@dataclass
class _Matcher:
    whole_words: bool = False
    _cache: dict = field(default_factory=dict)

    def hit(self, key: str, text: str) -> bool:
        key = (key or "").strip()
        if not key or not text:
            return False
        if key.startswith("/") and key.count("/") >= 2:
            pattern = self._cache.get(key, False)
            if pattern is False:
                pattern = self._cache[key] = _compile_pattern(key)
            return bool(pattern and pattern.search(text))
        haystack = text if self.whole_words else text.lower()
        needle = key if self.whole_words else key.lower()
        if not self.whole_words:
            return needle in haystack
        # 整词匹配只在英文/数字之间有边界；中日文没有"词"的概念，所以这个分支
        # 默认不会被打开（``match_whole_words=false``）。
        return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


class Worldbook:
    """``worldbook.json`` 的读写与激活。形状照 ``Lines``：有 ``path`` / ``last_error``。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else _default_path()
        self.entries: list[Entry] = []
        self.scan_depth = 4
        self.token_budget = 800
        self.recursive = 1
        self.whole_words = False
        self.last_error = ""
        self.loaded_from_file = False

    # -- 文件 ---------------------------------------------------------------
    def load(self) -> "Worldbook":
        self.last_error = ""
        self.loaded_from_file = False
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            self.entries = []
            return self
        if not text.strip():
            return self
        try:
            raw = json.loads(text)
        except ValueError as exc:
            # 不覆盖玩家的文件：只用内置空书，并在日志里说清原因（审计 A7）
            self.last_error = f"worldbook.json 不是合法 JSON（{exc}），本次不带世界书"
            self.entries = []
            return self
        if not isinstance(raw, dict):
            self.last_error = "worldbook.json 顶层不是对象，本次不带世界书"
            self.entries = []
            return self
        self.scan_depth = max(0, _as_int(raw.get("scan_depth"), 4))
        self.token_budget = max(0, _as_int(raw.get("token_budget"), 800))
        self.recursive = max(0, min(1, _as_int(raw.get("recursive"), 1)))  # 硬限 1 层
        self.whole_words = bool(raw.get("match_whole_words"))
        self.entries = parse_entries(raw.get("entries"))
        self.loaded_from_file = True
        return self

    # -- 激活 ---------------------------------------------------------------
    def activate(self, texts, rng: random.Random | None = None) -> list[Entry]:
        """``texts``：参与扫描的文本（旧→新）。返回按 ``order`` 升序的条目。"""
        rng = rng or random.Random(0)
        scan = " ".join(str(t) for t in texts if t)
        matcher = _Matcher(whole_words=self.whole_words)
        chosen: list[Entry] = []

        def consider(entry: Entry, text: str) -> bool:
            if not entry.usable:
                return False
            if entry.constant:
                return True
            if not entry.keys:
                return False                     # 没有 key 又不是常驻 → 永不激活
            if not any(matcher.hit(k, text) for k in entry.keys):
                return False
            if entry.secondary_keys:
                secondary = [matcher.hit(k, text) for k in entry.secondary_keys]
                if entry.logic == "AND_ALL" and not all(secondary):
                    return False
                if entry.logic == "NOT_ANY" and any(secondary):
                    return False
                if entry.logic == "NOT_ALL" and all(secondary):
                    return False
                # AND_ANY：至少一个 → 上面已经满足；一个都没命中就不过
                if entry.logic == "AND_ANY" and not any(secondary):
                    return False
            return True

        for entry in self.entries:
            if consider(entry, scan) and self._roll(entry, rng):
                chosen.append(entry)

        # 递归：命中条目的正文里出现别的 key，再扫一层（硬限 1 层，防爆）
        if self.recursive and chosen:
            extra_text = " ".join(e.content for e in chosen)
            picked = {id(e) for e in chosen}
            for entry in self.entries:
                if id(entry) in picked or entry.constant:
                    continue
                if consider(entry, extra_text) and self._roll(entry, rng):
                    chosen.append(entry)

        chosen.sort(key=lambda e: (e.order, e.id))
        return chosen

    @staticmethod
    def _roll(entry: Entry, rng: random.Random) -> bool:
        if entry.probability >= 100:
            return True
        if entry.probability <= 0:
            return False
        return rng.random() * 100 < entry.probability

    # -- 预算 ---------------------------------------------------------------
    def fit(self, entries: list[Entry], budget: int, cost) -> tuple[list[Entry], list[Entry]]:
        """按预算裁条目：**从 order 最低的丢起**（order 越大越靠后、影响越大）。

        ``cost`` 是 ``str -> int`` 的估算函数（由 ``prompt`` 层给，避免循环 import）。
        """
        keep: list[Entry] = []
        dropped: list[Entry] = []
        used = 0
        for entry in sorted(entries, key=lambda e: (-e.order, e.id)):
            price = cost(entry.content)
            if budget and used + price > budget:
                dropped.append(entry)
                continue
            keep.append(entry)
            used += price
        keep.sort(key=lambda e: (e.order, e.id))
        dropped.sort(key=lambda e: (e.order, e.id))
        return keep, dropped

    def state(self) -> dict:
        return {
            "path": str(self.path),
            "entries": len(self.entries),
            "enabled": sum(1 for e in self.entries if e.usable),
            "scan_depth": self.scan_depth,
            "token_budget": self.token_budget,
            "whole_words": self.whole_words,
            "loaded_from_file": self.loaded_from_file,
            "last_error": self.last_error,
        }


def _default_path() -> Path:
    from ..config import data_dir

    return data_dir() / "worldbook.json"
