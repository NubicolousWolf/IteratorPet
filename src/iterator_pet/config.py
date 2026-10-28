"""User settings, persisted next to the app data folder."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Config:
    margin: float = 34.0
    radius: float = 64.0
    click_through: bool = False
    always_on_top: bool = True
    fps: int = 60
    scale: float = 2.2
    # Rain World 安装目录（启动器第一次运行时自动探测并写入；空 = 还没初始化）
    install: str = ""
    # 是否已经提示过"怎么退出我"（Windows 11 把托盘图标折进溢出区，用户找不到）
    hint_shown: bool = False


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
    return Config(**{k: v for k, v in raw.items() if k in known})


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
