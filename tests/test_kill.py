"""心情 + "杀鼠标"仪式（docs/PLAN-kill.md）的纯状态测试。

只测仿真侧：心情速率、破闸强制打断、仪式时序、abort 不重入、冷却、快照刷新、面板合同。
平台层（光标特效/托盘）不在这里。
"""

from math import dist

from iterator_pet.platform.fixed_loop import GAME_DT
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

BOUNDS = (0.0, 0.0, 1138.0, 608.0)


def _sim(scale: float = 1.5) -> SimulationState:
    sim = SimulationState(BOUNDS, warmup=0)      # warmup=0 只为跑得快；预热不影响心情逻辑
    sim.scale = scale
    return sim


def _drag_until_gate(sim: SimulationState, sim_px_per_tick: float, limit: int = 3000) -> int:
    start = sim.creature.chunk0
    assert sim.begin_drag("body", start) is True
    n = 0
    while sim.kill.phase == "idle" and n < limit:
        n += 1
        sim.drag_to((start[0] + sim_px_per_tick * n, start[1]))
        sim.step(GAME_DT)
    return n


def test_slow_drag_takes_about_twenty_seconds():
    # 0.6 仿真 px/tick @ scale 1.5 = 36 屏幕 px/s 的"慢拖"
    ticks = _drag_until_gate(_sim(), 0.6)
    assert 700 <= ticks <= 830, f"慢拖到破闸用了 {ticks} tick（{ticks / 40:.1f} s），期望约 20 s"


def test_fast_drag_takes_about_ten_seconds():
    # 12 仿真 px/tick @ scale 1.5 = 720 屏幕 px/s（速度项打满）
    ticks = _drag_until_gate(_sim(), 12.0)
    assert 380 <= ticks <= 425, f"快拖到破闸用了 {ticks} tick（{ticks / 40:.1f} s），期望约 10 s"


def test_screen_speed_is_independent_of_size_tier():
    """同一只手（600 屏幕 px/s）在 40% 档与 PRO 档必须同样快到闸（审计 P0-6）。"""
    results = []
    for scale in (0.6, 2.25):
        sim = _sim(scale)
        # 600 屏幕 px/s → 每 tick 15 屏幕 px → 仿真 px = 15 / scale
        results.append(_drag_until_gate(sim, 15.0 / scale))
    slow, fast = results
    assert abs(slow - fast) / max(slow, fast) < 0.10, f"跨档差太多：{slow} vs {fast}"


def test_holding_still_does_not_drain_temper():
    sim = _sim()
    assert sim.begin_drag("body", sim.creature.chunk0) is True
    start = sim.creature.chunk0
    for _ in range(300):
        sim.drag_to(start)                      # 按住不动
        sim.step(GAME_DT)
    assert sim.temper.value > C.TEMPER_KILL_GATE, "按住不动不该掉心情（审计 P1-5）"
    assert sim.kill.phase == "idle"


def test_breaking_the_gate_interrupts_drag_and_fling():
    sim = _sim()
    _drag_until_gate(sim, 12.0)
    assert sim.drag_mode is None, "破闸必须清掉拖拽"
    assert sim.drag_point is None
    assert sim.fling_ticks == 0, "强制打断不能留下没执行的甩出（否则冷却后会莫名弹一下）"
    assert sim.begin_drag("body", sim.creature.chunk0) is False, "仪式/冷却期内不许拖"
    assert abs(sim.temper.value - C.TEMPER_AFTER_KILL) < 0.05


def test_ritual_refreshes_the_snapshot_every_tick():
    sim = _sim()
    _drag_until_gate(sim, 12.0)
    prev = sim._cur
    for _ in range(60):
        sim.step(GAME_DT)
        assert sim._cur is not prev, "仪式期间快照没刷新 → 白光会定格（审计 P0-2）"
        prev = sim._cur


def test_kill_fac_ramps_in_exactly_forty_ticks():
    sim = _sim()
    sim.kill.begin(sim.cursor, sim.ticks)
    for _ in range(C.KILL_WARNING_TICKS):
        sim.step(GAME_DT)
    assert sim.kill.phase == "charging"
    ticks = 0
    while sim.kill.kill_fac < 1.0 and ticks < 60:
        ticks += 1
        sim.step(GAME_DT)
    assert ticks == 40, f"kill_fac 应当恰好 40 tick 胀满，实测 {ticks}"


def test_flash_spawns_twenty_white_sparks():
    sim = _sim()
    sim.kill.begin(sim.cursor, sim.ticks)
    for _ in range(C.KILL_WARNING_TICKS + 41):
        sim.step(GAME_DT)
    sparks = sim.kill.sparks
    assert len(sparks) == C.KILL_SPARK_COUNT == 20
    for s in sparks:
        assert abs(complex(s[2], s[3])) <= C.KILL_SPARK_SPEED_MAX + 1.0
        # 检查时已经掉了 1-2 tick 寿命（flash 那一 tick 就调过 tick_sparks）
        assert C.KILL_SPARK_LIFE[0] - 2 <= s[4] <= C.KILL_SPARK_LIFE[1]


def test_abort_does_not_reenter_immediately():
    """蓄能时跑开 → abort；之后不许马上重入（审计 P0-1 的死循环）。"""
    sim = _sim()
    _drag_until_gate(sim, 12.0)                 # 进 warning
    for _ in range(C.KILL_WARNING_TICKS + 5):
        sim.step(GAME_DT)
    assert sim.kill.phase == "charging"
    far = (sim.creature.chunk0[0] - 900.0, sim.creature.chunk0[1])
    for _ in range(12):                         # 光标跑到很远，kill_fac 衰减到 abort
        sim.set_cursor(far)
        sim.step(GAME_DT)
    assert sim.kill.phase == "idle", "跑开应当 abort"
    assert abs(sim.temper.value - C.TEMPER_AFTER_ABORT) < 0.05
    assert sim.begin_drag("body", sim.creature.chunk0) is False, "abort 冷却期内不许立刻再抓"
    for _ in range(C.KILL_ABORT_COOLDOWN + 1):
        sim.step(GAME_DT)
    assert sim.begin_drag("body", sim.creature.chunk0) is True


def test_cooldown_lasts_ten_seconds():
    sim = _sim()
    sim.kill.begin(sim.cursor, sim.ticks)
    total = C.KILL_WARNING_TICKS + 40 + C.KILL_FLASH_TICKS
    for _ in range(total):
        sim.step(GAME_DT)
    assert sim.kill.phase == "cooldown"
    assert sim.kill.cooldown in (C.KILL_COOLDOWN_TICKS, C.KILL_COOLDOWN_TICKS - 1)
    elapsed = 0
    while not sim.begin_drag("body", sim.creature.chunk0) and elapsed < 600:
        elapsed += 1
        sim.step(GAME_DT)
    assert 395 <= elapsed <= 405, f"冷却应当 400 tick（10 s），实测 {elapsed}"


def test_status_keeps_the_three_state_behaviour():
    sim = _sim()
    _drag_until_gate(sim, 12.0)
    status = sim.status()
    assert "temper" in status and "kill" in status
    assert status["behaviour"] in C.BEHAVIOURS, "仪式期间不许把 behaviour 写成第 4 个值"
    assert status["kill"]["phase"] in ("warning", "charging", "flash", "cooldown", "idle")


def test_drag_test_guard_two_hundred_ticks_is_safe():
    """护栏：以后有人把拖拽测试加长到 200 tick，也不该被仪式打断（报错指得到真因）。"""
    sim = _sim()
    ticks = _drag_until_gate(sim, 12.0, limit=200)
    assert ticks == 200, "200 tick 就让仪式触发了，说明速率常数变了"
    assert sim.kill.phase == "idle"
    assert sim.dragging
