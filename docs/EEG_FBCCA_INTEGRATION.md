# EEG、LSL 与 FBCCA 接入说明

## 当前数据链

```text
start-1.csv 或 LSL EEG
        ↓ EEGStream
后台 QThread（连续采集和 4 秒滑动窗）
        ↓
纯 NumPy FBCCA（11 / 13.75 / 15 / 16.5 / 18.33 Hz）
        ↓
科研界面显示 + recognition_log.csv
        ↓ 仅当人工选择真实标签
raw_trials/*.npz + raw_trials_index.csv
```

解码结果不会调用 `SwarmGateway`，当前阶段不会产生任何无人机动作。

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
不会阻塞 Qt 主线程。

## 文件输出

默认输出目录为 `data/recordings/`：

- `recognition_log.csv`：每次 FBCCA 识别，包括各类别分数、阈值结果、真实标签和原始试次路径。
- `raw_trials_index.csv`：带标签原始试次索引。
- `raw_trials/*.npz`：`eeg`（通道×样本）、`fs`、`true_label` 和 JSON 元数据。

点击科研界面右上角“添加标签”后，程序从点击后的新数据开始采满连续 4 秒，标签只应用于该独立试次。没有人工标签
时仍会写识别日志，但不会伪造真实标签，也不会保存成有标签训练试次。
