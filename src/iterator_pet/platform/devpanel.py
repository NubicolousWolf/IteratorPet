"""Developer panel: a human-readable window onto the simulation.

Two pieces, deliberately separated:

* ``dev_lines(simulation) -> list[str]`` -- pure text, no Qt. Testable headless,
  and it is what the panel paints.
* ``DevPanel`` -- a small always-on-top window that repaints those lines ~12 Hz.

Enable it with ``--dev-panel``, from the tray menu, or standalone with
``python tools/devpanel.py`` (which needs no pet window at all).

It is a DEVELOPMENT aid: nothing here is enabled by default, and nothing feeds
back into the simulation.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import QWidget


BACKGROUND = QColor(14, 11, 16, 232)
BORDER = QColor(120, 96, 72, 200)
TITLE = QColor(255, 176, 92)
LABEL = QColor(160, 146, 132)
VALUE = QColor(226, 220, 212)
WARN = QColor(240, 120, 90)


def dev_lines(simulation) -> list[str]:
    """Format ``SimulationState.status()`` for a human.

    Layout is fixed-width so the numbers line up while they change. Anything out
    of bounds is flagged with ``!`` -- the panel exists to make exactly those
    obvious.
    """
    s = simulation.status()
    cursor = s["cursor"]
    scores = s["scores"] or {}
    order = ("roaming", "investigate", "talk")
    winner = max(scores, key=lambda k: scores[k]) if scores else "-"

    rows: list[tuple[str, str]] = [
        ("tick / time", f'{s["ticks"]}  {s["time"]:.1f}s'),
        ("behaviour", f'{s["behaviour"]}   (best: {winner})'
                      f'  held {s["held_ticks"]}/{s["min_hold"]}t'),
        ("", "  ".join(f"{name[:4]} {scores.get(name, 0):.2f}" for name in order)),
        ("cursor", "none" if cursor is None
         else f'({cursor[0]:.0f}, {cursor[1]:.0f})  still {s["still_ticks"]}t'),
        ("", ""),
        ("carriage", f'framePos {s["frame_pos"]:.2f}  edge {s["station_edge"]}'),
        ("", f'moving {s["base_moving"]}   start/stop '
             f'{s["start_threshold"]:.0f}/{s["stop_threshold"]:.0f} px'),
        ("base -> body", f'{s["base_body"]:.0f} px  '
                         f'{100 * s["reach_ratio"]:.0f}% of arm {s["reach"]:.0f}'),
        ("", "!" if s["reach_ratio"] > 1.0 else ""),
        ("", ""),
        ("puppet", f'speed {s["body_speed"]:.1f} px/t  tilt {s["tilt_deg"]:.0f} deg'),
        ("goal", f'{s["goal_distance"]:.0f} px away'
         + (f'   progress {s["progress"]:.2f}' if s["progress"] is not None
            else "   (behaviour goal)")),
        ("", ""),
        ("arm", f'stretch {s["arm_stretch"]:.2f}   ok {s["arm_ok"]}'),
        ("cord", f'{s["cord_nodes"]} nodes  stretch '
                 f'{s["cord_stretch"]:.3f} (free {s["cord_stretch_free"]:.3f})'),
        ("filaments", f'worst drawn/rope {s["filament_worst"]:.2f}'
         + ("  !" if s["filament_worst"] > 3.0 else "")),
        ("finite", f'{s["finite"]}' + ("" if s["finite"] else "  !")),
    ]
    width = max(len(label) for label, _ in rows)
    return [f"{label:<{width}}  {value}".rstrip() for label, value in rows]


class DevPanel(QWidget):
    """Always-on-top text panel; never takes focus, never feeds back."""

    def __init__(self, simulation, parent=None, refresh_hz: float = 12.0):
        super().__init__(parent)
        self.simulation = simulation
        self._lines: list[str] = []
        self.setWindowTitle("Iterator Pet - dev")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(430, 330)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(max(16, int(1000.0 / max(1.0, refresh_hz))))

    def refresh(self) -> None:
        self._lines = dev_lines(self.simulation)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(BORDER))
        painter.setBrush(BACKGROUND)
        painter.drawRect(rect)

        family = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        font = QFont(family, 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        line_height = metrics.height()
        painter.setPen(TITLE)
        painter.drawText(10, line_height + 2, "Iterator Pet / dev panel")
        painter.setPen(LABEL)
        for index, line in enumerate(self._lines):
            y = (index + 3) * line_height
            if y > self.height() - 4:
                break
            colour = WARN if line.rstrip().endswith("!") else VALUE
            painter.setPen(colour)
            painter.drawText(10, y, line)
        painter.end()
