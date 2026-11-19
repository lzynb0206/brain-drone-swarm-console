# 脑控无人机蜂群上位机 · Brain–Drone Swarm Console

[简体中文](#zh-cn) · [English](#english)

> 中英文文档放在同一份 README 中，点击上方链接切换阅读。软件界面保留现有中文。
> Both language versions are in this README. Use the links above to switch sections; the desktop UI remains in Chinese.

<a id="zh-cn"></a>

## 简体中文

### 项目介绍

基于 **Python 3.12 + PySide6** 的桌面上位机，将脑电采集、SSVEP / MI 实验、无人机控制和摄像头手势识别整合在一个工作台中。项目面向本地设备联调、脑机接口实验和科研数据分析；认证服务通过 HTTP 接入，界面与设备、算法逻辑分层组织。

当前保留 **士兵（作战操作员）、指挥官、科研人员** 三种身份。系统工程师、系统管理员相关页面及其专用接口已移除。

| 工作空间 | 主要功能 | 界面设计 |
| --- | --- | --- |
| 士兵 / 指挥官 | 作战地图、脑控驾驶、视觉手势、状态监控、手动控制 | 黑白灰主题；左侧导航支持靠近展开、离开收回、手动折叠和固定展开 |
| 科研人员 | 硬件采集、LSL 收集、CSV 回放、范式算法、数据采集与分析、单目标校准 | 黑白灰主题；保留顶部导航和原有分页布局，脑电曲线按通道保留区分色 |

主要能力：

- **脑电接入**：ExpandableEEG / Cyton 兼容设备原生串口采集、LSL 流接收和广播、CSV 记录与回放。
- **实验工作流**：SSVEP、MI、SSVEP＋MI 混合采集，正式任务、试跑和静息方案，受试者确认、事件标记与试次归档。
- **数据分析**：按受试者 → 日期 → 动作 → 试次筛选，截取分析时间段，查看波形、频谱、质量和解码结果；支持外部 BDF 与同轮实验日志对齐。
- **算法工作台**：FBCCA、BN-FBCCA、个人模板、个人校准融合及 Transformer 等算法入口；训练、评估和复现实验脚本独立于界面运行。
- **无人机控制**：Crazyflie / Crazyradio 接入，Flow / Lighthouse 相关控制、编队、手动摇杆、受控降落和急停。正常启动不会自动启用飞控。
- **视觉手势**：本机摄像头、MJPEG 网络摄像头和视频文件输入；实时画面、16 时间片事件图、11 类 SNN 结果及推理耗时。

### 安装与启动

**运行环境**：Python 3.12、桌面图形环境。基础界面使用 `requirements.txt`；摄像头和机器学习功能按需安装额外依赖。真实设备需要相应的系统权限、驱动、USB 或蓝牙连接。

先克隆仓库并进入项目目录：

```bash
git clone https://github.com/lzynb0206/brain-drone-swarm-console.git
cd brain-drone-swarm-console
```

**macOS / Linux：**

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python main.py
```

**Windows PowerShell：**

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe main.py
```

已有 `.venv312` 时可直接安装依赖并启动，无需重建。始终从项目根目录运行命令。

| 可选功能 | 安装方式（macOS / Linux） |
| --- | --- |
| 摄像头与 SNN 手势识别 | `.venv312/bin/python -m pip install -r requirements-vision.txt` |
| 模型训练、离线评估及完整测试依赖 | `.venv312/bin/python -m pip install -r requirements-ml.txt` |

Windows 将表中的 Python 路径替换为 `.\.venv312\Scripts\python.exe`。若要运行包含视觉功能的完整测试，两份可选依赖都需要安装。

### 登录与环境配置

- **本地直接入口**：输入 `soldier` 或 `research`，可分别进入士兵或科研工作空间，无需密码。这是当前保留的本地调试入口。
- **后端认证**：其他账号默认连接 `http://127.0.0.1:8080` 的 Spring Boot / JWT 服务；后端服务独立部署，不包含在本仓库中。
- **离线演示认证**：设置 `BDS_AUTH_BACKEND=mock` 后，`research`、`commander` 等名称路由到相应角色；普通演示账号需填写非空密码，但不会校验真实凭据。

程序直接读取系统环境变量，**不会自动加载 `.env`**。[`.env.example`](.env.example) 是配置参考，包含可选模型和设备参数。下面列的是代码中未设置环境变量时的默认值。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `BDS_AUTH_BACKEND` | `http` | `http` 为后端认证，`mock` 为离线演示 |
| `AUTH_API_URL` | `http://127.0.0.1:8080` | 认证服务地址 |
| `BDS_EEG_BACKEND` | `lsl` | EEG 初始数据源；`csv` 为显式回放模式 |
| `BDS_LSL_STREAM_NAME` / `BDS_LSL_STREAM_TYPE` | `obci_eeg1` / `EEG` | 目标 LSL 流 |
| `BDS_EEG_SAMPLE_RATE` | `250` | 配置采样率，单位 Hz |
| `BDS_EEG_WINDOW_SECONDS` | `4` | 基线解码窗口，单位秒；个人算法可能使用自己的窗口 |
| `BDS_EEG_DATA_ROOT` | 项目下 `data/` | 采集与实验数据根目录 |
| `BDS_EEG_CSV_PATH` | `data/research_experiments/samples/start-1.csv` | CSV 模式输入，文件需自行准备 |
| `BDS_INTENT_DECODER` | `fbcca` | 启动时选择的解码器 |
| `BDS_SOLDIER_EEG_PORT` | 空 | 士兵一键链路指定串口；为空时尝试识别唯一设备 |
| `BDS_SWARM_BACKEND` | `disabled` | `flowdeck` 启用真实 Crazyflie 接口 |
| `BDS_CRAZYFLIE_URIS` | 空 | 一架或多架设备的无线 URI |
| `BDS_HARDWARE_CONTROL_ENABLED` | `false` | 是否允许真实飞控；仅连接接口不会自动开启控制 |

例如，只切换为离线演示认证：

```bash
BDS_AUTH_BACKEND=mock .venv312/bin/python main.py
```

Windows PowerShell：

```powershell
$env:BDS_AUTH_BACKEND = "mock"
.\.venv312\Scripts\python.exe main.py
```

### 常用工作流程

**1. 脑电采集与回放**

1. 科研人员顶部“采集”页选择串口设备，建立采集会话。
2. 在采集总览检查通道波形、频谱和数据状态，使用信号质量、丢包检测及硬件设置子页查看细节。
3. 需要跨进程或跨程序使用信号时启动 LSL 输出，再在“LSL收集”发现流、开始接收并保存 CSV。
4. 已有 CSV 在“CSV回放”导入，可暂停、调整播放速度或定位时间；回放用于复盘和算法验证。

**2. 科研实验与数据分析**

1. 在“范式算法”选择实验范式与解码算法。
2. 在“数据采集”确认受试者，选择正式任务、试跑或静息方案，并核对采集计划。
3. 使用本机实时 EEG 时，核对电极布局并完成采集前检查；使用外部设备时，选择“独立呈现 / 外部 BDF”。
4. 外部记录需要同时保存 BDF 和本机同轮事件日志，在“数据分析”导入时填写时间偏移、漂移与对齐依据。
5. 按受试者、日期、动作和具体试次筛选，选择分析片段并查看结果。单目标模板采集另有“单目标校准”入口。

人工时间对齐会被标记为未验证，不能等同于硬件同步；仅内部交叉验证的成绩不能直接作为实机准确率。

**3. 士兵脑控驾驶**

在左侧“脑控驾驶”配置 URI、连接设备，再启动 SSVEP 实时链路。典型链路为：

```text
ExpandableEEG → LSL → EEGWorker → 解码器 → 飞控准入检查 → Crazyflie
```

CSV、模拟信号和未经独立验证的模型不能直接进入实机脑控执行链路。界面提供手动控制、停止、降落和急停入口；无真实连接时显示未连接状态，演示指标会明确标注。

**4. 摄像头与手势识别**

1. 安装 `requirements-vision.txt`，打开左侧“视觉手势”。
2. 选择本机摄像头编号（通常为 `0`）、MJPEG 地址（如 `http://设备IP/stream`）或本地视频文件。
3. 确认权重路径 `models/dvs128_snn_best_v4.pth`，点击“连接并识别”。
4. 查看视频、事件图、手势类别和耗时；停止后可调整帧差阈值及旋转角度。

采集与推理运行在独立进程，退出登录或关闭软件会停止该进程。当前手势仅提供前进 / 后退等界面提示，不自动派发飞控命令。SNN 原始输出不是校准概率，DVS 数据集表现不代表普通摄像头准确率；触觉反馈设备尚未接入。

### 目录与数据组织

| 路径 | 职责 |
| --- | --- |
| `main.py`、`src/main.py`、`src/app.py` | 程序入口、Qt 初始化和窗口组装 |
| `src/views/`、`src/widgets/`、`src/resources/` | 页面、复用控件、绘制与样式 |
| `src/controllers/` | 页面动作、采集、手势及分析流程协调 |
| `src/services/`、`src/domain/`、`src/ports/` | 业务规则、数据模型与接口契约 |
| `src/adapters/`、`src/integrations/` | 认证、EEG、无人机与摄像头外部接入 |
| `src/bci/`、`src/ml/`、`src/workers/` | 解码、模型训练推理及后台任务 |
| `models/` | 随仓库提供的手势权重、个人模型与元数据 |
| `scripts/` | 设备桥接、训练、评估、公开数据准备及复现实验工具 |
| `tests/` | 单元、集成和离屏界面测试 |
| `data/` | 本地科研数据、采集记录与试次索引 |
| `outputs/` | 本地科研结果与历史实验材料 |

运行记录按用途保存到 `data/research_experiments/`、`data/ssvep_realtime_tests/` 和 `data/soldier_ssvep_flights/`。可用 `BDS_EEG_DATA_ROOT` 将这些目录放到其他磁盘。

Git 提交包含源码、测试、配置示例和未被忽略的模型文件。科研数据集、运行记录、`outputs/`、虚拟环境、缓存及本地 `.env` 按 [`.gitignore`](.gitignore) 保留在本机，不随克隆下载。数据目录会在实际保存时创建；界面不会为缺失数据自动生成实验记录。可选 Transformer / FB-EEGNet 权重需要另行训练或准备。

### 测试与开发

macOS / Linux：

```bash
QT_QPA_PLATFORM=offscreen .venv312/bin/python -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv312\Scripts\python.exe -m unittest discover -s tests -v
```

离屏模式用于测试；正常打开桌面界面前，请取消此前设置的 `QT_QPA_PLATFORM=offscreen`。

测试覆盖角色路由、采集 / 回放、标记与试次记录、分析算法、飞控准入、折叠导航和手势进程。自动化测试不代替真实摄像头、EEG 和无人机联调。新增设备接入放在适配层，界面绘制只读取已有状态，耗时采集与推理放到后台任务中。

### 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 其他账号登录失败 | 检查认证服务和 `AUTH_API_URL`；仅查看界面可使用本地入口或显式 `mock` 认证 |
| LSL 找不到数据 | 先启动发送端，核对流名称、类型、网络和发送状态 |
| 摄像头打不开 | 检查系统权限、设备编号或 URL；仅允许单客户端的机载相机需关闭其他占用程序 |
| 提示缺少 `cv2` / `torch` | 在运行软件的同一虚拟环境安装 `requirements-vision.txt` |
| 模型或 CSV 不存在 | 检查配置路径；忽略的科研数据和可选训练权重不会随仓库下载 |
| 无人机已连接但不能控制 | 检查硬件控制开关、解锁状态和界面给出的准入原因 |
| `.env` 修改后没有生效 | 程序不自动加载该文件；先设置系统环境变量，再重新启动 |

[English →](#english) · [来源与第三方声明](#third-party)

---

<a id="english"></a>

## English

[切换到简体中文](#zh-cn)

### Overview

A **Python 3.12 + PySide6** desktop console for EEG acquisition, SSVEP / motor imagery (MI) experiments, drone control and camera gesture recognition. It brings device integration, experimental workflows and research analysis into one workspace. Authentication is provided by an external HTTP service; UI, business logic, algorithms and device adapters are separate layers.

The supported roles are **Soldier / Operator, Commander and Researcher**. System Engineer and System Administrator pages and their dedicated interfaces have been removed.

| Workspace | Main capabilities | Navigation and appearance |
| --- | --- | --- |
| Soldier / Commander | Map, BCI driving, visual gestures, status monitoring and manual controls | Monochrome theme; left sidebar expands on approach, collapses on leave and supports manual toggling or pinning |
| Researcher | Hardware acquisition, LSL collection, CSV replay, paradigms, algorithms, experiments, analysis and single-target calibration | Monochrome theme with the existing top navigation and page layout; EEG traces retain channel colors |

Core features include native serial acquisition from ExpandableEEG / Cyton-compatible devices; LSL receiving and broadcasting; CSV recording and replay; labeled SSVEP, MI and hybrid trials; subject-based data organization; external BDF import; and waveform, spectrum, quality and decoder analysis.

The decoder workspace exposes FBCCA, BN-FBCCA, personal templates, personal score calibration and Transformer options. Crazyflie / Crazyradio integration includes Flow / Lighthouse-related controls, formations, manual input, landing and emergency stop. Camera input supports local devices, MJPEG streams and video files, with live previews, 16-step event images and 11-class SNN inference.

### Installation and launch

Use **Python 3.12** and a desktop graphics environment. Real devices also require their platform permissions, drivers and USB or Bluetooth connections. Run commands from the repository root.

```bash
git clone https://github.com/lzynb0206/brain-drone-swarm-console.git
cd brain-drone-swarm-console
```

**macOS / Linux:**

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python main.py
```

**Windows PowerShell:**

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements.txt
.\.venv312\Scripts\python.exe main.py
```

Reuse an existing virtual environment when available. Optional dependencies:

```bash
# Camera capture and SNN gesture recognition
.venv312/bin/python -m pip install -r requirements-vision.txt
# Training, offline evaluation and full test dependencies
.venv312/bin/python -m pip install -r requirements-ml.txt
```

On Windows, replace the Python path with `.\.venv312\Scripts\python.exe`. Install both optional dependency sets to exercise the complete suite including vision tests.

### Login and configuration

- **Local entry points:** `soldier` and `research` open the corresponding workspace without a password. These are intentional local debugging entries.
- **Server authentication:** other accounts use the external Spring Boot / JWT service, defaulting to `http://127.0.0.1:8080`. The server is deployed separately and is not part of this repository.
- **Offline authentication:** `BDS_AUTH_BACKEND=mock` routes names such as `research` or `commander` to their roles. Ordinary mock accounts require a non-empty password but do not validate real credentials.

The application reads process environment variables and **does not automatically load `.env`**. See [`.env.example`](.env.example) for configuration examples and optional model/device settings. The table below describes defaults in the code when no environment override is supplied.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BDS_AUTH_BACKEND` | `http` | Remote authentication or `mock` for offline use |
| `AUTH_API_URL` | `http://127.0.0.1:8080` | Authentication endpoint |
| `BDS_EEG_BACKEND` | `lsl` | Initial EEG source; `csv` explicitly selects replay |
| `BDS_LSL_STREAM_NAME` / `BDS_LSL_STREAM_TYPE` | `obci_eeg1` / `EEG` | Target LSL stream |
| `BDS_EEG_SAMPLE_RATE` | `250` | Configured sample rate in Hz |
| `BDS_EEG_WINDOW_SECONDS` | `4` | Baseline decoding window in seconds; personal algorithms may use their own window |
| `BDS_EEG_DATA_ROOT` | Project `data/` directory | Recording and experiment root |
| `BDS_EEG_CSV_PATH` | `data/research_experiments/samples/start-1.csv` | CSV input; supply the file separately |
| `BDS_INTENT_DECODER` | `fbcca` | Initial decoder |
| `BDS_SOLDIER_EEG_PORT` | Empty | Explicit serial port for the soldier workflow; otherwise detect a unique device |
| `BDS_SWARM_BACKEND` | `disabled` | Use `flowdeck` for the real Crazyflie adapter |
| `BDS_CRAZYFLIE_URIS` | Empty | Radio URIs for one or more drones |
| `BDS_HARDWARE_CONTROL_ENABLED` | `false` | Explicit permission for hardware commands; connecting does not enable control automatically |

Example for offline authentication:

```bash
BDS_AUTH_BACKEND=mock .venv312/bin/python main.py
```

In PowerShell, set `$env:BDS_AUTH_BACKEND = "mock"`, then launch the Windows command above.

### Typical workflows

**EEG acquisition and replay**

1. Open **采集 / Acquisition**, select the serial device and establish a session.
2. Inspect waveforms, spectra, quality, packet loss and hardware settings.
3. Start LSL broadcasting when needed. Use **LSL收集 / LSL Collection** to discover the stream, receive samples and save CSV recordings.
4. Import an existing CSV in **CSV回放 / CSV Replay** to pause, change playback speed or seek. Replay is intended for review and algorithm evaluation.

**Experiments and analysis**

1. Select the paradigm and decoder in **范式算法 / Paradigms and Algorithms**.
2. In **数据采集 / Data Collection**, confirm the subject, choose a formal, pilot or resting protocol, and review the plan.
3. For local EEG, check electrode mapping and complete the preflight acquisition check. For another recording system, select independent presentation / external BDF.
4. Keep both the BDF and the matching event log. During import in **数据分析 / Data Analysis**, provide the time offset, drift and alignment evidence.
5. Filter by subject, date, action and trial, select an analysis interval and inspect results. **单目标校准 / Single-target Calibration** provides a separate template collection workflow.

Manual alignment is marked as unverified, not hardware-synchronized. Internal cross-validation results are not equivalent to independently validated live-control accuracy.

**BCI driving**

Configure radio URIs and connect the hardware from the soldier sidebar's BCI driving page, then start the SSVEP workflow:

```text
ExpandableEEG → LSL → EEGWorker → Decoder → Control eligibility checks → Crazyflie
```

CSV replay, simulated signals and models without independent validation cannot directly drive the live BCI control path. Manual controls, stop, landing and emergency stop are available. Missing hardware is shown as disconnected; demonstration metrics are explicitly labeled.

**Camera gestures**

Install the vision dependencies and open **视觉手势 / Visual Gestures**. Select a local camera index (usually `0`), a complete MJPEG URL such as `http://DEVICE_IP/stream`, or a video file. Use `models/dvs128_snn_best_v4.pth` and click connect. The page displays video, event images, gesture classes and inference time; stop capture before changing the difference threshold or rotation.

Capture and inference run in a separate process, stopped on logout or window close. Gestures currently provide UI movement hints only and do not dispatch flight commands. Raw SNN output is not a calibrated probability, and DVS benchmark performance does not establish ordinary-camera accuracy. Haptic feedback integration is not included.

### Source layout and local data

| Path | Responsibility |
| --- | --- |
| `main.py`, `src/main.py`, `src/app.py` | Entry points, Qt initialization and window composition |
| `src/views/`, `src/widgets/`, `src/resources/` | Pages, reusable widgets, drawing and styles |
| `src/controllers/` | User actions and acquisition, gesture and analysis workflows |
| `src/services/`, `src/domain/`, `src/ports/` | Business rules, data models and interfaces |
| `src/adapters/`, `src/integrations/` | Authentication, EEG, drone and camera integrations |
| `src/bci/`, `src/ml/`, `src/workers/` | Decoders, training/inference and background tasks |
| `models/` | Included gesture weights, personal models and metadata |
| `scripts/` | Device bridges, training, evaluation, public-data preparation and reproduction tools |
| `tests/` | Unit, integration and offscreen UI tests |
| `data/`, `outputs/` | Local datasets, recordings, indexes and research results |

Runtime recordings are separated into `data/research_experiments/`, `data/ssvep_realtime_tests/` and `data/soldier_ssvep_flights/`. Set `BDS_EEG_DATA_ROOT` to relocate them.

Git includes source, tests, configuration examples and non-ignored model files. Research datasets, runtime recordings, `outputs/`, virtual environments, caches and local `.env` files remain on the local machine according to [`.gitignore`](.gitignore); cloning does not download them. Recording directories are created when used, and missing experimental data is not silently generated. Optional Transformer / FB-EEGNet weights must be trained or supplied separately.

### Testing and development

```bash
QT_QPA_PLATFORM=offscreen .venv312/bin/python -m unittest discover -s tests -v
```

Windows PowerShell:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv312\Scripts\python.exe -m unittest discover -s tests -v
```

Unset `QT_QPA_PLATFORM=offscreen` before launching the normal desktop UI. Tests cover role routing, acquisition/replay, markers and trials, algorithms, control eligibility, sidebar behavior and gesture subprocesses. They do not replace physical camera, EEG and drone validation. Keep new device integrations in adapters, keep drawing dependent on cached state, and run expensive acquisition/inference outside the GUI thread.

### Troubleshooting

| Symptom | Check |
| --- | --- |
| Server login fails | Verify the authentication service and URL; use a local entry or explicit mock authentication for UI inspection |
| No LSL stream | Start the sender and check stream name, type, network and broadcasting state |
| Camera cannot open | Check permissions, index or URL; close other clients if the camera accepts only one connection |
| Missing `cv2` / `torch` | Install `requirements-vision.txt` in the same environment used to launch the app |
| Missing CSV or model | Check the path; ignored datasets and optional trained weights are not bundled |
| Connected drone rejects commands | Check hardware-control configuration, arm state and the eligibility reason shown in the UI |
| Editing `.env` has no effect | Set process environment variables explicitly and restart |

[返回中文 / Back to Chinese](#zh-cn) · [Third-party notices](#third-party)

---

<a id="third-party"></a>

## 来源与第三方声明 · Third-party notices

### 手势识别 · Gesture recognition

手势网络与帧差逻辑来自用户提供的 `无人机手势识别代码/demo_drone_cam.py`。权重 `dvs128_snn_best_v4.pth` 原样复制，原代码包未附独立许可证；本项目不为其声明额外授权。

The gesture network and frame-difference logic originate from the user-provided `demo_drone_cam.py` in the original gesture-recognition package. The checkpoint was copied unchanged. That package did not include a separate license; this project does not grant additional rights to it.

SHA-256 (`models/dvs128_snn_best_v4.pth`):

```text
fbddf481c9baadd2933f157017037542130098ea86c69097a9b11f361567c89e
```

### Graph Contrastive Denoising (GCD)

`src/ml/gcd_preprocessing.py` is adapted from the public `utils.py` in [Graph Contrastive Denoising Module](https://github.com/Quentin2022/Graph-Contrastive-Denoising-Module), licensed under Apache License 2.0.

The local adaptation keeps the upstream numerical preprocessing, graph handling, label mapping and ten-fold split while adding project documentation, path typing and a command-line entry point. The Apache License 2.0 text is available in the upstream repository.

### OpenBCI GUI

The researcher acquisition control-panel layout and interaction states are adapted from [OpenBCI GUI](https://github.com/OpenBCI/OpenBCI_GUI), particularly `OpenBCI_GUI/ControlPanel.pde`, licensed under MIT.

MIT License

Copyright (c) 2018 OpenBCI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

[简体中文](#zh-cn) · [English](#english)
