"""Full-screen transparent overlay window that hosts the pet."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from ..config import Config, load, save
from ..rendering.canvas import PetCanvas
from ..simulation.state import SimulationState
from .fixed_loop import DEFAULT_MAX_SUBSTEPS, GAME_DT, FixedStepLoop
from .workspace import ScreenWorkspace
from . import windows_overlay


def cursor_sim_point(
    global_pos,
    origin,
    screen_rect,
    scale: float,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    """Map a global cursor position into SIMULATION coordinates, or ``None``.

    The simulation runs in game pixels and ``scale`` maps those onto screen
    pixels (``rendering/canvas.py``), so a screen offset has to be DIVIDED by
    ``scale`` before ``SimulationState.set_cursor`` sees it. Feeding screen
    pixels straight in -- which this used to do -- inflates every cursor
    distance by ``scale`` (1.5x on this machine): ``SCORE_TALK_RANGE`` only
    caught a cursor that was physically 1.5x closer than intended, and
    ``investigate`` aimed at ``cursor + 150 px`` measured in the wrong unit.

    Containment is tested against the SCREEN, not the window. The pet window is
    ``availableGeometry()``, so the taskbar strip (48 px tall here) read as "no
    cursor at all" even with the mouse sitting in it; that forced the selector
    into its no-cursor branch on every trip past the bottom edge. The point is
    clamped into the chamber, which is where the game would put it as well.
    """
    if screen_rect is not None and not screen_rect.contains(global_pos):
        return None
    zoom = max(1e-6, float(scale))
    x = (global_pos.x() - origin.x()) / zoom
    y = (global_pos.y() - origin.y()) / zoom
    bx, by, bw, bh = bounds
    return (min(max(x, bx), bx + bw), min(max(y, by), by + bh))


class PetWindow(PetCanvas):
    closed = Signal()

    def __init__(self, simulation: SimulationState, config: Config, parent=None, sprites=None):
        # Rain World's native cadence: every oracle constant is per 40 Hz tick.
        self._loop = FixedStepLoop(dt=GAME_DT, max_substeps=DEFAULT_MAX_SUBSTEPS)
        super().__init__(
            simulation,
            alpha_provider=lambda: self._loop.alpha,
            parent=parent,
            scale=config.scale,
            sprites=sprites,
        )
        self.config = config
        self.click_through = bool(config.click_through)
        self._paused = False
        self._last = time.perf_counter()
        self._origin = (0.0, 0.0)

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if config.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setWindowTitle("Iterator Pet")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        workspace = ScreenWorkspace()
        snapshot = workspace.snapshot()
        geo = snapshot.available
        self._origin = (float(geo.x()), float(geo.y()))
        self.setGeometry(geo)
        zoom = max(0.3, min(4.0, float(config.scale)))
        self.simulation.resize(
            (0.0, 0.0, float(geo.width()) / zoom, float(geo.height()) / zoom)
        )

        interval = max(4, int(1000 / max(15, config.fps)))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(interval)

    # -- lifecycle ---------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        windows_overlay.prevent_activation(self)
        if self.click_through:
            windows_overlay.set_click_through(self, True)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        # NO save() HERE. Every mutator (toggle_click_through / toggle_rail)
        # already persists, so closing wrote nothing new -- but a TOOL
        # that builds a window from a default ``Config`` and then closes it
        # overwrote the user's real config. That happened: ``tools/smoke_window.py``
        # reset ``scale`` from the user's 1.5 to the 2.2 default, which silently
        # changed the chamber size and made the pet 1.5x bigger on screen.
        self.closed.emit()
        super().closeEvent(event)

    # -- per-frame ---------------------------------------------------------
    def _tick(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last
        self._last = now
        if not self._paused and elapsed > 0.0:
            # The cursor must be sampled once per SIMULATION TICK, not once per
            # frame. ``advance`` can run up to 8 sub-steps for one frame, and the
            # behaviour gates are per tick: sampling per frame made every repeat
            # tick read a speed of exactly 0, which shuts ``investigate`` and
            # opens ``talk`` -- measured through the real simulation with a cursor
            # moving 3 px/frame: 1 tick/frame -> investigate 24 %, 2+ ticks/frame
            # -> investigate 2 % and talk starts while the mouse is MOVING. Worse,
            # every segment length is even, so the parity of boundary ticks is
            # conserved and a boundary that lands on a duplicate sample stays
            # duplicate forever.
            self._loop.advance(elapsed, self._step_once)
        self._track_cursor()
        self.update()

    def _step_once(self, dt: float) -> None:
        """One 40 TPS tick: look at the pointer, then step the physics."""
        self._track_cursor()
        self.simulation.step(dt)

    def _track_cursor(self) -> None:
        """Feed the global cursor position to the puppet so it can look at it."""
        screen = self.screen()
        self.simulation.set_cursor(
            cursor_sim_point(
                QCursor.pos(),
                self.pos(),
                screen.geometry() if screen is not None else None,
                self.scale,
                self.simulation.bounds,
            )
        )

    # -- controls ----------------------------------------------------------
    def toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._loop.pause()
        else:
            self._loop.resume()
            self._last = time.perf_counter()
        self.update()

    @property
    def paused(self) -> bool:
        return self._paused

    def toggle_click_through(self) -> None:
        self.click_through = not self.click_through
        self._apply_click_through()
        self.config.click_through = self.click_through
        save(self.config)

    def _apply_click_through(self) -> None:
        applied = windows_overlay.set_click_through(self, self.click_through)
        # Qt-level fallback (also keeps behaviour sane on non-Windows).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.click_through)
        self._click_through_native = applied

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_pause()
        elif key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_C:
            self.toggle_click_through()
        else:
            super().keyPressEvent(event)


def create_window(config: Config | None = None, sprites=None) -> PetWindow:
    app = QApplication.instance() or QApplication([])
    cfg = config or load()
    workspace = ScreenWorkspace()
    snapshot = workspace.snapshot()
    geo = snapshot.available
    zoom = max(0.3, min(4.0, float(cfg.scale)))
    sim = SimulationState(
        (0.0, 0.0, float(geo.width()) / zoom, float(geo.height()) / zoom)
    )
    window = PetWindow(sim, cfg, sprites=sprites)
    window.setGeometry(geo)
    return window
