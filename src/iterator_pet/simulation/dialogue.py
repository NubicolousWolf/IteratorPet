"""一句台词的显示状态：逐字 → 停留 → 收起。纯数据，不碰 Qt、不碰渲染。

照 ``HUD.DialogBox`` 的两点行为：
* 弹出/收起是每帧逼近 **1/6**（``Update`` 里的 ``sizeFac``，约 6 帧）；
* 一句话有"停留时间"，到点才收起。

与源码的差别（**设计增补**）：游戏是 `DialogBox` 一行行 push + 打字机由 HUD 控制，
我们只要"一句就够"（用户划定：一次只说一句、不写角色名）。

审计 A2/A5 的要求也在这里落地：
* ``cancel()`` 是硬收起（暂停 / 换角色 / 托盘关掉对话时调用），不播完、不补播；
* ``text`` 是**字符串副本**，玩家热重载改掉池子不影响正在显示的这句。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import constants as C


@dataclass
class DialogueState:
    text: str = ""
    total: int = 0
    ticks: int = 0
    size_fac: float = 0.0          # 0=收起到没有，1=完全展开
    closing: bool = False
    hold: int = 0

    # -- 查询 ---------------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self.text) or self.size_fac > 0.01

    def visible_chars(self) -> int:
        """打字机进度：第一个字立刻出现，之后每 ``TYPE_TICKS_PER_CHAR`` 出一个。"""
        if not self.text:
            return 0
        per = max(1, int(C.DIALOGUE_TYPE_TICKS_PER_CHAR))
        return min(self.total, 1 + self.ticks // per)

    def visible_text(self) -> str:
        return self.text[: self.visible_chars()]

    def typing_done(self) -> bool:
        return self.visible_chars() >= self.total

    # -- 状态迁移 -----------------------------------------------------------
    def start(self, text: str | None) -> bool:
        """开始说一句。空串/None 直接返回 False（= 这次不说话）。"""
        if not text:
            return False
        self.text = str(text)                       # 文本副本（审计 A5）
        self.total = len(self.text)
        self.ticks = 0
        self.size_fac = 0.0
        self.closing = False
        self.hold = int(C.DIALOGUE_HOLD_BASE + C.DIALOGUE_HOLD_PER_CHAR * self.total)
        return True

    def cancel(self) -> None:
        """硬收起：暂停、换角色、关掉对话时用（审计 A2/A3）。"""
        self.text = ""
        self.total = 0
        self.ticks = 0
        self.size_fac = 0.0
        self.closing = False
        self.hold = 0

    # -- 每 tick ------------------------------------------------------------
    def step(self) -> None:
        if not self.text:
            if self.size_fac > 0.0:                 # 已经收起但还有残影
                self.size_fac = max(0.0, self.size_fac - 1.0 / C.DIALOGUE_FADE_TICKS)
            return
        self.ticks += 1
        if self.typing_done():
            if self.hold > 0:
                self.hold -= 1
            else:
                self.closing = True
        target = 0.0 if self.closing else 1.0
        self.size_fac += (target - self.size_fac) / C.DIALOGUE_FADE_TICKS
        if self.closing and self.size_fac <= 0.02:
            self.cancel()

    # -- 给面板/测试看 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "text": self.text,
            "visible": self.visible_text(),
            "closing": self.closing,
            "size": round(self.size_fac, 3),
            "hold": self.hold,
        }
