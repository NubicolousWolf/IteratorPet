"""PyInstaller 的入口脚本（onedir 打包用）。

`启动.exe` 的行为全部在 `iterator_pet.launcher` 里：找游戏目录 → 初始化素材 →
以无开发者面板的形式启动。这里只负责把异常留住 —— noconsole 打包没有 stderr，
不留痕的话只能看到"双击没反应"。

**不弹任何窗**（用户明确要求）：崩溃只写日志 + stderr。
日志写到**当前** `%LOCALAPPDATA%`（测试会用临时目录覆盖它），不硬编码 home。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "IteratorPet"


def _log(text: str) -> Path | None:
    try:
        base = _log_dir()
        base.mkdir(parents=True, exist_ok=True)
        target = base / "launcher-crash.log"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n\n")
        return target
    except OSError:
        return None


if __name__ == "__main__":
    try:
        from iterator_pet.launcher import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        detail = traceback.format_exc()
        where = _log("启动失败:\n" + detail)
        try:
            print(detail, file=sys.stderr)
        except Exception:
            pass
        if where is not None:
            raise SystemExit(f"启动失败，日志：{where}")
        raise
