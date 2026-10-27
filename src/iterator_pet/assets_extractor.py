"""Read-only extraction of the handful of base sprites an iterator is built from.

Rain World's iterator has no character sheet. ``OracleGraphics`` composes the
whole puppet out of a few generic atlas elements (``Circle20``, ``pixel``,
``CentipedeSegment``, ``LizardScaleA1`` ...) plus vertex colours. So all we need
is those few sprites, cut from the user's own installation and cached locally.

Nothing is redistributed: the cache lives under the user's app-data folder and
is regenerated from their game files. When no installation is present we fall
back to procedurally generated equivalents so tests still run anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_INSTALL = Path(r"F:\steam\steamapps\common\Rain World")
PALETTE_NAME = "palette26"

# Elements used by OracleGraphics / ArmJointGraphics / UbilicalCord / Gown.
SPRITE_NAMES = (
    "Circle20",
    "pixel",
    "CentipedeSegment",
    "MirosLegSmallPart",
    "deerEyeB",
    "LizardScaleA1",
    "haloGlyph-1",
    "KarmaPetal",
)


def cache_root() -> Path:
    import os

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "IteratorPet" / "cache"


@dataclass
class ExtractReport:
    ok: bool
    source: str
    cache_dir: Path
    sprites: dict[str, Path] = field(default_factory=dict)
    palette_path: Path | None = None
    colors: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"source      : {self.source}",
            f"cache       : {self.cache_dir}",
            f"sprites     : {len(self.sprites)}",
            f"palette     : {self.palette_path}",
        ]
        for key, value in self.colors.items():
            lines.append(f"  {key:18s} {value}")
        for note in self.notes:
            lines.append(f"note: {note}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# generated fallbacks (used when the game is absent)
# --------------------------------------------------------------------------
def _generated(name: str):
    """Produce a pixel-art equivalent of each primitive, drawn at 8x then downsampled."""
    from PIL import Image, ImageDraw

    scale = 8

    def canvas(w: int, h: int):
        img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    if name == "Circle20":
        img, d = canvas(20, 20)
        d.ellipse((0, 0, 20 * scale - 1, 20 * scale - 1), fill=(255, 255, 255, 255))
        size = (20, 20)
    elif name == "pixel":
        img = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
        size = (1, 1)
    elif name == "CentipedeSegment":
        img, d = canvas(18, 15)
        d.rounded_rectangle((0, 2 * scale, 18 * scale - 1, 13 * scale), radius=6 * scale, fill=(255, 255, 255, 255))
        size = (18, 15)
    elif name == "MirosLegSmallPart":
        img, d = canvas(4, 25)
        d.rounded_rectangle((0, 0, 4 * scale - 1, 25 * scale - 1), radius=2 * scale, fill=(255, 255, 255, 255))
        size = (4, 25)
    elif name == "deerEyeB":
        img, d = canvas(5, 5)
        d.ellipse((0, 0, 5 * scale - 1, 5 * scale - 1), fill=(255, 255, 255, 255))
        size = (5, 5)
    elif name == "LizardScaleA1":
        img, d = canvas(4, 14)
        d.polygon(
            [(2 * scale, 0), (4 * scale, 3 * scale), (3 * scale, 14 * scale), (1 * scale, 14 * scale), (0, 3 * scale)],
            fill=(255, 255, 255, 255),
        )
        size = (4, 14)
    elif name == "haloGlyph-1":
        img, d = canvas(6, 6)
        d.regular_polygon((3 * scale, 3 * scale, 3 * scale), 4, rotation=45, fill=(255, 255, 255, 255))
        size = (6, 6)
    elif name == "KarmaPetal":
        img, d = canvas(6, 20)
        d.ellipse((0, 0, 6 * scale - 1, 20 * scale - 1), fill=(255, 255, 255, 255))
        size = (6, 20)
    else:  # pragma: no cover - defensive
        img = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
        size = (4, 4)
    return img.resize(size, Image.LANCZOS)


def _generated_palette():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (32, 16), (0, 0, 0))
    d = ImageDraw.Draw(img)
    # Palette 26 (SS_AI) headline colours, sampled from the real palette layout.
    rows = {
        0: (0, 0, 0),
        1: (176, 0, 255),
        2: (255, 49, 22),
        3: (255, 35, 0),
        5: (31, 7, 0),
    }
    for y, colour in rows.items():
        for x in range(32):
            d.point((x, y), fill=colour)
    return img


# --------------------------------------------------------------------------
# extraction from the local installation
# --------------------------------------------------------------------------
def _load_atlas(install: Path):
    """Return (atlas image, frame table) from resources.assets, or None."""
    assets = install / "RainWorld_Data" / "resources.assets"
    if not assets.is_file():
        return None, None, f"missing {assets}"
    try:
        import UnityPy
    except ImportError:
        return None, None, "UnityPy is not installed"

    env = UnityPy.load(str(assets))
    atlas_image = None
    frame_table = None
    for obj in env.objects:
        try:
            name = obj.peek_name()
        except Exception:
            continue
        if obj.type.name == "Texture2D" and name == "rainWorld":
            atlas_image = obj.read().image
        elif obj.type.name == "TextAsset" and name == "rainWorld":
            payload = obj.read().m_Script
            text = payload if isinstance(payload, str) else bytes(payload).decode("utf-8", errors="replace")
            frame_table = json.loads(text).get("frames", {})
    if atlas_image is None:
        return None, None, "rainWorld texture not found"
    if frame_table is None:
        return None, None, "rainWorld frame table not found"
    return atlas_image, frame_table, ""


def _crop(atlas, frame_table, name: str):
    entry = frame_table.get(f"{name}.png")
    if not entry:
        return None
    frame = entry["frame"]
    box = (frame["x"], frame["y"], frame["x"] + frame["w"], frame["y"] + frame["h"])
    return atlas.crop(box)


def _load_palette(install: Path):
    path = install / "RainWorld_Data" / "StreamingAssets" / "palettes" / f"{PALETTE_NAME}.png"
    if not path.is_file():
        return None
    from PIL import Image

    return Image.open(path).convert("RGB")


def _palette_colors(palette) -> dict[str, tuple[int, int, int]]:
    if palette is None:
        return {}
    width, height = palette.size
    sample = lambda x, y: palette.getpixel((min(x, width - 1), min(y, height - 1)))
    return {
        "black": sample(0, 0),
        "metal_blend": sample(5, 5),
        "arm_base_b": sample(5, 1),
        "arm_highlight_b": sample(5, 2),
        "cloth_hot": sample(5, 2),
        "cloth_warm": sample(5, 3),
        "cloth_deep": sample(3, 5),
    }


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def extract(install: Path = DEFAULT_INSTALL, cache_dir: Path | None = None, force: bool = False) -> ExtractReport:
    """Cut the required sprites out of the local game into the user cache."""
    cache = Path(cache_dir) if cache_dir else cache_root()
    sprite_dir = cache / "sprites"
    sprite_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    atlas, frames, reason = _load_atlas(Path(install))
    palette = _load_palette(Path(install))
    source = "game" if atlas is not None else "generated"
    if atlas is None:
        notes.append(f"using generated primitives ({reason})")

    sprites: dict[str, Path] = {}
    for name in SPRITE_NAMES:
        image = _crop(atlas, frames, name) if atlas is not None else None
        if image is None:
            if atlas is not None:
                notes.append(f"{name}: not in atlas, generated instead")
            image = _generated(name)
        target = sprite_dir / f"{name}.png"
        if force or not target.is_file():
            image.save(target)
        sprites[name] = target

    palette_path = cache / f"{PALETTE_NAME}.png"
    if palette is None:
        palette = _generated_palette()
        if "generated" not in source:
            notes.append("palette26 missing, generated instead")
    if force or not palette_path.is_file():
        palette.save(palette_path)

    colors = _palette_colors(palette)
    manifest = {
        "source": source,
        "install": str(install),
        "sprites": {k: v.name for k, v in sprites.items()},
        "palette": palette_path.name,
        "colors": {k: list(v) for k, v in colors.items()},
        "notes": notes,
    }
    (cache / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return ExtractReport(
        ok=True,
        source=source,
        cache_dir=cache,
        sprites=sprites,
        palette_path=palette_path,
        colors=colors,
        notes=notes,
    )


def ensure_sprites(cache_dir: Path | None = None, install: Path | None = None) -> ExtractReport:
    """Return the cache, extracting only when it is incomplete.

    ``install`` 为空时退回 ``DEFAULT_INSTALL``（开发机）；发布版由 `launcher.py`
    把自动探测到的目录传进来。
    """
    cache = Path(cache_dir) if cache_dir else cache_root()
    manifest_path = cache / "manifest.json"
    complete = manifest_path.is_file() and all(
        (cache / "sprites" / f"{name}.png").is_file() for name in SPRITE_NAMES
    )
    if complete:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return ExtractReport(
            ok=True,
            source=data.get("source", "cache"),
            cache_dir=cache,
            sprites={n: cache / "sprites" / f"{n}.png" for n in SPRITE_NAMES},
            palette_path=cache / f"{PALETTE_NAME}.png",
            colors={k: tuple(v) for k, v in data.get("colors", {}).items()},
            notes=list(data.get("notes", [])),
        )
    return extract(Path(install) if install else DEFAULT_INSTALL, cache_dir=cache)


def sprite_path(name: str, cache_dir: Path | None = None) -> Path:
    cache = Path(cache_dir) if cache_dir else cache_root()
    return cache / "sprites" / f"{name}.png"
