# OpenBCI、LSL、EEG、运动想象与 SSVEP 解码接入说明

## 当前数据链

```text
ExpandableEEG蓝牙虚拟COM/串口 → 原生0xA0/0xCx数据包驱动（直接采集，推荐）
或 OpenBCI GUI → LSL EEG（兼容模式）
或 start-1.csv / 已保存的 lsl_raw_*.csv
        ↓ EEGStream
后台 QThread（连续分块录制、刺激同步试次、SSVEP 2秒滑动窗）
        ↓
质量检测 → FBCCA / Transformer-SSVEP → 意图安全阈值与连续窗口门控
        ↓
科研界面 + lsl_raw_*.csv + recognition_log.csv + raw_trials/*.npz
        ↓ 仅在实时硬件/LSL、安全条件满足且人工开启时
SwarmGateway 二次确认 → 串行硬件命令
```

解码结果默认不会调用 `SwarmGateway`。只有实时硬件或 LSL、实机已连接、
操作员显式完成脑控授权并按 `J` 启动在线刺激、同一意图连续确认且冷却通过时，才提交命令。
地面授权不会启动电机；“起飞”意图本身负责解锁并启动 Flow Hover。
CSV 回放被代码硬性禁止驱动实机。没有模型权重时，系统继续运行真实 FBCCA，不生成假的深度模型结果。

这里的职责必须分清：OpenBCI 是采集硬件及其配套软件，EEG 是采集到的脑电数据，
LSL 是把 EEG 和时间戳实时送进本软件的传输通道，FBCCA 是只针对 SSVEP 频率的解码算法。
运动想象（MI）模式目前只采集、质检和保存带标签原始数据，不运行 FBCCA，也不生成无人机指令。

## 科研人员界面的三段式工作流

说明书表明 ExpandableEEG 的蓝牙模式会先由操作系统映射为 COM/串口，然后按
`Cyton → Serial → 对应COM口` 读取；它不是 Ganglion 的原生 BLE 协议。根据实验室成功
日志和当前设备实测，固件会回复 `v`/`$$$` 欢迎信息，正确启动序列为
`v → d → c → b`。软件直接解析33字节 `0xA0 ... 0xCx` 数据包，固定为8个 EEG
通道和250 Hz，不需要启动 OpenBCI GUI，也不需要先转成 LSL。

当前这台 macOS 会创建 `/dev/cu.ExpandableEEGBT`，但该伪串口打开后不一定建立底层
ACL/RFCOMM 连接，表现为端口可打开、CTS/DSR/CD为真却始终收到0字节。项目在 macOS
检测到该设备名时会自动编译并启用原生 IOBluetooth RFCOMM bridge，直连
`ESP32SPP channel 1`；Windows 仍使用传出 COM 口。两条传输路径之后共用同一个包解析、
质量监测、LSL和算法链路。

进入科研人员界面后的推荐流程：

1. 先在 Windows/macOS 蓝牙设置中配对 ExpandableEEG，确认系统生成 COM 或 `/dev/cu.*` 串口。
2. 在导航栏“采集”点击 `REFRESH / SELECT` 选择对应串口，再点击 `START SESSION`。
3. 进入运行页后点击 `Start Data Stream`，实时查看8通道 Time Series、FFT、加速度/质量和丢包状态。
4. 需要把数据交给本软件的独立接收器或其他实验工具时，在 `Networking` 点击 `START LSL STREAM`。
5. 切换到导航栏“LSL收集”，刷新并选择 `type=EEG` 的流，点击“开始收集”；该页实时显示波形并自动保存完整 CSV。
6. 采集结束后进入导航栏“实验”，导入刚保存的 CSV 做回放、复盘和离线算法验证。

蓝牙“已配对”不等于正在输出数据。macOS 只有程序打开 SPP 串口期间才会显示“已连接”，
程序关闭后自动变回“未连接”属于正常现象。采集时必须关闭其他正在使用相同串口的
OpenBCI GUI/旧项目进程；原生驱动会申请独占锁并在冲突时明确报错。

## Type-C 配置与零字节诊断

商家配置程序的协议已经移植到 `scripts/expandable_eeg_config.py`。Type-C 数据口正常
枚举为 USB 串口后，可以先只读探测：

```bash
python scripts/expandable_eeg_config.py --port /dev/cu.usbmodemXXXX
```

设备应回复 `eeg-模式-通道数`。需要切回蓝牙模式时执行：

```bash
python scripts/expandable_eeg_config.py \
  --port /dev/cu.usbmodemXXXX \
  --set-bt ExpandableEEGBT
```

配置发送后设备会重启。如果 `/dev/cu.*` 和系统 USB 列表都没有新增端口，说明当前
Type-C 连接只在供电；应更换确认支持数据传输的线或 USB 口。此时软件无法跨过物理层
替设备切换模式。若 Windows 蓝牙 SPP 能打开但 `b` 后仍收到0字节，应核对是否选择了
`ESP32SPP` 的传出 COM 口。macOS 不应再以 `/dev/cu.*` 打开成功作为连接依据；项目会
直接建立 RFCOMM channel 1，并以收到 `v` 欢迎信息和 `0xA0...0xCx` 数据包作为成功标准。

因此，LSL不是脑电，也不是采集设备；它是实时搬运 EEG 样本和时间戳的通道。采集页关闭 LSL
输出不会停止硬件采集，LSL收集页暂停波形也不会停止 CSV 写入。

## OpenBCI兼容控制面板

进入“科研人员 → 采集”后先看到按 OpenBCI GUI 视觉结构适配的 `System Control Panel`。
它不是把官方软件所有无关选项原样堆进项目，而是固定为说明书和现有硬件实际需要的
`ExpandableEEG / Cyton → Bluetooth Virtual COM / Serial → 8 Channels → 250 Hz`。

点击 `Start Session` 后进入运行界面，再主动点击 `Start Data Stream` 才开始读取真实设备。
科研人员登录、打开采集页或进入 Session 都不会自动播放 CSV。运行页 `Networking` 是 LSL
输出端；LSL的搜索、接收和 CSV 保存位于独立“LSL收集”页面。

采集中心可以完成：

- 刷新并选择蓝牙映射出的 COM/串口，建立、启动、停止和释放 ExpandableEEG 会话；
- 在运行页查看经过独立显示滤波的 `Time Series / FFT Plot / Accelerometer`，并切换 `Focus / Networking / Packet Loss / Hardware`；显示使用 5–50 Hz、50/60 Hz 工频抑制与固定 ±200 μV 量程，CSV、LSL 和算法仍接收原始样本；
- 查看通道标签、实际采样率、逐通道信号质量和时间戳连续性；
- 从 Networking 开关标准 `name=obci_eeg1, type=EEG` 的 LSL 广播；
- 给下一段 SSVEP 2秒试次或 MI 4秒试次添加真实标签。

`Hardware` 页按说明书展示 ADS1299 的 PGA Gain、Input Type、Bias、SRB2 和 SRB1。
旧的 `Hardware` 页仍只展示 ADS1299 参数，不会伪造通道命令下发结果。当前直接采集后端负责
连接、启动、读取、停止和释放会话；逐通道 PGA/Bias/SRB 命令仍需在完成实机安全验证后接入。

需要与本软件的“LSL收集”页或外部 Matlab/OpenViBE 等工具共享数据时，在采集运行页打开
`Networking → START LSL STREAM`，输出 `name=obci_eeg1 / type=EEG`。也可以跳过本软件的
采集页，让其他软件发布同类 LSL 流，再由“LSL收集”页接收。

## CSV 实验回放

进入“科研人员 → 实验”，点击“导入 EEG CSV”可在运行时选择文件，不必重启软件。
预配置 CSV 初始为 `READY`，只有点击“开始回放”后才启动。页面支持暂停/继续、回到开头、
拖动进度和 `0.5x / 1x / 2x` 回放，并将波形继续送入
现有质量检测和解码链路。CSV 回放被安全层禁止驱动真实无人机。

正常启动不会自动播放 `start-1.csv`。如需通过启动参数显式进入 CSV 实验回放：

```bash
BDS_EEG_BACKEND=csv \
BDS_EEG_CSV_PATH="/absolute/path/to/start-1.csv" \
python main.py
```

## 真实 LSL

可以直接在本软件“采集 → Networking”启动 LSL 输出，也可以让 OpenBCI GUI 或其他设备发布
默认流名 `obci_eeg1`、类型 `EEG` 的数据流，再运行：

```bash
python main.py
```

若实验室的流名不同：

```bash
BDS_EEG_BACKEND=lsl BDS_LSL_STREAM_NAME="实际流名" python main.py
```

程序优先按 `name` 查找，失败后按 `type=EEG` 查找，也可以在科研采集中心搜索并选择。
采集和推理都在后台线程，
不会阻塞 Qt 主线程。LSL 时间戳用于估计实际采样率；开始全屏刺激试次时，
采集线程会先清空 inlet 历史缓存，再收集新的 SSVEP 2秒数据。

CSV 适配器同时支持两种格式：原有纯通道 CSV，以及 LSL采集上位机生成的
`sample_index, lsl_timestamp, elapsed_seconds, <各通道>` 格式。后一种会保留原始
LSL 时间戳和通道名，并从有效时间间隔估计实际采样率。

## 运动想象（MI）实验

在“范式”页选择 `MI` 后，采集线程继续接收和显示 EEG，但暂停周期性 FBCCA 解码。
右上角“添加标签”会提供 `静息 / 想象左手 / 想象右手 / 想象双脚`，从点击后的新数据
采集完整 4 秒并保存。建议电极位置关注 C3/Cz/C4，但必须以实际电极帽标签和实验方案为准。
MI 分类器在获得足够的受试者内数据、独立验证和误触发评估前不会接入控制链路。

## 频率配置

当前统一使用六频 v2：`14 / 16 / 18 / 20 / 22 / 24 Hz`，对应起飞、前进、后退、左移、右移、降落。
FBCCA 默认分数下限为 0.35、差值下限为 0.06；12 秒空闲校准时六块持续闪烁，操作者只注视中央。按各频率空闲分数估计背景和阈值，不再把高噪声阈值压低到固定上限。证据不足显示“未识别”。这些参数仍需真实操作者测试，不构成准确率保证。
刺激使用 Qt frameSwapped 驱动并请求垂直同步；日志绘制频率不等于光电传感器测得的物理屏幕频率。旧 Transformer 权重和旧频率采集记录不会用于新配置。

2026-10-17 现场短测尚未通过，不能用于实飞。最终试次组为
`auto-20261017-223211-730e95`（14 次，含 2 次空闲）。用记录的 analysis LSL
标记对齐、固定截取 2 秒、默认 FBCCA 门限作离线诊断时，1 个动作试次因
时间戳间断被剔除；其余 11 个动作试次仅 1 个最高分候选匹配标签，4 个错误
候选通过门限，0 个正确动作通过门限。2 个空闲试次均未通过门限。
这是固定试次窗诊断，不是完整在线滚动窗口准确率，也不能与采集时保存的
3 秒全窗评分混为一谈。186 项软件测试通过不代表佩戴者的识别性能通过。
Qt 静态绘制检查约 60 次/秒，与本机报告的 60 Hz 一致，但仍未作物理光电测量。
全屏刺激、FBCCA 参考信号和动作映射都读取 `src/bci/config.py`。如果老师修改
频率，必须统一修改并重新采集实验数据，不能只改其中一个模块。

## 全屏刺激与实验按键

- `J` 开始、`K` 停止；
- `0` 标注空闲，`1`–`6` 切换六个动作标签；
- `B` 停止刺激并触发应用内急停；`Esc` 退出全屏。

每个试次结束后，程序会把原始帧时间、六路亮度、刷新率、实测频率和频谱
`.npz/.png` 保存到 `data/stimulus_logs/`。原生 PySide6 实现替代旧工程里的
PsychoPy/Pygame 双份界面，避免维护三套不同的频率配置。

士兵登录后不需要进入科研界面。点击“启动 SSVEP 驾驶”后，程序自动选择已配对的
ExpandableEEG，启动蓝牙采集线程并发布 `name=obci_eeg1, type=EEG`；独立解码线程
从该 LSL 流收到首帧后才开放脑控授权并进入全屏刺激。在线驾驶模式不会接受数字键
真实标签，也不会生成伪训练试次；`K`、`Esc`、`B` 或关闭窗口会停止闪烁，并关闭
采集、LSL 输出和脑控授权。地面授权本身不启动电机，
授权后、按 `J` 启动闪烁之前也不会接受飞行指令；系统会等待新的完整 EEG 窗口，
只有通过安全门控的“起飞”意图才会启动 Flow Hover。每次动作后必须先移开视线，
由一次拒识/空闲结果释放动作锁，持续注视不会重复提交同一飞行指令。

## 信号质量

每个识别窗口都会保存 NaN/Inf、中心化标准差、最大振幅、带通后方差、50 Hz
工频功率占比和坏通道原因。坏通道会从本次识别中排除；全部不可用时拒绝识别。

## 文件输出

默认目录结构：

```text
data/
├── csv_sessions/       # 一次采集对应一个完整 LSL 兼容 CSV 和 JSON 元数据
├── raw_trials/          # SSVEP 2秒 / MI 4秒带真实标签 EEG
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
- `data/recognition_logs/recognition_log.csv`：每次 EEG 解码、置信度、margin 和门控结果。
- `data/exports/`：保留给后续训练/验证集导出。

点击科研界面右上角“添加标签”后，程序从点击后的新数据开始采满连续 SSVEP 2秒或 MI 4秒，标签只应用于该独立试次。没有人工标签
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
