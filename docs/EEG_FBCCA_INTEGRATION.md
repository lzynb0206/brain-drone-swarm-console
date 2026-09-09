# EEG、全屏刺激、FBCCA 与 FB-EEGNet 接入说明

## 当前数据链

```text
start-1.csv 或 LSL EEG（时间戳校准采样率）
        ↓ EEGStream
后台 QThread（连续分块录制、刺激同步试次、4 秒滑动窗）
        ↓
质量检测 → 纯 NumPy FBCCA / 可选 FB-EEGNet → 意图安全阈值
        ↓
科研界面 + recognition_log.csv + raw_trials/*.npz
        ↓ 仅在实时 LSL、安全条件满足且人工开启时
SwarmGateway 二次确认 → 串行硬件命令
```

解码结果默认不会调用 `SwarmGateway`。只有实时 LSL、实机已连接并解锁、
操作员显式开启“脑控执行”、同一意图连续确认两次且冷却通过时，才提交命令。
CSV 回放被代码硬性禁止驱动实机。没有模型权重时，系统继续运行真实 FBCCA，不生成假的深度模型结果。

## CSV 回放（默认）

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

程序优先按 `name` 查找，失败后按 `type=EEG` 查找。采集和推理都在后台线程，
不会阻塞 Qt 主线程。LSL 时间戳用于估计实际采样率；开始全屏刺激试次时，
采集线程会先清空 inlet 历史缓存，再收集新的 4 秒数据。

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
├── raw_trials/          # 4 秒带真实标签 EEG
├── continuous_raw/      # 长时间录制的 10 秒分块
├── sessions/            # 会话、试次索引、刺激事件标记
├── recognition_logs/    # 识别与硬件命令执行日志
├── stimulus_logs/       # 刺激时序、测频和频谱图片
├── calibration/         # 高度校准建议（不会自动应用）
└── exports/
```

- `data/raw_trials/*.npz`：8通道原始 EEG、采样率、被试、会话、真实标签、开始时间、信号质量及识别结果。
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
