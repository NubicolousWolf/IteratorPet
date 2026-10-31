"""首次运行把模板写到桌宠根目录（审计 A8）。

用户拍了板：**不做任何编辑 UI**，只在发布说明里告诉玩家"自己改 JSON"
（``llm.json`` / ``worldbook.json`` / ``characters/*.json``）。
那就必须保证这些文件**第一次启动就存在**、而且带着能跑的内容——否则"可以自己改"
是一句空话。

规则与 ``lines.json`` / ``llm.json`` 完全一致，一条都不放宽：

* 目录不存在 → 建（``characters/``、``chats/``）；
* 文件不存在 / 0 字节 / 只有空白 → 从 ``assets/llm/`` 复制一份；
* **非空文件一律不动**（哪怕它是坏的 JSON）——那是玩家手改的内容。

模板本体在包内 ``assets/llm/``，``packaging/build.py`` 用 ``--add-data`` 带进 exe；
``tests/test_llm_templates.py`` 会拿 ``docs/templates/`` 做交叉校验，防止两边漂移。
"""

from __future__ import annotations

import shutil
from pathlib import Path

TEMPLATES = (
    ("llm.json", "llm.json"),
    ("worldbook.json", "worldbook.json"),
    ("characters/pebbles.json", "characters/pebbles.json"),
    ("characters/past_moon.json", "characters/past_moon.json"),
    # 2026-10-30 用户写完的初版角色卡：这两人现在还**选不到**（外观注册表只有上面两位，
    # `pet_window.set_character` 会拒绝未知 key），先把卡发出去，等"自定义迭代器"那一版接上。
    ("characters/no_significant_harassment.json", "characters/no_significant_harassment.json"),
    ("characters/seven_red_suns.json", "characters/seven_red_suns.json"),
)
DIRECTORIES = ("characters", "chats")


def assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "llm"


def _folder(folder: Path | str | None) -> Path:
    if folder is not None:
        return Path(folder)
    from ..config import data_dir

    return data_dir()


def _needs_write(path: Path) -> bool:
    """不存在 / 0 字节 / 只有空白 → 该写。非空一律不动（玩家手改的内容）。"""
    try:
        if not path.is_file():
            return True
        return not path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return True


def ensure_template_files(folder: Path | str | None = None) -> list[str]:
    """把缺的模板补齐，返回**这次真的写了**的相对路径列表（空 = 什么都不缺）。"""
    target = _folder(folder)
    source = assets_dir()
    written: list[str] = []
    for name in DIRECTORIES:
        try:
            (target / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    for relative, asset in TEMPLATES:
        destination = target / relative
        if not _needs_write(destination):
            continue
        origin = source / asset
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
            written.append(relative)
        except OSError:
            continue                      # 只读介质/缺资源：不影响说话，静默跳过
    return written


def missing_templates(folder: Path | str | None = None) -> list[str]:
    target = _folder(folder)
    return [relative for relative, _asset in TEMPLATES if _needs_write(target / relative)]
