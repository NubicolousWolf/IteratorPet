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
        self.show_rail = bool(config.show_rail)

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
        save(self.config)
        self.closed.emit()
        super().closeEvent(event)

    # -- per-frame ---------------------------------------------------------
    def _tick(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._last
        self._last = now
        if not self._paused and elapsed > 0.0:
            self._loop.advance(elapsed, self.simulation.step)
        self._track_cursor()
        self.update()

    def _track_cursor(self) -> None:
        """Feed the global cursor position to the puppet so it can look at it."""
        pos = QCursor.pos()
        local = self.mapFromGlobal(pos)
        if self.rect().contains(local):
            self.simulation.set_cursor((float(local.x()), float(local.y())))
        else:
            self.simulation.set_cursor(None)

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

    def toggle_rail(self) -> None:
        self.show_rail = not self.show_rail
        self.config.show_rail = self.show_rail
        save(self.config)
        self.update()

    def set_speed(self, speed: float) -> None:
        self.simulation.set_rail_speed(speed)
        self.config.speed = float(speed)
        save(self.config)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_pause()
        elif key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_C:
            self.toggle_click_through()
        elif key == Qt.Key.Key_R:
            self.show_rail = not self.show_rail
            self.update()
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
