"""对话事件（用户 2026-10-29 划定：``talk`` / ``drag``；2026-10-30 追加 ``ritual``）。

形状照源码的 ``SpecialEvent(string eventName)``（`ss.cs:4335`）：事件是**字符串键名**，
由外部条件触发；事件本身不说话，由对话层决定说不说、说什么。

触发点与冷却（都可在设置页改，见 :meth:`set_cooldowns`）：

* ``drag``：``begin_drag()`` 成功的那一 tick（人偶**或**底座），同一次拖拽只触发一次。
* ``talk``：``behaviour`` 变成 ``talk`` 的第一 tick。
* ``ritual``：仪式**完成**的那一 tick —— 五块卵石是 ``temper.py:203`` 的 ``"flash"``，
  过去之月是 ``"tremble_done"``（她只有挣脱，没有杀鼠标，见 ``temper.py:171``）。

``drag`` / ``talk`` **不**在仪式进行中触发（两套台词会打架）；``ritual`` 本身就是仪式，
它要能在那一 tick 说话。冷却为 0 时表示"不限"，但同一 tick 仍然最多一个事件。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C

#: 外部可以 ``push()`` 进来的事件（2026-11-01 第一组）。
#: 它们不是"状态变化"：启动、被戳、空闲自语都没有一个可观测的前后态可比，
#: 所以由调用方显式排队（``SimulationState.poke()`` / ``announce_startup()`` /
#: ``_step_idle_talk()``），再由 :meth:`DialogueEvents.tick` 每 tick 最多取一条。
PENDING_EVENTS = ("startup", "poke", "idle_talk")


@dataclass
class DialogueEvents:
    drag_cooldown: int = 0
    talk_cooldown: int = 0
    ritual_cooldown: int = 0
    prev_behaviour: str = "roaming"
    prev_dragging: bool = False
    drag_fires: int = 0
    talk_fires: int = 0
    ritual_fires: int = 0
    poke_fires: int = 0
    # 外部触发的事件（2026-11-01 第一组）：``startup`` / ``poke`` / ``idle_talk``。
    # 三者都不是"状态变化"，是别人**明确要求说一句**，所以走队列，不走状态比对。
    pending: str | None = None
    poke_cooldown: int = 0
    poke_cooldown_ticks: int = field(default_factory=lambda: C.DIALOGUE_POKE_COOLDOWN)
    # 可调冷却（单位 tick，40 TPS）；窗口层按 llm.json 里的秒数换算后写进来
    drag_cooldown_ticks: int = field(default_factory=lambda: C.DIALOGUE_DRAG_COOLDOWN)
    talk_cooldown_ticks: int = field(default_factory=lambda: C.TALK_COOLDOWN_TICKS)
    ritual_cooldown_ticks: int = field(default_factory=lambda: C.DIALOGUE_RITUAL_COOLDOWN)

    def push(self, name: str) -> bool:
        """排一个外部事件（``startup`` / ``poke`` / ``idle_talk``）。返回是否排进去了。

        同一 tick 只留一个：戳了一下又来一条 ``idle`` 是不可能的（空闲恰恰要求
        没有互动），真撞上了也是后到的覆盖先到的。
        """
        if name not in PENDING_EVENTS:
            return False
        if name == "poke" and self.poke_cooldown > 0:
            return False
        self.pending = name
        return True

    def set_cooldowns(self, drag: int | None = None, talk: int | None = None,
                      ritual: int | None = None) -> None:
        """改冷却长度（tick）。0 = 不限；负数按 0 处理。已在走的冷却**不**被改短。"""
        if drag is not None:
            self.drag_cooldown_ticks = max(0, int(drag))
            self.drag_cooldown = min(self.drag_cooldown, self.drag_cooldown_ticks)
        if talk is not None:
            self.talk_cooldown_ticks = max(0, int(talk))
            self.talk_cooldown = min(self.talk_cooldown, self.talk_cooldown_ticks)
        if ritual is not None:
            self.ritual_cooldown_ticks = max(0, int(ritual))
            self.ritual_cooldown = min(self.ritual_cooldown, self.ritual_cooldown_ticks)

    def tick(self, behaviour: str, dragging: bool, ritual_active: bool = False,
             ritual_done: bool = False) -> str | None:
        """推进一步，返回本 tick 触发的事件名（最多一个）或 None。"""
        if self.drag_cooldown > 0:
            self.drag_cooldown -= 1
        if self.talk_cooldown > 0:
            self.talk_cooldown -= 1
        if self.ritual_cooldown > 0:
            self.ritual_cooldown -= 1
        if self.poke_cooldown > 0:
            self.poke_cooldown -= 1

        fired: str | None = None
        if ritual_done and self.ritual_cooldown <= 0:
            fired = "ritual"
            self.ritual_cooldown = self.ritual_cooldown_ticks
            self.ritual_fires += 1
            self.pending = None          # 仪式优先：同一 tick 里别的都得让路
        elif self.pending is not None:
            # 外部排进来的（启动问候 / 戳一下 / 空闲自语）。仪式进行中作废：
            # 两套台词会打架，而这条请求本来也没有时效性。
            fired = None if ritual_active else self.pending
            self.pending = None
            if fired == "poke":
                self.poke_cooldown = self.poke_cooldown_ticks
                self.poke_fires += 1
        elif dragging and not self.prev_dragging and not ritual_active and self.drag_cooldown <= 0:
            fired = "drag"
            self.drag_cooldown = self.drag_cooldown_ticks
            self.drag_fires += 1
        elif (
            behaviour == "talk"
            and self.prev_behaviour != "talk"
            and not ritual_active
            and self.talk_cooldown <= 0
        ):
            fired = "talk"
            self.talk_cooldown = self.talk_cooldown_ticks
            self.talk_fires += 1

        self.prev_behaviour = behaviour
        self.prev_dragging = dragging
        return fired

    def reset(self) -> None:
        """换角色时清掉冷却，免得新角色继承旧角色的"刚说过话"。"""
        self.drag_cooldown = 0
        self.talk_cooldown = 0
        self.ritual_cooldown = 0
        self.poke_cooldown = 0
        self.pending = None
        self.prev_behaviour = "roaming"
        self.prev_dragging = False

    def to_dict(self) -> dict:
        return {
            "drag_cooldown": self.drag_cooldown,
            "talk_cooldown": self.talk_cooldown,
            "ritual_cooldown": self.ritual_cooldown,
            "poke_cooldown": self.poke_cooldown,
            "pending": self.pending,
            "drag_fires": self.drag_fires,
            "talk_fires": self.talk_fires,
            "ritual_fires": self.ritual_fires,
            "poke_fires": self.poke_fires,
            "cooldown_ticks": {
                "drag": self.drag_cooldown_ticks,
                "talk": self.talk_cooldown_ticks,
                "ritual": self.ritual_cooldown_ticks,
                "poke": self.poke_cooldown_ticks,
            },
        }
