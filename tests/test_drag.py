"""鼠标拖拽（docs/PLAN-drag.md）的纯状态测试。

平台层的鼠标事件不在这里测（靠人工清单）；这里只钉住仿真侧的四条合同：
抓哪跟哪、拖拽期间行为层冻结、松手朝拖动方向甩且不出臂展、冷却期不能立刻再抓。
"""

from math import dist

from iterator_pet.platform.fixed_loop import GAME_DT
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

BOUNDS = (0.0, 0.0, 1138.0, 608.0)          # 本机满血档腔室（AGENTS §2）
LIMIT = sum(C.ARM_LENGTHS) * C.ARM_REACH_RATIO


def _warm(ticks: int = 60) -> SimulationState:
    sim = SimulationState(BOUNDS)
    for _ in range(ticks):
        sim.step(GAME_DT)
    return sim


def _drag_right(sim: SimulationState, ticks: int = 30, px_per_tick: float = 10.0):
    start = sim.creature.chunk0
    sim.begin_drag("body", start)
    for i in range(ticks):
        sim.drag_to((start[0] + px_per_tick * (i + 1), start[1]))
        sim.step(GAME_DT)
    return start


def test_dragging_puts_the_body_under_the_cursor():
    sim = _warm()
    offset_x = sim.drag_offset
    _drag_right(sim)
    # 抓取偏移让被抓点严格跟着光标：chunk0 = 光标 + 偏移
    assert abs(sim.creature.chunk0[0] - (sim.drag_point[0] + offset_x[0])) < 0.5
    assert abs(sim.creature.chunk0[1] - (sim.drag_point[1] + offset_x[1])) < 0.5


def test_dragging_freezes_the_behaviour_layer():
    sim = _warm(300)
    draws_before = sim.selector.draws
    behaviour_before = sim.behaviour
    _drag_right(sim, ticks=120)
    assert sim.selector.draws == draws_before, "拖拽期间不许抽签"
    assert sim.behaviour == behaviour_before, "拖拽期间 behavior 保持三态里的原值"
    # status() 的面板合同：behaviour 仍在三态里（曾经把这里写成 "dragging" → KeyError）
    status = sim.status()
    assert status["behaviour"] in C.BEHAVIOURS
    assert status["drag"] == "body"
    assert isinstance(status["segment"], int)


def test_release_flings_along_the_drag_direction():
    sim = _warm()
    _drag_right(sim, ticks=30, px_per_tick=10.0)
    end_x = sim.creature.chunk0[0]
    sim.end_drag()
    for _ in range(10):
        sim.step(GAME_DT)
    moved = sim.creature.chunk0[0] - end_x
    assert moved > 5.0, f"松手应沿拖动方向(+x)滑一段，实测 {moved:.1f} px（负数=往反方向弹）"


def test_release_never_leaves_the_arm_reach():
    sim = _warm()
    _drag_right(sim, ticks=40, px_per_tick=14.0)
    sim.end_drag()
    worst = 0.0
    for _ in range(60):
        sim.step(GAME_DT)
        base = sim.rail.pose(sim.frame_pos).point
        worst = max(worst, dist(base, sim.creature.chunk0))
    assert worst <= LIMIT * 1.02 + 1.0, f"甩出后离底座 {worst:.1f} px 超过臂展 {LIMIT:.0f}"


def test_cooldown_blocks_an_immediate_regrab():
    sim = _warm()
    _drag_right(sim, ticks=10)
    sim.end_drag()
    assert sim.begin_drag("body", sim.creature.chunk0) is False, "冷却期内不许立刻再抓"
    for _ in range(C.DRAG_COOLDOWN_TICKS + 1):
        sim.step(GAME_DT)
    assert sim.begin_drag("body", sim.creature.chunk0) is True


def test_drag_keeps_the_arm_tip_on_the_body():
    """审计/用户实测：机械臂是软约束，快拖时 tip→body 会拉开 200+ px（"手掉了"）。"""
    sim = _warm()
    _drag_right(sim, ticks=40, px_per_tick=12.0)
    gap = dist(sim.arm.joints[-1].pos, sim.creature.chunk0)
    assert gap <= sim.arm.joints[-1].total_length + 1.0, (
        f"机械臂最后一节离身体 {gap:.1f} px（正常 ≈ {sim.arm.joints[-1].total_length:.0f}）"
    )


def test_release_does_not_redraw_a_mood():
    """松手后不许立刻重新抽签：拖拽期间段计时冻结，且松手时刷新光标基准，
    否则第一 tick 的"速度"= 整个拖动距离 → gate 关 → 抽签（AGENTS §6.2 同类缺陷）。"""
    sim = _warm(300)
    draws_before = sim.selector.draws
    behaviour_before = sim.behaviour
    _drag_right(sim, ticks=60, px_per_tick=9.0)
    sim.end_drag()
    for _ in range(30):
        sim.step(GAME_DT)
    assert sim.selector.draws == draws_before, "松手后 30 tick 内不许重新抽签"
    assert sim.behaviour == behaviour_before
