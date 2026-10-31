"""对话池 / 对话状态 / 两个事件的纯数据测试（不碰 Qt，跑得飞快）。

对应 docs/PLAN-dialogue.md 的 M3/M4 验收：
* 四组默认台词必须逐字等于用户 2026-10-29 给的原话；
* 文件缺失自动重建、坏 JSON 保留旧表、热重载能读到玩家新加的条目；
* 哨兵「（不触发对话）」= 这次不说话；池长 > 1 时不连续重复；
* `drag` 在**开始**时触发、同一次拖拽一次、8 s 冷却；`talk` 进入 hablar 段触发、60 s 冷却；
* 仪式（挣脱/杀鼠标）期间两个事件都不触发。
"""

from __future__ import annotations

import json

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.dialogue import DialogueState
from iterator_pet.simulation.events import DialogueEvents
from iterator_pet.simulation.lines import Lines


def _lines(tmp_path, payload: dict | None = None, seed: int = 5) -> Lines:
    return Lines(path=tmp_path / "lines.json", payload=payload, seed=seed)


# --------------------------------------------------------------------------
# 默认台词：必须逐字等于用户给的原话
# --------------------------------------------------------------------------
def test_default_pools_are_exactly_what_the_user_asked_for():
    groups = C.DEFAULT_LINES["characters"]
    assert groups["pebbles"]["drag"] == [
        "滚开。", "别烦我。", "（不触发对话）", "你没有更重要的事可做吗？"
    ]
    assert groups["past_moon"]["drag"] == [
        "怎么了？", "请不要打扰我。", "（不触发对话）", "我在这里。怎么了？"
    ]
    assert groups["pebbles"]["talk"] == ["……"]
    assert groups["past_moon"]["talk"] == ["你还在这里吗？"]


def test_missing_file_is_created_with_defaults(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    assert path.is_file(), "第一次运行必须把默认池写到磁盘，玩家才能编辑"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["characters"]["past_moon"]["talk"] == ["你还在这里吗？"]
    assert lines.next_line("pebbles", "talk") == "……"


def test_broken_json_keeps_the_previous_table(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    assert lines.pool("pebbles", "drag")
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    assert lines.reload(force=True) is False
    assert lines.pool("pebbles", "drag") == [
        "滚开。", "别烦我。", "（不触发对话）", "你没有更重要的事可做吗？"
    ]


def test_player_can_add_lines_and_hot_reload_picks_them_up(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["characters"]["past_moon"]["talk"].append("我还在。")
    data["characters"]["past_moon"]["talk"].remove("你还在这里吗？")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert lines.reload(force=True) is True
    assert lines.next_line("past_moon", "talk") == "我还在。"


def test_silent_entry_and_empty_string_mean_no_dialogue(tmp_path):
    payload = {"characters": {"pebbles": {"drag": ["（不触发对话）", "   "]}}}
    lines = Lines(path=tmp_path / "lines.json", payload=payload)
    assert lines.next_line("pebbles", "drag") is None
    assert lines.next_line("pebbles", "drag") is None


def test_pool_does_not_repeat_the_same_line_twice_in_a_row(tmp_path):
    payload = {"characters": {"pebbles": {"drag": ["甲", "乙"]}}}
    lines = Lines(path=tmp_path / "lines.json", payload=payload, seed=1)
    picks = [lines.next_line("pebbles", "drag") for _ in range(40)]
    assert all(a != b for a, b in zip(picks, picks[1:])), "不能连续两次说同一句"


# --------------------------------------------------------------------------
# 对话状态：逐字 → 停留 → 收起
# --------------------------------------------------------------------------
def test_dialogue_types_holds_then_closes():
    line = "你还在这里吗？"
    d = DialogueState()
    assert d.start(line) is True
    assert d.active and d.visible_text() == "你"

    for _ in range(int(C.DIALOGUE_TYPE_TICKS_PER_CHAR) * (len(line) - 1)):
        d.step()
    assert d.typing_done(), f"应当打完了，实际 {d.visible_text()!r}"
    assert d.visible_text() == line

    hold = int(C.DIALOGUE_HOLD_BASE + C.DIALOGUE_HOLD_PER_CHAR * len(line))
    for _ in range(hold + 40):
        d.step()
    assert not d.active, "停留结束后应当彻底收起"
    assert d.text == ""


def test_cancel_hides_immediately():
    d = DialogueState()
    d.start("滚开。")
    d.step()
    d.cancel()
    assert not d.active and d.size_fac == 0.0 and d.text == ""


def test_start_with_none_or_empty_is_a_noop():
    d = DialogueState()
    assert d.start(None) is False
    assert d.start("") is False
    assert not d.active


# --------------------------------------------------------------------------
# 两个事件
# --------------------------------------------------------------------------
def test_drag_fires_once_per_drag_and_has_an_eight_second_cooldown():
    e = DialogueEvents()
    assert e.tick("roaming", False) is None
    assert e.tick("roaming", True) == "drag", "抓起的那一刻就该说"
    assert e.tick("roaming", True) is None, "同一次拖拽只触发一次"
    e.tick("roaming", False)                       # 松手
    assert e.tick("roaming", True) is None, "8 s 冷却内再抓不说"
    for _ in range(C.DIALOGUE_DRAG_COOLDOWN):
        e.tick("roaming", False)
    assert e.tick("roaming", True) == "drag"


def test_talk_fires_on_entering_talk_and_respects_the_sixty_second_cooldown():
    e = DialogueEvents()
    assert e.tick("roaming", False) is None
    assert e.tick("talk", False) == "talk", "进入 talk 段的第一 tick"
    assert e.tick("talk", False) is None, "同一段只说一次"
    e.tick("roaming", False)
    assert e.tick("talk", False) is None, "60 s 冷却里不再说"
    for _ in range(C.TALK_COOLDOWN_TICKS):
        e.tick("roaming", False)
    assert e.tick("talk", False) == "talk"


def test_ritual_suppresses_both_events():
    e = DialogueEvents()
    assert e.tick("roaming", True, ritual_active=True) is None
    assert e.tick("talk", True, ritual_active=True) is None


def test_reset_clears_cooldowns_so_a_new_character_can_speak():
    e = DialogueEvents()
    assert e.tick("roaming", True) == "drag"
    e.reset()
    e.tick("roaming", False)
    assert e.tick("roaming", True) == "drag", "换角色后不能继承旧冷却"


# --------------------------------------------------------------------------
# 接线：仿真层里的锚点与"事件 → 台词"
# --------------------------------------------------------------------------
def test_bubble_anchor_is_two_tiles_above_the_cord_socket():
    from iterator_pet.simulation.state import SimulationState

    for scale in (0.6, 1.5, 2.25):
        bounds = (0.0, 0.0, 1707.0 / scale, 912.0 / scale)
        sim = SimulationState(bounds, warmup=0)
        sim.dialogue.start("你还在这里吗？")
        sim.step(0.025)
        view = sim.dialogue_view()
        assert view is not None
        socket, tile = sim.cord.socket, sim.cord.tile
        assert abs(view["anchor"][0] - socket[0]) < 1e-9
        assert abs(view["anchor"][1] - (socket[1] - C.DIALOGUE_ANCHOR_TILES_UP * tile)) < 1e-9
        # 屏幕空间里应当落在下方（用户要的"屏幕正中下方"）
        assert view["anchor"][1] * scale > 0.85 * 912.0, (scale, view["anchor"])


def test_dragging_makes_the_pet_say_its_drag_line(tmp_path):
    from iterator_pet.simulation.state import SimulationState

    payload = {"characters": {"pebbles": {"drag": ["滚开。"], "talk": ["……"]}}}
    sim = SimulationState((0.0, 0.0, 1138.0, 608.0), warmup=0)
    sim.lines = Lines(path=tmp_path / "lines.json", payload=payload)
    sim.set_cursor((300.0, 300.0))
    assert sim.begin_drag("body", sim.creature.chunk0) is True
    sim.step(0.025)
    assert sim.dialogue.text == "滚开。", "抓起的那一 tick 就该触发 drag 台词"


def test_entering_talk_makes_the_pet_say_its_talk_line(tmp_path):
    from iterator_pet.simulation.state import SimulationState

    payload = {"characters": {"past_moon": {"drag": ["怎么了？"], "talk": ["你还在这里吗？"]}}}
    sim = SimulationState((0.0, 0.0, 1138.0, 608.0), warmup=0, character="past_moon")
    sim.lines = Lines(path=tmp_path / "lines.json", payload=payload)
    sim.set_cursor((300.0, 300.0))
    sim.behaviour = "talk"
    sim.step(0.025)
    assert sim.dialogue.text == "你还在这里吗？"


# --------------------------------------------------------------------------
# lines.json 的自愈（玩家反馈"文件好像没有正常生成"）
# --------------------------------------------------------------------------
def test_empty_file_is_repaired_with_defaults(tmp_path):
    path = tmp_path / "lines.json"
    path.write_text("", encoding="utf-8")           # 0 字节：旧实现永远不修
    lines = Lines(path=path)
    assert lines.pool("pebbles", "drag"), "空文件必须被补成默认内容"
    assert path.stat().st_size > 0


def test_whitespace_only_file_is_repaired(tmp_path):
    path = tmp_path / "lines.json"
    path.write_text("   \n\n\t", encoding="utf-8")
    lines = Lines(path=path)
    assert lines.pool("past_moon", "talk")


def test_file_deleted_at_runtime_is_recreated_on_the_next_tick(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    path.unlink()
    for _ in range(C.DIALOGUE_RELOAD_TICKS):
        lines.tick()
    assert path.is_file(), "运行中删掉文件，2 秒内应当自己长回来"
    assert json.loads(path.read_text(encoding="utf-8"))["characters"]["pebbles"]["talk"] == ["……"]


def test_non_empty_broken_json_is_never_overwritten(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    broken = '{ "characters": { "pebbles": '
    path.write_text(broken, encoding="utf-8")
    lines.reload(force=True)
    assert path.read_text(encoding="utf-8") == broken, "玩家手改坏的正文不能被我覆盖掉"
    assert lines.pool("pebbles", "drag"), "但内存里要保留上一份可用表"


def test_state_reports_path_existence_and_entries(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    state = lines.state()
    assert state["path"] == str(path)
    assert state["exists"] is True
    assert state["size"] > 0
    # 6 组：pebbles 4+1+2、past_moon 4+1+2 —— ritual 是 2026-10-30 用户要求新加的
    # 事件（仪式成功时说一句），所以总数从 10 变成 14。
    assert state["entries"] == 14
    assert state["characters"]["past_moon"] == ["drag", "ritual", "talk"]
    assert state["loaded_from_file"] is True


# --------------------------------------------------------------------------
# 数据目录：**桌宠根目录**优先（用户 2026-10-30：别往 C 盘写）
# --------------------------------------------------------------------------
def _pool_paths(tmp_path):
    return tmp_path / "app" / "lines.json", tmp_path / "legacy" / "lines.json"


def test_pool_path_prefers_app_dir_migrates_legacy_and_falls_back(tmp_path):
    """台词文件放哪：桌宠根目录优先；老位置有内容要搬过来；根目录不可写才退回老位置。

    合并理由：同一个函数的三个分支，一条用例遍历即可。
    """
    from iterator_pet.simulation.lines import pick_pool_path

    app = tmp_path / "app" / "lines.json"
    legacy = tmp_path / "legacy" / "lines.json"
    assert pick_pool_path(app, legacy, lambda _: True) == app, "根目录优先"

    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"characters": {"pebbles": {"talk": ["旧台词"]}}}', encoding="utf-8")
    chosen = pick_pool_path(app, legacy, lambda _: True)
    assert chosen == app, "老位置有内容时应当搬到桌宠目录"
    assert "旧台词" in app.read_text(encoding="utf-8"), "玩家改过的台词不能丢"

    readonly = tmp_path / "readonly" / "lines.json"
    legacy2 = tmp_path / "legacy2" / "lines.json"
    assert pick_pool_path(readonly, legacy2, lambda _: False) == legacy2, "不可写才退回老位置"


def test_data_dir_is_the_app_dir_by_default():
    from iterator_pet import config as cfg

    assert cfg.data_dir() == cfg.app_dir(), "源码版/打包版都必须优先用桌宠自己的目录"


def test_data_dir_honours_the_override(monkeypatch, tmp_path):
    from iterator_pet import config as cfg

    target = tmp_path / "sandbox"
    monkeypatch.setattr(cfg, "_DATA_DIR", None)
    monkeypatch.setenv("ITERATORPET_DATA_DIR", str(target))
    assert cfg.data_dir() == target
    monkeypatch.delenv("ITERATORPET_DATA_DIR")
    monkeypatch.setattr(cfg, "_DATA_DIR", None)


# --------------------------------------------------------------------------
# 编辑器用的保存入口（保留文件里其他键，原子写）
# --------------------------------------------------------------------------
def test_save_pools_keeps_unknown_keys_and_strips_blank_lines(tmp_path):
    path = tmp_path / "lines.json"
    lines = Lines(path=path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["version"] = 7
    raw["future_field"] = {"keep": True}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    lines.reload(force=True)

    assert lines.save_pools({("past_moon", "drag"): [" 滚开。 ", "", "   ", "别烦我。"]})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 7 and data["future_field"] == {"keep": True}, "未知键不能被冲掉"
    assert data["characters"]["past_moon"]["drag"] == ["滚开。", "别烦我。"]
    assert lines.pool("past_moon", "drag") == ["滚开。", "别烦我。"], "保存后要立刻热重载"
    assert data["characters"]["pebbles"]["drag"], "没动到的组要原样保留"


def test_save_pools_rebuilds_from_defaults_when_the_file_is_garbage(tmp_path):
    path = tmp_path / "lines.json"
    path.write_text("not json at all", encoding="utf-8")
    lines = Lines(path=path)
    assert lines.save_pools({("pebbles", "talk"): ["新台词"]})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["characters"]["pebbles"]["talk"] == ["新台词"]
    assert data["characters"]["past_moon"]["talk"], "其余组用内置默认补齐"
