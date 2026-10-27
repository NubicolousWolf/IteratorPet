"""The cursor crosses from screen pixels into simulation pixels exactly once.

``rendering/canvas.py`` draws the simulation scaled by ``config.scale``, so the
simulation's own frame is ``screen / scale``. Everything the simulation compares
against the cursor -- ``SCORE_NEAR_RANGE``, ``SCORE_TALK_RANGE``, the 150 px
investigate radius, ``TALK_DISTANCE_RATIO`` -- is in simulation pixels, so the
conversion belongs at the window boundary and nowhere else.

Regression: ``_track_cursor`` used to hand ``mapFromGlobal()`` straight to
``set_cursor``. That is screen pixels, so every cursor distance was inflated by
``scale`` (1.5x on this machine) and the pet only reacted to a mouse that was
physically 1.5x closer than the constants intend.
"""

from __future__ import annotations

import inspect

from PySide6.QtCore import QPoint, QRect

from iterator_pet.platform.pet_window import PetWindow, cursor_sim_point

SCREEN = QRect(0, 0, 1707, 960)      # this machine's logical screen
ORIGIN = QPoint(0, 0)                # the pet window sits on availableGeometry
ROOM = (0.0, 0.0, 1138.0, 608.0)     # 1707/1.5 x 912/1.5, the chamber


def test_a_point_on_screen_is_divided_by_the_render_scale():
    assert cursor_sim_point(QPoint(900, 450), ORIGIN, SCREEN, 1.5, ROOM) == (600.0, 300.0)


def test_scale_one_is_the_identity():
    assert cursor_sim_point(QPoint(900, 450), ORIGIN, SCREEN, 1.0, ROOM) == (900.0, 450.0)


def test_the_window_origin_is_subtracted():
    """The window is not always at (0, 0) -- a second screen, or a moved window."""
    point = cursor_sim_point(QPoint(900, 450), QPoint(100, 50), SCREEN, 1.5, ROOM)
    assert point == (800.0 / 1.5, 400.0 / 1.5)


def test_the_taskbar_strip_still_has_a_cursor():
    """The window is availableGeometry(), the screen is not.

    A pointer in the 48 px taskbar band is outside the WINDOW but still on the
    SCREEN. It used to read as "no cursor", which dropped the pet into the
    no-player branch every time the mouse crossed the bottom edge.
    """
    point = cursor_sim_point(QPoint(853, 935), ORIGIN, SCREEN, 1.5, ROOM)
    assert point is not None, "the cursor vanished in the taskbar strip"
    x, y = point
    assert abs(x - 853.0 / 1.5) < 1e-6
    assert abs(y - ROOM[3]) < 1e-6, "the taskbar point must clamp to the chamber floor"


def test_a_point_off_the_screen_is_none():
    assert cursor_sim_point(QPoint(100, 1000), ORIGIN, SCREEN, 1.5, ROOM) is None
    assert cursor_sim_point(QPoint(-5, 100), ORIGIN, SCREEN, 1.5, ROOM) is None


def test_every_accept_lands_inside_the_chamber():
    x, y, w, h = ROOM
    for gx in (0, 1, 850, 1706):
        for gy in (0, 1, 456, 959):
            point = cursor_sim_point(QPoint(gx, gy), ORIGIN, SCREEN, 1.5, ROOM)
            assert point is not None
            assert x <= point[0] <= x + w
            assert y <= point[1] <= y + h


def test_the_window_feeds_the_simulation_through_this_function():
    """The conversion must happen at the boundary, and only there.

    Every test above calls the pure function, so all of them stay green if
    ``_track_cursor`` goes back to handing ``mapFromGlobal()`` (screen pixels)
    straight to ``set_cursor`` -- that is exactly the bug this file exists for.
    There is no display in the test process, so this asserts on the source: the
    window's cursor path must go through ``cursor_sim_point`` with the render
    scale, and must not call ``mapFromGlobal`` at all.
    """
    source = inspect.getsource(PetWindow._track_cursor)
    assert "cursor_sim_point(" in source, "_track_cursor bypasses the unit conversion"
    assert "self.scale" in source, "_track_cursor no longer passes the render scale"
    assert "mapFromGlobal" not in source, "screen pixels are leaking into the simulation"
    assert "rect().contains" not in source, (
        "the window rect is availableGeometry(), so the taskbar strip would read "
        "as 'no cursor' again -- use the screen rect")
