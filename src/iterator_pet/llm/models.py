"""``GET /models``、最小可用性探测，以及它们的后台任务（P1 的 L1）。

三条设计约束，来自 ``docs/PLAN-llm.md`` §8：

* **A10**：各家中转的 ``/models`` 返回体不一样。解析器只认三种形状
  （``{"data":[{"id":…}]}``、``{"data":[{"model":…}]}``、裸数组），都不认就
  抛错让设置页退化成"手填模型名"，绝不瞎猜一个 id 出来。
* **A3**：线程里只做网络与解析，结果塞进一个 ``queue``；主线程 :meth:`poll`
  取。worker **不碰 Qt、不碰仿真**。
* 只用标准库 ``urllib``，不引 SDK（发布包体积和依赖都少一层）。

为什么超时写死 3 s：这是设置页的交互式操作，人盯着按钮等；8 s 的
``timeout_s`` 是给将来的对话调用用的，两条路径不共用这个数。
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import partial

INTERACTIVE_TIMEOUT = 3.0        # 设置页里拉模型 / 测试连接的上限
MAX_BODY = 4 * 1024 * 1024       # 再大的返回体肯定是中转在卖别的东西


class TransportError(Exception):
    """网络层的失败，已经翻成人话；``status`` 是 HTTP 码（没有就是 0）。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str = ""
    context_window: int = 0
    max_output_tokens: int = 0

    @property
    def label(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    models: tuple[ModelInfo, ...] = ()
    reason: str = ""
    status: int = 0


# ------------------------------------------------------------------ 端点 --
def _base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def models_url(base_url: str, protocol: str = "openai") -> str:
    """DeepSeek / 多数中转：``{base}/models``；Anthropic：``{base}/v1/models``。"""
    if protocol == "anthropic":
        return _base(base_url) + "/v1/models"
    return _base(base_url) + "/models"


def chat_url(base_url: str, protocol: str = "openai") -> str:
    if protocol == "anthropic":
        return _base(base_url) + "/v1/messages"
    return _base(base_url) + "/chat/completions"


def auth_headers(api_key: str, protocol: str = "openai") -> dict[str, str]:
    if protocol == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    return {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}


def _check_base_url(base_url: str) -> str:
    base = _base(base_url)
    if not base.startswith(("http://", "https://")):
        raise TransportError("Base URL 要以 http:// 或 https:// 开头")
    return base


# ---------------------------------------------------------------- 返回体 --
def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_models(payload) -> list[ModelInfo]:
    """把 ``/models`` 的返回体变成列表。认不出来就抛 ``ValueError``（A10）。"""
    items = payload
    if isinstance(payload, dict):
        for key in ("data", "models", "result"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        else:
            raise ValueError("返回体里没有模型列表")
    if not isinstance(items, list):
        raise ValueError("返回体不是数组")

    models: list[ModelInfo] = []
    for item in items:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        identifier = (
            item.get("id") or item.get("model") or item.get("name") or item.get("slug")
        )
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        models.append(
            ModelInfo(
                id=identifier.strip(),
                name=str(item.get("name") or item.get("display_name") or ""),
                context_window=_int(
                    item.get("context_window")
                    or item.get("context_length")
                    or item.get("max_context_length")
                ),
                max_output_tokens=_int(
                    item.get("max_output_tokens")
                    or item.get("max_completion_tokens")
                    or item.get("max_tokens")
                ),
            )
        )
    if not models:
        raise ValueError("返回体里没有可用的模型 id")
    return models


# ---------------------------------------------------------------- 传输层 --
class UrllibTransport:
    """真的走网络的实现。测试注入假的即可（那份不需要网络）。"""

    def get_json(self, url: str, headers: dict, timeout: float = INTERACTIVE_TIMEOUT):
        return self._request(url, headers, timeout, data=None)

    def post_json(
        self, url: str, payload: dict, headers: dict, timeout: float = INTERACTIVE_TIMEOUT
    ):
        body = json.dumps(payload).encode("utf-8")
        return self._request(url, headers, timeout, data=body)

    @staticmethod
    def _request(url: str, headers: dict, timeout: float, data: bytes | None):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_BODY)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(400).decode("utf-8", "replace").strip()
            except Exception:            # noqa: BLE001 —— 读错误体失败不影响报错
                detail = ""
            raise TransportError(
                f"HTTP {exc.code}" + (f"：{detail[:160]}" if detail else ""), status=exc.code
            ) from None
        except socket.timeout:
            raise TransportError(f"超时（{timeout:g} s 内没有响应）") from None
        except urllib.error.URLError as exc:
            raise TransportError(f"网络错误：{exc.reason}") from None
        except OSError as exc:
            raise TransportError(f"网络错误：{exc}") from None
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise TransportError("返回不是 JSON") from None


# ------------------------------------------------------------------ 动作 --
def run_fetch(
    transport,
    base_url: str,
    api_key: str,
    protocol: str = "openai",
    timeout: float = INTERACTIVE_TIMEOUT,
) -> FetchResult:
    """拉模型列表。**不抛异常**，失败全在 :class:`FetchResult` 里。"""
    if not (api_key or "").strip():
        return FetchResult(False, reason="没有 API Key")
    try:
        url = models_url(_check_base_url(base_url), protocol)
        payload = transport.get_json(url, auth_headers(api_key, protocol), timeout)
    except TransportError as exc:
        return FetchResult(False, reason=str(exc), status=exc.status)
    try:
        models = parse_models(payload)
    except ValueError as exc:
        return FetchResult(False, reason=str(exc))
    return FetchResult(True, tuple(models))


def run_probe(transport, base_url: str, api_key: str, model: str,
              protocol: str = "openai", timeout: float = INTERACTIVE_TIMEOUT) -> FetchResult:
    """``max_tokens=1`` 的最小请求：只想确认"这个地址 + 这个 key + 这个模型"能用。"""
    if not (api_key or "").strip():
        return FetchResult(False, reason="没有 API Key")
    if not (model or "").strip():
        return FetchResult(False, reason="先选一个模型")
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        url = chat_url(_check_base_url(base_url), protocol)
        transport.post_json(url, payload, auth_headers(api_key, protocol), timeout)
    except TransportError as exc:
        return FetchResult(False, reason=str(exc), status=exc.status)
    return FetchResult(True, status=200)


class BackgroundJob:
    """把一个纯函数丢进线程，主线程 :meth:`poll` 拿结果。

    刻意做得比 ``QThread`` 还笨：**只回一个值、只有一条路**。worker 里除了
    网络什么都不碰（审计 A3），所以这里没有任何 Qt 依赖，测试也不起窗口。
    """

    def __init__(self, fn, *args, **kwargs):
        self._fn = partial(fn, *args, **kwargs)
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "BackgroundJob":
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            result = self._fn()
        except BaseException as exc:      # noqa: BLE001 —— 任何异常都要变成一句话
            result = FetchResult(False, reason=f"{type(exc).__name__}: {exc}")
        try:
            self._queue.put_nowait(result)
        except queue.Full:
            pass

    def poll(self):
        """还没好返回 ``None``；好了返回结果（只返回一次）。"""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()
