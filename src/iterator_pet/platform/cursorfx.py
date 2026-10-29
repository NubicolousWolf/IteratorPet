"""杀鼠标那一刻"劫持光标"：让它掉到屏幕底部、被按住 1.5 s，再放开。

参考 `lingxiaojun/slugcatpet` 的 `CursorHijack`（fall → lock → release），但把它没做的
安全措施补齐（用户 2026-10-29 要求这个效果；档 2 的"换忙光标"在桌宠是非前台窗口时看不出来）：

1. **崩溃残留**：本机实测（`_work/clip_probe.py`）设了 `ClipCursor(1×1)` 的进程硬崩后，
   系统**不会**释放裁剪，鼠标会被钉死 → 所以**启动时无条件 `ClipCursor(None)` 自愈**，
   这是唯一兜底（`atexit` 在硬崩下不执行）。
2. **挂起**：看门狗放**独立线程**（GUI 线程被 `QMenu.exec()` 卡住时它照样能放），硬上限 2 s。
3. **逃生键**：看门狗线程同时轮询键盘，按 **Esc** 立刻放开。
4. **不乱抢**：用户按着左键（正在拖窗口/文件）、或前台是全屏游戏时**不启动**。
5. 时长 1.5 s（不是参考项目的 5 s），`lock` 结束后立刻 `ClipCursor(None)`。
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
import time

_IS_WIN = sys.platform == "win32"
SPI_SETCURSORS = 0x0057
OCR_NORMAL, OCR_IBEAM, OCR_HAND = 32512, 32513, 32649
IDC_APPSTARTING = 32650
VK_ESCAPE = 0x1B
VK_LBUTTON = 0x01

GRAVITY = 1.6        # px/tick²（照参考项目 T_FALL_GRAV）
LOCK_TICKS = 60      # 1.5 s @40 TPS（参考项目是 200 = 5 s，太长）
HARD_LIMIT_S = 2.0   # 看门狗硬上限

_lock = threading.Lock()
_deadline = 0.0
_watchdog: threading.Thread | None = None
_enabled = True
_hijack: "Hijack | None" = None


def enabled() -> bool:
    return _enabled and not os.environ.get("ITERATORPET_NO_CURSORFX")


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)
    if not _enabled:
        release()


# ------------------------------------------------------------------ 纯逻辑
class Motion:
    """光标"掉落 → 锁住"的运动学（不碰 Windows，可单测）。"""

    __slots__ = ("x", "y", "vy", "bottom", "phase", "lock_t", "lock_ticks")

    def __init__(self, x: float, y: float, bottom: float, lock_ticks: int = LOCK_TICKS):
        self.x = float(x)
        self.y = float(y)
        self.vy = 0.0
        self.bottom = float(bottom)
        self.phase = "fall"
        self.lock_t = 0
        self.lock_ticks = int(lock_ticks)

    def tick(self) -> bool:
        """推进一步；返回 False 表示结束（该放开了）。"""
        if self.phase == "fall":
            self.vy += GRAVITY
            self.y += self.vy
            if self.y >= self.bottom:
                self.y = self.bottom
                self.vy = 0.0
                self.phase = "lock"
                self.lock_t = 0
            return True
        self.lock_t += 1
        return self.lock_t < self.lock_ticks


# ------------------------------------------------------------------ Win32
def _user32():
    import ctypes
    return ctypes.windll.user32


def _clip(x: float, y: float) -> bool:
    import ctypes
    u = _user32()

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    return bool(u.ClipCursor(ctypes.byref(RECT(int(x), int(y), int(x) + 1, int(y) + 1))))


def _unclip() -> None:
    try:
        _user32().ClipCursor(None)
    except Exception:
        pass


def _setpos(x: float, y: float) -> None:
    try:
        _user32().SetCursorPos(int(x), int(y))
    except Exception:
        pass


def _apply_busy() -> None:
    import ctypes
    from ctypes import wintypes
    try:
        u = _user32()
        u.LoadCursorW.restype = ctypes.c_void_p
        u.LoadCursorW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        u.CopyIcon.restype = ctypes.c_void_p
        u.CopyIcon.argtypes = (ctypes.c_void_p,)
        u.SetSystemCursor.restype = wintypes.BOOL
        u.SetSystemCursor.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        src = u.LoadCursorW(None, IDC_APPSTARTING)
        if not src:
            return
        for ocr in (OCR_NORMAL, OCR_IBEAM, OCR_HAND):
            handle = u.CopyIcon(src)          # Set 会接管句柄 → 每次复制
            if handle:
                u.SetSystemCursor(handle, ocr)
    except Exception:
        pass


def restore_cursors() -> None:
    """从注册表重载系统光标。fWinIni 必须是 0（带 SPIF_UPDATEINIFILE 会写用户注册表）。"""
    if not _IS_WIN:
        return
    try:
        _user32().SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
    except Exception:
        pass


def _keys_down() -> bool:
    """用户是否按着 Esc / 左键（逃生 + 不乱抢都用它）。"""
    try:
        u = _user32()
        return bool(u.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
    except Exception:
        return False


def _left_button_down() -> bool:
    try:
        return bool(_user32().GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False


def _foreground_is_fullscreen() -> bool:
    """前台窗口铺满某块屏幕 → 大概率是全屏游戏，别去抢它的鼠标。"""
    import ctypes
    from ctypes import wintypes
    try:
        u = _user32()
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        rect = wintypes.RECT()
        if not u.GetClientRect(hwnd, ctypes.byref(rect)):
            return False
        w, h = rect.right - rect.left, rect.bottom - rect.top
        return w >= 1280 and h >= 720 and w * h >= 0.95 * (2560 * 1440 * 0.25)
    except Exception:
        return False


# ------------------------------------------------------------------ 生命周期
class Hijack:
    def __init__(self, x: float, y: float, bottom: float) -> None:
        self.motion = Motion(x, y, bottom)
        self.active = True

    def tick(self) -> bool:
        if not self.active:
            return False
        alive = self.motion.tick()
        _clip(self.motion.x, self.motion.y)
        _setpos(self.motion.x, self.motion.y)
        if not alive:
            self.release()
        return self.active

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        _unclip()
        restore_cursors()


def _die_with_dignity() -> None:
    """看门狗线程：到点 / 按 Esc / 硬上限，一律放开。"""
    global _hijack
    while time.monotonic() < _deadline:
        if _keys_down():
            break
        time.sleep(0.02)
    release()


def start(x: float, y: float, bottom: float) -> bool:
    """开始劫持。返回 False 表示这次不劫持（被开关/场景挡掉），调用方退回普通表现。"""
    global _hijack, _deadline, _watchdog
    if not _IS_WIN or not enabled():
        return False
    # 用户 2026-10-29 拍板：**删掉**"按着左键/前台全屏就不劫持"这两条。
    # 理由：杀鼠标本来就是拖拽/戳出来的，触发那一刻手往往还在左键上 —— 加了这两条
    # 就变成"几乎永远不触发"（实测就是这样）。玩家高频玩桌宠的几秒里做大操作的
    # 概率极低，剩下的保护（2 s 硬上限 / Esc 逃生 / 启动自愈）够用。
    # 两个函数保留（GetAsyncKeyState 的封装没删），以后要恢复这些护栏只需加回三行。
    with _lock:
        if _hijack is not None and _hijack.active:
            _hijack.release()
        _hijack = Hijack(x, y, bottom)
        _apply_busy()
        _deadline = time.monotonic() + HARD_LIMIT_S
        if _watchdog is None or not _watchdog.is_alive():
            _watchdog = threading.Thread(target=_die_with_dignity, daemon=True,
                                         name="cursorfx-watchdog")
            _watchdog.start()
    return True


def tick() -> None:
    """每 tick 推一次（在仿真线程里，40 Hz）。"""
    global _hijack
    with _lock:
        if _hijack is None:
            return
        _hijack.tick()
        if not _hijack.active:
            _hijack = None


def release() -> None:
    global _hijack
    with _lock:
        if _hijack is not None:
            _hijack.release()
            _hijack = None
    _unclip()
    restore_cursors()


def self_heal() -> None:
    """启动时无条件做一次：覆盖"上一次硬崩留下的 ClipCursor/忙光标"。"""
    if _IS_WIN:
        _unclip()
        restore_cursors()


atexit.register(release)
