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
from math import atan2, cos, degrees, hypot, sin

from . import constants as C
from .arm import ArmChain
from .behavior import Wander
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
            time=other.time,
            ticks=other.ticks,
        )


class SimulationState:
    def __init__(self, bounds: tuple[float, float, float, float], inset: float = 26.0, seed: int = 1234):
        self.inset = inset
        self._bounds = bounds
        self.rail = RailPath(self._inset())
        self.wander = Wander(self._inset(), seed=seed)
        # Destinations are pulled into the arm's reach of their nearest rail
        # station, so a point on the far side of a wide screen does not leave the
        # body leaning on the reach limit for the whole hop.

        self.base_ideal = self.wander.current
        self.start_threshold = 160.0
        self.stop_threshold = 24.0
        self.ideal_tolerance = 190.0
        self.body_target: tuple[float, float] | None = None
        self.drag_mode: str | None = None
        self.station_timer = 0
        self.base_cooldown = 0
        self._wander_radius = 260.0
        self.station_f = 0.0
        self.rng = __import__("random").Random(seed)
        self.drag_point: tuple[float, float] | None = None
        self._refresh_thresholds()
        self.frame_pos = self.rail.project(self.wander.current)
        self.station_f = self.frame_pos
        self.base_moving = False
        self.stable_ticks = 0

        base_pose = self.rail.pose(self.frame_pos)
        self.arm = ArmChain(base_pose.point)
        self.creature = Creature(self.wander.current)
        self.cord = Cord(self._inset(), self.creature.chunk0)
        self.gown = Gown(self.creature.chunk0, self.creature.body_dir)
        self.halo = Halo(seed=seed)

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
        _, _, w, h = self._inset()
        span = max(w, h)
        self.start_threshold = C.BASE_MOVE_START_RATIO * span
        self.stop_threshold = C.BASE_MOVE_STOP_RATIO * span
        self.ideal_tolerance = C.BASE_IDEAL_RATIO * span

    def resize(self, bounds: tuple[float, float, float, float]) -> None:
        self._bounds = bounds
        inner = self._inset()
        self.rail = RailPath(inner)
        self.wander.bounds = inner
        self._refresh_thresholds()
        self.frame_pos = self.rail.project(self.creature.chunk0)
        self._cur = self._compose()

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
        down = self.creature.body_dir
        tilt = abs(degrees(atan2(down[0], down[1])))
        settled = tilt < C.WANDER_SETTLED_DEG
        self.wander.update(self.cursor, base=pose_now.point, radius=self._wander_radius,
                           tangent=pose_now.tangent, settled=settled)
        get_to = self.wander.get_to()
        get_to_dir = self.wander.get_to_dir()

        # ---- base: hysteresis + at most 1° of a quadrant per tick --------
        self.stable_ticks += 1

        # The puppet leads, the base follows.  The carriage only re-sites when the
        # station 200 px from the destination is far away, which is what makes it
        # move in occasional hops instead of chasing every twitch.
        base_point = self.rail.pose(self.frame_pos).point
        _, _, cw, ch = self._inset()
        span = hypot(cw, ch)
        ideal_f = self.frame_pos
        best_err = 1e9
        for i in range(64):
            f = i * 4.0 / 64.0
            err = abs(C.dist(self.rail.pose(f).point, self.wander.next) - 200.0)
            if err < best_err:
                best_err, ideal_f = err, f

        gap = C.dist(base_point, self.rail.pose(ideal_f).point)
        if gap > span * 0.28:
            speed = C.BASE_SPEED_SLOW if gap < span * 0.50 else C.BASE_SPEED_FAST
        else:
            speed = 0.0

        self.last_base_speed = speed
        if speed > 0.0:
            target_f = ideal_f
            self.base_moving = True
        else:
            target_f = self.frame_pos
            self.base_moving = False

        if self.base_moving and speed > 0.0:
            self.frame_pos = move_towards_angle(
                self.frame_pos * 90.0, target_f * 90.0, speed
            ) / 90.0
            self.frame_pos %= 4.0

        # ---- arm drives the body ----------------------------------------
        # The game updates the body chunks inside OracleArm.Update *before*
        # the joints chase them, so the limb always aims at a fresh target.
        self.creature.step(get_to, get_to_dir, self.wander.look_point)
        pose = self.rail.pose(self.frame_pos)
        # Keep the *goal* reachable, not just the body. The game's room AI only
        # picks destinations the arm can cover; asking for a point outside the
        # reach is what made the limb fight the body every tick.
        get_to = self._reachable(pose.point, get_to)
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

    def _reachable(self, root: tuple[float, float], target: tuple[float, float]) -> tuple[float, float]:
        limit = sum(C.ARM_LENGTHS) * 0.62
        distance = C.dist(root, target)
        if distance <= limit or distance < 1e-6:
            return target
        direction = C.dir_vec(root, target)
        return (root[0] + direction[0] * limit, root[1] + direction[1] * limit)

    def _leash(self, root: tuple[float, float]) -> None:
        limit = sum(C.ARM_LENGTHS) * 0.72
        chunk0 = self.creature.chunk0
        direction = C.dir_vec(root, chunk0)
        distance = C.dist(root, chunk0)
        if distance <= limit or distance < 1e-6:
            return
        target = (root[0] + direction[0] * limit, root[1] + direction[1] * limit)
        delta = (target[0] - chunk0[0], target[1] - chunk0[1])
        c = self.creature
        c.chunk0 = target
        c.chunk1 = (c.chunk1[0] + delta[0], c.chunk1[1] + delta[1])
        c.head = (c.head[0] + delta[0], c.head[1] + delta[1])
        c.hands = [(h[0] + delta[0], h[1] + delta[1]) for h in c.hands]
        c.feet = [(f[0] + delta[0], f[1] + delta[1]) for f in c.feet]

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
        ok_arm, arm_stretch, _ = self.arm.validate()
        return {
            "ticks": self.ticks,
            "frame_pos": round(self.frame_pos, 3),
            "base_moving": self.base_moving,
            "arm_stretch": round(arm_stretch, 2),
            "cord_stretch": round(self.cord.stretch()[0], 3),
            "cord_stretch_free": round(self.cord.stretch()[1], 3),
            "finite": self.cord.finite(),
            "arm_ok": ok_arm,
        }
