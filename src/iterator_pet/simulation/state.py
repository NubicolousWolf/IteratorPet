"""Simulation root: everything the renderer needs, in game pixels.

Tick order mirrors ``Oracle.Update``:

1. behaviour picks a Bezier goal and a gaze point,
2. the arm drives the two body chunks (clamped impulses, no gravity),
3. the creature integrates its head/hands/feet,
4. the base decides whether to slide along the rail (hysteresis + 1°/tick),
5. ornaments (cord, gown, halo) follow.

The renderer interpolates between the previous and current tick with ``alpha``,
exactly like the game's ``timeStacker``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, hypot, radians, sin

from . import constants as C
from .arm import ArmChain
from .behavior import BehaviourSelector, Wander
from .cord import Cord
from .creature import Creature
from .ornament import Gown, Halo, HaloBit
from .rail import RailPath, move_towards_angle


@dataclass
class RenderState:
    base_point: tuple[float, float]
    base_dir: tuple[float, float]
    frame_pos: float
    base_moving: bool
    joints: list[tuple[float, float]]
    cog_rot: list[float]
    body0: tuple[float, float]
    body1: tuple[float, float]
    head: tuple[float, float]
    hands: list[tuple[float, float]]
    feet: list[tuple[float, float]]
    look_dir: tuple[float, float]
    cord: list[tuple[float, float]]
    filaments: list[list[tuple[float, float]]]
    filament_colours: list[str]
    gown: list[list[tuple[float, float]]]
    gown_divs: int
    halo_center: tuple[float, float]
    halo_radii: list[float]
    halo_bits: list[tuple[int, tuple[float, float], tuple[float, float], float]]
    halo_connections: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], float]]
    cursor: tuple[float, float] | None
    behaviour: str
    time: float
    ticks: int

    def lerp(self, other: "RenderState", alpha: float) -> "RenderState":
        if alpha <= 0.0:
            return self
        if alpha >= 1.0:
            return other

        def mix(a, b):
            return (a[0] + (b[0] - a[0]) * alpha, a[1] + (b[1] - a[1]) * alpha)

        def mix_list(a, b):
            if len(a) != len(b):
                return a
            return [mix(p, q) for p, q in zip(a, b)]

        gown = [mix_list(row_a, row_b) for row_a, row_b in zip(self.gown, other.gown)]
        filaments = [
            mix_list(a, b) for a, b in zip(self.filaments, other.filaments)
        ]
        return RenderState(
            base_point=mix(self.base_point, other.base_point),
            base_dir=mix(self.base_dir, other.base_dir),
            frame_pos=self.frame_pos + (other.frame_pos - self.frame_pos) * alpha,
            base_moving=other.base_moving,
            joints=mix_list(self.joints, other.joints),
            cog_rot=[a + (b - a) * alpha for a, b in zip(self.cog_rot, other.cog_rot)],
            body0=mix(self.body0, other.body0),
            body1=mix(self.body1, other.body1),
            head=mix(self.head, other.head),
            hands=mix_list(self.hands, other.hands),
            feet=mix_list(self.feet, other.feet),
            look_dir=mix(self.look_dir, other.look_dir),
            cord=mix_list(self.cord, other.cord),
            filaments=filaments,
            filament_colours=self.filament_colours,
            gown=gown,
            gown_divs=self.gown_divs,
            halo_center=mix(self.halo_center, other.halo_center),
            halo_radii=list(other.halo_radii),
            halo_bits=other.halo_bits,
            halo_connections=other.halo_connections,
            cursor=other.cursor,
            behaviour=other.behaviour,
            time=other.time,
            ticks=other.ticks,
        )


class SimulationState:
    def __init__(self, bounds: tuple[float, float, float, float], inset: float = 26.0, seed: int = 1234):
        self.inset = inset
        self._bounds = bounds
        self.rail = RailPath(self._inset())
        self.wander = Wander(self._inset(), seed=seed)
        self.wander.clamp = self._clamp_destination
        # Destinations are pulled into the arm's reach of their nearest rail
        # station, so a point on the far side of a wide screen does not leave the
        # body leaning on the reach limit for the whole hop.

        # ``baseIdeal`` is a ROOM POINT -- the puppet's own position -- which is
        # then projected onto the rail. ``start/stop_threshold`` are the source's
        # 350/50 px hysteresis scaled to this chamber (see constants).
        self.base_ideal = self.wander.current
        self.body_target: tuple[float, float] | None = None
        self.drag_mode: str | None = None
        self.rng = __import__("random").Random(seed)
        self.drag_point: tuple[float, float] | None = None
        self._refresh_thresholds()
        self.frame_pos = self.rail.project(self.wander.current)
        self.base_moving = False
        self.consistent_base_pos = 0
        self._dest_version = self.wander.version
        self._last_chunk0 = self.wander.current
        self.last_body_speed = 0.0
        self.last_goal = self.wander.current

        base_pose = self.rail.pose(self.frame_pos)
        self.arm = ArmChain(base_pose.point)
        self.creature = Creature(self.wander.current)
        self.cord = Cord(self._inset(), self.creature.chunk0)
        self.gown = Gown(self.creature.chunk0, self.creature.body_dir)
        self.halo = Halo(seed=seed)

        self.selector = BehaviourSelector()
        self.behaviour = "roaming"
        self.investigate_angle = 0.0
        self.investigate_speed = C.INVESTIGATE_ANGLE_SPEED_MIN
        self._talk_point: tuple[float, float] | None = None
        self._talk_anchor: tuple[float, float] | None = None
        self.cursor: tuple[float, float] | None = None
        self.time = 0.0
        self.ticks = 0
        self._prev: RenderState | None = None
        self._cur = self._compose()

        for _ in range(120):
            self._advance()

    # -- geometry ----------------------------------------------------------
    def _inset(self) -> tuple[float, float, float, float]:
        x, y, w, h = self._bounds
        inset = self.inset
        return (x + inset, y + inset, max(40.0, w - 2 * inset), max(40.0, h - 2 * inset))

    def _refresh_thresholds(self) -> None:
        # oracle.cs:327 hard-codes 350 / 50 px for a 1400 px wide room; here they
        # are ratios of the INSET chamber diagonal (724x363 -> 810 on a 776x415
        # chamber, i.e. 243 / 29 px; the outer diagonal 882 would give 264 / 32).
        #
        # The start threshold is CAPPED by the destination clamp radius, and the
        # reason is structural: ``Wander.set_destination`` pulls every destination
        # inside ``DESTINATION_REACH_RATIO * ARM_LENGTHS_TOTAL`` (353 px), so a
        # start threshold near or above that radius is crossed only by luck. At
        # this machine's scale the uncapped value is 0.30 * 1220 = 366 px against
        # that 353 px radius, and the carriage measured 2 % moving ticks / 4 px/s
        # / 53 % x coverage, versus 31 % / 42 px/s / 73 % in the documented
        # 776x415 chamber, where 243 px < 353 px does not conflict. (It is not
        # literally zero: the body overshoots the destination clamp, so the old
        # code moved on 2 % of ticks and reached 383 px from the carriage once.)
        _, _, w, h = self._inset()
        span = hypot(w, h)
        reach_radius = C.DESTINATION_REACH_RATIO * C.ARM_LENGTHS_TOTAL
        self.start_threshold = min(
            C.BASE_MOVE_START_RATIO * span,
            C.BASE_MOVE_START_REACH_RATIO * reach_radius,
        )
        self.stop_threshold = C.BASE_MOVE_STOP_RATIO * span

    def resize(self, bounds: tuple[float, float, float, float]) -> None:
        self._bounds = bounds
        inner = self._inset()
        self.rail = RailPath(inner)
        self.wander.bounds = inner
        self._refresh_thresholds()
        self.frame_pos = self.rail.project(self.creature.chunk0)
        self._cur = self._compose()

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """The chamber in SIMULATION pixels.

        ``PetWindow`` builds it from ``QScreen.availableGeometry() / config.scale``
        -- the WORKING AREA, not the whole screen (this machine: 1707x912 / 1.5 =
        1138x608; the screen itself is 1707x960 and the 48 px taskbar strip is
        excluded). Everything the simulation stores -- body positions,
        destinations, and the cursor from ``platform/pet_window.py`` -- lives in
        this frame.
        """
        return self._bounds

    # -- per tick ----------------------------------------------------------
    # -- external control (future drag & "move to point" features) ----------
    def set_body_target(self, point: tuple[float, float] | None) -> None:
        """Ask the puppet to travel to a coordinate (None resumes wandering)."""
        self.body_target = point
        self.wander.follow_target = point
        if point is not None:
            self.wander.set_destination(point)

    def begin_drag(self, mode: str = "puppet") -> None:
        self.drag_mode = mode
        self.drag_point = None

    def drag_to(self, point: tuple[float, float]) -> None:
        self.drag_point = point

    def end_drag(self) -> None:
        self.drag_mode = None
        self.drag_point = None

    def _apply_control(self) -> None:
        if self.drag_mode == "base":
            if self.drag_point is not None:
                self.frame_pos = self.rail.project(self.drag_point)
                self.base_moving = False
                self.base_ideal = self.drag_point
            return
        if self.drag_mode == "puppet" and self.drag_point is not None:
            target = self.drag_point
            delta = (target[0] - self.creature.chunk0[0], target[1] - self.creature.chunk0[1])
            self._translate_creature(delta)
            for attribute in ("vel0", "vel1", "head_vel"):
                setattr(self.creature, attribute, (0.0, 0.0))
            self.creature.hand_vel = [(0.0, 0.0), (0.0, 0.0)]
            self.creature.foot_vel = [(0.0, 0.0), (0.0, 0.0)]

    def _translate_creature(self, delta: tuple[float, float]) -> None:
        c = self.creature
        c.chunk0 = (c.chunk0[0] + delta[0], c.chunk0[1] + delta[1])
        c.chunk1 = (c.chunk1[0] + delta[0], c.chunk1[1] + delta[1])
        c.head = (c.head[0] + delta[0], c.head[1] + delta[1])
        c.hands = [(h[0] + delta[0], h[1] + delta[1]) for h in c.hands]
        c.feet = [(f[0] + delta[0], f[1] + delta[1]) for f in c.feet]

    def _advance(self) -> None:
        self.ticks += 1
        self.time = self.ticks / C.GAME_HZ

        self._apply_control()
        pose_now = self.rail.pose(self.frame_pos)
        # ``consistentBasePosCounter`` gate: the source only asks for a new
        # destination once the assembly is settled, so the same test decides
        # whether the wander may start its next drift.
        # ``settled`` decides when the wander may pick a new destination, and the
        # old test -- "is the body axis vertical" -- was a trap: a body that is
        # travelling ALWAYS leans (chunk 0 gets 6.2 of impulse, chunk 1 only 3.2),
        # so it was never settled, the wander re-targeted on the WANDER_HOLD_CAP
        # timeout instead, and the puppet travelled forever in 160-tick bursts.
        # The question the gate actually asks is "has the assembly come to rest",
        # so measure that: tick-to-tick body speed.
        body_speed = C.dist(self._last_chunk0, self.creature.chunk0)
        self._last_chunk0 = self.creature.chunk0
        self.last_body_speed = body_speed
        settled = body_speed < C.WANDER_SETTLED_SPEED
        self.wander.update(self.cursor, settled=settled)
        get_to = self.wander.get_to()
        get_to_dir = self.wander.get_to_dir()

        # ---- behaviour: roaming / investigate / talk ---------------------
        # The selector is OUR addition (see constants.py); the three poses are
        # the source's. ``get_to`` and the gaze are then re-derived per state.
        cursor_speed = self.selector.observe_cursor(self.cursor)
        talk_distance = self.wander._safe_rect()[2] * C.TALK_DISTANCE_RATIO
        self.behaviour = self.selector.choose(
            self.creature.chunk0, self.cursor, cursor_speed, talk_distance)
        if self.behaviour == "investigate" and self.cursor is not None:
            # ss.cs:4185-4188  angle inside +-70 deg, swept 0.4-0.8 deg/tick;
            # nextPos = player + DegToVec(angle) * 150.
            self.investigate_angle += self.investigate_speed
            if abs(self.investigate_angle) > C.INVESTIGATE_ANGLE_LIMIT:
                self.investigate_speed = -self.investigate_speed
                self.investigate_angle = max(-C.INVESTIGATE_ANGLE_LIMIT,
                                             min(C.INVESTIGATE_ANGLE_LIMIT,
                                                 self.investigate_angle))
            angle = radians(self.investigate_angle)
            offset = (sin(angle) * C.INVESTIGATE_RADIUS,
                      -cos(angle) * C.INVESTIGATE_RADIUS)
            get_to = (self.cursor[0] + offset[0], self.cursor[1] + offset[1])
        elif self.behaviour == "talk" and self.cursor is not None:
            # ss.cs:4278-4285  CommunicatePosScore parks the iterator at a fixed
            # distance from the player. Commit to a POINT (re-picked only if the
            # cursor wanders off) so the body can arrive and rest instead of
            # chasing a goal that slides every tick.
            if (self._talk_point is None or self._talk_anchor is None
                    or C.dist(self.cursor, self._talk_anchor) > C.TALK_REPICK):
                direction = C.dir_vec(self.cursor, self.creature.chunk0)
                if direction == (0.0, 0.0):
                    direction = (0.0, 1.0)
                self._talk_point = (self.cursor[0] + direction[0] * talk_distance,
                                    self.cursor[1] + direction[1] * talk_distance)
                self._talk_anchor = self.cursor
            get_to = self._talk_point
        else:
            self._talk_point = None
            self._talk_anchor = None

        look_point = (self.cursor if self.behaviour != "roaming" and self.cursor is not None
                      else self.wander.look_point)

        # ---- base: the puppet leads, the carriage follows (oracle.cs:327-331) --
        # baseMoving = Distance(BasePos, baseIdeal) > (baseMoving ? 50f : 350f)
        #              && consistentBasePosCounter > 30;
        # if (baseMoving) framePos = MoveTowardsAngle(framePos*90, target*90, 1)/90;
        #
        # This is the design three documents describe (plan:22, HANDOFF:69,
        # motion-spec:1.2) and what an earlier version of this file did: the
        # carriage parks and only re-sites when the PUPPET has drifted past
        # ``start_threshold``. The version that replaced it scored 64 rail
        # stations against ``|dist(station, nextPos) - 200|``, which on a
        # 776x415 chamber can only ever resolve to the right/bottom edge -- that
        # is what pinned the pet to one corner (see AGENTS.md 6.1).
        if self.wander.version != self._dest_version:
            self._dest_version = self.wander.version
            self.consistent_base_pos = 0
        self.consistent_base_pos += 1

        self.base_ideal = self.creature.chunk0
        ideal_f = self.rail.project_near(self.base_ideal, self.frame_pos)
        # The hysteresis must be measured in RAIL ARC LENGTH, not in a straight
        # line. The source compares Distance(BasePos, baseIdeal) in a 560x560
        # room where the chord is close to the arc; on a 776x415 chamber a 264 px
        # chord can be half a quadrant of travel (1190 px), and the carriage --
        # 1 degree per tick -- then needs 45-180 ticks to arrive while the puppet
        # keeps drifting. Measured with the chord version: base->body p95 421 px,
        # max 627. Arc length is what the carriage actually has to drive.
        base_point = self.rail.pose(self.frame_pos).point
        # ``Distance(BasePos, baseIdeal)`` -- the source's own chord measure.
        gap = C.dist(base_point, self.creature.chunk0)
        # Hard physical override. The arm is 570 px (300+150+90+30); once the
        # puppet is further than 90 % of that from the carriage the arm simply
        # cannot hold it, whatever the hysteresis and the 30-tick gate say. The
        # source never needs this because its room (560x560) is about one arm
        # long; a 776x415 chamber is wider, so the carriage has to be allowed to
        # break its own hysteresis to stay within reach.
        must_follow = gap > sum(C.ARM_LENGTHS) * C.BASE_FOLLOW_ARM_RATIO
        reach = self.stop_threshold if self.base_moving else self.start_threshold
        self.base_moving = (must_follow or gap > reach) and (
            must_follow or self.consistent_base_pos > C.BASE_STABLE_TICKS
        )
        if self.base_moving:
            self.frame_pos = move_towards_angle(
                self.frame_pos * 90.0, ideal_f * 90.0, C.BASE_MOVE_SPEED_DEG
            ) / 90.0
            self.frame_pos %= 4.0

        # ---- arm drives the body ----------------------------------------
        # ``OracleGetToPos`` (ss.cs:3122-3133) is ONE clamped goal, and the game
        # feeds it to BOTH the trunk and the arm:
        #     get => ClampVectorInRoom(floatyMovement && near(nextPos,50) ? nextPos : currentGetTo)
        # So the body chases the clamped point, not the raw Bezier. Clamping only
        # the arm (which is what this port used to do) lets the body outrun its own
        # limb: measured base->body up to 652 px against a 570 px arm, and the
        # trunk then trails flat. Two clamps, in this order:
        #   1. into the chamber   (ClampVectorInRoom, ss.cs:4298)
        #   2. inside the arm     (ARM_REACH_RATIO * 570 = 541 px from the base)
        # Because the carriage then follows the body, a full-screen destination is
        # still reachable -- it just takes two phases: the body pushes against the
        # reach limit, the carriage catches up, the limit slides out.
        pose = self.rail.pose(self.frame_pos)
        # The destination was already clamped when it was picked, so ``get_to`` is
        # reachable; only the room has the last word (ClampVectorInRoom).
        get_to = self._clamp_to_room(get_to)
        self.last_goal = get_to
        self.creature.step(get_to, get_to_dir, look_point)
        self.arm.update(
            pose.point, self.creature.chunk0, self.creature.chunk1,
            get_to_dir, self.base_moving, bounds=self._inset(),
        )

        # ---- ornaments ---------------------------------------------------
        # og.cs:930-931  SetStuckSegments hooks the cord on
        # Lerp(myJoint.pos, myJoint.ElbowPos(1f, next.pos), 0.4 + 0.07*i) -- the
        # ELBOW of joint 1, not joint 2's position. Passing the joint1 -> joint2
        # chord put the hook ~20 px away from the source's point.
        elbow_a = self.arm.joints[1].pos
        elbow_b = self.arm.elbow_pos(1)
        self.cord.step(elbow_a, elbow_b, self.creature.chunk0, self.creature.head, self.creature.look_dir)
        self.gown.step(self.creature.chunk0, self.creature.body_dir, self.creature.chunk1)
        halo_center = self._halo_center()
        self.halo.step(halo_center, self.creature.body_dir, self.ticks)

        self._prev = self._cur
        self._cur = self._compose()

    def _rail_gap(self, a: float, b: float) -> float:
        """Shorter arc length between two ``framePos`` values, in chamber px."""
        _, _, w, h = self._inset()
        per_quadrant = 2.0 * (w + h) / 4.0
        delta = (a - b) % 4.0
        if delta > 2.0:
            delta = 4.0 - delta
        return delta * per_quadrant

    def _clamp_destination(self, point: tuple[float, float]) -> tuple[float, float]:
        """Pull a freshly picked destination inside the arm's reach of the carriage.

        Called once per ``SetNewDestination``. ``_reachable`` does the same job for
        the arm every tick, but applying it to the body's GOAL every tick makes the
        goal slide with the carriage; a destination clamped at pick time is a fixed
        point the body can reach, stop on, and rest against.
        """
        root = self.rail.pose(self.frame_pos).point
        limit = C.ARM_LENGTHS_TOTAL * C.DESTINATION_REACH_RATIO
        distance = C.dist(root, point)
        if distance > limit and distance >= 1e-6:
            direction = C.dir_vec(root, point)
            point = (root[0] + direction[0] * limit, root[1] + direction[1] * limit)
        return self._clamp_to_room(point)

    def _clamp_to_room(self, point: tuple[float, float], margin: float = 10.0) -> tuple[float, float]:
        """``SSOracleBehavior.ClampVectorInRoom`` (ss.cs:4298-4305).

        ``result.x = Clamp(x, corner[0].x + 10, corner[1].x - 10)`` and likewise for
        y between corner 2 and corner 1 -- i.e. the inset rectangle grown in by 10 px.
        """
        x, y, w, h = self._inset()
        return (
            min(max(point[0], x + margin), x + w - margin),
            min(max(point[1], y + margin), y + h - margin),
        )

    def _reachable(self, root: tuple[float, float], target: tuple[float, float]) -> tuple[float, float]:
        limit = sum(C.ARM_LENGTHS) * C.ARM_REACH_RATIO
        distance = C.dist(root, target)
        if distance <= limit or distance < 1e-6:
            return target
        direction = C.dir_vec(root, target)
        return (root[0] + direction[0] * limit, root[1] + direction[1] * limit)

    def _filaments(self) -> list[list[tuple[float, float]]]:
        """The 14 strand node lists, straight out of ``Cord``.

        These used to be an analytic sine bow spanning the tail -> head gap. That
        was wrong twice over: the drawn length was the GAP (60-95 px) while the
        source gives each strand 50-200 px of free rope, and a curve has no
        inertia, so it could never sag, overshoot or cross itself. The physics
        now lives in ``Cord._step_filaments`` (``og.cs:873-913``); this only
        hands the nodes to the renderer.
        """
        return self.cord.filaments()

    def _halo_center(self) -> tuple[float, float]:
        axis = C.dir_vec(self.creature.chunk0, self.creature.head)
        return (
            self.creature.head[0] + axis[0] * C.HALO_HEAD_OFFSET,
            self.creature.head[1] + axis[1] * C.HALO_HEAD_OFFSET,
        )

    def step(self, dt: float) -> None:
        if dt <= 0.0:
            return
        self._advance()

    # -- snapshot ----------------------------------------------------------
    def _compose(self) -> RenderState:
        pose = self.rail.pose(self.frame_pos)
        center = self._halo_center()
        bits = self.halo.bit_positions(center)
        return RenderState(
            base_point=pose.point,
            base_dir=pose.inward,
            frame_pos=self.frame_pos,
            base_moving=self.base_moving,
            joints=[j.pos for j in self.arm.joints],
            cog_rot=[j.cog_rot for j in self.arm.joints],
            body0=self.creature.chunk0,
            body1=self.creature.chunk1,
            head=self.creature.head,
            hands=list(self.creature.hands),
            feet=list(self.creature.feet),
            look_dir=self.creature.look_dir,
            cord=list(self.cord.nodes),
            filaments=self._filaments(),
            filament_colours=list(self.cord.filament_colours),
            gown=[list(row) for row in self.gown.pos],
            gown_divs=self.gown.divs,
            halo_center=center,
            halo_radii=[self.halo.radius(r) for r in range(3)],
            halo_bits=[(bit.ring, base, tip, bit.brightness) for bit, base, tip in bits],
            halo_connections=self.halo.connection_curves(center),
            cursor=self.cursor,
            behaviour=self.behaviour,
            time=self.time,
            ticks=self.ticks,
        )

    def interpolate(self, alpha: float) -> RenderState:
        if self._prev is None:
            return self._cur
        return self._prev.lerp(self._cur, max(0.0, min(1.0, alpha)))

    # -- input -------------------------------------------------------------
    def set_cursor(self, pos: tuple[float, float] | None) -> None:
        self.cursor = pos

    # -- diagnostics -------------------------------------------------------
    def status(self) -> dict:
        """Everything a human needs to see why the pet is doing what it is doing.

        Cheap enough to call at 12 Hz from the developer panel; also used by the
        tools. Keep the keys stable -- ``tools/devpanel.py`` formats them.
        """
        ok_arm, arm_stretch, _ = self.arm.validate()
        cord_all, cord_free = self.cord.stretch()
        base_point = self.rail.pose(self.frame_pos).point
        body = self.creature.chunk0
        gap = C.dist(base_point, body)
        reach = sum(C.ARM_LENGTHS)
        down = self.creature.body_dir
        tilt = abs(180.0 - abs(degrees(atan2(down[0], down[1]))))
        worst_filament = max(
            (drawn / max(1e-9, free)
             for drawn, free in (self.cord.filament_rope(i)
                                 for i in range(len(self.cord.small)))),
            default=0.0,
        )
        return {
            "ticks": self.ticks,
            "time": round(self.time, 2),
            "behaviour": self.behaviour,
            "scores": {k: round(v, 3) for k, v in self.selector.last_scores.items()},
            # How long the current state has been held. Without this a panel can
            # legitimately show "behaviour roaming (best: talk)" and look broken:
            # the challenger did win on score but the minimum dwell has not run
            # out yet.
            "held_ticks": self.selector.ticks,
            "min_hold": C.BEHAVIOUR_MIN_TICKS,
            # Freshness per behaviour. Without it a panel cannot explain why the
            # pet dropped a behaviour it was enjoying -- the drained multiplier
            # is the reason, and it is invisible in the scores alone.
            "freshness": {k: round(v, 2) for k, v in self.selector.freshness.items()},
            "cursor": self.cursor,
            "still_ticks": self.selector.still_ticks,
            "frame_pos": round(self.frame_pos, 3),
            "station_edge": ("top", "right", "bottom", "left")[int(self.frame_pos) % 4],
            "base_moving": self.base_moving,
            "start_threshold": round(self.start_threshold, 1),
            "stop_threshold": round(self.stop_threshold, 1),
            "base_body": round(gap, 1),
            "reach": reach,
            "reach_ratio": round(gap / reach, 3),
            "body_speed": round(self.last_body_speed, 2),
            "tilt_deg": round(tilt, 1),
            # The goal actually being chased. During investigate/talk this is NOT
            # ``wander.next`` -- showing the wander's stale destination made the
            # panel read "dist 297 progress 1.00" while the pet was circling the
            # cursor, which is exactly the kind of lie a dev tool must not tell.
            "goal": self.last_goal,
            "goal_distance": round(C.dist(body, self.last_goal), 1),
            "dest": self.wander.next,
            "progress": (round(self.wander.progress, 3)
                         if self.behaviour == "roaming" else None),
            "arm_stretch": round(arm_stretch, 2),
            "cord_nodes": len(self.cord.nodes),
            "cord_stretch": round(cord_all, 3),
            "cord_stretch_free": round(cord_free, 3),
            "filament_worst": round(worst_filament, 2),
            "finite": self.cord.finite(),
            "arm_ok": ok_arm,
        }
