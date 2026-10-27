"""Optional sprite pack.

The pet draws itself with vector shapes by default. If the user supplies their
own artwork in ``%LOCALAPPDATA%/IteratorPet/sprites`` (or a directory given on
the command line) the renderer swaps the robe and head for those images.

Expected files, both optional:

- ``body.png`` – the robe, facing down, anchored at its top centre.
- ``head.png`` – the mask, centred on the sheet.

Nothing here reads from the game installation; the pack is entirely user
supplied so the repository never ships copyrighted art.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QPixmap


@dataclass
class SpritePack:
    body: QPixmap | None = None
    head: QPixmap | None = None
    source: Path | None = None

    @property
    def usable(self) -> bool:
        return self.body is not None or self.head is not None

    @classmethod
    def load(cls, directory: str | Path | None) -> "SpritePack":
        if directory is None:
            return cls()
        root = Path(directory)
        if not root.is_dir():
            return cls()
        pack = cls(source=root)
        for name, attribute in (("body.png", "body"), ("head.png", "head")):
            path = root / name
            if not path.is_file():
                continue
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                setattr(pack, attribute, pixmap)
        return pack


def default_sprite_dir() -> Path:
    from ..config import config_dir

    return config_dir() / "sprites"
