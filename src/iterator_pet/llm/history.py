"""对话历史（``chats/<角色>.jsonl``，一行一条）。

**为什么单独存文件**（而不是只放在内存里）：

* 玩家能直接看见/删掉自己跟她的对话（和 ``lines.json`` 一个待遇）；
* 下次启动她还记得上一轮说过什么——"记得"是这类桌宠最廉价也最有效的连续感；
* 一行一个 JSON，坏行只丢那一行，不会因为一次断电丢掉整份历史。

条数上限 200（计划 §1.2 的约定）：超了就在**追加时**原子重写一份只留最近 200 条的，
不做后台清理、不建索引。``limit`` 参数留给测试。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

MAX_ENTRIES = 200
# 超过上限这么多条才重写一次文件（每次追加都重写 200 行太浪费）
TRIM_SLACK = 20


def _folder() -> Path:
    from ..config import data_dir

    return data_dir()


class ChatLog:
    def __init__(self, character: str = "pebbles", folder: Path | str | None = None,
                 limit: int = MAX_ENTRIES):
        self.character = character
        self.folder = Path(folder) if folder else _folder()
        self.limit = max(2, int(limit))
        self.last_error = ""

    @property
    def path(self) -> Path:
        return self.folder / "chats" / f"{self.character}.jsonl"

    # -- 读 ---------------------------------------------------------------
    def recent(self, count: int) -> list[tuple[str, str]]:
        """最近 ``count`` 条，旧→新。坏行跳过；文件不在就是空的（不是错误）。"""
        if count <= 0:
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[tuple[str, str]] = []
        for line in lines[-count * 2:]:        # 多读一点，坏的会被跳掉
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            role, text = item.get("role"), item.get("text")
            if role in ("user", "assistant") and isinstance(text, str) and text:
                out.append((role, text))
        return out[-count:]

    # -- 写 ---------------------------------------------------------------
    def append(self, role: str, text: str) -> bool:
        if role not in ("user", "assistant") or not text:
            return False
        record = {"ts": round(time.time(), 3), "role": role, "text": str(text)[:500]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self._maybe_trim()
        return True

    def _maybe_trim(self) -> None:
        """超过 ``limit + slack`` 条就原子重写成最近 ``limit`` 条。"""
        try:
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return
        if len(lines) <= self.limit + TRIM_SLACK:
            return
        try:
            handle, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".chat-", suffix=".jsonl")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines[-self.limit:]) + "\n")
            Path(tmp).replace(self.path)
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    def clear(self) -> bool:
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def state(self) -> dict:
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        return {
            "path": str(self.path),
            "bytes": size,
            "entries": len(self.recent(self.limit)),
            "last_error": self.last_error,
        }
