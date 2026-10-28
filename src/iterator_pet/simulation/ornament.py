"""``OracleGraphics.Gown`` and ``OracleGraphics.Halo``.

Gown: an 11x11 cloth grid whose ideal shape is a widening tube hanging off the
body axis. Each point is pulled toward its ideal position with a tolerance that
grows toward the hem (neck tight, hem loose), plus a 4-neighbour spring -- that
is what makes the robe billow instead of reading as a rigid cone.

Halo: two ring sprites + 100 ``MemoryBit`` ticks (10 / 30 / 60) + 20 arcing
connections around a point 20 px behind the head. The sprites are BLACK in the
source; the amber look in-game comes from the ``ForegroundLights`` lamp drawn on
top of them. Every number comes from ``og.cs:419-750`` (see docs/PLAN-halo.md).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import cos, hypot, pi, sin

from . import constants as C
from .easing import lerp_and_tick, s_curve


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
class MemoryBit:
    """og.cs:442-501 —— 一个内存 bit。``filled`` 是它此刻的填充度（0..1）。"""

    ring: int
    index: int
    filled: float
    last_filled: float
    get_to_filled: float
    fill_speed: float = 0.0
    blink_counter: int = 0

    def set_to_max(self) -> None:
        """og.cs:447 ``SetToMax``：充满 + 开始闪烁。"""
        self.get_to_filled = 1.0
        self.fill_speed = 0.25
        self.blink_counter = 20

    def update(self, rng: random.Random) -> None:
        """og.cs:449-480 ``Update``。"""
        self.last_filled = self.filled
        if self.filled != self.get_to_filled:
            self.filled = lerp_and_tick(
                self.filled, self.get_to_filled, C.HALO_BIT_FILL_LERP, self.fill_speed
            )
        elif self.blink_counter > 0:
            self.blink_counter -= 1
        elif rng.random() < C.HALO_BIT_REROLL_CHANCE:
            u = rng.random()
            self.get_to_filled = u
            # og.cs: fillSpeed = 1 / Lerp(2, 80, U)：目标越接近空，涨回去越快。
            self.fill_speed = 1.0 / (2.0 + (80.0 - 2.0) * u)

    def fill(self) -> float:
        """og.cs:483 ``Fill``：``blinkCounter % 4 > 1`` 且已到目标时整段熄灭。"""
        if self.blink_counter % 4 > 1 and self.filled == self.get_to_filled:
            return 0.0
        return self.filled


@dataclass
class Connection:
    """og.cs:419-440 —— 一根电光。锚点在构造时定死，之后只变 ``light_up``。"""

    stuck_at: tuple[float, float]
    handle: tuple[float, float]
    light_up: float = 0.0
    last_light_up: float = 0.0


class Halo:
    """og.cs:548-750 ``OracleGraphics.Halo`` 的状态机（不含 sprite 与音效）。"""

    ROWS = 10          # 源码 ringRotations 是 10 行；Update 迭代 10 行（会消耗 RNG）

    def __init__(self, bounds: tuple[float, float, float, float] | None = None,
                 seed: int = 11) -> None:
        self.rng = random.Random(seed)
        self.bounds = bounds
        # 三个缓动量 + 各自的目标（og.cs:563 / 621-647）
        self.expand = C.HALO_EXPAND_BASE
        self.last_expand = self.expand
        self.get_to_expand = self.expand
        self.push = C.HALO_PUSH_BASE
        self.last_push = self.push
        self.get_to_push = self.push
        self.white = C.HALO_WHITE_BASE
        self.last_white = self.white
        self.get_to_white = self.white
        # ringRotations[10, 5] = [current, last, start, target, progress]
        self.ring_rot = [[0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(self.ROWS)]
        # 100 个 bit：构造时 filled = lastFilled = getToFilled = U(0,1)，fillSpeed = 0
        self.bits: list[MemoryBit] = []
        for ring, count in enumerate(C.HALO_RINGS):
            for index in range(count):
                value = self.rng.random()
                self.bits.append(MemoryBit(ring, index, value, value, value))
        self.connections: list[Connection] = []
        # 构造函数里先给一个 Pow(U,3)（og.cs:433）
        self.connections_fire_chance = self.rng.random() ** 3
        self.conscious = True
        # 电光频率倍数（off=0 / eco=1 / computing=1.5）
        self.spark_rate = C.HALO_SPARK_RATES[C.HALO_SPARK_DEFAULT_RATE]

    # -- 构造期采样 --------------------------------------------------------
    def _random_unit(self) -> tuple[float, float]:
        """``RNV()``：随机单位向量（游戏里是 ``DegToVec(Range(0, 360))``）。"""
        angle = self.rng.uniform(0.0, 2.0 * pi)
        return (sin(angle), cos(angle))

    def seed_connections(self) -> None:
        """og.cs:419-440 —— 20 根电光的锚点/控制柄，构造后不再变。"""
        self.connections = []
        if self.bounds is None:
            self.connections = [Connection((0.0, 0.0), (0.0, 0.0)) for _ in range(C.HALO_CONNECTIONS)]
            return
        x, y, w, h = self.bounds
        room_center = (x + w * 0.5, y + h * 0.5)
        for _ in range(C.HALO_CONNECTIONS):
            # sample = 房间中心 + RNV() * Lerp(300, 500, U)；**未夹取**（handle 的基准是它）
            reach = C.HALO_ANCHOR_MIN + (C.HALO_ANCHOR_MAX - C.HALO_ANCHOR_MIN) * self.rng.random()
            direction = self._random_unit()
            sample = (room_center[0] + direction[0] * reach, room_center[1] + direction[1] * reach)
            # b = Clamp(sample, 轨道矩形)；stuckAt = Lerp(sample, b, 0.5)：只往矩形里挪一半
            clamped = (
                min(max(sample[0], x), x + w),
                min(max(sample[1], y), y + h),
            )
            stuck_at = (
                sample[0] + (clamped[0] - sample[0]) * 0.5,
                sample[1] + (clamped[1] - sample[1]) * 0.5,
            )
            handle_reach = C.HALO_HANDLE_MIN + (
                C.HALO_HANDLE_MAX - C.HALO_HANDLE_MIN
            ) * self.rng.random()
            handle_dir = self._random_unit()
            handle = (
                sample[0] + handle_dir[0] * handle_reach,
                sample[1] + handle_dir[1] * handle_reach,
            )
            self.connections.append(Connection(stuck_at, handle))

    def resize(self, bounds: tuple[float, float, float, float]) -> None:
        """窗口尺寸变了：锚点是「房间坐标」，必须重采样（PLAN-halo §6.4）。"""
        self.bounds = bounds
        self.seed_connections()

    # -- 每 tick -----------------------------------------------------------
    def step(self, center: tuple[float, float], body_dir: tuple[float, float],
             ticks: int, conscious: bool | None = None) -> None:
        """og.cs:567-648 ``Halo.Update`` —— 顺序照源码，一步不换。"""
        conscious = self.conscious if conscious is None else conscious

        # 1. 电光：先归档 last、再乘衰减、最后按概率开火（Consious 只门控开火这一行）
        chance = self.connections_fire_chance / C.HALO_SPARK_FIRE_DIVISOR * self.spark_rate
        for connection in self.connections:
            connection.last_light_up = connection.light_up
            connection.light_up *= C.HALO_SPARK_DECAY
            if conscious and self.rng.random() < chance:
                connection.light_up = 1.0

        # 2. 每 tick 1/60 重掷开火概率（均值 0.25 → ~5 次/秒，同时可见 3-4 根）
        if self.rng.random() < C.HALO_SPARK_REROLL_CHANCE:
            self.connections_fire_chance = self.rng.random() ** 3

        # 3. 每圈旋转：绝对目标 ± 15..150°，SCurve 进度条（og.cs:587-605）
        for row in self.ring_rot:
            row[1] = row[0]
            if row[0] != row[3]:
                span = abs(row[2] - row[3])
                row[4] += 1.0 / (20.0 + (span - 20.0) * 0.5)
                row[0] = row[2] + (row[3] - row[2]) * s_curve(row[4], 0.5)
                if row[4] > 1.0:
                    row[4] = 0.0
                    row[2] = row[3]
                    row[0] = row[3]
            elif self.rng.random() < C.HALO_RING_ROTATE_CHANCE:
                sign = -1.0 if self.rng.random() < 0.5 else 1.0
                amount = C.HALO_RING_ROTATE_MIN + (
                    C.HALO_RING_ROTATE_MAX - C.HALO_RING_ROTATE_MIN
                ) * self.rng.random()
                row[3] = row[0] + sign * amount
                row[2] = row[0]
                row[4] = 0.0

        # 4. bits
        for bit in self.bits:
            bit.update(self.rng)

        # 5. 每 tick 1/60 挑一圈整体充能（这就是"光环一阵阵亮起来"的来源）
        if self.rng.random() < C.HALO_RING_CHARGE_CHANCE:
            ring = self.rng.randrange(len(C.HALO_RINGS))
            for bit in self.bits:
                if bit.ring == ring:
                    bit.set_to_max()

        # 6. 三个缓动量：先存档 last，再 LerpAndTick
        self.last_expand = self.expand
        self.expand = lerp_and_tick(
            self.expand, self.get_to_expand, C.HALO_EXPAND_LERP, C.HALO_EXPAND_TICK
        )
        self.last_push = self.push
        self.push = lerp_and_tick(
            self.push, self.get_to_push, C.HALO_PUSH_LERP, C.HALO_PUSH_TICK
        )
        self.last_white = self.white
        self.white = lerp_and_tick(
            self.white, self.get_to_white, C.HALO_WHITE_LERP, C.HALO_WHITE_TICK
        )

        # 7. 抽新目标（1/160；white 抽中 1 时 expand/push 一定走"非默认"分支）
        if self.rng.random() < C.HALO_TARGET_CHANCE:
            flash = self.rng.random() < C.HALO_WHITE_FLASH_CHANCE
            self.get_to_white = 1.0 if flash else 0.0
            if not flash and self.rng.random() < 0.5:
                self.get_to_expand = 1.0
            else:
                self.get_to_expand = C.HALO_EXPAND_MIN + (
                    C.HALO_EXPAND_MAX - C.HALO_EXPAND_MIN
                ) * (self.rng.random() ** 1.5)
            if not flash and self.rng.random() < 0.5:
                self.get_to_push = 0.0
            else:
                # getToPush = -1 + Range(0, Range(1, 6)) → {-1, 0, 1, 2, 3}
                hi = self.rng.randrange(1, 6)
                self.get_to_push = -1.0 + self.rng.randrange(0, hi)

    # -- 查询 --------------------------------------------------------------
    def set_spark_rate(self, name: str) -> None:
        """开发者菜单里切换电光频率（off / eco / computing）。"""
        self.spark_rate = C.HALO_SPARK_RATES.get(name, 1.0)

    def radius(self, ring: float, voice: float = C.HALO_VOICE) -> float:
        return (C.HALO_RADIUS_BASE + ring + self.push - 0.5 * voice) * self.expand * 10.0

    def rotation(self, ring: int) -> float:
        return self.ring_rot[ring][0]

    def ring_alpha(self, ring: int) -> float:
        """og.cs:707-714：环 0 的 alpha 受 white 牵引（→1 时整盘填满），环 1 恒为 3/R。"""
        radius = self.radius(ring)
        if radius < 0.001:
            return 1.0
        thin = 3.0 / radius
        if ring == 0:
            return thin + (1.0 - thin) * self.white
        return min(1.0, thin)

    def bit_fills(self) -> list[float]:
        """100 个 bit 的填充度；渲染层按键数自己算角度（顺序 = 构造顺序）。"""
        return [bit.fill() for bit in self.bits]
