"""角色外观参数表：同一套 ``OracleGraphics`` 按 ``OracleID`` 分支出来的那些值。

源码里没有"角色类"，只有 ``IsPebbles`` / ``IsMoon`` / ``IsPastMoon`` 这些布尔量在
``og.cs`` 里分支。这个模块把它们收成数据，渲染层只读 ``state.appearance``。

**每个数字都必须指到一行源码或一张实机截图**（AGENTS §1 硬规则 2）。指不到的
（比如光环颜色——源码是黑环、颜色来自房间灯）单独标注来源。

- ``PEBBLES`` = 五块卵石，``OracleID.SS``，这些值就是 `constants.py` 里现在那套。
- ``PAST_MOON`` = 过去之月，``OracleID.DM``，房间 ``DM_AI``（倾盆大雨 DLC 里那位）。
  注意 **DM 不满足 ``IsMoon``**，所以它走的是 ``!IsMoon`` 那一大段分支：
  有光环、有完整脐带、有长袍、有 ``killSprite``。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from ..rendering.pixel_art import PixelArt, normalize_colour
from . import constants as C


@dataclass(frozen=True)
class Appearance:
    key: str
    # og.cs:2288-2303  SkinColor()
    skin: tuple[int, int, int]
    # og.cs:1946-1949  眼睛颜色；Pebbles 是 FSprite 默认白
    eye: tuple[int, int, int]
    # og.cs:2169  scaleY = Lerp(1, IsPebbles ? 2 : 3, eyesOpen)
    eye_scale_y: float
    # og.cs:1913-1916  IsMoon || IsPastMoon 时 chunk0 的 scaleY = (rad+2)/10 -> 16 px
    body0_h: float
    # og.cs:1939  neckSprite.scaleX = (IsPebbles || IsSaintPebbles) ? 3 : 4
    neck_width: float
    # og.cs:2197  手（袖子）半宽
    hand_half_w: float
    # og.cs:2224  脚带半宽：Pebbles 恒 2；其它 Lerp(4, 2, sqrt(f))
    leg_half_w: float
    leg_taper: bool
    # og.cs:341-361  Gown.Color(f)
    gown: Callable[[float], tuple[int, int, int]]
    # og.cs:2238-2254  第三只眼（IsMoon || IsPastMoon || IsStraw）
    third_eye: bool
    third_eye_colour: tuple[int, int, int] | None
    # 光环。源码里环/bit 是**黑色**，颜色来自房间灯（`ForegroundLights`），
    # 桌面端没有光照系统，所以这个值是**实机截图采样**（见各实例的注释）。
    halo: tuple[int, int, int]
    halo_spark: tuple[int, int, int]
    # user 2026-10-29 拍板不做 MoonLight：`oracle.cs:658-664` 那盏 Color(0,0,1)/α0.8/
    # 半径 200 的灯在桌面端没有"暗房间"可照，实测就是一整块蓝布（_work/moon-moonlight.png）。
    # 同理 `MoonSigil`（og.cs:1973-1977/2255-2276）也不做，记在 ROADMAP §8 待办里。
    # 用户 2026-10-29 拍板：DM 删掉杀鼠标、只保留挣脱（PLAN-moon §4.1）
    tremble_only: bool = False
    # 自定义头顶部件（v0.1.9 起玩家可画）。``None`` = 用源码那个被压扁的圆（第三只眼）。
    # 这里存的是**纯像素矩阵**，不是 QPixmap：渲染层每帧都要读这个数据类，
    # 往 frozen dataclass 里塞 Qt 资源会连带引入缓存失效/线程/析构三个问题。
    headpiece: PixelArt | None = None
    # 1 格 = 多少仿真像素。24×20 的画布 × 0.5 = 12×10 仿真 px，正好盖住 11.1×9.1 的头
    # （renderer.py:974）。见 docs/PLAN-custom-iterator.md §3.5。
    headpiece_scale: float = 0.5
    # 侧边"耳麦 + 触角"（`PhoneSprite` 两个圆 + `LizardScaleA1`）。这三个默认值就是
    # 渲染层里原来写死的那三个深灰（设计增补，源码没有对应的颜色常数），
    # 接通前后观感一致；从 2026-10-31 起玩家可以在工作室里改。
    pod_colour: tuple[int, int, int] = (58, 52, 56)
    pod_rim_colour: tuple[int, int, int] = (32, 28, 32)
    antenna_colour: tuple[int, int, int] = (40, 34, 38)
    # 玩家画的侧边部件（**左右镜像**同一张图）。None = 用源码那两个圆 + 一根触角。
    antenna_art: PixelArt | None = None
    antenna_scale: float = 0.5
    #: 这张图里"耳麦圆心"落在**第几格**（col, row，都从画布左上角数）。
    #:
    #: ``None`` = 走第一版的隐含约定（横向居中、纵向 ``h-1-5.5``，见
    #: :func:`antenna_anchor`）。2026-10-31 画板扩成"半个脑袋"之后它变成**偏心**的
    #: （6, 24），"居中"这条假设不成立了，锚点必须跟着图一起存，
    #: 否则玩家已经画好的天线会整体漂 2.5 px。
    antenna_anchor: tuple[float, float] | None = None


def _moon_gown(f: float) -> tuple[int, int, int]:
    """``Gown.Color`` 的 default 分支（DM 落在这里，不是 IsMoon 那支）。

    `HSL(Lerp(.08, .02, f^2), Lerp(.6, .4, f), .4)` —— 与 Pebbles 同色相，
    但饱和度和亮度都低一档（更暗、更灰的橙）。
    """
    return C.hsl_to_rgb8(C._lerp(0.08, 0.02, f ** 2), C._lerp(0.6, 0.4, f), 0.4)


PEBBLES = Appearance(
    key="pebbles",
    skin=C.SKIN_COLOUR,                 # (255,102,203)
    eye=C.EYE_COLOUR,                   # 纯白
    eye_scale_y=C.EYE_SCALE_Y,          # 2
    body0_h=12.0,                       # Circle20 * rad/10 = 0.6 -> 12 px
    neck_width=3.0,                     # og.cs:1939
    hand_half_w=4.0,                    # og.cs:2197
    leg_half_w=2.0,                     # og.cs:2224
    leg_taper=False,
    gown=C.gown_colour,                 # HSL(.08->.02, 1->.8, .5)
    halo=C.HALO_COLOUR,                 # (215,122,88) 实机截图采样
    # 2026-10-31：`halo_spark` 以前是**死字段**（渲染层写死 halo×1.05），现在接通了。
    # 这两个值就是"接通前实际画出来的颜色"（int(215*1.05)=225 …），所以观感零变化；
    # 从这一版起它才真正可改。（`constants.HALO_SPARK_COLOUR` 那个从未生效的意图一并删了。）
    halo_spark=(225, 128, 92),
    third_eye=False,
    third_eye_colour=None,
)

PAST_MOON = Appearance(
    key="past_moon",
    skin=(33, 135, 175),                # og.cs:2298-2300 (0.13,0.53,0.69)
    eye=(5, 0, 0),                      # og.cs:1948 (0.02,0,0)
    eye_scale_y=3.0,                    # og.cs:2169 非 Pebbles 分支
    body0_h=16.0,                       # og.cs:1913-1916 (rad+2)/10 * 20 = 16 px
    neck_width=4.0,                     # og.cs:1939
    hand_half_w=2.0,                    # og.cs:2197
    leg_half_w=2.0,                     # og.cs:2224 末端
    leg_taper=True,                     # 起点 4 -> 末端 2，按 sqrt(f)
    gown=_moon_gown,
    # 实机采样（_work/moon_frames/f02.png，以人偶为心、半径 52–86 px 的环带）：
    # 均值 (168,76,88)、亮端 (196,103,116)。取亮端当基色，电光再暗一档。
    # 电光=拍摄基色×1.05（接通前渲染层的写死公式），保持观感不变。
    halo=(196, 103, 116),
    halo_spark=(205, 108, 121),
    tremble_only=True,
    third_eye=True,
    # og.cs:2373-2376  Color.Lerp(new Color(1,0,1), skin, 0.3f)
    third_eye_colour=(
        int(255 + (33 - 255) * 0.3),
        int(0 + (135 - 0) * 0.3),
        int(255 + (175 - 255) * 0.3),
    ),
)

CHARACTERS = {"pebbles": PEBBLES, "past_moon": PAST_MOON}

#: 玩家能挑的"骨骼基底"。外观自定义只换颜色/头饰，骨骼差异（眼高、躯干高度、
#: 仪式是否杀鼠标）来自基底 —— 见 docs/PLAN-custom-iterator.md §1。
BASE_TEMPLATES = {"pebbles": "五块卵石", "past_moon": "仰望皓月"}

#: 天线画板的几何（**整条侧边部件 + 半个脑袋**，不只是那根杆）。
#:
#: 数值都来自 `renderer.py::draw_puppet_head` 里的字面量：
#: 耳麦直径 `Lerp(3.5, 5.0, |look.x|)` → 最大 5.0 px，内圈 ×0.8，触角 `3.0 × 11.2` px。
#: 画板 1 格 = ``antenna_scale``（默认 0.5 仿真 px），所以：
#:   * 耳麦圆心在横向第 ``anchor_col`` 格、纵向第 ``anchor_row`` 格（见 `antenna_board_anchor`）；
#:   * 触角从圆心朝外 11.2 px = 22.4 格。
#:
#: 2026-10-31（用户第三条）：画板从"只有触角"扩到"触角尖 → 下巴、耳麦外缘 → 脑袋中线"。
#: 扩的理由不是好看：贴着下巴与脸颊的那一圈**在屏幕上离天线最近**，想画须子/挂饰
#: 就必须能画到那儿。第一版只给 3×11.2 的触角（8×24 格），连耳麦和内圈都在画布外。
ANTENNA_POD_ROWS = 5.5
ANTENNA_POD_DIAMETER_PX = 5.0
ANTENNA_STALK_WIDTH_PX = 3.0
ANTENNA_STALK_LENGTH_PX = 11.2

#: 耳麦圆心 → **脑袋中线**的横向距离（画板右侧要够到这条线）。
#:
#: 来源 `og.cs:2185`：耳麦挂在 `head + perp * clamp(Lerp(7, 5, |look.x|) * sign, -11, 11)`。
#: 正脸（`look.x = 0`）时是 7.0 px；侧看时缩到 5.0 px —— 取 7.0 是因为画板画的是
#: "正着看的脸"（特写预览也固定在 look=0），侧看时中线会落在画布里面一点，不会画不到。
ANTENNA_HEAD_MIDLINE_PX = 7.0

#: 耳麦圆心 → **下巴最低点**（画板下缘要够到这儿）。
#:
#: **实测值，指不到单行源码**（属于 §1 规则 2 的"设计增补"，推导链写在这里）：
#: 头与躯干的平衡距离是约束解出来的，实测 `|head - body0| = 13.99 px`
#: （1138×608、seed 1234、跑满 320 tick）；
#: 下巴圆心 = `renderer.py:1024` 的 `head + (body0-head)*0.15` → 2.10 px；
#: 下巴半径 = `Circle20 * HEAD_RAD/15 / 2` = 3.33 px；合计 **5.43 px**。
#: 它比头精灵的下缘（4.55 px）还低一格，所以画板下缘按下巴算，不按脑袋算。
ANTENNA_CHIN_BOTTOM_PX = 5.43


def antenna_pod_center_row(height: int) -> float:
    """**第一版**的耳麦圆心行：从底部往上 ``ANTENNA_POD_ROWS`` 格。

    只在"没存锚点的旧图"上还用（见 :func:`antenna_anchor`）。新画板的圆心行是
    ``antenna_board_anchor()`` 给的、从**顶部**数的 24 格，两者不是一回事。
    """
    return height - 1 - ANTENNA_POD_ROWS


def antenna_board_anchor(cell: float = 0.5) -> tuple[float, float]:
    """新画板的耳麦圆心在**第几格**（col, row，都从左上角数）。

    左/上要给"圆外那半圈 + 整根触角"留位置，右/下要给"脑袋中线 + 下巴"留位置，
    所以圆心是**偏左上**的（6, 24），不是画布中心。这也是为什么锚点要跟着图一起存：
    画板不对称了，"居中"这条隐含假设就不成立了。
    """
    cell = max(0.05, float(cell))
    outward = int(ANTENNA_POD_DIAMETER_PX * 0.5 / cell) + 1     # 圆心左侧（外）1 格余量
    upward = int(ANTENNA_STALK_LENGTH_PX / cell) + 2            # 圆心上方（触角尖）2 格余量
    return float(outward), float(upward)


def antenna_anchor(
    width: int, height: int, anchor=None
) -> tuple[float, float]:
    """这张图画在哪个位置：**耳麦圆心的格子坐标**（col, row，从左上角数）。

    两种含义不同的旧约定必须都认，否则玩家已经画好的天线会整体漂 2.5 px：

    * ``anchor`` 给了（v0.2.1 起新画的图会随卡存）→ 就是它；
    * 没给（第一版的 12×29 图）→ 横向居中、纵向 ``h-1-5.5``，也就是第一版的公式。
    """
    if anchor is not None:
        try:
            col, row = float(anchor[0]), float(anchor[1])
            return col, row
        except (TypeError, ValueError, IndexError):
            pass
    return width * 0.5, antenna_pod_center_row(height)


def antenna_board_size(cell: float = 0.5) -> tuple[int, int]:
    """整块天线画板要多少格：**触角尖 → 下巴**、**耳麦外缘 → 脑袋中线**。"""
    cell = max(0.05, float(cell))
    outward = int(ANTENNA_POD_DIAMETER_PX * 0.5 / cell) + 1
    inward = int(ANTENNA_HEAD_MIDLINE_PX / cell) + 2            # 到中线再留 2 格
    upward = int(ANTENNA_STALK_LENGTH_PX / cell) + 2
    downward = int(ANTENNA_CHIN_BOTTOM_PX / cell) + 2           # 到下巴底再留 2 格
    return max(4, outward + inward), max(6, upward + downward)


HEADPIECE_MODES = {
    "inherit": "跟随基底（皓月有圆点、卵石没有）",
    "none": "无",
    "pixel": "自定义像素画",
}


def default_headpiece_mode(key: str) -> str:
    """头顶部件的默认模式：内置两位 ``inherit``，自定义角色 ``none``。

    自定义角色套了皓月模板时**不该白得一颗第三只眼**（用户 2026-10-31 澄清：
    要改的是"自定义角色套模板时的默认值"，不是删掉仰望皓月本人的那颗）。
    """
    return "inherit" if key in CHARACTERS else "none"


def apply_headpiece_default(key: str, draft: dict | None) -> dict:
    """把"头顶部件默认值"补进外观草稿 → 新字典（不改入参）。

    为什么要有这个函数：**预览与桌面必须用同一份默认值**。v0.1.9 的工坊预览
    走 ``puppet_tab.collect()``（自定义角色写了 ``headpiece_mode="none"``），
    而桌面走 ``appearance_of`` 直接读卡——卡里没这个键时两边结论相反，
    就会出现"预览里没有圆点、桌面上多一颗"这种最难查的偏差。

    ``inherit`` 是"什么都不写"：内置两位在卡里没有外观块时必须继续命中
    ``build_appearance`` 的"直接返回模板"那条早退（= 100% 源码数值）。
    """
    draft = dict(draft or {})
    if str(draft.get("headpiece_mode") or "").strip():
        return draft
    mode = default_headpiece_mode(key)
    if mode != "inherit":
        draft["headpiece_mode"] = mode
    return draft


def _rgb(value) -> tuple[int, int, int] | None:
    colour = normalize_colour(value)
    if colour is None:
        return None
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def _gown_between(top: tuple[int, int, int], bottom: tuple[int, int, int]):
    """两点线性插值顶替源码那条 HSL 曲线（玩家只想给"上缘/下摆"两个色）。"""

    def colour(f: float) -> tuple[int, int, int]:
        return C.mix(top, bottom, max(0.0, min(1.0, float(f))))

    return colour


def build_appearance(key: str, base: str = "pebbles", override: dict | None = None) -> Appearance:
    """把卡里的外观草稿叠到某个基底上 → 一个完整的 :class:`Appearance`。

    ``override`` 为空时**直接返回基底**（内置两位不改色 = 100% 源码数值，一个字段都不碰）。
    字段认不出/越界就保持基底原值，绝不因为玩家手改坏了一个颜色就让人偶变色或崩掉。
    """
    template = CHARACTERS.get(base) or PEBBLES
    if not isinstance(override, dict) or not override:
        return template if key == template.key else replace(template, key=key)

    patch: dict = {"key": key}

    for field in ("skin", "eye", "halo", "halo_spark"):
        colour = _rgb(override.get(field))
        if colour is not None:
            patch[field] = colour

    top = _rgb(override.get("gown_top"))
    bottom = _rgb(override.get("gown_bottom"))
    if top is not None and bottom is not None:
        patch["gown"] = _gown_between(top, bottom)
    elif top is not None or bottom is not None:
        # 只给了一个色：另一头用基底的端点，玩家不会看到"半个袍子没上色"
        top = top or template.gown(0.0)
        bottom = bottom or template.gown(1.0)
        patch["gown"] = _gown_between(top, bottom)

    skin = patch.get("skin", template.skin)
    mode = str(override.get("headpiece_mode") or "").strip().lower()
    if mode == "none":
        # 明确不要头饰：连源码那颗第三只眼一起关掉（自定义角色套皓月模板时的默认值）
        patch["third_eye"] = False
        patch["third_eye_colour"] = None
        patch["headpiece"] = None
    elif mode == "pixel":
        art = PixelArt.from_dict(override.get("headpiece_pixels"))
        if art is not None and not art.is_empty():
            patch["headpiece"] = art
            # 自己画了 → 不再叠源码那颗圆点
            patch["third_eye"] = False
            patch["third_eye_colour"] = None
        else:
            # 选了"自定义像素画"但画布是空的 → 当作没开，别留一个看不见的状态
            patch["headpiece"] = None
    # "inherit"（或没给）：**一个字段都不碰**，跟随基底的源码行为。
    # 2026-10-31 用户澄清：要改的是"自定义角色套皓月模板时的默认值"，
    # **不是**删掉仰望皓月本人的第三只眼（og.cs:2238-2254 给 DM 画的那颗）。

    scale = override.get("headpiece_scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        patch["headpiece_scale"] = max(0.2, min(2.0, float(scale)))

    for field in ("pod_colour", "pod_rim_colour", "antenna_colour"):
        colour = _rgb(override.get(field))
        if colour is not None:
            patch[field] = colour

    antenna = PixelArt.from_dict(override.get("antenna_pixels"))
    if antenna is not None and not antenna.is_empty():
        patch["antenna_art"] = antenna
    antenna_scale = override.get("antenna_scale")
    if isinstance(antenna_scale, (int, float)) and not isinstance(antenna_scale, bool):
        patch["antenna_scale"] = max(0.2, min(2.0, float(antenna_scale)))
    # 耳麦锚点：给了就记下（画板扩尺寸后靠它把图摆回原位）；没给/写坏了就保持 None，
    # 让 `antenna_anchor()` 走第一版的老约定。
    anchor = override.get("antenna_anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        try:
            patch["antenna_anchor"] = (float(anchor[0]), float(anchor[1]))
        except (TypeError, ValueError):
            pass

    return replace(template, **patch)


def appearance_for(key: str) -> Appearance:
    return CHARACTERS.get(key, PEBBLES)
