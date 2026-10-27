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


def test_a_parked_cursor_earns_talk():
    """Nearby and motionless is the Talk pose: hold a distance and keep looking."""
    cursor = (BODY[0] + 150.0, BODY[1])
    final, history = _drive(lambda tick: cursor)
    assert final == "talk", f"ended in {final}"
    assert history.count("talk") > 0.5 * len(history)


def test_a_moving_cursor_earns_investigate():
    """Motion is something to investigate -- that is the source's trigger."""
    for amplitude in (40.0, 120.0, 200.0):
        def moving(tick, amplitude=amplitude):
            # A smooth sweep around the pet: always in motion, always nearby.
            return (BODY[0] + amplitude * sin(tick * 0.05), BODY[1])

        final, history = _drive(moving)
        assert final == "investigate", f"amplitude {amplitude} ended in {final}"
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
