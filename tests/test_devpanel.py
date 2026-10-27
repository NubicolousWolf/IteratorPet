"""The developer panel's text, and the ``status()`` keys it is built from.

The panel is a human-facing tool, so the thing worth testing is not its pixels
but its CONTENT: does it actually surface the numbers a developer needs to
explain the pet's behaviour, and does it flag the ones that are out of bounds?

``dev_lines`` is deliberately pure (no Qt window), so these tests are fast and
cannot flake with the physics.
"""

from iterator_pet.platform.devpanel import dev_lines
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState


def _sim(ticks: int = 400, cursor=None) -> SimulationState:
    sim = SimulationState((0.0, 0.0, 776.0, 415.0))
    sim.cursor = cursor
    for _ in range(ticks):
        sim.step(0.025)
    return sim


def test_status_reports_everything_the_panel_needs():
    """The key set is a contract: ``dev_lines`` formats exactly these."""
    sim = _sim()
    status = sim.status()
    for key in (
        "ticks", "time", "behaviour", "scores", "cursor", "still_ticks",
        "frame_pos", "station_edge", "base_moving", "start_threshold",
        "stop_threshold", "base_body", "reach", "reach_ratio", "body_speed",
        "tilt_deg", "goal", "goal_distance", "dest", "progress",
        "arm_stretch", "cord_nodes", "cord_stretch", "cord_stretch_free",
        "filament_worst", "finite", "arm_ok",
    ):
        assert key in status, f"status() lost {key!r}"
    assert status["behaviour"] in C.BEHAVIOURS
    assert status["reach"] == sum(C.ARM_LENGTHS)
    assert 0.0 <= status["reach_ratio"] < 2.0


def test_the_panel_shows_the_behaviour_scores():
    """A switch must be explainable: all three scores, with the winner named."""
    sim = _sim(600, cursor=(388.0, 207.0))
    text = "\n".join(dev_lines(sim))
    assert "behaviour" in text
    assert sim.behaviour in text
    assert "roam" in text and "inve" in text and "talk" in text
    scores = sim.status()["scores"]
    assert len(scores) == 3
    for name, value in sim.selector.last_scores.items():
        assert abs(scores[name] - value) < 5e-4, "status() must carry the live scores"


def test_the_panel_reports_the_goal_the_pet_is_actually_chasing():
    """During investigate the goal is the cursor, not the stale wander target.

    An earlier version printed ``dest``/``progress``, so the panel read
    "dist 297 progress 1.00" while the pet was circling the cursor -- a dev tool
    that lies is worse than no dev tool.
    """
    sim = _sim(600, cursor=(388.0, 207.0))
    status = sim.status()
    if sim.behaviour == "roaming":
        assert status["progress"] is not None
    else:
        assert status["progress"] is None, "a behaviour goal has no pathProgression"
        assert status["goal_distance"] < 500.0
    assert "goal" in "\n".join(dev_lines(sim))


def test_the_panel_flags_an_out_of_reach_body():
    """``reach_ratio > 1`` means the arm is being pulled past its length."""
    sim = _sim()
    lines = dev_lines(sim)
    status = sim.status()
    flagged = any(line.rstrip().endswith("!") for line in lines)
    assert flagged == (status["reach_ratio"] > 1.0
                       or status["filament_worst"] > 3.0
                       or not status["finite"])


def test_the_panel_is_readable_text_of_a_bounded_size():
    lines = dev_lines(_sim())
    assert 10 <= len(lines) <= 24
    assert max(len(line) for line in lines) <= 64, "lines must fit a 430 px panel"
    assert all(isinstance(line, str) for line in lines)
