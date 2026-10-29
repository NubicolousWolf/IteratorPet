"""``OracleGraphics.UbilicalCord`` - the 80-segment spine plus its filaments.

The topology is the part that reads as "iterator" at a glance:

* node 0 is a socket on the **bottom edge** of the chamber,
* node ``count - 20`` (og.cs:935) is clamped onto the arm's first joint, so the
  cord rises out of the floor, hooks onto the limb and then hangs,
* the last node is tethered within 80 px of the body,
* from that tail **14 filaments** (20 nodes each, dark red / blue / black) fan
  out and plug into the back of the puppet's head.
"""

from __future__ import annotations

import math
import random
from math import hypot

from . import constants as C


class Cord:
    def __init__(self, bounds: tuple[float, float, float, float], body: tuple[float, float]):
        x, y, w, h = bounds
        # 插口：源码 og.cs:918-920 的 `room.MiddleOfTile(24, 2)`。
        # 轨道矩形在源码里是 tiles (10..38) x (3..31)：28 格见方、底边在 y=3。
        # MiddleOfTile 取格心 → x = (24+0.5-10)/28 = 0.518 个轨道宽，
        # y = (2+0.5-3) = 底边再往下 0.5 格；用户实机看下来还要再往下 1 格 → 1.5 格。
        # 旧值是 `52% / 底 - 6 px`（落在轨道矩形**里面**），插口从一开始就偏上，
        # 整条缆线自然整体上移。
        self.bounds = (x, y, w, h)
        tile = h / 28.0
        # 气泡定位要用**同一个** tile（'插口往上两格'），所以存下来供上层读
        self.tile = tile
        self.socket = (x + w * 0.518, y + h + tile * 1.5)
        self.rest = C.CORD_REST  # fixed 10 px pitch, as the game does
        self.stuck_index = 0     # set once the node count is known
        self.nodes: list[tuple[float, float]] = []
        self.vels: list[tuple[float, float]] = []
        self.anchor: tuple[float, float] = body
        self.rng = random.Random(7)

        # og.cs:787 builds 80 nodes because SS_AI's room is 800 px; the pitch is
        # what the physics pins (10f in both sweeps), not the count. On a
        # variable desktop the count has to cover the span instead -- at 80 nodes
        # the cord could only reach 790 px of a 1335 px diagonal, which is why it
        # looked too short. Reach = socket -> farthest corner, times a slack
        # factor so it can still bow instead of being a taut wire.
        far = max(
            hypot(corner_x - self.socket[0], corner_y - self.socket[1])
            for corner_x in (x, x + w)
            for corner_y in (y, y + h)
        )
        count = int(max(20, min(C.CORD_MAX_NODES,
                                math.ceil(far * C.CORD_SLACK / C.CORD_REST) + 1)))
        self._total_len = (count - 1) * C.CORD_REST
        self.stuck_index = count - 20   # og.cs:933, derived -- not 0.75*count
        for i in range(count):
            t = i / (count - 1)
            px = self.socket[0] + (body[0] - self.socket[0]) * t
            py = self.socket[1] + (body[1] - self.socket[1]) * t
            self.nodes.append((px, py))
            self.vels.append((0.0, 0.0))

        # og.cs:802-803  smallCordsLengths / smallCoordColors. The colour index is
        # 0 = metal, 1 = red, 2 = blue (og.cs:1014-1025), NOT 0 = red.
        self.filament_len: list[float] = []
        self.filament_head_dirs: list[tuple[float, float]] = []
        self.filament_colours: list[str] = []
        for _ in range(C.SMALL_CORDS):
            roll = self.rng.random()
            if roll < 0.5:
                length = C.SMALL_CORD_MIN_LEN + self.rng.random() * 15.0
            else:
                length = C._lerp(C.SMALL_CORD_MIN_LEN, C.SMALL_CORD_MAX_LEN,
                                 self.rng.random() ** 1.5)
            self.filament_len.append(length)
            self.filament_colours.append(
                ("black", "red", "blue")[self.rng.randrange(3)])
            # og.cs:800  smallCordsHeadDirs[n] = Custom.RNV() * Random.value.
            # The source spends this on a velocity kick near the head
            # (og.cs:910-911), which is what scatters the 14 strands into a
            # visible bundle. Same random vector, same use, now that the strands
            # are simulated instead of drawn as a curve.
            angle = self.rng.uniform(0.0, 6.28318)
            scale = self.rng.random()
            self.filament_head_dirs.append((math.cos(angle) * scale,
                                            math.sin(angle) * scale))

        # og.cs:796  smallCords = new Vector2[14, 20, 3] -- 20 SIMULATED nodes per
        # strand, not a drawn curve. `state._filaments` used to fake these with a
        # sine bow because a 50 px strand "cannot" span a 90 px gap; the source
        # does not care. It welds both ends and lets the two constraint sweeps
        # stretch the middle, and the knots' inertia does the rest. That surplus
        # rope -- strands run 50..200 px across a 40..90 px gap -- is exactly the
        # drooping and self-overlap the game shows, so it cannot be faked with a
        # curve whose length IS the gap.
        self.small: list[list[tuple[float, float]]] = [
            [body] * C.SMALL_CORD_NODES for _ in range(C.SMALL_CORDS)
        ]
        self.small_vels: list[list[tuple[float, float]]] = [
            [(0.0, 0.0)] * C.SMALL_CORD_NODES for _ in range(C.SMALL_CORDS)
        ]

    # -- simulation --------------------------------------------------------
    def step(
        self,
        elbow_a: tuple[float, float],
        elbow_b: tuple[float, float],
        body0: tuple[float, float],
        head: tuple[float, float],
        look_dir: tuple[float, float],
    ) -> None:
        self.anchor = body0
        count = len(self.nodes)

        # Pitch stays 10 px in every sweep, exactly like og.cs:838/850. The old
        # code shrank it to 6..10 px to "fit" a short chamber, which made the
        # cord physically shorter than the span it had to cross.
        self.rest = C.CORD_REST

        bx, by, bw, bh = self.bounds
        for i in range(count):
            px, py = self.nodes[i]
            vx, vy = self.vels[i]
            # og.cs:818-825, exactly in this order:
            #     coord[i,1] = coord[i,0];  coord[i,0] += coord[i,2];
            #     coord[i,2] *= 0.995f;     coord[i,2].y += InverseLerp(0.2,0,value)
            # so the force added here acts on the NEXT tick. (Measured: moving the
            # force before the integration changes mean link length by 0.004 px,
            # so this ordering is fidelity, not the cure for the stretch -- the
            # cure is the sweep count below.)
            px += vx
            py += vy
            vx *= C.CORD_DAMPING
            vy *= C.CORD_DAMPING
            value = i / (count - 1)
            if value < 0.2:
                # `vel.y += InverseLerp(0.2f, 0f, value)`: +y is UP in Rain World,
                # which is -y in this +y-down frame. This buoyancy is what lifts
                # the grounded end off the floor and keeps the riser straight.
                vy -= C.CORD_BUOYANCY * (1.0 - value / 0.2)
            vy += C.CORD_SAG
            # Side/top collision only: the floor is deliberately open so slack
            # coils in mid-air rather than being flattened against the bottom.
            # No floor collision on purpose: the socket IS the floor anchor, so
            # any slack simply drops out of the frame instead of being flattened
            # into a visible horizontal run along the chamber edge.
            if py < by + 2.0:
                py = by + 2.0
                vy = 0.0
            if px < bx + 2.0:
                px = bx + 2.0
                vx = 0.0
            elif px > bx + bw - 2.0:
                px = bx + bw - 2.0
                vx = 0.0
            self.nodes[i] = (px, py)
            self.vels[i] = (vx, vy)


        self._pin_elbow(elbow_a, elbow_b)
        self._tend_vertical()

        # Forward then backward distance sweeps (the game does exactly this).
        # The source runs ONE pair of sweeps over its fixed 80 links; this port can
        # carry up to CORD_MAX_NODES, and a Gauss-Seidel sweep propagates a
        # correction one link per pass, so the same two passes leave a visible
        # 20-50 % stretch in a longer chain. The pass count is solver convergence,
        # not physics, so it is the one number allowed to grow with the node count.
        passes = max(1, (count + C.CORD_SWEEP_LINKS - 1) // C.CORD_SWEEP_LINKS)
        for _ in range(passes):
            for i in range(1, count):
                self._solve_pair(i - 1, i)
            self._pin_elbow(elbow_a, elbow_b)
            self._tend_vertical()
            for i in range(count - 1):
                self._solve_pair(i, i + 1)
            self._pin_elbow(elbow_a, elbow_b)
            self._tend_vertical()

        # Coarse bending stiffness keeps the spine from collapsing.
        stiffness = C.CORD_BEND_STIFFNESS
        for level in C.CORD_BEND_LEVELS:
            for i in range(level, count - level):
                a = self.nodes[i - level]
                b = self.nodes[i]
                d = C.dir_vec(a, b)
                self.vels[i] = (self.vels[i][0] + d[0] * stiffness, self.vels[i][1] + d[1] * stiffness)
                self.vels[i - level] = (
                    self.vels[i - level][0] - d[0] * stiffness,
                    self.vels[i - level][1] - d[1] * stiffness,
                )
                c = self.nodes[i + level]
                d2 = C.dir_vec(c, b)
                self.vels[i] = (self.vels[i][0] + d2[0] * stiffness, self.vels[i][1] + d2[1] * stiffness)
                self.vels[i + level] = (
                    self.vels[i + level][0] - d2[0] * stiffness,
                    self.vels[i + level][1] - d2[1] * stiffness,
                )
            stiffness *= 0.75

        # Tail must stay near the body.
        tail = self.nodes[-1]
        distance = C.dist(tail, body0)
        if distance > C.CORD_TAIL_LIMIT:
            direction = C.dir_vec(tail, body0)
            excess = distance - C.CORD_TAIL_LIMIT
            self.nodes[-1] = (tail[0] + direction[0] * excess * 0.25, tail[1] + direction[1] * excess * 0.25)
            self.vels[-1] = (
                self.vels[-1][0] + direction[0] * excess * 0.5,
                self.vels[-1][1] + direction[1] * excess * 0.5,
            )

        # VerticalCollision, applied after the constraint sweeps so nothing
        # gets pushed back below the floor tile once the chain is resolved.
        for i in range(count):
            if self.nodes[i][1] > self.socket[1]:
                self.nodes[i] = (self.nodes[i][0], self.socket[1])
                self.vels[i] = (self.vels[i][0], 0.0)

        self._step_filaments(head, look_dir)

    # -- filaments ---------------------------------------------------------
    def _step_filaments(self, head, look_dir) -> None:
        """``og.cs:873-913`` -- the 14 small cords, 20 nodes each, verbatim.

        Both ends are welded (node 0 to the cord's tail, node 19 to
        ``owner.head.pos``) and the free length is ``smallCordsLengths[n] / 20``
        per link, so a 200 px strand really is 200 px of rope in a gap that is
        often half that -- it has to bow, sag and cross itself. Nothing here is
        damped per-node below what ``LerpMap`` says, so the strands keep
        swinging: that inertia is the "垂落段 / 重叠" the game shows.
        """
        tail = self.nodes[-1]
        last = C.SMALL_CORD_NODES - 1
        # og.cs:905  DirVec(coord[n-2], coord[n-1]) -- the cord's own last link.
        kick = C.dir_vec(self.nodes[-2], tail)
        for index in range(C.SMALL_CORDS):
            nodes = self.small[index]
            vels = self.small_vels[index]
            # og.cs:877-880  integrate with the OLD velocity, then damp by speed,
            # then (gravity is 0 for OracleID.SS, oracle.cs:696) nothing else.
            for k in range(C.SMALL_CORD_NODES):
                px, py = nodes[k]
                vx, vy = vels[k]
                px += vx
                py += vy
                damp = _lerp_map(
                    hypot(vx, vy),
                    C.SMALL_CORD_DAMP_SLOW_SPEED, C.SMALL_CORD_DAMP_FAST_SPEED,
                    C.SMALL_CORD_DAMP_SLOW, C.SMALL_CORD_DAMP_FAST,
                )
                vx *= damp
                vy *= damp
                # og.cs:880  vel.y -= room.gravity * 0.9f -- applied AFTER the
                # damping, so it is undamped this tick. OracleID.SS sets
                # room.gravity = 0 (oracle.cs:696), so this is 0 for Five Pebbles;
                # it is kept so the tick order matches the source.
                vy += C.CORD_SAG
                nodes[k] = (px, py)
                vels[k] = (vx, vy)

            # og.cs:882  num6 = smallCordsLengths[n] / 20 -- the rest length is
            # this strand's own free length split over its 20 nodes, NOT the
            # cord's 10 px pitch. Short strands are therefore genuinely taut.
            rest = self.filament_len[index] / C.SMALL_CORD_NODES
            # og.cs:883-902  one backward sweep then one forward sweep. The
            # source needs exactly one of each because its cord tail barely
            # moves; a roaming pet drags that tail around every tick, and one
            # pass leaves the residual concentrated in one link (measured: a
            # 91 px strand drawn 163 px, i.e. 1.8x its own rope). Repeating the
            # pair is SOLVER convergence -- the same concession the 80-node
            # spine already makes -- not new physics.
            for _ in range(C.SMALL_CORD_PASSES):
                for k in range(1, C.SMALL_CORD_NODES):
                    _solve_pair(nodes, vels, k - 1, k, rest)
                for k in range(last):
                    _solve_pair(nodes, vels, k, k + 1, rest)

            # og.cs:903-911  weld both ends, then kick the nodes next to them.
            nodes[0] = tail
            vels[0] = (0.0, 0.0)
            for offset, strength in enumerate(C.SMALL_CORD_TAIL_KICK, start=1):
                vx, vy = vels[offset]
                vels[offset] = (vx + kick[0] * strength, vy + kick[1] * strength)

            nodes[last] = head
            vels[last] = (0.0, 0.0)
            head_dir = self.filament_head_dirs[index]
            hx = -(look_dir[0] + head_dir[0])
            hy = -(look_dir[1] + head_dir[1])
            for offset, strength in enumerate(C.SMALL_CORD_HEAD_KICK):
                node = last - 1 - offset
                vx, vy = vels[node]
                vels[node] = (vx + hx * strength, vy + hy * strength)

    # -- geometry ----------------------------------------------------------
    def filaments(self) -> list[list[tuple[float, float]]]:
        """A COPY of the 14 strand node lists, for the renderer.

        It must be a copy: ``SimulationState._compose`` keeps one RenderState as
        the previous tick and one as the current tick, so handing out the live
        list makes both snapshots the same object and ``RenderState.lerp`` then
        interpolates the strands with themselves -- they judder at any render rate
        above 40 Hz while the spine (which is copied) stays smooth. The spine
        already does ``list(self.cord.nodes)`` for the same reason.
        """
        return [list(strand) for strand in self.small]


    def _pin_elbow(self, point_a, point_b) -> None:
        """``SetStuckSegments``: three nodes clamped onto the arm elbow.

        og.cs:933-939 spreads them along ``Lerp(myJoint.pos, ElbowPos, 0.4f +
        0.07f * i)`` (i = -1, 0, 1) with weights 0.5 / 1 / 0.5 and an 8 px sideways
        offset. The source's chord is ``|joint1 - ElbowPos|``, which the IK in
        ``oracle.cs:50`` pins at 2/3 of the segment length (~100 px), so its three
        nodes sit ~7 px apart; this port passes the joint1 -> joint2 chord
        instead (a known deviation, see the audit note), which makes the spacing
        ~10 px and lands the hook within ~20 px of the source's point.
        """
        count = len(self.nodes)
        # og.cs:933  coord[Length - 20 + i] -- the STUCK INDEX follows the
        # node count; hardcoding 0.75*count only happened to equal 60 at 80 nodes.
        base = count - 20
        axis = C.dir_vec(point_a, point_b)
        # y-down twin of Custom.PerpendicularVector: the game frame is +y up.
        perp = (axis[1], -axis[0])
        for offset, weight in ((-1, 0.5), (0, 1.0), (1, 0.5)):
            index = base + offset
            if not 0 <= index < count:
                continue
            t = 0.40 + 0.07 * offset
            target = (
                point_a[0] + (point_b[0] - point_a[0]) * t + perp[0] * 8.0,
                point_a[1] + (point_b[1] - point_a[1]) * t + perp[1] * 8.0,
            )
            cx, cy = self.nodes[index]
            self.nodes[index] = (cx + (target[0] - cx) * weight, cy + (target[1] - cy) * weight)
            if weight >= 1.0:
                self.vels[index] = (0.0, 0.0)
            else:
                vx, vy = self.vels[index]
                self.vels[index] = (vx * (1 - weight), vy * (1 - weight))

    def _tend_vertical(self) -> None:
        """Weld node 0 to the socket, exactly like ``SetStuckSegments``.

        og.cs:921-926 sets ``coord[0,0] = MiddleOfTile(...)`` and zeroes its
        velocity. This is a HARD pin, not a soft pull -- an earlier docstring here
        claimed otherwise while the code already pinned.
        """
        self.nodes[0] = self.socket
        self.vels[0] = (0.0, 0.0)


    def _pin(self, index: int, point: tuple[float, float]) -> None:
        self.nodes[index] = point
        self.vels[index] = (0.0, 0.0)

    def _solve_pair(self, i: int, j: int) -> None:
        _solve_pair(self.nodes, self.vels, i, j, self.rest)

    # -- diagnostics -------------------------------------------------------
    def stretch(self) -> tuple[float, float]:
        """``(all_links, free_links)`` worst relative error, never one alone.

        Reporting only the free links is what let this diagnostic look healthy
        while the pinned socket link sat at 15 % -- the pinned segments are where
        the solver fights hardest, so they must stay in the number that is quoted.
        """
        worst = 0.0
        worst_free = 0.0
        pins = (0, self.stuck_index - 1, self.stuck_index, self.stuck_index + 1)
        for i in range(len(self.nodes) - 1):
            distance = C.dist(self.nodes[i], self.nodes[i + 1])
            error = abs(distance - self.rest) / max(1.0, self.rest)
            worst = max(worst, error)
            if i not in pins:
                worst_free = max(worst_free, error)
        return worst, worst_free

    def finite(self) -> bool:
        """No NaN anywhere, spine AND strands.

        The strands are the likeliest place for a blow-up (2.5 px links plus the
        5/3/1.5 kicks), and ``finite()`` used to scan only the spine -- a broken
        filament would have gone unreported.
        """
        for x, y in self.nodes:
            if x != x or y != y:
                return False
        for strand in self.small:
            for x, y in strand:
                if x != x or y != y:
                    return False
        return True

    def filament_rope(self, index: int) -> tuple[float, float]:
        """``(drawn, free)`` path length of one strand, for diagnostics/tests."""
        strand = self.small[index]
        drawn = sum(C.dist(strand[k], strand[k + 1])
                    for k in range(len(strand) - 1))
        return drawn, self.filament_len[index]


def _solve_pair(nodes, vels, i: int, j: int, rest: float) -> None:
    """One distance constraint between two nodes, exactly ``og.cs:834-840``.

    ``vector2 = DirVec(coord[j], coord[j-1]) * ((10f - num) * 0.5f)`` then
    ``coord[j] -= vector2`` / ``coord[j-1] += vector2``, and the SAME push is
    applied to both velocities -- that velocity feedback is why the cord keeps
    its speed instead of being a pure position solver.
    """
    a = nodes[i]
    b = nodes[j]
    distance = C.dist(a, b)
    if distance < 1e-6:
        return
    direction = C.dir_vec(a, b)
    push = (rest - distance) * 0.5
    vx_i, vy_i = vels[i]
    vx_j, vy_j = vels[j]
    nodes[i] = (a[0] - direction[0] * push, a[1] - direction[1] * push)
    vels[i] = (vx_i - direction[0] * push, vy_i - direction[1] * push)
    nodes[j] = (b[0] + direction[0] * push, b[1] + direction[1] * push)
    vels[j] = (vx_j + direction[0] * push, vy_j + direction[1] * push)


def _lerp_map(value: float, a: float, b: float, c: float, d: float) -> float:
    """``Custom.LerpMap``: clamp01((v-a)/(b-a)) then lerp(c, d, t)."""
    if b == a:
        return c
    t = max(0.0, min(1.0, (value - a) / (b - a)))
    return c + (d - c) * t
