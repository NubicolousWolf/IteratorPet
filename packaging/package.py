"""把构建产物打成可直接发给玩家的 zip。

产物：``%LOCALAPPDATA%\\IteratorPet\\dist\\五块卵石-<版本>-win64.zip``
解开是一个 ``五块卵石/`` 文件夹，里面有 ``启动.exe``、``_internal/`` 和 ``使用说明.txt``。

**必须在干净目录里打包/验证**：仓库所在的 ``F:\\RainworldIteratorPet`` 带继承性的
Low 完整性标签，exe 在里面跑会被降级到 Low IL，任务栏拒绝它注册托盘图标
（详见 ``build.py`` 顶部）。所以这里默认跟 build 用同一个 ``%LOCALAPPDATA%`` 产物目录。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "dist"
FOLDER = "五块卵石"
EXE_NAME = "启动"

README = """五块卵石 · 桌面宠物
==================

怎么开始
--------
双击 启动.exe 就行。

第一次运行会自己找正版 Rain World（Steam 库、注册表都查一遍）。
找不到就先用内置图元跑起来（不会再弹窗问你）；想手动指定：
    启动.exe --setup
或者设环境变量 RAINWORLD_DIR 指向游戏根目录
（里面有 RainWorld.exe 和 RainWorld_Data 文件夹）。

之后每次运行都直接启动，不再问。

怎么退出
--------
三选一，随便哪个：
  1. 右键任务栏托盘里的图标 → 退出。
     Windows 11 默认会把新图标折进任务栏的 ^ 里，看不见就点一下 ^。
  2. 右键桌面上的五块卵石本身 → 退出（开了「鼠标穿透」时这一条不可用）。
  3. 按 Ctrl+Alt+Q，任何时候都能退出。

托盘菜单里还能：暂停/继续、开关鼠标穿透、开关开发者面板。

**别把本文件夹放进带了"低完整性"标记的目录再运行**（极少见，一般是某些
沙箱/下载工具建的目录）。Windows 会拒绝 Low 完整性进程注册托盘图标，
表现就是"托盘里没有图标"。普通目录（桌面、下载、D 盘自建文件夹）都没问题。

数据放在哪
----------
  %LOCALAPPDATA%\\IteratorPet\\config.json    设置
  %LOCALAPPDATA%\\IteratorPet\\cache\\        从你本机游戏里切出来的图元
  %LOCALAPPDATA%\\IteratorPet\\launcher.log   启动过程日志（排障用）

卸载：删掉本文件夹，再删 %LOCALAPPDATA%\\IteratorPet 即可。

不会往你的游戏目录写任何东西
------------------------------
只读地读 resources.assets / palette26，不改动、不复制游戏资源到外面。

Rain World 及其全部美术、代码版权归 Videocult / Akupara Games 所有。
"""


def version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None, help="构建产物目录（与 build.py 一致）")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    dist = out_dir / EXE_NAME
    exe = dist / f"{EXE_NAME}.exe"
    if not exe.is_file():
        print(f"先跑 packaging/build.py（找不到 {exe}）")
        return 2

    stage = out_dir / FOLDER
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(dist, stage)
    (stage / "使用说明.txt").write_text(README, encoding="utf-8-sig")

    target = out_dir / f"{FOLDER}-v{version()}-win64.zip"
    if target.exists():
        target.unlink()
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, Path(FOLDER) / path.relative_to(stage))
                count += 1
    size = target.stat().st_size / (1024 * 1024)
    print(f"[pack] {target}  ({count} 个文件, {size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
