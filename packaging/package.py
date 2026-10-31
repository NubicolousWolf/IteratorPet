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

README = r"""迭代器 · 桌面宠物
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
  2. 右键桌面上的迭代器本身 → 退出（开了「鼠标穿透」时这一条不可用）。
  3. 按 Ctrl+Alt+Q，任何时候都能退出。

托盘菜单里还能：**尺寸**（便携版 40% / 青春版 75% / 满血版 100% / PRO 150%）、
**角色**（五块卵石 / 仰望皓月）、**对话**（开 / 关）、**编辑对话文件**、**打开数据文件夹**、
电光频率（关 / 节能 / computing）、暂停/继续、开关鼠标穿透、开关开发者面板。
觉得太大就先切到"便携版"或"青春版"，切完立刻生效并记住。

**可以拖着玩**：左键按住**人偶**（头或身体）拖着走，松手会顺着拖动方向滑一小段；
左键按住底部那个**灰色滑车**拖着走，滑车沿屏幕边缘的轨道跟手，人偶被机械臂拖着挪。
（注意：开着"鼠标穿透"时收不到鼠标，拖不了——右键托盘图标把穿透关掉即可。）

**别把本文件夹放进带了"低完整性"标记的目录再运行**（极少见，一般是某些
沙箱/下载工具建的目录）。Windows 会拒绝 Low 完整性进程注册托盘图标，
表现就是"托盘里没有图标"。普通目录（桌面、下载、D 盘自建文件夹）都没问题。

大语言模型
----------
右键人偶 →「大语言模型…」：勾「启用大语言模型」→ 选服务（默认 DeepSeek）→ 填 API Key →
点「获取模型」 → 按你的喜好勾选控制行为/阅读文件并设置会发给LLM的历史对话条数 → 保存。

  1. （勾了才生效）LLM 可以有限地控制行为。目前只是一个框架，所以迭代器能做的很有限：
     过来看看你、靠近你，以及企图用神秘小技能杀掉你（五块卵石特供！）。
  2. （勾了才生效）LLM 可以阅读你拖到她们脑袋上的文件。.txt .md .csv .doc .docx .xlsx
     会阅读正文（每个文件最多 10000 字，超出部分截断），其他文件只读文件名。
     可识别的区域会出现「+复制」。
  3. 以「即时广播-」开头的文件会被当成**直接对迭代器说话**，不读正文。
  4. 提示词里有角色输出字数限制和简易反注入，别拿迭代器当通用助手用。

和这个功能有关的文件（都在本文件夹里，可以直接用记事本改）：
    llm.json              连接与采样设置（**里面有明文 api_key，别把这个文件发给别人**）
    worldbook.json        世界书
    characters/*.json     角色卡
    chats/<角色>.jsonl    对话历史（长期保存）

隐私提醒
--------
  * 拖给她的文本，**正文会被发给你自己选的服务商**（DeepSeek 等），并且**长期写进
    chats/<角色>.jsonl**。不建议把隐私内容喂给她。
  * 角色卡和世界书里写的内容同样会随每次请求发出去。

数据放在哪
----------
  全都在**本文件夹**（和 启动.exe 并排，不往 C 盘写）：
    config.json     设置
    lines.json      迭代器说的话（可以自己加，两秒内生效；菜单「编辑对话文件」直接打开）
    launcher.log    启动过程日志（排障用）
    cache/          从你本机游戏里切出来的图元（删了会自动重切）
    llm.json        大语言模型的连接设置（第一次启动才生成）
    worldbook.json  世界书（第一次启动才生成）
    characters/     角色卡（第一次启动才生成）
    chats/          对话历史（第一次启动才生成）
  如果把这个文件夹放在只读位置（例如 Program Files），会退回 %LOCALAPPDATA%\IteratorPet\。

卸载：删掉本文件夹即可（数据都在里面）。

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

    (stage / "使用说明.txt").write_text(README, encoding="utf-8-sig")

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
