"""The chamber rail: ``framePos`` walks 0..4 around a rectangle.

``OracleArm.OnFramePos`` keeps one scalar ``framePos`` and derives both the base
position and the inward normal from it. Straight stretches interpolate between
consecutive corners; the last ``CORNER_BLEND`` (0.1) of every quadrant is a
quarter arc of radius ``0.1 * edge_length``, so the base never jerks at a corner.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin

from . import constants as C


@dataclass(frozen=True)
class RailPose:
    point: tuple[float, float]
    inward: tuple[float, float]
    tangent: tuple[float, float]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _short_arc(a0: float, a1: float, t: float) -> float:
    delta = (a1 - a0 + pi) % (2 * pi) - pi
    return a0 + delta * t


def _slerp_vec(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    angle = _short_arc(atan2(a[1], a[0]), atan2(b[1], b[0]), t)
    return (cos(angle), sin(angle))


class RailPath:
    """Rectangle perimeter parameterised by ``framePos`` in [0, 4)."""

    def __init__(self, bounds: tuple[float, float, float, float]):
        x, y, w, h = bounds
        self.corners = (
            (x, y),          # 0 top-left
            (x + w, y),      # 1 top-right
            (x + w, y + h),  # 2 bottom-right
            (x, y + h),      # 3 bottom-left
        )
        self.width = w
        self.height = h
        self.radius = max(8.0, C.CORNER_BLEND * w)
        self.edges = (
            (self.corners[0], self.corners[1]),
            (self.corners[1], self.corners[2]),
            (self.corners[2], self.corners[3]),
            (self.corners[3], self.corners[0]),
        )
        # Screen space: y grows downward. Inward normals point into the room.
        self.normals = ((0.0, 1.0), (-1.0, 0.0), (0.0, -1.0), (1.0, 0.0))
        self.tangents = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))

    # -- geometry ---------------------------------------------------------
    def pose(self, frame_pos: float) -> RailPose:
        f = frame_pos % 4.0
        quadrant = int(f)
        local = f - quadrant
        blend = C.CORNER_BLEND
        a, b = self.edges[quadrant]
        normal = self.normals[quadrant]
        tangent = self.tangents[quadrant]

        if blend <= local <= 1.0 - blend:
            t = (local - blend) / max(1e-9, 1.0 - 2.0 * blend)
            r = self.radius
            start = (a[0] + tangent[0] * r, a[1] + tangent[1] * r)
            end = (b[0] - tangent[0] * r, b[1] - tangent[1] * r)
            return RailPose((_lerp(start[0], end[0], t), _lerp(start[1], end[1], t)), normal, tangent)

        # Rounded corner: centre sits `r` inside the corner along both edges.
        if local < blend:
            arc_t = local / blend
            corner = a
            in_t = self.tangents[(quadrant - 1) % 4]
            out_t = self.tangents[quadrant]
            prev_normal = self.normals[(quadrant - 1) % 4]
        else:
            arc_t = (local - (1.0 - blend)) / blend
            corner = b
            in_t = self.tangents[quadrant]
            out_t = self.tangents[(quadrant + 1) % 4]
            prev_normal = normal
        centre = (
            corner[0] - in_t[0] * self.radius + out_t[0] * self.radius,
            corner[1] - in_t[1] * self.radius + out_t[1] * self.radius,
        )
        start_angle = atan2(-out_t[1], -out_t[0])
        end_angle = atan2(in_t[1], in_t[0])
        angle = _short_arc(start_angle, end_angle, arc_t)
        point = (centre[0] + cos(angle) * self.radius, centre[1] + sin(angle) * self.radius)
        next_normal = self.normals[(quadrant + 1) % 4] if local >= 1.0 - blend else normal
        return RailPose(
            point,
            _slerp_vec(prev_normal, next_normal, arc_t),
            _slerp_vec(in_t, out_t, arc_t),
        )

    def project(self, point: tuple[float, float]) -> float:
        """Nearest ``framePos`` on the perimeter (the game's four-edge search)."""
        x, y = point
        best_f = 0.0
        best_d = float("inf")
        for index, (a, b) in enumerate(self.edges):
            ax, ay = a
            bx, by = b
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            if length_sq < 1e-9:
                continue
            # Project onto the *straight* part of the edge (the arcs occupy
            # the first and last CORNER_BLEND of each quadrant).
            r = self.radius
            dlen = max(1e-9, length_sq ** 0.5)
            ux, uy = dx / dlen, dy / dlen
            sx, sy = ax + ux * r, ay + uy * r
            ex, ey = bx - ux * r, by - uy * r
            span = max(1e-9, dlen - 2.0 * r)
            t = max(0.0, min(1.0, ((x - sx) * ux + (y - sy) * uy) / span))
            px, py = sx + (ex - sx) * t, sy + (ey - sy) * t
            distance = hypot(px - x, py - y)
            if distance < best_d:
                best_d = distance
                best_f = index + C.CORNER_BLEND + t * (1.0 - 2.0 * C.CORNER_BLEND)
        return best_f % 4.0

    def project_on_edge(self, point: tuple[float, float], quadrant: int) -> float:
        """``framePos`` of the foot of the perpendicular on ONE edge."""
        quadrant %= 4
        a, b = self.edges[quadrant]
        r = self.radius
        dlen = max(1e-9, hypot(b[0] - a[0], b[1] - a[1]))
        ux, uy = (b[0] - a[0]) / dlen, (b[1] - a[1]) / dlen
        sx, sy = a[0] + ux * r, a[1] + uy * r
        span = max(1e-9, dlen - 2.0 * r)
        t = max(0.0, min(1.0, ((point[0] - sx) * ux + (point[1] - sy) * uy) / span))
        return quadrant + C.CORNER_BLEND + t * (1.0 - 2.0 * C.CORNER_BLEND)

    def project_near(self, point: tuple[float, float], current_f: float,
                     margin: float = 60.0) -> float:
        """Nearest perimeter point, biased to the edge the carriage is already on.

        ``project`` is discontinuous across the four diagonal bisectors: a puppet
        standing near the middle of a 776x415 chamber makes the foot of its
        perpendicular jump between adjacent edges, and the target then flips up to
        180 degrees -- the carriage needs 180 ticks to come round, so it never
        settles (measured: base in motion on 55 % of ticks, and the body trailed
        sideways on the sliding goal, tilt p90 90 deg). Keep the CURRENT edge
        unless another one is clearly closer.
        """
        best = self.project(point)
        edge = self.project_on_edge(point, int(current_f) % 4)
        best_point = self.pose(best).point
        edge_point = self.pose(edge).point
        d_best = hypot(best_point[0] - point[0], best_point[1] - point[1])
        d_edge = hypot(edge_point[0] - point[0], edge_point[1] - point[1])
        return edge if d_edge <= d_best + margin else best

    def sample(self, count: int = 240) -> list[tuple[float, float]]:
        return [self.pose(4.0 * i / count).point for i in range(count)]


def move_towards_angle(current_deg: float, target_deg: float, max_delta: float) -> float:
    delta = (target_deg - current_deg + 180.0) % 360.0 - 180.0
    if abs(delta) <= max_delta:
        return target_deg
    return current_deg + (max_delta if delta > 0 else -max_delta)
