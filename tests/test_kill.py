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
    # 默认给一个屏上的光标：蓄能期间"光标为 None 累计 40 tick 就 abort"（真实场景里只有
    # 鼠标移出屏幕才会这样），测试不该因为没设光标就莫名 abort
    sim.set_cursor((sim.creature.chunk0[0] - 120.0, sim.creature.chunk0[1]))
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


def test_kill_fac_ramps_in_eighty_ticks():
    """用户 2026-10-29 反馈"太快"，蓄能放慢到 0.0125/tick = 80 tick（2 s）。"""
    sim = _sim()
    sim.kill.begin(sim.cursor or sim.creature.head, sim.ticks)
    guard = 0
    while sim.kill.phase == "warning" and guard < 200:
        guard += 1
        sim.step(GAME_DT)
    assert sim.kill.phase == "charging"
    ticks = 0
    while sim.kill.kill_fac < 1.0 and ticks < 120:
        ticks += 1
        sim.step(GAME_DT)
    assert ticks in (79, 80, 81), f"kill_fac 应当约 80 tick 胀满（2 s），实测 {ticks}"


def test_flash_spawns_twenty_white_sparks():
    sim = _sim()
    sim.kill.begin(sim.cursor, sim.ticks)
    for _ in range(C.KILL_WARNING_TICKS + C.KILL_STAND_TICKS + 81):
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
    guard = 0
    while sim.kill.phase != "charging" and guard < 200:
        guard += 1
        sim.step(GAME_DT)
    assert sim.kill.phase == "charging"
    for _ in range(3):
        sim.step(GAME_DT)
    # 新的口径：光标在屏上就追着杀，**跑不掉**；只有光标离开屏幕（None）累计 40 tick 才 abort
    for _ in range(C.KILL_ESCAPE_TICKS + 5):
        sim.set_cursor(None)
        sim.step(GAME_DT)
    assert sim.kill.phase == "idle", "跑开应当 abort"
    assert abs(sim.temper.value - C.TEMPER_AFTER_ABORT) < 0.05
    # 新语义（用户 2026-10-29）：只有**仪式进行中**不许拖；abort 之后立刻能拖，
    # 被冷却拦住的是"再次触发"，不是拖拽。
    assert sim.begin_drag("body", sim.creature.chunk0) is True
    sim.end_drag()
    sim.temper.value = C.TEMPER_KILL_GATE - 0.01
    for _ in range(C.KILL_ABORT_COOLDOWN - 20):
        sim.step(GAME_DT)
    assert sim.kill.phase == "idle", "abort 短冷却期内不许重新进仪式"
    for _ in range(30):
        sim.step(GAME_DT)
    assert sim.kill.phase != "idle", "短冷却结束后应当能再次触发"


def test_cooldown_lasts_ten_seconds():
    sim = _sim()
    sim.kill.begin(sim.cursor, sim.ticks)
    guard = 0
    while sim.kill.phase != "cooldown" and guard < 400:
        guard += 1
        sim.step(GAME_DT)
    assert sim.kill.phase == "cooldown"
    assert sim.kill.cooldown in (C.KILL_COOLDOWN_TICKS, C.KILL_COOLDOWN_TICKS - 1)
    assert sim.begin_drag("body", sim.creature.chunk0) is True, "冷却期应当可以拖（只有动画期间不行）"
    sim.end_drag()
    sim.temper.value = C.TEMPER_KILL_GATE - 0.01
    elapsed = 0
    # 相位是 cooldown（不是 idle），一直压着心情推，直到重新进 warning
    while sim.kill.phase in ("idle", "cooldown") and elapsed < 600:
        sim.temper.value = C.TEMPER_KILL_GATE - 0.01
        elapsed += 1
        sim.step(GAME_DT)
    assert 380 <= elapsed <= 420, f"冷却应当 400 tick（10 s）后才允许再触发，实测 {elapsed}"


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


def test_trigger_retreats_to_one_and_a_half_talk_distance():
    """用户 2026-10-29：触发时人偶要立刻挣脱光标，退到 1.5×talk 距离处站定、摆正位姿。"""
    sim = _sim()
    body = sim.creature.chunk0
    sim.set_cursor((max(60.0, body[0] - 160.0), body[1]))
    sim.temper.value = C.TEMPER_KILL_GATE - 0.01
    for _ in range(12):                      # 挣脱 + 站定
        sim.step(GAME_DT)
    goal = C.TALK_DISTANCE * C.KILL_RETREAT_TALK_RATIO
    got = dist(sim.creature.chunk0, sim.cursor)
    assert abs(got - goal) < 3.0, f"挣脱后应离光标 {goal:.0f} px，实测 {got:.0f}"
    assert abs(sim.creature.chunk1[0] - sim.creature.chunk0[0]) < 0.5, "身体应当摆正（竖直）"
    assert abs(sim.creature.chunk1[1] - sim.creature.chunk0[1] - 9.0) < 1.0


def test_charging_chases_the_cursor_and_cannot_be_dodged():
    """用户 2026-10-29：触发挣脱之后必须杀到 —— 鼠标乱跑也照样蓄满（只跟，不衰减）。"""
    sim = _sim()
    body = sim.creature.chunk0
    sim.set_cursor((max(60.0, body[0] - 160.0), body[1]))
    sim.temper.value = C.TEMPER_KILL_GATE - 0.01
    guard = 0
    while sim.kill.phase != "charging" and guard < 200:
        guard += 1
        sim.step(GAME_DT)
    assert sim.kill.phase == "charging"
    base = sim.creature.chunk0
    for i in range(95):                          # 一边跑一边推（80 tick 才蓄满，留余量）
        sim.set_cursor((base[0] - 200.0 + (i % 20) * 30.0, base[1] + (i % 7) * 20.0))
        sim.step(GAME_DT)
    assert sim.kill.phase in ("flash", "cooldown"), "光标乱跑也必须蓄满并进 flash"


def test_halo_brightens_during_the_ritual():
    """K2（用户 2026-10-29）：杀鼠标期间光环/电光要变亮，结束后回到原亮度。"""
    sim = _sim()
    before = sim._cur.halo_gain
    assert abs(before - 1.0) < 0.02
    sim.temper.value = C.TEMPER_KILL_GATE - 0.01
    guard = 0
    while sim.kill.phase != "charging" and guard < 200:
        guard += 1
        sim.step(GAME_DT)
    during = sim._cur.halo_gain
    assert during > 1.1, f"蓄能期光环应当变亮，实测 gain={during:.3f}"
    assert sim._cur.halo_white > 0.1
    # 推到 flash：顶格
    guard = 0
    while sim.kill.phase != "flash" and guard < 200:
        guard += 1
        sim.step(GAME_DT)
    assert sim._cur.halo_gain > 1.5
    # 冷却/结束
    guard = 0
    while sim.kill.phase != "idle" and guard < 800:
        guard += 1
        sim.step(GAME_DT)
    assert abs(sim._cur.halo_gain - 1.0) < 0.02, "仪式结束后光环亮度要回原值"
