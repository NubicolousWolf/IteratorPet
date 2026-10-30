"""真正的对话调用：一次请求、一条人话、一道保险（P1 的 L2）。

这一层只做四件事，别的都不做（角色卡/世界书/历史在 L3/L4）：

1. **组请求**：``POST /chat/completions``（OpenAI）或 ``POST /v1/messages``（Anthropic），
   只用标准库，采样参数与超时全部来自 ``llm.json``。
2. **摘话**：模型返回的是一坨结构，我们只要"能放进气泡的那一句"。
   :func:`clean_line` 负责去换行、去引号、去角色名前缀、去括号动作、截断。
3. **熔断**：连续 3 次失败 → 冷却 60 s 内**一次都不发**（审计的"断网降级"）。
4. **限流**：两次调用至少隔 1 s，冷却中的调用直接返回原因。

**关掉就是 v0.1.7**（审计 A1 的底线，最硬的一条）：

* ``enabled`` 为假（没 key / 没模型 / 网址不合法）→ :meth:`LLMClient.ask` **直接返回**，
  transport 的调用次数为 0。这一条有单测钉住（``test_disabled_client_never_touches_the_network``）。
* 这一层不 import 任何仿真/渲染模块，也不碰 Qt —— 它是可以在线程里跑的纯函数集合。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import actions as llm_actions
from . import config as llm_config
from . import models as llm_models

# 气泡里一句最多这么长。模型被要求"不超过 40 字"，这里留一倍余量再硬截，
# 免得某次抽风把整段独白塞进气泡（截断后补「…」）。
MAX_LINE_CHARS = 80
# 熔断与限流（设计增补：宁可让桌宠安静，也别把玩家的额度刷掉）
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 60.0
MIN_INTERVAL_S = 1.0

_NAME_PREFIX = re.compile(r"^\s*(?:五块卵石|过去之月|仰望皓月|她|pebbles|moon)\s*[:：]\s*", re.I)
_QUOTES = "\"'“”‘’「」『』"
_PAREN = re.compile(r"[（(][^）)]*[）)]")
_MARKDOWN = re.compile(r"[*_`]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ChatResult:
    ok: bool
    text: str = ""
    reason: str = ""
    status: int = 0
    latency_s: float = 0.0
    action: str = ""          # LLM 要求的动作（白名单之一），空 = 这次只想说话
    action_reason: str = ""   # 模型给的理由，只进日志/面板，不驱动任何东西


def clean_line(raw, limit: int = MAX_LINE_CHARS) -> str:
    """把模型输出收成"一句能进气泡的话"。空字符串 = 这次不说话。

    规则（都对着一句真实输出想过）：

    * 换行/连续空白 → 一个空格（气泡是单行居中，换行会被吃成糊字）；
    * 去掉 ``五块卵石：`` 这种自己加的角色名前缀（气泡不写角色名）；
    * 去掉成对的引号与 markdown 星号（模型很喜欢 ``“……”``）；
    * 去掉 ``（叹气）`` 这类括号动作（提示词里已经要求不用括号，但模型不听）；
    * 超过 ``limit`` 硬截断并补 ``…``。
    """
    if not isinstance(raw, str):
        return ""
    text = _SPACES.sub(" ", raw).strip()
    if not text:
        return ""
    text = _NAME_PREFIX.sub("", text)
    text = _PAREN.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = text.strip().strip(_QUOTES).strip()
    text = _SPACES.sub(" ", text)
    if not text:
        return ""
    if len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def parse_completion(payload, protocol: str = "openai") -> str:
    """从返回体里取正文。取不到就抛 ``ValueError``（调用方翻成"空回复"）。"""
    if not isinstance(payload, dict):
        raise ValueError("返回体不是对象")
    if protocol == "anthropic":
        blocks = payload.get("content")
        if isinstance(blocks, list):
            parts = [b.get("text", "") for b in blocks if isinstance(b, dict)]
            text = "".join(parts)
            if text.strip():
                return text
        raise ValueError("返回体里没有 content")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    raise ValueError("返回体里没有 choices[0].message.content")


def build_messages(system: str, history, user: str) -> list[dict]:
    """``system`` + 历史 + 当前这句。历史是 ``[(role, text), …]``，只会被截断在上游。"""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    for role, text in history or ():
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": str(text)})
    messages.append({"role": "user", "content": str(user)})
    return messages


class CircuitBreaker:
    """连续失败就闭嘴一会儿。成功一次立刻复位（失败不必"惩罚"到下次开机）。"""

    def __init__(self, threshold: int = BREAKER_THRESHOLD,
                 cooldown_s: float = BREAKER_COOLDOWN_S, clock=time.monotonic):
        self.threshold = max(1, int(threshold))
        self.cooldown_s = float(cooldown_s)
        self._clock = clock
        self.failures = 0
        self.opened_at = 0.0

    @property
    def open(self) -> bool:
        if self.failures < self.threshold:
            return False
        return (self._clock() - self.opened_at) < self.cooldown_s

    def allow(self) -> bool:
        return not self.open

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
            self.opened_at = 0.0
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self._clock()      # 到点自动重试（半开就是"允许下一次"）

    def state(self) -> dict:
        return {"failures": self.failures, "open": self.open,
                "cooldown_left_s": round(max(0.0, self.cooldown_s - (self._clock() - self.opened_at)), 1)
                if self.failures >= self.threshold else 0.0}


class RateLimiter:
    """两次调用至少隔 ``min_interval_s``。只防"气急败坏连发"，不是安全机制。"""

    def __init__(self, min_interval_s: float = MIN_INTERVAL_S, clock=time.monotonic):
        self.min_interval_s = float(min_interval_s)
        self._clock = clock
        self.last_call = -1e9

    def ready(self) -> bool:
        return (self._clock() - self.last_call) >= self.min_interval_s

    def wait_s(self) -> float:
        return max(0.0, self.min_interval_s - (self._clock() - self.last_call))

    def mark(self) -> None:
        self.last_call = self._clock()


class LLMClient:
    """持有 ``store``（配置）+ ``transport``（网络）。**同步**，调用方负责放进线程。"""

    def __init__(self, store: llm_config.LLMStore, transport=None,
                 breaker: CircuitBreaker | None = None,
                 limiter: RateLimiter | None = None, clock=time.monotonic):
        self.store = store
        self.transport = transport or llm_models.UrllibTransport()
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self.limiter = limiter or RateLimiter(clock=clock)
        self.last_result = ChatResult(False, reason="还没调用过")
        self.calls = 0            # 真正发出去的请求数（含失败）——"关掉时必须是 0"

    # -- 能不能用 -----------------------------------------------------------
    @property
    def config(self) -> llm_config.LLMConfig:
        return self.store.config

    @property
    def enabled(self) -> bool:
        cfg = self.config
        return bool(
            cfg.enabled                       # 总开关（设置页 / 右键菜单）
            and llm_config.has_key(cfg)
            and (cfg.model or "").strip()
            and (cfg.base_url or "").startswith(("http://", "https://"))
        )

    def state(self) -> dict:
        """给开发者面板/日志用：**不含 key**。"""
        cfg = self.config
        return {
            "enabled": self.enabled,
            "model": cfg.model,
            "protocol": cfg.protocol,
            "history_messages": cfg.history_messages,
            "calls": self.calls,
            "breaker": self.breaker.state(),
            "last_reason": self.last_result.reason,
            "last_ok": self.last_result.ok,
            "last_latency_s": round(self.last_result.latency_s, 2),
        }

    # -- 一次调用 -----------------------------------------------------------
    def ask(self, messages: list[dict], allow_actions: bool | None = None) -> ChatResult:
        """同步发一次。**关掉时绝不发请求**；所有失败都变成 ``ChatResult``。

        ``allow_actions``：是否把 ``speak``/``act`` 两个工具挂上去。``None`` =
        看 ``llm.json`` 的 ``allow_actions``；``False`` 时模型拿不到工具，只能说话。
        """
        if not self.enabled:
            return self._finish(ChatResult(False, reason=self._disabled_reason()))
        if not self.breaker.allow():
            return self._finish(ChatResult(False, reason="连接多次失败，冷却中"))
        if not self.limiter.ready():
            return self._finish(
                ChatResult(False, reason=f"太快了（{self.limiter.wait_s():.1f}s 后再试）")
            )

        cfg = self.config
        with_tools = bool(cfg.allow_actions) if allow_actions is None else bool(allow_actions)
        key = llm_config.resolve_key(cfg)
        payload = self._payload(messages, with_tools)
        url = llm_models.chat_url(cfg.base_url, cfg.protocol)
        headers = llm_models.auth_headers(key, cfg.protocol)
        started = time.perf_counter()
        self.calls += 1
        self.limiter.mark()
        try:
            body = self.transport.post_json(url, payload, headers, cfg.timeout_s)
        except llm_models.TransportError as exc:
            self.breaker.record(False)
            return self._finish(ChatResult(False, reason=str(exc), status=exc.status,
                                           latency_s=time.perf_counter() - started))
        except Exception as exc:                 # noqa: BLE001 —— 任何异常都不能炸掉桌宠
            self.breaker.record(False)
            return self._finish(ChatResult(False, reason=f"{type(exc).__name__}: {exc}",
                                           latency_s=time.perf_counter() - started))

        latency = time.perf_counter() - started
        try:
            parsed = llm_actions.parse_reply(body, cfg.protocol)
        except Exception as exc:                 # noqa: BLE001 —— 返回体再怪也只算失败
            parsed = None
            reason = f"返回体解析失败（{type(exc).__name__}）"
        else:
            reason = ""
        if parsed is None:
            self.breaker.record(False)
            return self._finish(ChatResult(False, reason=reason, latency_s=latency))

        # 不支持 function calling 的模型会直接甩一段 JSON 文本（§3.4）
        if not parsed.action and not parsed.text:
            self.breaker.record(False)
            return self._finish(ChatResult(False, reason="空回复", latency_s=latency))
        if not parsed.action and parsed.text.strip().startswith("{"):
            fallback = llm_actions.parse_json_fallback(parsed.text)
            if fallback.action or fallback.text:
                parsed = fallback

        text = clean_line(parsed.text)
        if not text and not parsed.action:
            self.breaker.record(False)
            return self._finish(ChatResult(False, reason="回复清洗后是空的", latency_s=latency))
        self.breaker.record(True)
        return self._finish(ChatResult(
            True, text=text, status=200, latency_s=latency,
            action=parsed.action, action_reason=parsed.reason,
        ))

    def _payload(self, messages: list[dict], with_tools: bool) -> dict:
        """组请求体。Anthropic 的 system 是顶层参数，不能留在 messages 里。"""
        cfg = self.config
        payload: dict = {
            "model": cfg.model,
            "temperature": float(cfg.temperature),
            "max_tokens": int(cfg.max_tokens),
            "stream": False,
        }
        rows = [dict(m) for m in messages]
        if cfg.protocol == "anthropic":
            system = "\n\n".join(
                str(m.get("content", "")) for m in rows if m.get("role") == "system"
            )
            rows = [m for m in rows if m.get("role") != "system"]
            if system.strip():
                payload["system"] = system
        payload["messages"] = rows
        if with_tools:
            payload["tools"] = llm_actions.tool_specs(cfg.protocol)
            payload["tool_choice"] = ({"type": "auto"} if cfg.protocol == "anthropic"
                                      else "auto")
        return payload

    def _finish(self, result: ChatResult) -> ChatResult:
        self.last_result = result
        return result

    def _disabled_reason(self) -> str:
        cfg = self.config
        if not cfg.enabled:
            return "已关闭（设置页里勾上「启用大语言模型」）"
        if not llm_config.has_key(cfg):
            return "未启用：没有 API Key"
        if not (cfg.model or "").strip():
            return "未启用：还没选模型"
        return "未启用：Base URL 不对"
