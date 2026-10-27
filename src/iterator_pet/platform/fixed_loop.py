"""Fixed-step accumulator with Rain World's native 40 TPS cadence.

Rain World runs its gameplay at exactly 40 updates per second, and every
constant in ``Oracle``/``OracleGraphics`` is expressed *per update* (``vel *=
0.4``, ``1°`` of rail per tick, ...). Running at the same rate lets the port
copy those numbers verbatim instead of re-deriving them per second, which is
where the earlier frame-rate conversion drifted.

Rendering happens at whatever the display does (60/120 Hz); the leftover
accumulator is exposed as ``alpha`` and used to interpolate between the last
two physics states.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

GAME_HZ = 40.0
GAME_DT = 1.0 / GAME_HZ
# Eight 40 Hz ticks = 200 ms of catch-up before we start dropping time.
DEFAULT_MAX_SUBSTEPS = 8
MAX_FRAME_CATCHUP = 0.25


@dataclass
class FixedStepLoop:
    dt: float = GAME_DT
    max_substeps: int = DEFAULT_MAX_SUBSTEPS
    accumulator: float = 0.0
    paused: bool = False
    dropped_time: float = 0.0
    steps_total: int = 0

    def advance(self, real_dt: float, step: Callable[[float], None]) -> int:
        if self.paused:
            return 0
        real_dt = max(0.0, real_dt)
        capped_dt = min(real_dt, MAX_FRAME_CATCHUP)
        self.dropped_time += real_dt - capped_dt
        self.accumulator += capped_dt
        count = 0
        # 1e-9 absorbs float drift so a 40 Hz tick is not skipped by 1e-16.
        while self.accumulator + 1e-9 >= self.dt and count < self.max_substeps:
            step(self.dt)
            self.accumulator -= self.dt
            count += 1
        if self.accumulator >= self.dt:
            self.dropped_time += self.accumulator
            self.accumulator = 0.0
        if self.accumulator < 0.0:
            self.accumulator = 0.0
        self.steps_total += count
        return count

    @property
    def alpha(self) -> float:
        """Fraction between the previous and current physics state, 0..1."""
        if self.dt <= 0.0:
            return 0.0
        return max(0.0, min(1.0, self.accumulator / self.dt))

    @property
    def seconds_simulated(self) -> float:
        return self.steps_total * self.dt

    def pause(self) -> None:
        self.paused = True
        self.accumulator = 0.0

    def resume(self) -> None:
        self.paused = False
        self.accumulator = 0.0
