"""PyInstaller 的入口脚本（onedir 打包用）。

`启动.exe` 的行为全部在 `iterator_pet.launcher` 里：找游戏目录 → 初始化素材 →
以无开发者面板的形式启动。这里只负责把异常留住，别让 noconsole 打包把错误吞掉。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _log(text: str) -> None:
    try:
        base = Path.home() / "AppData" / "Local" / "IteratorPet"
        base.mkdir(parents=True, exist_ok=True)
        with (base / "launcher-crash.log").open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        from iterator_pet.launcher import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        _log("启动失败:\n" + traceback.format_exc())
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, "启动失败", "启动失败，详情见 %LOCALAPPDATA%\\IteratorPet\\launcher-crash.log")
        except Exception:
            pass
        raise
