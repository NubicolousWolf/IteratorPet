import pytest
"""Behaviour selection: roaming / investigate / talk.

The selector itself is a DESIGN ADDITION -- the source sets ``movementBehavior``
from story actions (``SeePlayer`` -> KeepDistance, conversation -> Talk) and runs
Idle, which ignores the player, when nothing is happening. What is borrowed is
the *shape* of each state: Investigate aims at ``cursor + DegToVec(angle) * 150``
with the angle sweeping inside +-70 deg (ss.cs:4174-4204), Talk parks a fixed
distance from the player the way ``CommunicatePosScore`` does (ss.cs:4278-4285).

Since 2026-10-27 the selector DRAWS instead of taking an argmax, so the contract
these tests pin is a RATE, not a phase: "a parked cursor spends three quarters of
its time roaming" is a property of the draw, not of which state tick 600 landed
in. The numbers asserted here are the user's targets, measured in
``_work/behaviour_probe.py`` (15 min x 8 seeds) -- see AGENTS 6.2.
"""

import inspect
from math import sin

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.behavior import BehaviourSelector
from iterator_pet.simulation.state import SimulationState

BODY = (400.0, 200.0)


def _drive(cursor_at, ticks: int = 600, seed: int = 20261027) -> tuple[str, list[str]]:
    """Run the selector against ``cursor_at(t) -> cursor``."""
    selector = BehaviourSelector(seed=seed)
    history = []
    for tick in range(ticks):
        cursor = cursor_at(tick)
        measured = selector.observe_cursor(cursor)
        history.append(selector.choose(BODY, cursor, measured))
    return selector.behaviour, history


def _share(history: list[str], name: str) -> float:
    return history.count(name) / max(1, len(history))


def _parked(tick: int):
    return (BODY[0] + 150.0, BODY[1])


def _sweep(tick: int):
    return (BODY[0] + 120.0 * sin(tick * 0.05), BODY[1])


def test_no_cursor_means_roaming():
    """A desktop with no mouse is the source's Idle: the pet just wanders."""
    final, history = _drive(lambda tick: None)
    assert final == "roaming"
    assert set(history) == {"roaming"}


def test_a_parked_cursor_still_roams_three_quarters_of_the_time():
    """The user's target: "roaming 75 %" while the cursor sits still.

    15 min of a parked cursor. Measured 75.0-76.2 % across 8 seeds; old argmax
    code sat at roaming 31.8 % / talk 68.2 % on this exact scenario.

    WHAT THIS TEST DOES NOT PIN: the 75 % is the cooldown arithmetic
    900/(900+2700), not the roulette -- a deterministic argmax at the boundaries
    also lands on 75.00, and 7 of 8 seeds give exactly 75.00 %. The band is a
    coarse net (20x the seed spread). The draw itself is pinned by
    ``test_the_draw_follows_its_weights`` and the hand-over rule by
    ``test_a_finished_conversation_must_hand_over``.
    """
    _, history = _drive(_parked, ticks=40 * 900)
    roaming, talk = _share(history, "roaming"), _share(history, "talk")
    assert 0.70 <= roaming <= 0.80, f"roaming {roaming:.1%} is not ~75 %"
    assert 0.20 <= talk <= 0.30, f"talk {talk:.1%} is not ~25 %"
    assert "investigate" not in history, "a motionless cursor is not investigated"


def test_a_moving_cursor_earns_investigate_without_crowding_out_roaming():
    """Motion is something to investigate -- but it may not swallow the pet.

    The user's complaint was "it runs after the mouse forever" (roaming 9.1 %,
    investigate 68.4 % measured on the old selector). Investigating is now a
    ~one-in-four detour. Like the parked test, the band is a coarse net; the
    mechanism is pinned by the three tests below.
    """
    _, history = _drive(_sweep, ticks=40 * 600)
    investigate = _share(history, "investigate")
    assert 0.15 <= investigate <= 0.35, f"investigate {investigate:.1%}"
    assert _share(history, "roaming") > 0.6, "roaming must stay the majority"
    assert "talk" not in history, "a cursor in motion is not something to talk at"


@pytest.mark.slow
def test_the_conversation_standoff_is_three_gown_widths():
    """Talk parks 76 sim px from the cursor, not 25 % of the chamber.

    The gown is 25.2 sim px wide at the median (measured over 1200 ticks), and
    the user asked for "三个人偶宽度左右". The old value was
    ``0.25 x safe-rect width`` = 236.5 px on this machine -- which is the
    "it runs away from my mouse" the user reported.
    """
    assert C.TALK_DISTANCE == 76.0
    assert not hasattr(C, "TALK_DISTANCE_RATIO"), "the standoff must not scale with the room"

    sim = SimulationState((0.0, 0.0, 1138.0, 608.0))
    cursor = (569.0, 300.0)
    sim.cursor = cursor
    for _ in range(40 * 120):
        sim.step(0.025)
        if sim.behaviour == "talk":
            break
    assert sim.behaviour == "talk", "the pet never entered talk with a parked cursor"
    assert sim._talk_point is not None
    measured = C.dist(sim._talk_point, cursor)
    assert abs(measured - C.TALK_DISTANCE) < 1.0, f"standoff point {measured:.1f} px"

    # ... and the BODY has to actually walk there. Asserting only the point is
    # asserting the line above that built it (state.py:306) -- the test would
    # pass if the puppet never moved a pixel toward the cursor.
    entry = C.dist(sim.creature.chunk0, cursor)
    for _ in range(40 * 20):
        if sim.behaviour != "talk":
            break
        sim.step(0.025)
    settled = C.dist(sim.creature.chunk0, cursor)
    assert settled < entry - 10.0, (
        f"the body did not approach: {entry:.0f} -> {settled:.0f} px")
    assert abs(settled - C.TALK_DISTANCE) < 40.0, (
        f"the body stands {settled:.0f} px from the cursor, not {C.TALK_DISTANCE:.0f}")


def test_the_selector_does_not_flip_flop():
    """A state must be held for at least BEHAVIOUR_MIN_TICKS."""
    selector = BehaviourSelector()
    held = selector.behaviour
    run = 0
    # Alternate the situation every tick: the shortest possible trigger to flip.
    for tick in range(C.BEHAVIOUR_MIN_TICKS * 4):
        cursor = (BODY[0] + 100.0, BODY[1]) if tick % 2 else None
        speed = selector.observe_cursor(cursor)
        chosen = selector.choose(BODY, cursor, speed)
        if chosen == held:
            run += 1
        else:
            assert run >= C.BEHAVIOUR_MIN_TICKS, f"switched after only {run} ticks"
            held, run = chosen, 1


def test_a_taskbar_trip_does_not_lock_the_pet():
    """Cursor to the taskbar and back: the pet has to look at it again.

    Reported symptom: "鼠标挪到任务栏一会后再挪回来，似乎无法再检测到鼠标，
    一直触发roaming". Two separate things were wrong; this test pins the one that
    made it permanent.

    (a) The 48 px taskbar strip is outside the WINDOW but on the SCREEN. The port
        maps it into the chamber floor (``platform/pet_window.py``), so the cursor
        does not vanish at all. This test still drives the selector with
        ``cursor=None`` for 15 s because that is the harshest form of the same
        situation -- and it is what a *different* monitor or a locked screen looks
        like.
    (b) The score ratchet: 12 s of continuous roaming saturated ``bored``, which
        both maxed roaming's own score (0.20 + 0.20 + 0.70 = 1.10) and multiplied
        talk's by ``(1 - bored)`` = 0, so talk was pinned at exactly 0.00 for the
        rest of the session and nothing could exceed 1.10 + margin. Measured on the
        old code, this test's phase B was 900/900 ticks of roaming with talk 0.000.

    The selector has been rewritten twice since (freshness multiplier -> weighted
    draw), but this stays the regression test for "a long absence must not make
    the next approach impossible".
    """
    cursor = (BODY[0] + 150.0, BODY[1])
    selector = BehaviourSelector()
    for _ in range(40 * 15):                       # 15 s with the pointer away
        selector.observe_cursor(None)
        selector.choose(BODY, None, 0.0)
    assert selector.behaviour == "roaming"

    history = []
    for _ in range(40 * 120):                      # 2 min with it parked nearby
        speed = selector.observe_cursor(cursor)
        history.append(selector.choose(BODY, cursor, speed))
    assert "talk" in history, "the pet never looked at the cursor again"


def test_no_behaviour_locks_in_for_good():
    """Coarse guard: 2 min of a parked cursor cannot end in one state only."""
    cursor = (BODY[0] + 150.0, BODY[1])
    for seed in C.BEHAVIOURS:
        selector = BehaviourSelector()
        selector.behaviour = seed
        seen = set()
        for _ in range(40 * 120):
            speed = selector.observe_cursor(cursor)
            seen.add(selector.choose(BODY, cursor, speed))
        assert len(seen) >= 2, f"{seed} was absorbing: only {seen} in 2 min"


def test_a_conversation_starts_a_one_minute_cooldown():
    """No second conversation within TALK_COOLDOWN_TICKS of the previous one.

    Also asserts the COUNTER, not just the gap: with 450-tick boundaries the
    first eligible draw after a 2400-tick cooldown is at 2700, so a gap-only
    assertion would still pass for a cooldown as short as ~1950 ticks. And a
    self-renewing talk segment never *ends*, so the old gap assertion could not
    fail at all in that mode (it never reached the ``talk_ended`` branch).
    """
    selector = BehaviourSelector()
    previous = None
    talk_started = None
    talk_ended = None
    for tick in range(40 * 900):
        speed = selector.observe_cursor(_parked(tick))
        name = selector.choose(BODY, _parked(tick), speed)
        if name == "talk" and previous != "talk":
            talk_started = tick
            if talk_ended is not None:
                gap = tick - talk_ended
                assert gap >= C.TALK_COOLDOWN_TICKS, (
                    f"a second conversation began {gap} ticks after the last one; "
                    f"the cooldown is {C.TALK_COOLDOWN_TICKS}"
                )
        if previous == "talk" and name != "talk":
            talk_ended = tick
            assert selector.talk_cooldown == C.TALK_COOLDOWN_TICKS, (
                f"leaving talk must arm the full cooldown, got {selector.talk_cooldown}")
            assert selector.weights_for(BODY, _parked(tick), 0.0)["talk"] == 0.0, (
                "talk still has a draw weight on the tick it ended")
        previous = name
    assert talk_started is not None, "no conversation in 15 min of hovering"


def test_the_draw_follows_its_weights():
    """Roulette, not argmax: 1 : 3 weights must produce a 25 / 75 split."""
    selector = BehaviourSelector(seed=7)
    fixed = {"roaming": 1.0, "investigate": 0.0, "talk": 3.0}
    selector.weights_for = lambda *a, **k: dict(fixed)   # type: ignore[method-assign]
    counts = {name: 0 for name in C.BEHAVIOURS}
    draws = 4000
    for _ in range(draws):
        selector._draw(BODY, (BODY[0] + 150.0, BODY[1]), 0.0)
        counts[selector.behaviour] += 1
    talk = counts["talk"] / draws
    assert 0.72 <= talk <= 0.78, f"talk drawn {talk:.1%} of {draws} draws, want 75 %"
    assert counts["investigate"] == 0


def test_both_states_read_the_same_motion_threshold():
    """One line, two sides: "判据统一成 0 px/tick" (user, 2026-10-27).

    It used to be 1 px/tick for "still enough to talk" and >0 for "moving enough
    to investigate", so a 0.5 px/tick drift was BOTH and the scores broke the tie
    on a number nobody can see.
    """
    assert C.SCORE_MOTION_MIN == 0.0
    selector = BehaviourSelector()
    cursor = (BODY[0] + 150.0, BODY[1])
    assert selector.gate("talk", BODY, cursor, 0.0) is True
    assert selector.gate("investigate", BODY, cursor, 0.0) is False
    assert selector.gate("talk", BODY, cursor, 0.001) is False
    assert selector.gate("investigate", BODY, cursor, 0.001) is True

    # ... and the hover counter agrees with it.
    selector.observe_cursor(cursor)
    selector.observe_cursor((cursor[0] + 0.5, cursor[1]))
    assert selector.still_ticks == 0, "a 0.5 px move is not hovering"


def test_talk_gets_more_likely_the_longer_the_cursor_hovers():
    """ "talk 概率随鼠标悬停时间增加而增大" -- a ramp, monotonically."""
    selector = BehaviourSelector()
    cursor = (BODY[0] + 150.0, BODY[1])
    shares = []
    for hover in (0, 300, 1200, 2400, 3600):
        selector.still_ticks = hover
        weights = selector.weights_for(BODY, cursor, 0.0)
        shares.append(weights["talk"] / (weights["talk"] + weights["roaming"]))
    assert shares == sorted(shares), f"not monotone: {shares}"
    assert shares[0] < 0.1, f"talk is not shy right after the cursor stops: {shares[0]:.2f}"
    assert shares[-1] > 0.5, f"talk never becomes likely: {shares[-1]:.2f}"


def test_the_state_and_the_gaze_reach_the_render_state():
    """``RenderState.behaviour`` exists and the puppet looks at the cursor."""
    sim = SimulationState((0.0, 0.0, 776.0, 415.0))
    cursor = (620.0, 180.0)
    sim.cursor = cursor
    for _ in range(600):
        sim.step(0.025)
    state = sim.interpolate(1.0)
    assert state.behaviour in C.BEHAVIOURS
    assert sim.status()["behaviour"] == state.behaviour

    # ``lookDir`` = ClampMagnitude(cursor - chunk0, 100) / 100 (og.cs:1618), so it
    # must point at the cursor from the body.
    body = state.body0
    want = C.dir_vec(body, cursor)
    got = C.dir_vec((0.0, 0.0), state.look_dir)
    assert got[0] * want[0] + got[1] * want[1] > 0.9, "the gaze left the cursor"


def test_a_finished_conversation_must_hand_over():
    """A segment that runs out takes its own mood out of the next draw.

    This is the rule that makes the 60 s cooldown real: if the mood may re-draw
    itself, talk renews past its segment, never "ends", and its cooldown never
    starts (measured on the build without this rule: parked roaming 61.3-72.5 %
    instead of 75.0-76.2 %). Removing the rule makes this test fail on every
    seed, which the rate tests above do not.
    """
    boundary = C.BEHAVIOUR_SEGMENT_TICKS["talk"]
    # EIGHT seeds, not one: without the hand-over rule talk renews itself on
    # roughly a third of its own boundaries (its drained weight is ~0.47 vs
    # roaming's ~1.0), so a single seed passes by luck 68 % of the time -- which
    # is exactly how the audit's substituted bug slipped past a one-seed version.
    for seed in range(1, 9):
        selector = BehaviourSelector(seed=seed)
        for tick in range(40 * 200):
            speed = selector.observe_cursor(_parked(tick))
            if selector.choose(BODY, _parked(tick), speed) == "talk":
                break
        assert selector.behaviour == "talk", f"seed {seed} never entered talk"

        for _ in range(boundary + 5):
            speed = selector.observe_cursor(_parked(0))
            selector.choose(BODY, _parked(0), speed)
            if selector.behaviour != "talk":
                break
        assert selector.behaviour != "talk", (
            f"seed {seed}: talk re-drew itself at its own boundary;"
            " the cooldown never starts")
        assert selector.talk_cooldown > 0


def test_the_draw_cannot_pick_an_excluded_mood():
    """``_draw(exclude=...)`` must never return that mood, gate or not."""
    selector = BehaviourSelector(seed=11)
    fixed = {"roaming": 0.01, "investigate": 0.0, "talk": 10.0}
    selector.weights_for = lambda *a, **k: dict(fixed)     # type: ignore[method-assign]
    counts = {name: 0 for name in C.BEHAVIOURS}
    for _ in range(2000):
        selector._draw(BODY, (BODY[0] + 150.0, BODY[1]), 0.0, exclude="talk")
        counts[selector.behaviour] += 1
    assert counts["talk"] == 0, f"talk was drawn {counts['talk']} times while excluded"
    assert counts["roaming"] == 2000


def test_every_tick_of_a_frame_sees_a_fresh_cursor_sample():
    """The window must sample the cursor per SIM TICK, not once per frame.

    ``FixedStepLoop.advance`` runs up to 8 sub-steps for one frame; the behaviour
    gates are per tick. Sampling per frame made every repeat tick read a speed of
    exactly 0, which closes ``investigate`` and opens ``talk`` -- measured with a
    cursor moving 3 px/frame: investigate 24 % at 1 tick/frame, 2 % at 2+.
    """
    from iterator_pet.platform.pet_window import PetWindow

    order: list[str] = []

    class Fake:
        simulation = type("S", (), {"step": lambda self, dt: order.append("step")})()

        def _track_cursor(self):
            order.append("cursor")

    PetWindow._step_once(Fake(), 1.0 / C.GAME_HZ)
    assert order == ["cursor", "step"], f"wrong order per tick: {order}"

    # ... and the loop must actually be driven with it. Handing ``advance`` the
    # bare ``simulation.step`` is the bug this pins.
    source = inspect.getsource(PetWindow._tick)
    assert "self._step_once" in source, (
        "the fixed-step loop is not driven through _step_once any more")
    assert "self.simulation.step" not in source, (
        "the loop is being handed the raw step again (one cursor sample per frame)")
