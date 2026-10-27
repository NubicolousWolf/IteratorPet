"""Dependency-free smoke test: run the simulation and assert it stays sane."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iterator_pet.platform.fixed_loop import GAME_DT, FixedStepLoop
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState


def main() -> int:
    loop = FixedStepLoop()
    assert loop.dt == GAME_DT

    sim = SimulationState((0.0, 0.0, 853.0, 480.0))
    worst_stretch = 0.0
    for _ in range(int(60.0 / GAME_DT)):
        loop.advance(1.0 / 60.0, lambda _dt: sim.step(_dt))
        status = sim.status()
        worst_stretch = max(worst_stretch, status["cord_stretch"])
        assert status["finite"], status

    state = sim.interpolate(1.0)
    body_to_tail = C.dist(state.joints[-1], state.body0)
    report = sim.status()
    report["worst_cord_stretch"] = round(worst_stretch, 3)
    report["tail_gap"] = round(body_to_tail, 2)
    print("selftest ok:", report)
    assert report["arm_ok"], report
    assert abs(body_to_tail - C.ARM_LENGTHS[-1]) < 6.0, report
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
