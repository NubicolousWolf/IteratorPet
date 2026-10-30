"""``llm.json``：连接信息与开关。放桌宠根目录（v0.1.7 的数据目录规则）。

**为什么单独一个文件，不塞进 ``config.json``**：

* 连接信息（base_url / api_key / model）玩家会想单独备份、单独搬走；
* ``config.load()`` 里有一段"老配置只有 scale → 反推档位"的兼容逻辑，混进一堆
  无关字段只会让那段更难读；
* 将来的 ``worldbook.json`` / ``characters/*.json`` / ``chats/*.jsonl`` 都在同一个
  目录里，玩家打开文件夹就能看全（用户 2026-10-30 拍板：不做编辑 UI，只发模板）。

**容错口径与 ``lines.json`` 完全一致**（审计 A7）：

* 不存在 / 0 字节 / 只有空白 → 写一份模板；
* 非空的坏 JSON → **不覆盖**（那是玩家手改的），退回默认值并留下 ``last_error``；
* 缺字段 → 补默认；越界值（温度、超时、历史条数）→ 夹进合法区间；
* 未知字段 → 保存时原样保留，别把人家的扩展吃掉。

**密钥只经 :func:`redacted` 进日志**（审计 A9）。:meth:`LLMStore.state` 只回答
"有没有 key"，永远不吐 key 本身。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# 环境变量优先于文件里的明文（适合"不想把 key 写进文件"的人）。
KEY_ENV = "ITERATORPET_LLM_KEY"

PROTOCOLS = ("openai", "anthropic")

# 预置服务：只给 DeepSeek 官方（用户拍板：不内置任何中转模板）。
# 值是 (base_url, 协议)。
PRESETS = {"deepseek": ("https://api.deepseek.com", "openai")}

# 字段 → 合法区间。越界不报错，夹回来（玩家手改 JSON 手抖是常态）。
LIMITS: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "max_tokens": (1, 32768),
    "timeout_s": (1.0, 120.0),
    "history_messages": (0, 50),
    "worldbook_budget": (0, 8000),
    "context_window": (1024, 2_000_000),
    "action_cooldown_s": (0.0, 60.0),
}
# 这些字段必须是整数（JSON 里写成 10.0 也接受，读进来取整）
INT_FIELDS = frozenset(
    {"version", "max_tokens", "history_messages", "worldbook_budget", "context_window"}
)


@dataclass
class LLMConfig:
    """与 ``docs/templates/llm.json`` 一一对应；加字段两边一起改。"""

    version: int = 1
    provider: str = "deepseek"
    protocol: str = "openai"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    api_key_env: str = KEY_ENV
    model: str = "deepseek-chat"
    context_window: int = 65536
    temperature: float = 1.1
    max_tokens: int = 120
    timeout_s: float = 8.0
    allow_actions: bool = False
    file_drop: bool = False
    worldbook_budget: int = 800
    history_messages: int = 10
    action_cooldown_s: float = 2.0


def default_path() -> Path:
    """``<桌宠根目录>/llm.json``（``config.data_dir()`` 已经处理了只读介质兜底）。"""
    from ..config import data_dir

    return data_dir() / "llm.json"


def _clamp(name: str, value):
    low, high = LIMITS[name]
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 不是数字")
    number = min(max(number, low), high)
    return int(round(number)) if name in INT_FIELDS else number


def normalize(raw: dict) -> LLMConfig:
    """把任意 dict 变成一个合法 ``LLMConfig``（不抛异常，坏字段回默认）。"""
    known = {f.name for f in fields(LLMConfig)}
    cfg = LLMConfig()
    for name in known:
        if name not in raw:
            continue
        value = raw[name]
        try:
            if name in LIMITS:
                value = _clamp(name, value)
            elif name in INT_FIELDS:
                value = int(value)
            elif name in ("allow_actions", "file_drop"):
                value = bool(value)
            elif isinstance(getattr(cfg, name), str):
                value = str(value)
        except (TypeError, ValueError):
            continue        # 这一项坏了就用默认值，别的照收
        setattr(cfg, name, value)

    cfg.protocol = cfg.protocol if cfg.protocol in PROTOCOLS else "openai"
    cfg.base_url = (cfg.base_url or "").strip().rstrip("/") or LLMConfig.base_url
    cfg.api_key = (cfg.api_key or "").strip()
    cfg.model = (cfg.model or "").strip()
    return cfg


def redacted(cfg: LLMConfig) -> dict:
    """日志/面板用的副本：key 只留前 4 位 + ``***``。

    **所有**要写进日志的 config 都必须过这里（审计 A9）。短 key 直接 ``***``，
    免得"前 4 位"本身就把 key 露完了。
    """
    data = asdict(cfg)
    key = str(data.get("api_key") or "")
    data["api_key"] = (key[:4] + "***") if len(key) >= 12 else ("***" if key else "")
    return data


def resolve_key(cfg: LLMConfig) -> str:
    """真正用来发请求的 key：环境变量 > 文件明文。

    环境变量名由 :attr:`LLMConfig.api_key_env` 指定（默认
    ``ITERATORPET_LLM_KEY``），所以玩家可以完全不把 key 写进文件。
    """
    name = (cfg.api_key_env or KEY_ENV).strip() or KEY_ENV
    return (os.environ.get(name) or "").strip() or (cfg.api_key or "").strip()


def has_key(cfg: LLMConfig) -> bool:
    return bool(resolve_key(cfg))


class LLMStore:
    """``llm.json`` 的读写器。形状照 ``simulation/lines.py::Lines``，方便互相对照。"""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()
        self.config = LLMConfig()
        self.last_error = ""
        self.loaded_from_file = False

    # -- 文件 ---------------------------------------------------------------
    def install_default_file(self) -> bool:
        """缺文件 / 0 字节 / 只有空白 → 写一份模板。**非空的坏 JSON 不许覆盖。**

        返回 True 表示这次真的写了文件（UI 用它决定要不要提示"已生成模板"）。
        """
        try:
            if self.path.is_file():
                text = self.path.read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    return False         # 有内容就别动，哪怕它是坏的
        except OSError:
            pass
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(_default_document(), indent=2) + "\n", encoding="utf-8"
            )
            return True
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def load(self) -> LLMConfig:
        """读文件 → :class:`LLMConfig`。坏文件不修，只记 ``last_error``。"""
        self.last_error = ""
        self.loaded_from_file = False
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            self.config = LLMConfig()
            return self.config
        if not text.strip():
            self.config = LLMConfig()
            return self.config
        try:
            raw = json.loads(text)
        except ValueError as exc:
            self.last_error = f"llm.json 不是合法 JSON（{exc}），本次用默认值"
            self.config = LLMConfig()
            return self.config
        if not isinstance(raw, dict):
            self.last_error = "llm.json 顶层不是对象，本次用默认值"
            self.config = LLMConfig()
            return self.config
        self.config = normalize(raw)
        if not self.config.model:
            self.last_error = "llm.json 里没有 model，请在设置页选一个"
        self.loaded_from_file = True
        return self.config

    def save(self, cfg: LLMConfig | None = None) -> bool:
        """原子写回（临时文件 + replace），并发读的人不会看到半个 JSON。

        落盘前先过一遍 :func:`normalize`：文件是给玩家看也给他们手改的合同，
        里面出现 ``history_messages = 999`` 只会让人以为我们读错了。
        未知键原样保留：玩家可能已经照着模板加了字段。
        """
        cfg = normalize(asdict(cfg or self.config))
        raw: dict = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, ValueError):
            raw = {}
        raw.update(asdict(cfg))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".llm-", suffix=".json"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(raw, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            Path(tmp).replace(self.path)
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.config = cfg
        return True

    # -- 给日志/面板 --------------------------------------------------------
    def state(self) -> dict:
        """启动日志用：**不含 key**，只回答"有没有"。"""
        return {
            "path": str(self.path),
            "exists": self.path.is_file(),
            "model": self.config.model,
            "has_key": has_key(self.config),
            "allow_actions": bool(self.config.allow_actions),
            "file_drop": bool(self.config.file_drop),
            "loaded_from_file": self.loaded_from_file,
            "last_error": self.last_error,
        }


def _default_document() -> dict:
    """模板文件的内容 = 数据类默认值（唯一真相，别手抄第二份）。"""
    return asdict(LLMConfig())
