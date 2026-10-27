"""机械臂的绘制几何：肘点照源码算，且不许画到腔室外。

两条独立的合同：

1. **肘点口径**（og.cs:135-141 + oracle.cs:45-53）。``vector3 = ElbowPos(t, vector2)``
   且 ``ElbowPos = InverseKinematic(vector2, pos, L/3, 2L/3, index%2==0?1:-1)``——
   ``start`` 是下一个关节。曾经 ``draw_arm`` 把两个端点传反、也没处理 y 轴镜像，
   画出来的肘点与源码差整整 L/3（0/1/2 关节分别是 100/50/30 px），肢体网格、
   活塞、拉杆全长在源码不存在的点上，和缆线钩点（``ArmChain.elbow_pos``）也对不上。
2. **不出屏幕**。肘点是 IK 的两个解之一，会合法地鼓到离弦 100 px；轨道离腔室边
   只有 26 px，实测机械臂最多伸出屏幕 59 px（底边，追角光标 90 s）。游戏里那边
   是贴着轨道的房间墙，臂压在墙前看不出来；桌面上屏幕外什么都没有，被窗口裁掉
   就是硬切口。所以肘点出腔室时翻到 IK 的另一个解（口径同 oracle.cs:92-102），
   活塞按 ``_fit_inside`` 截长度。实测翻肘只发生在关节 0，2.5-3.9% 的 tick。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from iterator_pet.platform.fixed_loop import GAME_DT
from iterator_pet.rendering.renderer import (
    ARM_DRAW_MARGIN,
    _inside,
    draw_arm,
    drawn_elbow,
)
from iterator_pet.simulation import constants as C
from iterator_pet.simulation.state import SimulationState

ROOM = (0.0, 0.0, 1138.0, 608.0)   # 1707/1.5 x 912/1.5，本机腔室


def _app():
    return QApplication.instance() or QApplication([])


def _corner_cursor(tick: int):
    w, h = ROOM[2], ROOM[3]
    corners = ((30.0, 30.0), (w - 30.0, 30.0), (w - 30.0, h - 30.0), (30.0, h - 30.0))
    return corners[(tick // 80) % 4]


def _run(seconds: float, bounds=ROOM, seed: int = 1234) -> SimulationState:
    sim = SimulationState(bounds, seed=seed)
    for tick in range(int(seconds / GAME_DT)):
        sim.set_cursor(_corner_cursor(tick))
        sim.step(GAME_DT)
    return sim


def _bleeds(state, margin: int = 120) -> bool:
    """把机械臂画到一张四周留 margin 的画布上，看留白里有没有像素。"""
    _app()
    w, h = ROOM[2], ROOM[3]
    iw, ih = int(w) + 2 * margin, int(h) + 2 * margin
    image = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(margin, margin)
    draw_arm(painter, state)
    painter.end()
    for rect in (
        (0, 0, iw, margin),                 # 上
        (0, ih - margin, iw, margin),       # 下
        (0, 0, margin, ih),                 # 左
        (iw - margin, 0, margin, ih),       # 右
    ):
        mask = image.copy(*rect).createAlphaMask()
        if any(bytes(mask.constBits())[: mask.sizeInBytes()]):
            return True
    return False


def test_the_drawn_elbow_is_the_source_elbow():
    """正常姿态下，画的肘点必须与 ArmChain.elbow_pos 逐 px 相同。"""
    sim = _run(20.0)
    state = sim.interpolate(1.0)
    for index in (0, 1, 2):
        elbow, tip = drawn_elbow(state, index)
        source = sim.arm.elbow_pos(index)
        assert C.dist(elbow, source) < 1e-9, f"joint{index} 的绘制肘点不是源码肘点"
        length = C.ARM_LENGTHS[index]
        assert abs(C.dist(elbow, state.joints[index]) - length * 2.0 / 3.0) < 0.5
        assert abs(C.dist(elbow, tip) - length / 3.0) < 0.5


def test_the_tail_elbow_connects_to_body_chunk_one():
    """尾关节没有 next：源码用 bodyChunks[1]，两根骨都是 L/3（og.cs:137-141）。

    注意 ``start`` 是 ``vector2``（也就是 body1），所以肘点离 **body1** 正好 L/3；
    和弦长可能超过 2L/3（尾关节被焊在 chunk0 上，body1 又是另一个点），那时源码
    的 IK 把 reach 夹到 upper+fore，肘点离本关节就比 L/3 远。两个断言分别钉住
    "vector2 是 body1" 和 "start 那一端的骨长是 L/3"。
    """
    sim = _run(20.0)
    state = sim.interpolate(1.0)
    index = len(state.joints) - 1
    elbow, tip = drawn_elbow(state, index)
    assert tip == state.body1, "尾关节的 vector2 必须是 bodyChunks[1]，不是 chunk0"
    third = C.ARM_LENGTHS[index] / 3.0
    assert abs(C.dist(elbow, tip) - third) < 0.5, "肘点离 body1 不是 L/3"
    assert abs(C.dist(elbow, state.joints[index]) - third) >= -0.5, "肘点离本关节不可能短于 L/3"


def test_the_elbow_flips_when_it_would_leave_the_chamber():
    """真实腔室里追角，一旦出现"肘点本来会出界"的姿态，画出来的必须是另一个解。"""
    sim = SimulationState(ROOM, seed=1234)
    checked = 0
    for tick in range(int(90.0 / GAME_DT)):
        sim.set_cursor(_corner_cursor(tick))
        sim.step(GAME_DT)
        state = sim.interpolate(1.0)
        elbow, tip = drawn_elbow(state, 0)
        length = C.ARM_LENGTHS[0]
        natural = C.inverse_kinematic(tip, state.joints[0], length / 3.0, length * 2.0 / 3.0, -1.0)
        if _inside(natural, state.bounds, ARM_DRAW_MARGIN):
            continue
        checked += 1
        assert _inside(elbow, state.bounds, ARM_DRAW_MARGIN), "肘点本来出界，却没翻到另一个解"
        if checked >= 20:
            break
    assert checked >= 20, f"90 s 追角里只出现 {checked} 次「肘点本来会出界」的姿态"


def test_the_arm_never_draws_outside_the_chamber():
    """本机腔室 + 追角光标 100 s：机械臂一个像素都不许画到腔室外。

    窗口不能短：seed 1234 下"肘点出界"的姿态最早出现在第 3010 tick（75 s），
    我第一版只跑 40 s，在旧代码上照样通过——那就是个空转的测试（实测旧代码在
    tick 3010 有 2361 个越界像素）。
    """
    sim = SimulationState(ROOM, seed=1234)
    bled = 0
    for tick in range(int(100.0 / GAME_DT)):
        sim.set_cursor(_corner_cursor(tick))
        sim.step(GAME_DT)
        if tick % 10:
            continue
        if _bleeds(sim.interpolate(1.0)):
            bled += 1
    assert bled == 0, f"{bled} 帧画到腔室外（修前实测最多伸出 59 px）"
