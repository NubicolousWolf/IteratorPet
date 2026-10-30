"""抽池规则。

"""

from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
from pathlib import Path

from . import constants as C


def _writable(folder: Path) -> bool:
    """目录能不能写（只用于选位置，探针文件写完立刻删）。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".iterator-pet-write-probe"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def pick_pool_path(app_path: Path, legacy_path: Path, can_write=_writable) -> Path:
    """选台词文件位置：**优先桌宠根目录**，其次老位置。

    1. 桌宠目录里已经有 ``lines.json`` → 就用它；
    2. 老位置（``%LOCALAPPDATA%``）有 → **搬过来**（玩家改过的内容不丢）；搬不动就继续用老的；
    3. 都没有 → 桌宠目录可写就写那儿，不可写（Program Files / 只读介质）退回老位置。
    """
    if app_path.is_file():
        return app_path
    if legacy_path.is_file():
        try:
            app_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy_path, app_path)
            return app_path
        except OSError:
            return legacy_path
    return app_path if can_write(app_path.parent) else legacy_path


def default_path() -> Path:
    """默认位置：**桌宠根目录**（打包版 = 启动.exe 旁边；源码版 = 仓库根）。

    只读介质（Program Files）下 ``data_dir()`` 会退回 AppData，这里再兜一次老位置，
    保证"写不进去"不会变成"没有台词"。
    """
    from ..config import data_dir, legacy_data_dir

    return pick_pool_path(data_dir() / "lines.json", legacy_data_dir() / "lines.json")


class Lines:
    """对话池。抽池规则：等概率；池长 > 1 时不与上一次相同；哨兵 = 沉默。"""

    def __init__(self, path: Path | str | None = None, seed: int = 5,
                 payload: dict | None = None, autoload: bool = True):
        self.path = Path(path) if path is not None else default_path()
        self._default = payload if payload is not None else C.DEFAULT_LINES
        self._table: dict[str, dict[str, list[str]]] = {}
        self._signature: int | None = None
        self._reload_in = C.DIALOGUE_RELOAD_TICKS
        self._last: dict[tuple[str, str], str] = {}
        self.rng = random.Random(seed)
        self.loaded_from_file = False
        self.last_error: str | None = None
        self.installed_default = False
        if autoload:
            self.install_default_file()
            self.reload(force=True)

    # -- 文件 ---------------------------------------------------------------
    def install_default_file(self) -> bool:
        """文件**不存在或是空白**时写一份默认的出来（玩家随即可编辑）。

        空白文件也要修：0 字节的 `lines.json`（写入被打断 / 编辑器新建没保存）在旧实现里
        永远命中 "is_file() -> True" 这条早退，于是玩家看到"文件在，但里面啥都没有"。
        """
        if self.path.is_file():
            try:
                size = self.path.stat().st_size
                if size == 0:
                    blank = True
                elif size < 8:                      # 只有空白字符的小文件
                    blank = not self.path.read_text(encoding="utf-8").strip()
                else:
                    blank = False
            except OSError:
                blank = False
            if not blank:
                return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._default, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.installed_default = True
            return True
        except OSError as exc:                      # 只读目录等：不致命
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def _mtime(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def reload(self, force: bool = False) -> bool:
        """mtime 变了才重读。返回"表是否被替换"。"""
        self.install_default_file()      # 文件被删/被清空时，每 2 s 有一次自愈机会
        signature = self._mtime()
        if not force and signature == self._signature:
            return False
        table: dict | None = None
        if signature is not None:
            try:
                table = self._validate(json.loads(self.path.read_text(encoding="utf-8")))
                self.last_error = None
            except (OSError, ValueError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                table = None                        # 坏 JSON：保留旧表
        if table is None:
            if not self._table:                     # 首次 / 从来没有可用表 -> 用内置默认
                self._table = self._validate(self._default)
            self._signature = signature
            return False
        self._table = table
        self._signature = signature
        self.loaded_from_file = True
        return True

    def tick(self, ticks: int = 1) -> None:
        """每 tick 调用：到点了查一次 mtime（热重载）。"""
        self._reload_in -= ticks
        if self._reload_in <= 0:
            self._reload_in = C.DIALOGUE_RELOAD_TICKS
            self.reload()

    # -- 校验 ---------------------------------------------------------------
    @staticmethod
    def _validate(data: object) -> dict[str, dict[str, list[str]]]:
        """只留 ``characters.<角色>.<事件> = [str, ...]``；其余一律丢掉。"""
        out: dict[str, dict[str, list[str]]] = {}
        if not isinstance(data, dict):
            return out
        characters = data.get("characters")
        if not isinstance(characters, dict):
            return out
        for character, events in characters.items():
            if not isinstance(character, str) or not isinstance(events, dict):
                continue
            kept: dict[str, list[str]] = {}
            for event, pool in events.items():
                if not isinstance(event, str) or not isinstance(pool, list):
                    continue
                kept[event] = [item for item in pool if isinstance(item, str)]
            out[character] = kept
        return out

    def state(self) -> dict:
        """给 `status()` / 日志看：文件在哪、有没有、里面几条。"""
        try:
            size = self.path.stat().st_size
        except OSError:
            size = -1
        entries = sum(len(pool) for events in self._table.values() for pool in events.values())
        return {
            "path": str(self.path),
            "exists": self.path.is_file(),
            "size": size,
            "entries": entries,
            "characters": {name: sorted(events) for name, events in self._table.items()},
            "loaded_from_file": self.loaded_from_file,
            "installed_default": self.installed_default,
            "last_error": self.last_error,
        }

    # -- 保存（编辑器用）------------------------------------------------------
    def save_pools(self, updates: dict[tuple[str, str], list[str]]) -> bool:
        """把 ``{(角色, 事件): [台词...]}`` 写回 ``lines.json``。

        只动这几个组的台词，**保留**文件里其他键（`version`、以后新增的字段、别的角色的自定义组）。
        原文读不出来就从内置默认起稿再套修改；写临时文件 + `replace`，避免半个 JSON。
        成功返回 True（并已强制热重载），失败返回 False（旧文件保持不动）。
        """
        raw: dict = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw.get("characters"), dict):
            raw = json.loads(json.dumps(self._default))
        characters = raw.setdefault("characters", {})
        raw.setdefault("version", self._default.get("version", 1))

        for (character, event), items in updates.items():
            bucket = characters.get(character)
            if not isinstance(bucket, dict):
                bucket = characters[character] = {}
            bucket[event] = [str(item).strip() for item in items if str(item).strip()]

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".lines-", suffix=".json")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(raw, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            Path(tmp).replace(self.path)
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

        self.reload(force=True)
        return True

    # -- 抽池 ---------------------------------------------------------------
    def pool(self, character: str, event: str) -> list[str]:
        """某个角色某个事件的台词表。

        **缺这一组**（老 ``lines.json`` 没有后来新增的 ``ritual``）时回落到内置默认：
        玩家不该因为"没手动加新事件"就在更新后变成哑巴。玩家**故意清空**的组
        （键存在、值是空表）仍然按"沉默"处理，不会被默认值顶回来。
        """
        bucket = self._table.get(character) or {}
        if event in bucket:
            return list(bucket[event])
        default = (self._default.get("characters") or {}).get(character) or {}
        return list(default.get(event) or [])

    def next_line(self, character: str, event: str) -> str | None:
        """返回一句台词；``None`` = 这次不说话（池空 / 抽到哨兵）。"""
        pool = self.pool(character, event)
        if not pool:
            return None
        key = (character, event)
        if len(pool) > 1:
            last = self._last.get(key)
            candidates = [item for item in pool if item != last] or pool
        else:
            candidates = pool
        choice = self.rng.choice(candidates)
        self._last[key] = choice
        if not choice.strip() or choice.strip() == C.DIALOGUE_SILENT_ENTRY:
            return None                              # 哨兵：抽到了但沉默
        return choice
