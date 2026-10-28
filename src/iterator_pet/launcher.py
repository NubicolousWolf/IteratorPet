"""发布版入口：自动找游戏 → 初始化素材缓存 → 以**无开发者面板**形式启动桌宠。

打包成 `启动.exe` 后双击即可。第一次运行会：
1. 读 `%LOCALAPPDATA%\\IteratorPet\\config.json` 里记的 `install`；
2. 没有就自动探测 Steam 库（`install.detect_install`）；
3. 还找不到就弹框让玩家指到 Rain World 根目录——**也可以选“先用内置图元继续”**，
   桌宠不会因为找不到游戏就彻底起不来；
4. 把路径写回配置，并从游戏里切出精灵/调色板（`assets_extractor.extract`）；
5. 启动桌宠本体（不带 `--dev-panel`）。

之后每次运行：验证配置里的目录还在 → 缓存缺了才补 → 直接启动。
`--setup` 可以强制重走一遍探测（换机器、重装游戏、缓存坏了）。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from .assets_extractor import ExtractReport, ensure_sprites
from .config import Config, config_dir, load, save
from .install import describe_missing, detect_install, looks_like_install


def _say(message: str) -> None:
    """noconsole 打包下 print 看不见；同时写日志文件，排障用。"""
    try:
        log = config_dir() / "launcher.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


def _interactive() -> bool:
    """能不能弹窗。``ITERATORPET_NONINTERACTIVE=1`` 时一律不弹——自动化测试用这个跑，
    免得桌面上冒出一堆等人点的模态框。"""
    import os

    return not os.environ.get("ITERATORPET_NONINTERACTIVE")


def _already_running() -> None:
    """第二个实例：告诉用户一声就退，别把两只宠物放桌面上。"""
    if not _interactive():
        return
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox()
    box.setWindowTitle("五块卵石")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(
        "它已经在你的桌面上了。\n\n"
        "退出：右键托盘图标（Windows 11 可能把它折进任务栏的 ^ 里）→ 退出；"
        "或者按 Ctrl+Alt+Q。"
    )
    box.exec()


def _ask_install_dialog() -> tuple[Path | None, bool]:
    """返回 ``(游戏目录, 是否无游戏继续)``。"""
    if not _interactive():
        # 没有窗口可给人点的时候不弹框：直接按"先用内置图元继续"处理。
        _say("[setup] 非交互模式：跳过选择框，用内置图元继续")
        return None, True

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    box = QMessageBox()
    box.setWindowTitle("首次运行")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(
        "没有自动找到正版 Rain World。\n\n"
        "点「选择文件夹」指到游戏根目录（里面有 RainWorld.exe 和 RainWorld_Data 文件夹）；"
        "也可以先用内置图元跑起来，之后再放游戏目录（`启动.exe --setup`）。"
    )
    pick = box.addButton("选择文件夹", QMessageBox.ButtonRole.AcceptRole)
    skip = box.addButton("先用内置图元运行", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("退出", QMessageBox.ButtonRole.RejectRole)
    box.exec()

    if box.clickedButton() is skip:
        return None, True
    if box.clickedButton() is not pick:
        return None, False

    while True:
        chosen = QFileDialog.getExistingDirectory(None, "选择 Rain World 文件夹")
        if not chosen:
            return None, False
        path = Path(chosen)
        if looks_like_install(path):
            return path, False
        QMessageBox.warning(None, "目录不对", describe_missing(path) + "\n\n请再选一次。")


def _resolve_install(config: Config, *, force_setup: bool = False) -> tuple[Path | None, bool]:
    """返回 ``(游戏目录, 是否无游戏继续)``；必要时写回配置。"""
    if not force_setup and looks_like_install(config.install):
        return Path(config.install), False

    found = detect_install()
    _say(f"[setup] 自动探测: {found}")
    if found is None:
        found, continue_without = _ask_install_dialog()
        _say(f"[setup] 手动选择: {found}（无游戏继续={continue_without}）")
        if found is None:
            return None, continue_without
    config.install = str(found)
    save(config)
    return found, False


def _debug_dump() -> None:
    """``ITERATORPET_DEBUG=1`` 时把环境/路径/依赖打到 stdout（noconsole 下需重定向才看得到，
    也能从 bash 里 `> out.txt 2>&1` 抓到）。发布版留着不碍事，排障时省一次重建。"""
    import os

    print("[debug] frozen:", getattr(sys, "frozen", False))
    print("[debug] LOCALAPPDATA:", os.environ.get("LOCALAPPDATA"))
    print("[debug] config_dir:", config_dir())
    print("[debug] cwd:", os.getcwd())
    try:
        import UnityPy

        print("[debug] UnityPy:", getattr(UnityPy, "__version__", "?"))
    except Exception as exc:  # noqa: BLE001
        print("[debug] UnityPy FAILED:", repr(exc))
    try:
        from .assets_extractor import cache_root

        print("[debug] cache_root:", cache_root(), "exists:", cache_root().is_dir())
    except Exception as exc:  # noqa: BLE001
        print("[debug] cache_root FAILED:", repr(exc))
    try:
        from .install import detect_install

        found = detect_install()
        print("[debug] detect_install:", found, "valid:", looks_like_install(found))
    except Exception as exc:  # noqa: BLE001
        print("[debug] detect_install FAILED:", repr(exc))


def ensure_ready(config: Config, *, force_setup: bool = False) -> ExtractReport | None:
    """保证 install 与精灵缓存就绪。返回 None 表示"以无游戏状态继续"。"""
    install, _continue_without = _resolve_install(config, force_setup=force_setup)
    if install is None:
        return None
    _say(f"[setup] 使用游戏目录: {install}")
    report = ensure_sprites(install=install)
    _say(f"[setup] 素材来源: {report.source} -> {report.cache_dir}")
    for note in report.notes:
        _say(f"[setup] note: {note}")
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    force_setup = "--setup" in args
    args = [a for a in args if a != "--setup"]

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    from .platform.single_instance import acquire

    if not acquire():
        _say("[setup] 已有实例在运行，本次退出")
        _already_running()
        return 0

    if __import__("os").environ.get("ITERATORPET_DEBUG"):
        _debug_dump()

    config = load()
    try:
        if ensure_ready(config, force_setup=force_setup) is None:
            _say("[setup] 没有游戏目录，用内置图元继续")
    except Exception:  # 提取失败不该拦住桌宠：会用程序内生成的图元兜底
        _say("[setup] 初始化失败:\n" + traceback.format_exc())

    from .main import main as pet_main

    return pet_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
