# Transformer-SSVEP 复现与实验说明

## 当前实现边界

代码已经具备论文方法的可训练、可加载基线，但仓库不附带任何伪造权重。
没有 `models/transformer_ssvep.pt` 和匹配的 metadata 时，科研界面会将
`Transformer-SSVEP` 标为不可用，点击加载会明确失败；运行时仍保持 FBCCA。

SSVEP 是视觉刺激范式，Transformer 只替换 FBCCA 所在的解码环节：

```text
六频视觉刺激 → 8通道EEG → 2秒窗口 → 4个非重叠时间片
→ log FFT幅值 → 128维嵌入 → 2层/8头Transformer
→ 空闲+六动作概率 → 0.60置信度 → 连续2窗口 → 0.50秒冷却
```

论文使用 500 Hz 输入；当前硬件为 250 Hz，因此项目直接使用 `8×500` 的2秒窗口，
每个时间片125点。代码不会把250 Hz强行插值成500 Hz冒充新增信息。

## 固定通道与接线顺序

| 软件通道 | 电极位置 | Cyton兼容板 |
| --- | --- | --- |
| 1 | O1 | 底部 N1P |
| 2 | Oz | 底部 N2P |
| 3 | O2 | 底部 N3P |
| 4 | PO3 | 底部 N4P |
| 5 | POz | 底部 N5P |
| 6 | PO4 | 底部 N6P |
| 7 | P3 | 底部 N7P |
| 8 | P4 | 底部 N8P |
| REF | A1 | 底部 SRB2 |
| BIAS | A2 | 底部 BIAS |

直接 BrainFlow 采集会输出上述脑区名称。外部 OpenBCI GUI 若只输出 `CH1..CH8`
或 `EXG Channel 0..7`，LSL适配器只在八个标签全部为通用名称时按位置映射；原始
来源标签仍保存在 CSV/NPZ metadata 的 `original_channel_names` 中。若外部流已经有
具体脑区名称，程序不会覆盖。

## 采集要求

必须采集七类：`空闲、起飞、前进、后退、左转、右转、降落`。试次文件默认位于
`data/raw_trials/`，索引位于 `data/sessions/raw_trials_index.csv`。

正式数据至少需要两个具有不同 `session_id` 的会话。训练脚本默认按完整会话分组，
同一会话不会同时落入训练集和验证集。`--split-mode stratified` 只用于少量数据的
管线冒烟测试，不能作为跨会话论文结果。

正式采集前检查：

- 八通道名称和接线顺序完全一致；
- 单位为 `uV`，无削顶、平坦通道和明显50 Hz主导；
- 250 Hz时间戳单调且无大量缺口；
- 每次试次使用人工确认的真实标签；
- 空闲数据包含睁眼、未注视目标和自然小动作等真实负样本。

## 训练

```bash
python -m pip install -r requirements-ml.txt
python -m src.ml.train_transformer_ssvep \
  --index data/sessions/raw_trials_index.csv \
  --split-mode session
```

输出文件：

- `models/transformer_ssvep.pt`
- `models/transformer_ssvep_metadata.json`

metadata 包含通道、参考、采样率、窗口、网络参数、会话划分、准确率、宏 F1、
混淆矩阵和空闲误触发率。权重和 metadata 必须成对使用。

## 启用

重启软件后进入“科研人员 → 算法”，选择 `Transformer-SSVEP` 并点击“加载算法”。
也可以在启动前显式设置：

```bash
BDS_INTENT_DECODER=transformer python main.py
```

若模型不存在、PyTorch未安装或 metadata 不匹配，启动时或加载时会直接报错，不会
静默回退并把 FBCCA 结果显示成 Transformer 结果。

可调复现参数：

```bash
export BDS_TRANSFORMER_MIN_CONFIDENCE=0.60
export BDS_TRANSFORMER_REQUIRED_WINDOWS=2
export BDS_TRANSFORMER_COOLDOWN_SECONDS=0.50
export BDS_EEG_WINDOW_SECONDS=2
export BDS_EEG_DECISION_STEP_SECONDS=0.25
```

## 论文实验注意事项

连续2秒窗口每0.25秒更新一次时相邻窗口重叠87.5%。当前
`ConsecutiveWindowGate` 是论文复现基线，不应被描述为独立证据或绝对安全保证。
后续改进算法应与这条基线分离，并至少报告空闲误触发次数/分钟、触发延迟、准确率、
ITR和跨会话结果。

