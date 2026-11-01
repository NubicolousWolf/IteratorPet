"""第二组：电脑状态 → 她的收敛 / 休眠（2026-11-01）。

这一层是**纯映射**：`WorldState` 进来，`WorldReactions` 出去，外加仿真层把
电光、光环亮度、休眠姿态、免打扰接上。采样本身（`platform/sensors.py`）的
ctypes 调用不在这里测——只测它的两个纯函数（CPU 增量、电池解析）。

分组见 `tests/conftest.py`（``test_world`` → ``sim``）。
"""

from __future__ import annotations

from iterator_pet.platform.fixed_loop import GAME_DT
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState
from iterator_pet.simulation.world import WorldReactions, WorldState

BOUNDS = (0.0, 0.0, 1138.0, 608.0)


def _sim() -> SimulationState:
    return SimulationState(BOUNDS, warmup=0)


def _feed(sim: SimulationState, samples: int, **kwargs) -> None:
    world = WorldState(**kwargs)
    for _ in range(samples):
        sim.observe_world(world)


# ------------------------------------------------------------------ A1 CPU --
def test_high_cpu_dims_the_halo_and_kills_the_sparks():
    reactions = WorldReactions()
    world = WorldState(cpu_pct=0.95, hour=14, valid=True)
    for _ in range(12):
        reactions.update(world)
    assert reactions.cpu_high is True
    assert reactions.sparks == 0.0, "用户 2026-10-31：高占用时**关闭**电光"
    assert reactions.dim < 0.6, "光环要真的变暗（EMA 12 s 后到 0.50 附近）"


def test_cpu_hysteresis_keeps_it_from_flickering():
    reactions = WorldReactions()
    for _ in range(12):
        reactions.update(WorldState(cpu_pct=0.9, valid=True))
    assert reactions.cpu_high
    # 0.60 在"进入 0.70 / 退出 0.55"之间 → 不许抖回亮
    for _ in range(10):
        reactions.update(WorldState(cpu_pct=0.60, valid=True))
    assert reactions.cpu_high is True
    for _ in range(20):
        reactions.update(WorldState(cpu_pct=0.40, valid=True))
    assert reactions.cpu_high is False
    assert reactions.sparks == 1.0


def test_the_simulation_hands_the_spark_scale_to_the_halo():
    sim = _sim()
    _feed(sim, 12, cpu_pct=0.95, hour=14, valid=True)
    assert sim.halo.spark_scale == 0.0
    sim.step(GAME_DT)                       # 快照在 tick 里组，喂完要推进一帧
    assert sim.interpolate(1.0).halo_dim < 0.7
    assert sim.status()["world"]["reactions"]["cpu_high"] is True


# -------------------------------------------------------------- A2 memory --
def test_memory_bits_are_only_mapped_when_the_switch_is_on():
    off = WorldReactions()
    for _ in range(6):
        off.update(WorldState(cpu_pct=0.1, mem_pct=0.95, hour=14, valid=True),
                   reflect_memory=False)
    assert off.bits == -1, "开关关着时保持原来的 100 位"

    on = WorldReactions()
    for _ in range(6):
        on.update(WorldState(cpu_pct=0.1, mem_pct=0.95, hour=14, valid=True),
                  reflect_memory=True)
    assert 60 <= on.bits <= sum(C.HALO_RINGS), "占用高时记忆位密集浮现"


# -------------------------------------------------------------- A3 battery --
def test_low_battery_dims_and_only_warns_once():
    reactions = WorldReactions()
    world = WorldState(battery_pct=0.10, on_ac=False, hour=14, valid=True)
    for _ in range(8):
        reactions.update(world)
    assert reactions.dim < C.WORLD_DIM_BATTERY + 0.1
    assert reactions.take_low_battery() is True
    assert reactions.take_low_battery() is False, "提醒只响一次"
    # 插上电以后重新武装
    for _ in range(3):
        reactions.update(WorldState(battery_pct=0.10, on_ac=True, valid=True))
    for _ in range(3):
        reactions.update(world)
    assert reactions.take_low_battery() is True


def test_a_desktop_without_a_battery_is_left_alone():
    reactions = WorldReactions()
    for _ in range(6):
        reactions.update(WorldState(battery_pct=None, on_ac=True, valid=True))
    assert reactions.dim == 1.0
    assert reactions.take_low_battery() is False


# ----------------------------------------------------------- A4 / A5 / A6 ----
def test_night_raises_the_meditate_odds():
    reactions = WorldReactions()
    reactions.update(WorldState(hour=2, valid=True))
    assert reactions.night is True
    assert reactions.meditate_gain() == C.WORLD_MEDITATE_NIGHT_GAIN
    reactions.update(WorldState(hour=14, valid=True))
    assert reactions.night is False
    assert reactions.meditate_gain() == 1.0


def test_long_idle_puts_her_to_sleep_and_input_wakes_her():
    sim = _sim()
    _feed(sim, 3, hour=14, user_idle_s=C.WORLD_SLEEP_AFTER_S + 60, valid=True)
    sim.step(GAME_DT)
    assert sim.status()["posture"] == "sleep"
    assert sim.interpolate(1.0).eyes_closed is True

    _feed(sim, 1, hour=14, user_idle_s=0.0, valid=True)
    assert sim.status()["posture"] == "", "输入回来了就该醒"


def test_fullscreen_quiet_suppresses_talk_and_freezes_idle_talk():
    sim = _sim()
    _feed(sim, 2, hour=14, fullscreen=True, valid=True)
    assert sim.selector.quiet is True
    body = sim.creature.chunk0
    cursor = (body[0] + 100.0, body[1])
    sim.set_cursor(cursor)
    sim.selector.still_ticks = 10_000
    assert sim.selector.gate("talk", body, cursor, 0.0) is False, "全屏时不主动搭话"

    before = sim._idle_ticks
    for _ in range(30):
        sim.step(GAME_DT)
    assert sim._idle_ticks == before, "全屏时空闲计时冻结（不是清零）"


# ------------------------------------------------------------- sensors 纯函数 --
def test_cpu_ratio_matches_the_task_manager_formula():
    from iterator_pet.platform.sensors import cpu_ratio

    prev = (1_000, 3_000, 1_000)
    # idle +1000、kernel +1500（含 idle）、user +500 → 总量 2000，空闲 1000 → 50 %
    assert abs(cpu_ratio(prev, (2_000, 4_500, 1_500)) - 0.5) < 1e-9
    assert cpu_ratio(prev, prev) == 0.0, "没有增量时不猜"


def test_battery_fraction_understands_the_unknown_byte():
    from iterator_pet.platform.sensors import battery_fraction

    assert battery_fraction(255, 1) == (None, True)
    pct, on_ac = battery_fraction(20, 0)
    assert abs(pct - 0.2) < 1e-9 and on_ac is False
