# 行动序列合成图生成器

把一段视频里运动物体在不同时刻的位置抠出来，叠加到同一张静态背景上，生成一张 action-sequence（轨迹）合成图。

## 使用方式（三选一）

### 1. Windows 桌面程序（推荐 Windows 用户）

| 文件/目录 | 说明 |
| --- | --- |
| `dist/ActionSequenceComposer.exe` | 单文件版，一个 exe 直接双击运行（拷贝/分享方便） |
| `dist/ActionSequenceComposer/` | 文件夹版，启动更快（整个文件夹一起拷贝），已实测可运行 |

两者都无需安装 Python 或任何依赖。

### 2. 浏览器版（跨平台，Mac / Linux / Windows 通用）

双击打开 **`action_sequence_web.html`** 即可，无需安装任何东西、无需联网。
以下三个文件必须放在**同一个文件夹**里：

- `action_sequence_web.html` —— 界面与逻辑
- `opencv.js` —— OpenCV 的 WebAssembly 版（约 9.8 MB）
- `compose_core.js` —— 背景中值估计等纯 JS 工具

> 浏览器版与桌面版**算法完全一致**：都用 OpenCV 的 MOG2 背景减除器
> + 形态学闭运算 + 轮廓提取 + 多边形近似，输出效果一致。
> 功能一致：固定间隔 / 手动逐帧选帧、实时预览、中英文切换、参数可调、
> 并可直接下载合成图 PNG。

### 3. macOS / Linux 桌面程序（自行在本机打包）

在对应系统上安装依赖后运行打包脚本（PyInstaller 不支持跨平台交叉编译，需在目标系统上打包）：

```bash
# macOS（生成 .app）
brew install python-tk
pip3 install numpy opencv-python pillow pyinstaller
bash build_mac.sh

# Linux（生成单个可执行文件）
sudo apt install python3-tk
pip3 install numpy opencv-python pillow pyinstaller
bash build_linux.sh
```

## 界面语言切换

桌面版：窗口右上角 **中文 / English** 单选按钮，即时切换。
浏览器版：右上角 **中文 / English** 按钮，即时切换。

## 主要功能

- 选择视频，设定作图时间区间（起始/结束秒数）。
- 两种取帧方式：固定间隔（间隔可按帧或秒）/ 手动逐帧选帧（可插入、删除、上移下移、自动填充）。
- 生成过程可视化：实时显示 当前帧 / 前景遮罩 / 合成结果。
- 算法参数全部可在界面调节（**桌面版与浏览器版参数一致**）：
  MOG2 history、MOG2 varThreshold、形态学核大小、轮廓面积阈值、
  多边形近似 ε、背景采样帧数、随机种子。
  浏览器版额外有一个「帧率 fps」字段（因为浏览器无法直接从视频容器读取帧率，
  仅用于把「秒」换算成帧；按帧逐帧选帧不受影响）。

## 修改后重新打包（桌面版）

1. 用任意编辑器修改 `action_sequence_gui.py`。
2. Windows：双击 `build_exe.bat`；macOS/Linux：运行 `build_mac.sh` / `build_linux.sh`。
3. 完成后 `dist/` 下即为新版本。

> 打包参数都在脚本里，注释里写了如何加图标（`--icon 图标.ico`）、
> 切换单文件/文件夹形式（`--onefile` / `--onedir`）。

## 开发调试

```bash
# 桌面版源码
python action_sequence_gui.py

# 浏览器版算法核心单元测试（Node）
node test_core.js

# 浏览器版端到端自动化测试（需本机 Chrome + Node≥22）
node e2e_browser_test.js
```

若桌面版打包后出错，会在 exe 同目录生成 `composer_error.log`，记录了错误详情。

---

# English

# Action Sequence Composer

Extracts a moving object's positions at different moments in a video and overlays them onto a single static background to produce an action-sequence (trajectory) composite image.

## How to Use (pick one)

### 1. Windows Desktop App (recommended for Windows users)

| File / Folder | Description |
| --- | --- |
| `dist/ActionSequenceComposer.exe` | Single-file version — just double-click to run (easy to copy/share) |
| `dist/ActionSequenceComposer/` | Folder version — starts faster (copy the whole folder), verified working |

Neither requires installing Python or any dependencies.

### 2. Browser Version (cross-platform: macOS / Linux / Windows)

Just double-click **`action_sequence_web.html`** — no installation and no internet required.
The following three files must stay in the **same folder**:

- `action_sequence_web.html` — UI and logic
- `opencv.js` — the WebAssembly build of OpenCV (~9.8 MB)
- `compose_core.js` — pure-JS utilities such as background median estimation

> The browser version uses the **exact same algorithm** as the desktop version:
> OpenCV's MOG2 background subtractor + morphological closing + contour extraction
> + polygon approximation, so the output quality is identical.
> Same features: fixed interval / manual frame-by-frame selection, live preview,
> Chinese/English switching, adjustable parameters, and direct PNG download.

### 3. macOS / Linux Desktop App (build it on that machine)

Install the dependencies on the target OS, then run the packaging script
(PyInstaller cannot cross-compile; you must build on the target OS):

```bash
# macOS (produces a .app)
brew install python-tk
pip3 install numpy opencv-python pillow pyinstaller
bash build_mac.sh

# Linux (produces a single executable)
sudo apt install python3-tk
pip3 install numpy opencv-python pillow pyinstaller
bash build_linux.sh
```

## UI Language Switching

Desktop app: the **中文 / English** radio buttons in the top-right corner switch instantly.
Browser version: the **中文 / English** buttons in the top-right corner switch instantly.

## Main Features

- Choose a video and set the time range (start/end in seconds).
- Two frame-selection modes: fixed interval (in frames or seconds) / manual
  frame-by-frame selection (add, insert, delete, move up/down, auto-fill).
- Visualized generation: live display of current frame / foreground mask / composite result.
- All algorithm parameters are adjustable in the UI (**identical between the
  desktop and browser versions**):
  MOG2 history, MOG2 varThreshold, morphology kernel size, contour area threshold,
  polygon-approximation ε, background sample count, and random seed.
  The browser version additionally has an "FPS" field (because a browser cannot read
  the frame rate directly from the video container; it is only used to convert
  seconds to frames — frame-by-frame selection is unaffected).

## Rebuilding After Changes (desktop app)

1. Edit `action_sequence_gui.py` with any editor.
2. Windows: double-click `build_exe.bat`; macOS/Linux: run `build_mac.sh` / `build_linux.sh`.
3. The new version is under `dist/`.

> Packaging options are in the scripts (with comments): add an icon via
> `--icon icon.ico`, and switch between single-file / folder form via
> `--onefile` / `--onedir`.

## Development & Debugging

```bash
# Desktop source
python action_sequence_gui.py

# Browser-version core algorithm unit tests (Node)
node test_core.js

# Browser-version end-to-end automated test (requires Chrome + Node >= 22)
node e2e_browser_test.js
```

If the packaged desktop app crashes, it writes `composer_error.log` next to the exe with the error details.
