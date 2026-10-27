"""Standalone portrait of the Five Pebbles puppet, built 1:1 from OracleGraphics.

Every geometry constant carries the decompiled line it came from. Methods that
are not in the three decompiled .cs files were read out of Assembly-CSharp.dll
with a Mono.Cecil IL dump (the ildump project under .tmp/ildump).

  og.cs:1546  head = new GenericBodyPart(this, 5f, 0.5f, 0.995f, firstChunk)
  IL          GenericBodyPart::.ctor(ow, rd, sfFric, aFric, con) -> rad 5,
              surfaceFric .5, airFriction .995, Reset(con.pos)
  IL          GenericBodyPart::Update = pos += vel; vel *= airFriction;
              PushOutOfTerrain(room, connection.pos)        <-- NO gravity
  og.cs:1613  head.ConnectToPoint(chunk0 + DirVec(chunk1, chunk0) * 6f, 8f,
              push: true, 0f, chunk0.vel, 0.5f, 0.01f)
  IL          BodyPart::ConnectToPoint with push=true skips the distance test and
              forces |pos - pnt| == connectionRad   <-- a weld, not a leash
  og.cs:1638  head.vel += DirVec(chunk1, chunk0) * 0.75f;  head.vel.y -= 0.7f
              (net +0.05/frame along the body axis -> the head floats to the top
               of that sphere: chunk0 + up * (6 + 8) = up * 14)
  og.cs:1649  hands[j].ConnectToPoint(chunk0.pos, 15f, push: false, ...)
              hands[j].vel.y -= 0.5f; vel += downAxis * 0.3f; vel += perp * 0.3f
  og.cs:1640  feet[j].ConnectToPoint(chunk1.pos, IsMoon ? 20f : 10f, push: false)
              feet[j].vel += downAxis * 0.3f; vel += perp * 0.15f

  oracle.cs:616  bodyChunks[i] = new BodyChunk(this, i, pos, 6f, 0.5f)  (2 chunks)
  oracle.cs:642  BodyChunkConnection(m, m+1, 9f, Normal, 1f, 0.5f)
  og.cs:1909  body chunk sprite = Circle20, scale = rad / 10f  -> 12 px circles
  og.cs:1938  neckSprite = pixel, scaleX = IsPebbles ? 3f : 4f, anchorY = 0
  og.cs:1986  HeadSprite = Circle20, scaleX = rad/9, scaleY = rad/11
  og.cs:1988  ChinSprite = Circle20, scale = rad/15
  og.cs:2166  eye pos = head + perp*clamp(look.x*3 + 2.5*sign, -5, 5) + axis*(1-look.y*3)
  og.cs:2168  eye scaleX = 1 + InverseLerp(...) + (1 - eyesOpen); scaleY = Lerp(1, 2, eyesOpen)
  og.cs:2174  phone pos = head + perp*clamp(Lerp(7, 5, |look.x|)*sign, -11, 11)
  og.cs:2178  phone scaleY = 5.5/20, scaleX = Lerp(3.5, 5, |look.x|)/20
  og.cs:1952  phone stalk = LizardScaleA1, anchorY = 0, scaleY = 0.8, scaleX = +-0.75
  og.cs:2187  hand attach = bodyChunk0 + perp * 4 * sign
  og.cs:2197  hand half width = IsPebbles ? 4f : 2f
  og.cs:2203  ring perpendicular taken from the running segment, not the body axis
  og.cs:2219  foot half width = 2f (IsPebbles)
  og.cs:2292  SkinColor() IsPebbles -> new Color(1f, 0.4f, 0.796f)
  og.cs:349   Gown::Color(f) IsPebbles -> HSL2RGB(Lerp(.08,.02,f^2), Lerp(1,.8,f), .5)
  og.cs:333   Gown::IdealPosForPoint -> a = 5, b = 11, drop = Lerp(a, -(b+7), t)
  og.cs:2343  hand verticeColors = gown.Color(0.4) / gown.Color(0f)

Sprite ORDER is sprite-index order, so it is also the draw order (og.cs:1516-1544):
  umbilical < bodyChunks(2) < neck < feet(4) < halo < robe < hands(4) < head(10) < fade
  -> the neck and both feet are BEHIND the gown; the hands are in front of it.

Run:  python tools/puppet_portrait.py [--out puppet-portrait.png]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QPen,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPolygonF,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iterator_pet.assets_extractor import ensure_sprites  # noqa: E402

# ---------------------------------------------------------------- constants

BODY_R = 6.0            # oracle.cs:616 BodyChunk radius
BODY_GAP = 9.0          # oracle.cs:642 connection distance
HEAD_RAD = 5.0          # og.cs:1546
HEAD_ANCHOR = 6.0       # og.cs:1613 anchor = chunk0 + up * 6
HEAD_TETHER = 8.0       # og.cs:1613 weld radius (push: true)
HAND_LEASH = 15.0       # og.cs:1650
FOOT_LEASH = 10.0       # og.cs:1643  IsMoon ? 20f : 10f -- Pebbles takes the 10
NECK_W = 3.0            # og.cs:1939 IsPebbles
DIVS = 11               # og.cs:274 Gown.divs
GOWN_A = 5.0            # og.cs:333
GOWN_B = 11.0
GOWN_DROP = 7.0         # og.cs:333 dir * Lerp(a, -(b + 7), t)
HAND_HALF = 4.0         # og.cs:2197
FOOT_HALF = 2.0         # og.cs:2224  num4 = IsPebbles ? 2f : ...
KNEE_BOW = 4.0          # og.cs:1770  perp * (4f * sign); 1788/5f is Moon

SKIN = QColor(255, 102, 203)          # og.cs:2292 (1, .4, .796)
# og.cs:1943-1949 -- `new FSprite("pixel")`; the near-black (0.02, 0, 0) is
# assigned only for IsMoon / IsPastMoon / IsStraw, and ApplyPalette never
# touches the eye sprites. `FSprite::.ctor` IL_0001..0006 stores
# `Futile::white` into `_color`, so Five Pebbles' eyes are WHITE.
EYE = QColor(255, 255, 255)
EYE_H = 2.0                           # og.cs:2169  scaleY = Lerp(1, 2, eyesOpen)
DARKNESS = 0.45                       # matches simulation/constants.ROOM_DARKNESS


def hsl2rgb(h: float, s: float, l: float) -> QColor:
    """Unity Custom.HSL2RGB."""
    h = h % 1.0
    if s <= 0.0:
        v = round(l * 255)
        return QColor(v, v, v)
    q = l * (1.0 + s) if l < 0.5 else l + s - l * s
    p = 2.0 * l - q
    out = []
    for t in (h + 1.0 / 3.0, h, h - 1.0 / 3.0):
        t %= 1.0
        if t < 1.0 / 6.0:
            c = p + (q - p) * 6.0 * t
        elif t < 0.5:
            c = q
        elif t < 2.0 / 3.0:
            c = p + (q - p) * (2.0 / 3.0 - t) * 6.0
        else:
            c = p
        out.append(round(max(0.0, min(1.0, c)) * 255))
    return QColor(*out)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _inv_lerp(a: float, b: float, value: float) -> float:
    """``Mathf.InverseLerp``: the IL clamps the ratio to [0, 1]."""
    if b == a:
        return 0.0
    return max(0.0, min(1.0, (value - a) / (b - a)))


def gown_colour(f: float) -> QColor:
    """og.cs:349 Gown::Color for Five Pebbles."""
    f = max(0.0, min(1.0, f))
    return hsl2rgb(lerp(0.08, 0.02, f * f), lerp(1.0, 0.8, f), 0.5)


POD_BASE = hsl2rgb(0.025, 0.4, 0.7 - 0.5 * DARKNESS)          # og.cs:231
POD_HI = hsl2rgb(0.025, 0.5, 0.85 - 0.65 * DARKNESS)          # og.cs:240
HAND_HI = gown_colour(0.0)                                    # og.cs:2344


def bez(p0, p1, p2, p3, t):
    u = 1.0 - t
    x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
    y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
    return (x, y)


def norm(v):
    d = math.hypot(*v)
    return (v[0] / d, v[1] / d) if d else (0.0, 1.0)


def perp(v):
    """Custom.PerpendicularVector: (y, -x) of the normalised vector."""
    n = norm(v)
    return (-n[1], n[0])


# ---------------------------------------------------------------- drawing


class Canvas:
    """World space is Rain World's: +y is up, units are game pixels."""

    def __init__(self, painter: QPainter, ox: float, oy: float, scale: float):
        self.p = painter
        self.ox, self.oy, self.s = ox, oy, scale
        self.p.setRenderHint(QPainter.Antialiasing, True)

    def pt(self, x: float, y: float) -> QPointF:
        return QPointF(self.ox + x * self.s, self.oy - y * self.s)

    def circle(self, cx, cy, rx, ry, colour: QColor):
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(colour)
        self.p.drawEllipse(
            QRectF(
                self.ox + (cx - rx) * self.s,
                self.oy - (cy + ry) * self.s,
                2 * rx * self.s,
                2 * ry * self.s,
            )
        )

    def poly(self, pts, colour: QColor):
        path = QPainterPath()
        for i, (x, y) in enumerate(pts):
            q = self.pt(x, y)
            path.moveTo(q) if i == 0 else path.lineTo(q)
        path.closeSubpath()
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(colour)
        self.p.drawPath(path)

    def strip(self, rings, colour: QColor):
        """TriangleMesh.MakeLongMesh: ring quads joined into one band.

        The union outline and the per-ring quads are drawn as two separate
        paths on purpose. Putting both in one ``WindingFill`` path cancels them:
        the union runs left-down/right-up while the quads run the other way, so
        the winding sums to zero and nothing is painted at all.
        """
        left = [self.pt(*l) for l, _ in rings]
        right = [self.pt(*r) for _, r in rings]
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(colour)

        union = QPainterPath()
        union.setFillRule(Qt.WindingFill)
        union.addPolygon(QPolygonF(left + right[::-1]))
        self.p.drawPath(union)

        quads = QPainterPath()
        quads.setFillRule(Qt.WindingFill)
        for i in range(len(rings) - 1):
            (l0, r0), (l1, r1) = rings[i], rings[i + 1]
            quads.addPolygon(QPolygonF([self.pt(*l0), self.pt(*r0),
                                        self.pt(*r1), self.pt(*l1)]))
        self.p.drawPath(quads)

    def sprite(self, pix, cx, cy, w, h, colour: QColor | None, rot_deg=0.0, smooth=True):
        """w, h are world units (game pixels); the canvas applies its own scale."""
        if pix is None:
            return
        self.p.save()
        self.p.translate(self.pt(cx, cy))
        if rot_deg:
            self.p.rotate(rot_deg)
        self.p.setRenderHint(QPainter.SmoothPixmapTransform, smooth)
        self.p.scale(w * self.s / pix.width(), h * self.s / pix.height())
        if colour is not None:
            tinted = QImage(pix.size(), QImage.Format_ARGB32_Premultiplied)
            tinted.fill(Qt.transparent)
            tp = QPainter(tinted)
            tp.drawImage(0, 0, pix)
            tp.setCompositionMode(QPainter.CompositionMode_SourceIn)
            tp.fillRect(tinted.rect(), colour)
            tp.end()
            self.p.drawImage(QPointF(-pix.width() / 2.0, -pix.height() / 2.0), tinted)
        else:
            self.p.drawImage(QPointF(-pix.width() / 2.0, -pix.height() / 2.0), pix)
        self.p.restore()


def _limb(c: Canvas, attach, tip, ctrl_a, ctrl_b, half, colour, cap=None,
          cap_behind=False, root_half=None, cap_colour=None):
    """One 7-ring TriangleMesh.MakeLongMesh limb (og.cs:2196-2236).

    ``root_half`` reproduces the mesh's start flare: ring 0's trailing vertices
    sit at ``(num7 + num4) / 2`` (og.cs:2229) while every later ring uses
    ``(num4 + num4) / 2`` -- for Pebbles 3 px against 2 px, one segment long.
    """
    # Custom.Bezier(v1, c1, v2, c2, t) runs v1 -> v2 with c1/c2 as controls, i.e.
    # the endpoints are arguments 1 and 3, NOT 1 and 4 (the IL keeps that order).
    pts = [bez(attach, ctrl_a, ctrl_b, tip, n / 6.0) for n in range(7)]
    rings = []
    for n, q in enumerate(pts):
        nxt = pts[min(n + 1, 6)]
        prv = pts[max(n - 1, 0)]
        pv = perp((nxt[0] - prv[0], nxt[1] - prv[1]))
        h = root_half if (root_half is not None and n == 0) else half
        rings.append(((q[0] - pv[0] * h, q[1] - pv[1] * h),
                      (q[0] + pv[0] * h, q[1] + pv[1] * h)))
    if cap is not None and cap_behind:
        # sprite index order == draw order (og.cs:1462): FootSprite(m, 0) is the
        # haloGlyph cap and FootSprite(m, 1) the mesh, so the cap is BEHIND it.
        c.sprite(cap, tip[0], tip[1], 6.0, 6.0, cap_colour or colour, smooth=False)
    c.strip(rings, colour)
    if cap is not None and not cap_behind:  # hands: see the note in draw_puppet
        c.sprite(cap, tip[0], tip[1], 6.0, 6.0, cap_colour or colour, smooth=False)


def draw_puppet(c: Canvas, sprites: dict, look=(0.0, 0.0), show_aura=True,
                show_gown=True, gown_alpha=1.0, gown_grid=None):
    """Draw the puppet once. Origin of the world frame = body chunk 1."""
    look = (max(-1.0, min(1.0, look[0])), max(-1.0, min(1.0, look[1])))
    b1 = (0.0, 0.0)                              # oracle.bodyChunks[1]
    b0 = (0.0, BODY_GAP)                         # oracle.bodyChunks[0] / firstChunk
    head = (0.0, BODY_GAP + HEAD_ANCHOR + HEAD_TETHER)
    up = (0.0, 1.0)                              # DirVec(chunks[1], firstChunk)
    right = perp(up)                             # Custom.PerpendicularVector
    axis = (-up[0], -up[1])                      # DirVec(head, body)

    # --- fadeSprite: Futile_White (1x1), scale 12.5, black, alpha .5, behind all
    if show_aura:
        grad = QRadialGradient(c.pt(*head), 12.5 * 0.5 * c.s)
        grad.setColorAt(0.0, QColor(0, 0, 0, 150))
        grad.setColorAt(0.55, QColor(0, 0, 0, 70))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        c.p.setPen(Qt.NoPen)
        c.p.setBrush(grad)
        c.p.drawEllipse(c.pt(*head), 12.5 * 0.5 * c.s, 12.5 * 0.5 * c.s)

    # --- 1st/2nd sprites: body chunks, Circle20 * rad/10 -> 12 px (og.cs:1909)
    for pos in (b0, b1):
        c.circle(pos[0], pos[1], BODY_R, BODY_R, SKIN)

    # --- 3rd sprite: neck, pixel, scaleX 3, anchorY 0 (og.cs:1938)
    c.poly(
        [
            (b0[0] - NECK_W / 2, b0[1]),
            (b0[0] + NECK_W / 2, b0[1]),
            (head[0] + NECK_W / 2, head[1]),
            (head[0] - NECK_W / 2, head[1]),
        ],
        SKIN,
    )

    # --- 4th..7th: feet -- BEHIND the gown, foot sprites precede robeSprite
    #
    # og.cs:1643  feet[j].ConnectToPoint(bodyChunks[1].pos, 10f, push: false, ...)
    #             push:false means the clamp only fires past the radius; with the
    #             two velocity terms below the foot settles exactly ON it.
    # og.cs:1648  vel += DirVec(firstChunk, bodyChunks[1]) * 0.3   -> down the axis
    # og.cs:1649  vel += PerpendicularVector(same) * (0.15 * sign)
    # No gravity term for Pebbles (that one is guarded by IsMoon), so the net is
    # (0.447, -0.894) * 10 -- NOT straight down.
    #
    # og.cs:1770  the `!IsMoon` (Five Pebbles) branch:
    #   knee = (foot + bodyChunks[1]) / 2 + Perp(DirVec(firstChunk, body1)) * (4 * sign)
    # The 5f and the 0.4 smoothing / 15 px clamp at og.cs:1788-1794 are the Moon
    # branch -- same trap as the 10/20 foot tether above.
    # og.cs:2212  the leg's bezier starts at bodyChunks[1].pos itself; the
    #             +-2 px offset only feeds the segment direction (vector14).
    #
    # Note for whoever reads this next: on 10 px the foot lands 4 px outside the
    # 12 px body sprite, so the leg is a stub. Do NOT "fix" that by borrowing the
    # 20 px from the Moon branch -- that is a different character, and
    # tools/leg_visibility.py exists to print the real numbers instead.
    foot_pix = sprites.get("haloGlyph-1")
    for m in (0, 1):
        sign = -1.0 if m == 1 else 1.0
        foot = (b1[0] + right[0] * (0.447 * FOOT_LEASH * sign),
                b1[1] + right[1] * (0.447 * FOOT_LEASH * sign) - 0.894 * FOOT_LEASH)
        mid = ((foot[0] + b1[0]) * 0.5, (foot[1] + b1[1]) * 0.5)
        knee = (mid[0] + right[0] * KNEE_BOW * sign, mid[1] + right[1] * KNEE_BOW * sign)
        cB = (lerp(foot[0], knee[0], 0.9), lerp(foot[1], knee[1], 0.9))
        cA = (lerp(b1[0], knee[0], 0.9), lerp(b1[1], knee[1], 0.9))
        _limb(c, b1, foot, cA, cB, FOOT_HALF, SKIN,
              cap=foot_pix, cap_behind=True, root_half=FOOT_HALF + 1.0)

    # --- robeSprite: the gown (og.cs:333)
    grid = [[None] * DIVS for _ in range(DIVS)]
    for i in range(DIVS):            # across
        u = i / (DIVS - 1.0)
        for j in range(DIVS):        # down the robe
            t = j / (DIVS - 1.0)
            half = lerp(-1.0, 1.0, u) * lerp(GOWN_A, GOWN_B, t)
            drop = lerp(GOWN_A, -(GOWN_B + GOWN_DROP), t) * (
                1.0 + math.sin(math.pi * u) * 0.35 * lerp(-1.0, 1.0, t)
            )
            grid[i][j] = (b0[0] + right[0] * half + up[0] * drop,
                          b0[1] + right[1] * half + up[1] * drop)
    if gown_grid is not None:
        grid = gown_grid
    if show_gown:
        outline = [grid[i][0] for i in range(DIVS)]
        outline += [grid[DIVS - 1][j] for j in range(1, DIVS)]
        outline += [grid[i][DIVS - 1] for i in range(DIVS - 2, -1, -1)]
        outline += [grid[0][j] for j in range(DIVS - 2, 0, -1)]
        # `verticeColors[j*divs+i] = Color(i/(divs-1))` (og.cs:377) against
        # `MoveVertice(i*divs+j, ...)` (og.cs:394) is a transposed pair, so the
        # colour depends only on j: eleven bands neck -> hem. One stop per row,
        # placed at that row's own height down the centre column -- the hem bows
        # 6.3 px lower mid-robe than at the seams, so a corner-to-corner axis
        # would invent a horizontal gradient the source does not have.
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        for n, pt in enumerate(outline):
            q = c.pt(*pt)
            path.moveTo(q) if n == 0 else path.lineTo(q)
        path.closeSubpath()
        c.p.setPen(Qt.NoPen)
        c.p.setOpacity(gown_alpha)
        c.p.setBrush(gown_colour(0.5))
        c.p.drawPath(path)
        # Ten bands, one per row pair, so the isochromes follow the mesh rows:
        # the hem bows 6.3 px lower mid-robe than at the seams, which one
        # straight gradient cannot express (it was off by 1.54 rows at the hem).
        for j in range(DIVS - 1):
            band = ([grid[i][j] for i in range(DIVS)]
                    + [grid[i][j + 1] for i in range(DIVS - 1, -1, -1)])
            poly = QPolygonF([c.pt(*q) for q in band])
            bp = QPainterPath()
            bp.setFillRule(Qt.WindingFill)
            bp.addPolygon(poly)
            g = QLinearGradient(c.pt(*grid[DIVS // 2][j]), c.pt(*grid[DIVS // 2][j + 1]))
            g.setColorAt(0.0, gown_colour(j / (DIVS - 1.0)))
            g.setColorAt(1.0, gown_colour((j + 1) / (DIVS - 1.0)))
            c.p.setBrush(g)
            c.p.drawPath(bp)
        c.p.setOpacity(1.0)

    # --- firstHandSprite..+3: hands -- IN FRONT of the gown
    hand_pix = sprites.get("haloGlyph-1")
    for m in (0, 1):
        sign = -1.0 if m == 1 else 1.0          # og.cs:2187
        attach = (b0[0] + right[0] * 4.0 * sign, b0[1] + right[1] * 4.0 * sign)
        # +0.3 out, +0.3 down-axis and vel.y -= 0.5 -> dir (0.351, -0.937)
        hand = (b0[0] + right[0] * (0.351 * HAND_LEASH * sign),
                b0[1] - 0.937 * HAND_LEASH)
        d = norm((attach[0] - hand[0], attach[1] - hand[1]))
        cB = (hand[0] + d[0] * 3.0 + up[0], hand[1] + d[1] * 3.0 + up[1])
        cA = (attach[0] + right[0] * 5.0 * sign, attach[1] + right[1] * 5.0 * sign)
        # og.cs:1544-1545  HandSprite(l,0) is the cap and HandSprite(l,1) the
        # sleeve, so the cap is painted first and the 8 px sleeve covers its
        # middle -- the source's own "pink tips on an orange arm" hand.
        _limb(c, attach, hand, cA, cB, HAND_HALF, HAND_HI,
              cap=hand_pix, cap_behind=True, cap_colour=SKIN)

    # --- head sprites: ear pods behind the head first (slot 0), then the head
    reach = lerp(7.0, 5.0, abs(look[0]))
    pods = []
    for m in (0, 1):
        sign = -1.0 if m == 0 else 1.0
        off = max(-11.0, min(11.0, reach * sign))
        pos = (head[0] + right[0] * off, head[1] + right[1] * off)
        # og.cs:2173 side = (m < 1) != (look.x < 0)  -> slot 0 is behind the head
        side = 1 if ((m < 1) != (look[0] < 0.0)) else 0
        pods.append((pos, sign, side))
    pods.sort(key=lambda item: item[2])

    def draw_pods(which):
        for pos, sign, side in pods:
            if side != which:
                continue
            w = lerp(3.5, 5.0, abs(look[0]))
            c.circle(pos[0], pos[1], w / 2.0, 5.5 / 2.0, POD_BASE)
            c.circle(pos[0], pos[1], w * 0.4 / 2.0, 4.4 / 2.0, POD_HI)
            target = (pos[0] + up[0] * 40.0 - look[0] * 10.0,
                      pos[1] + up[1] * 40.0 - look[1] * 10.0)
            d = norm((target[0] - b0[0], target[1] - b0[1]))
            # anchorY 0 -> the 11.2 px stalk grows out of the pod along `d`
            c.sprite(sprites.get("LizardScaleA1"),
                     pos[0] + d[0] * 11.2 * 0.5, pos[1] + d[1] * 11.2 * 0.5,
                     3.0, 11.2, POD_HI, math.degrees(math.atan2(d[0], d[1])),
                     smooth=False)

    draw_pods(0)

    # --- HeadSprite: Circle20, scale (rad/9, rad/11), rotated with the body axis
    # og.cs:2159  HeadSprite.rotation = Custom.VecToDeg(DirVec(head, firstChunk)),
    # i.e. local +X lies along the head->body axis. With the body hanging that is
    # a quarter turn, so the 11.1 x 9.09 ellipse reads 9.09 wide x 11.1 tall on
    # screen. Drawing it unrotated transposed it.
    c.sprite(sprites.get("Circle20"), head[0], head[1],
             20 * HEAD_RAD / 9.0, 20 * HEAD_RAD / 11.0, SKIN,
             math.degrees(math.atan2(axis[0], axis[1])))

    # --- ChinSprite: Circle20 rad/15, 15% from head towards body
    chin = (lerp(head[0], b0[0], 0.15) + right[0] * look[0] * 2.0,
            lerp(head[1], b0[1], 0.15) + right[1] * look[0] * 2.0)
    c.circle(chin[0], chin[1], 20 * HEAD_RAD / 15.0 / 2, 20 * HEAD_RAD / 15.0 / 2, SKIN)

    # --- eyes (og.cs:2166-2172)
    for m in (0, 1):
        sign = -1.0 if m == 0 else 1.0
        off = max(-5.0, min(5.0, look[0] * 3.0 + 2.5 * sign))
        pos = (head[0] + right[0] * off + axis[0] * (1.0 - look[1] * 3.0),
               head[1] + right[1] * off + axis[1] * (1.0 - look[1] * 3.0))
        # og.cs:2168  scaleX = 1 + InverseLerp(...) + (1 - eyesOpen); eyes open,
        # so the 1x1 `pixel` is 1..2 px wide. m == 0 widens via
        # InverseLerp(-1, -0.5, look.x), m == 1 via InverseLerp(1, 0.5, look.x).
        widen = (_inv_lerp(-1.0, -0.5, look[0]) if m == 0
                 else _inv_lerp(1.0, 0.5, look[0]))
        half_w = (1.0 + widen) * 0.5
        half_h = EYE_H * 0.5
        c.poly([(pos[0] - half_w, pos[1] - half_h), (pos[0] + half_w, pos[1] - half_h),
                (pos[0] + half_w, pos[1] + half_h), (pos[0] - half_w, pos[1] + half_h)], EYE)

    draw_pods(1)


# ---------------------------------------------------------------- layout

POSES = [
    ("look (0,0)", (0.0, 0.0)),
    ("look (+1,0)", (1.0, 0.0)),
    ("look (-1,0)", (-1.0, 0.0)),
    ("look (0,+1) up", (0.0, 1.0)),
]

BG = QColor(16, 16, 20)
LABEL = QColor(150, 150, 160)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "puppet-portrait.png"))
    args = ap.parse_args()

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    report = ensure_sprites()
    sprites = {name: QImage(str(p)) for name, p in report.sprites.items()}

    W, H = 1480, 1250
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    img.fill(BG)
    p = QPainter(img)
    p.setFont(QFont("Consolas", 10))

    def panel(rect, ox, oy, scale, draw, label, note="", label_y=700):
        """Draw one clipped cell so a wide gown cannot bleed into its neighbour."""
        p.save()
        p.setClipRect(QRectF(*rect))
        draw(Canvas(p, ox, oy, scale))
        p.restore()
        p.setPen(LABEL)
        text = label if not note else f"{label}\n{note}"
        p.drawText(QRectF(rect[0] + 8, label_y, rect[2] - 16, 60),
                   int(Qt.AlignLeft | Qt.AlignTop) | int(Qt.TextWordWrap), text)

    # --- whole puppet, solid and see-through ------------------------------
    panel((16, 20, 330, 670), 185, 395, 13.0,
          lambda c: draw_puppet(c, sprites, look=(0.55, 0.25)),
          "13x   full puppet",
          "the robe hides the whole lower body and both legs, exactly as measured")
    panel((356, 20, 340, 670), 530, 395, 13.0,
          lambda c: draw_puppet(c, sprites, look=(0.55, 0.25), gown_alpha=0.22),
          "13x   gown 22%", "neck + both feet sit BEHIND the robe")

    # --- the legs: normally 100% covered, so show them on their own -------
    panel((706, 20, 350, 670), 885, 325, 13.0,
          lambda c: draw_puppet(c, sprites, look=(0.0, 0.0), show_gown=False,
                                show_aura=False),
          "13x   whole puppet, robe removed",
          "7-ring bezier from bodyChunks[1] itself, knee +4 px outboard, 4 px "
          "leg band, plus-glyph foot; the arms are 8 px wide and gown-coloured")

    panel((1066, 20, 398, 670), 1265, 375, 13.0,
          lambda c: draw_puppet(c, sprites, look=(0.0, 0.0), show_aura=False,
                                gown_alpha=0.22),
          "13x   legs in place, robe 22%",
          "measured, not guessed: the foot lands 5.15 px INSIDE the robe at its "
          "own x (tools/leg_visibility.py), so the legs are covered at rest and "
          "only the cloth's 9 px radial tolerance lets them flash")

    # --- the four look directions -----------------------------------------
    for k, (label, look) in enumerate(POSES):
        ox = 130 + k * 200
        panel((ox - 96, 900, 190, 300), ox, 1040, 6.5,
              lambda c, look=look: draw_puppet(c, sprites, look=look),
              f"6.5x  {label}", label_y=1205)

    # --- real in-game size ------------------------------------------------
    p.save()
    p.setClipRect(QRectF(930, 900, 200, 300))
    draw_puppet(Canvas(p, 990, 1040, 1.0), sprites, look=(0.0, 0.0), show_aura=False)
    draw_puppet(Canvas(p, 1050, 1040, 2.0), sprites, look=(0.0, 0.0), show_aura=False)
    p.restore()
    p.setPen(LABEL)
    p.drawText(QRectF(930, 1120, 320, 46), int(Qt.AlignLeft | Qt.AlignTop),
               "1x / 2x  real in-game size (43 px tall)")
    p.setPen(QColor(90, 90, 100))
    p.drawRect(QRectF(1170, 1036, 4, 4))
    p.drawText(QRectF(1162, 1046, 100, 24), Qt.AlignLeft, "4px")

    p.end()
    img.save(args.out)
    print(f"wrote {args.out}  ({W}x{H})   sprites: {report.source}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
