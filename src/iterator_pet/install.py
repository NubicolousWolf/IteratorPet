"""定位玩家本机的 Rain World 安装（只读，绝不改动游戏目录）。

发布版要能"第一次运行就自己找到正版游戏"，所以这里做三件事：
1. 从注册表拿 Steam 根目录；
2. 顺着 ``steamapps/libraryfolders.vdf`` 把每个库根都读出来（游戏常装在第二个盘）；
3. 逐个库试 ``steamapps/common/Rain World``，再用**图标/调色板必需的文件**验证。

验证只看我们真正要用的两样东西：``RainWorld_Data/resources.assets``（图集）
与 ``RainWorld_Data/Managed/Assembly-CSharp.dll``（版本校验用）——这样即使用户
把游戏装在非 Steam 目录，只要指到那个文件夹也能通过。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

GAME_DIR_NAME = "Rain World"
REQUIRED_FILES = (
    Path("RainWorld_Data") / "resources.assets",
    Path("RainWorld_Data") / "Managed" / "Assembly-CSharp.dll",
)


def looks_like_install(path: str | Path | None) -> bool:
    """``path`` 是不是一个可用的 Rain World 根目录。"""
    if not path:
        return False
    root = Path(path)
    try:
        return root.is_dir() and all((root / rel).is_file() for rel in REQUIRED_FILES)
    except OSError:
        return False


def _drive_roots() -> list[Path]:
    """Windows 上枚举存在的盘符（库可能在任何盘）。"""
    if sys.platform != "win32":
        home = Path.home()
        return [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / "Library" / "Application Support" / "Steam",
        ]
    return [Path(f"{letter}:\\") for letter in "CDEFGHIJKLMNOP" if Path(f"{letter}:\\").exists()]


def steam_root() -> Path | None:
    """注册表里的 Steam 根目录。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                path = Path(winreg.QueryValueEx(handle, value)[0])
        except OSError:
            continue
        if path.is_dir():
            return path
    return None


def steam_libraries() -> list[Path]:
    """所有 Steam 库根目录（去重、按发现顺序）。"""
    candidates: list[Path] = []
    root = steam_root()
    if root is not None:
        candidates.append(root)
    for drive in _drive_roots():
        candidates += [
            drive / "Steam",
            drive / "SteamLibrary",
            drive / "Program Files (x86)" / "Steam",
            drive / "Program Files" / "Steam",
        ]

    libraries: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        key = candidate.resolve() if hasattr(candidate, "resolve") else candidate
        if key in seen:
            continue
        seen.add(key)
        libraries.append(candidate)

        vdf = candidate / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # VDF 里 "path" "F:\\SteamLibrary"（反斜杠被转义成 \\）
        for match in re.finditer(r'"path"\s*"([^"]+)"', text):
            library = Path(match.group(1).replace("\\\\", "\\"))
            try:
                if not library.is_dir():
                    continue
                key = library.resolve()
            except OSError:
                continue
            if key not in seen:
                seen.add(key)
                libraries.append(library)
    return libraries


def detect_install() -> Path | None:
    """自动探测：Steam 各库里找 ``steamapps/common/Rain World``。"""
    for library in steam_libraries():
        game = library / "steamapps" / "common" / GAME_DIR_NAME
        if looks_like_install(game):
            return game
    return None


def describe_missing(path: str | Path | None) -> str:
    """给用户看的一句话：选中的目录为什么不行。"""
    if not path:
        return "没有选择文件夹。"
    root = Path(path)
    if not root.is_dir():
        return f"不是一个文件夹：{root}"
    missing = [str(rel) for rel in REQUIRED_FILES if not (root / rel).is_file()]
    if missing:
        return "缺少这些文件：\n  " + "\n  ".join(missing)
    return "目录看起来是对的，但读取失败（权限？）。"
