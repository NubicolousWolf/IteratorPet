"""Print the arithmetic that decides whether the puppet's legs can be seen.

Everything is re-derived from the source formulas rather than read back out of
the renderer, so the script doubles as a cross-check of ``simulation/constants``:

  og.cs:332  Gown.IdealPosForPoint(x, y, bodyPos, dir, perp)
             num = InverseLerp(0, divs-1, x)      -- across the robe
             t   = InverseLerp(0, divs-1, y)      -- down the robe
             a = 5f; num2 = 11f
             bodyPos
               + perp * (Lerp(-1, 1, num) * Lerp(a, num2, t))
               + dir  * (Lerp(a, -(num2 + 7), t) * (1 + sin(pi*num) * 0.35 * Lerp(-1, 1, t)))

  og.cs:1643 feet[j].ConnectToPoint(bodyChunks[1].pos, IsMoon ? 20f : 10f, ...)
  og.cs:1648 feet[j].vel += DirVec(firstChunk, bodyChunks[1]) * 0.3f
  og.cs:1649 feet[j].vel += PerpendicularVector(same) * (0.15f * ((j==0) ? -1f : 1f))
             -> the foot settles on the leash radius along that net force.

  og.cs:1770 knees[j,0] = (feet[j].pos + bodyChunks[1].pos) / 2
                          + PerpendicularVector(DirVec(firstChunk, bodyChunks[1]))
                            * (4f * ((j == 0) ? -1f : 1f))

Frame: this file uses the GAME's frame, +x right and **+y UP**, so
``PerpendicularVector(v) = (-v.y, v.x)`` as the IL defines it. The live sim runs
with +y DOWN and mirrors the helper to (v.y, -v.x); that is noted where it
matters instead of being silently mixed in.

Run:  python tools/leg_visibility.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iterator_pet.simulation import constants as C  # noqa: E402

DIVS = 11          # og.cs:275  private int divs = 11
A = 5.0            # og.cs:336  float a = 5f
NUM2 = 11.0        # og.cs:337  float num2 = 11f (hem half-width)
DROP = -18.0       # og.cs:338  0f - (num2 + 7f)
TOL = 9.0          # og.cs:308  num2 = Lerp(0f, 9f, t)


def perp(v: tuple[float, float]) -> tuple[float, float]:
    """``Custom.PerpendicularVector``: IL is Normalize then (-y, x), +y up."""
    d = math.hypot(v[0], v[1]) or 1.0
    return (-v[1] / d, v[0] / d)


def ideal(x: int, y: int, body: tuple[float, float],
          direction: tuple[float, float]) -> tuple[float, float]:
    num = x / (DIVS - 1)
    t = y / (DIVS - 1)
    p = perp(direction)
    lateral = (-1.0 + 2.0 * num) * (A + (NUM2 - A) * t)
    drop = (A + (DROP - A) * t) * (1.0 + math.sin(math.pi * num) * 0.35 * (-1.0 + 2.0 * t))
    return (body[0] + p[0] * lateral + direction[0] * drop,
            body[1] + p[1] * lateral + direction[1] * drop)


def gown_outline(body: tuple[float, float],
                 direction: tuple[float, float]) -> list[tuple[float, float]]:
    """The mesh's own boundary: neck row, right seam, hem row, left seam."""
    outline = [ideal(i, 0, body, direction) for i in range(DIVS)]
    outline += [ideal(DIVS - 1, j, body, direction) for j in range(1, DIVS)]
    outline += [ideal(i, DIVS - 1, body, direction) for i in range(DIVS - 2, -1, -1)]
    outline += [ideal(0, j, body, direction) for j in range(DIVS - 2, 0, -1)]
    return outline


def inside(poly: list[tuple[float, float]], point: tuple[float, float]) -> bool:
    """Even-odd ray cast -- a real containment test, not a height comparison."""
    x, y = point
    hit = False
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        if (y0 > y) != (y1 > y):
            xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xc > x:
                hit = not hit
    return hit


def main() -> int:
    gap = C.BODY_CONNECTION
    c0 = (0.0, 0.0)                    # upper chunk / firstChunk
    c1 = (0.0, -gap)                   # lower chunk, below c0 in a +y-up frame
    direction = (0.0, 1.0)             # DirVec(chunk1, chunk0) -- up
    outline = gown_outline(c0, direction)

    print("frame: +x right, +y UP (game frame, not the sim's)")
    print(f"body radius     {C.BODY_RADIUS:5.2f}   Circle20 * rad/10 -> {2 * C.BODY_RADIUS:.0f} px sprite")
    print(f"chunk gap       {gap:5.2f}   chunk0 at y=0, chunk1 at y={c1[1]:+.2f}")
    print(f"robe            half width {A:.0f} -> {NUM2:.0f}, drop +{A:.0f} .. {DROP:.0f}"
          f" (i.e. {A:.0f} px above chunk0 down to {-DROP:.0f} px below)")
    print(f"foot tether     {C.FOOT_TETHER:.1f} px   (og.cs:1643, Pebbles = !IsMoon)")
    print()

    down = (c1[0] - c0[0], c1[1] - c0[1])
    dist = math.hypot(*down)
    down = (down[0] / dist, down[1] / dist)
    p_down = perp(down)
    print(f"DirVec(chunk0, chunk1)  = ({down[0]:+.3f}, {down[1]:+.3f})")
    print(f"PerpendicularVector     = ({p_down[0]:+.3f}, {p_down[1]:+.3f})")

    hem_mid = ideal(DIVS // 2, DIVS - 1, c0, direction)
    hem_seam = ideal(0, DIVS - 1, c0, direction)
    print(f"hem: seam y {hem_seam[1]:+.2f}, mid y {hem_mid[1]:+.2f}"
          f"  -> bows {abs(hem_mid[1] - hem_seam[1]):.2f} px lower mid-robe")
    print()

    worst_lift = 0.0
    for index, label in ((0, "j=0"), (1, "j=1")):
        sign = -1.0 if index == 0 else 1.0
        net = (down[0] * 0.3 + p_down[0] * 0.15 * sign,
               down[1] * 0.3 + p_down[1] * 0.15 * sign)
        mag = math.hypot(*net)
        foot = (c1[0] + net[0] / mag * C.FOOT_TETHER,
                c1[1] + net[1] / mag * C.FOOT_TETHER)
        knee_mid = ((foot[0] + c1[0]) * 0.5, (foot[1] + c1[1]) * 0.5)
        knee = (knee_mid[0] + p_down[0] * C.KNEE_BOW * sign,
                knee_mid[1] + p_down[1] * C.KNEE_BOW * sign)
        side = "left" if foot[0] < 0 else "right"

        # how far the cloth must ride up before this foot is outside the robe
        lift = 0.0
        while lift < TOL + 4.0:
            moved = [(x, y + lift) for x, y in outline]
            if not inside(moved, foot):
                break
            lift += 0.05
        hidden = inside(outline, foot)
        worst_lift = max(worst_lift, lift)

        print(f"foot {label} ({side:5s})")
        print(f"  unit accel      ({net[0] / mag:+.4f}, {net[1] / mag:+.4f})")
        print(f"  position        ({foot[0]:+.3f}, {foot[1]:+.3f})"
              f"   = {C.FOOT_TETHER - C.BODY_RADIUS:.2f} px beyond the body rim")
        print(f"  knee            ({knee[0]:+.3f}, {knee[1]:+.3f})"
              f"   bows to the {'same' if (knee[0] < 0) == (foot[0] < 0) else 'OPPOSITE'}"
              f" side as its foot")
        print(f"  inside the robe? {'YES -> hidden at rest' if hidden else 'no -> visible'}")
        print(f"  cloth lift needed to expose it: {lift:.2f} px"
              f"  (tolerance budget at the hem = {TOL:.1f} px radial)")
        print()

    print(f"worst case: the robe must swing up {worst_lift:.2f} px for a foot to appear.")
    print("The tolerance is radial, so that lift is reachable -- but the only bias in")
    print("Gown.Update (og.cs:306) points down, so at rest the cloth sits *lower* than")
    print("the ideal and the feet stay covered. The legs are a flash, not a feature.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
