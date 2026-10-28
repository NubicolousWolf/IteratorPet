"""Windows-specific window tweaks, isolated so other platforms stay importable."""

from __future__ import annotations

import ctypes
import sys

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def _hwnd(window) -> int | None:
    try:
        return int(window.winId())
    except Exception:
        return None


def _get_long(hwnd: int, index: int) -> int:
    user32 = ctypes.windll.user32
    getter = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    return int(getter(hwnd, index))


def _set_long(hwnd: int, index: int, value: int) -> None:
    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    setter(hwnd, index, value)


def set_click_through(window, enabled: bool) -> bool:
    """Toggle real OS-level click-through. Returns True when applied."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        style = _get_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        _set_long(hwnd, GWL_EXSTYLE, style)
        return True
    except Exception:
        return False


def prevent_activation(window) -> bool:
    """Keep the pet from stealing focus when it appears."""
    if sys.platform != "win32":
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        style = _get_long(hwnd, GWL_EXSTYLE)
        _set_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED)
        return True
    except Exception:
        return False


def keep_on_top(window) -> bool:
    """把窗口重新顶到 topmost 层的最前面（不抢焦点、不改尺寸）。

    为什么需要：WS_EX_TOPMOST 只把窗口放进"置顶带"，带内谁在前取决于**激活顺序** ——
    别的置顶窗口（聊天/IDE/浏览器置顶）被点一下，就会跑到我们上面，人偶就被挡住。
    定时重提一次是这个需求的常规做法；这里用 SWP_NOACTIVATE 保证不会把焦点从用户手里抢走。
    """
    if sys.platform != "win32":
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        HWND_TOPMOST = -1
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        return True
    except Exception:
        return False
