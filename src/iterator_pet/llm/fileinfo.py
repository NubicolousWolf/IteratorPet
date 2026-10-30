"""拖进来的文件：**只看名字**，永不读内容。

三条死规则（PLAN-llm §4.3 + R6，审计 A15）：

1. 只取 ``Path.name``（基名 + 后缀），**不传路径、不读内容**；
2. 清洗：去控制字符/换行、压成一行、截到 64 字符（保住后缀）——文件名是**数据**，
   不是指令，所以进 prompt 时用「」包起来；
3. 后缀白名单之外一律回"这个后缀我不认识"，不猜、不硬读。

一个特例（用户 2026-10-30）：名字以 ``即时广播-`` 开头时，**不是文件**，而是玩家
借文件名直接对她说话（拖放比打开输入框快）。此时把前缀后面的部分当玩家原文发过去。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_NAME_CHARS = 64
MAX_BROADCAST_CHARS = 200
BROADCAST_PREFIX = "即时广播-"

EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".txt", ".md", ".json", ".py", ".js",
    ".zip", ".exe", ".pdf", ".docx",
)

FILE_PROMPT = (
    "玩家把一份文件放到你面前，你只看得见它的名字：「{name}」。"
    "你看不到里面的内容。用一句话说说这个名字让你想到什么——别说你在读文件。"
)

BROADCAST_PROMPT = "玩家借文件名直接对你说话：「{text}」。像平时一样回一句。"


@dataclass(frozen=True)
class Drop:
    """一次拖放被解析后的结果。``kind`` 为空表示"不看这个"。"""

    kind: str = ""          # "file" | "broadcast" | ""
    name: str = ""          # 清洗后的文件名（含后缀）
    text: str = ""          # 广播时=玩家原文；文件时=专用提示词
    reason: str = ""        # kind 为空时说明为什么

    @property
    def ok(self) -> bool:
        return bool(self.kind)


def sanitize_name(raw: str) -> str:
    """只留基名 + 后缀，去控制字符与换行，压成一行，截到 64 字符（后缀优先保住）。"""
    text = str(raw or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    # 有的 drop 会带路径（Windows 也可能带反斜杠），只取最后一段
    text = text.replace("\\", "/").split("/")[-1]
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if len(text) <= MAX_NAME_CHARS:
        return text
    suffix = Path(text).suffix
    if suffix and len(suffix) < MAX_NAME_CHARS:
        keep = MAX_NAME_CHARS - len(suffix)
        return text[:keep] + suffix
    return text[:MAX_NAME_CHARS]


def classify(raw: str) -> Drop:
    """把玩家拖进来的名字分成"文件 / 广播 / 不看"。"""
    name = sanitize_name(raw)
    if not name:
        return Drop(reason="名字是空的")

    if name.startswith(BROADCAST_PREFIX):
        text = " ".join(name[len(BROADCAST_PREFIX):].split()).strip()[:MAX_BROADCAST_CHARS]
        if not text:
            return Drop(reason="广播里没写话")
        return Drop(kind="broadcast", name=name, text=BROADCAST_PROMPT.format(text=text))

    suffix = Path(name).suffix.lower()
    if not suffix:
        return Drop(name=name, reason="没有后缀，我不知道是什么")
    if suffix not in EXTENSIONS:
        return Drop(name=name, reason=f"我不认识 {suffix} 这种文件")
    return Drop(kind="file", name=name, text=FILE_PROMPT.format(name=name))
