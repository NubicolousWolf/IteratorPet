"""角色卡与世界书的**文件层**（GUI 用）。只碰磁盘，不懂 Qt，也不需要 Qt 才能测。

为什么单独一个模块而不是塞进 ``prompt.py``：``prompt.py`` 负责"把卡片拼成 prompt"，
它读卡是为了组装消息；"把玩家改的卡写回去"是另一件事，牵扯到
**保留未知字段**（玩家的 `extensions` / 未来字段）和**坏文件保护**。
两件事混在一个文件里，以后改 prompt 的人会不小心改坏写盘路径。

三条铁律（和 ``llm/config.py::save`` 完全一致）：

1. **原子写**：临时文件 + ``replace``，读的人永远看不到半个 JSON；
2. **未知键原样保留**：卡里 `extensions.rainworld` 存着外观数据，写卡时绝不能冲掉；
3. **坏文件不覆盖**：非空但解析不了的卡，我们要写就先备份成 ``.bak``（见 ``_backup``），
   绝不静默把玩家的东西吃掉。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import prompt as llm_prompt

CARD_SPEC = "chara_card_v2"
APPEARANCE_KEY = ("extensions", "rainworld", "appearance")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _folder(folder: Path | str | None = None) -> Path:
    if folder is not None:
        return Path(folder)
    from ..config import data_dir

    return data_dir()


def _read_json(path: Path) -> tuple[dict | None, str]:
    """读 JSON → ``(dict, error)``。文件不存在算"空卡"（不是错误）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not text.strip():
        return {}, ""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return None, f"不是合法 JSON（{exc}）"
    if not isinstance(raw, dict):
        return None, "顶层不是对象"
    return raw, ""


def _atomic_write(path: Path, payload: dict) -> str:
    """写成功返回 ``""``，失败返回错误串（调用方直接显示给玩家）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.stem}-", suffix=".json"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(tmp).replace(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def _backup(path: Path) -> None:
    """坏文件先留一份 ``.bak``：玩家的手写内容不该因为"我们想保存"就消失。"""
    try:
        if path.is_file():
            shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
    except OSError:
        pass


# --------------------------------------------------------------------------
# 角色卡
# --------------------------------------------------------------------------
@dataclass
class CardRef:
    """列在工作室下拉框里的一个角色：``key`` 是文件名，``name`` 是卡里的显示名。"""

    key: str
    name: str
    path: Path
    builtin: bool = False
    has_appearance: bool = False


#: 内置两位：外观与兜底都写死在 ``simulation/appearance.py`` / ``prompt.py`` 里，
#: 工作室里允许改卡与改色，但不允许删除、也不允许改 key（``v0.2.0`` 的注册表才管生命周期）。
BUILTIN = (("pebbles", "五块卵石"), ("past_moon", "仰望皓月"))


def card_path(key: str, folder: Path | str | None = None) -> Path:
    return llm_prompt.card_path(key, _folder(folder))


def list_cards(folder: Path | str | None = None) -> list[CardRef]:
    """内置两位在前，其余按文件名排序。

    **即使卡文件不存在也要列出来**：内置两位没有文件时用 ``FALLBACK_CARDS`` 的名称，
    玩家第一次打开工作室就该看到"五块卵石 / 仰望皓月"两项能点，而不是空列表。
    """
    root = _folder(folder)
    seen: dict[str, CardRef] = {}
    for key, label in BUILTIN:
        path = root / "characters" / f"{key}.json"
        raw, _error = _read_json(path)
        name = ""
        if isinstance(raw, dict):
            name = str(raw.get("name") or "").strip()
        seen[key] = CardRef(
            key=key,
            name=name or label,
            path=path,
            builtin=True,
            has_appearance=has_appearance(raw),
        )
    custom: list[CardRef] = []
    directory = root / "characters"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            key = path.stem
            if key in seen:
                continue
            raw, _error = _read_json(path)
            name = ""
            if isinstance(raw, dict):
                name = str(raw.get("name") or "").strip()
            custom.append(
                CardRef(
                    key=key,
                    name=name or key,
                    path=path,
                    builtin=False,
                    has_appearance=has_appearance(raw),
                )
            )
    return [seen[key] for key, _label in BUILTIN] + custom


def read_document(key: str, folder: Path | str | None = None) -> tuple[dict, str]:
    """整张卡的原始字典（写回时以它为底，才能保住未知字段）。"""
    return _read_json(card_path(key, folder))


def read_card(key: str, folder: Path | str | None = None) -> tuple[llm_prompt.Card, str]:
    """→ ``(card, error)``。缺文件时卡片全空、不报错（工作室里从空白开始填）。"""
    raw, error = read_document(key, folder)
    if not isinstance(raw, dict):
        return llm_prompt.Card(), error
    card = llm_prompt.Card.from_dict(raw)
    return card, error


def save_card(key: str, card: llm_prompt.Card, folder: Path | str | None = None) -> str:
    """把 8 个文本字段写回，**其它键（含 ``extensions``）原样保留**。

    返回 ``""`` = 成功；否则是给玩家看的错误串。
    """
    path = card_path(key, folder)
    raw, _error = _read_json(path)
    if raw is None:
        _backup(path)                       # 坏文件：先留 .bak 再覆盖
        raw = {}
    raw.setdefault("spec", CARD_SPEC)
    raw.update(
        {
            "name": card.name,
            "description": card.description,
            "personality": card.personality,
            "scenario": card.scenario,
            "first_mes": card.first_mes,
            "mes_example": card.mes_example,
            "system_prompt": card.system_prompt,
            "post_history_instructions": card.post_history_instructions,
        }
    )
    return _atomic_write(path, raw)


# --------------------------------------------------------------------------
# 外观草稿（存在卡里的 extensions.rainworld.appearance）
# --------------------------------------------------------------------------
def _rainworld(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    extensions = raw.get("extensions")
    if not isinstance(extensions, dict):
        return {}
    block = extensions.get("rainworld")
    return block if isinstance(block, dict) else {}


def has_appearance(raw) -> bool:
    block = _rainworld(raw).get("appearance")
    return isinstance(block, dict) and bool(block)


def read_appearance(key: str, folder: Path | str | None = None) -> dict:
    raw, _error = read_document(key, folder)
    block = _rainworld(raw).get("appearance")
    return dict(block) if isinstance(block, dict) else {}


def save_appearance(
    key: str, appearance: dict | None, folder: Path | str | None = None
) -> str:
    """写外观草稿；``None`` = 删除这个块（"恢复默认"就是删覆盖层）。

    **不碰卡里的其它任何东西**（8 个文本字段、`oracle_id`、未来字段全留着）。
    """
    path = card_path(key, folder)
    raw, _error = _read_json(path)
    if raw is None:
        _backup(path)
        raw = {}
    raw.setdefault("spec", CARD_SPEC)
    extensions = raw.get("extensions")
    if not isinstance(extensions, dict):
        extensions = raw["extensions"] = {}
    block = extensions.get("rainworld")
    if not isinstance(block, dict):
        block = extensions["rainworld"] = {}
    if appearance is None:
        block.pop("appearance", None)
    else:
        block["appearance"] = appearance
    return _atomic_write(path, raw)
