"""Simulation root: everything the renderer needs, in game pixels.

Tick order mirrors ``Oracle.Update``:

1. behaviour picks a Bezier goal and a gaze point,
2. the arm drives the two body chunks (clamped impulses, no gravity),
3. the creature integrates its head/hands/feet,
4. the base decides whether to slide along the rail (hysteresis + 0.12/0.45°/tick),
5. ornaments (cord, gown, halo) follow.

The renderer interpolates between the previous and current tick with ``alpha``,
exactly like the game's ``timeStacker``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, degrees, hypot, radians, sin

from . import constants as C
from .temper import KillRitual, Temper
from .arm import ArmChain
from .behavior import BehaviourSelector, Wander
from .cord import Cord
from .creature import Creature
from .ornament import Gown, Halo
from .rail import RailPath, move_towards_angle


def mix_scalar(a: float, b: float, alpha: float) -> float:
    """标量插值（``RenderState.lerp`` 里混相邻两个 tick 的快照用）。"""
    return a + (b - a) * alpha


@dataclass
class RenderState:
    base_point: tuple[float, float]
    base_dir: tuple[float, float]
    frame_pos: float
    base_moving: bool
    joints: list[tuple[float, float]]
    cog_rot: list[float]
    body0: tuple[float, float]
    body1: tuple[float, float]
    head: tuple[float, float]
    hands: list[tuple[float, float]]
    feet: list[tuple[float, float]]
    look_dir: tuple[float, float]
    cord: list[tuple[float, float]]
    filaments: list[list[tuple[float, float]]]
    filament_colours: list[str]
    gown: list[list[tuple[float, float]]]
    gown_divs: int
    # 光环：快照只存**原始标量**（expand/push/white/旋转/每 bit 填充度），
    # 半径、环厚、bit 尺寸都在渲染时折算——源码也是这么做的（`Lerp(lastX, X, ts)`）。
    # `interpolate(alpha)` 混的是相邻两个 tick 的快照，等价于源码的 last/cur。
    halo_center: tuple[float, float]
    halo_expand: float
    halo_push: float
    halo_white: float
    halo_rot: list[float]
    halo_fill: list[float]
    # 20 根电光：(锚点 stuck_at, 控制柄 handle, 当前亮度 lightUp)
    halo_sparks: list[tuple[tuple[float, float], tuple[float, float], float]]
    cursor: tuple[float, float] | None
    behaviour: str
    time: float
    ticks: int
    # 腔室矩形（仿真像素）。渲染层拿它把活塞截到腔室里（游戏里墙紧贴轨道，活塞压在
    # 墙前看不出问题；桌面上屏幕外什么都没有，被窗口裁掉就是硬切口）。肘点**不**夹：
    # 2026-10-27 的翻肘护栏会让肘点单 tick 跳 ~196 px，已整个拆掉。
    bounds: tuple[float, float, float, float] | None = None

    def lerp(self, other: "RenderState", alpha: float) -> "RenderState":
        if alpha <= 0.0:
            return self
        if alpha >= 1.0:
            return other

        def mix(a, b):
            return (a[0] + (b[0] - a[0]) * alpha, a[1] + (b[1] - a[1]) * alpha)

        def mix_list(a, b):
            if len(a) != len(b):
                return a
            return [mix(p, q) for p, q in zip(a, b)]

        gown = [mix_list(row_a, row_b) for row_a, row_b in zip(self.gown, other.gown)]
        filaments = [
            mix_list(a, b) for a, b in zip(self.filaments, other.filaments)
        ]
        return RenderState(
            base_point=mix(self.base_point, other.base_point),
            base_dir=mix(self.base_dir, other.base_dir),
            frame_pos=self.frame_pos + (other.frame_pos - self.frame_pos) * alpha,
            base_moving=other.base_moving,
            joints=mix_list(self.joints, other.joints),
            cog_rot=[a + (b - a) * alpha for a, b in zip(self.cog_rot, other.cog_rot)],
            body0=mix(self.body0, other.body0),
            body1=mix(self.body1, other.body1),
            head=mix(self.head, other.head),
            hands=mix_list(self.hands, other.hands),
            feet=mix_list(self.feet, other.feet),
            look_dir=mix(self.look_dir, other.look_dir),
            cord=mix_list(self.cord, other.cord),
            filaments=filaments,
            filament_colours=self.filament_colours,
            gown=gown,
            gown_divs=self.gown_divs,
            halo_center=mix(self.halo_center, other.halo_center),
            halo_expand=mix_scalar(self.halo_expand, other.halo_expand, alpha),
            halo_push=mix_scalar(self.halo_push, other.halo_push, alpha),
            halo_white=mix_scalar(self.halo_white, other.halo_white, alpha),
            halo_rot=[mix_scalar(a, b, alpha) for a, b in zip(self.halo_rot, other.halo_rot)],
            halo_fill=[mix_scalar(a, b, alpha) for a, b in zip(self.halo_fill, other.halo_fill)],
            halo_sparks=[
                (a[0], a[1], mix_scalar(a[2], b[2], alpha))
                for a, b in zip(self.halo_sparks, other.halo_sparks)
            ],
            cursor=other.cursor,
            behaviour=other.behaviour,
            time=other.time,
            ticks=other.ticks,
            bounds=other.bounds,
        )


class SimulationState:
    WARMUP_TICKS = 320

    def __init__(self, bounds: tuple[float, float, float, float], inset: float = 26.0,
                 seed: int = 1234, warmup: int | None = None):
        self.inset = inset
        self._bounds = bounds
        self.rail = RailPath(self._inset())
        self.wander = Wander(self._inset(), seed=seed)
        self.wander.clamp = self._clamp_destination
        # Destinations are pulled into the arm's reach of their nearest rail
        # station, so a point on the far side of a wide screen does not leave the
        # body leaning on the reach limit for the whole hop.

        # ``baseIdeal`` is a ROOM POINT -- the puppet's own position -- which is
        # then projected onto the rail. ``start/stop_threshold`` are the source's
        # 350/50 px hysteresis scaled to this chamber (see constants).
        self.base_ideal = self.wander.current
        self.body_target: tuple[float, float] | None = None
        self.drag_mode: str | None = None
        self.drag_offset: tuple[float, float] = (0.0, 0.0)
        self.drag_cooldown: int = 0
        # 最近几个 tick 的拖拽点，松手时用来算"甩"的方向与速度
        self._drag_samples: list[tuple[int, tuple[float, float]]] = []
        # 心情与"杀鼠标"仪式（docs/PLAN-kill.md）。scale 由 PetWindow 注入，
        # 用来把"仿真 px/tick"换成"屏幕 px/s"——四档尺寸下到闸时间才一致。
        self.temper = Temper()
        self.kill = KillRitual()
        self.scale = 1.5
        self._drag_prev_point: tuple[float, float] | None = None
        # 松手后的"甩"：自己积分一小段，不依赖 wander 的贝塞尔（那条路会把目标夹回臂展内）
        self.fling_vel: tuple[float, float] = (0.0, 0.0)
        self.fling_ticks: int = 0
        self.rng = __import__("random").Random(seed)
        self.drag_point: tuple[float, float] | None = None
        self._refresh_thresholds()
        self.frame_pos = self.rail.project(self.wander.current)
        self.base_moving = False
        self.consistent_base_pos = 0
        self._dest_version = self.wander.version
        self._last_chunk0 = self.wander.current
        self.last_body_speed = 0.0
        self.last_goal = self.wander.current

        base_pose = self.rail.pose(self.frame_pos)
        self.arm = ArmChain(base_pose.point)
        self.creature = Creature(self.wander.current)
        self.cord = Cord(self._inset(), self.creature.chunk0)
        self.gown = Gown(self.creature.chunk0, self.creature.body_dir)
        self.halo = Halo(self._inset(), seed=seed)
        self.halo.seed_connections()

        self.selector = BehaviourSelector()
        self.behaviour = "roaming"
        self.investigate_angle = 0.0
        self.investigate_speed = C.INVESTIGATE_ANGLE_SPEED_MIN
        self._talk_point: tuple[float, float] | None = None
        self._talk_anchor: tuple[float, float] | None = None
        self.cursor: tuple[float, float] | None = None
        self.time = 0.0
        self.ticks = 0
        self._prev: RenderState | None = None
        self._cur = self._compose()

        # 预跑 320 tick（8 s）而不是 120：机械臂刚建好时所有关节叠在基点，前 ~180 tick
        # 要把链条拉开、把姿态摆稳，这段过渡里画出来的臂会擦出腔室（逐 tick 实测：
        # seed 1234 的 62 个出界 tick 全在 tick 0–61，最大 6 px，tick>=200 一个都没有，
        # 见 `_work/bleed_per_tick.py`）。构造期跑完，窗口第一帧就已经稳了。
        # 代价：约 320 × 4 ms ≈ 1.3 s 启动时间 —— 所以**切尺寸时不能同步跑**：
        # ``warmup=0`` 先建好，再让 ``PetWindow`` 在接下来几帧里分帧补 warmup
        # （腔室越大单 tick 越贵：2845×1520 的构造是 11.7 s，一步都不能卡在 UI 线程里）。
        budget = self.WARMUP_TICKS if warmup is None else max(0, int(warmup))
        for _ in range(budget):
            self._advance()

    # -- geometry ----------------------------------------------------------
    def _inset(self) -> tuple[float, float, float, float]:
        x, y, w, h = self._bounds
        inset = self.inset
        return (x + inset, y + inset, max(40.0, w - 2 * inset), max(40.0, h - 2 * inset))

    def _refresh_thresholds(self) -> None:
        # oracle.cs:327 hard-codes 350 / 50 px for a 1400 px wide room; here they
        # are ratios of the INSET chamber diagonal (724x363 -> 810 on a 776x415
        # chamber, i.e. 243 / 29 px; the outer diagonal 882 would give 264 / 32).
        #
        # The start threshold is CAPPED by the destination clamp radius, and the
        # reason is structural: ``Wander.set_destination`` pulls every destination
        # inside ``DESTINATION_REACH_RATIO * ARM_LENGTHS_TOTAL`` (353 px), so a
        # start threshold near or above that radius is crossed only by luck. At
        # this machine's scale the uncapped value is 0.30 * 1220 = 366 px against
        # that 353 px radius, and the carriage measured 2 % moving ticks / 4 px/s
        # / 53 % x coverage, versus 31 % / 42 px/s / 73 % in the documented
        # 776x415 chamber, where 243 px < 353 px does not conflict. (It is not
        # literally zero: the body overshoots the destination clamp, so the old
        # code moved on 2 % of ticks and reached 383 px from the carriage once.)
        _, _, w, h = self._inset()
        span = hypot(w, h)
        reach_radius = C.DESTINATION_REACH_RATIO * C.ARM_LENGTHS_TOTAL
        self.start_threshold = min(
            C.BASE_MOVE_START_RATIO * span,
            C.BASE_MOVE_START_REACH_RATIO * reach_radius,
        )
        self.stop_threshold = C.BASE_MOVE_STOP_RATIO * span

    def resize(self, bounds: tuple[float, float, float, float]) -> None:
        self._bounds = bounds
        inner = self._inset()
        self.rail = RailPath(inner)
        self.wander.bounds = inner
        self.halo.resize(inner)
        self._refresh_thresholds()
        self.frame_pos = self.rail.project(self.creature.chunk0)
        self._cur = self._compose()

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """The chamber in SIMULATION pixels.

        ``PetWindow`` builds it from ``QScreen.availableGeometry() / config.scale``
        -- the WORKING AREA, not the whole screen (this machine: 1707x912 / 1.5 =
        1138x608; the screen itself is 1707x960 and the 48 px taskbar strip is
        excluded). Everything the simulation stores -- body positions,
        destinations, and the cursor from ``platform/pet_window.py`` -- lives in
        this frame.
        """
        return self._bounds

    # -- per tick ----------------------------------------------------------
    # -- external control (future drag & "move to point" features) ----------
    def set_body_target(self, point: tuple[float, float] | None) -> None:
        """Ask the puppet to travel to a coordinate (None resumes wandering)."""
        self.body_target = point
        self.wander.follow_target = point
        if point is not None:
            self.wander.set_destination(point)

    def begin_drag(self, mode: str = "body", point: tuple[float, float] | None = None) -> bool:
        """开始拖拽。``point`` 是按下时光标的仿真坐标，用来记抓取偏移（人偶不会跳到光标下）。

        返回 False 表示当前不能拖（冷却中）。``mode`` 只区分 "body" / "base"，
        抓头/抓躯干在物理上是同一件事：拖 ``chunk0``（头被 _weld 焊在 chunk0 上方 8 px）。
        """
        if self.drag_cooldown > 0:
            return False
        # 仪式与冷却期内不许拖拽（他真要动手的时候你拽不动他）；锁用**独立字段**，
        # 不能复用 drag_cooldown —— 那条被 tests/test_drag.py 钉死在 13 tick 后必须能再抓。
        if self.kill.blocked:
            return False
        self.drag_mode = mode
        self.drag_point = point
        if point is not None:
            self.drag_offset = (
                self.creature.chunk0[0] - point[0],
                self.creature.chunk0[1] - point[1],
            )
        else:
            self.drag_offset = (0.0, 0.0)
        return True

    def drag_to(self, point: tuple[float, float] | None) -> None:
        self.drag_point = point
        if point is not None:
            self._drag_samples.append((self.ticks, point))
            if len(self._drag_samples) > 5:
                del self._drag_samples[0]

    def end_drag(self, fling: bool = True) -> None:
        if self.drag_mode is None:
            return
        self.drag_mode = None
        self.drag_point = None
        self.drag_cooldown = C.DRAG_COOLDOWN_TICKS
        # 松手：顺着**最后几个 tick 的拖动方向**再走一段（这就是用户要的"惯性"）。
        # 不能靠速度：creature.step 每 tick 把 vel *= 0.4*0.99，任何初速 2 tick 就没了
        # （实测 30 px/tick 只滑 ~50 px 且方向不可控）→ 直接给一个目的地更可控。
        chunk0 = self.creature.chunk0
        self.fling_vel = (0.0, 0.0)
        self.fling_ticks = 0
        if fling and len(self._drag_samples) >= 2:
            first_tick, first_point = self._drag_samples[0]
            last_tick, last_point = self._drag_samples[-1]
            spans = max(1, last_tick - first_tick)
            vx = (last_point[0] - first_point[0]) / spans
            vy = (last_point[1] - first_point[1]) / spans
            speed = hypot(vx, vy)
            if speed > 0.6:                       # 慢拖 = 松手放下，不甩
                cap = C.FLING_MAX_SPEED
                scale = min(1.0, cap / speed) * C.FLING_GAIN
                self.fling_vel = (vx * scale, vy * scale)
                self.fling_ticks = C.FLING_TICKS
                # 甩出去也算"被他记一笔"：按屏幕 px/s 加一点心情惩罚
                self.temper.drain_fling(speed * self.scale * C.GAME_HZ)
        self._drag_samples.clear()
        self.wander.current = chunk0
        self.wander.next = chunk0
        self.wander.progress = 1.0
        self.wander.last_handle = (0.0, 0.0)
        self.wander.next_handle = (0.0, 0.0)
        self.wander.follow_target = None
        self.wander.look_point = (chunk0[0], chunk0[1] - 40.0)

        self.wander.version += 1
        self._dest_version = self.wander.version
        self.consistent_base_pos = 0
        self._last_chunk0 = chunk0
        self.body_target = None
        # 光标统计也复位：否则松手第一 tick 的"速度"= 整个拖动距离，gate 关掉，
        # 立刻触发一次抽签（AGENTS §6.2 修过同类缺陷）。
        self.selector._last_cursor = self.cursor
        self.selector.still_ticks = 0

    @property
    def dragging(self) -> bool:
        return self.drag_mode is not None and self.drag_point is not None

    def _apply_control(self) -> None:
        """外部控制的旧入口（v0 的脚手架）。

        现在拖拽完全由 ``_advance`` 里的 ``drag_mode`` 分支处理：那里要用
        `project_near`（`project` 在腔室对角线上不连续，会把底座瞬移半圈）、
        要夹臂展、要盖 `get_to`。这里**不能再动 frame_pos/chunk0**，
        否则会和新分支打架（实测：这里用 `project` 把 frame_pos 从 1.332 一步
        跳到 0.792，48.7° 的瞬移就是这么来的）。
        """
        return
    def _translate_creature(self, delta: tuple[float, float]) -> None:
        c = self.creature
        c.chunk0 = (c.chunk0[0] + delta[0], c.chunk0[1] + delta[1])
        c.chunk1 = (c.chunk1[0] + delta[0], c.chunk1[1] + delta[1])
        c.head = (c.head[0] + delta[0], c.head[1] + delta[1])
        c.hands = [(h[0] + delta[0], h[1] + delta[1]) for h in c.hands]
        c.feet = [(f[0] + delta[0], f[1] + delta[1]) for f in c.feet]

    def _tick_kill(self) -> None:
        """仪式的一 tick：推进状态机、让身体原地站住、照常更新饰物与快照。

        ``ss.cs`` 里这段没有身体动作（他站着盯着你）；桌面版也让他站住，
        但仍要跑 ``creature.step``（否则身体会冻在"刚被拖开的姿势"）与
        ``_drag_base_and_ornaments``（底座/臂/缆线/袍/光环），最后刷新快照。
        """
        cursor = self.cursor
        was_active = self.kill.active
        event = self.kill.step(cursor, C.KILL_LOCK_SCREEN_PX / max(0.2, float(self.scale)))
        if was_active and not self.kill.active and event is None:
            # 蓄能失败（光标跑开）：把心情抬到 -0.30，并进入短冷却，避免下一 tick 立刻重入
            self.temper.value = C.TEMPER_AFTER_ABORT
        self.kill.tick_sparks()
        if event == "flash":
            self.temper.value = min(0.0, C.TEMPER_AFTER_KILL)
        if self.kill.phase == "cooldown":
            self.temper.tick(cooldown=True)
        elif not self.kill.active:
            self.temper.tick()
        get_to = self.creature.chunk0
        look_point = self.creature.head
        self._apply_control_noop()
        self.creature.step(get_to, self.creature.body_dir, look_point)
        self._drag_base_and_ornaments()
        self._prev = self._cur
        self._cur = self._compose()

    def _apply_control_noop(self) -> None:
        """占位：保留调用点（旧 ``_apply_control`` 已经是空实现）。"""
        return

    def _drag_base_and_ornaments(self) -> None:
        """拖拽分支的尾巴：底座跟随 + 机械臂 + 缆线/袍/光环（与正常路径同一套）。"""
        self.base_ideal = self.creature.chunk0
        ideal_f = self.rail.project_near(self.base_ideal, self.frame_pos)
        pose = self.rail.pose(self.frame_pos)
        gap = C.dist(pose.point, self.creature.chunk0)
        # 拖拽时底座必须**按缺口比例追**：常规跟随是迟滞 + 0.12/0.45 °/tick
        # （≈ 1–4 px/tick），而鼠标一甩就是上千 px/s → 人偶瞬间被拖出臂长。
        # 这里改成"缺口越大走得越快"，上限 DRAG_BASE_FOLLOW_DEG（≈55 px/tick）。
        # 物理直觉：先把臂**拉直**，快满时才让底座追。所以有一个死区
        # （DRAG_BASE_START_RATIO × 臂长），过界后速度随"超出量"平滑上升，
        # 到 DRAG_BASE_FULL_RATIO 时给满速。之前 0.45 就起步，结果底座和人偶
        # 一起平移、臂永远拉不直，用户看着像"底座在拖人偶"。
        arm = sum(C.ARM_LENGTHS)
        keep = arm * C.DRAG_BASE_START_RATIO
        full = arm * C.DRAG_BASE_FULL_RATIO
        if gap > keep:
            # 用户口径：**先按正常漫游的快速档起步**（BASE_SPEED_FAST = 0.45°/tick），
            # 随着臂越来越直再平滑升到满速。这样"看起来还有余量就突然冲刺"不会发生。
            ratio = min(1.0, (gap - keep) / max(1.0, full - keep))
            speed = C.BASE_SPEED_FAST + (C.DRAG_BASE_FOLLOW_DEG - C.BASE_SPEED_FAST) * ratio
            self.frame_pos = move_towards_angle(
                self.frame_pos * 90.0, ideal_f * 90.0, speed
            ) / 90.0 % 4.0
            self.base_moving = True
            pose = self.rail.pose(self.frame_pos)
        else:
            self.base_moving = False
        self.arm.update(
            pose.point, self.creature.chunk0, self.creature.chunk1,
            self.creature.body_dir, False, bounds=self._inset(),
        )
        # 拖拽/甩出期间机械臂要**跟手**（软约束会落后上百 px，见 snap_to 的注释）
        self.arm.snap_to(pose.point, self.creature.chunk0)
        self.cord.step(self.arm.joints[1].pos, self.arm.elbow_pos(1),
                       self.creature.chunk0, self.creature.head, self.creature.look_dir)
        self.gown.step(self.creature.chunk0, self.creature.body_dir, self.creature.chunk1)
        self.halo.step(self._halo_center(), self.creature.body_dir, self.ticks)

    def _advance(self) -> None:
        self.ticks += 1
        if self.drag_cooldown > 0:
            self.drag_cooldown -= 1
        self.time = self.ticks / C.GAME_HZ

        # ---- 杀鼠标仪式：最高优先级，整块早退 -------------------------------
        # 三件必须做的事（审计 P0-2）：身体照常积分、饰物/臂照常更新、**快照照常刷新**。
        # 少最后一行，RenderState 永远不更新 → 白光定格在进入仪式的那一帧。
        if self.kill.active:
            self._tick_kill()
            return

        # ---- 心情：拖拽中按"真位移 + 屏幕速度"扣；破闸立刻强制打断 ----------
        if self.dragging and self.drag_point is not None:
            prev = self._drag_prev_point
            self._drag_prev_point = self.drag_point
            moved = prev is not None and C.dist(prev, self.drag_point) > 0.5
            speed = 0.0 if prev is None else C.dist(prev, self.drag_point)
            self.temper.drain_drag(speed * self.scale * C.GAME_HZ, moved)
        else:
            self._drag_prev_point = None
        if self.temper.angry and not self.kill.blocked:
            # 强制打断：清掉拖拽与还没执行的甩出（否则冷却结束后人偶会莫名弹一下）
            self.end_drag(fling=False)
            # 锚点：光标优先；光标不在屏上（None）就用拖拽点/头顶，
            # 否则 anchor 为 None 时永远量不出"跑开"，abort 分支形同虚设（实测踩过）
            anchor = self.cursor or self.drag_point or self.creature.head
            self.kill.begin(anchor, self.ticks)
            self.temper.value = C.TEMPER_AFTER_KILL
            self._tick_kill()
            return

        self._apply_control()
        pose_now = self.rail.pose(self.frame_pos)
        # ``consistentBasePosCounter`` gate: the source only asks for a new
        # destination once the assembly is settled, so the same test decides
        # whether the wander may start its next drift.
        # ``settled`` decides when the wander may pick a new destination, and the
        # old test -- "is the body axis vertical" -- was a trap: a body that is
        # travelling ALWAYS leans (chunk 0 gets 6.2 of impulse, chunk 1 only 3.2),
        # so it was never settled, the wander re-targeted on the WANDER_HOLD_CAP
        # timeout instead, and the puppet travelled forever in 160-tick bursts.
        # The question the gate actually asks is "has the assembly come to rest",
        # so measure that: tick-to-tick body speed.
        body_speed = C.dist(self._last_chunk0, self.creature.chunk0)
        self._last_chunk0 = self.creature.chunk0
        self.last_body_speed = body_speed
        settled = body_speed < C.WANDER_SETTLED_SPEED
        self.wander.update(self.cursor, settled=settled)
        get_to = self.wander.get_to()
        get_to_dir = self.wander.get_to_dir()

        # ---- drag (P0)：拖拽时不跑行为层，目标直接就是光标 ------------------
        # 为什么必须覆盖 get_to 而不是只改坐标：creature.step 每 tick 都会给
        # vel0/vel1 加 6.2/3.2 px 冲量、9 px 连杆还会把误差各退一半（见
        # docs/PLAN-drag.md §5 与审计 P0-1），只设坐标活不过一个 tick。
        # ---- 松手后的"甩" --------------------------------------------------
        # 为什么不走 wander：`set_destination` 会把目标夹进"底座为中心的臂展圆"，而甩的
        # 方向正是背离底座 → 夹完就变成往回走（实测松手 10 tick 仍 -13 px）。这里自己
        # 积分十几 tick 的刚体位移，方向就是拖动方向，衰减到停为止。
        if self.fling_ticks > 0 and not self.dragging:
            base_now = self.rail.pose(self.frame_pos).point
            wanted = (self.creature.chunk0[0] + self.fling_vel[0],
                      self.creature.chunk0[1] + self.fling_vel[1])
            allowed = self._reachable(base_now, wanted)     # 甩也不能甩出臂展
            self._translate_creature((allowed[0] - self.creature.chunk0[0],
                                      allowed[1] - self.creature.chunk0[1]))
            self.fling_vel = (self.fling_vel[0] * C.FLING_DECAY,
                              self.fling_vel[1] * C.FLING_DECAY)
            self.fling_ticks -= 1
            self.creature.vel0 = (0.0, 0.0)
            self.creature.vel1 = (0.0, 0.0)
            self.creature.head_vel = (0.0, 0.0)
            self.creature.hand_vel = [(0.0, 0.0), (0.0, 0.0)]
            self.creature.foot_vel = [(0.0, 0.0), (0.0, 0.0)]
            self._drag_base_and_ornaments()
            self._prev = self._cur
            self._cur = self._compose()
            return

        if self.dragging and self.drag_mode != "base":
            target = self._clamp_to_room(
                (self.drag_point[0] + self.drag_offset[0],
                 self.drag_point[1] + self.drag_offset[1])
            )
            # 人偶不能被拖出机械臂的可达范围（老问题，拖拽时最明显：臂被拉成一条线、
            # 人偶还在往前飞）。夹在"底座 + ARM_REACH_RATIO×臂长"的圆内，
            # 光标继续往外时人偶会贴着这个圆滑走，底座再慢慢追上来。
            target = self._reachable(self.rail.pose(self.frame_pos).point, target)
            get_to = target
            get_to_dir = self.creature.body_dir if self.creature.body_dir != (0.0, 0.0) else (0.0, 1.0)
            look_point = (self.creature.head[0] + get_to_dir[0] * 40.0,
                          self.creature.head[1] + get_to_dir[1] * 40.0)
            self.last_goal = target
            self.creature.step(get_to, get_to_dir, look_point)
            # 刚体平移 chuck0+chunk1（连头/手/脚一起，保持相对姿态），让被抓点**精确**跟着光标：
            # 只靠冲量追的话，快速拖动会落后几十 px。
            delta = (target[0] - self.creature.chunk0[0], target[1] - self.creature.chunk0[1])
            self._translate_creature(delta)
            for attribute in ("vel0", "vel1", "head_vel"):
                setattr(self.creature, attribute, (0.0, 0.0))
            self.creature.hand_vel = [(0.0, 0.0), (0.0, 0.0)]
            self.creature.foot_vel = [(0.0, 0.0), (0.0, 0.0)]
            self._drag_base_and_ornaments()
            self._prev = self._cur
            self._cur = self._compose()
            return

        # ---- behaviour: roaming / investigate / talk ---------------------
        # The selector is OUR addition (see constants.py); the three poses are
        # the source's. ``get_to`` and the gaze are then re-derived per state.
        cursor_speed = self.selector.observe_cursor(self.cursor)
        self.behaviour = self.selector.choose(
            self.creature.chunk0, self.cursor, cursor_speed)
        if self.behaviour == "investigate" and self.cursor is not None:
            # ss.cs:4185-4188  angle inside +-70 deg, swept 0.4-0.8 deg/tick;
            # nextPos = player + DegToVec(angle) * 150.
            self.investigate_angle += self.investigate_speed
            if abs(self.investigate_angle) > C.INVESTIGATE_ANGLE_LIMIT:
                self.investigate_speed = -self.investigate_speed
                self.investigate_angle = max(-C.INVESTIGATE_ANGLE_LIMIT,
                                             min(C.INVESTIGATE_ANGLE_LIMIT,
                                                 self.investigate_angle))
            angle = radians(self.investigate_angle)
            offset = (sin(angle) * C.INVESTIGATE_RADIUS,
                      -cos(angle) * C.INVESTIGATE_RADIUS)
            get_to = (self.cursor[0] + offset[0], self.cursor[1] + offset[1])
        elif self.behaviour == "talk" and self.cursor is not None:
            # ss.cs:4278-4285  CommunicatePosScore parks the iterator at a fixed
            # distance from the player. Commit to a POINT (re-picked only if the
            # cursor wanders off) so the body can arrive and rest instead of
            # chasing a goal that slides every tick. The distance is the constant
            # ``C.TALK_DISTANCE`` = 76 sim px (three gown widths), NOT a fraction
            # of the chamber -- a standoff that grows with the monitor is how the
            # pet ended up talking from 236 px away.
            if (self._talk_point is None or self._talk_anchor is None
                    or C.dist(self.cursor, self._talk_anchor) > C.TALK_REPICK):
                direction = C.dir_vec(self.cursor, self.creature.chunk0)
                if direction == (0.0, 0.0):
                    direction = (0.0, 1.0)
                self._talk_point = (self.cursor[0] + direction[0] * C.TALK_DISTANCE,
                                    self.cursor[1] + direction[1] * C.TALK_DISTANCE)
                self._talk_anchor = self.cursor
            get_to = self._talk_point
        else:
            self._talk_point = None
            self._talk_anchor = None

        look_point = (self.cursor if self.behaviour != "roaming" and self.cursor is not None
                      else self.wander.look_point)

        # ---- base: the puppet leads, the carriage follows (oracle.cs:327-331) --
        # baseMoving = Distance(BasePos, baseIdeal) > (baseMoving ? 50f : 350f)
        #              && consistentBasePosCounter > 30;
        # if (baseMoving) framePos = MoveTowardsAngle(framePos*90, target*90, 1)/90;
        #
        # This is the design three documents describe (plan:22, HANDOFF:69,
        # motion-spec:1.2) and what an earlier version of this file did: the
        # carriage parks and only re-sites when the PUPPET has drifted past
        # ``start_threshold``. The version that replaced it scored 64 rail
        # stations against ``|dist(station, nextPos) - 200|``, which on a
        # 776x415 chamber can only ever resolve to the right/bottom edge -- that
        # is what pinned the pet to one corner (see AGENTS.md 6.1).
        # ---- P1：抓着滑车底座拖（直接操纵，整块跳过迟滞跟随）-------------
        # 迟滞那一套是"人偶走了、底座该不该动"的决策；手抓在底座上时这个决策没有
        # 意义，而且它会把 frame_pos 改回身体方向，和手对打。
        if self.dragging and self.drag_mode == "base" and self.drag_point is not None:
            # 目标必须用 project_near：project 在腔室四条对角平分线上不连续，
            # 拖过中心时会整段跳到邻边（rail.py:134-192）。
            target_f = self.rail.project_near(self.drag_point, self.frame_pos)
            delta_deg = ((target_f - self.frame_pos + 2.0) % 4.0 - 2.0) * 90.0
            # 限速：差得远按上限走，差得近按比例慢下来（贴着光标时不抖）
            speed = min(C.DRAG_BASE_DRAG_DEG, max(0.35, abs(delta_deg) * 0.35))
            self.frame_pos = move_towards_angle(
                self.frame_pos * 90.0, target_f * 90.0, speed
            ) / 90.0 % 4.0
            self.base_moving = True
            self.base_ideal = self.creature.chunk0
            ideal_f = target_f
        else:
            if self.wander.version != self._dest_version:
                self._dest_version = self.wander.version
                self.consistent_base_pos = 0
            self.consistent_base_pos += 1

            self.base_ideal = self.creature.chunk0
            ideal_f = self.rail.project_near(self.base_ideal, self.frame_pos)
            # The hysteresis must be measured in RAIL ARC LENGTH, not in a straight
            # line. The source compares Distance(BasePos, baseIdeal) in a 560x560
            # room where the chord is close to the arc; on a 776x415 chamber a 264 px
            # chord can be half a quadrant of travel (1190 px), and the carriage --
            # 1 degree per tick -- then needs 45-180 ticks to arrive while the puppet
            # keeps drifting. Measured with the chord version: base->body p95 421 px,
            # max 627. Arc length is what the carriage actually has to drive.
            base_point = self.rail.pose(self.frame_pos).point
            # ``Distance(BasePos, baseIdeal)`` -- the source's own chord measure.
            gap = C.dist(base_point, self.creature.chunk0)
            # Hard physical override. The arm is 570 px (300+150+90+30); once the
            # puppet is further than 90 % of that from the carriage the arm simply
            # cannot hold it, whatever the hysteresis and the 30-tick gate say. The
            # source never needs this because its room (560x560) is about one arm
            # long; a 776x415 chamber is wider, so the carriage has to be allowed to
            # break its own hysteresis to stay within reach.
            must_follow = gap > sum(C.ARM_LENGTHS) * C.BASE_FOLLOW_ARM_RATIO
            reach = self.stop_threshold if self.base_moving else self.start_threshold
            self.base_moving = (must_follow or gap > reach) and (
                must_follow or self.consistent_base_pos > C.BASE_STABLE_TICKS
            )
            if self.base_moving:
                # Two-tier speed (see constants.BASE_SPEED_*): slow while the body is
                # close, fast only when the carriage has most of a quadrant left to
                # drive. The source's literal 1 degree/tick is 249 px/s on its 560 px
                # rail and measured 135 px/s here, which is what the user saw as the
                # carriage racing the puppet.
                _, _, cw, ch = self._inset()
                remaining = abs((ideal_f - self.frame_pos + 2.0) % 4.0 - 2.0)
                ahead = remaining * 0.5 * (cw + ch)        # px of rail left to drive
                speed = (C.BASE_SPEED_FAST
                         if ahead > 0.5 * hypot(cw, ch) else C.BASE_SPEED_SLOW)
                self.frame_pos = move_towards_angle(
                    self.frame_pos * 90.0, ideal_f * 90.0, speed
                ) / 90.0
                self.frame_pos %= 4.0

        # ---- arm drives the body ----------------------------------------
        # ``OracleGetToPos`` (ss.cs:3122-3133) is ONE clamped goal, and the game
        # feeds it to BOTH the trunk and the arm:
        #     get => ClampVectorInRoom(floatyMovement && near(nextPos,50) ? nextPos : currentGetTo)
        # So the body chases the clamped point, not the raw Bezier. Clamping only
        # the arm (which is what this port used to do) lets the body outrun its own
        # limb: measured base->body up to 652 px against a 570 px arm, and the
        # trunk then trails flat. Two clamps, in this order:
        #   1. into the chamber   (ClampVectorInRoom, ss.cs:4298)
        #   2. inside the arm     (ARM_REACH_RATIO * 570 = 541 px from the base)
        # Because the carriage then follows the body, a full-screen destination is
        # still reachable -- it just takes two phases: the body pushes against the
        # reach limit, the carriage catches up, the limit slides out.
        pose = self.rail.pose(self.frame_pos)
        # The destination was already clamped when it was picked, so ``get_to`` is
        # reachable; only the room has the last word (ClampVectorInRoom).
        get_to = self._clamp_to_room(get_to)
        self.last_goal = get_to
        self.creature.step(get_to, get_to_dir, look_point)
        # 兜底：身体真的跑到臂展之外（拖拽残留、甩出、极端行为）时拉回边界。
        # 用 1.02 倍留一点余量，正常漫游碰不到这条分支。
        limit = sum(C.ARM_LENGTHS) * C.ARM_REACH_RATIO * 1.02
        if C.dist(pose.point, self.creature.chunk0) > limit:
            fixed = self._reachable(pose.point, self.creature.chunk0)
            self._translate_creature((fixed[0] - self.creature.chunk0[0],
                                      fixed[1] - self.creature.chunk0[1]))
        self.arm.update(
            pose.point, self.creature.chunk0, self.creature.chunk1,
            get_to_dir, self.base_moving, bounds=self._inset(),
        )

        # ---- ornaments ---------------------------------------------------
        # og.cs:930-931  SetStuckSegments hooks the cord on
        # Lerp(myJoint.pos, myJoint.ElbowPos(1f, next.pos), 0.4 + 0.07*i) -- the
        # ELBOW of joint 1, not joint 2's position. Passing the joint1 -> joint2
        # chord put the hook ~20 px away from the source's point.
        elbow_a = self.arm.joints[1].pos
        elbow_b = self.arm.elbow_pos(1)
        self.cord.step(elbow_a, elbow_b, self.creature.chunk0, self.creature.head, self.creature.look_dir)
        self.gown.step(self.creature.chunk0, self.creature.body_dir, self.creature.chunk1)
        halo_center = self._halo_center()
        self.halo.step(halo_center, self.creature.body_dir, self.ticks)

        # 冷却要在**正常路径**里走：仪式 active 时有专门的早退分支，
        # 冷却期不走那条分支，否则 cooldown 永不递减 → 永久抓不住（实测踩过）
        self.kill.tick_cooldown()
        self.temper.tick(cooldown=self.kill.cooldown > 0)
        self._prev = self._cur
        self._cur = self._compose()

    def _rail_gap(self, a: float, b: float) -> float:
        """Shorter arc length between two ``framePos`` values, in chamber px."""
        _, _, w, h = self._inset()
        per_quadrant = 2.0 * (w + h) / 4.0
        delta = (a - b) % 4.0
        if delta > 2.0:
            delta = 4.0 - delta
        return delta * per_quadrant

    def _clamp_destination(self, point: tuple[float, float]) -> tuple[float, float]:
        """Pull a freshly picked destination inside the arm's reach of the carriage.

        Called once per ``SetNewDestination``. ``_reachable`` does the same job for
        the arm every tick, but applying it to the body's GOAL every tick makes the
        goal slide with the carriage; a destination clamped at pick time is a fixed
        point the body can reach, stop on, and rest against.
        """
        root = self.rail.pose(self.frame_pos).point
        limit = C.ARM_LENGTHS_TOTAL * C.DESTINATION_REACH_RATIO
        distance = C.dist(root, point)
        if distance > limit and distance >= 1e-6:
            direction = C.dir_vec(root, point)
            point = (root[0] + direction[0] * limit, root[1] + direction[1] * limit)
        return self._clamp_to_room(point)

    def _clamp_to_room(self, point: tuple[float, float], margin: float = 10.0) -> tuple[float, float]:
        """``SSOracleBehavior.ClampVectorInRoom`` (ss.cs:4298-4305).

        ``result.x = Clamp(x, corner[0].x + 10, corner[1].x - 10)`` and likewise for
        y between corner 2 and corner 1 -- i.e. the inset rectangle grown in by 10 px.
        """
        x, y, w, h = self._inset()
        return (
            min(max(point[0], x + margin), x + w - margin),
            min(max(point[1], y + margin), y + h - margin),
        )

    def _reachable(self, root: tuple[float, float], target: tuple[float, float]) -> tuple[float, float]:
        limit = sum(C.ARM_LENGTHS) * C.ARM_REACH_RATIO
        distance = C.dist(root, target)
        if distance <= limit or distance < 1e-6:
            return target
        direction = C.dir_vec(root, target)
        return (root[0] + direction[0] * limit, root[1] + direction[1] * limit)

    def _filaments(self) -> list[list[tuple[float, float]]]:
        """The 14 strand node lists, straight out of ``Cord``.

        These used to be an analytic sine bow spanning the tail -> head gap. That
        was wrong twice over: the drawn length was the GAP (60-95 px) while the
        source gives each strand 50-200 px of free rope, and a curve has no
        inertia, so it could never sag, overshoot or cross itself. The physics
        now lives in ``Cord._step_filaments`` (``og.cs:873-913``); this only
        hands the nodes to the renderer.
        """
        return self.cord.filaments()

    def _halo_center(self) -> tuple[float, float]:
        axis = C.dir_vec(self.creature.chunk0, self.creature.head)
        return (
            self.creature.head[0] + axis[0] * C.HALO_HEAD_OFFSET,
            self.creature.head[1] + axis[1] * C.HALO_HEAD_OFFSET,
        )

    def step(self, dt: float) -> None:
        if dt <= 0.0:
            return
        self._advance()

    # -- snapshot ----------------------------------------------------------
    def _compose(self) -> RenderState:
        pose = self.rail.pose(self.frame_pos)
        center = self._halo_center()
        return RenderState(
            base_point=pose.point,
            base_dir=pose.inward,
            frame_pos=self.frame_pos,
            base_moving=self.base_moving,
            joints=[j.pos for j in self.arm.joints],
            cog_rot=[j.cog_rot for j in self.arm.joints],
            body0=self.creature.chunk0,
            body1=self.creature.chunk1,
            head=self.creature.head,
            hands=list(self.creature.hands),
            feet=list(self.creature.feet),
            look_dir=self.creature.look_dir,
            cord=list(self.cord.nodes),
            filaments=self._filaments(),
            filament_colours=list(self.cord.filament_colours),
            gown=[list(row) for row in self.gown.pos],
            gown_divs=self.gown.divs,
            halo_center=center,
            halo_expand=self.halo.expand,
            halo_push=self.halo.push,
            halo_white=self.halo.white,
            halo_rot=[self.halo.rotation(r) for r in range(self.halo.ROWS)],
            halo_fill=self.halo.bit_fills(),
            halo_sparks=[
                (c.stuck_at, c.handle, c.light_up) for c in self.halo.connections
            ],
            cursor=self.cursor,
            behaviour=self.behaviour,
            time=self.time,
            ticks=self.ticks,
            bounds=self._bounds,
        )

    def interpolate(self, alpha: float) -> RenderState:
        if self._prev is None:
            return self._cur
        return self._prev.lerp(self._cur, max(0.0, min(1.0, alpha)))

    # -- input -------------------------------------------------------------
    def set_cursor(self, pos: tuple[float, float] | None) -> None:
        self.cursor = pos

    # -- diagnostics -------------------------------------------------------
    def status(self) -> dict:
        """Everything a human needs to see why the pet is doing what it is doing.

        Cheap enough to call at 12 Hz from the developer panel; also used by the
        tools. Keep the keys stable -- ``tools/devpanel.py`` formats them.
        """
        ok_arm, arm_stretch, _ = self.arm.validate()
        cord_all, cord_free = self.cord.stretch()
        base_point = self.rail.pose(self.frame_pos).point
        body = self.creature.chunk0
        gap = C.dist(base_point, body)
        reach = sum(C.ARM_LENGTHS)
        down = self.creature.body_dir
        tilt = abs(180.0 - abs(degrees(atan2(down[0], down[1]))))
        worst_filament = max(
            (drawn / max(1e-9, free)
             for drawn, free in (self.cord.filament_rope(i)
                                 for i in range(len(self.cord.small)))),
            default=0.0,
        )
        return {
            "ticks": self.ticks,
            "time": round(self.time, 2),
            "behaviour": self.behaviour,
            "drag": self.drag_mode,
            "temper": round(self.temper.value, 4),
            "kill": {"phase": self.kill.phase, "fac": round(self.kill.kill_fac, 4),
                     "cooldown": self.kill.cooldown},
            # Draw weights: what the last/next draw was choosing between. This is
            # the "probability" the selector talks about -- an argmax has no such
            # thing, which is why the panel used to show a "best" that lost.
            "weights": {k: round(v, 3) for k, v in self.selector.weights.items()},
            "eligible": list(self.selector.eligible),
            # The mood the last draw was forbidden to pick (the hand-over rule).
            # Without it, "weight 0.00" means two different things.
            "excluded": self.selector.excluded,
            "draws": self.selector.draws,
            "talk_cooldown": self.selector.talk_cooldown,
            # How long the current state has been held, against the segment length
            # it will serve. Without this the panel cannot say why a state that
            # "should" have been replaced is still running.
            "held_ticks": self.selector.ticks,
            "min_hold": C.BEHAVIOUR_MIN_TICKS,
            "segment": C.BEHAVIOUR_SEGMENT_TICKS[self.behaviour],
            # Freshness per behaviour. Without it a panel cannot explain why the
            # pet dropped (or ignored) a behaviour: a drained mood is invisible in
            # the weights alone.
            "freshness": {k: round(v, 2) for k, v in self.selector.freshness.items()},
            "cursor": self.cursor,
            "still_ticks": self.selector.still_ticks,
            "frame_pos": round(self.frame_pos, 3),
            "station_edge": ("top", "right", "bottom", "left")[int(self.frame_pos) % 4],
            "base_moving": self.base_moving,
            "start_threshold": round(self.start_threshold, 1),
            "stop_threshold": round(self.stop_threshold, 1),
            "base_body": round(gap, 1),
            "reach": reach,
            "reach_ratio": round(gap / reach, 3),
            "body_speed": round(self.last_body_speed, 2),
            "tilt_deg": round(tilt, 1),
            # The goal actually being chased. During investigate/talk this is NOT
            # ``wander.next`` -- showing the wander's stale destination made the
            # panel read "dist 297 progress 1.00" while the pet was circling the
            # cursor, which is exactly the kind of lie a dev tool must not tell.
            "goal": self.last_goal,
            "goal_distance": round(C.dist(body, self.last_goal), 1),
            "dest": self.wander.next,
            "progress": (round(self.wander.progress, 3)
                         if self.behaviour == "roaming" else None),
            "arm_stretch": round(arm_stretch, 2),
            "cord_nodes": len(self.cord.nodes),
            "cord_stretch": round(cord_all, 3),
            "cord_stretch_free": round(cord_free, 3),
            "filament_worst": round(worst_filament, 2),
            "finite": self.cord.finite(),
            "arm_ok": ok_arm,
        }
