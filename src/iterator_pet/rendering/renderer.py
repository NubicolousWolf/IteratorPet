"""Draws the chamber the way ``OracleGraphics`` composes it.

Layer order, bottom to top:

1. rail + U-shaped carriage (``ArmBase``)
2. halo (concentric amber rings with 100 ticks, right behind the head)
3. the four-segment mechanical arm, each limb bent by a two-bone IK
4. umbilical spine (80 chitin segments) + 14 filaments into the head
5. cloth robe (11x11 grid, orange at the neck to deep red at the hem)
6. the puppet itself: two pink chunks, steel ear pods, antennae, black pixel eyes

Everything is in *game pixels*; the canvas applies the world scale.
"""

from __future__ import annotations

from math import atan2, cos, degrees, hypot, pi, radians, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from ..simulation import constants as C
from ..simulation.state import RenderState
from .sprites import tinted


def qc(rgb, alpha: int = 255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], max(0, min(255, alpha)))


# --------------------------------------------------------------------------
# rail + carriage
# --------------------------------------------------------------------------
def draw_rail(painter: QPainter, state: RenderState) -> None:
    # 早期原型把整圈轨道画成一条橙色发光框（两层描边：2 px 暗线 + 7 px 暖光）。
    # 游戏里轨道是砌进墙里的，只有滑车/底座露出来 —— 那条框是原型的调试辅助，
    # 发布版删掉（2026-10-27）。要临时看轨道请用开发者面板的数字，不要再画回来。
    x, y = state.base_point
    d = state.base_dir                      # BaseDir: points into the chamber
    p = (-d[1], d[0])                       # Custom.PerpendicularVector
    origin = (x - d[0] * 10.0, y - d[1] * 10.0)

    # --- ArmBase: three Futile_White meshes, vertices from UpdateMesh -----
    # Not "triangles" visually -- TriangleMesh is one filled silhouette; the
    # triangles only exist inside the engine. The outline is a bucket whose
    # back edge is notched so the rail runs through it.
    def base_outline(pos, width, height, inner, shade, alpha=255):
        v = [
            (-inner * 0.5, -height),
            (-width, -height * 0.75),
            (-width, height * 0.75),
            (-width * 0.8, height),
            (-width * 0.5, height),
            (width * 0.5, height),
            (width * 0.8, height),
            (width, height * 0.75),
            (width, -height * 0.75),
            (inner * 0.5, -height),
        ]
        path = QPainterPath()
        pts = [(pos[0] + p[0] * ox + d[0] * oy, pos[1] + p[1] * ox + d[1] * oy) for ox, oy in v]
        path.moveTo(QPointF(*pts[0]))
        for q in pts[1:]:
            path.lineTo(QPointF(*q))
        path.closeSubpath()
        painter.setBrush(QBrush(qc(shade, alpha)))
        painter.setPen(QPen(qc((26, 21, 24), min(255, alpha)), 1.0))
        painter.drawPath(path)

    metal_col = C.metal_colour()
    metal = metal_col  # the support legs below still use this name
    shell = C.mix(metal_col, C.arm_base_colour(), 0.8)            # +6
    inner_shell = C.mix(metal_col, C.arm_highlight_colour(), 0.8)  # +7
    base_outline(origin, 30.0, 17.0, 20.0, shell)
    base_outline(origin, 24.0, 15.0, 30.0, inner_shell)
    base_outline(origin, 30.0, 17.0, 20.0, shell)

    # --- two hydraulic support legs (25 + 45 bones) ----------------------
    pivot = (origin[0] + d[0] * 11.0, origin[1] + d[1] * 11.0)
    anchor = state.joints[0] if state.joints else origin
    for side in (-1.0, 1.0):
        foot = (pivot[0] + p[0] * 17.0 * side, pivot[1] + p[1] * 17.0 * side)
        bend = C.inverse_kinematic(foot, anchor, 25.0, 45.0, side)
        # SupportSprite(k, 0/1) are `pixel` bars, scaleX 2, coloured metalColor.
        bar = tinted("pixel", metal)
        for p0, p1 in ((foot, bend), (bend, anchor)):
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            length = hypot(dx, dy)
            angle = atan2(dy, dx) * 180.0 / pi
            painter.save()
            painter.translate(QPointF(*p0))
            painter.rotate(-angle)
            painter.drawPixmap(QRectF(0.0, -1.0, length + 10.0, 2.0), bar, QRectF(bar.rect()))
            painter.restore()
        bolt = tinted("deerEyeB", metal)
        painter.drawPixmap(QRectF(bend[0] - 3.0, bend[1] - 3.0, 6.0, 6.0), bolt, QRectF(bolt.rect()))

        # CircleSprite(i, 0/1): 0.5 and 0.45 scale Circle20 at +-17 perp
        for sprite, scale, dx, dy in ((0, 0.5, 0.0, 0.0), (1, 0.45, -1.0, 1.0)):
            knob = tinted("Circle20", metal if sprite == 0 else C.mix(metal, shell, 0.35))
            size = 20.0 * scale
            painter.drawPixmap(
                QRectF(foot[0] - size * 0.5 + dx, foot[1] - size * 0.5 + dy, size, size),
                knob, QRectF(knob.rect()),
            )

# --------------------------------------------------------------------------
# halo
# --------------------------------------------------------------------------
def draw_halo(painter: QPainter, state: RenderState) -> None:
    """og.cs:672-750 —— 2 个环 + 100 个 bit + （P3）20 根电光。

    颜色：源码里三样都是**纯黑**（`og.cs:678/683/689`），实机发橙是那盏
    `ForegroundLights` 的灯盖在上面。桌面版没有光照系统，2026-10-28 用户拍板
    **直接上色**（PLAN-halo §4 选项 C）：环/bit 用那盏灯的颜色画，不叠柔光。
    """
    cx, cy = state.halo_center
    expand = state.halo_expand
    push = state.halo_push
    white = state.halo_white
    gain = max(1.0, state.halo_gain)

    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)

    # 内暗外亮的径向渐变：源码里这层亮度差来自那盏 400 px 的灯，桌面端直接画出来。
    # 环用一个"到环 0 半径就收尾"的渐变（这样白闪填盘时外缘最亮），
    # bit 用延伸到最外圈 bit 半径的那个。
    base = QColor(min(255, int(C.HALO_COLOUR[0] * gain)),
                  min(255, int(C.HALO_COLOUR[1] * gain)),
                  min(255, int(C.HALO_COLOUR[2] * gain)))

    def _gradient(outer: float, dim: float) -> QBrush:
        grad = QRadialGradient(QPointF(cx, cy), max(1.0, outer))
        for stop, factor in ((0.0, C.HALO_GRADIENT_INNER),
                             (0.55, 0.5 * (C.HALO_GRADIENT_INNER + C.HALO_GRADIENT_OUTER)),
                             (1.0, C.HALO_GRADIENT_OUTER)):
            grad.setColorAt(stop, QColor(
                min(255, int(base.red() * factor)),
                min(255, int(base.green() * factor)),
                min(255, int(base.blue() * factor)),
                int(255 * C.HALO_OPACITY * dim),
            ))
        return QBrush(grad)

    ring_zero = (C.HALO_RADIUS_BASE + push) * expand * 10.0
    ring_brush = _gradient(ring_zero, 1.0)
    bit_brush = _gradient((C.HALO_RADIUS_BASE + 3.0 + push) * expand * 10.0, C.HALO_BIT_DIM)

    # 0. 电光（20 根，画在**最底层**：被环和 bit 盖住，看起来是从光环内部伸出来的）：源码 `Bezier(stuckAt, handle, center+dir·Radius(2), center+dir·400)`。
    #    桌面端按用户 2026-10-28 的三条意见调整（源码数字见 docs/PLAN-halo.md §2.8）：
    #      · 宽度 = 光环环带宽度（2 px 全宽）——原来 3.2 半宽太粗
    #      · 长度拉到**腔室对角线**，任何时刻都延伸出屏幕（原来 140–230 px 会在中途截断）
    #      · 端点落在最外圈 bit 的外缘，真正"接上"光环
    # 端点接**内圈环**（ring 0）：电光画在最底层，被环和 bit 盖住，视觉上就是"扎进光环里"。
    spark_radius = (C.HALO_RADIUS_BASE + push) * expand * 10.0 + 1.0
    spark_base = QColor(min(255, int(C.HALO_COLOUR[0] * gain)),
                        min(255, int(C.HALO_COLOUR[1] * gain)),
                        min(255, int(C.HALO_COLOUR[2] * gain)))
    spark_colour = QColor(
        min(255, int(spark_base.red() * 1.05)),
        min(255, int(spark_base.green() * 1.05)),
        min(255, int(spark_base.blue() * 1.05)),
        int(255 * C.HALO_OPACITY),
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(spark_colour))
    clip = state.bounds
    far_reach = 0.0
    if clip is not None:
        far_reach = hypot(clip[2], clip[3])
    for (stuck_at, handle, light) in state.halo_sparks:
        if light <= C.HALO_SPARK_VISIBLE:
            continue
        dx, dy = stuck_at[0] - cx, stuck_at[1] - cy
        reach = hypot(dx, dy) or 1.0
        dirx, diry = dx / reach, dy / reach
        anchor_reach = far_reach if far_reach > 0.0 else max(reach, 300.0)
        ax = cx + dirx * anchor_reach
        ay = cy + diry * anchor_reach
        # 弯曲量：用 handle 的横向分量，放大成可见的弧（±200 px）；锚点那头只带一点，
        # 大部分弯放在靠近光环的 cB 上，这样屏幕上看到的是明显的一条弧而不是直线。
        # 弯曲：**沿路径加一个正弦鼓包**（两端为 0、中段最大），端点仍然精确落在光环上。
        # 只偏控制点时，锚点在 1290 px 外 → 屏幕上那一段几乎还是直线；正弦鼓包能把弯
        # 直接压到看得见的范围里。方向由 handle 决定，幅度 120–360 px。
        lateral = (handle[0] - ax) * -diry + (handle[1] - ay) * dirx
        bow_sign = 1.0 if lateral >= 0.0 else -1.0
        handle_len = hypot(handle[0] - ax, handle[1] - ay)
        bow = bow_sign * (180.0 + 240.0 * min(1.0, handle_len / C.HALO_HANDLE_MAX))
        # 第二谐波 + 相位（相位由 handle 决定）：一根弧里有 1.5 个波峰，才像"扭"的雷电，
        # 而不是一条平滑的圆弧。
        phase = (handle[0] * 0.017 + handle[1] * 0.031) % (2.0 * pi)
        control_a = (ax, ay)
        end_point = (cx + dirx * spark_radius, cy + diry * spark_radius)
        control_b = end_point
        half_width = C.HALO_SPARK_HALF_WIDTH * light

        points = []
        for i in range(C.HALO_SPARK_POINTS + 1):
            f = i / C.HALO_SPARK_POINTS
            # 参数序：|_bezier| 是教科书式 (起点, c1, c2, 终点, t)
            base = _bezier((ax, ay), control_a, control_b, end_point, f)
            # sin(πf) 保证两端为 0（端点仍精确落在刻度环上），再乘一个带相位的
            # 余弦做 S 形扭动。
            bulge = bow * sin(pi * f) * cos(2.2 * pi * f + phase)
            points.append((base[0] + (-diry) * bulge, base[1] + dirx * bulge))
        left_side: list[tuple[float, float]] = []
        right_side: list[tuple[float, float]] = []
        for index, point in enumerate(points):
            nxt = points[min(index + 1, len(points) - 1)]
            prv = points[max(index - 1, 0)]
            tx, ty = nxt[0] - prv[0], nxt[1] - prv[1]
            norm = hypot(tx, ty) or 1.0
            nx, ny = ty / norm, -tx / norm      # y 朝下时的 PerpendicularVector 孪生式
            left_side.append((point[0] + nx * half_width, point[1] + ny * half_width))
            right_side.append((point[0] - nx * half_width, point[1] - ny * half_width))
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        path.moveTo(QPointF(*left_side[0]))
        for point in left_side[1:]:
            path.lineTo(QPointF(*point))
        for point in reversed(right_side):
            path.lineTo(QPointF(*point))
        path.closeSubpath()
        painter.drawPath(path)

    # 1. 环（2 个）：填充的**圆环带**，外半径 = Radius(k)，内半径 = R·(1−alpha)。
    #    alpha = 3/R（环 1 固定、环 0 被 white 拉向 1）→ 普通时厚 3 px，
    #    white 时环 0 的内半径收缩到 0，整盘填满——这才是源码的"闪白"。
    for ring in range(C.HALO_RING_SPRITES):
        radius = (C.HALO_RADIUS_BASE + ring + push) * expand * 10.0
        if radius <= 0.5:
            continue
        thin = C.HALO_RING_THICKNESS / radius
        alpha = thin + (1.0 - thin) * white if ring == 0 else min(1.0, thin)
        alpha = max(0.0, min(1.0, alpha))
        inner = radius * (1.0 - alpha)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addEllipse(QPointF(cx, cy), radius, radius)
        if inner > 0.02:
            path.addEllipse(QPointF(cx, cy), inner, inner)
        painter.setBrush(ring_brush)
        painter.drawPath(path)

    # 2. bit（100 个）：固定 4 px 宽、8·Fill 长的矩形，**中心**落在 Radius(ring+0.5)。
    #    角度 = i/len·360 + Rotation(ring)；长轴指向径向。
    offset = 0
    for ring, count in enumerate(C.HALO_RINGS):
        if ring >= len(state.halo_rot):
            break
        rotation = state.halo_rot[ring]
        radius = (C.HALO_RADIUS_BASE + ring + 0.5 + push) * expand * 10.0
        painter.setBrush(bit_brush)
        for index in range(count):
            fill = state.halo_fill[offset + index] if offset + index < len(state.halo_fill) else 0.0
            offset += 1
            length = C.HALO_BIT_LENGTH * fill
            if length <= 0.05:
                continue
            angle = index / count * 360.0 + rotation
            rad = radians(angle)
            x = cx + sin(rad) * radius
            y = cy + cos(rad) * radius
            painter.save()
            painter.translate(x, y)
            painter.rotate(-angle)
            painter.drawRect(
                QRectF(-C.HALO_BIT_WIDTH * 0.5, -length * 0.5, C.HALO_BIT_WIDTH, length)
            )
            painter.restore()

    painter.restore()


# --------------------------------------------------------------------------
# arm
# --------------------------------------------------------------------------
def _bezier(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def _ribbon_half(start, elbow, end, half_width: float, mesh_segs: int, flip: float) -> list[QPainterPath]:
    """One `TriangleMesh.MakeLongMesh` limb, exactly as ArmJointGraphics builds it.

    Two bezier halves (joint -> elbow, elbow -> tip) sampled ``mesh_segs`` times,
    with the game's width modulation and the 25 % elbow pinch.
    """
    v, v3, v2 = start, elbow, end
    d1 = C.dir_vec(v, v3)
    d2 = C.dir_vec(v3, v2)
    d3 = _slerp2(d1, d2, 0.5)
    c_a = (v[0] + d1[0] * C.dist(v, v3) * 0.2, v[1] + d1[1] * C.dist(v, v3) * 0.2)
    c_b = (v3[0] - d3[0] * C.dist(v, v3) * 0.2, v3[1] - d3[1] * C.dist(v, v3) * 0.2)
    c_a2 = (v3[0] + d3[0] * C.dist(v3, v2) * 0.2, v3[1] + d3[1] * C.dist(v3, v2) * 0.2)
    c_b2 = (v2[0] - d2[0] * C.dist(v3, v2) * 0.2, v2[1] - d2[1] * C.dist(v3, v2) * 0.2)

    paths: list[QPainterPath] = []
    for half in (0, 1):
        if half == 0:
            p0, p1, p2, p3, axis = v, c_a, c_b, v3, d1
        else:
            p0, p1, p2, p3, axis = v3, c_a2, c_b2, v2, d3
        left: list[tuple[float, float]] = []
        right: list[tuple[float, float]] = []
        for step in range(1, mesh_segs + 1):
            t = step / max(1, mesh_segs - 1)
            # 0.6 + 0.5(1 - sin) + 0.3 * max(sin(InverseLerp(0,0.3,t)), sin(InverseLerp(0.7,1,t)))
            bulge = 0.6 + 0.5 * (1.0 - sin(t * pi))
            if t < 0.3:
                bulge += 0.3 * sin((t / 0.3) * pi)
            elif t > 0.7:
                bulge += 0.3 * sin(((t - 0.7) / 0.3) * pi)
            if t >= 1.0:
                bulge = 0.5
            point = _bezier(p0, p1, p2, p3, t)
            nxt = _bezier(p0, p1, p2, p3, min(1.0, t + 1.0 / max(1, mesh_segs - 1)))
            heading = C.dir_vec(point, nxt) if C.dist(point, nxt) > 1e-6 else axis
            perp = (-heading[1], heading[0])
            offset = 0.0
            if (half == 0 and t > 0.75) or (half == 1 and t < 0.25):
                bulge *= 0.5
                offset = bulge * half_width * flip
            half_w = half_width * bulge
            left.append((point[0] + perp[0] * (offset + half_w), point[1] + perp[1] * (offset + half_w)))
            right.append((point[0] + perp[0] * (offset - half_w), point[1] + perp[1] * (offset - half_w)))
        path = QPainterPath()
        path.moveTo(QPointF(*left[0]))
        for point in left[1:]:
            path.lineTo(QPointF(*point))
        for point in reversed(right):
            path.lineTo(QPointF(*point))
        path.closeSubpath()
        paths.append(path)
    return paths


def _inverse_lerp(a: float, b: float, value: float) -> float:
    """``Mathf.InverseLerp``: the IL clamps the ratio to [0, 1]."""
    if b == a:
        return 0.0
    return max(0.0, min(1.0, (value - a) / (b - a)))


def _slerp2(a, b, t):
    a0 = atan2(a[1], a[0])
    a1 = atan2(b[1], b[0])
    delta = (a1 - a0 + pi) % (2 * pi) - pi
    angle = a0 + delta * t
    return (cos(angle), sin(angle))


def _limb(painter: QPainter, a, elbow, b, width: float, alpha: int = 255, mesh_segs: int = 12) -> None:
    """Filled long-mesh ribbon plus a lighter inner highlight ribbon."""
    flip = 1.0 if mesh_segs % 2 else -1.0
    for path in _ribbon_half(a, elbow, b, width, mesh_segs, flip):
        grad = QLinearGradient(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
        grad.setColorAt(0.0, qc(C.arm_base_colour(), alpha))
        grad.setColorAt(0.45, qc(C.arm_highlight_colour(), alpha))
        grad.setColorAt(1.0, qc(C.mix(C.arm_base_colour(), (24, 20, 22), 0.35), alpha))
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(qc((30, 25, 28), min(255, alpha)), 0.8))
        painter.drawPath(path)


def _fit_inside(origin, direction, length: float, bounds) -> float:
    """把从 ``origin`` 沿 ``direction`` 伸 ``length`` 的杆件截到腔室里，返回可用长度。

    桌面端增补，不是源码行为。活塞（``MirosLegSmallPart``，关节 0 长 50 px）从
    肘点向外伸，而肘点本身可以合法地鼓到离弦 100 px（IK 的两个解之一，见
    ``inverse_kinematic``），滑车轨道离腔室边只有 26 px —— 实测机械臂最多伸出
    屏幕 59 px（底边，追角光标 90 s）。游戏里腔室外是实心边框（墙贴着轨道），
    臂画在墙前没有观感问题；桌面上屏幕外什么都没有，被窗口裁掉就是一条硬切口，
    所以这里只截杆件长度，不动肘点、不动肢体、不动速度。实测见
    ``_work/scene_bleed_probe.py``。
    """
    bx, by, bw, bh = bounds
    limit = length
    for axis in (0, 1):
        o = origin[axis]
        d = direction[axis]
        low = bx if axis == 0 else by
        high = (bx + bw) if axis == 0 else (by + bh)
        if o < low or o > high:
            return 0.0  # 起点已经在腔室外，截不动，直接不画
        if abs(d) < 1e-9:
            continue
        target = high if d > 0.0 else low
        t = (target - o) / d
        if t >= 0.0:
            limit = min(limit, t)
    return max(0.0, limit)


def drawn_elbow(state: RenderState, index: int):
    """这个关节的绘制肘点（``vector3``）和它连到的下一个点（``vector2``）。

    源码口径（og.cs:135-141 + oracle.cs:45-53）::

        vector2 = myJoint.next == null ? bodyChunks[1] : myJoint.next.pos
        vector3 = myJoint.ElbowPos(timeStacker, vector2)
        ElbowPos: InverseKinematic(vector2, pos, L/3, 2L/3, index%2==0 ? 1 : -1)

    ``start`` 是 **vector2**、``target`` 才是本关节，所以肘点离下一个关节 L/3、
    离本关节 2L/3；没有 ``next`` 的尾关节用 ``(L/3, L/3)``。这个函数原来写在
    ``draw_arm`` 里并且把两个端点传反了（也没处理 y 轴镜像），画出来的肘点与源码
    差整整 L/3（实测 100/50/30 px），肢体网格、活塞、拉杆全长在源码没有的点上，
    与缆线钩点（``ArmChain.elbow_pos``）也对不上。

    两处桌面端的改动，都只影响画不画得下、不动物理：

    1. **翻肘**：肘点出了腔室就取 IK 的另一个解。方向和 oracle.cs:92-102 一样
       （``vector = IK(..., flip)`` / ``a = IK(..., -flip)``，哪边在 Solid 瓦片里
       就翻号），只是游戏的"实心"是房间瓦片，桌面这边对应腔室外（屏幕外什么都
       没有，臂伸出去就被窗口裁掉）。实测只在关节 0 上触发，占 2.5-3.9% 的 tick，
       10 分钟 14 段、最长 7.6 s，不会逐帧抖动。
    2. 余量 ``ARM_DRAW_MARGIN``：只判肘点圆心在不在腔室里，边缘还会被切掉 12 px。

    活塞（``MirosLegSmallPart``）另由 ``_fit_inside`` 截长度。
    """
    joints = state.joints
    last = index == len(joints) - 1
    joint = joints[index]
    tip = joints[index + 1] if not last else state.body1
    length = C.ARM_LENGTHS[index]
    upper = length / 3.0
    fore = length / 3.0 if last else length * 2.0 / 3.0
    source_flip = 1.0 if index % 2 == 0 else -1.0
    # flip 就是源码 `index % 2 == 0 ? 1 : -1` 那个符号，**不取负**：
    # C.inverse_kinematic 已按 y 镜像帧实现，同一个 flip 给出同一侧
    # （_work/source_ik_probe.py：4 个关节长度 × 500 组 × 两个 flip，原样 1000/1000
    # 命中、取负 0 命中）。取负会把肘点画到弦的另一侧——2026-10-27 修掉。
    elbow = C.inverse_kinematic(tip, joint, upper, fore, source_flip)
    return elbow, tip


def draw_arm(painter: QPainter, state: RenderState) -> None:
    """One ``ArmJointGraphics`` per joint, in the game's sprite order.

    Sprite indices decide the layering: cogs (0-2) and metal parts (3-6) come
    before the limb mesh (9-12), and the joint circles (7, 8, 13, 14) come
    after. So the rods and gears sit *behind* the arm, while the axle discs sit
    in front of it.
    """
    joints = state.joints
    count = len(joints)
    metal = C.metal_colour()
    axle_colour = C.mix(metal, C.arm_highlight_colour(), 0.5)
    half_widths = (7.0, 5.0, 4.0, 3.0)

    for index in range(count):
        joint = joints[index]
        elbow, tip = drawn_elbow(state, index)

        # 活塞长度（源码 og.cs:103：scaleY = 2 / 1 / 0.5 × MirosLegSmallPart 25 px）。
        piston_h = 25.0 * (2.0 if index == 0 else 1.0 if index == 1 else 0.5)

        aim = C.dir_vec(joint, elbow)
        root = (joint[0] + aim[0] * (12.0 if index == 0 else 2.0),
                joint[1] + aim[1] * (12.0 if index == 0 else 2.0))
        cog = state.cog_rot[index] if index < len(state.cog_rot) else 0.0

        # ---- layer 1: CogSprite(0..2), behind everything -------------------
        # FSprite default anchor is 0.5/0.5, so each bar is CENTRED on the
        # joint: it spans +-9 px, not 0..18. Drawing it outward made the teeth
        # twice as long and turned a near-circular gear into a star.
        cog_sprite = tinted("pixel", metal)
        cog_len = 18.0 - index * 2.0
        cog_wid = max(1.5, 5.0 - index * 0.5)
        base_angle = atan2(aim[1], aim[0]) * 180.0 / pi
        for lobe in range(3):
            painter.save()
            painter.translate(QPointF(*root))
            painter.rotate(base_angle + 60.0 * lobe + cog * 360.0)
            painter.drawPixmap(
                QRectF(-cog_len * 0.5, -cog_wid * 0.5, cog_len, cog_wid),
                cog_sprite, QRectF(cog_sprite.rect()),
            )
            painter.restore()

        # ---- layer 2: MetalPart(0..3), the hydraulic brace -----------------
        near = (joint[0] + (tip[0] - joint[0]) * 0.2, joint[1] + (tip[1] - joint[1]) * 0.2)
        far = (joint[0] + (tip[0] - joint[0]) * 0.8, joint[1] + (tip[1] - joint[1]) * 0.8)
        small_len = C.ARM_LENGTHS[index] / 4.0
        to_far = C.dir_vec(elbow, far)
        v4 = (elbow[0] + to_far[0] * small_len, elbow[1] + to_far[1] * small_len)

        # MetalPart(0): MirosLegSmallPart (4x25 px), anchored at the elbow
        piston = tinted("MirosLegSmallPart", metal)
        piston_w = 4.0 * (1.5 if index == 0 else 1.0 if index == 1 else 0.8)
        piston_dir = C.dir_vec(near, elbow)
        piston_len = piston_h
        if state.bounds is not None:
            # og.cs:97 `anchorY = 1` + og.cs:143 `rotation = Aim(Lerp(0.2), elbow)`：
            # Futile 的 anchorY=1 让精灵从锚点朝 aim 的**反方向**长，所以这根活塞
            # 实际是从肘点朝近点（弦）方向伸，而不是背离弦。截长度必须按画出来的
            # 方向截——按 +piston_dir 截的是它根本不画的那一侧（600 s 里 24 个 tick
            # 被无故截短，最多 32.9/50 px）。
            piston_len = _fit_inside(elbow, (-piston_dir[0], -piston_dir[1]), piston_h, state.bounds)
        painter.save()
        painter.translate(QPointF(*elbow))
        painter.rotate(atan2(piston_dir[1], piston_dir[0]) * 180.0 / pi + 90.0)
        painter.drawPixmap(QRectF(-piston_w * 0.5, 0.0, piston_w, piston_len), piston, QRectF(piston.rect()))
        painter.restore()

        bar = tinted("pixel", metal)
        # MetalPart(1): elbow -> v4, scaleY = smallArmLength
        # MetalPart(2): tip -> v4, scaleY = |v4-tip| + 10
        for p0, p1, extra in ((elbow, v4, 0.0), (tip, v4, 10.0)):
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            length = hypot(dx, dy)
            if length < 0.5:
                continue
            painter.save()
            painter.translate(QPointF(*p0))
            painter.rotate(atan2(dy, dx) * 180.0 / pi)
            painter.drawPixmap(QRectF(0.0, -1.0, length + extra, 2.0), bar, QRectF(bar.rect()))
            painter.restore()

        # MetalPart(3) is part of the `for k < 4) MetalPartSprite(k).color =
        # metalColor` group -- near black, so it reads as the rod end rather than
        # a floating grey dot. deerEyeB is 5x5 px and its scale is never set.
        bolt = tinted("deerEyeB", metal)
        painter.drawPixmap(QRectF(v4[0] - 2.5, v4[1] - 2.5, 5.0, 5.0), bolt, QRectF(bolt.rect()))

        # ---- layer 3: the limb mesh ---------------------------------------
        mesh_segs = max(3, int(C.ARM_LENGTHS[index] / 10.0))
        _limb(painter, joint, elbow, tip, half_widths[min(index, 3)], mesh_segs=mesh_segs)

        # ---- layer 4: JointSprite(0,0/1) and (1,0/1), in front ------------
        painter.setPen(Qt.PenStyle.NoPen)
        gear = tinted("Circle20", metal)
        pin = tinted("deerEyeB", axle_colour)
        for centre, circle_scale in ((root, (7.0 - index * 0.5) / 10.0),
                                     (elbow, (6.0 - index * 1.5) / 10.0)):
            size = 20.0 * max(0.12, circle_scale)
            painter.drawPixmap(
                QRectF(centre[0] - size * 0.5, centre[1] - size * 0.5, size, size),
                gear, QRectF(gear.rect()),
            )
            painter.drawPixmap(QRectF(centre[0] - 2.5, centre[1] - 2.5, 5.0, 5.0), pin, QRectF(pin.rect()))


# --------------------------------------------------------------------------
# umbilical cord
# --------------------------------------------------------------------------
def _ribbon(points, half_width: float) -> QPainterPath:
    """``TriangleMesh.MakeLongMesh``: a constant-width strip through the nodes."""
    if len(points) < 2:
        return QPainterPath()
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for i, point in enumerate(points):
        if i == 0:
            a, b = points[0], points[1]
        elif i == len(points) - 1:
            a, b = points[-2], points[-1]
        else:
            a, b = points[i - 1], points[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = hypot(dx, dy) or 1.0
        nx, ny = -dy / length * half_width, dx / length * half_width
        left.append((point[0] + nx, point[1] + ny))
        right.append((point[0] - nx, point[1] - ny))
    path = QPainterPath()
    path.moveTo(QPointF(*left[0]))
    for point in left[1:]:
        path.lineTo(QPointF(*point))
    for point in reversed(right):
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    return path


def _plates(painter: QPainter, points, colour, scale_x: float, scale_y: float,
            alpha: int = 255) -> None:
    """``CentipedeSegment`` sprites laid on top of the cord ribbon.

    ``og.cs:947-950`` gives both layers FIXED scales -- (0.5, 0.3) and
    (0.4, 0.15) -- with no pitch-dependent clamp anywhere in the source.

    The extracted sprite is 18x15 and drawn with its long axis horizontal (its
    top-centre and bottom-centre pixels are notched), which is exactly why
    ``og.cs:979`` adds ``+ 90f`` to the rotation: that turns the sprite's WIDTH
    onto the cable. So ``scaleX`` is the length ALONG the cord (18*0.5 = 9 px)
    and ``scaleY`` the width ACROSS (15*0.3 = 4.5 px), on a 10 px pitch -- a rib
    with a 1 px gap, i.e. a continuous ribbed cable.

    ``painter.rotate`` here maps the sprite's local +x onto the cord, and the
    sprite's width is its local +x, so the QRectF takes (along, across) directly.
    """
    pixmap = tinted("CentipedeSegment", colour, alpha)
    along = pixmap.width() * scale_x
    across = pixmap.height() * scale_y
    # Futile point-samples its sprites, and draw_scene() already turns smoothing
    # off -- but canvas.py turns it back ON for the live window, so the live plate
    # was a filtered 18x15 -> 9x4.5 downscale whose soft ends shaved ~1 px off each
    # side and widened the 1 px inter-plate gap into a dotted line. Force it off
    # for the plates only, and put the caller's setting back.
    smooth = painter.testRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    for i in range(len(points)):
        a = points[i - 1] if i else points[0]
        b = points[i + 1] if i + 1 < len(points) else points[-1]
        angle = atan2(b[1] - a[1], b[0] - a[0])
        painter.save()
        painter.translate(QPointF(*points[i]))
        painter.rotate(angle * 180.0 / pi)
        painter.drawPixmap(
            QRectF(-along * 0.5, -across * 0.5, along, across),
            pixmap,
            QRectF(pixmap.rect()),
        )
        painter.restore()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)


def filament_palette() -> dict[str, tuple[int, int, int]]:
    """``og.cs:1012-1026``: the three strand colours, derived not invented.

        smallCoordColors 0 -> metalColor
                         1 -> Lerp(new Color(1,0,0), metalColor, 0.5f)
                         2 -> Lerp(new Color(0,0,1), metalColor, 0.5f)

    With ``metalColor = Lerp(black, (31,7,0), 0.12f)`` = (3,0,0) that is (3,0,0),
    (129,0,0) and (1,0,127). An earlier version hard-coded (128,26,30) /
    (36,40,86) / (22,20,24): G and B of the red strand cannot be non-zero out of
    a 50/50 mix with a near-black metal, the blue was a desaturated grey-blue
    instead of blue, and the dict order was shifted by one (0 is the metal
    strand, not red).
    """
    metal = C.metal_colour()
    return {
        "black": metal,
        "red": C.mix((255, 0, 0), metal, 0.5),
        "blue": C.mix((0, 0, 255), metal, 0.5),
    }


def draw_cord(painter: QPainter, state: RenderState) -> None:
    spine = state.cord
    dark = C.metal_colour()
    # og.cs:1009-1010  the plate colours are not free constants either:
    #     part 0 = Lerp(armJointGraphics[0].BaseColor(default), metalColor, 0.5f)
    #     part 1 = Lerp(armJointGraphics[0].HighLightColor(default), metalColor, 0.35f)
    # so they are the SAME two colours the arm joints use. (146,136,134) was an
    # invented mid-grey; the source derives both from HSL(0.025, .1) with the
    # room's darkness, which constants.arm_base_colour already implements.
    band = C.mix(C.arm_base_colour(), dark, 0.5)
    highlight = C.mix(C.arm_highlight_colour(), dark, 0.35)

    # 1. the cord body: one 2.4 px ribbon (og.cs:961 num = 1.2f, a half-width)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(qc(dark, 255)))
    painter.drawPath(_ribbon(spine, 1.2))

    # 2. chitin plates: 0.5/0.3 then 0.4/0.15, exactly og.cs:947-950 -> 9 x 4.5 px
    # and 7.2 x 2.25 px ribs on a 10 px pitch (see _plates for the axis proof).
    # Opaque: the source never sets .alpha on those 160 sprites.
    _plates(painter, spine, band, *C.CORD_PLATE_SCALE)
    _plates(painter, spine, highlight, *C.CORD_PLATE2_SCALE)

    # og.cs:1012-1026  the three strand colours are derived from metalColor --
    # see filament_palette() for the maths and for what used to be wrong.
    filament_colours = filament_palette()
    painter.setPen(Qt.PenStyle.NoPen)
    # og.cs:988  num3 = 0.5f -- the strand ribbon is 1 px wide, not the cord's 2.4.
    # Alpha stays 255: the source meshes are opaque.
    for index, filament in enumerate(state.filaments):
        colour = filament_colours.get(state.filament_colours[index], filament_colours["black"])
        painter.setBrush(QBrush(qc(colour, 255)))
        painter.drawPath(_ribbon(filament, C.SMALL_CORD_HALF_WIDTH))


# --------------------------------------------------------------------------
# robe
# --------------------------------------------------------------------------
def draw_gown(painter: QPainter, state: RenderState) -> None:
    """Fill the robe the way ``MakeGridMesh("Futile_White", divs-1)`` + vertex colours does.

    ``sprites[robeSprite].verticeColors[j * divs + i] = Gown.Color(i / (divs-1))``
    (og.cs:377) against ``MoveVertice(i * divs + j, clothPoints[i][j])``
    (og.cs:394) is a transposed pair, so the colour only depends on ``j``: eleven
    bands running neck -> hem, orange ``Color(0)`` to red ``Color(1)``.

    There is no outline, no centre seam and no hem trim in the source -- the
    texture is a 1x1 white tile and every bit of colour comes from the vertex
    colours -- so those three decorations are gone.
    """
    grid = state.gown
    divs = state.gown_divs
    if len(grid) < 3:
        return

    outline: list[tuple[float, float]] = []
    outline.extend(grid[i][0] for i in range(divs))                  # neck
    outline.extend(grid[divs - 1][j] for j in range(1, divs))        # right seam
    outline.extend(grid[i][divs - 1] for i in range(divs - 2, -1, -1))  # hem
    outline.extend(grid[0][j] for j in range(divs - 2, 0, -1))       # left seam

    path = QPainterPath()
    # WindingFill, not the default odd-even: a folded cloth polygon can cross
    # itself, and odd-even punches holes where the game's triangle grid does not.
    path.setFillRule(Qt.FillRule.WindingFill)
    path.moveTo(QPointF(*outline[0]))
    for point in outline[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()

    painter.setPen(Qt.PenStyle.NoPen)
    # Underlay in a mid tone: the per-row bands below meet edge to edge, and with
    # antialiasing that can leave a hairline of background at a seam.
    painter.setBrush(QBrush(qc(C.gown_colour(0.5), 255)))
    painter.drawPath(path)

    # Ten bands, one per grid-row pair, each filled with a gradient between that
    # pair's two colours. A single straight gradient cannot follow the rows: the
    # hem bows 6.3 px lower mid-robe than at the seams, and measured against the
    # real vertex colours a straight axis was off by up to 1.54 rows (18.9/255)
    # in the hem corners.
    for j in range(divs - 1):
        band = QPolygonF([QPointF(*grid[i][j]) for i in range(divs)]
                         + [QPointF(*grid[i][j + 1]) for i in range(divs - 1, -1, -1)])
        band_path = QPainterPath()
        band_path.setFillRule(Qt.FillRule.WindingFill)
        band_path.addPolygon(band)
        band_grad = QLinearGradient(QPointF(*grid[divs // 2][j]),
                                    QPointF(*grid[divs // 2][j + 1]))
        band_grad.setColorAt(0.0, qc(C.gown_colour(j / (divs - 1.0)), 255))
        band_grad.setColorAt(1.0, qc(C.gown_colour((j + 1) / (divs - 1.0)), 255))
        painter.setBrush(QBrush(band_grad))
        painter.drawPath(band_path)


# --------------------------------------------------------------------------
# puppet
# --------------------------------------------------------------------------
# og.cs:1546  head = new GenericBodyPart(this, 5f, 0.5f, 0.995f, firstChunk)
# og.cs:1986  HeadSprite scale (rad/9, rad/11) -> 11.11 x 9.09 px
HEAD_RAD = 5.0
HAND_HALF_W = 4.0       # og.cs:2197  num4 = IsPebbles ? 4f : 2f   (hand half width)
FOOT_HALF_W = 2.0       # og.cs:2224  num4 = IsPebbles ? 2f : ...


def _limb_band(painter: QPainter, start, tip, half_w: float, colour,
               ctrl_a=None, ctrl_b=None) -> None:
    """``TriangleMesh.MakeLongMesh(7)``: a 7-ring bezier band, filled not stroked.

    A ``QPen`` of width ``half_w`` draws a 2*half_w line; the mesh's vertices are
    at +-half_w, so a stroked limb was half the width the game gives it. Filling
    the strip is what makes a 4 px leg read as a limb instead of a hairline.
    """
    if ctrl_a is None:
        ctrl_a = (start[0] + (tip[0] - start[0]) * 0.15,
                  start[1] + (tip[1] - start[1]) * 0.15)
    if ctrl_b is None:
        ctrl_b = (tip[0] + (start[0] - tip[0]) * 0.15,
                  tip[1] + (start[1] - tip[1]) * 0.15)
    points = [_bezier(start, ctrl_a, ctrl_b, tip, n / 6.0) for n in range(7)]
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(qc(colour, 255)))
    painter.drawPath(_ribbon(points, half_w))


def draw_puppet_body(painter: QPainter, state: RenderState) -> None:
    """Two Circle20 chunks and two MakeLongMesh(7) legs with haloGlyph feet."""
    skin = C.SKIN_COLOUR
    body_sprite = tinted("Circle20", skin)
    for centre in (state.body0, state.body1):
        painter.drawPixmap(QRectF(centre[0] - 6.0, centre[1] - 6.0, 12.0, 12.0), body_sprite, QRectF(body_sprite.rect()))

    cap = tinted("haloGlyph-1", skin)
    # og.cs:1648/1770  both the foot force and the knee are built from
    # PerpendicularVector(DirVec(firstChunk, bodyChunks[1])) with the SAME
    # `(j == 0) ? -1 : +1` factor, so the knee always bows to its own foot's
    # side. Using perp-of-UP here instead (the mirror) is what made the legs
    # bow inwards and cross the body axis.
    # og.cs:1518  neckSprite sits between bodyChunks and feet in the sprite order,
    # so it is drawn here -- before the legs, the robe and the hands. Drawing it
    # inside draw_puppet_head put the pink neck on top of the orange robe.
    painter.setPen(QPen(qc(skin), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    painter.drawLine(QPointF(*state.body0), QPointF(*state.head))

    down = C.dir_vec(state.body0, state.body1)
    perp = (down[1], -down[0])          # y-down twin of Custom.PerpendicularVector
    for index, tip in enumerate(state.feet):
        sign = -1.0 if index == 0 else 1.0
        mid = ((tip[0] + state.body1[0]) * 0.5, (tip[1] + state.body1[1]) * 0.5)
        knee = (mid[0] + perp[0] * C.KNEE_BOW * sign,
                mid[1] + perp[1] * C.KNEE_BOW * sign)
        # og.cs:2214  cB = Lerp(foot, knee, 0.9), cA = Lerp(bodyChunks[1], knee, 0.9)
        ctrl_a = (state.body1[0] + (knee[0] - state.body1[0]) * 0.9,
                  state.body1[1] + (knee[1] - state.body1[1]) * 0.9)
        ctrl_b = (tip[0] + (knee[0] - tip[0]) * 0.9,
                  tip[1] + (knee[1] - tip[1]) * 0.9)
        # og.cs:1962-1963  FootSprite(m,0) is the haloGlyph cap and FootSprite(m,1)
        # the mesh, and the cap has the LOWER index, so it is drawn first.
        painter.drawPixmap(QRectF(tip[0] - 3.0, tip[1] - 3.0, 6.0, 6.0),
                           cap, QRectF(cap.rect()))
        _limb_band(painter, state.body1, tip, FOOT_HALF_W, skin, ctrl_a, ctrl_b)


def draw_puppet_hands(painter: QPainter, state: RenderState) -> None:
    """Hands sit IN FRONT of the gown: firstHandSprite follows robeSprite."""
    skin = C.SKIN_COLOUR
    cap = tinted("haloGlyph-1", skin)
    up = C.dir_vec(state.body1, state.body0)
    perp = (-up[1], up[0])
    for index, tip in enumerate(state.hands):
        side = -1.0 if index == 0 else 1.0
        anchor = (state.body0[0] + perp[0] * 4.0 * side,
                  state.body0[1] + perp[1] * 4.0 * side)
        # og.cs:2192  cB = hand + DirVec(hand, attach) * 3 + up
        d = C.dir_vec(tip, anchor)
        ctrl_b = (tip[0] + d[0] * 3.0 + up[0], tip[1] + d[1] * 3.0 + up[1])
        # og.cs:2193  cA = attach + perp * (5 * sign)
        ctrl_a = (anchor[0] + perp[0] * 5.0 * side,
                  anchor[1] + perp[1] * 5.0 * side)
        # og.cs:1544-1545  HandSprite(l,0) is the haloGlyph cap and HandSprite(l,1)
        # the sleeve, so the cap has the lower index and is painted first.
        painter.drawPixmap(QRectF(tip[0] - 3.0, tip[1] - 3.0, 6.0, 6.0),
                           cap, QRectF(cap.rect()))
        _limb_band(painter, anchor, tip, HAND_HALF_W, C.gown_colour(0.0), ctrl_a, ctrl_b)


def face_look(look_world, axis):
    """``OracleGraphics.RelativeLookDir`` -- og.cs:1486, transcribed literally.

    ``RotateAroundOrigo(Vector2.Lerp(lastLookDir, lookDir, t),
                       0f - AimFromOneVectorToAnother(b1, b0))``

    The two helpers, from IL:

    * ``VecToDeg(v)`` is ``Mathf.Atan2(v.x, v.y) * 180/pi`` -- the **compass**
      angle (0 = +y), because the call loads ``x`` first and ``Atan2`` takes
      ``(y, x)``. Reading it as ``atan2(y, x)`` is what made this port believe the
      source was self-contradictory and try to "fix" it with a projection; that
      projection differs from the source by a sign on the along-body component,
      which pointed the eyes backwards when looking up or down.
    * ``RotateAroundOrigo(v, d)`` multiplies ``d`` by ``-pi/180`` and applies the
      standard ``[[c,-s],[s,c]]`` matrix, i.e. it rotates by ``-d``.

    ``axis`` is the screen-space ``vector6 = DirVec(head, body0)``; the game frame
    is +y up, so every vector is converted on the way in and back on the way out.
    """
    axis_g = (axis[0], -axis[1])
    up_g = (-axis_g[0], -axis_g[1])
    aim = degrees(atan2(up_g[0], up_g[1]))          # Custom.VecToDeg(b0 - b1)
    look_g = (look_world[0], -look_world[1])
    rad = radians(aim)                              # RotateAroundOrigo(v, -aim)
    c, s = cos(rad), sin(rad)
    # NOTE: the components are returned in the GAME frame and must stay that way.
    # og.cs:2166 consumes them as coefficients of vector7/vector6 and `1-look.y*3`
    # is a game-frame expression; mirroring y back here would flip the along-body
    # term, which put the eyes 4 px DOWN when the iterator looked up (source: 2 px
    # up). Only the vectors multiplied in are screen-space.
    return (c * look_g[0] - s * look_g[1], s * look_g[0] + c * look_g[1])


def eye_offset(look, perp, axis, index):
    """og.cs:2166 -- one eye's offset from the head centre, and its scaleX.

    ``vector10 = head + vector7 * clamp(look.x*3 + 2.5*sign, -5, 5)
                       + vector6 * (1 - look.y*3)``
    and ``vector10``'s width is ``og.cs:2168``'s scaleX on the 1x1 ``pixel``.
    """
    sign = -1.0 if index == 0 else 1.0
    lateral = max(-5.0, min(5.0, look[0] * 3.0 + 2.5 * sign))
    forward = 1.0 - look[1] * 3.0
    widen = (_inverse_lerp(-1.0, -0.5, look[0]) if index == 0
             else _inverse_lerp(1.0, 0.5, look[0]))
    width = C.EYE_WIDTH_MIN + widen * (C.EYE_WIDTH_MAX - C.EYE_WIDTH_MIN)
    return ((perp[0] * lateral + axis[0] * forward,
             perp[1] * lateral + axis[1] * forward), width)


def draw_puppet_head(painter: QPainter, state: RenderState) -> None:
    """Everything the source positions in OracleGraphics.DrawSprites."""
    axis = C.dir_vec(state.head, state.body0)      # vector6 = DirVec(head, body)
    perp = (axis[1], -axis[0])                     # vector7, mirrored for +y down
    look = face_look(state.look_dir, axis)         # vector8
    # og.cs:2159  HeadSprite.rotation = Custom.VecToDeg(DirVec(head, firstChunk)).
    # VecToDeg's IL is Atan2(x, y) (compass: 0 = +y), so with the game vector
    # (axis.x, -axis.y) this is degrees(atan2(axis[0], -axis[1])). For a hanging
    # body that is 180 deg, which leaves the 11.1 px local x axis horizontal --
    # the source head really is a WIDE ellipse at rest. The previous
    # atan2(y, x) misreading rotated it a quarter turn and drew a tall egg.
    angle = degrees(atan2(axis[0], -axis[1]))

    # PhoneSprite(side, 0/1): Circle20 at +-clamp(Lerp(7,5,|look.x|)*sign, -11, 11)
    pod_w = C._lerp(3.5, 5.0, abs(look[0]))
    for sign in (-1.0, 1.0):
        offset = max(-11.0, min(11.0, C._lerp(7.0, 5.0, abs(look[0])) * sign))
        cx = state.head[0] + perp[0] * offset
        cy = state.head[1] + perp[1] * offset
        for part, scale in ((0, 1.0), (1, 0.8)):
            pod = tinted("Circle20", (58, 52, 56) if part == 0 else (32, 28, 32))
            w = pod_w * scale
            h = 5.5 * scale
            painter.drawPixmap(QRectF(cx - w * 0.5, cy - h * 0.5, w, h), pod, QRectF(pod.rect()))
        # PhoneSprite(side, 2): LizardScaleA1 aimed from the body outward
        antenna = tinted("LizardScaleA1", (40, 34, 38))
        # og.cs:2185  Aim(..., vector11 - vector6*40f - vector4*10f) where
        # vector4 is the WORLD look (og.cs:2060), not the body-frame perp.
        tip = (cx - axis[0] * 40.0 - state.look_dir[0] * 10.0,
               cy - axis[1] * 40.0 - state.look_dir[1] * 10.0)
        aim = atan2(state.body0[1] - tip[1], state.body0[0] - tip[0]) * 180.0 / pi + 90.0
        painter.save()
        painter.translate(QPointF(cx, cy))
        painter.rotate(aim)
        painter.drawPixmap(QRectF(-1.5, 0.0, 3.0, 11.2), antenna, QRectF(antenna.rect()))
        painter.restore()

    # HeadSprite: Circle20 at (rad/9, rad/11), rotated with the body axis
    head_w = 20.0 * HEAD_RAD / 9.0
    head_h = 20.0 * HEAD_RAD / 11.0
    sprite = tinted("Circle20", C.SKIN_COLOUR)
    painter.save()
    painter.translate(QPointF(*state.head))
    painter.rotate(angle)
    painter.drawPixmap(QRectF(-head_w * 0.5, -head_h * 0.5, head_w, head_h), sprite, QRectF(sprite.rect()))
    painter.restore()

    # ChinSprite: Circle20 at rad/15, at Lerp(head, body, 0.15) + perp * look.x * 2
    chin = 20.0 * HEAD_RAD / 15.0
    chin_c = (
        state.head[0] + (state.body0[0] - state.head[0]) * 0.15 + perp[0] * look[0] * 2.0,
        state.head[1] + (state.body0[1] - state.head[1]) * 0.15 + perp[1] * look[0] * 2.0,
    )
    painter.drawPixmap(QRectF(chin_c[0] - chin * 0.5, chin_c[1] - chin * 0.5, chin, chin), sprite, QRectF(sprite.rect()))

    # EyeSprite: 1x1 `pixel` at head + perp*clamp(look.x*3 + 2.5*sign, -5, 5)
    # + axis*(1 - look.y*3), rotated onto the body axis. og.cs:2168 sets
    # scaleX = 1 + InverseLerp(...) + (1 - eyesOpen), og.cs:2169 sets
    # scaleY = Lerp(1, IsPebbles ? 2 : 3, eyesOpen): with the eyes open the blit
    # is 1..2 px wide and exactly 2 px tall. The near-black (0.02, 0, 0) at
    # og.cs:1948 is Moon's branch; Pebbles keeps the FSprite default, white.
    eye = tinted("pixel", C.EYE_COLOUR)
    for index in (0, 1):
        offset, width = eye_offset(look, perp, axis, index)
        ex = state.head[0] + offset[0]
        ey = state.head[1] + offset[1]
        painter.save()
        painter.translate(QPointF(ex, ey))
        painter.rotate(angle)
        painter.drawPixmap(
            QRectF(-width * 0.5, -C.EYE_SCALE_Y * 0.5, width, C.EYE_SCALE_Y),
            eye, QRectF(eye.rect()))
        painter.restore()


# --------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------
def draw_kill(painter: QPainter, state: RenderState) -> None:
    """五块卵石的"杀鼠标"表现（docs/PLAN-kill.md §3.3）。

    照 `og.cs:2096-2098`：`f = Lerp(lastKillFac, killFac, ts)`（我们的插值已经做了），
    `scale = Lerp(200, 2, sqrt(f))`、`alpha = f**3`；位置在**光标**上（源码是玩家）。
    半径用**腔室对角线**（仿真像素）而不是屏幕像素 —— `draw_scene` 已经 `painter.scale(scale)`，
    用屏幕像素会让四档尺寸差 3.75 倍。
    """
    if state.kill_phase in ("idle", "cooldown") and state.kill_fac <= 0.001:
        return
    anchor = state.kill_anchor
    if anchor is None:
        return
    bounds = state.bounds
    diagonal = 1.0
    if bounds is not None:
        diagonal = max(1.0, (bounds[2] ** 2 + bounds[3] ** 2) ** 0.5)
    f = max(0.0, min(1.0, state.kill_fac))
    # Lerp(200, 2, sqrt(f)) 换算成"腔室对角线的倍数"：200 对应 1.05 个对角线，
    # 2 对应 ~8 仿真 px（与 PLAN-kill §3.3 一致）
    end_radius = 8.0
    radius = (1.05 * diagonal) * (1.0 - f ** 0.5) + end_radius * f ** 0.5
    # 用户 2026-10-29：太暗，至少两倍 → alpha = f^3 * GAIN（再夹 0.85 免得过曝）
    alpha = min(0.85, (f ** 3) * C.KILL_GLOW_ALPHA_GAIN)
    if alpha <= 0.002:
        return
    cx, cy = anchor
    grad = QRadialGradient(QPointF(cx, cy), max(1.0, radius))
    for stop, dim in ((0.0, 1.0), (0.55, 0.55), (1.0, 0.0)):
        grad.setColorAt(stop, QColor(255, 255, 255, int(255 * C.KILL_FLASH_ALPHA * alpha * dim)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawEllipse(QPointF(cx, cy), radius, radius)

    # 白闪：**径向**闪（不是整屏白矩形）。审计意见：整屏 0.85 alpha 会盖住用户正在看的一切；
    # 这里从光标向外扩散一圈 0.75 对角线的白光，随 flash 进度 1→0 淡出。
    if state.kill_flash > 0.001:
        flash_r = 0.75 * diagonal
        fgrad = QRadialGradient(QPointF(cx, cy), max(1.0, flash_r))
        base_alpha = int(255 * C.KILL_FLASH_ALPHA * state.kill_flash)
        for stop, dim in ((0.0, 1.0), (0.5, 0.45), (1.0, 0.0)):
            fgrad.setColorAt(stop, QColor(255, 255, 255, int(base_alpha * dim)))
        painter.setBrush(QBrush(fgrad))
        painter.drawEllipse(QPointF(cx, cy), flash_r, flash_r)

    # 20 颗白 spark（数据在仿真侧，长度会变所以不插值）
    for spark in state.kill_sparks:
        life = max(0.0, min(1.0, spark[4] / max(1.0, spark[5])))
        painter.setBrush(QColor(255, 255, 255, int(255 * life)))
        painter.drawEllipse(QPointF(spark[0], spark[1]), 1.6, 1.6)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def draw_scene(
    painter: QPainter,
    state: RenderState,
    scale: float = 1.0,
    sprites=None,
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Sprite blits must NOT be smoothed: the atlas tiles are 15-20 px, and
    # resampling them twice (down to world px, up by `scale`) is what made the
    # cord look out of focus.
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    painter.save()
    painter.scale(scale, scale)

    # 层序：光环在 BackgroundShortcuts(=1)，轨道/机械臂在 Midground(3)/Shortcuts(8)，
    # 所以光环必须**先**画、被轨道盖住（og.cs:2018 的 ArmBase 层）。
    draw_halo(painter, state)
    draw_rail(painter, state)
    draw_arm(painter, state)
    draw_cord(painter, state)
    # Layer order matters: the robe must cover the lower body, the head sits on top.
    draw_puppet_body(painter, state)
    draw_gown(painter, state)
    draw_puppet_hands(painter, state)
    draw_puppet_head(painter, state)
    # "杀鼠标"的白光/白闪/spark 必须在**所有东西之上**（og.cs:2048 挂在 Shortcuts 容器）
    draw_kill(painter, state)

    painter.restore()
