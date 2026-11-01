"""Windows 上的状态采样（2026-11-01 第二组）。

**只读**：全是 `Get*`，不写注册表、不碰配置、不联网。全部 `ctypes` 直调，
**不引入 psutil**（打包里少一个依赖就少一类坑，见 AGENTS §8.1 的打包血泪）。

给 `SimulationState.observe_world()` 喂 `WorldState`；采样失败（非 Windows、
API 返回 0、句柄拿不到）一律返回 ``valid=False`` 的默认值，仿真那边会把收敛值
慢慢回弹，不会突然全亮/全暗。

每个数字的量纲都写清楚：
* CPU 0–1（两次 `GetSystemTimes` 的增量，`1 - idle/total`，与任务管理器同口径）；
* 内存 0–1（`GlobalMemoryStatusEx.dwMemoryLoad`）；
* 电池 0–1 或 None（`BatteryLifePercent` 的 255 = 未知）；
* `user_idle_s` 秒（`GetLastInputInfo`）；
* `fullscreen` 前台窗口是否铺满它所在的显示器（±2 px 容差）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from ..simulation.world import WorldState

_IS_WINDOWS = hasattr(ctypes, "windll")


# ------------------------------------------------------------------ structs --
class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wt.BYTE),
        ("BatteryFlag", wt.BYTE),
        ("BatteryLifePercent", wt.BYTE),
        ("Reserved1", wt.BYTE),
        ("BatteryLifeTime", wt.DWORD),
        ("BatteryFullLifeTime", wt.DWORD),
    ]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                ("right", wt.LONG), ("bottom", wt.LONG)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wt.DWORD)]


# --------------------------------------------------------------- pure math --
def _ft(value: _FILETIME) -> int:
    """`FILETIME` → 100 ns 计数（两个 DWORD 拼起来）。"""
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def cpu_ratio(prev: tuple[int, int, int], cur: tuple[int, int, int]) -> float:
    """两次 `(idle, kernel, user)` 的增量 → 0–1 的占用率。

    `kernel` 已经包含 idle（Windows 的口径），所以总量 = kernel + user，
    空闲占比 = idle / 总量。一次采样窗口里没有增量时返回 0（不猜）。
    """
    idle = cur[0] - prev[0]
    total = (cur[1] - prev[1]) + (cur[2] - prev[2])
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - idle / total))


def battery_fraction(percent: int, ac_line: int) -> tuple[float | None, bool]:
    """`SYSTEM_POWER_STATUS` → ``(0–1 或 None, 是否插着电)``。255 = 未知。"""
    on_ac = ac_line != 0                 # 0 = 放电，1 = 交流电，255 = 未知
    if percent == 255:
        return None, on_ac
    return max(0.0, min(1.0, percent / 100.0)), on_ac


# ------------------------------------------------------------------ readers --
_CPU_PREV: tuple[int, int, int] | None = None


def _read_cpu() -> float:
    global _CPU_PREV
    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return 0.0
    cur = (_ft(idle), _ft(kernel), _ft(user))
    prev, _CPU_PREV = _CPU_PREV, cur
    return 0.0 if prev is None else cpu_ratio(prev, cur)


def _read_memory() -> float:
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0.0
    return max(0.0, min(1.0, status.dwMemoryLoad / 100.0))


def _read_battery() -> tuple[float | None, bool]:
    status = _SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None, True
    return battery_fraction(status.BatteryLifePercent, status.ACLineStatus)


def _read_idle_seconds() -> float:
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # GetTickCount64 是 64 位；dwTime 只有 32 位，两者相减**只在 32 位回绕的
    # 边界（49.7 天）会错一次**。按 32 位掩码对齐，普通开机时长下是正确的。
    now = int(ctypes.windll.kernel32.GetTickCount64()) & 0xFFFFFFFF
    delta = (now - int(info.dwTime)) & 0xFFFFFFFF
    return max(0.0, delta / 1000.0)


def _read_fullscreen() -> bool:
    """前台窗口是否铺满它所在的整个显示器（任务栏被盖住才算）。"""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return False
    rect = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    # MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST=2)
    monitor = user32.MonitorFromWindow(hwnd, 2)
    if not monitor:
        return False
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    screen = info.rcMonitor
    tol = 2
    return (abs(rect.left - screen.left) <= tol
            and abs(rect.top - screen.top) <= tol
            and abs(rect.right - screen.right) <= tol
            and abs(rect.bottom - screen.bottom) <= tol)


def sample(now: float | None = None) -> WorldState:
    """采一帧。任何一步失败都退回默认值并把 ``valid`` 置 False。"""
    if not _IS_WINDOWS:
        return WorldState(hour=time.localtime(now).tm_hour, valid=False)
    try:
        battery, on_ac = _read_battery()
        return WorldState(
            cpu_pct=_read_cpu(),
            mem_pct=_read_memory(),
            battery_pct=battery,
            on_ac=on_ac,
            user_idle_s=_read_idle_seconds(),
            fullscreen=_read_fullscreen(),
            hour=time.localtime(now).tm_hour,
            valid=True,
        )
    except Exception:                     # noqa: BLE001 —— 采样层不许把桌宠带崩
        return WorldState(hour=time.localtime(now).tm_hour, valid=False)


def reset() -> None:
    """丢上一次的 CPU 基线（测试用；切尺寸重建仿真时也可以调）。"""
    global _CPU_PREV
    _CPU_PREV = None
