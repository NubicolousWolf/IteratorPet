"""``SSOracleBehavior.Move`` reduced to a desktop-sized wandering routine.

The real behaviour scores random room points, walks a cubic Bezier between the
old and new destination, and slowly sweeps ``investigateAngle`` so the body
keeps turning to face different things. Same structure here, just clipped to
the screen's safe area.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import cos, hypot, sin

from . import constants as C


@dataclass
class WanderState:
    current: tuple[float, float]
    next: tuple[float, float]
    last_handle: tuple[float, float]
    next_handle: tuple[float, float]
    progress: float
    angle: float


class Wander:
    """Bezier roaming inside ``bounds`` with a slowly turning heading."""

    def __init__(self, bounds: tuple[float, float, float, float], seed: int = 1234):
        x, y, w, h = bounds
        self.bounds = (x, y, w, h)
        self.margin = 70.0
        self.rng = random.Random(seed)
        cx, cy = x + w * 0.5, y + h * 0.5
        self.current = (cx, cy)
        self.next = (cx, cy)
        self.last_handle = (0.0, 0.0)
        self.next_handle = (0.0, 0.0)
        self.progress = 1.0
        self.follow_target: tuple[float, float] | None = None
        self.hold = 0
        self.look_point = (cx, cy - 40.0)
        self.pick_new_destination()
        self.progress = 1.0
        self.current = self.next

    # -- destinations ------------------------------------------------------
    def _safe_rect(self) -> tuple[float, float, float, float]:
        x, y, w, h = self.bounds
        m = self.margin
        return (x + m, y + m, max(40.0, w - 2 * m), max(40.0, h - 2 * m))

    def pick_new_destination(self) -> None:
        """``SetNewDestination``: sample a point and randomise the handles."""
        x, y, w, h = self._safe_rect()
        dst = (self.rng.uniform(x, x + w), self.rng.uniform(y, y + h))
        self.set_destination(dst)

    def set_destination(self, dst: tuple[float, float]) -> None:
        span = max(1.0, hypot(dst[0] - self.current[0], dst[1] - self.current[1]))
        self.last_handle = self._random_handle(span)
        tx, ty = self.get_to_dir()
        reach = self.rng.uniform(0.3, 0.65) * span
        self.next_handle = (-tx * reach, -ty * reach)
        self.next = dst
        self.progress = 0.0

    def _random_handle(self, span: float) -> tuple[float, float]:
        angle = self.rng.uniform(0.0, 6.28318)
        length = self.rng.uniform(0.3, 0.65) * span
        return (cos(angle) * length, sin(angle) * length)

    # -- per-tick ----------------------------------------------------------
    def update(
        self,
        cursor: tuple[float, float] | None = None,
        base: tuple[float, float] | None = None,
        radius: float = 240.0,
        tangent: tuple[float, float] | None = None,
        settled: bool = True,
    ) -> None:
        span = max(1.0, hypot(self.next[0] - self.current[0], self.next[1] - self.current[1]))
        # pathProgression += 1 / Lerp(40 + p * 80, span / 5, 0.5)
        speed = _lerp(40.0 + self.progress * 80.0, span / 5.0, 0.5)
        self.progress = min(1.0, self.progress + 1.0 / max(1.0, speed))

        if self.follow_target is not None:
            # An explicit "go to this coordinate" command overrides wandering.
            self.next = self.follow_target
            span = max(1.0, hypot(self.follow_target[0] - self.current[0],
                                  self.follow_target[1] - self.current[1]))
            speed = _lerp(40.0 + self.progress * 80.0, span / 5.0, 0.5)
            self.progress = min(1.0, self.progress + 1.0 / max(1.0, speed))
            if self.progress >= 1.0:
                self.current = self.follow_target
            self.look_point = self.follow_target
            return

        if self.progress >= 1.0:
            # ``SetNewDestination`` is event driven in the source (ss.cs:4290):
            # ``currentGetTo`` sits on ``nextPos`` until the base is asked to
            # move again, which only happens once ``consistentBasePosCounter``
            # has climbed past 30. Re-targeting on arrival instead -- which is
            # what this port used to do -- keeps ``getTo`` permanently in flight,
            # and the 6.2 / 3.2 impulse split then trails the trunk along the
            # direction of travel until it lies flat. So: hold the destination
            # until the pose has settled.
            self.current = self.next
            self.hold += 1
            if settled or self.hold >= C.WANDER_HOLD_CAP:
                self.hold = 0
                if base is not None:
                    self.next = _point_near(self.rng, self._safe_rect(), base, radius, tangent)
                    self.last_handle = (0.0, 0.0)
                    self.next_handle = (0.0, 0.0)
                    self.progress = 0.0
                else:
                    self.pick_new_destination()

        # Gaze. ``SSOracleBehavior.lookPoint`` is simply wherever the iterator is
        # paying attention to -- the game has no distance cutoff on it, and the
        # 380 px gate that used to live here meant a cursor parked on the right
        # half of a wide screen was never looked at at all. The only fallback is
        # "no cursor", which is the room's meditate pose: a little above the body.
        look = (self.current[0], self.current[1] - 40.0)
        if cursor is not None:
            look = cursor
        self.look_point = look

    def get_to(self) -> tuple[float, float]:
        return C.bezier(
            self.current,
            (self.current[0] + self.last_handle[0], self.current[1] + self.last_handle[1]),
            self.next,
            (self.next[0] + self.next_handle[0], self.next[1] + self.next_handle[1]),
            self.progress,
        )

    def get_to_dir(self) -> tuple[float, float]:
        """``SSOracleBehavior.GetToDir`` -- the game's default branch only.

        ss.cs:3137-3152 returns ``DegToVec(investigateAngle)`` only while
        ``movementBehavior == Idle`` and its negation while Investigating;
        every other state returns ``new Vector2(0f, 1f)``, i.e. straight up in
        the game's +y-up frame, which is a body hanging straight down. Those
        two rotating branches exist so the SS_AI behaviour can swing Pebbles
        towards an investigate target, and they let the body axis rest at any
        angle. Feeding this port a continuously spinning heading is what put
        the puppet on its side, so this returns the portable branch.
        """
        return C.BODY_HANG_DIR


def _point_near(rng, safe_rect, base, radius: float, tangent=None) -> tuple[float, float]:
    # The iterator never floats outside its arm's reach, so destinations are
    # sampled inside a disc around the rail base instead of the whole screen.
    # BasePosScore in the source scores DISTANCE, never direction, so the
    # destination is sampled over the full circle.
    angle = rng.uniform(0.0, 6.28318)
    distance = radius * (0.45 + 0.55 * rng.random())
    point = (base[0] + cos(angle) * distance, base[1] + sin(angle) * distance)
    x, y, w, h = safe_rect
    return (min(max(point[0], x), x + w), min(max(point[1], y), y + h))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t
