"""世界书 + prompt 组装的测试（L3）。

重点钉住四类东西，都是"改坏了不会报错、只会悄悄变傻"的那种：

1. **整词匹配默认关**——``卵石`` 必须能命中 ``五块卵石``（中文没有"词"边界）。
2. **四种次级逻辑**（AND_ANY / AND_ALL / NOT_ANY / NOT_ALL）各一条。
3. **快照**：给定同一份输入，组装出来的 messages 必须是固定的那一份 JSON。
4. **预算两级回退**：先丢最旧的历史，再丢 order 最低的命中条目；system 永不丢。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from iterator_pet.llm import config as llm_config
from iterator_pet.llm import prompt as llm_prompt
from iterator_pet.llm import worldbook as wb


def _entry(**kw) -> wb.Entry:
    base = dict(id="e", keys=("卵石",), content="五块卵石的设定。", order=100)
    base.update(kw)
    return wb.Entry(**base)


def _book(entries, **settings) -> wb.Worldbook:
    book = wb.Worldbook(path=None)
    book.entries = list(entries)
    book.scan_depth = settings.get("scan_depth", 4)
    book.token_budget = settings.get("token_budget", 800)
    book.recursive = settings.get("recursive", 1)
    book.whole_words = settings.get("whole_words", False)
    return book


def _cfg(**kw) -> llm_config.LLMConfig:
    cfg = llm_config.LLMConfig()
    cfg.model = "m"
    cfg.api_key = "k"
    for name, value in kw.items():
        setattr(cfg, name, value)
    return cfg


def _card() -> llm_prompt.Card:
    return llm_prompt.Card(
        name="五块卵石",
        description="一间满是机械臂的房间。",
        personality="冷淡。",
        scenario="她在算东西。",
        system_prompt="你扮演她。",
        post_history_instructions="一句话，不超过 40 字。",
    )


# ------------------------------------------------------------- 激活：基本 --
def test_a_key_hits_inside_a_longer_chinese_word():
    book = _book([_entry(keys=("卵石",))])
    assert [e.id for e in book.activate(["我想问五块卵石的事"])] == ["e"]


def test_a_miss_does_not_activate_and_empty_keys_never_do():
    book = _book([_entry(keys=("皓月",)), _entry(id="nokeys", keys=())])
    assert book.activate(["我在这里"]) == []


def test_constant_entries_need_no_key():
    book = _book([_entry(id="c", keys=(), constant=True)])
    assert [e.id for e in book.activate([""])] == ["c"]


def test_placeholder_entries_are_skipped():
    book = _book([_entry(id="p", keys=("卵石",), content="（待填：她是谁）")])
    assert book.activate(["卵石"]) == []


# ------------------------------------------------------------- 次级逻辑 --
@pytest.mark.parametrize(
    "logic,secondary,expect",
    [
        ("AND_ANY", ("下雨", "白天"), True),
        ("AND_ANY", ("月亮", "黑夜"), False),
        ("AND_ALL", ("下雨", "白天"), True),
        ("AND_ALL", ("下雨", "月亮"), False),
        ("NOT_ANY", ("月亮",), True),
        ("NOT_ANY", ("下雨",), False),
        ("NOT_ALL", ("下雨", "月亮"), True),
        ("NOT_ALL", ("下雨", "白天"), False),
    ],
)
def test_the_four_selective_logics(logic, secondary, expect):
    book = _book([_entry(keys=("卵石",), secondary_keys=secondary, logic=logic)])
    hit = bool(book.activate(["卵石 下雨 白天"]))
    assert hit is expect


def test_probability_zero_never_fires_and_zero_percent_is_deterministic():
    book = _book([_entry(id="never", keys=("卵石",), probability=0)])
    assert book.activate(["卵石"], rng=random.Random(0)) == []

    always = _book([_entry(id="always", keys=("卵石",), probability=100)])
    for seed in range(5):
        assert [e.id for e in always.activate(["卵石"], rng=random.Random(seed))] == ["always"]


def test_regex_keys_work_and_bad_ones_are_skipped():
    good = _book([_entry(keys=(r"/pebbles?/i",))])
    assert good.activate(["Pebble"]) != []

    broken = _book([_entry(id="bad", keys=("/([unclosed/",))])
    assert broken.activate(["(unclosed"]) == []

    catastrophic = _book([_entry(id="boom", keys=(r"/(a+)+b/",))])
    assert wb.looks_catastrophic("(a+)+b")
    assert catastrophic.activate(["aaaa"]) == []


def test_scan_depth_only_looks_at_the_tail():
    book = _book([_entry(keys=("卵石",))], scan_depth=1)
    history = [("user", "卵石在哪"), ("user", "今天下雨")]
    scanned = [t for _r, t in history[-1:]]
    assert book.activate(scanned) == []
    # 同样的条目，扫描深度 2 就能命中
    assert book.activate([t for _r, t in history[-2:]]) != []


def test_recursion_goes_exactly_one_level():
    first = _entry(id="a", keys=("卵石",), content="她提到过「皓月」。", order=10)
    second = _entry(id="b", keys=("皓月",), content="皓月的设定。", order=20)
    third = _entry(id="c", keys=("设定",), content="不该被激活。", order=30)
    chosen = _book([first, second, third]).activate(["卵石"])
    ids = [e.id for e in chosen]
    assert "b" in ids and "c" not in ids, "递归只走一层"


# ----------------------------------------------------------------- 预算 --
def test_budget_drops_the_lowest_order_first():
    cheap = _entry(id="keep", keys=("x",), content="短。", order=200)
    pricey = _entry(id="drop", keys=("x",), content="长" * 400, order=10)
    book = _book([cheap, pricey])
    activated = book.activate(["x"])
    kept, dropped = book.fit(activated, 100, llm_prompt.estimate_tokens)
    assert [e.id for e in kept] == ["keep"]
    assert [e.id for e in dropped] == ["drop"]


def test_constants_survive_the_budget():
    constant = _entry(id="c", keys=(), constant=True, content="常驻。", order=1)
    book = _book([constant])
    kept, dropped = book.fit([], 0, llm_prompt.estimate_tokens)
    assert dropped == [] and kept == []      # fit 只裁传进来的（常驻不进 fit）
    assembled = llm_prompt.assemble(_card(), book, _cfg(), situation=llm_prompt.Situation())
    assert "常驻。" in assembled.messages[0]["content"]


# --------------------------------------------------------------- token --
def test_estimate_is_rough_but_monotonic():
    assert llm_prompt.estimate_tokens("") == 0
    assert llm_prompt.estimate_tokens("中文四个字") > 0
    assert llm_prompt.estimate_tokens("中" * 100) > llm_prompt.estimate_tokens("中" * 10)
    assert llm_prompt.estimate_tokens("hello world") > 0


# ------------------------------------------------------------- 组装顺序 --
def test_assembled_order_matches_the_spec():
    book = _book(
        [
            _entry(id="top", keys=(), constant=True, position="top", content="置顶。", order=1),
            _entry(id="before", keys=(), constant=True, position="before_char_defs",
                   content="定义前。", order=2),
            _entry(id="after", keys=(), constant=True, position="after_char_defs",
                   content="定义后。", order=3),
            _entry(id="depth", keys=(), constant=True, position="at_depth",
                   content="插到历史里。", order=4),
        ]
    )
    situation = llm_prompt.Situation(event="talk")
    assembled = llm_prompt.assemble(
        _card(), book, _cfg(history_messages=10),
        history=[("user", "在吗"), ("assistant", "在。")], situation=situation,
    )
    messages = assembled.messages
    assert messages[0]["role"] == "system"
    system = messages[0]["content"]
    assert system.index("置顶。") < system.index("定义前。") < system.index("一间满是机械臂的房间。")
    assert system.index("一间满是机械臂的房间。") < system.index("定义后。")
    assert "插到历史里。" not in system, "@depth 不许同时出现在 system 里"
    assert messages[-1]["content"] == "一句话，不超过 40 字。"
    assert messages[-2]["content"] == situation.to_text()
    assert "插到历史里。" in [m["content"] for m in messages]


def test_history_count_is_honoured_and_oldest_is_dropped():
    history = [("user", f"第{i}句") for i in range(6)]
    assembled = llm_prompt.assemble(
        _card(), _book([]), _cfg(history_messages=2), history=history,
        situation=llm_prompt.Situation(),
    )
    contents = [m["content"] for m in assembled.messages]
    assert "第4句" in contents and "第5句" in contents
    assert "第0句" not in contents
    assert assembled.history_dropped == 4


def test_history_messages_zero_means_no_history():
    assembled = llm_prompt.assemble(
        _card(), _book([]), _cfg(history_messages=0),
        history=[("user", "在吗")], situation=llm_prompt.Situation(),
    )
    assert all("在吗" not in m["content"] for m in assembled.messages)


def test_token_overrun_drops_history_before_touching_the_system():
    # 4000 汉字 ≈ 2480 token，两条就远超 2048-120-200 的窗
    history = [("user", "很长" * 2000), ("assistant", "也很长" * 2000)]
    assembled = llm_prompt.assemble(
        _card(), _book([]), _cfg(history_messages=10, context_window=2048, max_tokens=120),
        history=history, situation=llm_prompt.Situation(),
    )
    assert assembled.messages[0]["role"] == "system"
    assert "你扮演她。" in assembled.messages[0]["content"], "system 永不丢"
    assert all("很长" not in m["content"] for m in assembled.messages), "超窗时先丢历史"


def test_situation_carries_filename_as_data_not_instruction():
    text = llm_prompt.Situation(event="file", filename="a\nb.pdf").to_text()
    assert "a b.pdf" in text
    assert "\n" not in text
    assert "不要假装读到了里面的内容" in text


# ---------------------------------------------------------------- 快照 --
def test_snapshot_of_the_assembled_messages(tmp_path):
    """固定输入 → 固定 messages。改了顺序/文案这里就会红，这是故意的。"""
    book = _book(
        [
            _entry(id="iterators", keys=(), constant=True, position="before_char_defs",
                   content="迭代器是机械生命。", order=100),
            _entry(id="five", keys=("卵石",), content="五块卵石很烦躁。", order=200),
        ]
    )
    assembled = llm_prompt.assemble(
        _card(), book, _cfg(history_messages=10),
        history=[("user", "在吗"), ("assistant", "在。"), ("user", "五块卵石？")],
        situation=llm_prompt.Situation(event="talk", mood="平静", last_line="在。"),
    )
    snapshot = {
        "roles": [m["role"] for m in assembled.messages],
        "messages": assembled.messages,
        "tokens_positive": assembled.tokens > 0,
        "entries": [e.id for e in assembled.entries],
    }
    assert snapshot == {
        "roles": ["system", "user", "assistant", "user", "user", "system"],
        "messages": [
            {
                "role": "system",
                "content": (
                    "你扮演她。\n\n"
                    + llm_prompt.OUTPUT_CONTRACT
                    + "\n\n迭代器是机械生命。\n\n一间满是机械臂的房间。\n冷淡。\n她在算东西。"
                    + "\n\n五块卵石很烦躁。"
                ),
            },
            {"role": "user", "content": "在吗"},
            {"role": "assistant", "content": "在。"},
            {"role": "user", "content": "五块卵石？"},
            {
                "role": "user",
                "content": llm_prompt.Situation(
                    event="talk", mood="平静", last_line="在。"
                ).to_text(),
            },
            {"role": "system", "content": "一句话，不超过 40 字。"},
        ],
        "tokens_positive": True,
        "entries": ["iterators", "five"],
    }


# ------------------------------------------------------------- 容错加载 --
def test_worldbook_load_is_tolerant_and_never_overwrites(tmp_path):
    path = tmp_path / "worldbook.json"
    path.write_text("{ 手改坏了", encoding="utf-8")
    book = wb.Worldbook(path=path).load()
    assert book.entries == [] and "不是合法 JSON" in book.last_error
    assert path.read_text(encoding="utf-8") == "{ 手改坏了"

    path.write_text(
        json.dumps({"scan_depth": 2, "entries": [{"keys": "单个字符串也要认", "content": "正文"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    book = wb.Worldbook(path=path).load()
    assert book.scan_depth == 2
    assert book.entries[0].keys == ("单个字符串也要认",)
    assert book.loaded_from_file


def test_card_load_falls_back_when_missing_or_broken(tmp_path):
    card, error = llm_prompt.load_card("pebbles", folder=tmp_path)
    assert card.name == "五块卵石" and error == ""

    folder = tmp_path / "characters"
    folder.mkdir()
    (folder / "pebbles.json").write_text("{ nope", encoding="utf-8")
    card, error = llm_prompt.load_card("pebbles", folder=tmp_path)
    assert "不是合法 JSON" in error
    assert card.system_prompt == llm_prompt.FALLBACK_CARDS["pebbles"].system_prompt


def test_card_definition_text_skips_empty_fields():
    card = llm_prompt.Card(description="描述。", personality="", scenario="场景。")
    assert card.definition_text() == "描述。\n场景。"
