# EEG、LSL 与 FBCCA 接入说明

## 当前数据链

```text
start-1.csv 或 LSL EEG
        ↓ EEGStream
后台 QThread（连续采集和 4 秒滑动窗）
        ↓
预处理 → 纯 NumPy FBCCA → 意图安全阈值
        ↓
科研界面显示 + recognition_log.csv
        ↓ 仅当人工选择真实标签
raw_trials/*.npz + raw_trials_index.csv
```

解码结果默认不会调用 `SwarmGateway`。只有实时 LSL、实机已连接并解锁、
操作员显式开启“脑控执行”、同一意图连续确认两次且冷却通过时，才提交命令。
CSV 回放被代码硬性禁止驱动实机。

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

## 频率配置

当前暂用传承代码的单机五频：`11 / 13.75 / 15 / 16.5 / 18.33 Hz`。
蜂群资料中的 `20.625 Hz` 没有启用。老师确认最终频率后，只修改
`src/bci/config.py`，界面、参考信号和标签映射必须继续共用这一份配置。

## 文件输出

默认目录结构：

- `data/raw_trials/*.npz`：8通道原始 EEG、采样率、被试、会话、真实标签、开始时间、信号质量及识别结果。
- `data/sessions/*.json`：会话元数据；`raw_trials_index.csv` 为试次索引。
- `data/recognition_logs/recognition_log.csv`：每次 FBCCA 识别、置信度、margin 和门控结果。
- `data/exports/`：保留给后续训练/验证集导出。

点击科研界面右上角“添加标签”后，程序从点击后的新数据开始采满连续 4 秒，标签只应用于该独立试次。没有人工标签
时仍会写识别日志，但不会伪造真实标签，也不会保存成有标签训练试次。

PostgreSQL 当前仍只用于账号系统。后续实验元数据接口只保存上述元数据和
NPZ 文件路径，不把每个 EEG 采样点写入数据库。

## 暂不启用的能力

- `FB_EEGNET_ENABLED = False`：没有足够的新带标签数据、`fb_eegnet_fusion.pt`
  和匹配的 metadata 前保持关闭；旧 `joblib` 不能视为已训练的深度模型。
- Crazyflie 软件网关已加入，但默认禁用。现场仍须依次完成只读遥测、单机人工、
  单机实时脑控、多机人工、多机脑控和实验室安全审核。
