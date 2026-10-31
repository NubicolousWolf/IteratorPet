"""角色注册表：谁存在、叫什么、长什么样、能不能删。

v0.1.9 之前"角色"是两张写死在代码里的常量（``appearance.CHARACTERS``），
从 v0.2.0 起一个角色 = ``characters/<key>.json`` 一张卡 + 卡内
``extensions.rainworld.appearance`` 的外观覆盖层。这个模块是**唯一的角色清单来源**：
托盘的「当前角色」菜单、右键菜单、工坊顶部的下拉框、工坊的「本地对话」页，全都读它，
不许再各写一份 ``(("pebbles", "五块卵石"), ...)``。

为什么放在顶层而不在 ``simulation/`` 下（计划里原本写的是 ``simulation/characters.py``）：
它要读 ``llm/cards.py`` 的 JSON 读写帮手，而 ``simulation/`` 现在**一行都不依赖 llm**
（路由器是窗口层注入的，见 ``state.py`` 的注释）。真放进去就变成
``simulation -> llm`` 的反向依赖，顺手把"仿真能在没有 LLM 文件系统的环境里跑测试"
这条性质弄丢了。

四条规矩：

1. **内置两位不可删、key 不可改**（``pebbles`` / ``past_moon``）：默认外观写在
   ``simulation/appearance.py`` 里，每个数字都挂在源码行号上，删掉就找不回来了。
2. **key 只允许 ``[a-z0-9_-]{1,32}``**：key 就是文件名，``../x`` 之类会写到数据目录外面，
   Windows 还有一批保留名（``con`` / ``nul`` / ``com1`` …）根本建不出来。
3. **删除只删卡文件**：聊天记录 ``chats/<key>.jsonl`` 与 ``lines.json`` 里的台词组都留着，
   玩家可能想用同一个 key 再建一个；要清理历史由调用方另问（见 ``delete(drop_chats=True)``）。
4. **坏卡不当不存在**：卡解析不了时照样列出来（名字退回 key），玩家得能看见它、
   在编辑器里修它——而不是"角色忽然消失了"。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .llm import cards as llm_cards
from .simulation import appearance as ap

#: 内置两位：``(key, 显示名)``。顺序就是菜单顺序。
BUILTIN: tuple[tuple[str, str], ...] = (
    ("pebbles", "五块卵石"),
    ("past_moon", "仰望皓月"),
)

BUILTIN_KEYS = tuple(key for key, _label in BUILTIN)

#: key 的合法形状。刻意只给 ASCII 小写：key 会变成文件名、URL 里的片段、
#: 以及 ``status()`` 的键，中文/空格/大小写混写都会在两个平台上踩不同的坑。
KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")

#: Windows 的保留设备名（``con.json`` 建不出来，报错信息还很难懂）。
RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)


@dataclass(frozen=True)
class Character:
    """下拉框里的一行。

    ``base`` 是"骨骼基底"（``pebbles`` / ``past_moon``）：眼高、躯干高度、
    仪式是杀鼠标还是只挣脱，都来自基底，不是来自颜色。
    """

    key: str
    name: str
    path: Path
    builtin: bool = False
    base: str = "pebbles"
    has_appearance: bool = False


# --------------------------------------------------------------------------
# 位置
# --------------------------------------------------------------------------
def folder_of(folder: Path | str | None = None) -> Path:
    """数据目录：``characters/`` 的上一级。没给就用 ``config.data_dir()``。"""
    if folder is not None:
        return Path(folder)
    from .config import data_dir

    return data_dir()


def card_path(key: str, folder: Path | str | None = None) -> Path:
    return folder_of(folder) / "characters" / f"{key}.json"


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate_key(key: str) -> str:
    """``""`` = 合法；否则是给玩家看的一句话（直接显示在新建对话框里）。"""
    text = str(key or "").strip()
    if not text:
        return "key 不能为空。它是文件名，只能是 a-z、0-9、下划线、减号。"
    if not KEY_PATTERN.match(text):
        return "key 只能用小写字母、数字、下划线和减号，最长 32 个字符。"
    if text in RESERVED:
        return f"「{text}」是 Windows 的保留文件名，换一个。"
    return ""


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------
def _read_document(path: Path) -> dict | None:
    """卡文件 → 字典。不存在/空文件 = ``{}``；坏 JSON = ``None``（调用方退回 key）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        raw = json.loads(text)
    except ValueError:
        return None
    return raw if isinstance(raw, dict) else None


def _name_of(raw: dict | None, fallback: str) -> str:
    if isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
        if name:
            return name
    return fallback


def base_of(key: str, folder: Path | str | None = None) -> str:
    """这个角色用哪套骨骼。

    优先读卡里外观草稿的 ``base_template``；没写/写坏了就按 key 猜：
    内置两位各自是基底，自定义角色默认 ``pebbles``（和 ``puppet_tab.load`` 一致）。
    """
    draft = llm_cards.read_appearance(key, folder)
    base = str(draft.get("base_template") or "").strip()
    if base in ap.BASE_TEMPLATES:
        return base
    return key if key in ap.BASE_TEMPLATES else "pebbles"


def characters(folder: Path | str | None = None) -> list[Character]:
    """全部角色：内置两位在前（顺序固定），其余按 key 排序。

    内置两位**即使没有卡文件也会列出来**：第一次启动时玩家就该在菜单里看到
    「五块卵石 / 仰望皓月」，而不是一个空菜单。
    """
    root = folder_of(folder)
    directory = root / "characters"
    rows: list[Character] = []
    for key, label in BUILTIN:
        path = directory / f"{key}.json"
        raw = _read_document(path)
        rows.append(
            Character(
                key=key,
                name=_name_of(raw, label),
                path=path,
                builtin=True,
                base=base_of(key, root),
                has_appearance=llm_cards.has_appearance(raw),
            )
        )
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            key = path.stem
            if key in BUILTIN_KEYS:
                continue
            raw = _read_document(path)
            rows.append(
                Character(
                    key=key,
                    name=_name_of(raw, key),
                    path=path,
                    builtin=False,
                    base=base_of(key, root),
                    has_appearance=llm_cards.has_appearance(raw),
                )
            )
    return rows


def names(folder: Path | str | None = None) -> list[tuple[str, str]]:
    """``[(key, 显示名), ...]``：菜单和下拉框直接吃的形状。"""
    return [(row.key, row.name) for row in characters(folder)]


def keys(folder: Path | str | None = None) -> list[str]:
    return [row.key for row in characters(folder)]


def exists(key: str, folder: Path | str | None = None) -> bool:
    return any(row.key == key for row in characters(folder))


def find(key: str, folder: Path | str | None = None) -> Character | None:
    return next((row for row in characters(folder) if row.key == key), None)


def resolve_key(key: str, folder: Path | str | None = None) -> str:
    """把配置里那个可能是历史遗留/已被删掉的 key 换成真实存在的。

    玩家删掉正在用的角色后，``config.character`` 会留着一个不存在的 key；
    不兜底就会变成"托盘里没有一项打勾、人偶穿的是别人的外观"。
    """
    text = str(key or "").strip()
    return text if exists(text, folder) else BUILTIN[0][0]


# --------------------------------------------------------------------------
# 外观
# --------------------------------------------------------------------------
def appearance_of(key: str, folder: Path | str | None = None) -> ap.Appearance:
    """卡里的外观覆盖层 + 基底 → 一个完整的 :class:`Appearance`。

    **没改过色的内置角色会拿到 ``appearance.py`` 里那个常量本身**（``build_appearance``
    在 ``override`` 为空时直接返回模板），所以源码数值一个字段都没被重算过。

    头顶部件的默认值走 ``ap.apply_headpiece_default``：自定义角色默认"无"，
    和工坊预览用同一条规则（两边不一致就会"预览里没有、桌面上多一颗"）。
    """
    return ap.build_appearance(
        key,
        base_of(key, folder),
        ap.apply_headpiece_default(key, llm_cards.read_appearance(key, folder)),
    )


# --------------------------------------------------------------------------
# 生命周期
# --------------------------------------------------------------------------
def _write_new_card(
    key: str, name: str, base: str, folder: Path | str | None, body: dict | None = None
) -> str:
    path = card_path(key, folder)
    document = dict(body or {})
    document.setdefault("spec", llm_cards.CARD_SPEC)
    document["name"] = name
    extensions = document.get("extensions")
    if not isinstance(extensions, dict):
        extensions = document["extensions"] = {}
    block = extensions.get("rainworld")
    if not isinstance(block, dict):
        block = extensions["rainworld"] = {}
    appearance = block.get("appearance")
    if not isinstance(appearance, dict):
        appearance = block["appearance"] = {}
    # 只写"用哪套骨骼"。颜色一律不写：写了就等于把基底的源码数值复制一份进卡里，
    # 以后我们改 appearance.py 她也跟着不变了（这正是内置两位要避免的漂移）。
    appearance.setdefault("base_template", base if base in ap.BASE_TEMPLATES else "pebbles")
    return llm_cards.write_document(path, document)


def create(
    key: str,
    name: str,
    base: str = "pebbles",
    folder: Path | str | None = None,
) -> tuple[Character | None, str]:
    """新建一张卡 → ``(Character, "")`` 或 ``(None, 错误)``。

    卡里只写 ``name`` / ``spec`` / ``extensions.rainworld.appearance.base_template``，
    8 个文本字段留空：玩家在工作室里自己填。留空不是为了偷懒——预填一份"看起来像
    五块卵石"的文案，玩家会以为那是她的性格，然后永远不去改。
    """
    root = folder_of(folder)
    key = str(key or "").strip()
    error = validate_key(key)
    if error:
        return None, error
    if exists(key, root):
        return None, f"「{key}」已经存在了，换一个 key。"
    name = str(name or "").strip() or key
    error = _write_new_card(key, name, base, root)
    if error:
        return None, error
    return find(key, root), ""


def duplicate(
    source: str,
    new_key: str,
    new_name: str = "",
    folder: Path | str | None = None,
) -> tuple[Character | None, str]:
    """复制一个角色（卡里**所有内容**原样带走，包括外观与未知字段）。"""
    root = folder_of(folder)
    new_key = str(new_key or "").strip()
    error = validate_key(new_key)
    if error:
        return None, error
    if not exists(source, root):
        return None, f"要复制的「{source}」不存在。"
    if exists(new_key, root):
        return None, f"「{new_key}」已经存在了，换一个 key。"
    if new_key == source:
        return None, "新的 key 和原来的相同。"
    origin = card_path(source, root)
    body = _read_document(origin)
    if body is None:
        return None, f"「{source}」那张卡不是合法 JSON，先修好再复制（不然复制出来也是坏的）。"
    name = str(new_name or "").strip() or f"{_name_of(body, source)}（副本）"
    error = _write_new_card(new_key, name, base_of(source, root), root, body=body)
    if error:
        return None, error
    return find(new_key, root), ""


def delete(
    key: str,
    folder: Path | str | None = None,
    drop_chats: bool = False,
) -> str:
    """删掉一张自定义角色的卡 → ``""`` = 成功，否则是错误串。

    内置两位拒绝删除（它们的默认外观是源码数值，删了卡只会"看起来没了、其实还在"，
    等于给玩家一个骗人的按钮）。聊天记录默认**保留**：``chats/<key>.jsonl`` 里是玩家
    和她的对话，误删找不回来；``drop_chats=True`` 才一起清理。
    """
    root = folder_of(folder)
    if key in BUILTIN_KEYS:
        return "内置角色不能删除（五块卵石与仰望皓月的默认外观写在源码里）。"
    if not exists(key, root):
        return f"「{key}」不存在。"
    path = card_path(key, root)
    try:
        if path.is_file():
            path.unlink()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    if drop_chats:
        chat = root / "chats" / f"{key}.jsonl"
        try:
            if chat.is_file():
                chat.unlink()
        except OSError:
            pass                    # 聊天记录删不掉不影响"角色已删"这个结果
    return ""
