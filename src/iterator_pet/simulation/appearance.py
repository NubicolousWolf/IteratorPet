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

from dataclasses import dataclass
from typing import Callable

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
    # 光环。源码里环/bit 是**黑色**，颜色来自房间灯（`ForegroundLights`），
    # 桌面端没有光照系统，所以这个值是**实机截图采样**（见各实例的注释）。
    halo: tuple[int, int, int]
    halo_spark: tuple[int, int, int]
    # og.cs:2238-2254  第三只眼（IsMoon || IsPastMoon || IsStraw）
    third_eye: bool
    third_eye_colour: tuple[int, int, int] | None
    # user 2026-10-29 拍板不做 MoonLight：`oracle.cs:658-664` 那盏 Color(0,0,1)/α0.8/
    # 半径 200 的灯在桌面端没有"暗房间"可照，实测就是一整块蓝布（_work/moon-moonlight.png）。
    # 同理 `MoonSigil`（og.cs:1973-1977/2255-2276）也不做，记在 ROADMAP §8 待办里。
    # 用户 2026-10-29 拍板：DM 删掉杀鼠标、只保留挣脱（PLAN-moon §4.1）
    tremble_only: bool = False


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
    halo_spark=C.HALO_SPARK_COLOUR,
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
    halo=(196, 103, 116),
    halo_spark=(150, 72, 86),
    third_eye=True,
    # og.cs:2373-2376  Color.Lerp(new Color(1,0,1), skin, 0.3f)
    third_eye_colour=(
        int(255 + (33 - 255) * 0.3),
        int(0 + (135 - 0) * 0.3),
        int(255 + (175 - 255) * 0.3),
    ),
    tremble_only=True,
)

CHARACTERS = {"pebbles": PEBBLES, "past_moon": PAST_MOON}


def appearance_for(key: str) -> Appearance:
    return CHARACTERS.get(key, PEBBLES)
