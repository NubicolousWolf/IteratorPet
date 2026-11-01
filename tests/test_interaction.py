"""2026-11-01 第一组：打坐 / 戳一下 / 启动问候 / 空闲自语 / 托盘状态。

真窗口的**手感**（点一下不挪窝、拖起来才跟手）靠人工验；这里钉的是可自动化的合同：
姿态层、事件队列、计时的界、托盘那一行只读状态。

分组见 ``tests/conftest.py``（``test_interaction`` → ``ui``）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from iterator_pet.platform.fixed_loop import GAME_DT  # noqa: E402
from iterator_pet.simulation import constants as C  # noqa: E402
from iterator_pet.simulation.state import SimulationState  # noqa: E402

APP = QApplication.instance() or QApplication([])
BOUNDS = (0.0, 0.0, 1138.0, 608.0)          # 本机满血档腔室（AGENTS §2）


def _warm(ticks: int = 40) -> SimulationState:
    sim = SimulationState(BOUNDS)
    for _ in range(ticks):
        sim.step(GAME_DT)
    return sim


# ---------------------------------------------------------------- B1 打坐 --
def test_meditate_stays_where_she_is_and_closes_her_eyes():
    """B1：原地、闭眼。目标点**不许**是腔室中心（用户 2026-10-31 明确否掉）。"""
    sim = _warm()
    assert sim.enter_meditate(400) is True
    before = sim.creature.chunk0
    for _ in range(120):                    # 3 s
        sim.step(GAME_DT)
    drift = C.dist(before, sim.creature.chunk0)
    assert drift < 12.0, f"打坐是原地不动，实测漂了 {drift:.1f} px"
    assert sim.status()["posture"] == "meditate"
    assert sim.interpolate(1.0).eyes_closed is True, "闭眼要进渲染快照"
    assert sim.interpolate(1.0).behaviour in C.BEHAVIOURS, "打坐不进三态抽签"


def test_waking_up_opens_the_eyes_again():
    sim = _warm()
    sim.enter_meditate()
    assert sim.meditating is True
    assert sim.wake_up() is True
    sim.step(GAME_DT)
    assert sim.status()["posture"] == ""
    assert sim.interpolate(1.0).eyes_closed is False


def test_dragging_her_wakes_her_up():
    sim = _warm()
    sim.enter_meditate()
    assert sim.begin_drag("body", sim.creature.chunk0) is True
    assert sim.status()["posture"] == "", "被拎起来还在打坐就是 bug"


# ---------------------------------------------------------------- D1 戳 ----
def test_a_poke_wakes_her_and_says_one_line():
    sim = _warm()
    sim.enter_meditate()
    assert sim.poke() is True
    assert sim.status()["posture"] == ""
    sim.step(GAME_DT)
    assert sim.dialogue.text, "戳一下必须换来一句台词（或一句唤醒）"


def test_the_second_poke_inside_the_cooldown_is_silent():
    sim = _warm()
    assert sim.poke() is True
    sim.step(GAME_DT)                       # 第一句说出口
    sim.dialogue.cancel()
    assert sim.poke() is False, "10 s 内连戳不该一句接一句"


# ------------------------------------------------------- B4 启动 / B3 空闲 --
def test_construction_alone_does_not_queue_a_greeting():
    """构造器不许自己排台词：测试里上百次 ``SimulationState(...)`` 都会冒一句话。"""
    sim = SimulationState(BOUNDS, warmup=0)
    assert sim.events.pending is None


def test_the_startup_greeting_is_said_once():
    sim = SimulationState(BOUNDS, warmup=0)
    assert sim.announce_startup() is True
    sim.step(GAME_DT)
    assert sim.dialogue.text
    assert sim.events.pending is None, "说过就把队列清掉，不能每 tick 重复"


def test_idle_talk_needs_twenty_minutes_without_interaction():
    sim = SimulationState(BOUNDS, warmup=0)
    sim._idle_ticks = C.DIALOGUE_IDLE_COOLDOWN - 1
    sim.step(GAME_DT)
    assert sim.events.pending == "idle_talk"
    sim.step(GAME_DT)                       # 下一 tick 才轮到说话
    assert sim.dialogue.text


def test_dragging_resets_the_idle_timer():
    sim = _warm()
    sim._idle_ticks = C.DIALOGUE_IDLE_COOLDOWN - 1
    sim.begin_drag("body", sim.creature.chunk0)
    sim.step(GAME_DT)
    assert sim._idle_ticks == 0, "有人拎她就不算没人理"


# ------------------------------------------------------------- E2 托盘 ----
class _Window:
    """``tray_menu`` 只读属性、只在点下去时才调方法，所以这里给个壳就够。"""

    paused = False
    click_through = False
    size_preset = "full"
    spark_rate = "eco"
    character = "pebbles"

    def __init__(self, sim) -> None:
        from iterator_pet.config import Config

        self.simulation = sim
        self.config = Config()

    def character_list(self):
        return [("pebbles", "五块卵石")]

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def test_the_tray_menu_starts_with_the_current_status():
    from iterator_pet.platform.tray import tray_menu

    sim = _warm()
    sim.enter_meditate()
    menu = tray_menu(_Window(sim))
    first = menu.actions()[0]
    assert first.text() == "状态：打坐中"
    assert not first.isEnabled(), "状态是只读的，不该可点"
    menu.deleteLater()


def test_the_tray_status_follows_the_posture():
    sim = _warm()
    sim.enter_meditate()
    assert sim.tray_status() == "打坐中"
    sim.wake_up()
    assert sim.tray_status() in ("漫游中", "看着你", "凑近光标")


# --------------------------------------------------- 工坊「本地对话」页 ----
def test_the_dialogue_tab_offers_the_three_new_events(tmp_path):
    from iterator_pet.platform.dialogue_tab import DialogueTab
    from iterator_pet.simulation.lines import Lines

    tab = DialogueTab(Lines(path=tmp_path / "lines.json"))
    labels = [tab.event_box.itemText(i) for i in range(tab.event_box.count())]
    assert labels == ["拖拽", "长时间静止", "仪式", "启动时", "戳一下", "空闲自语", "低电量"]
    tab.deleteLater()
