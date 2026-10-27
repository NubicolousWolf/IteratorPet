"""Build a labelled contact sheet of the extracted iterator primitives."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw

from iterator_pet.assets_extractor import SPRITE_NAMES, ensure_sprites

BG = (24, 20, 26)
FG = (255, 214, 232)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    out = Path(argv[0]) if argv else Path("asset-sheet.png")
    report = ensure_sprites()
    print(report.describe())

    zoom = 10
    pad = 16
    label_h = 18
    cells = []
    for name in SPRITE_NAMES:
        img = Image.open(report.sprites[name]).convert("RGBA")
        w, h = img.size
        scaled = img.resize((max(1, w * zoom), max(1, h * zoom)), Image.NEAREST)
        cells.append((name, scaled, (w, h)))

    cell_w = max(c[1].width for c in cells) + pad * 2
    cell_h = max(c[1].height for c in cells) + pad * 2 + label_h
    sheet = Image.new("RGB", (cell_w * len(cells), cell_h), BG)
    draw = ImageDraw.Draw(sheet)
    for index, (name, img, size) in enumerate(cells):
        x = index * cell_w
        ox = x + (cell_w - img.width) // 2
        oy = pad + (cell_h - label_h - pad * 2 - img.height) // 2
        sheet.paste(img, (ox, oy), img)
        draw.text((x + pad, cell_h - label_h + 2), f"{name} {size[0]}x{size[1]}", fill=FG)
        if index:
            draw.line([(x, 0), (x, cell_h)], fill=(60, 48, 58))

    # palette strip on a second row
    if report.palette_path and report.palette_path.is_file():
        pal = Image.open(report.palette_path).convert("RGB")
        pal_big = pal.resize((pal.width * 8, pal.height * 8), Image.NEAREST)
        strip = Image.new("RGB", (sheet.width, pal_big.height + 22), BG)
        strip.paste(pal_big, (0, 22))
        ImageDraw.Draw(strip).text((4, 4), "palette26 32x16", fill=FG)
        combined = Image.new("RGB", (sheet.width, sheet.height + strip.height), BG)
        combined.paste(sheet, (0, 0))
        combined.paste(strip, (0, sheet.height))
        sheet = combined

    sheet.save(out)
    print(f"wrote {out} ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
