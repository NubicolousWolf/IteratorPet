"""``Oracle.OracleArm`` - four joints pinned to the rail, holding the body.

Transcribed from the decompiled ``Joint.Update``:

```csharp
lastPos = pos; pos += vel; vel *= 0.8f;
if (index == 0) pos = arm.BasePos(1f);          // pinned to the rail
else if (index == 1 && baseMoving) vel += clamp(ikElbow - pos, 100)/100 * 1.8f;
float share = (index == 0) ? 0f : (index == len - 2 ? 1f : 0.5f);
float minFrac = LerpMap(dot(prevDir, dir), -1f, 1f, 1f, 0.2f);
if (d > totalLength)              target = totalLength;
else if (d < totalLength*minFrac) target = totalLength * minFrac;
```

The last joint is snapped onto body chunk 0 at ``totalLength`` (30 px), which is
why the pincer is glued to the puppet's back rather than gripping its head.
"""

from __future__ import annotations

from math import atan2, hypot

from . import constants as C


class Joint:
    __slots__ = ("index", "total_length", "pos", "last_pos", "vel", "flip",
                 "cog_rot", "last_cog_rot", "last_len")

    def __init__(self, index: int, total_length: float, position: tuple[float, float], flip: float):
        self.index = index
        self.total_length = total_length
        self.pos = position
        self.last_pos = position
        self.vel = (0.0, 0.0)
        self.flip = flip
        self.cog_rot = 0.0
        self.last_cog_rot = 0.0
        self.last_len = 0.0

    def integrate(self) -> None:
        self.last_pos = self.pos
        self.pos = (self.pos[0] + self.vel[0], self.pos[1] + self.vel[1])
        self.vel = (self.vel[0] * C.ARM_VEL_DAMPING, self.vel[1] * C.ARM_VEL_DAMPING)


class ArmChain:
    def __init__(self, base: tuple[float, float], flip: float = 1.0):
        self.joints: list[Joint] = []
        position = base
        for index, length in enumerate(C.ARM_LENGTHS):
            self.joints.append(Joint(index, length, position, flip))
            position = (position[0], position[1] + length)

    def elbow_pos(self, index: int) -> tuple[float, float]:
        """``Oracle.OracleArm.Joint.ElbowPos(1f, next.pos)`` -- oracle.cs:45-53.

        ``return Custom.InverseKinematic(Tip, Lerp(lastPos, pos, t),
                                         totalLength/3, totalLength*2/3,
                                         index % 2 == 0 ? 1f : -1f)``

        so the elbow is ``totalLength/3`` from the NEXT joint and
        ``totalLength*2/3`` from this one -- for joint 1 (totalLength 150) that is
        50 px from joint 2 and **100 px from joint 1**, at an angle set by the law
        of cosines. ``UbilicalCord.SetStuckSegments`` hooks the cord onto this point
        (``Lerp(myJoint.pos, ElbowPos, 0.4 + 0.07*i)``), NOT onto joint 2 -- using
        the joint1 -> joint2 chord instead put the hook ~20 px away from where the
        game puts it.

        ``flip`` 直接就是源码那个 ``index % 2 == 0 ? 1f : -1f``，**不取负**：
        ``C.inverse_kinematic`` 已经按 y 镜像帧实现（见它的 docstring，
        以及 ``_work/source_ik_probe.py`` 的逐值核对）。2026-10-27 之前这里写的是
        ``-source_flip``，把肘点画到弦的另一侧——缆线钩点因此一直挂在错误的一侧。
        """
        if not 0 <= index < len(self.joints) - 1:
            raise ValueError(
                f"elbow_pos({index}) needs a following joint; the chain has "
                f"{len(self.joints)} joints (only the tail joint has none, and the "
                f"source's no-next branch needs a Tip we do not carry)")
        joint = self.joints[index]
        tip = self.joints[index + 1]
        source_flip = 1.0 if index % 2 == 0 else -1.0
        return C.inverse_kinematic(
            tip.pos,
            joint.pos,
            joint.total_length / 3.0,
            joint.total_length * 2.0 / 3.0,
            source_flip,
        )

    # -- simulation --------------------------------------------------------
    def update(
        self,
        base_point: tuple[float, float],
        body0: tuple[float, float],
        body1: tuple[float, float],
        get_to_dir: tuple[float, float],
        base_moving: bool,
        bounds: tuple[float, float, float, float] | None = None,
    ) -> None:
        """``OracleArm.Joint.Update``, one pass per 40 Hz tick.

        Faithful to the decompile: integrate, damp by 0.8, pin joint 0, nudge
        joint 1 toward its IK elbow while the base travels, then walk the chain
        applying the variable-length constraint. The tail is snapped onto body
        chunk 0 at ``ARM_LENGTHS[3]``.
        """
        for joint in self.joints:
            joint.integrate()
        self.joints[0].pos = base_point

        if base_moving:
            j0, j1, j2 = self.joints[0], self.joints[1], self.joints[2]
            elbow = C.inverse_kinematic(j0.pos, j2.pos, j0.total_length, j1.total_length, j1.flip)
            pull = C.clamp_magnitude((elbow[0] - j1.pos[0], elbow[1] - j1.pos[1]), C.ARM_ELBOW_CLAMP)
            j1.vel = (
                j1.vel[0] + pull[0] / C.ARM_ELBOW_CLAMP * C.ARM_ELBOW_BIAS,
                j1.vel[1] + pull[1] / C.ARM_ELBOW_CLAMP * C.ARM_ELBOW_BIAS,
            )

        # Tail: aim just off the lower chunk, then weld to the upper chunk.
        tail = self.joints[-1]
        aim = (
            body1[0] - get_to_dir[0] * tail.total_length * 0.5,
            body1[1] - get_to_dir[1] * tail.total_length * 0.5,
        )
        to_self = C.dir_vec(body1, tail.pos)
        aim = (aim[0] + to_self[0] * tail.total_length * 0.5, aim[1] + to_self[1] * tail.total_length * 0.5)
        pull = C.clamp_magnitude((aim[0] - tail.pos[0], aim[1] - tail.pos[1]), C.ARM_TAIL_CLAMP)
        step = (pull[0] / C.ARM_TAIL_CLAMP * C.ARM_TAIL_BIAS, pull[1] / C.ARM_TAIL_CLAMP * C.ARM_TAIL_BIAS)
        tail.vel = (tail.vel[0] + step[0], tail.vel[1] + step[1])
        tail.pos = (tail.pos[0] + step[0], tail.pos[1] + step[1])

        direction = C.dir_vec(tail.pos, body0)
        correction = C.dist(tail.pos, body0) - tail.total_length
        correction = max(-14.0, min(14.0, correction))
        tail.pos = (tail.pos[0] + direction[0] * correction, tail.pos[1] + direction[1] * correction)
        tail.vel = (tail.vel[0] + direction[0] * correction, tail.vel[1] + direction[1] * correction)

        for _ in range(2):
            self.joints[0].pos = base_point
            for index in range(len(self.joints) - 1):
                self._constrain(index)

        # Rack-and-pinion: the gear turns by the *change in link length*,
        # normalised by the link's rest length. Alternating sign because
        # neighbouring gears mesh. This is why the gear stops when the limb
        # holds still -- it is driven by extension, not by time.
        for index, joint in enumerate(self.joints):
            nxt = self.joints[index + 1].pos if index + 1 < len(self.joints) else body0
            current = C.dist(joint.pos, nxt)
            delta = (joint.last_len - current) / joint.total_length
            joint.last_cog_rot = joint.cog_rot
            joint.cog_rot += delta * (-1.0 if index % 2 == 0 else 1.0) * 2.0
            joint.last_len = current

        # Wall collision: with the puppet roaming the whole screen the chain can
        # be pulled straight and its middle joints would poke outside the room.
        if bounds is not None:
            bx, by, bw, bh = bounds
            for joint in self.joints[1:]:
                joint.pos = (
                    min(max(joint.pos[0], bx + 6.0), bx + bw - 6.0),
                    min(max(joint.pos[1], by + 6.0), by + bh - 6.0),
                )

        # Guarantee: walk outward from the pinned base and clamp every link.
        # `share=0` means joint 1 absorbs the whole correction for link 0 while
        # the tail simultaneously drags it, so a fixed iteration count cannot
        # promise convergence. This single forward sweep makes "no link longer
        # than its rest length" a property, not a hope.
        self.joints[0].pos = base_point
        for index in range(len(self.joints) - 1):
            a = self.joints[index].pos
            b = self.joints[index + 1].pos
            limit = self.joints[index].total_length
            distance = C.dist(a, b)
            if distance > limit and distance > 1e-6:
                direction = C.dir_vec(a, b)
                self.joints[index + 1].pos = (
                    a[0] + direction[0] * limit,
                    a[1] + direction[1] * limit,
                )
                self.joints[index + 1].vel = (0.0, 0.0)

    def _constrain(self, index: int) -> None:
        """One link of the variable-length constraint from ``Joint.Update``."""
        joint = self.joints[index]
        nxt = self.joints[index + 1]
        direction = C.dir_vec(joint.pos, nxt.pos)
        distance = C.dist(joint.pos, nxt.pos)
        share = 0.0 if index == 0 else (1.0 if index == len(self.joints) - 2 else 0.5)

        min_fraction = 0.5
        if index > 0:
            prev = self.joints[index - 1]
            prev_dir = C.dir_vec(prev.pos, joint.pos)
            dot = prev_dir[0] * direction[0] + prev_dir[1] * direction[1]
            min_fraction = _lerp(1.0, C.ARM_MIN_LEN_STRAIGHT, (dot + 1.0) * 0.5)

        limit = joint.total_length
        target = -1.0
        if distance > limit:
            target = limit
        elif distance < limit * min_fraction:
            target = limit * min_fraction
        if target <= 0.0:
            return

        delta = distance - target
        move = (direction[0] * delta * share, direction[1] * delta * share)
        joint.pos = (joint.pos[0] + move[0], joint.pos[1] + move[1])
        back = (direction[0] * delta * (1.0 - share), direction[1] * delta * (1.0 - share))
        nxt.pos = (nxt.pos[0] - back[0], nxt.pos[1] - back[1])

    # -- rendering helpers -------------------------------------------------
    # 2026-10-27 删掉 `ArmChain.elbow()`：全仓库无人调用，而且它用的是
    # "端点传反 + joint.flip"的旧口径（会把肘点画偏 L/3）。要肘点请用
    # `elbow_pos()`（缆线钩点）或 `renderer.drawn_elbow()`（绘制，含屏幕护栏）。

    def limb_tip(self, index: int, body0: tuple[float, float]) -> tuple[float, float]:
        if index < len(self.joints) - 1:
            return self.joints[index + 1].pos
        return body0

    # -- diagnostics -------------------------------------------------------
    def validate(self) -> tuple[bool, float, float]:
        """Report over-extension only.

        The variable-length constraint deliberately lets a link compress to
        20 % of its rest length when the limb folds, so ``abs(d - rest)``
        counted legitimate folding as an error and reported phantom 150 px
        "stretches".
        """
        over = 0.0
        under = 0.0
        for index in range(len(self.joints) - 1):
            limit = self.joints[index].total_length
            distance = C.dist(self.joints[index].pos, self.joints[index + 1].pos)
            over = max(over, distance - limit)
            under = max(under, limit * 0.2 - distance)
        finite = all(j.pos[0] == j.pos[0] and j.pos[1] == j.pos[1] for j in self.joints)
        return finite and over < 2.0, max(0.0, over), max(0.0, under)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t
