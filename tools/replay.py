"""Deterministic replay: advance the simulation at a fixed render rate and dump
per-second samples. Used by tests and for cross-frame-rate comparisons."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iterator_pet.platform.fixed_loop import FixedStepLoop
from iterator_pet.simulation.state import SimulationState


def run(seed: int = 1234, seconds: float = 60.0, fps: int = 60) -> list[dict]:
    del seed  # the simulation is fully deterministic and needs no seed
    sim = SimulationState((0, 0, 1280, 720))
    loop = FixedStepLoop()
    samples: list[dict] = []
    frames = int(seconds * fps)
    render_dt = 1.0 / fps
    for i in range(frames):
        loop.advance(render_dt, sim.step)
        if (i + 1) % max(1, fps) == 0:
            state = sim.interpolate(1.0)
            cable = sim.cable.validate()
            samples.append(
                {
                    "t": (i + 1) * render_dt,
                    "s": sim.s,
                    "carriage": [round(v, 3) for v in state.carriage],
                    "puppet": [round(v, 3) for v in state.puppet.head],
                    "angle": round(state.puppet.angle, 4),
                    "cable_valid": cable.valid,
                    "cable_stretch": round(cable.max_stretch, 4),
                    "arm_valid": sim.arm.validate().valid,
                }
            )
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--out", default="replay.json")
    args = parser.parse_args(argv)

    data = run(args.seed, args.seconds, args.fps)
    payload = {"seed": args.seed, "seconds": args.seconds, "fps": args.fps, "samples": data}
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    ok = all(x["cable_valid"] and x["arm_valid"] for x in data)
    worst = max((x["cable_stretch"] for x in data), default=0.0)
    print(
        f"wrote {args.out}: {len(data)} samples; "
        f"cable_valid={all(x['cable_valid'] for x in data)}; "
        f"arm_valid={all(x['arm_valid'] for x in data)}; max_stretch={worst:.3f}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
