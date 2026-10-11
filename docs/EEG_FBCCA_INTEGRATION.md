# OpenBCI、LSL、EEG、运动想象与 FBCCA 接入说明

## 当前数据链

```text
ExpandableEEG蓝牙虚拟COM/串口 → BrainFlow Cyton驱动（直接采集，推荐）
或 OpenBCI GUI → LSL EEG（兼容模式）
或 start-1.csv / 已保存的 lsl_raw_*.csv
        ↓ EEGStream
后台 QThread（连续分块录制、刺激同步试次、4 秒滑动窗）
        ↓
质量检测 → 纯 NumPy FBCCA / 可选 FB-EEGNet → 意图安全阈值
        ↓
科研界面 + lsl_raw_*.csv + recognition_log.csv + raw_trials/*.npz
        ↓ 仅在实时硬件/LSL、安全条件满足且人工开启时
SwarmGateway 二次确认 → 串行硬件命令
```

解码结果默认不会调用 `SwarmGateway`。只有实时硬件或 LSL、实机已连接并解锁、
操作员显式开启“脑控执行”、同一意图连续确认两次且冷却通过时，才提交命令。
CSV 回放被代码硬性禁止驱动实机。没有模型权重时，系统继续运行真实 FBCCA，不生成假的深度模型结果。

这里的职责必须分清：OpenBCI 是采集硬件及其配套软件，EEG 是采集到的脑电数据，
LSL 是把 EEG 和时间戳实时送进本软件的传输通道，FBCCA 是只针对 SSVEP 频率的解码算法。
运动想象（MI）模式目前只采集、质检和保存带标签原始数据，不运行 FBCCA，也不生成无人机指令。

## 科研人员界面的“一体化采集器”

说明书表明 ExpandableEEG 的蓝牙模式会先由操作系统映射为 COM/串口，然后按
`Cyton → Serial → 对应COM口` 读取；它不是 Ganglion 的原生 BLE 协议。软件因此使用
BrainFlow `CYTON_BOARD` 驱动直接读取该串口，固定为8个 EEG 通道和250 Hz，不需要再启动
OpenBCI GUI，也不需要先转成 LSL。

进入“科研人员 → 采集器”后的推荐流程：

1. 先在 Windows/macOS 蓝牙设置中配对 ExpandableEEG，确认系统生成 COM 或 `/dev/cu.*` 串口。
2. 选择“直接连接设备”，点击“刷新/选择”，选择对应串口。
3. 点击“开始采集”。程序在后台建立 BrainFlow 会话并自动创建 CSV。
4. 页面实时显示8通道最近5秒原始波形、样本数、时长、实际采样率、时间戳缺口和运行日志。
5. “暂停波形”只停止界面重绘，后台采集和 CSV 保存不会暂停。
6. 点击“停止采集”后刷新并关闭 CSV，同时安全释放硬件会话。

同一页面仍保留“接收 LSL 流”，用于接收其他 EEG 软件或实验设备发布的 `type=EEG`
数据；它是兼容输入，不再是 ExpandableEEG 的必经链路。两种实时输入都会进入同一个
`EEGWorker`、信号质量、实验打标、算法和保存流程。

## OpenBCI兼容控制面板

进入“科研人员 → 采集”后先看到从 OpenBCI GUI v6 `ControlPanel.pde` 移植的
`System Control Panel`。页面按官方状态机提供 `CYTON / GANGLION / PLAYBACK /
SYNTHETIC / STREAMING`，并根据数据源切换 Serial、WiFi、Bluetooth、通道数、采样率、
Session Data、BrainFlow Streamer、回放文件和 Streaming Board Config。启动页不再混入 LSL 参数。

选择实时硬件入口并点击 `Start Session` 后进入运行界面；在那里通过 `Networking` 搜索和选择
`type=EEG` 的 LSL 流，再主动点击 `Start Data Stream` 才开始收取真实数据。科研人员登录、
打开采集页或仅进入 Session 都不会自动播放 CSV。`PLAYBACK` 选择的文件会转交给独立“实验”页，
仍需点击“开始回放”。

采集中心可以完成：

- 刷新并选择当前局域机可见的 `type=EEG` LSL 流；
- 建立、启动、停止、重连和断开 OpenBCI/LSL 会话；
- 在白色 OpenBCI 风格运行页同时查看 `Time Series / FFT Plot / Accelerometer`，并切换 `Focus / Networking / Packet Loss / Hardware`；
- 查看流名、通道标签、实际采样率、逐通道信号质量和时间戳连续性；
- 将实时数据原样保存为 LSL 兼容 CSV，同时保留 10 秒 NPZ 分块；
- 给下一段 4 秒试次添加真实标签。

`Hardware` 页按说明书展示 ADS1299 的 PGA Gain、Input Type、Bias、SRB2 和 SRB1。
旧的 `Hardware` 页仍只展示 ADS1299 参数，不会伪造通道命令下发结果。当前直接采集后端负责
连接、启动、读取、停止和释放会话；逐通道 PGA/Bias/SRB 命令仍需在完成实机安全验证后接入。

需要与外部 Matlab/OpenViBE 等工具共享数据时，仍可使用 OpenBCI GUI：连接设备并确认原始波形后，打开
`Networking → LSL`，输出 `TimeSeriesRaw` / `type=EEG`。随后在本软件点击
“刷新 LSL 流”，选择流后点击 `Start Session → Start Data Stream`，即可在不重启软件的情况下
切换到实时 LSL，不再需要手改流名才能切换设备。

## CSV 实验回放

进入“科研人员 → 实验”，点击“导入 EEG CSV”可在运行时选择文件，不必重启软件。
预配置 CSV 初始为 `READY`，只有点击“开始回放”后才启动。页面支持暂停/继续、回到开头、
拖动进度和 `0.5x / 1x / 2x` 回放，并将波形继续送入
现有质量检测和解码链路。CSV 回放被安全层禁止驱动真实无人机。

默认文件仍可通过环境变量配置：

将数据放到 `data/samples/start-1.csv`，直接运行：

```bash
python main.py
```

也可指定其他文件：

```bash
BDS_EEG_BACKEND=csv \
BDS_EEG_CSV_PATH="/absolute/path/to/start-1.csv" \
python main.py
```

## 真实 LSL

先在 OpenBCI GUI 的 Networking 页面输出 LSL，默认流名为 `obci_eeg1`、
类型为 `EEG`，再运行：

```bash
BDS_EEG_BACKEND=lsl python main.py
```

若实验室的流名不同：

```bash
BDS_EEG_BACKEND=lsl BDS_LSL_STREAM_NAME="实际流名" python main.py
```

程序优先按 `name` 查找，失败后按 `type=EEG` 查找，也可以在科研采集中心搜索并选择。
采集和推理都在后台线程，
不会阻塞 Qt 主线程。LSL 时间戳用于估计实际采样率；开始全屏刺激试次时，
采集线程会先清空 inlet 历史缓存，再收集新的 4 秒数据。

CSV 适配器同时支持两种格式：原有纯通道 CSV，以及 LSL采集上位机生成的
`sample_index, lsl_timestamp, elapsed_seconds, <各通道>` 格式。后一种会保留原始
LSL 时间戳和通道名，并从有效时间间隔估计实际采样率。

## 运动想象（MI）实验

在“范式”页选择 `MI` 后，采集线程继续接收和显示 EEG，但暂停周期性 FBCCA 解码。
右上角“添加标签”会提供 `静息 / 想象左手 / 想象右手 / 想象双脚`，从点击后的新数据
采集完整 4 秒并保存。建议电极位置关注 C3/Cz/C4，但必须以实际电极帽标签和实验方案为准。
MI 分类器在获得足够的受试者内数据、独立验证和误触发评估前不会接入控制链路。

## 频率配置

当前统一使用传承蜂群六频：`11 / 13.75 / 15 / 16.5 / 18.33 / 20.625 Hz`。
全屏刺激、FBCCA 参考信号和动作映射都读取 `src/bci/config.py`。如果老师修改
频率，必须统一修改并重新采集实验数据，不能只改其中一个模块。

## 全屏刺激与实验按键

- `J` 开始、`K` 停止；
- `0` 标注空闲，`1`–`6` 切换六个动作标签；
- `B` 停止刺激并触发应用内急停；`Esc` 退出全屏。

每个试次结束后，程序会把原始帧时间、六路亮度、刷新率、实测频率和频谱
`.npz/.png` 保存到 `data/stimulus_logs/`。原生 PySide6 实现替代旧工程里的
PsychoPy/Pygame 双份界面，避免维护三套不同的频率配置。

## 信号质量

每个识别窗口都会保存 NaN/Inf、中心化标准差、最大振幅、带通后方差、50 Hz
工频功率占比和坏通道原因。坏通道会从本次识别中排除；全部不可用时拒绝识别。

## 文件输出

默认目录结构：

```text
data/
├── csv_sessions/       # 一次采集对应一个完整 LSL 兼容 CSV 和 JSON 元数据
├── raw_trials/          # 4 秒带真实标签 EEG
├── continuous_raw/      # 长时间录制的 10 秒分块
├── sessions/            # 会话、试次索引、刺激事件标记
├── recognition_logs/    # 识别与硬件命令执行日志
├── stimulus_logs/       # 刺激时序、测频和频谱图片
├── calibration/         # 高度校准建议（不会自动应用）
└── exports/
```

- `data/raw_trials/*.npz`：原始 EEG、逐样本 LSL 时间戳、通道名/单位、采样率、范式、被试、会话、真实标签、开始时间、信号质量及识别结果。
- `data/csv_sessions/lsl_raw_*.csv`：完整原始采集，列为 `sample_index, lsl_timestamp, elapsed_seconds, <各通道>`；同名 JSON 保存会话、被试、来源、采样率和通道元数据。
- `data/continuous_raw/*.npz`：10 秒原始分块及对应逐样本时间戳和通道元数据。
- `data/sessions/*.json`：会话元数据；`raw_trials_index.csv` 为试次索引。
- `data/recognition_logs/recognition_log.csv`：每次 FBCCA 识别、置信度、margin 和门控结果。
- `data/exports/`：保留给后续训练/验证集导出。

点击科研界面右上角“添加标签”后，程序从点击后的新数据开始采满连续 4 秒，标签只应用于该独立试次。没有人工标签
时仍会写识别日志，但不会伪造真实标签，也不会保存成有标签训练试次。

PostgreSQL 当前仍只用于账号系统。后续实验元数据接口只保存上述元数据和
NPZ 文件路径，不把每个 EEG 采样点写入数据库。

## FB-EEGNet 训练与启用

必须先采集足够的七类数据（空闲 + 六个动作）。训练集和验证集都必须包含每一类，
正式评估所需数量应由实验方案确定。

```bash
python -m pip install -r requirements-ml.txt
python -m src.ml.train_fb_eegnet --index data/sessions/raw_trials_index.csv
```

训练会进行分层训练/验证划分、早停，并输出准确率、macro-F1、混淆矩阵和空闲
误触发率。生成的权重与 metadata 经人工复核后才能启用：

```bash
BDS_FB_EEGNET_ENABLED=true python main.py
```

模型加载失败时会回退到 FBCCA。旧 `joblib` 文件不能视为已经完成的 FB-EEGNet 模型。
