"""Behaviour selection: roaming / investigate / talk.

The selector itself is a DESIGN ADDITION -- the source sets ``movementBehavior``
from story actions (``SeePlayer`` -> KeepDistance, conversation -> Talk) and runs
Idle, which ignores the player, when nothing is happening. What is borrowed is
the *shape* of each state: Investigate aims at ``cursor + DegToVec(angle) * 150``
with the angle sweeping inside +-70 deg (ss.cs:4174-4204), Talk parks a fixed
distance from the player the way ``CommunicatePosScore`` does (ss.cs:4278-4285).

These tests pin the three situations the pet has to tell apart. They are pure
(no simulation) except the last one, so they cannot flake with the physics.
"""

from math import sin

from iterator_pet.simulation import constants as C
from iterator_pet.simulation.behavior import BehaviourSelector
from iterator_pet.simulation.state import SimulationState

BODY = (400.0, 200.0)
TALK_DISTANCE = 194.0


def _drive(cursor_at, ticks: int = 600) -> tuple[str, list[str]]:
    """Run the selector against ``cursor_at(t) -> cursor``."""
    selector = BehaviourSelector()
    history = []
    for tick in range(ticks):
        cursor = cursor_at(tick)
        measured = selector.observe_cursor(cursor)
        history.append(selector.choose(BODY, cursor, measured, TALK_DISTANCE))
    return selector.behaviour, history


def test_no_cursor_means_roaming():
    """A desktop with no mouse is the source's Idle: the pet just wanders."""
    final, history = _drive(lambda tick: None)
    assert final == "roaming"
    assert set(history) == {"roaming"}


def _dominant(history: list[str]) -> str:
    """The state that holds the most ticks.

    "Which state was active on tick 600" is a phase sample of a limit cycle, not
    a verdict: with freshness in play the selector runs a cycle, and 600 ticks is
    not a multiple of its period. Measured over 600 ticks: parked cursor ->
    talk 439 / roam 161; sweeping cursor (amplitude 40) -> investigate 381 /
    talk 139 / roam 80. The intent of these two tests is "this situation makes
    that state WIN", which is a rate, so it is measured as one.
    """
    counts: dict[str, int] = {}
    for name in history:
        counts[name] = counts.get(name, 0) + 1
    return max(counts, key=lambda name: counts[name])


def test_a_parked_cursor_earns_talk():
    """Nearby and motionless is the Talk pose: hold a distance and keep looking."""
    cursor = (BODY[0] + 150.0, BODY[1])
    final, history = _drive(lambda tick: cursor)
    assert _dominant(history) == "talk", f"ended in {final}; mix={sorted(history)}"
    assert history.count("talk") > 0.5 * len(history)


def test_a_moving_cursor_earns_investigate():
    """Motion is something to investigate -- that is the source's trigger."""
    for amplitude in (40.0, 120.0, 200.0):
        def moving(tick, amplitude=amplitude):
            # A smooth sweep around the pet: always in motion, always nearby.
            return (BODY[0] + amplitude * sin(tick * 0.05), BODY[1])

        final, history = _drive(moving)
        assert _dominant(history) == "investigate", f"amplitude {amplitude} ended in {final}"
        assert history.count("investigate") > 0.5 * len(history)


def test_the_selector_does_not_flip_flop():
    """A state must be held for at least BEHAVIOUR_MIN_TICKS."""
    selector = BehaviourSelector()
    held = selector.behaviour
    run = 0
    # Alternate the situation every tick: the shortest possible trigger to flip.
    for tick in range(C.BEHAVIOUR_MIN_TICKS * 4):
        cursor = (BODY[0] + 100.0, BODY[1]) if tick % 2 else None
        speed = selector.observe_cursor(cursor)
        chosen = selector.choose(BODY, cursor, speed, TALK_DISTANCE)
        if chosen == held:
            run += 1
        else:
            assert run >= C.BEHAVIOUR_MIN_TICKS, f"switched after only {run} ticks"
            held, run = chosen, 1


def test_a_taskbar_trip_does_not_lock_the_pet():
    """Cursor to the taskbar and back: the pet has to look at it again.

    Reported symptom: "鼠标挪到任务栏一会后再挪回来，似乎无法再检测到鼠标，
    一直触发roaming". The window is ``availableGeometry()``, so a pointer in the
    taskbar strip does read as "no cursor" -- that part is intended. What was not
    was the score ratchet: 12 s of continuous roaming saturated ``bored``, which
    both maxed roaming's own score (0.20 + 0.20 + 0.70 = 1.10) and multiplied
    talk's by ``(1 - bored)`` = 0, so talk was pinned at exactly 0.00 for the rest
    of the session and nothing could exceed 1.10 + margin. Measured: roaming
    60 s out of 60, talk 0.00 throughout.
    """
    cursor = (BODY[0] + 150.0, BODY[1])
    selector = BehaviourSelector()
    for _ in range(40 * 15):                       # 15 s with the pointer away
        selector.observe_cursor(None)
        selector.choose(BODY, None, 0.0, TALK_DISTANCE)
    assert selector.behaviour == "roaming"

    history = []
    for _ in range(40 * 30):                       # 30 s with it parked nearby
        speed = selector.observe_cursor(cursor)
        history.append(selector.choose(BODY, cursor, speed, TALK_DISTANCE))
    assert "talk" in history, "the pet never looked at the cursor again"


def test_no_behaviour_locks_in_for_good():
    """Guard: whatever the pet is doing, 60 s of a parked cursor changes its mind.

    This is the invariant the old ``bored`` ratchet broke for roaming. It is a
    guard rather than the red-first reproduction (that is the taskbar test) --
    investigate and talk were escapable by accident, through the opposite term.
    """
    cursor = (BODY[0] + 150.0, BODY[1])
    for seed in C.BEHAVIOURS:
        selector = BehaviourSelector()
        selector.behaviour = seed
        seen = set()
        for _ in range(40 * 60):
            speed = selector.observe_cursor(cursor)
            seen.add(selector.choose(BODY, cursor, speed, TALK_DISTANCE))
        assert len(seen) >= 2, f"{seed} was absorbing: only {seen} in 60 s"


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
