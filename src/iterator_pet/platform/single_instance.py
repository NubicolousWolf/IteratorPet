"""单实例保护：重复启动时**杀掉旧进程、起新的**（不弹提示）。

旧做法是"检测到已有实例就提示后退出"——用户明确不要这个：双击就应该是"重开一只"。
做法：
1. 命名互斥体（``CreateMutexW``）：进程活着就占着名字，进程一死内核自动释放，
   不会有 pid 文件那种"上次崩溃留下的假锁"；
2. 拿不到锁 → 用 ``taskkill /F /IM 启动.exe /FI "PID ne <自己>"`` 杀掉别人（只杀同名的
   发布版 exe，不碰 python 之类），等它退出，然后重试拿锁。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "IteratorPet.SingleInstance"

_handle = None  # 保活：句柄一关，互斥体就释放了


def acquire(name: str = MUTEX_NAME) -> bool:
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


def kill_other_instances(image_name: str | None = None, wait: float = 3.0) -> int:
    """杀掉所有**同名发布版进程**（不含自己），返回杀了几次 taskkill 成功。

    ``image_name`` 默认取当前 exe 的文件名（打包后是 ``启动.exe``；从源码跑是 ``python.exe``，
    那种情况一律不动手，避免把用户别的 python 任务干掉）。
    """
    if sys.platform != "win32":
        return 0
    exe = os.path.basename(sys.executable)
    if image_name is None:
        if not getattr(sys, "frozen", False):
            return 0            # 源码运行：别乱杀 python
        image_name = exe
    if not image_name.lower().endswith(".exe"):
        return 0

    args = ["taskkill", "/F", "/IM", image_name, "/FI", f"PID ne {os.getpid()}"]
    try:
        result = subprocess.run(args, capture_output=True, timeout=10)
    except Exception:
        return 0
    killed = 1 if result.returncode == 0 else 0
    if killed:
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.15)
            if acquire():
                break
    return killed


def acquire_or_replace() -> bool:
    """拿到锁；拿不到就把旧实例杀掉再拿。全部失败时返回 False（照常启动，宁可两只）。"""
    if acquire():
        return True
    if kill_other_instances() == 0:
        return False
    for _ in range(20):
        if acquire():
            return True
        time.sleep(0.1)
    return False
