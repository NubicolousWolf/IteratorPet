"""``OracleGraphics.Gown`` and ``OracleGraphics.Halo``.

Gown: an 11x11 cloth grid whose ideal shape is a widening tube hanging off the
body axis. Each point is pulled toward its ideal position with a tolerance that
grows toward the hem (neck tight, hem loose), plus a 4-neighbour spring -- that
is what makes the robe billow instead of reading as a rigid cone.

Halo: concentric rings of ``MemoryBit`` ticks (10 / 30 / 60) around a point 20 px
behind the head, each ring rotating at its own rate, plus occasional arcing
connections. In SS_AI the room's amber lights make all of it glow orange.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import cos, hypot, pi, sin

from . import constants as C


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# --------------------------------------------------------------------------
# gown
# --------------------------------------------------------------------------
class Gown:
    def __init__(self, body: tuple[float, float], body_dir: tuple[float, float]):
        divs = C.GOWN_DIVS
        self.divs = divs
        self.pos = [[body for _ in range(divs)] for _ in range(divs)]
        self.last = [[body for _ in range(divs)] for _ in range(divs)]
        self.vel = [[(0.0, 0.0) for _ in range(divs)] for _ in range(divs)]
        for i in range(divs):
            for j in range(divs):
                self.pos[i][j] = self._ideal(i, j, body, body_dir)
                self.last[i][j] = self.pos[i][j]

    # -- shape -------------------------------------------------------------
    def _ideal(
        self,
        x: int,
        y: int,
        body: tuple[float, float],
        direction: tuple[float, float],
    ) -> tuple[float, float]:
        divs = self.divs
        u = x / (divs - 1)
        t = y / (divs - 1)
        perp = (-direction[1], direction[0])
        half = _lerp(C.GOWN_NECK_HALF, C.GOWN_HEM_HALF, t)
        lateral = _lerp(-1.0, 1.0, u) * half
        drop = _lerp(C.GOWN_NECK_HALF, -(C.GOWN_HEM_HALF + 7.0), t)
        drop *= 1.0 + sin(pi * u) * 0.35 * _lerp(-1.0, 1.0, t)
        return (
            body[0] + perp[0] * lateral + direction[0] * drop,
            body[1] + perp[1] * lateral + direction[1] * drop,
        )

    # -- simulation --------------------------------------------------------
    def step(
        self,
        body: tuple[float, float],
        body_dir: tuple[float, float],
        lower: tuple[float, float],
    ) -> None:
        divs = self.divs
        for i in range(divs):
            for j in range(divs):
                px, py = self.pos[i][j]
                vx, vy = self.vel[i][j]
                self.last[i][j] = (px, py)
                px += vx
                py += vy
                vx *= C.GOWN_VEL_DAMPING
                vy *= C.GOWN_VEL_DAMPING
                # gravity 0 in SS_AI rooms, so nothing is added here

                ideal = self._ideal(i, j, body, body_dir)
                t = j / (divs - 1)
                # Slerp(-dir, DirVec(lower, ideal), t) * 0.02 keeps the hem
                # trailing behind the body instead of snapping to the axis.
                to_ideal = C.dir_vec(lower, ideal)
                blend = _slerp((-body_dir[0], -body_dir[1]), to_ideal, t)
                vx += blend[0] * 0.02
                vy += blend[1] * 0.02

                tolerance = _lerp(0.0, C.GOWN_TOLERANCE, t)
                dx, dy = ideal[0] - px, ideal[1] - py
                distance = hypot(dx, dy)
                if distance > tolerance and distance > 1e-6:
                    pull = (distance - tolerance)
                    ux, uy = dx / distance, dy / distance
                    px += ux * pull
                    py += uy * pull
                    vx += ux * pull
                    vy += uy * pull

                self.pos[i][j] = (px, py)
                self.vel[i][j] = (vx, vy)

        # 4-neighbour spring (0.05) reshapes the cloth.
        for i in range(divs):
            for j in range(divs):
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < divs and 0 <= nj < divs):
                        continue
                    a = self.pos[i][j]
                    b = self.pos[ni][nj]
                    direction = C.dir_vec(a, b)
                    ideal_a = self._ideal(i, j, body, body_dir)
                    ideal_b = self._ideal(ni, nj, body, body_dir)
                    target = C.dist(ideal_a, ideal_b)
                    current = C.dist(a, b)
                    push = (target - current) * C.GOWN_NEIGHBOUR_SPRING
                    va = self.vel[i][j]
                    vb = self.vel[ni][nj]
                    self.vel[i][j] = (va[0] - direction[0] * push, va[1] - direction[1] * push)
                    self.vel[ni][nj] = (vb[0] + direction[0] * push, vb[1] + direction[1] * push)


def _slerp(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    from math import atan2

    a0 = atan2(a[1], a[0])
    a1 = atan2(b[1], b[0])
    delta = (a1 - a0 + pi) % (2 * pi) - pi
    angle = a0 + delta * t
    return (cos(angle), sin(angle))


# --------------------------------------------------------------------------
# halo
# --------------------------------------------------------------------------
@dataclass
class HaloBit:
    ring: int
    angle: float
    length: float
    brightness: float


@dataclass
class HaloConnection:
    a: tuple[float, float]
    b: tuple[float, float]
    control: tuple[float, float]
    light: float


class Halo:
    def __init__(self, seed: int = 11):
        self.rng = random.Random(seed)
        self.ring_rotation = [0.0, 0.0, 0.0]
        self.ring_target = [0.0, 0.0, 0.0]
        self.ring_timer = [0, 0, 0]
        self.expand = C.HALO_EXPAND_BASE
        self.push = C.HALO_PUSH_BASE
        self.bits = [
            HaloBit(ring, self.rng.uniform(0.0, 6.28318), 0.0, 0.0)
            for ring, count in enumerate(C.HALO_RINGS)
            for _ in range(count)
        ]
        self.connections = [
            HaloConnection((0.0, 0.0), (0.0, 0.0), (0.0, 0.0), 0.0)
            for _ in range(C.HALO_CONNECTIONS)
        ]
        self._connection_seed()

    def _connection_seed(self) -> None:
        for connection in self.connections:
            connection.light = self.rng.uniform(0.0, 1.0)
            connection.control = (self.rng.uniform(-1.0, 1.0), self.rng.uniform(-1.0, 1.0))

    def step(self, center: tuple[float, float], body_dir: tuple[float, float], ticks: int) -> None:
        self.expand = 1.0 + 0.06 * sin(ticks * 0.031)
        self.push = 0.35 * sin(ticks * 0.017 + 1.2)

        for ring in range(3):
            self.ring_timer[ring] -= 1
            if self.ring_timer[ring] <= 0:
                self.ring_target[ring] += self.rng.choice((-1.0, 1.0)) * self.rng.uniform(0.15, 0.6)
                self.ring_timer[ring] = self.rng.randint(35, 120)
            self.ring_rotation[ring] += (self.ring_target[ring] - self.ring_rotation[ring]) * 0.08

        for bit in self.bits:
            target = 1.0 if self.rng.random() < 0.06 else 0.0
            bit.brightness += (target - bit.brightness) * 0.15
            bit.length = 2.0 + 6.0 * bit.brightness

        for connection in self.connections:
            if self.rng.random() < 0.01:
                connection.light = 1.0
                connection.control = (self.rng.uniform(-1.0, 1.0), self.rng.uniform(-1.0, 1.0))
            connection.light *= 0.94

    def radius(self, ring: int) -> float:
        return (C.HALO_RADIUS_BASE + ring * 1.0 + self.push) * self.expand * 10.0

    def bit_positions(self, center: tuple[float, float]) -> list[tuple[HaloBit, tuple[float, float], tuple[float, float]]]:
        out = []
        for bit in self.bits:
            radius = self.radius(bit.ring)
            angle = bit.angle + self.ring_rotation[bit.ring]
            base = (center[0] + cos(angle) * radius, center[1] + sin(angle) * radius)
            tip = (
                center[0] + cos(angle) * (radius + bit.length),
                center[1] + sin(angle) * (radius + bit.length),
            )
            out.append((bit, base, tip))
        return out

    def connection_curves(self, center: tuple[float, float]) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], float]]:
        out = []
        for connection in self.connections:
            if connection.light < 0.05:
                continue
            radius = self.radius(2)
            a = (center[0] + connection.control[0] * radius * 1.4, center[1] + connection.control[1] * radius * 1.4)
            b = (center[0] - connection.control[1] * radius * 0.9, center[1] + connection.control[0] * radius * 0.9)
            control = (center[0] + connection.control[0] * radius * 0.2, center[1] + connection.control[1] * radius * 0.2)
            out.append((a, b, control, connection.light))
        return out
