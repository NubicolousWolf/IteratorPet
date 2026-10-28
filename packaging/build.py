"""打包脚本（PyInstaller onedir + 裁剪）。

为什么用 Python 而不是 .cmd：cmd.exe 按控制台代码页解析批处理文件的字节，中文注释/中文
exe 名会被拆成乱码当命令执行（我们已经踩过两次，报错信息还长得像别的问题）。这里全部
用 ``subprocess.run(list)``，路径和中文都由 Python 处理，没有编码这一层。

用法：
    python packaging/build.py            # 全量构建（约 3 分钟）
    python packaging/build.py --no-prune # 只构建，不删运行时

两个必须记住的坑：
1. ``--specpath`` 会让 ``--add-data`` / ``--icon`` 的相对路径按 spec 目录解析 → 这里一律传绝对路径。
   （早先就是这么静默漏掉 tray.png 的：PyInstaller 找不到 data 文件时不报错，退出码还是 0。）
2. 本机同时装了 PyQt5，collect UnityPy 会把它拖进来，触发 "multiple Qt bindings" 直接中止 →
   必须显式 exclude 掉别的绑定。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # IteratorPet/
SRC = ROOT / "src"
EXE_NAME = "启动"
DIST = ROOT / "dist" / EXE_NAME

# 注意：numpy 看似用不到，但 UnityPy 的贴图解码路径会碰它；排掉之后 exe 会在切素材时
# 以 0xC0000409 直接死掉（连 traceback 都没有）。所以这里只排真没用的那几个。
EXCLUDES = (
    "scipy", "pandas", "matplotlib", "lxml", "cryptography",
    "PyQt5", "PyQt6", "PySide2", "tkinter", "IPython", "jedi",
)

# 删掉的 Qt 运行时：我们只用 Core/Gui/Widgets。
QT_FILES = (
    "opengl32sw.dll", "Qt6Quick.dll", "Qt6QuickWidgets.dll", "Qt6Qml.dll",
    "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll", "Qt6Pdf.dll", "Qt6Network.dll",
    "Qt6OpenGL.dll", "Qt6OpenGLWidgets.dll", "Qt6Svg.dll", "Qt6Test.dll",
)
QT_DIRS = ("translations",)
QT_PLUGIN_DIRS = ("generic", "networkinformation", "platforminputcontexts", "tls",
                  "sqldrivers", "iconengines")
QT_IMAGE_PLUGINS = ("qpdf.dll", "qsvg.dll", "qtga.dll", "qtiff.dll", "qwbmp.dll",
                    "qwebp.dll", "qicns.dll")


def build() -> None:
    args = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "run_pet.py"),
        "--name", EXE_NAME,
        "--noconfirm", "--noconsole",
        "--paths", str(SRC),
        "--add-data", f"{SRC / 'iterator_pet' / 'assets' / 'tray.png'};iterator_pet/assets",
        # UnityPy 的 export/__init__ 会连带 import AudioClipConverter -> fmod_toolkit，
        # 而 fmod_toolkit 用 ctypes 去加载包内的 libfmod/.../fmod.dll；只收 .pyd 不收数据，
        # 冻结后就会 FileNotFoundError，切素材这一步静默退化（实机表现为"没有游戏素材"）。
        "--collect-all", "UnityPy",
        "--collect-all", "fmod_toolkit",
        "--collect-all", "texture2ddecoder",
        "--collect-all", "etcpak",
        "--collect-all", "astc_encoder",
        # astc_encoder 在 import 时就会 archspec.cpu.host() 读 JSON 数据表；
        # PyInstaller 默认只收 py 文件，缺 json 会在切素材时抛 FileNotFoundError。
        "--collect-all", "archspec",
        "--icon", str(ROOT / "packaging" / "icon.ico"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
    ]
    for name in EXCLUDES:
        args += ["--exclude-module", name]
    print("[build] PyInstaller ...", flush=True)
    subprocess.run(args, check=True)


def prune() -> None:
    """删掉用不到的 Qt 运行时 —— 先保证能跑，再删。"""
    qt = DIST / "_internal" / "PySide6"
    removed = 0
    for name in QT_FILES:
        target = qt / name
        if target.is_file():
            target.unlink()
            removed += 1
    for name in QT_DIRS:
        target = qt / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
    for name in QT_PLUGIN_DIRS:
        target = qt / "plugins" / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
    for name in QT_IMAGE_PLUGINS:
        target = qt / "plugins" / "imageformats" / name
        if target.is_file():
            target.unlink()
            removed += 1
    print(f"[build] 裁剪了 {removed} 项 Qt 运行时")


def size_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-prune", action="store_true")
    args = parser.parse_args()

    build()
    if not args.no_prune:
        prune()

    exe = DIST / f"{EXE_NAME}.exe"
    if not exe.is_file():
        print("[build] 失败：没生成 exe", file=sys.stderr)
        return 1
    print(f"[build] {exe}")
    print(f"[build] 体积 {size_mb(DIST):.1f} MB（exe {exe.stat().st_size / 1048576:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
