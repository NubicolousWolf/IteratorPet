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
        # Clamps a freshly picked destination into what the carriage can serve
        # RIGHT NOW; set by SimulationState.
        self.clamp = None
        # Bumped by every ``SetNewDestination``; the carriage uses it as its
        # ``consistentBasePosCounter`` reset (a new destination restarts the
        # 30-tick stability gate).
        self.version = 0
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
        # Clamped ONCE here, not every tick. Clamping the goal each tick made it
        # slide with the carriage, so the body was dragged along a moving boundary
        # and never arrived: it never rested, the settled gate never fired, and the
        # trunk axis tracked the slide (tilt median 78 deg). A destination fixed
        # at pick time is a point the body can actually reach and stop on.
        if self.clamp is not None:
            dst = self.clamp(dst)
        span = max(1.0, hypot(dst[0] - self.current[0], dst[1] - self.current[1]))
        self.last_handle = self._random_handle(span)
        tx, ty = self.get_to_dir()
        reach = self.rng.uniform(0.3, 0.65) * span
        self.next_handle = (-tx * reach, -ty * reach)
        self.next = dst
        self.progress = 0.0
        self.version += 1

    def _random_handle(self, span: float) -> tuple[float, float]:
        angle = self.rng.uniform(0.0, 6.28318)
        length = self.rng.uniform(0.3, 0.65) * span
        return (cos(angle) * length, sin(angle) * length)

    # -- per-tick ----------------------------------------------------------
    def update(
        self,
        cursor: tuple[float, float] | None = None,
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
            # DWELL before re-targeting. ``SetNewDestination`` is event driven in
            # the source (ss.cs:4290): the iterator arrives, then holds the same
            # destination for a long time. Without the dwell the port re-targeted
            # on the first settled tick, so the puppet travelled ~90 % of its life
            # and its trunk -- whose 9 px hang term is swamped by the 100 px clamp
            # the moment the goal gets far -- lay along the direction of travel
            # (measured: tilt median 72 deg while drifting). A 3 s dwell also
            # reads better: it drifts, it looks around, it drifts again.
            if self.hold < C.WANDER_DWELL_TICKS:
                pass
            elif settled or self.hold >= C.WANDER_HOLD_CAP:
                self.hold = 0
                # The WHOLE safe rect, exactly like the source's
                # ``SetNewDestination(随机房间点)`` + ``ClampVectorInRoom``. The carriage
                # follows the puppet now, so a destination on the far side of a
                # 776 px screen is reachable -- it used to be sampled inside a
                # 260 px disc around the base precisely because it was not.
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
        """``currentGetTo`` -- ``ss.cs:3569``, control points clamped.

            currentGetTo = Custom.Bezier(lastPos, ClampVectorInRoom(lastPos + lastPosHandle),
                                         nextPos, ClampVectorInRoom(nextPos + nextPosHandle),
                                         pathProgression);

        The handles are ``RNV() * Lerp(.3,.65) * dist`` -- a RANDOM direction of up to
        65 % of the distance to the destination -- so an unclamped one throws the
        curve well outside the chamber. Measured with the clamp missing: the body
        reached y = 504 in a 415 px room and the trunk lay flat (tilt median 168
        deg) because it was permanently chasing a target below the screen. A cubic
        Bezier stays inside the convex hull of its control points, so clamping the
        two handles keeps the whole path in the safe rect.
        """
        x, y, w, h = self._safe_rect()

        def clamp(point: tuple[float, float]) -> tuple[float, float]:
            return (min(max(point[0], x), x + w), min(max(point[1], y), y + h))

        return C.bezier(
            self.current,
            clamp((self.current[0] + self.last_handle[0],
                   self.current[1] + self.last_handle[1])),
            self.next,
            clamp((self.next[0] + self.next_handle[0],
                   self.next[1] + self.next_handle[1])),
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


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class BehaviourSelector:
    """``roaming`` / ``investigate`` / ``talk`` chosen by utility score.

    DESIGN ADDITION. The source switches these states from story actions, never
    from a score (see the block comment in ``constants.py``). What IS borrowed
    from the source is the shape of each state: Investigate aims at
    ``cursor + DegToVec(angle) * 150`` with the angle sweeping inside +-70 deg
    (ss.cs:4174-4204), and Talk parks at a fixed distance from the player the way
    ``CommunicatePosScore`` does (ss.cs:4278-4285). Only the selector is ours.
    """

    def __init__(self):
        self.behaviour = "roaming"
        self.ticks = 0
        self.still_ticks = 0
        self._last_cursor: tuple[float, float] | None = None
        # Last computed scores, for the developer panel (and for explaining a
        # switch after the fact). Never read by the simulation itself.
        self.last_scores: dict[str, float] = {}

    # -- inputs ------------------------------------------------------------
    def observe_cursor(self, cursor: tuple[float, float] | None) -> float:
        """Update the stillness counter; return the cursor's speed in px/tick."""
        if cursor is None:
            self.still_ticks = 0
            self._last_cursor = None
            return 0.0
        if self._last_cursor is None:
            speed = 0.0
        else:
            speed = C.dist(cursor, self._last_cursor)
        self._last_cursor = cursor
        if speed < 1.0:
            self.still_ticks += 1
        else:
            self.still_ticks = 0
        return speed

    # -- scoring -----------------------------------------------------------
    def scores(self, body, cursor, cursor_speed: float, talk_distance: float) -> dict[str, float]:
        if cursor is None:
            return {"roaming": 1.0, "investigate": 0.0, "talk": 0.0}
        distance = C.dist(body, cursor)
        near = max(0.0, 1.0 - distance / C.SCORE_NEAR_RANGE)
        motion = min(1.0, cursor_speed / C.SCORE_MOTION_SPEED)
        still = min(1.0, self.still_ticks / C.SCORE_STILL_TICKS)
        # Restlessness: measured from how long the CURRENT state has been held,
        # not from cursor stillness. Keyed on stillness it only opened a 4 s
        # window after the mouse stopped (measured: talk 294/4000 ticks) because
        # ``bored`` hit 1 while the cursor was still parked. "I have been doing
        # this a while, let me go and wander" is the behaviour we actually want.
        bored = min(1.0, max(0.0, (self.ticks - C.SCORE_BORED_TICKS)
                             / float(C.SCORE_BORED_TICKS)))
        # Talking is about the cursor being NEARBY -- the body then travels to the
        # talk distance and holds there -- not about already being at it (that
        # would never trigger: the pet would have to guess the right radius first).
        talk_near = max(0.0, 1.0 - distance / C.SCORE_TALK_RANGE)
        since = min(1.0, self.ticks / C.SCORE_SINCE_TICKS)
        return {
            # Investigate is about MOTION (the source triggers it when the player
            # does something, ss.cs:4174): urgency is proximity + movement, never
            # stillness -- a still cursor belongs to ``talk``, and sharing the
            # stillness term made the two states fight to a near-tie.
            "investigate": (C.W_INVESTIGATE_NEAR * near
                            + C.W_INVESTIGATE_MOTION * motion),
            # Talking needs the cursor to SETTLE; a cursor that is moving around
            # is something to investigate, not to talk at. The stillness factor
            # is multiplicative so a moving cursor kills the talk score outright.
            "talk": (C.W_TALK_DISTANCE * (0.45 + 0.55 * talk_near)
                     * (0.35 + 0.65 * still) * (1.0 - bored)),
            # Roaming wins by default and keeps winning the longer it has been
            # ignored, so the pet never parks on the cursor forever.
            "roaming": (C.W_ROAMING_BASE + C.W_ROAMING_SINCE * since
                        + C.W_ROAMING_BORED * bored),
        }

    def choose(self, body, cursor, cursor_speed: float, talk_distance: float) -> str:
        scores = self.scores(body, cursor, cursor_speed, talk_distance)
        self.last_scores = scores
        if self.ticks < C.BEHAVIOUR_MIN_TICKS:
            self.ticks += 1
            return self.behaviour
        best = max(scores, key=lambda name: scores[name])
        if (best != self.behaviour
                and scores[best] > scores[self.behaviour] + C.BEHAVIOUR_SWITCH_MARGIN):
            self.behaviour = best
            self.ticks = 0
        else:
            self.ticks += 1
        return self.behaviour
