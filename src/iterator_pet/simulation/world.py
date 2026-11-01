"""电脑状态 → 她自己的收敛/休眠（2026-11-01 第二组）。

**这一层不碰 Qt、不碰 ctypes**：`platform/sensors.py` 只负责把 Windows 的数字读出来，
所有"读出来之后怎么反应"都在这里，纯函数、可单测。

"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C


@dataclass(frozen=True)
class WorldState:
    """传感器一次采样的**原始**读数（0–1 的百分比，不是 0–100）。"""

    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    battery_pct: float | None = None    # None = 没有电池（台式机）
    on_ac: bool = True
    user_idle_s: float = 0.0            # 距离最后一次键鼠输入多少秒
    fullscreen: bool = False            # 前台窗口铺满显示器
    hour: int = 12
    valid: bool = False                 # 采样失败（非 Windows / API 报错）时为 False

    def status(self) -> dict:
        return {
            "cpu": round(self.cpu_pct, 3),
            "mem": round(self.mem_pct, 3),
            "battery": None if self.battery_pct is None else round(self.battery_pct, 3),
            "on_ac": self.on_ac,
            "idle_s": round(self.user_idle_s, 1),
            "fullscreen": self.fullscreen,
            "hour": self.hour,
            "valid": self.valid,
        }


@dataclass
class WorldReactions:
    """把读数变成"她这一刻该多暗、电光多少、要不要睡"。"""

    cpu: float = 0.0
    cpu_high: bool = False
    dim: float = 1.0
    sparks: float = 1.0
    bits: int = -1               # -1 = 全部 100 位（开关关掉时的默认）
    night: bool = False
    quiet: bool = False          # 全屏：不主动搭话、不自言自语
    sleep_wanted: bool = False
    wake_signal: bool = False
    low_battery: bool = False
    reflect_memory: bool = False
    _low_notified: bool = field(default=False, repr=False)

    # -- 每帧（1 Hz）--------------------------------------------------------
    def update(self, world: WorldState, reflect_memory: bool = False) -> None:
        self.reflect_memory = bool(reflect_memory)
        self.night = world.hour >= C.WORLD_NIGHT_START or world.hour < C.WORLD_NIGHT_END
        self.quiet = bool(world.fullscreen)
        if not world.valid:
            # 读不到就当没有波动：保留上一帧的收敛值慢慢回弹，别突然全亮。
            self.cpu = max(0.0, self.cpu - C.WORLD_CPU_SMOOTH * self.cpu)
            self._settle_dim(battery_low=False)
            self.sparks = 1.0
            return

        self.cpu += (max(0.0, min(1.0, world.cpu_pct)) - self.cpu) * C.WORLD_CPU_SMOOTH
        if not self.cpu_high and self.cpu >= C.WORLD_CPU_HIGH:
            self.cpu_high = True
        elif self.cpu_high and self.cpu <= C.WORLD_CPU_LOW:
            self.cpu_high = False

        battery_low = self._battery_low(world)
        self._settle_dim(battery_low=battery_low)
        if self.cpu_high:
            self.sparks = C.WORLD_SPARKS_CPU
        elif self.night:
            self.sparks = C.WORLD_SPARKS_NIGHT
        else:
            self.sparks = 1.0

        self.bits = (int(round(sum(C.HALO_RINGS) * (C.WORLD_MEM_BITS_MIN
                                                   + (1.0 - C.WORLD_MEM_BITS_MIN)
                                                   * max(0.0, min(1.0, world.mem_pct)))))
                     if self.reflect_memory else -1)

        # 休眠：很久没输入，或者深夜人不在。醒来只看"输入回来了"。
        self.sleep_wanted = (world.user_idle_s >= C.WORLD_SLEEP_AFTER_S
                             or (self.night and world.user_idle_s >= C.WORLD_SLEEP_AFTER_S / 4))
        self.wake_signal = world.user_idle_s < C.WORLD_WAKE_IDLE_S

    def _battery_low(self, world: WorldState) -> bool:
        if world.battery_pct is None or world.on_ac:
            if world.on_ac:
                self._low_notified = False      # 插上电：重新武装
            return False
        pct = max(0.0, min(1.0, world.battery_pct))
        if pct >= C.WORLD_BATTERY_RECOVER:
            self._low_notified = False
        low = pct <= C.WORLD_BATTERY_LOW
        if low and not self._low_notified:
            self._low_notified = True
            self.low_battery = True             # 由 state 取走（消费即清）
        return low

    def _settle_dim(self, battery_low: bool) -> None:
        target = 1.0
        if self.cpu_high:
            target *= C.WORLD_DIM_CPU
        if battery_low:
            target *= C.WORLD_DIM_BATTERY
        if self.night:
            target *= C.WORLD_DIM_NIGHT
        # 亮度也走 EMA：突然变暗/变亮比"暗着"更像 bug。
        self.dim += (target - self.dim) * C.WORLD_CPU_SMOOTH

    def take_low_battery(self) -> bool:
        """低电量提醒是**一次性**的：取走就清。"""
        if not self.low_battery:
            return False
        self.low_battery = False
        return True

    def meditate_gain(self) -> float:
        """深夜更容易打坐（`ss.cs:3170` 构造时就是 50/50 抽 Meditate/Idle）。"""
        return C.WORLD_MEDITATE_NIGHT_GAIN if self.night else 1.0

    def status(self) -> dict:
        return {
            "cpu": round(self.cpu, 3),
            "cpu_high": self.cpu_high,
            "dim": round(self.dim, 3),
            "sparks": round(self.sparks, 3),
            "bits": self.bits,
            "night": self.night,
            "quiet": self.quiet,
            "sleep_wanted": self.sleep_wanted,
            "reflect_memory": self.reflect_memory,
        }
