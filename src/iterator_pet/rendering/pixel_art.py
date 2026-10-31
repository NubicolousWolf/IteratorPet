"""像素画数据（头顶部件用）：纯数据，不持有任何 Qt 对象。

为什么单独一层：`Appearance` 是 frozen dataclass，渲染层每帧都要读它。如果把
`QPixmap` 塞进去，就变成"数据类里藏着一个 Qt 资源"，缓存失效、线程、析构三件事
全都会来。这里只存**字符串矩阵**，渲染时按需画（`renderer.draw_headpiece`）。

序列化格式（写进 `characters/<key>.json` 的 `extensions.rainworld.appearance`）::

    {"w": 24, "h": 20,
     "palette": ["#ff66cb", "#1a1a1a"],
     "grid": ["......00......", ...]}

* `grid` 一行一个字符串，字符 = `palette` 下标（`0-9a-z`，最多 36 色）；
* `.` = 全透明；
* 之所以不用 base64 PNG：这个文件是**给玩家看也给他们手改的**，字符串矩阵能 diff、
  能肉眼认、能在记事本里改一个像素。
"""

from __future__ import annotations

# 24×20：头精灵只有 11.1×9.1 仿真像素（`renderer.py:974`），1 格 = 0.5 仿真像素，
# 这个分辨率刚好够画出"角/齿/尖顶"，又不至于盖住光环。见 docs/PLAN-custom-iterator.md §3.5。
DEFAULT_W, DEFAULT_H = 24, 20
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
MAX_COLOURS = len(ALPHABET)
TRANSPARENT = "."


def normalize_colour(value) -> str | None:
    """``"#RRGGBB"`` / ``"#RGB"`` / ``(r,g,b)`` → ``"#rrggbb"``；认不出就 ``None``。"""
    if isinstance(value, (tuple, list)) and len(value) == 3:
        try:
            r, g, b = (max(0, min(255, int(v))) for v in value)
        except (TypeError, ValueError):
            return None
        return f"#{r:02x}{g:02x}{b:02x}"
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text.startswith("#"):
        text = "#" + text
    body = text[1:]
    if len(body) == 3 and all(c in "0123456789abcdef" for c in body):
        return "#" + "".join(c * 2 for c in body)
    if len(body) == 6 and all(c in "0123456789abcdef" for c in body):
        return text
    return None


class PixelArt:
    """一张 ``w × h`` 的像素图。``pixels[y][x]`` 存 ``"#rrggbb"`` 或 ``None``。"""

    __slots__ = ("w", "h", "pixels")

    def __init__(self, w: int = DEFAULT_W, h: int = DEFAULT_H):
        self.w = max(1, min(64, int(w)))
        self.h = max(1, min(64, int(h)))
        self.pixels: list[list[str | None]] = [
            [None] * self.w for _ in range(self.h)
        ]

    # -- 基本操作 -----------------------------------------------------------
    @classmethod
    def blank(cls, w: int = DEFAULT_W, h: int = DEFAULT_H) -> "PixelArt":
        return cls(w, h)

    def copy(self) -> "PixelArt":
        other = PixelArt(self.w, self.h)
        other.pixels = [row[:] for row in self.pixels]
        return other

    def get(self, x: int, y: int) -> str | None:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.pixels[y][x]
        return None

    def set(self, x: int, y: int, colour: str | None) -> bool:
        """越界不报错（画板拖动时鼠标会滑出去）。返回"值真的变了吗"。"""
        if not (0 <= x < self.w and 0 <= y < self.h):
            return False
        value = normalize_colour(colour) if colour else None
        if self.pixels[y][x] == value:
            return False
        self.pixels[y][x] = value
        return True

    def clear(self) -> None:
        self.pixels = [[None] * self.w for _ in range(self.h)]

    def mirror_x(self) -> "PixelArt":
        """左右镜像（新图）。"""
        other = PixelArt(self.w, self.h)
        for y in range(self.h):
            other.pixels[y] = list(reversed(self.pixels[y]))
        return other

    def is_empty(self) -> bool:
        return all(pixel is None for row in self.pixels for pixel in row)

    def filled(self) -> int:
        return sum(1 for row in self.pixels for pixel in row if pixel is not None)

    def used_colours(self) -> list[str]:
        """按出现顺序去重（越早用的排前面，serialize 出来的 palette 才稳定）。"""
        seen: list[str] = []
        for row in self.pixels:
            for pixel in row:
                if pixel is not None and pixel not in seen:
                    seen.append(pixel)
        return seen

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> dict:
        """超过 36 色会抛 ``ValueError``（调用方要接住并提示，不要静默丢色）。"""
        colours = self.used_colours()
        if len(colours) > MAX_COLOURS:
            raise ValueError(f"像素画最多 {MAX_COLOURS} 种颜色（现在 {len(colours)} 种）")
        index = {colour: ALPHABET[i] for i, colour in enumerate(colours)}
        rows = [
            "".join(TRANSPARENT if pixel is None else index[pixel] for pixel in row)
            for row in self.pixels
        ]
        return {"w": self.w, "h": self.h, "palette": colours, "grid": rows}

    @classmethod
    def from_dict(cls, raw) -> "PixelArt | None":
        """坏数据一律 ``None``（渲染层会退化成"不画"，绝不崩）。"""
        if not isinstance(raw, dict):
            return None
        try:
            w = int(raw.get("w", DEFAULT_W))
            h = int(raw.get("h", DEFAULT_H))
        except (TypeError, ValueError):
            return None
        if not (1 <= w <= 64 and 1 <= h <= 64):
            return None
        palette_raw = raw.get("palette")
        grid = raw.get("grid")
        if not isinstance(palette_raw, list) or not isinstance(grid, list):
            return None
        palette: list[str] = []
        for item in palette_raw:
            colour = normalize_colour(item)
            if colour is None:
                return None
            palette.append(colour)
        art = cls(w, h)
        for y in range(h):
            line = grid[y] if y < len(grid) else ""
            if not isinstance(line, str):
                return None
            for x in range(w):
                ch = line[x] if x < len(line) else TRANSPARENT
                if ch == TRANSPARENT:
                    continue
                position = ALPHABET.find(ch)
                if position < 0 or position >= len(palette):
                    continue          # 认不出的字符当透明，不整张丢弃
                art.pixels[y][x] = palette[position]
        return art
