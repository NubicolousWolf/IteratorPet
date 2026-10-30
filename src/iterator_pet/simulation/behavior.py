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
    """``roaming`` / ``investigate`` / ``talk`` chosen by a weighted draw.

    DESIGN ADDITION. The source switches these states from story actions, never
    from a score (see the block comment in ``constants.py``). What IS borrowed
    from the source is the shape of each state: Investigate aims at
    ``cursor + DegToVec(angle) * 150`` with the angle sweeping inside +-70 deg
    (ss.cs:4174-4204), and Talk parks at a fixed distance from the player the way
    ``CommunicatePosScore`` does (ss.cs:4278-4285). Only the selector is ours.

    WHY A DRAW AND NOT AN ARGMAX (2026-10-27, user's item 4). "roaming should
    stay the most likely behaviour even with a mouse on screen" cannot be
    expressed by taking the maximum of three numbers: a base score big enough to
    win most ticks wins EVERY tick, and the state the user actually saw -- a
    parked cursor producing 68 % talk -- is the other side of the same coin. So
    the selector borrows slugcatpet's ``MoodArbiter`` (``brain/desire.py``): each
    mood has a draw weight, a segment has a length, and the next mood is drawn by
    roulette. Freshness survives, but as a multiplier that only ever DAMPS the
    mood that has just been running, so no state can make itself stickier by
    being held (the ratchet in AGENTS 6.4 stays structurally impossible).

    The one deterministic rule is ``BEHAVIOUR_MIN_TICKS``: a draw happens only at
    a segment boundary, and no boundary may land sooner than 2 s, otherwise the
    pose stutters every time the mouse twitches.
    """

    def __init__(self, seed: int = 20261027):
        self.behaviour = "roaming"
        self.ticks = 0
        # LLM 动作强切的剩余 tick：>0 时 choose() 不改写 behaviour（段末照常回抽签）
        self.forced = 0
        self.still_ticks = 0
        self._last_cursor: tuple[float, float] | None = None
        # Freshness: 1 = keen, 0 = fed up. Decays while the mood is active,
        # recovers while it is not, and multiplies that mood's draw weight.
        self.freshness: dict[str, float] = {name: 1.0 for name in C.BEHAVIOURS}
        # "暂定为一分钟冷却": ticks until talk may be drawn again.
        self.talk_cooldown = 0
        self.rng = random.Random(seed)
        # Diagnostics, for the developer panel and for explaining a draw after
        # the fact. Never read by the simulation itself.
        self.weights: dict[str, float] = {}
        self.eligible: tuple[str, ...] = ()
        self.draws = 0
        # The mood the last draw was not allowed to pick (the hand-over rule).
        # Shown in the panel: without it a gated mood and an excluded mood both
        # read as weight 0.00 and the two mean opposite things.
        self.excluded: str | None = None

    # -- inputs ------------------------------------------------------------
    def observe_cursor(self, cursor: tuple[float, float] | None) -> float:
        """Update the hover counter; return the cursor's speed in px/tick."""
        if cursor is None:
            self.still_ticks = 0
            self._last_cursor = None
            return 0.0
        if self._last_cursor is None:
            speed = 0.0
        else:
            speed = C.dist(cursor, self._last_cursor)
        self._last_cursor = cursor
        # ONE threshold for both states (user: "判据统一成 0 px/tick"). A cursor
        # that moved at all is moving; a cursor that did not move at all is
        # hovering. There is no band in which both are true.
        if speed <= C.SCORE_MOTION_MIN:
            self.still_ticks += 1
        else:
            # DECAY, not reset. The gate is still the single 0 px/tick line the
            # user asked for, but the hover RAMP behind talk's weight is a 90 s
            # quantity: resetting it on one optical-mouse count (a 0.01 px jitter)
            # threw away the whole warm-up, so a hand resting on a mouse never
            # warmed up at all (measured: 5 Hz x 0.01 px -> talk 3.9 % of the
            # time instead of 25 %). Losing ~1 s per jitter is the compromise.
            self.still_ticks = max(0, self.still_ticks - C.STILL_DECAY_TICKS)
        return speed

    # -- gates -------------------------------------------------------------
    def gate(self, name: str, body, cursor, cursor_speed: float) -> bool:
        """Can this mood start a segment right now?"""
        if name not in C.BEHAVIOURS:
            raise KeyError(name)
        if cursor is None:
            # The source's Idle: with no player, ``Move()`` (ss.cs:4110) ignores
            # everything but the marbles. Roaming is the only portable state.
            return name == "roaming"
        if name == "roaming":
            return True
        if name == "investigate":
            # ss.cs:4174 -- investigate is triggered by the player doing
            # something. A cursor that has not moved this tick is not something
            # to inspect; it is something to talk at.
            return cursor_speed > C.SCORE_MOTION_MIN
        # talk
        return (cursor_speed <= C.SCORE_MOTION_MIN
                and self.talk_cooldown <= 0
                and C.dist(body, cursor) <= C.SCORE_TALK_RANGE)

    def talk_gain(self) -> float:
        """"talk 概率随鼠标悬停时间增加而增大" -- a ramp, not a threshold."""
        ramp = min(1.0, self.still_ticks / C.TALK_HOVER_FULL_TICKS)
        return C.TALK_GAIN_MIN + (C.TALK_GAIN_MAX - C.TALK_GAIN_MIN) * ramp

    # -- freshness / cooldowns --------------------------------------------
    def tick_freshness(self) -> None:
        """Drain the active mood's freshness, recover every other one."""
        for name in C.BEHAVIOURS:
            if name == self.behaviour:
                self.freshness[name] = max(
                    0.0, self.freshness[name] - C.SCORE_FRESHNESS_DECAY)
            else:
                self.freshness[name] = min(
                    1.0, self.freshness[name] + C.SCORE_FRESHNESS_RECOVER)
        if self.talk_cooldown > 0:
            self.talk_cooldown -= 1

    # -- the draw ----------------------------------------------------------
    def weights_for(self, body, cursor, cursor_speed: float) -> dict[str, float]:
        """Draw weight per mood, zero for the ones whose gate is shut.

        ``freshness`` damps the weight but never zeroes it (``FRESHNESS_FLOOR``):
        the only thing that can forbid a mood is its gate. Ordering matters --
        without the floor the mood that just used up its segment is at 0.06 and
        its rival at 1.0, so every boundary is decided by the durations alone and
        the draw is decoration.
        """
        weights = {}
        for name in C.BEHAVIOURS:
            if not self.gate(name, body, cursor, cursor_speed):
                weights[name] = 0.0
                continue
            gain = self.talk_gain() if name == "talk" else 1.0
            damp = (C.FRESHNESS_FLOOR
                    + (1.0 - C.FRESHNESS_FLOOR) * self.freshness[name])
            weights[name] = C.BEHAVIOUR_BASE[name] * gain * damp
        return weights

    def _draw(self, body, cursor, cursor_speed: float, exclude: str | None = None) -> None:
        """Roulette over the eligible moods; the one that just ended hands over.

        ``exclude`` is the mood whose segment has just run out. Without it a mood
        could re-draw itself and the segment length would be a lie: on the build
        with an 800-tick talk segment, talk ran 800 / 1600 / 2400 ticks, and
        because the 60 s cooldown is only started when something ELSE is drawn, a
        self-renewing conversation never started its cooldown at all -- talk then
        took 28.7 % of a parked run on seed 20261027 (8 seeds 26.2-37.8 %). On the
        shipped 900-tick segment the same hole gives 30.0 % / 27.5-38.8 %, i.e.
        more talk than the target on every seed.
        Roaming is exempt -- it is the source's Idle, the state the pet is in when
        there is nothing else to do, and it has to be able to simply continue.
        """
        weights = self.weights_for(body, cursor, cursor_speed)
        if exclude is not None and exclude != "roaming":
            weights[exclude] = 0.0
        eligible = tuple(name for name in C.BEHAVIOURS if weights[name] > 0.0)
        winner = None
        total = sum(weights[name] for name in eligible)
        if eligible and total > 0.0:
            roll = self.rng.random() * total
            acc = 0.0
            for name in eligible:
                acc += weights[name]
                if roll <= acc:
                    winner = name
                    break
            if winner is None:
                winner = eligible[-1]
        if winner is None:
            # Defensive only: ``gate("roaming")`` is True with and without a
            # cursor and its weight is floored at FRESHNESS_FLOOR, so ``eligible``
            # is never empty and ``total`` is never 0 (checked over 4 cursor
            # modes x 12000 ticks: 0 occurrences). If it ever fires, the source's
            # Idle is the resting state.
            winner = "roaming"
        self.weights = weights
        self.eligible = eligible
        self.excluded = exclude if exclude in eligible else None
        self.draws += 1
        if self.behaviour == "talk" and winner != "talk":
            self.talk_cooldown = C.TALK_COOLDOWN_TICKS
        self.behaviour = winner
        self.ticks = 1

    def _segment_ended(self, body, cursor, cursor_speed: float) -> bool:
        if not self.gate(self.behaviour, body, cursor, cursor_speed):
            return True
        return self.ticks >= C.BEHAVIOUR_SEGMENT_TICKS[self.behaviour]

    # -- per tick ----------------------------------------------------------
    def force_segment(self, kind: str, ticks: int) -> bool:
        """外部（LLM 的 ``act``）强切一段行为。返回 False = 这个行为现在不能进。

        **段内 `choose()` 不改写**（否则动作活不过一个 tick），段末照常回抽签；
        抽签时"刚结束的那个"会被排除（老规则），所以强切的行为不会自我续约。
        闸门（光标、距离、冷却）仍然生效：闸门一关就当场交还给抽签。
        """
        if kind not in C.BEHAVIOURS:
            return False
        self.behaviour = kind
        self.ticks = 1
        self.forced = max(1, int(ticks))
        self.excluded = None
        return True

    def choose(self, body, cursor, cursor_speed: float) -> str:
        self.tick_freshness()
        self.weights = self.weights_for(body, cursor, cursor_speed)
        self.eligible = tuple(name for name in C.BEHAVIOURS
                              if self.weights[name] > 0.0)
        if self.forced > 0:
            # 强切段只看"光标还在不在"，**不看"动了没有"**：这一段是模型要求的，
            # 不是抽签决定的。用抽签那套动/静判据会把 investigate 在光标一停时
            # 立刻掐掉，动作等于没发生。光标离屏（None）就当场交还抽签。
            if cursor is None:
                self.forced = 0
            else:
                self.forced -= 1
                self.ticks += 1
                self.excluded = None
                return self.behaviour
        if self.ticks < C.BEHAVIOUR_MIN_TICKS:
            self.ticks += 1
            return self.behaviour
        if self._segment_ended(body, cursor, cursor_speed):
            self._draw(body, cursor, cursor_speed, exclude=self.behaviour)
            # ``_draw`` zeroes the excluded mood so the roulette cannot pick it.
            # Publish what the NEXT draw would weigh instead, and name the
            # exclusion separately -- otherwise the panel shows an eligible mood
            # at 0.00, which everywhere else means "gate shut".
            self.weights = self.weights_for(body, cursor, cursor_speed)
        else:
            self.ticks += 1
            self.excluded = None
        return self.behaviour
