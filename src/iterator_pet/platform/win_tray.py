"""托盘图标：**直接调 Win32 `Shell_NotifyIconW`**，不用 `QSystemTrayIcon`。

为什么不用 Qt 的：实测 `QSystemTrayIcon` 在**从源码跑**时正常，但在**PyInstaller 打包后**
（windowed / console、裁剪 / 未裁剪都试过）根本不会出现在任务栏溢出区——连一个只有
`QApplication + QSystemTrayIcon` 的最小 exe 都注册不上（`_work/tray_probe.py` 复现）。
所以这里绕开 Qt，自己拿窗口句柄 + `Shell_NotifyIconW` 注册图标；菜单仍然用 Qt 的 `QMenu`
（在回调里 `exec`），这样外观和其它菜单一致。

要点：
* 需要一个**普通窗口**的 HWND（message-only 窗口不行）→ 用一个从不显示的 `QWidget` 拿句柄；
* 消息走 Qt 的 `QAbstractNativeEventFilter`（Qt 的事件循环会替我们 DispatchMessage）；
* explorer 重启会广播 `TaskbarCreated`，收到就重新 NIM_ADD 一次，否则图标会永久消失。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu, QWidget

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
SM_CXSMICON = 49


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


def _asset_path(name: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "iterator_pet" / "assets" / name
    return Path(__file__).resolve().parent.parent / "assets" / name


class WinTray(QObject):
    """一个 Win32 托盘图标 + Qt 菜单。``menu_factory()`` 返回要弹出的 ``QMenu``。"""

    def __init__(self, tooltip: str, menu_factory, parent: QObject | None = None,
                 fallback_hwnd: int | None = None) -> None:
        super().__init__(parent)
        self._tooltip = tooltip
        self._menu_factory = menu_factory
        self._icon = None
        self._added = False
        self._fallback_hwnd = fallback_hwnd
        self._host = QWidget()                 # 只为拿一个普通窗口的 HWND，永不 show()
        self._host.setWindowTitle("IteratorPetTrayHost")
        self._hwnd = int(self._host.winId())
        self._filter = _TrayFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    # -- icon ---------------------------------------------------------------
    def _load_icon(self):
        user32 = ctypes.windll.user32
        path = _asset_path("icon.ico")
        debug = bool(__import__("os").environ.get("ITERATORPET_DEBUG"))
        if path.is_file():
            size = user32.GetSystemMetrics(SM_CXSMICON) or 16
            handle = user32.LoadImageW(
                None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            if debug:
                print(f"[dbg] icon path={path} is_file=True size={size} "
                      f"LoadImageW={handle!r} err={ctypes.GetLastError()}", flush=True)
            if handle:
                return handle
        elif debug:
            print(f"[dbg] icon path={path} is_file=False", flush=True)
        fallback = user32.LoadIconW(None, ctypes.cast(32512, ctypes.c_void_p))
        if debug:
            print(f"[dbg] LoadIconW fallback={fallback!r} err={ctypes.GetLastError()}", flush=True)
        return fallback

    def _data(self, hwnd: int | None = None) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = wt.HWND(self._hwnd if hwnd is None else hwnd)
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = self._icon
        data.szTip = self._tooltip
        return data

    def show(self) -> bool:
        if self._added:
            return True
        debug = bool(__import__("os").environ.get("ITERATORPET_DEBUG"))
        self._icon = self._load_icon()
        # 用 WinDLL(use_last_error=True) 才能读到**这一次调用**的错误码
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]
        shell.Shell_NotifyIconW.restype = wt.BOOL

        candidates = [self._hwnd]
        if self._fallback_hwnd and self._fallback_hwnd != self._hwnd:
            candidates.append(self._fallback_hwnd)

        for hwnd in candidates:
            data = self._data(hwnd)
            ok = bool(shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
            if debug:
                print(
                    f"[dbg] Shell_NotifyIcon hwnd={hwnd} hicon={self._icon!r} ok={ok} "
                    f"err={ctypes.get_last_error()} struct={ctypes.sizeof(NOTIFYICONDATAW)}",
                    flush=True,
                )
            if ok:
                self._hwnd = hwnd
                self._added = True
                return True
        return False

    def hide(self) -> None:
        if self._added:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
            self._added = False

    # -- events -------------------------------------------------------------
    def _on_message(self, lparam: int) -> None:
        if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
            menu = self._menu_factory()
            if menu is not None:
                from PySide6.QtGui import QCursor

                menu.exec(QCursor.pos())
        elif lparam == WM_LBUTTONUP:
            pass    # 左键留给以后的"打开对话"，现在什么都不做

    def _on_taskbar_created(self) -> None:
        # explorer.exe 重启后图标会丢，必须重新注册。
        self._added = False
        self.show()


class _TrayFilter(QAbstractNativeEventFilter):
    """把托盘窗口的消息转给 WinTray（Qt 的事件循环会 DispatchMessage 到我们）。"""

    TASKBAR_CREATED = None  # 运行时注册

    def __init__(self, tray: WinTray) -> None:
        super().__init__()
        self.tray = tray
        user32 = ctypes.windll.user32
        # "TaskbarCreated" 是广播消息，先注册拿到它的 id
        type(self).TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        try:
            msg = wt.MSG.from_address(int(message))
        except Exception:
            return False, 0
        if int(msg.hWnd) != self.tray._hwnd:
            return False, 0
        if msg.message == WM_TRAYICON:
            self.tray._on_message(int(msg.lParam))
            return True, 0
        created = type(self).TASKBAR_CREATED
        if created and msg.message == created:
            self.tray._on_taskbar_created()
            return True, 0
        return False, 0
