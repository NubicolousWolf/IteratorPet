"""单实例保护：双击第二次不要变成"两只桌宠 + 两个托盘图标"。

用命名互斥体（`CreateMutexW`）：进程活着就占着名字，进程一死内核自动释放，
不需要清理锁文件，也不会像 pid 文件那样出现"上次崩溃留下的假锁"。
"""

from __future__ import annotations

import ctypes

ERROR_ALREADY_EXISTS = 183

_handle = None  # 保活：句柄一关，互斥体就释放了


def acquire(name: str = "IteratorPet.SingleInstance") -> bool:
    """拿到单实例锁返回 True；已有实例在跑返回 False。"""
    global _handle
    if _handle is not None:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True  # 建不出来就别拦着用户
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _handle = handle
        return True
    except Exception:
        return True


def release() -> None:
    global _handle
    if _handle is not None:
        try:
            ctypes.windll.kernel32.CloseHandle(_handle)
        except Exception:
            pass
        _handle = None
