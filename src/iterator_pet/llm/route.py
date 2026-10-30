"""把"要不要说话/要不要动"接到 LLM 上（P1 + 行动）。

分工非常窄，是故意窄的：

* **主线程（仿真 tick）**：判断能不能问 → 组装 prompt（读文件很小）→ 起一个
  ``BackgroundJob`` → 每 tick ``poll()`` 一次。从不阻塞。
* **worker 线程**：只发 HTTP、只往队列里投一个 :class:`~.client.ChatResult`
  （审计 A3：不碰 Qt、不碰仿真）。
* **失败一律退回本地台词池**：没 key / 断网 / 401 / 空回复都只影响"这次说什么"，
  绝不影响她动不动、绝不弹窗。

动作（``act``）**不在 worker 里落地**：模型给的动作先进 :class:`~.actions.IntentQueue`，
由 ``SimulationState`` 每 tick 消费一条并过闸门（仪式中/拖拽中/说话中一票否决）。
这样"模型想动"和"她能不能动"是两件事，出了问题也分得开。

关掉 LLM 时 :meth:`LLMRouter.request` 直接返回 ``False``，调用方照旧抽本地池——
所以"关掉 = v0.1.7"在这条链路上是可验证的（``test_router_disabled_falls_back``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import actions as llm_actions
from . import client as llm_client
from . import config as llm_config
from . import history as llm_history
from . import models as llm_models
from . import prompt as llm_prompt
from . import worldbook as llm_worldbook


@dataclass(frozen=True)
class Reply:
    """一次"该说话了"的结果。``source="pool"`` 表示这次没用 LLM。"""

    text: str = ""
    source: str = "pool"
    event: str = ""
    reason: str = ""
    action: str = ""          # 模型同时要求的动作（已经进队列，这里只是给面板看）


class LLMRouter:
    def __init__(
        self,
        store: llm_config.LLMStore,
        character: str = "pebbles",
        folder=None,
        transport=None,
        job_factory=None,
    ):
        self.store = store
        self.character = character
        self.folder = folder
        self.client = llm_client.LLMClient(store, transport=transport)
        self.chat = llm_history.ChatLog(character, folder=folder)
        self.card = llm_prompt.Card()
        self.book = llm_worldbook.Worldbook(
            path=(folder / "worldbook.json") if folder else None
        )
        self.intents = llm_actions.IntentQueue()
        self._job_factory = job_factory or llm_models.BackgroundJob
        self._job = None
        self._pending: tuple[str, llm_prompt.Assembled] | None = None
        # 请求序号：调用方（调试入口）靠它认出"这条回执是不是我这次要的"。
        # 没有它就只能看 last_reply.event，而上一次的失败回执会一直躺在那儿。
        self.seq = 0
        self._pending_seq = 0
        self.last_reply_seq = 0
        self.last_reply = Reply()
        self.last_debug: dict = {}
        self.reload()

    # -- 文件 ---------------------------------------------------------------
    def reload(self) -> None:
        """重读角色卡与世界书（设置页保存后 / 换角色时调）。"""
        self.card, self.card_error = llm_prompt.load_card(self.character, self.folder)
        self.book = llm_worldbook.Worldbook(
            path=(self.folder / "worldbook.json") if self.folder else None
        )
        self.book.load()

    def set_character(self, character: str) -> None:
        if character == self.character:
            return
        self.character = character
        self.chat = llm_history.ChatLog(character, folder=self.folder)
        self.intents.clear()               # 换角色时不许把旧意图带过去
        self.reload()

    # -- 状态 ---------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.client.enabled

    @property
    def busy(self) -> bool:
        return self._job is not None

    def state(self) -> dict:
        data = self.client.state()
        data.update(
            {
                "character": self.character,
                "allow_actions": bool(self.store.config.allow_actions),
                "intents": self.intents.state(),
                "worldbook": self.book.state(),
                "chats": self.chat.state(),
                "pending": self.busy,
                "last_source": self.last_reply.source,
                "last_action": self.last_reply.action,
                "last_debug": self.last_debug,
                "card_error": getattr(self, "card_error", ""),
            }
        )
        return data

    # -- 主线程：发一个请求 --------------------------------------------------
    def request(self, event: str, *, last_line: str = "", mood: str = "",
                distance: str = "") -> bool:
        """能不能用 LLM 说话。返回 ``False`` 表示调用方照旧抽本地池。"""
        if not self.enabled or self.busy:
            return False
        situation = llm_prompt.Situation(
            event=event, mood=mood, distance=distance, last_line=last_line
        )
        assembled = llm_prompt.assemble(
            self.card,
            self.book,
            self.store.config,
            history=self.chat.recent(self.store.config.history_messages),
            situation=situation,
        )
        self.last_debug = assembled.debug()
        # 玩家这一侧先落盘：即使请求失败，"她听到过"这件事也算发生过
        self.chat.append("user", situation.to_text())
        self.seq += 1
        self._pending_seq = self.seq
        self._pending = (event, assembled)
        allow_actions = bool(self.store.config.allow_actions)
        self._job = self._job_factory(
            self.client.ask, assembled.messages, allow_actions
        ).start()
        return True

    # -- 主线程：调试入口（菜单里"直接给她发一句话"）-------------------------
    def ask_text(self, text: str, event: str = "debug") -> bool:
        """把玩家打的一句话原样发过去。返回 ``False`` = 没发（关着/忙/空）。

        和 :meth:`request` 的区别只有一个：用户那一侧不是 ``Situation`` 模板，
        而是玩家手打的原文——用来看"她现在会怎么回"。
        """
        text = (text or "").strip()
        if not text or not self.enabled or self.busy:
            return False
        assembled = llm_prompt.assemble(
            self.card,
            self.book,
            self.store.config,
            history=self.chat.recent(self.store.config.history_messages),
            situation_text=text,
        )
        self.last_debug = assembled.debug()
        self.chat.append("user", text)
        self.seq += 1
        self._pending_seq = self.seq
        self._pending = (event, assembled)
        self._job = self._job_factory(
            self.client.ask, assembled.messages, bool(self.store.config.allow_actions)
        ).start()
        return True

    # -- 主线程：每 tick 收一次 ----------------------------------------------
    def poll(self) -> Reply | None:
        """还没好 → ``None``；好了 → :class:`Reply`（失败时 ``text`` 为空）。"""
        if self._job is None:
            return None
        result = self._job.poll()
        if result is None:
            return None
        event, _assembled = self._pending or ("", None)
        self.last_reply_seq = self._pending_seq      # 本次回执属于哪一个请求
        self._job = None
        self._pending = None
        if isinstance(result, llm_client.ChatResult) and result.ok:
            action = getattr(result, "action", "") or ""
            if action:
                # 只投队列，绝不在这里改仿真（审计 A3）
                self.intents.push(
                    action,
                    getattr(result, "action_reason", ""),
                    min_interval_s=float(self.store.config.action_cooldown_s),
                )
            if result.text:
                self.chat.append("assistant", result.text)
            self.last_reply = Reply(
                text=result.text, source="llm", event=event, action=action
            )
        else:
            reason = getattr(result, "reason", "") or "没有回复"
            self.last_reply = Reply(text="", source="llm", event=event, reason=reason)
        return self.last_reply

    # -- 主线程：给仿真取一个动作 --------------------------------------------
    def peek_intent(self) -> llm_actions.Intent | None:
        """看一眼队首动作**不消费**——仿真每 tick 用它在闸门关着时"先别丢"。"""
        if not self.enabled or not self.store.config.allow_actions:
            self.intents.clear()
            return None
        return self.intents.peek()

    def take_intent(self) -> llm_actions.Intent | None:
        """取一条待执行的动作。总开关或"允许控制行动"关掉时**一条都不给**。"""
        if not self.enabled or not self.store.config.allow_actions:
            self.intents.clear()
            return None
        return self.intents.pop()
