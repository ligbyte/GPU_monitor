# GPU_monitor

```python
pip install -r requirements-desktop.txt
python run.py             # 启动托盘监控
python run.py --install   # 安装开机自启动
python run.py --uninstall # 卸载自启动

```


PyInstaller 的核心逻辑只有一句：**在目标系统上，把 Python 解释器和所有依赖"冻"进一个包里，用户不用装 Python 也能跑**。下面按"跑通 → 常用参数 → 踩坑"的顺序给你。

---

## 一、三步跑通

### 1. 装（强烈建议用干净虚拟环境）

```bash
pip install pyinstaller
pyinstaller --version     # 当前为 6.x，支持 Python 3.8+
```

虚拟环境不是为了好看——PyInstaller 会把环境里**所有**能import到的东西都扫一遍，全局环境装得越杂，包越大、越容易误带依赖。

### 2. 打

假设入口文件是 `app.py`：

```bash
pyinstaller app.py                 # 默认 onedir：dist/app/ 一整个文件夹
pyinstaller --onefile app.py       # 单文件：dist/app（Windows 下 app.exe）
```

### 3. 发

产物在 `dist/` 里。`build/` 是中间产物、`app.spec` 是这次打包的配置存档，**要发给别人的是 `dist/` 下的东西**。

---

## 二、常用参数速查

| 参数 | 简写 | 作用 |
|---|---|---|
| `--onefile` | `-F` | 打成单个 exe，交付干净，但每次启动要解压到临时目录，**启动慢一截** |
| `--onedir` | `-D` | 默认。文件夹模式，启动快、依赖摊开好排查 |
| `--windowed` / `--noconsole` | `-w` | 隐藏控制台窗口，**GUI 程序用**；命令行工具千万别加，否则报错都看不见 |
| `--name` | `-n` | 指定产物名 |
| `--icon` | `-i` | 图标：Windows 用 `.ico`，macOS 用 `.icns` |
| `--add-data` | | 带非 py 文件（配置、模板、图片），`源:目标`；**Windows 用分号 `;`，macOS/Linux 用冒号 `:`** |
| `--hidden-import` | | 手动补静态分析扫不到的动态导入 |
| `--exclude-module` | | 剔掉不要的模块（瘦身利器） |
| `--clean` / `--noconfirm` | `-y` | 清缓存 / 不询问直接覆盖 |



### 一个贴近真实的完整命令

```bash
pyinstaller --onefile --windowed \
  --name 我的工具 \
  --icon icon.ico \
  --add-data "config.yaml:." \
  --add-data "templates:templates" \
  --hidden-import=requests \
  --exclude-module=matplotlib \
  --clean --noconfirm \
  app.py
```

> 第一次跑完会生成 `app.spec`。**之后改配置直接编辑 spec，然后 `pyinstaller app.spec` 重跑**，比每次敲一长串命令靠谱得多（spec 本质就是个可执行的 Python 脚本）。

---

## 三、最容易翻车的三件事

### 1. 打包后找不到数据文件 ❌

**这是头号坑。** 因为 onefile 运行时会把东西解压到一个临时目录（`_MEIxxxxx`），`open('config.yaml')` 这种相对路径必然失效。官方推荐写法：

```python
import os, sys

def resource_path(relative):
    if hasattr(sys, '_MEIPASS'):        # 打包后的临时解压目录
        base = sys._MEIPASS
    else:
        base = os.path.abspath(".")
    return os.path.join(base, relative)

cfg = open(resource_path("config.yaml"), encoding="utf-8").read()
```

PyInstaller 6.x 也提供了 `pyinstaller.utils.hooks` 里的辅助函数，但上面这段自己写最稳、无额外依赖。

### 2. ModuleNotFoundError：明明 pip 装了却说没有

PyInstaller 靠**静态分析** import 语句，`importlib.import_module()`、插件式加载、部分 C 扩展（numpy/pandas/ PyQt 的子模块）它看不见。解法两条路：

```bash
pyinstaller --hidden-import=pandas --hidden-import=myplugin.app app.py
```

或者写 **hook 文件**（`hook-myplugin.py`，放 `hooks/` 目录，spec 里 `hookspath=['hooks']`），适合复杂包。常用库 PyInstaller 已内置 hook，numpy/matplotlib/PyQt 基本开箱即用。

### 3. 不能交叉编译 ⚠️

**在 Windows 上打不出 macOS 的 .app，在 Mac 上也打不出 .exe。** 要哪个平台的包，就得在哪台机器上跑一次 PyInstaller。多平台发布的常规解法：

- 三台机器 / 虚拟机
- **GitHub Actions matrix 构建**（最省事，`windows-latest` / `macos-latest` / `ubuntu-latest` 各跑一次，artifacts 收产物）
- Linux 目标可用 Docker

---

## 四、按场景选模式

| 场景 | 推荐 | 原因 |
|---|---|---|
| GUI 桌面应用、正式对外发布 | **onedir** | 启动快，崩了好定位；macOS 尤其别用 onefile（官方明确不推荐 onefile + .app 组合，签名/公证会出问题） |
| 内部小工具、命令行脚本 | **onefile** | 发一个文件过去体验最好 |
| 用了 PyQt / Tkinter | onedir + `--windowed` + 剔 Qt 翻译和无关插件 | Qt 全家桶动辄 120MB，排除后能压到 40–60MB |
| 用了 `multiprocessing` | 入口第一行加 `multiprocessing.freeze_support()` | 否则 Windows 下会**无限孵化子进程**把 CPU 打满 |

**心法：先用 onedir 跑通调好，确认一切正常再切 onefile。** 文件夹模式下依赖摊在明面上，出问题时能一眼对照。

---

## 五、平台专属注意

- **Windows**：产物 `dist/app.exe`。PyInstaller 会自动带上 MSVC 运行库，目标机一般不用装任何东西。杀软误报是常态，发布前自己传一次 VirusTotal 心里有数；正式分发建议套一层 Inno Setup / NSIS 做安装程序。
- **macOS**：加 `--windowed` 才生成 `.app` bundle，配 `--osx-bundle-identifier=com.xxx.yyy`。**没签名+公证，用户双击会被 Gatekeeper 拦**，得 `codesign` + `xcrun notarytool` 走一遍（需要 Apple 开发者账号）。
- **Linux**：6.0 起构建内部用了符号链接，`zip` 默认不保留符号链接，**打包分发请用 `tar.gz`**；另外 onefile 和挂载为 `noexec` 的 `/tmp` 不兼容，可用 `--runtime-tmpdir` 改解压位置。还有个隐藏雷：**PyInstaller 不打包 glibc**，所以要在你打算支持的**最老**那版 Linux 上构建。

---

## 六、发布前的自检清单

```bash
rm -rf build dist            # 1. 干净构建
pyinstaller --onedir --windowed --clean --noconfirm app.py
```

1. 找一台**没装 Python 的干净机器 / 全新虚拟机**跑一遍——"我这儿能跑"在打包这件事上等于零
2. 把所有涉及文件读写的路径过一遍（`resource_path` 是否都用上了）
3. 看体积：超过 60MB 就查查是不是误带了 matplotlib / pandas / scipy
4. 加 `--debug all` 或先别加 `-w`，让控制台把 traceback 吐出来再定位

---

## 七、如果 PyInstaller 搞不定

| 工具 | 特点 |
|---|---|
| **Nuitka** | 把 Python 编译成 C 再编二进制，**真编译**、启动快、反编译难、体积更小，代价是构建慢、偶尔要调 C 编译器 |
| **cx_Freeze** | 老牌，跨平台，配置偏手写 setup.py |
| **PyOxidizer** | Rust 写的，极致单文件，但生态和踩坑成本更高 |

需要我针对你的具体场景给一份可直接用的命令吗？告诉我：**GUI 还是命令行（Tkinter/PyQt/FastAPI？）、目标系统、有没有带数据文件或模型权重**，我给你把 spec 文件和 `resource_path` 那套一起写出来。
