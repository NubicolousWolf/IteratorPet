"""两个对话事件（用户 2026-10-29 划定：**只有** talk 和 drag）。

形状照源码的 ``SpecialEvent(string eventName)``（`ss.cs:4335`）：事件是**字符串键名**，
由外部条件触发；事件本身不说话，由对话层决定说不说、说什么。

触发点（用户拍板）：
* ``drag``：``begin_drag()`` 成功的那一 tick（人偶**或**底座），同一次拖拽只触发一次；
  冷却 ``DIALOGUE_DRAG_COOLDOWN``（8 s）。
* ``talk``：``behaviour`` 变成 ``talk`` 的第一 tick；冷却复用 ``TALK_COOLDOWN_TICKS``（60 s）。

两者都**不**在挣脱/杀鼠标仪式期间触发（避免两套台词打架）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import constants as C


@dataclass
class DialogueEvents:
    drag_cooldown: int = 0
    talk_cooldown: int = 0
    prev_behaviour: str = "roaming"
    prev_dragging: bool = False
    drag_fires: int = 0
    talk_fires: int = 0

    def tick(self, behaviour: str, dragging: bool, ritual_active: bool = False) -> str | None:
        """推进一步，返回本 tick 触发的事件名（最多一个）或 None。"""
        if self.drag_cooldown > 0:
            self.drag_cooldown -= 1
        if self.talk_cooldown > 0:
            self.talk_cooldown -= 1

        fired: str | None = None
        started_drag = dragging and not self.prev_dragging
        if started_drag and not ritual_active and self.drag_cooldown <= 0:
            fired = "drag"
            self.drag_cooldown = C.DIALOGUE_DRAG_COOLDOWN
            self.drag_fires += 1
        elif (
            behaviour == "talk"
            and self.prev_behaviour != "talk"
            and not ritual_active
            and self.talk_cooldown <= 0
        ):
            fired = "talk"
            self.talk_cooldown = C.TALK_COOLDOWN_TICKS
            self.talk_fires += 1

        self.prev_behaviour = behaviour
        self.prev_dragging = dragging
        return fired

    def reset(self) -> None:
        """换角色时清掉冷却，免得新角色继承旧角色的"刚说过话"。"""
        self.drag_cooldown = 0
        self.talk_cooldown = 0
        self.prev_behaviour = "roaming"
        self.prev_dragging = False

    def to_dict(self) -> dict:
        return {
            "drag_cooldown": self.drag_cooldown,
            "talk_cooldown": self.talk_cooldown,
            "drag_fires": self.drag_fires,
            "talk_fires": self.talk_fires,
        }
