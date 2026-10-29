"""抽池规则。

"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import constants as C


def default_path() -> Path:
    """``%LOCALAPPDATA%\\IteratorPet\\lines.json``（与 config.json 同目录）。"""
    from ..config import config_dir

    return config_dir() / "lines.json"


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
        """文件不存在就写一份默认的出来（玩家随即可编辑）。"""
        if self.path.is_file():
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

    # -- 抽池 ---------------------------------------------------------------
    def pool(self, character: str, event: str) -> list[str]:
        return list(self._table.get(character, {}).get(event) or [])

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
