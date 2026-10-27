# Iterator Pet · 雨世界迭代器桌宠

一只迭代器桌宠，暂时是fp桌宠。

> 预览图由本仓库代码离线渲染：`python -m iterator\\\_pet.main --preview out.png --width 1280 --height 720 --scale 2.2`

## 运行

```bash
python -m pip install -r requirements.txt   # 或： pip install PySide6
$env:PYTHONPATH = "src"
python -m iterator\\\_pet.main
```

托盘菜单可调置顶、鼠标穿透、显示滑轨、开发者面板（移速菜单因为从来没生效过，2026-10-27 已删，见 docs/ROADMAP.md §1.7）；配置持久化在 `%LOCALAPPDATA%\IteratorPet\config.json`。

## 功能

1. fp现在会在你的屏幕上无所事事地漫游，因为这里没有三重肯定可以算。

2\. 鼠标悬停时，随时间增加触发fp看着鼠标的行为的概率。

3\. 鼠标移动时，fp有概率会追着鼠标跑。有点bug。

### 美术资源

精灵**不会**随仓库分发。启动时程序只读地从**你自己的** Rain World 安装里提取基础图元
（`Circle20` / `CentipedeSegment` / `pixel` 等）到 `%LOCALAPPDATA%\IteratorPet\cache\sprites\`。
找不到游戏时会退化为等价像素图元，仍可运行。安装路径可用环境变量覆盖：

```powershell
$env:RAINWORLD\\\_DIR = "D:\SteamLibrary\steamapps\common\Rain World"
```

## 开发

```bash
python -m pytest -q                      # 73 项测试
python tools/selftest.py                 # 无依赖烟雾测试
python tools/puppet\\\_portrait.py          # 1:1 人偶特写
python tools/roam\\\_report.py 10           # 10 分钟漫游统计（行为占比 / 覆盖）
python tools/devpanel.py --cursor sweep  # 开发者面板：实时看行为、抽签权重、臂展、缆线拉伸
python -m iterator\\\_pet.main --dev-panel  # 桌宠 + 面板同屏
python tools/cord\\\_diag.py                # 缆线长度/拉伸数字
```

|工具|用途|
|-|-|
|`tools/selftest.py`|不依赖 Qt 的冒烟测试|
|`tools/smoke\\\_window.py`|真实窗口短跑并截图|
|`tools/crop.py`|放大截取局部，用于像素级判断|
|`tools/puppet\\\_portrait.py`|人偶 1:1 特写|
|`tools/leg\\\_visibility.py`|腿是否被袍子挡住的计算|
|`tools/asset\\\_sheet.py`|抽取到的图元联络表|
|`tools/probe\\\_assets.py`|只读探测本机游戏安装|
|`tools/replay.py`|定帧率确定性回放|
|`tools/roam\\\_report.py`|漫游覆盖率 / 基座分布|
|`tools/cord\\\_diag.py`, `tools/cord\\\_fold.py`|缆线长度与折叠诊断|

## 已知BUG

* fp追随鼠标的行为有点问题，可能导致自己被拽离机械臂。暂时不打算修。

## 许可与致谢

代码以 MIT 许可发布（见 `LICENSE`）。Rain World 及其全部美术、音频、代码版权归 **Videocult / Akupara Games**
所有；本仓库不含任何游戏素材，仅在运行时从用户本机安装读取基础图元。

受https://github.com/lingxiaojun/slugcatpet/启发，参考了这个项目的桌宠行为。

