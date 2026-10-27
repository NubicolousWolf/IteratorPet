from dataclasses import dataclass
from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QGuiApplication, QScreen

@dataclass(frozen=True)
class WorkspaceSnapshot:
    geometry: QRect
    available: QRect
    dpr: float
    name: str

class ScreenWorkspace:
    def __init__(self, screen: QScreen | None = None):
        self.screen = screen or QGuiApplication.primaryScreen()

    def snapshot(self) -> WorkspaceSnapshot:
        s = self.screen or QGuiApplication.primaryScreen()
        return WorkspaceSnapshot(s.geometry(), s.availableGeometry(), s.devicePixelRatio(), s.name())

    def refresh_primary(self) -> WorkspaceSnapshot:
        self.screen = QGuiApplication.primaryScreen()
        return self.snapshot()
