"""机械臂的绘制几何：肘点照源码算，且不许画到腔室外。

三条独立的合同：

1. **IK 公式**。`Custom.InverseKinematic` 的 IL（`tools/ildump` 导出）是
   `c = Clamp((d² + A² - B²)/(2·d·A), 0.2, 0.98); ang = Acos(c)·flip;
   return va + DegToVec(Aim(va→vc) + ang)·A`，`DegToVec=(sin,cos)`（游戏 +y 朝上）。
   这里在测试里**独立**实现一遍（游戏帧算完再镜像回仿真帧），不和被测代码共享任何一行
   ——2026-10-27 的第一版测试拿 `ArmChain.elbow_pos` 当参照，而它和渲染器犯同一个错号，
   等于自己证明自己（审计把渲染器改成源码符号后那条测试立刻红）。
2. **flip 不取负**。`C.inverse_kinematic` 已经按 y 镜像帧实现，同一个 flip 给出同一侧；
   取负会把肘点画到弦的另一侧（joint0 中位 34.4 px、最大 200 px 的偏差就是这么来的）。
3. **不出屏幕**。修对 IK 之后，肘点本身不再折出腔室（旧的 reach-clamp 在退化区会折到外面
   100 px），逐 tick 实测 12000 个 tick 里 0 次越界；唯一例外是刚构造时前 ~180 tick 的
   拉链过程，已由 `SimulationState` 的 320 tick 预跑盖住。
"""

from __future__ import annotations

from math import acos, atan2, cos, hypot, sin

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from iterator_pet.platform.fixed_loop import GAME_DT
from iterator_pet.rendering.renderer import draw_arm, drawn_elbow
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

ROOM = (0.0, 0.0, 1138.0, 608.0)   # 1707/1.5 x 912/1.5，本机腔室


# --------------------------------------------------------------------------
# 按 IL 独立实现的源码模型（游戏 +y 朝上的帧里算，再镜像回仿真帧）
# --------------------------------------------------------------------------
def source_elbow(start, target, upper, fore, flip):
    va = (start[0], -start[1])
    vc = (target[0], -target[1])
    d = hypot(vc[0] - va[0], vc[1] - va[1])
    c = (d * d + upper * upper - fore * fore) / (2.0 * d * upper)
    c = 0.2 if c < 0.2 else (0.98 if c > 0.98 else c)
    ang = acos(c) * flip
    aim = atan2(vc[0] - va[0], vc[1] - va[1])
    t = aim + ang
    return (va[0] + sin(t) * upper, va[1] + cos(t) * upper)   # 仍是游戏帧（+y 朝上）


def mirror(point):
    """y 镜像到仿真帧（只用来把 source_elbow 的返回值对齐到仿真坐标）。"""
    return (point[0], -point[1])


def _app():
    return QApplication.instance() or QApplication([])


def _corner_cursor(tick: int, room=ROOM):
    w, h = room[2], room[3]
    corners = ((30.0, 30.0), (w - 30.0, 30.0), (w - 30.0, h - 30.0), (30.0, h - 30.0))
    return corners[(tick // 80) % 4]


def _render(state, room=ROOM):
    _app()
    w, h = int(room[2]), int(room[3])
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_arm(painter, state)
    painter.end()
    return image


def _outside_px(image) -> int:
    """画面四边最外一圈里可见像素的个数（>0 就是画到腔室外了）。"""
    data = bytes(image.constBits())
    stride = image.bytesPerLine()
    w, h = image.width(), image.height()
    count = 0
    for y in range(h):
        row = data[y * stride:(y + 1) * stride]
        if y in (0, h - 1):
            count += sum(1 for x in range(w) if row[x * 4 + 3] > 8)
        else:
            if row[3] > 8:
                count += 1
            if row[(w - 1) * 4 + 3] > 8:
                count += 1
    return count


# --------------------------------------------------------------------------
# 1) IK 公式本身
# --------------------------------------------------------------------------
def test_inverse_kinematic_matches_the_source_model():
    """4 个关节长度 × 200 组可行朝向 × 两个 flip，逐值对上源码模型。"""
    import random

    rng = random.Random(20261027)
    for total in C.ARM_LENGTHS:
        upper, fore = total / 3.0, total * 2.0 / 3.0
        start = (400.0, 300.0)
        trials = 0
        while trials < 200:
            d = rng.uniform(abs(upper - fore) + 5.0, upper + fore - 5.0)
            a = rng.uniform(0.0, 6.283)
            target = (start[0] + cos(a) * d, start[1] + sin(a) * d)
            c = (d * d + upper * upper - fore * fore) / (2.0 * d * upper)
            if not (0.2 < c < 0.98):
                continue
            trials += 1
            for flip in (1.0, -1.0):
                want = mirror(source_elbow(start, target, upper, fore, flip))
                got = C.inverse_kinematic(start, target, upper, fore, flip)
                assert C.dist(want, got) < 1e-6, (
                    f"L={total} d={d:.1f} flip={flip}: 源码 {want} 仓库 {got}")


def test_inverse_kinematic_clamps_the_cosine_not_the_chord():
    """弦长退化时源码把余弦夹在 [0.2, 0.98]，臂不折叠也不拉伸。

    以前这里夹的是 reach：d 很小的时候肘点被折到起点**后面**（实测与源码差 154 px）。
    """
    upper, fore = 100.0, 200.0
    start = (500.0, 300.0)
    for d in (10.0, 60.0, 150.0):
        target = (start[0] + d, start[1])
        want = mirror(source_elbow(start, target, upper, fore, 1.0))
        got = C.inverse_kinematic(start, target, upper, fore, 1.0)
        assert C.dist(want, got) < 1e-6, f"d={d}: 源码 {want} 仓库 {got}"
        # 夹在余弦 0.2 上 → 肘点在弦上前方 0.2*upper、侧向 0.98*upper
        assert abs(C.dist(got, start) - upper) < 1e-6, "肘点必须离起点正好 upper"


# --------------------------------------------------------------------------
# 2) 绘制肘点
# --------------------------------------------------------------------------
def test_the_drawn_elbow_is_the_source_elbow():
    """绘制肘点必须等于按 IL 独立算出来的源码肘点（含 flip 符号）。"""
    sim = SimulationState(ROOM, seed=1234)
    for _ in range(200):
        sim.set_cursor((900.0, 500.0))
        sim.step(GAME_DT)
    state = sim.interpolate(1.0)
    for index in range(3):
        length = C.ARM_LENGTHS[index]
        flip = 1.0 if index % 2 == 0 else -1.0
        want = mirror(source_elbow(
            state.joints[index + 1], state.joints[index],
            length / 3.0, length * 2.0 / 3.0, flip,
        ))
        elbow, _ = drawn_elbow(state, index)
        assert C.dist(want, elbow) < 1e-6, f"joint{index}: 源码 {want} 绘制 {elbow}"
        assert C.dist(sim.arm.elbow_pos(index), elbow) < 1e-6, "elbow_pos 与绘制肘点必须一致"


def test_the_tail_elbow_connects_to_body_chunk_one():
    """尾关节没有 next：源码用 bodyChunks[1]，两骨都是 L/3（og.cs:137-141）。"""
    sim = SimulationState(ROOM, seed=1234)
    for _ in range(200):
        sim.step(GAME_DT)
    state = sim.interpolate(1.0)
    index = len(state.joints) - 1
    elbow, tip = drawn_elbow(state, index)
    assert tip == state.body1, "尾关节的 vector2 必须是 bodyChunks[1]，不是 chunk0"
    length = C.ARM_LENGTHS[index]
    flip = 1.0 if index % 2 == 0 else -1.0
    want = mirror(source_elbow(tip, state.joints[index], length / 3.0, length / 3.0, flip))
    assert C.dist(want, elbow) < 1e-6, f"尾关节肘点 {elbow} != 源码 {want}"


# --------------------------------------------------------------------------
# 3) 不出屏幕（逐 tick，不抽样）
# --------------------------------------------------------------------------
CHAMBERS = ((ROOM, 120.0), ((0.0, 0.0, 776.0, 415.0), 60.0))


def test_the_arm_never_draws_outside_the_chamber__big_chamber():
    _assert_arm_stays_inside(ROOM, 120.0)


def test_the_arm_never_draws_outside_the_chamber__documented_chamber():
    _assert_arm_stays_inside((0.0, 0.0, 776.0, 415.0), 60.0)


def _assert_arm_stays_inside(room, seconds: float):
    """追角光标 N 秒，**每一个 tick** 都渲染一遍，臂不许碰到腔室边缘。

    不能抽样：审计证明 `tick % 5` 会跳过 seed 7 的 tick 294、`tick % 10` 会跳过
    seed 1234 的 tick 3057 —— 我上一版就是那样写出一个永远绿的测试。
    大腔室必须跑 120 s：旧 IK（reach-clamp）的坏姿态最早出现在第 3010 tick（75 s），
    60 s 窗口里它照样绿——那又是一个空转测试。两种腔室都要跑（AGENTS §6.4）：
    776x415 是文档举例的腔室，轨道内缩同样是 26 px，量下来 0 越界。
    """
    sim = SimulationState(room, seed=1234)
    bad = []
    for tick in range(int(seconds / GAME_DT)):
        sim.set_cursor(_corner_cursor(tick, room))
        sim.step(GAME_DT)
        if _outside_px(_render(sim.interpolate(1.0), room)):
            bad.append(tick)
            if len(bad) > 5:
                break
    assert not bad, f"{room} 这些 tick 画到腔室外：{bad[:6]}"
