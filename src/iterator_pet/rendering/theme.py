"""Palette for the iterator pet.

The colours follow the iterator chambers in Rain World: dusty rose cloth over
a dark, warm frame, with a single saturated accent for the rail glow.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    rail_line: str = "#7d6157"
    rail_glow: str = "#ff9a4d"
    rail_clamp: str = "#c9a08a"
    arm_dark: str = "#2b2528"
    arm_mid: str = "#5d5357"
    arm_light: str = "#a3979a"
    arm_rim: str = "#d8c9c4"
    cord_dark: str = "#431f2a"
    cord_mid: str = "#7a3446"
    cord_light: str = "#b8636f"
    robe_dark: str = "#8f4a60"
    robe_mid: str = "#cf869c"
    robe_light: str = "#f0c3d0"
    head_dark: str = "#a56178"
    head_mid: str = "#e8aabc"
    head_light: str = "#ffd9e2"
    eye: str = "#241720"
    eye_glow: str = "#ffe8ef"
    pearl: str = "#f0dcc8"
    glow: str = "#ff9ec4"


DEFAULT_PALETTE = Palette()
