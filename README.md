# Iterator Pet · 雨世界迭代器桌宠

一只迭代器桌宠，暂时是fp桌宠。

> 预览图由本仓库代码离线渲染：`python -m iterator_pet.main --preview out.png --width 1280 --height 720 --scale 2.2`

## 发布版

发布包解开后直接双击启动.exe。

觉得太大 / 太小：右键托盘图标 → 尺寸：便携版（40%）· 青春版（75%）· 满血版（100%，默认）· PRO（150%）
同一个菜单里还有「电光频率」（关 / 节能 / computing）。


### 首次运行 / 之后每次

* **第一次运行**：自动在 Steam 库里找正版 Rain World，找不到会弹窗让你指到游戏根目录（里面有 `RainWorld.exe` 和
  `RainWorld_Data` 文件夹）
* **之后每次运行**：配置里的路径还在就直接启动；缓存缺了才重新切一次。


## 从源码运行

```bash
python -m pip install -r requirements.txt   # PySide6 + UnityPy
$env:PYTHONPATH = "src"
python -m iterator_pet.main                 # 桌宠本体
python -m iterator_pet.launcher             # 发布版入口（先找游戏、再启动）
```

## 功能

1. fp现在会在你的屏幕上无所事事地漫游，因为这里没有三重肯定可以算。

2. 鼠标悬停时，随时间增加触发fp看着鼠标的行为的概率。

3. 鼠标移动时，fp有概率会追着鼠标跑。有点bug。

### 美术资源

精灵**不会**随仓库分发。启动时程序只读地从**你自己的** Rain World 安装里提取基础图元
（`Circle20` / `CentipedeSegment` / `pixel` 等）到 `%LOCALAPPDATA%\IteratorPet\cache\sprites\`。
找不到游戏时会退化为等价像素图元，仍可运行。安装路径可用环境变量覆盖：

```powershell
$env:RAINWORLD_DIR = "D:\SteamLibrary\steamapps\common\Rain World"
```

托盘缩略图 `src/iterator_pet/assets/tray.png` 是本仓库渲染管线画出来的人偶全身像
（`tools/puppet_portrait.py` 联络表左起第一个，128 px），不是从游戏里切出来的图。

## 已知BUG

* fp追随鼠标的行为有点问题，可能导致自己被拽离机械臂。暂时不打算修。
* 电光看起来不太对。
* 改了尺寸之后电光需要重新设置。

## 待办事项

* 点击和拖拽反应
* 物品和对话系统
* 其他迭代器，或自定义


## 许可与致谢

代码以 MIT 许可发布（见 `LICENSE`）。Rain World 及其全部美术、音频、代码版权归 **Videocult / Akupara Games**
所有；本仓库不含任何游戏素材，仅在运行时从用户本机安装读取基础图元。

受https://github.com/lingxiaojun/slugcatpet/ 启发，参考了这个项目的桌宠行为。
我不懂python，所以如果你觉得代码很烂，请责怪DSH吧。
