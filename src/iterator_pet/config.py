"""User settings, persisted next to the app data folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


# 尺寸档位（用户 2026-10-28 定的四档）：倍率是相对"满血版"的。
# 渲染倍率 scale 越大 → 腔室（仿真像素）越小 → 人偶在屏幕上越大。
SIZE_PRESETS = {
    "portable": (0.40, "便携版（40%）"),
    "youth": (0.75, "青春版（75%）"),
    "full": (1.00, "满血版（100%）"),
    "pro": (1.50, "PRO（150%）"),
}
BASE_SCALE = 1.5          # "满血版"=100% 对应的渲染倍率（本机调出来的值）
DEFAULT_SIZE = "full"


def scale_for(size: str) -> float:
    return round(BASE_SCALE * SIZE_PRESETS.get(size, (1.0, ""))[0], 3)


def nearest_size(scale: float) -> str:
    """老配置里只有 scale（没有 size）时，按最接近的档位补齐。"""
    return min(SIZE_PRESETS, key=lambda key: abs(scale_for(key) - scale))


@dataclass
class Config:
    margin: float = 34.0
    radius: float = 64.0
    click_through: bool = False
    always_on_top: bool = True
    fps: int = 60
    scale: float = BASE_SCALE
    # 尺寸档位：portable / youth / full / pro（托盘菜单里切；scale 由它派生）
    size: str = DEFAULT_SIZE
    # Rain World 安装目录（启动器第一次运行时自动探测并写入；空 = 还没初始化）
    install: str = ""
    # 电光频率档位：off / eco / computing（开发者菜单里切，见 constants.HALO_SPARK_RATES）
    spark_rate: str = "eco"
    # 杀鼠标时的光标特效（档 2：换忙光标 0.3 s）。默认开，可用托盘开关 / 环境变量关掉
    cursor_fx: bool = True


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "IteratorPet"


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> Config:
    path = config_path()
    if not path.is_file():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    known = {f.name for f in fields(Config)}
    if isinstance(raw, dict) and "size" not in raw and "scale" in raw:
        # 老配置：只有 scale，没有档位 → 反推最接近的那一档（不改它的 scale）
        try:
            raw["size"] = nearest_size(float(raw["scale"]))
        except (TypeError, ValueError):
            pass
    cfg = Config(**{k: v for k, v in raw.items() if k in known})
    # size 是唯一真相：scale 永远由档位派生（老配置刚在上面补完 size，这里顺手对齐）
    cfg.scale = scale_for(cfg.size)
    return cfg


def save(cfg: Config) -> None:
    import os
    import traceback

    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    except OSError:
        if os.environ.get("ITERATORPET_DEBUG"):
            print(f"[debug] save() failed for {config_path()}:\n{traceback.format_exc()}")
        else:
            pass
