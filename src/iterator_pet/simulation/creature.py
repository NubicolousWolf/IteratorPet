"""The iterator creature: two body chunks, a head, two hands and two feet.

Mirrors ``Oracle`` + ``OracleGraphics``:

* two ``BodyChunk`` radii 6 joined by a 9 px connection,
* weightless: velocity is multiplied by 0.4 and nudged by clamped impulses,
* a ``GenericBodyPart`` head that trails 6-8 px above the upper chunk and
  follows ``lookDir``,
* hands/feet tethered with ``ConnectToPoint`` (15 px / 10 px).
"""

from __future__ import annotations

from math import cos, hypot, radians, sin

from . import constants as C


def _clamp_mag(v: tuple[float, float], limit: float) -> tuple[float, float]:
    return C.clamp_magnitude(v, limit)


class Creature:
    def __init__(self, position: tuple[float, float]):
        self.chunk0 = position
        self.chunk1 = (position[0], position[1] + C.BODY_CONNECTION)
        self.vel0 = (0.0, 0.0)
        self.vel1 = (0.0, 0.0)

        self.head = (position[0], position[1] - 8.0)
        self.head_vel = (0.0, 0.0)

        self.hands = [
            (position[0] - 8.0, position[1] + 6.0),
            (position[0] + 8.0, position[1] + 6.0),
        ]
        self.hand_vel = [(0.0, 0.0), (0.0, 0.0)]
        self.feet = [
            (position[0] - 6.0, position[1] + 16.0),
            (position[0] + 6.0, position[1] + 16.0),
        ]
        self.foot_vel = [(0.0, 0.0), (0.0, 0.0)]

        self.look_dir = (0.0, 0.0)
        self.last_look_dir = (0.0, 0.0)
        self.breath = 0.0
        self.breath_fac = 1.0
        self.last_breath_fac = 1.0

    # -- integration -------------------------------------------------------
    def step(self, get_to: tuple[float, float], get_to_dir: tuple[float, float], look_point: tuple[float, float]) -> None:
        # --- body: ``vel *= 0.4`` then a clamped impulse toward the target ---
        # BodyChunk.Update() applies the chunk's own airFriction (0.99) every tick,
        # BEFORE OracleArm's 0.4 damping. The constant existed but was never used.
        self.vel0 = (self.vel0[0] * C.AIR_FRICTION * C.BODY_VEL_DAMPING,
                     self.vel0[1] * C.AIR_FRICTION * C.BODY_VEL_DAMPING)
        self.vel1 = (self.vel1[0] * C.AIR_FRICTION * C.BODY_VEL_DAMPING,
                     self.vel1[1] * C.AIR_FRICTION * C.BODY_VEL_DAMPING)

        push0 = _clamp_mag((get_to[0] - self.chunk0[0], get_to[1] - self.chunk0[1]), C.BODY_IMPULSE_CLAMP)
        self.vel0 = (
            self.vel0[0] + push0[0] / C.BODY_IMPULSE_CLAMP * C.BODY_IMPULSE_UPPER,
            self.vel0[1] + push0[1] / C.BODY_IMPULSE_CLAMP * C.BODY_IMPULSE_UPPER,
        )
        # oracle.cs:294 -- note the origin: the displacement is measured from
        # ``bodyChunks[0].pos``, NOT from bodyChunks[1].pos. The whole sum
        # ``OracleGetToPos - GetToDir * connection.distance - bodyChunks[0].pos``
        # is what gets clamped, so chunk 1 is driven by chunk 0's offset from the
        # target. Reading it as "chunk1 to target" turned the hang torque into a
        # position spring and let the trunk lie flat while roaming.
        offset1 = (
            get_to[0] - get_to_dir[0] * C.BODY_CONNECTION - self.chunk0[0],
            get_to[1] - get_to_dir[1] * C.BODY_CONNECTION - self.chunk0[1],
        )
        push1 = _clamp_mag(offset1, C.BODY_IMPULSE_CLAMP)
        self.vel1 = (
            self.vel1[0] + push1[0] / C.BODY_IMPULSE_CLAMP * C.BODY_IMPULSE_LOWER,
            self.vel1[1] + push1[1] / C.BODY_IMPULSE_CLAMP * C.BODY_IMPULSE_LOWER,
        )

        self.chunk0 = (self.chunk0[0] + self.vel0[0], self.chunk0[1] + self.vel0[1])
        self.chunk1 = (self.chunk1[0] + self.vel1[0], self.chunk1[1] + self.vel1[1])

        # Keep the two chunks exactly 9 px apart (BodyChunkConnection).
        dx, dy = self.chunk1[0] - self.chunk0[0], self.chunk1[1] - self.chunk0[1]
        distance = hypot(dx, dy)
        if distance > 1e-6:
            error = (distance - C.BODY_CONNECTION) * 0.5
            ux, uy = dx / distance, dy / distance
            self.chunk0 = (self.chunk0[0] + ux * error, self.chunk0[1] + uy * error)
            self.chunk1 = (self.chunk1[0] - ux * error, self.chunk1[1] - uy * error)

        # --- head: og.cs:1596-1641, in the source's order --------------------
        # breathFac = Lerp(0.5 + 0.5*sin(2*pi*breathe), 1, health^2). A healthy
        # iterator sits at health 1, so breathFac is a CONSTANT 1 -- the old
        # 0.5 + 0.5*sin() here dropped the guidance force to zero for half of
        # every breath cycle, which is why the head had no reason to stay up.
        self.breath += C.BREATHE_RATE
        self.last_breath_fac = self.breath_fac
        wave = 0.5 + 0.5 * sin(self.breath * 6.28318)
        self.breath_fac = C._lerp(wave, 1.0, C.HEALTH ** 2)

        self.last_look_dir = self.look_dir
        # og.cs:1618  lookDir = ClampMagnitude(lookPoint - firstChunk.pos, 100f) / 100f
        aim = (look_point[0] - self.chunk0[0], look_point[1] - self.chunk0[1])
        clamped = _clamp_mag(aim, 100.0)
        self.look_dir = (clamped[0] / 100.0, clamped[1] / 100.0)

        up = C.dir_vec(self.chunk1, self.chunk0)
        anchor = (self.chunk0[0] + up[0] * C.HEAD_ANCHOR,
                  self.chunk0[1] + up[1] * C.HEAD_ANCHOR)

        # 1. og.cs:1621  head.Update(): pos += vel; vel *= airFriction (0.995)
        self.head = (self.head[0] + self.head_vel[0],
                     self.head[1] + self.head_vel[1])
        self.head_vel = (self.head_vel[0] * C.HEAD_AIR_FRICTION,
                         self.head_vel[1] * C.HEAD_AIR_FRICTION)
        # 2. og.cs:1622  ConnectToPoint(anchor, 8f, push: true, 0f,
        #    firstChunk.vel, 0.5f, 0.01f) -- a weld onto the 8 px sphere.
        self.head, self.head_vel = _weld(
            self.head, self.head_vel, anchor, C.HEAD_TETHER, self.vel0, up)
        # 3. og.cs:1626-1632  guidance is added AFTER the tether, so it survives
        #    into the next tick instead of being damped by it. This -- not any
        #    positional rule -- is what decides WHERE on the sphere the head sits.
        self.head_vel = (
            self.head_vel[0] + up[0] * self.breath_fac
            + self.look_dir[0] * 0.5 * self.breath_fac,
            self.head_vel[1] + up[1] * self.breath_fac
            + self.look_dir[1] * 0.5 * self.breath_fac,
        )

        # --- hands / feet ---------------------------------------------------
        # Custom.PerpendicularVector(v) is (-v.y, v.x) in the game's +y-UP frame
        # (`Custom::PerpendicularVector`, IL: Normalize then (-y, x)). This sim
        # runs with +y DOWN, and mirroring an axis flips chirality, so the same
        # helper becomes (v.y, -v.x) here. Getting that sign wrong put every
        # foot/hand on the mirror side of its own knee/attach point, which is
        # what made the legs bow inwards and the sleeves cross the body.
        side = C.dir_vec(self.chunk0, self.chunk1)          # down the body axis
        perp = (side[1], -side[0])
        host_vel = (self.vel0[0], self.vel0[1])
        lower_vel = (self.vel1[0], self.vel1[1])
        for index in (0, 1):
            sign = -1.0 if index == 0 else 1.0
            # og.cs:1644-1652, in the source's order: Update() (pos += vel, then
            # vel *= 0.98) -> ConnectToPoint -> and only THEN the steering terms.
            # Adding the steering before the tether made this tick's displacement
            # carry it and handed it straight to `_tether`'s 0.7 damping, so the
            # limbs got 30 % less guidance than the source -- the same ordering
            # bug the head had.
            #
            # og.cs:1651  hands: ConnectToPoint(firstChunk.pos, 15f, push: false,
            # 0f, firstChunk.vel, 0.3f, 0.01f) -- anchor is the chunk's centre.
            hand_anchor = self.chunk0
            hand = (self.hands[index][0] + self.hand_vel[index][0],
                    self.hands[index][1] + self.hand_vel[index][1])
            hv = (self.hand_vel[index][0] * C.LIMB_AIR_FRICTION,
                  self.hand_vel[index][1] * C.LIMB_AIR_FRICTION)
            self.hands[index], self.hand_vel[index] = _tether(
                hand, hv, hand_anchor, C.HAND_TETHER, host_vel)
            self.hand_vel[index] = (
                self.hand_vel[index][0] + side[0] * 0.3 + perp[0] * 0.3 * sign,
                self.hand_vel[index][1] + side[1] * 0.3 + perp[1] * 0.3 * sign + 0.5,
            )

            # og.cs:1643  ConnectToPoint(bodyChunks[1].pos, 10f, ...) and only the
            # Moon variant adds the extra vel.y -= 0.5f, so no +0.5 here. Its unit
            # direction is (0.447, -0.894): 8.94 px below, 4.47 px outboard.
            foot_anchor = self.chunk1
            foot = (self.feet[index][0] + self.foot_vel[index][0],
                    self.feet[index][1] + self.foot_vel[index][1])
            fv = (self.foot_vel[index][0] * C.LIMB_AIR_FRICTION,
                  self.foot_vel[index][1] * C.LIMB_AIR_FRICTION)
            self.feet[index], self.foot_vel[index] = _tether(
                foot, fv, foot_anchor, C.FOOT_TETHER, lower_vel)
            self.foot_vel[index] = (
                self.foot_vel[index][0] + side[0] * 0.3 + perp[0] * 0.15 * sign,
                self.foot_vel[index][1] + side[1] * 0.3 + perp[1] * 0.15 * sign,
            )

    # -- derived -----------------------------------------------------------
    @property
    def body_dir(self) -> tuple[float, float]:
        return C.dir_vec(self.chunk1, self.chunk0)

    @property
    def knees(self) -> list[tuple[float, float]]:
        """og.cs:1770 -- the ``!IsMoon`` (Five Pebbles) branch.

        ``knees[j,0] = (feet[j].pos + bodyChunks[1].pos) / 2
                       + PerpendicularVector(DirVec(firstChunk, bodyChunks[1]))
                         * (4f * ((j == 0) ? (-1f) : 1f))``

        Note the 5f and the 0.4 smoothing / 15 px clamp at og.cs:1788-1794 belong
        to the Looks to the Moon branch; this one is a plain assignment.
        """
        down = C.dir_vec(self.chunk0, self.chunk1)
        perp = (down[1], -down[0])
        out = []
        for index in (0, 1):
            sign = -1.0 if index == 0 else 1.0
            foot = self.feet[index]
            mid = ((foot[0] + self.chunk1[0]) * 0.5, (foot[1] + self.chunk1[1]) * 0.5)
            out.append((mid[0] + perp[0] * C.KNEE_BOW * sign,
                        mid[1] + perp[1] * C.KNEE_BOW * sign))
        return out


def _tether(point: tuple[float, float], vel: tuple[float, float],
            anchor: tuple[float, float], radius: float,
            host_vel: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """``BodyPart.ConnectToPoint(pnt, rad, push:false, elastic:0, hostVel, 0.3, 0.01)``.

    Transcribed from the IL rather than from the call site, because the call site
    hides three effects that matter:

        vel += hostVel * exaggerateVel                       // 0.01
        if |pos - pnt| > rad:
            d = DirVec(pos, pnt) * (rad - dist)               // points *away*
            pos -= d;  vel -= d                               // ...so both move in
        vel -= hostVel
        vel *= 1 - adaptVel                                   // 0.3 -> 0.7
        vel += hostVel                                        // IL_00d8 -- put the
                                                              // host's motion back
    """
    vx = vel[0] + host_vel[0] * C.LIMB_HOST_EXAGGERATE
    vy = vel[1] + host_vel[1] * C.LIMB_HOST_EXAGGERATE
    dx, dy = anchor[0] - point[0], anchor[1] - point[1]
    distance = hypot(dx, dy)
    if distance > radius and distance > 1e-9:
        ux, uy = dx / distance, dy / distance
        corr_x, corr_y = ux * (radius - distance), uy * (radius - distance)
        point = (point[0] - corr_x, point[1] - corr_y)
        vx -= corr_x
        vy -= corr_y
    adapt = 1.0 - C.LIMB_ADAPT_VEL
    return point, ((vx - host_vel[0]) * adapt + host_vel[0],
                   (vy - host_vel[1]) * adapt + host_vel[1])


def _weld(point: tuple[float, float], vel: tuple[float, float],
          anchor: tuple[float, float], radius: float,
          host_vel: tuple[float, float],
          fallback_dir: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """``BodyPart.ConnectToPoint(pnt, rad, push: true, 0f, hostVel, 0.5f, 0.01f)``.

    This is the **other** branch of the same function ``_tether`` ports. The IL
    (``IL_0054``-``IL_0064``) reads

        if (push || !DistLess(pos, pnt, rad)) {        // push skips the test
            dist = Distance(pos, pnt);
            d    = DirVec(pos, pnt) * (rad - dist);    // DirVec points pos -> pnt
            pos -= d;
            vel -= d;
        }
        vel -= hostVel;  vel *= 1 - adaptVel;  vel += hostVel;

    so with ``push`` the part is placed exactly on the circle **along whatever
    direction it already sits**, and the correction is also subtracted from the
    velocity. Nothing here decides *where* on the circle the part ends up -- the
    caller's impulse does. That is the whole head constraint.
    """
    vx = vel[0] + host_vel[0] * C.HEAD_EXAGGERATE_VEL
    vy = vel[1] + host_vel[1] * C.HEAD_EXAGGERATE_VEL
    dx, dy = anchor[0] - point[0], anchor[1] - point[1]
    distance = hypot(dx, dy)
    if distance < 1e-6:
        # DirVec of a zero vector is (0,0) and the head would be stuck on the
        # anchor forever; the source never hits this, we just need an out.
        point = (anchor[0] + fallback_dir[0] * radius,
                 anchor[1] + fallback_dir[1] * radius)
    else:
        ux, uy = dx / distance, dy / distance
        corr_x, corr_y = ux * (radius - distance), uy * (radius - distance)
        point = (point[0] - corr_x, point[1] - corr_y)
        vx -= corr_x
        vy -= corr_y
    adapt = 1.0 - C.HEAD_ADAPT_VEL
    return point, ((vx - host_vel[0]) * adapt + host_vel[0],
                   (vy - host_vel[1]) * adapt + host_vel[1])
