"""档 2：杀鼠标那一瞬间把系统光标换成"忙"的样子（0.3 s），然后恢复。

**为什么不是档 3（ClipCursor + SetCursorPos）**：本机实测（`_work/clip_probe.py`）
设了 `ClipCursor(1×1)` 的进程硬崩（`os._exit`）之后，**系统不会自动释放**，鼠标会被钉死；
`SetCursorPos` 还会劫持用户正在拖的窗口/文件/滑块。用户 2026-10-29 拍板：只做档 2。

安全措施（每一条都对应审计里的一条风险）：
* `SetSystemCursor` 会**取走句柄** → 每次先 `CopyIcon`；
* `SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)` 的 `fWinIni` **必须 0**——
  带 `SPIF_UPDATEINIFILE(1)` 会把"忙"写进用户注册表的光标方案，永久生效；
* 看门狗放在**独立线程**：GUI 线程会被 `QMenu.exec()` 卡住，最需要它的时刻恰好不响；
* `atexit` + 平台层四处（关闭/切尺寸/开穿透/暂停）都调 `restore()`；
* **启动时无条件自愈一次**：硬崩（本项目历史上真出过 0xC0000409）时 `atexit` 不执行，
  这条是唯一兜底。
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

DURATION_S = 0.3          # 用户口径：0.3 s（不是参考项目的 5 s）
WATCHDOG_S = 10.0         # 万一 Timer 没跑，最多 10 s 后强制恢复

_lock = threading.Lock()
_deadline = 0.0
_watchdog: threading.Thread | None = None
_applied = False


def enabled() -> bool:
    """托盘开关 / 环境变量。默认开（用户要评估效果），`ITERATORPET_NO_CURSORFX=1` 可关。"""
    if os.environ.get("ITERATORPET_NO_CURSORFX"):
        return False
    return _enabled


_enabled = True


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)
    if not _enabled:
        restore()


def _user32():
    import ctypes
    return ctypes.windll.user32


def _apply_busy() -> bool:
    """把系统箭头/文本/链接光标换成"忙"。失败返回 False（静默退化成档 1）。"""
    import ctypes
    from ctypes import wintypes
    u = _user32()
    u.LoadCursorW.restype = ctypes.c_void_p
    u.LoadCursorW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    u.CopyIcon.restype = ctypes.c_void_p
    u.CopyIcon.argtypes = (ctypes.c_void_p,)
    u.SetSystemCursor.restype = wintypes.BOOL
    u.SetSystemCursor.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    src = u.LoadCursorW(None, IDC_APPSTARTING)
    if not src:
        return False
    ok = False
    for ocr in (OCR_NORMAL, OCR_IBEAM, OCR_HAND):
        handle = u.CopyIcon(src)        # Set 会接管句柄，所以每次都复制一份
        if handle and u.SetSystemCursor(handle, ocr):
            ok = True
    return ok


def restore() -> None:
    """从注册表重新加载系统光标（fWinIni 必须 0，绝不许写注册表）。"""
    global _applied
    if not _IS_WIN:
        return
    try:
        _user32().SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
    except Exception:
        pass
    _applied = False


def _watchdog_loop() -> None:
    while time.monotonic() < _deadline:
        time.sleep(0.02)
    restore()


def flash(duration_s: float = DURATION_S) -> bool:
    """杀鼠标 flash 的那一下调用。0.3 s 后自动恢复，带独立线程看门狗。"""
    global _deadline, _watchdog, _applied
    if not _IS_WIN or not enabled():
        return False
    if not _apply_busy():
        return False
    with _lock:
        _applied = True
        _deadline = time.monotonic() + duration_s
        if _watchdog is None or not _watchdog.is_alive():
            _watchdog = threading.Thread(target=_watchdog_loop, daemon=True,
                                         name="cursorfx-watchdog")
            _watchdog.start()
        # 看门狗兜底：超过 WATCHDOG_S 一定恢复（Timer 线程挂了也不会留残）
        timer = threading.Timer(min(WATCHDOG_S, duration_s + 0.05), restore)
        timer.daemon = True
        timer.start()
    return True


def self_heal() -> None:
    """启动时无条件调一次：覆盖"上一次硬崩留下忙光标"（atexit 在硬崩下不执行）。"""
    if _IS_WIN:
        restore()


atexit.register(restore)
