"""``Custom.LerpAndTick`` 与 ``Custom.SCurve`` 的逐字复刻。

两个都是 Rain World 的通用缓动原语，光环的三个缓动量（expand / push / white）
和每圈旋转全靠它们。数值已按 IL 逐条核过（见 ``docs/PLAN-halo.md`` §2.4 / §2.6）。
"""

from __future__ import annotations


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_and_tick(value: float, target: float, rate: float, tick: float) -> float:
    """``LerpAndTick``：先按比例靠拢，但至少走 ``tick``，最多正好落到目标。

    ```csharp
    v = Lerp(from, to, lerp);
    v = (v < to) ? Min(v + tick, to) : Max(v - tick, to);
    ```
    """
    v = lerp(value, target, rate)
    if v < target:
        return min(v + tick, target)
    return max(v - tick, target)


def s_curve(x: float, k: float = 0.5) -> float:
    """``SCurve(x, k)``：k=0.5 时对称，S(0.25)=0.125、S(0.75)=0.875。"""
    if x < 0.5:
        t = 2.0 * x
        return 0.5 * k * t / (k - t + 1.0)
    t = 2.0 * x - 1.0
    flipped = -1.0 - k
    return 0.5 + 0.5 * flipped * t / (flipped - t + 1.0)
