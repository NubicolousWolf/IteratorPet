"""命中测试：给定屏幕上**真正画出去**的那份 RenderState，判断点到了什么。

纯函数，不碰 Qt、不碰仿真，方便单测。阈值是**屏幕像素**（`constants.DRAG_HIT_*_PX`），
这里按 `scale` 换成仿真像素再比较 —— 四档尺寸下都要点得中。
"""

from __future__ import annotations

from math import hypot

from ..simulation import constants as C


def _near(point, target, limit_px: float, scale: float) -> bool:
    limit = limit_px / max(0.2, float(scale))
    return hypot(point[0] - target[0], point[1] - target[1]) <= limit


def hit_test(state, point, scale: float) -> str | None:
    """返回 ``"base" | "head" | "body" | None``。``point`` 是仿真坐标。"""
    if state is None or point is None:
        return None
    # 底座最外层、面积最小，先判它，免得贴着滑车时抓不到底座
    if _near(point, state.base_point, C.DRAG_HIT_BASE_PX, scale):
        return "base"
    if _near(point, state.head, C.DRAG_HIT_HEAD_PX, scale):
        return "head"
    for body in (state.body0, state.body1):
        if _near(point, body, C.DRAG_HIT_BODY_PX, scale):
            return "body"
    return None
