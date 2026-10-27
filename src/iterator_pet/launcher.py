"""发布版入口：自动找游戏 → 初始化素材缓存 → 以**无开发者面板**形式启动桌宠。

打包成 `启动.exe` 后双击即可。第一次运行会：
1. 读 `%LOCALAPPDATA%\\IteratorPet\\config.json` 里记的 `install`；
2. 没有就自动探测 Steam 库（`install.detect_install`）；
3. 还找不到就弹一个文件夹选择框，让玩家指到自己的 Rain World 根目录；
4. 把路径写回配置，并从游戏里切出精灵/调色板（`assets_extractor.extract`）；
5. 启动桌宠本体（不带 `--dev-panel`）。

之后每次运行：验证配置里的目录还在 → 缓存缺了才补 → 直接启动。
`--setup` 可以强制重走一遍探测（换机器、重装游戏、缓存坏了）。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from .assets_extractor import ExtractReport, ensure_sprites, extract
from .config import Config, load, save
from .install import describe_missing, detect_install, looks_like_install


def _say(message: str) -> None:
    """noconsole 打包下 print 看不见；同时写日志文件，排障用。"""
    try:
        from .config import config_dir

        log = config_dir() / "launcher.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


def _ask_install_dialog() -> Path | None:
    """图形化的兜底：让玩家自己指到 Rain World 文件夹。"""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    box = QMessageBox()
    box.setWindowTitle("首次运行")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(
        "没有自动找到正版 Rain World。\n\n"
        "请点确定后选择游戏根目录（里面有 RainWorld.exe 和 RainWorld_Data 文件夹）。"
    )
    box.exec()
    chosen = QFileDialog.getExistingDirectory(None, "选择 Rain World 文件夹")
    if not chosen:
        return None
    path = Path(chosen)
    if looks_like_install(path):
        return path
    QMessageBox.warning(None, "目录不对", describe_missing(path))
    return None


def _resolve_install(config: Config, *, force_setup: bool = False) -> Path | None:
    """返回可用的游戏目录；必要时写回配置。"""
    if not force_setup and looks_like_install(config.install):
        return Path(config.install)

    found = detect_install()
    _say(f"[setup] 自动探测: {found}")
    if found is None:
        found = _ask_install_dialog()
        _say(f"[setup] 手动选择: {found}")
    if found is None:
        return None

    config.install = str(found)
    save(config)
    return found


def ensure_ready(config: Config, *, force_setup: bool = False) -> ExtractReport | None:
    """保证 install 与精灵缓存都就绪。返回 None 表示用户放弃。"""
    install = _resolve_install(config, force_setup=force_setup)
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
    config = load()
    try:
        if ensure_ready(config, force_setup=force_setup) is None:
            return 1
    except Exception:  # 提取失败不该拦住桌宠：会用程序内生成的图元兜底
        _say("[setup] 初始化失败:\n" + traceback.format_exc())

    from .main import main as pet_main

    return pet_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
