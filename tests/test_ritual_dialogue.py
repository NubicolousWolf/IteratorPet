"""仪式对话事件（用户 2026-10-30 追加）的测试。

三件事：

1. 仪式**完成那一 tick**触发一次 ``ritual``（五块卵石是 ``flash``，过去之月是
   ``tremble_done``），冷却期内不再触发；
2. 老 ``lines.json`` 里没有 ``ritual`` 这一组时**回落到内置默认**（不然更新后她就哑了），
   但玩家**故意清空**的组仍然沉默；
3. 「自定义对话」编辑器的下拉里有第三项，能增删并保存。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.platform.dialogue_editor import DialogueEditor  # noqa: E402
from iterator_pet.simulation import constants as C  # noqa: E402
from iterator_pet.simulation.events import DialogueEvents  # noqa: E402
from iterator_pet.simulation.lines import Lines  # noqa: E402
from iterator_pet.simulation.state import SimulationState  # noqa: E402

APP = QApplication.instance() or QApplication([])


def _sim(error=0.0):
    return SimulationState((0.0, 0.0, 776.0, 415.0), character="pebbles", warmup=0)


# ------------------------------------------------------------------ 事件 --
def test_ritual_fires_once_then_cools_down():
    events = DialogueEvents()
    assert events.tick("roaming", False, False, True) == "ritual"
    assert events.ritual_fires == 1
    assert events.ritual_cooldown == C.DIALOGUE_RITUAL_COOLDOWN

    assert events.tick("roaming", False, False, True) is None
    assert events.ritual_fires == 1


def test_ritual_wins_over_drag_on_the_same_tick():
    events = DialogueEvents()
    assert events.tick("roaming", True, False, True) == "ritual", "同一 tick 只报一个，仪式优先"


def test_drag_and_talk_stay_silent_during_the_ritual():
    events = DialogueEvents()
    assert events.tick("talk", True, True, False) is None


def test_cooldowns_are_configurable_and_zero_means_unlimited():
    events = DialogueEvents()
    events.set_cooldowns(drag=0, talk=0, ritual=0)
    assert events.tick("roaming", True) == "drag"
    assert events.tick("roaming", False, False, True) == "ritual"
    assert events.tick("talk", False) == "talk", "0 冷却 = 下一 tick 还能说"
    assert events.drag_cooldown_ticks == 0


# ------------------------------------------------------------- 台词池回落 --
def test_lines_fall_back_to_the_builtin_ritual_pool(tmp_path):
    import json

    path = tmp_path / "lines.json"
    # 老玩家的文件：只有 drag / talk，没有 ritual
    path.write_text(
        json.dumps({"version": 1, "characters": {"pebbles": {"drag": ["滚开。"], "talk": ["……"]}}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    lines = Lines(path=path, seed=1)
    lines.reload(force=True)
    assert lines.pool("pebbles", "ritual"), "缺组要回落到内置默认，不能哑掉"
    assert "滚开。" == lines.pool("pebbles", "drag")[0], "文件里的内容优先"


def test_a_deliberately_emptied_group_stays_silent(tmp_path):
    import json

    path = tmp_path / "lines.json"
    path.write_text(
        json.dumps({"characters": {"pebbles": {"ritual": []}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    lines = Lines(path=path, seed=1)
    lines.reload(force=True)
    assert lines.pool("pebbles", "ritual") == [], "玩家清空 = 不要再说话"


# ---------------------------------------------------------------- 仿真端 --
def test_the_simulation_consumes_the_ritual_event_exactly_once():
    sim = _sim()
    sim._ritual_event = "flash"
    sim._step_dialogue()
    assert sim.events.ritual_fires == 1
    assert sim.last_spoken in sim.lines.pool("pebbles", "ritual")

    sim._step_dialogue()          # 没有新的 flash 了
    assert sim.events.ritual_fires == 1


@pytest.mark.parametrize("event", ["flash", "tremble_done"])
def test_both_ritual_endings_count_as_the_ritual(event):
    sim = _sim()
    sim._ritual_event = event
    sim._step_dialogue()
    assert sim.events.ritual_fires == 1


def test_an_abort_is_not_a_ritual():
    sim = _sim()
    sim._ritual_event = "abort"
    sim._step_dialogue()
    assert sim.events.ritual_fires == 0


# ---------------------------------------------------------------- 编辑器 --
def test_the_editor_offers_the_ritual_group(tmp_path):
    editor = DialogueEditor(Lines(path=tmp_path / "lines.json"))
    items = [editor.event_box.itemText(i) for i in range(editor.event_box.count())]
    assert items == ["拖拽", "长时间静止", "仪式"]
    editor.event_box.setCurrentIndex(2)
    assert editor.list.count() == len(C.DEFAULT_LINES["characters"]["pebbles"]["ritual"])
    editor.close()


def test_the_editor_can_add_and_save_a_ritual_line(tmp_path):
    lines = Lines(path=tmp_path / "lines.json")
    editor = DialogueEditor(lines)
    editor.event_box.setCurrentIndex(2)
    editor.add_line()
    editor.list.item(editor.list.count() - 1).setText("这次没有下一次。")
    editor.save()
    assert "这次没有下一次。" in lines.pool("pebbles", "ritual")
    editor.close()
