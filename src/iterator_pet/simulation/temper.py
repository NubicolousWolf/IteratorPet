"""心情（temper）与"杀鼠标"仪式，纯数据、不碰 Qt、不碰渲染。

**为什么叫 temper 不叫 mood**：三态行为那边已经用 "mood" 指 roaming/investigate/talk
（`constants.py` 的注释、`status()["excluded"]`），同一个词两个意思会混淆。

设计依据（详见 `docs/PLAN-kill.md`）：
* 心情：用户要"任何拖拽都掉心情"，但**只在真位移时**扣（按住不动不该 20 秒后被杀）；
  速度为**屏幕 px/s**，四档尺寸下到闸时间一致；掉到 `TEMPER_KILL_GATE` 就强制打断。
* 仪式：`killFac += 0.025/tick`（40 tick 胀满，`ss.cs:2790`）；光标跑开 `*= 0.8`
  （`ss.cs:2803`）；胀满那一 tick 产 20 颗白 spark（`ss.cs:2794-2799`）。
"""

from __future__ import annotations

import math
import random

from . import constants as C

IDLE, WARNING, CHARGING, FLASH, COOLDOWN = "idle", "warning", "charging", "flash", "cooldown"


class Temper:
    """心情：−1 … +1，负值代表被惹。"""

    __slots__ = ("value", "rng")

    def __init__(self) -> None:
        self.value = 0.0
        self.rng = random.Random(7)

    # -- 掉心情 -------------------------------------------------------------
    def drain_drag(self, screen_speed: float, moved: bool) -> None:
        """拖拽中每 tick 调一次。``screen_speed`` 单位是屏幕 px/s。

        ``moved`` 为假（按住不动）时不扣基础项 —— 否则"把桌宠挪开、手一直按着"
        20 秒就会被杀（审计 P1-5）。
        """
        if not moved:
            return
        ratio = min(1.0, max(0.0, screen_speed) / C.TEMPER_SCREEN_SPEED_CAP)
        self.value -= C.TEMPER_DRAG_BASE + C.TEMPER_DRAG_SPEED_K * ratio
        if self.value < -1.0:
            self.value = -1.0

    def drain_fling(self, screen_speed: float) -> None:
        ratio = min(1.0, max(0.0, screen_speed) / C.TEMPER_SCREEN_SPEED_CAP)
        self.value -= C.TEMPER_FLING_GAIN * ratio
        if self.value < -1.0:
            self.value = -1.0

    # -- 回中 ---------------------------------------------------------------
    def tick(self, cooldown: bool = False) -> None:
        """不被打扰时朝 0 回中。``cooldown`` 期间不回（冷却就是"消气"，见计划 §3.1）。"""
        if cooldown:
            return
        if self.value < 0.0:
            self.value = min(0.0, self.value + C.TEMPER_DECAY)

    def reset(self) -> None:
        self.value = 0.0

    @property
    def angry(self) -> bool:
        return self.value <= C.TEMPER_KILL_GATE


class KillRitual:
    """杀鼠标的仪式状态机（只有状态，没有美术）。"""

    __slots__ = ("phase", "kill_fac", "_t", "cooldown", "anchor", "focus", "sparks", "rng",
                 "retreat", "_still", "_lost", "tremble_only")

    def __init__(self) -> None:
        self.phase = IDLE
        self.kill_fac = 0.0
        self._t = 0
        self.cooldown = 0
        # anchor = 仪式开始那一刻光标的"锁定点"（固定）；focus = 白光此刻的位置（跟着光标）
        self.anchor: tuple[float, float] | None = None
        self.focus: tuple[float, float] | None = None
        self.sparks: list[list[float]] = []
        self.rng = random.Random(11)
        # 挣脱目标（人偶要退到离光标 1.5×talk 距离处站定）；None = 还没算
        self.retreat: tuple[float, float] | None = None
        self._still = 0
        # 角色是否只有"挣脱"、没有后续的白光（过去之月，用户 2026-10-29 拍板）
        self.tremble_only = False

    # -- 查询 ---------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.phase in (WARNING, CHARGING, FLASH)

    @property
    def flash_progress(self) -> float:
        """白闪进度 1→0（只在 flash 段非 0）。渲染用它算"闪一下"的 alpha。"""
        if self.phase != FLASH:
            return 0.0
        return max(0.0, 1.0 - self._t / max(1, C.KILL_FLASH_TICKS))

    @property
    def blocked(self) -> bool:
        """**仪式进行中**才不许拖拽（用户 2026-10-29：只有动画期间拖不动）。

        冷却期只是"不许再触发"，不影响拖拽 —— 否则一次杀鼠标之后 10 秒都拖不动他，
        手感上就是"坏了"。
        """
        return self.active

    # -- 状态迁移 -----------------------------------------------------------
    def begin(self, anchor: tuple[float, float] | None, ticks: int,
              tremble_only: bool = False) -> None:
        self.tremble_only = tremble_only
        self.phase = WARNING
        self.kill_fac = 0.0
        self._t = 0
        self.anchor = anchor
        self.focus = anchor
        self.retreat = None
        self._still = 0
        self._lost = 0
        self.cooldown = 0

    def cancel(self, ticks: int = 0) -> None:
        self.phase = IDLE
        self.kill_fac = 0.0
        self._t = 0
        self.anchor = None
        self.focus = None
        self.retreat = None
        self._still = 0
        self.sparks.clear()

    def abort(self) -> None:
        """蓄能失败（光标跑开）：回 idle，但给一条独立短冷却避免立刻重入。"""
        self.phase = IDLE
        self.kill_fac = 0.0
        self._t = 0
        self.anchor = None
        self.focus = None
        self.cooldown = C.KILL_ABORT_COOLDOWN

    def tick_cooldown(self) -> None:
        """冷却期也要每 tick 走：``step()`` 只在仪式 active 时被调，冷却期没人推就会永远 blocked。

        注意 abort 之后相位是 IDLE 但 ``cooldown`` 仍 > 0（那是"蓄能失败"的短冷却），
        所以这里按**计数**递减，不能只在 COOLDOWN 相位里减。
        """
        if self.cooldown <= 0:
            return
        self.cooldown -= 1
        if self.phase == COOLDOWN:
            self._t += 1
            if self.cooldown <= 0:
                self.phase = IDLE
                self._t = 0

    # -- 每 tick ------------------------------------------------------------
    def step(self, cursor: tuple[float, float] | None, lock_dist_sim: float) -> str | None:
        """推进一步。返回本 tick 发生的事件：``"flash"`` / ``None``。"""
        if self.phase == IDLE:
            return None

        if self.phase == WARNING:
            self._t += 1
            # 警告段只跟"视觉焦点"，锁定点固定不动（否则后面永远量不出"跑开"）
            if cursor is not None:
                self.focus = cursor
            if self._t >= C.KILL_WARNING_TICKS + C.KILL_STAND_TICKS:
                if self.tremble_only:
                    # 过去之月：挣脱+站定之后**到此为止**（用户 2026-10-29：删掉杀鼠标，
                    # 只保留挣脱）。abort() 顺带给了 KILL_ABORT_COOLDOWN 的短冷却，
                    # kill_fac 全程为 0 → 渲染层一次 draw_kill 都不会画。
                    self.abort()
                    return "tremble_done"
                self.phase = CHARGING
                self._t = 0
            return None

        if self.phase == CHARGING:
            if cursor is not None:
                # 用户 2026-10-29：挣脱之后**必须杀到**。所以锁定点跟着光标走（他跑不掉），
                # 这一步刻意偏离了"跑开就衰减"的源码条件（源码的条件是"玩家不在房间"）。
                self.anchor = cursor
                self.focus = cursor
                self._lost = 0
            else:
                self._lost += 1
            if cursor is None:
                # 光标离开屏幕 ≈ 源码的"玩家不在房间"：这时才按 ×0.8 衰减/放弃
                self.kill_fac *= C.KILL_FAC_DECAY
                if self._lost > C.KILL_ESCAPE_TICKS:
                    self.abort()
                    return "abort"
                return None
            # 光标在屏上：一路蓄满（追着光标）。用户口径："触发挣脱就一定杀到"。
            self.kill_fac = min(1.0, self.kill_fac + C.KILL_FAC_RATE)
            if self.kill_fac >= 1.0:
                self.phase = FLASH
                self._t = 0
                self._spawn_sparks()
                return "flash"
            return None

        if self.phase == FLASH:
            self._t += 1
            if self._t >= C.KILL_FLASH_TICKS:
                self.phase = COOLDOWN
                self._t = 0
                self.cooldown = C.KILL_COOLDOWN_TICKS
                self.kill_fac = 0.0
            return None

        if self.phase == COOLDOWN:
            self._t += 1
            self.cooldown = max(0, self.cooldown - 1)
            if self.cooldown <= 0:
                self.phase = IDLE
                self._t = 0
            return None

        return None

    # -- 火花（照 ss.cs:2794-2797 的参数；不放 RenderState，避免插值吞列表） ---
    def _spawn_sparks(self) -> None:
        self.sparks.clear()
        cx, cy = self.focus if self.focus is not None else (0.0, 0.0)
        for i in range(C.KILL_SPARK_COUNT):
            ang = (i / C.KILL_SPARK_COUNT) * math.tau + self.rng.random() * 0.6
            speed = self.rng.random() * C.KILL_SPARK_SPEED_MAX
            life = self.rng.randint(*C.KILL_SPARK_LIFE)
            self.sparks.append([cx, cy, math.cos(ang) * speed, math.sin(ang) * speed,
                                float(life), float(C.KILL_SPARK_LIFE[1])])

    def tick_sparks(self) -> None:
        """火花掉寿命（仍是纯数据；渲染只管画）。"""
        alive = []
        for spark in self.sparks:
            spark[0] += spark[2]
            spark[1] += spark[3]
            spark[2] *= 0.93
            spark[3] = spark[3] * 0.93 + 0.45
            spark[4] -= 1.0
            if spark[4] > 0:
                alive.append(spark)
        self.sparks = alive
