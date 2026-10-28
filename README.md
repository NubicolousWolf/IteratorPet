# Iterator Pet · 雨世界迭代器桌宠

一只迭代器桌宠，暂时是fp桌宠。

> 预览图由本仓库代码离线渲染：`python -m iterator_pet.main --preview out.png --width 1280 --height 720 --scale 2.2`

## 发布版（双击 `启动.exe`）

发布包解开后直接双击 `启动.exe`（`五块卵石/启动.exe`）。整包 56 MB（解 145 MB）。

### 怎么退出（三个入口，因为 Windows 11 会把托盘图标折进 `^`）

1. 右键托盘图标 → 退出（看不见就点任务栏的 `^`；首次运行会弹一次气泡提示）；
2. **右键桌面上的人偶本身** → 退出（开了「鼠标穿透」时这条不可用，因为窗口收不到鼠标）；
3. **Ctrl+Alt+Q**，任何时候都能退（全局热键，`platform/hotkey.py`）。

### 首次运行 / 之后每次

* **第一次运行**：自动在 Steam 库里找正版 Rain World（注册表 → `libraryfolders.vdf` → 各盘
  `steamapps/common/Rain World`），找不到会弹窗让你指到游戏根目录（里面有 `RainWorld.exe` 和
  `RainWorld_Data` 文件夹），**也可以选「先用内置图元运行」**；找到后从你自己的游戏里切出基础图元，
  写进 `%LOCALAPPDATA%\IteratorPet\cache\sprites\`。
* **之后每次运行**：配置里的路径还在就直接启动；缓存缺了才重新切一次。**默认不带开发者面板**。
* 换机器 / 重装游戏 / 缓存坏了：`启动.exe --setup` 强制重走一遍探测。
* 重复双击：第二个实例会提示"已经在运行"然后退出（命名互斥体，内核自动释放，不会有假锁）。
* 排障日志：`%LOCALAPPDATA%\IteratorPet\launcher.log`（初始化过程）、
  `launcher-crash.log`（启动异常栈）。

### 调试用的环境变量

| 变量 | 作用 |
| --- | --- |
| `RAINWORLD_DIR` | 直接指定游戏目录，跳过探测（指错了就当没找到，不会偷偷回退） |
| `ITERATORPET_NONINTERACTIVE=1` | 一律不弹窗：找不到游戏就用内置图元继续，重复启动直接退（自动化测试用） |
| `ITERATORPET_NO_DETECT=1` | 强制"没有游戏"，用来测未初始化 / 无游戏本体的流程 |
| `ITERATORPET_DEBUG=1` | 启动时打印环境、路径、UnityPy 版本、探测结果，并把 `save()` 的失败栈打出来 |

托盘菜单可调鼠标穿透、开发者面板；配置持久化在 `%LOCALAPPDATA%\IteratorPet\config.json`
（移速菜单因为从来没生效过，2026-10-27 已删，见 docs/ROADMAP.md §1.7）。

## 从源码运行

```bash
python -m pip install -r requirements.txt   # PySide6 + UnityPy
$env:PYTHONPATH = "src"
python -m iterator_pet.main                 # 桌宠本体
python -m iterator_pet.launcher             # 发布版入口（先找游戏、再启动）
```

## 功能

1. fp现在会在你的屏幕上无所事事地漫游，因为这里没有三重肯定可以算。

2\. 鼠标悬停时，随时间增加触发fp看着鼠标的行为的概率。

3\. 鼠标移动时，fp有概率会追着鼠标跑。有点bug。

### 美术资源

精灵**不会**随仓库分发。启动时程序只读地从**你自己的** Rain World 安装里提取基础图元
（`Circle20` / `CentipedeSegment` / `pixel` 等）到 `%LOCALAPPDATA%\IteratorPet\cache\sprites\`。
找不到游戏时会退化为等价像素图元，仍可运行。安装路径可用环境变量覆盖：

```powershell
$env:RAINWORLD_DIR = "D:\SteamLibrary\steamapps\common\Rain World"
```

托盘缩略图 `src/iterator_pet/assets/tray.png` 是本仓库渲染管线画出来的人偶全身像
（`tools/puppet_portrait.py` 联络表左起第一个，128 px），不是从游戏里切出来的图。

## 开发

```bash
python -m pytest -q                      # 74 项测试
python tools/selftest.py                 # 无依赖烟雾测试
python tools/puppet_portrait.py          # 1:1 人偶特写（顺带刷新托盘缩略图素材）
python tools/roam_report.py 10           # 10 分钟漫游统计（行为占比 / 覆盖）
python tools/devpanel.py --cursor sweep  # 开发者面板：实时看行为、抽签权重、臂展、缆线拉伸
python -m iterator_pet.main --dev-panel  # 桌宠 + 面板同屏
python tools/cord_diag.py                # 缆线长度/拉伸数字
```

### 打包

```bash
python packaging/build.py      # PyInstaller onedir + 裁剪 -> dist/启动/启动.exe（约 145 MB）
python packaging/package.py    # 打成 dist/五块卵石-v0.1.0-win64.zip（约 56 MB）
```

`build.cmd` 只是转发给 `build.py`（.cmd 里写中文会被 cmd 按控制台代码页拆开执行，踩过两次）。
裁剪清单在 `build.py` 的 `QT_FILES/QT_DIRS/QT_PLUGIN_DIRS`：只留 Core/Gui/Widgets、`qwindows` 平台插件、
png/ico/jpeg 图片插件；删掉 Qt Quick/Qml/Pdf/Network/translations/opengl32sw。

|工具|用途|
|-|-|
|`tools/selftest.py`|不依赖 Qt 的冒烟测试|
|`tools/smoke_window.py`|真实窗口短跑并截图|
|`tools/crop.py`|放大截取局部，用于像素级判断|
|`tools/puppet_portrait.py`|人偶 1:1 特写|
|`tools/leg_visibility.py`|腿是否被袍子挡住的计算|
|`tools/asset_sheet.py`|抽取到的图元联络表|
|`tools/probe_assets.py`|只读探测本机游戏安装|
|`tools/replay.py`|定帧率确定性回放|
|`tools/roam_report.py`|漫游覆盖率 / 基座分布|
|`tools/cord_diag.py`, `tools/cord_fold.py`|缆线长度与折叠诊断|

## 已知BUG

* fp追随鼠标的行为有点问题，可能导致自己被拽离机械臂。暂时不打算修。

## 许可与致谢

代码以 MIT 许可发布（见 `LICENSE`）。Rain World 及其全部美术、音频、代码版权归 **Videocult / Akupara Games**
所有；本仓库不含任何游戏素材，仅在运行时从用户本机安装读取基础图元。

受https://github.com/lingxiaojun/slugcatpet/ 启发，参考了这个项目的桌宠行为。
