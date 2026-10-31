"""把构建产物打成可直接发给玩家的 zip。

产物：``%LOCALAPPDATA%\\IteratorPet\\dist\\迭代器-<版本>-win64.zip``
解开是一个 ``迭代器/`` 文件夹，里面有 ``启动.exe``、``_internal/`` 和 ``使用说明.txt``。

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
FOLDER = "迭代器"
EXE_NAME = "启动"

def readme_text() -> str:
    """打包版的使用说明 = **仓库 README.md**（用户 2026-10-31：以后直接用 README）。

    以前这里内嵌了一份玩家向说明，和 README 各写一遍 → 两份迟早漂移（发布说明已经
    为这个坑付过一次代价）。现在只留一个真相源：改 README 就是改使用说明。
    """
    return (ROOT / "README.md").read_text(encoding="utf-8")




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
    # 运行时数据不进包。**注意：`ignore_patterns` 是按「文件名」匹配整棵树的**，
    # 用它排除根目录的 `llm.json` / `worldbook.json` / `characters` 会连
    # `_internal/iterator_pet/assets/llm/` 里的**随包模板**一起删掉
    # （2026-10-30 实际踩到：zip 里 assets/llm 一个文件都没有，玩家拿到的 LLM
    #   功能没有任何默认世界书和角色卡）。所以这里只按目录名排除 cache/sprites，
    # 根目录那些运行时文件**拷完之后逐个删**，作用域精确到根。
    #
    # 为什么必须删：llm.json 里有开发机的**明文 api_key**（审计 A9）；chats/ 是真实
    # 对话历史（隐私）；config.json 带开发机设置；worldbook.json / characters/ 在根目录
    # 是开发机自己改过的那份，随包模板的真相源是 `assets/llm/`，不能两处漂移。
    skip = shutil.ignore_patterns("cache", "sprites")
    shutil.copytree(dist, stage, ignore=skip)
    for name in ("config.json", "launcher.log", "llm.json", "worldbook.json"):
        target = stage / name
        if target.is_file():
            target.unlink()
    for name in ("chats", "characters"):
        target = stage / name
        if target.is_dir():
            shutil.rmtree(target)
    # 保证包里有默认台词表：玩家解压后它就在 启动.exe 旁边，一眼能看到、直接能改。
    if not (stage / "lines.json").exists():
        import json as _json
        import sys as _sys

        # constants.py 只依赖 colorsys/math，脚本里补一下 src 就能直接读默认台词，
        # 不用在这里复制一份（也不会因为 shell 没设 PYTHONPATH 就 ModuleNotFoundError）。
        _src = str(ROOT / "src")
        if _src not in _sys.path:
            _sys.path.insert(0, _src)
        from iterator_pet.simulation import constants as _constants

        (stage / "lines.json").write_text(
            _json.dumps(_constants.DEFAULT_LINES, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("[pack] 补上默认 lines.json")

    (stage / "使用说明.txt").write_text(readme_text(), encoding="utf-8-sig")

    # 出厂自检：下面任何一条错，都是"玩家拿到残包"而且**不会报错**的形态。
    # （tray.png 当年就是这么静默漏掉的，退出码还是 0。）
    problems: list[str] = []
    if not (stage / f"{EXE_NAME}.exe").is_file():
        problems.append(f"缺 {EXE_NAME}.exe")
    llm_assets = stage / "_internal" / "iterator_pet" / "assets" / "llm"
    for relative in ("llm.json", "worldbook.json",
                     "characters/pebbles.json", "characters/past_moon.json",
                     "characters/seven_red_suns.json",
                     "characters/no_significant_harassment.json"):
        if not (llm_assets / relative).is_file():
            problems.append(f"缺包内模板 assets/llm/{relative}")
    for leaked in ("llm.json", "worldbook.json", "config.json", "launcher.log",
                   "chats", "characters", "cache"):
        if (stage / leaked).exists():
            problems.append(f"根目录泄漏了运行时数据：{leaked}")
    if problems:
        for line in problems:
            print(f"[pack] 失败：{line}")
        return 3

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
