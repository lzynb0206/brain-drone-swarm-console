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
| REF | 保持当前参考位置 | 底部 SRB2/REF |
| GND/BIAS | 保持当前地位置 | 底部 BIAS/GND |

直接原生串口采集会输出上述脑区名称。外部 OpenBCI GUI 若只输出 `CH1..CH8`
或 `EXG Channel 0..7`，LSL适配器只在八个标签全部为通用名称时按位置映射；原始
来源标签仍保存在 CSV/NPZ metadata 的 `original_channel_names` 中。若外部流已经有
具体脑区名称，程序不会覆盖。

默认运行时使用带通滤波器组＋八路枕区/顶枕区通道选择＋FBCCA，不执行ICA。

## 采集要求

必须采集七类：`空闲、起飞、前进、后退、左移、右移、降落`。试次文件默认位于
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

## 科研界面自动随机采集

1. 进入“科研人员 → 采集”，启动真实硬件或实时 LSL 数据流；
2. 确认波形持续更新后进入“范式 → SSVEP”；
3. 点击“配置自动实验”，设置每类次数、提示/刺激/休息时长和随机种子；
4. 点击“开始自动采集”，按全屏提示注视目标。默认每类15次，共105个试次。

计划按重复块平衡随机：每块恰好包含一次`空闲+六动作`，相邻标签不重复，固定
seed 可复现。空闲试次中六个目标全部停止闪烁。`P`在安全边界暂停/继续，`Esc`
在当前试次安全保存后中止，休息阶段按`R`可剔除刚才试次并追加同标签补采；质量
判定为`bad`的试次也会自动剔除补采。

默认每次保留刺激开始后的完整3秒原始 EEG 和逐样本 LSL 时间戳，标记0.5秒后的
真实分析起点。当前训练器从该标记精确取随后的2秒（500点），而不是错误地取原始
试次最后2秒；有时间戳缺口的窗口会拒绝训练。每个 NPZ/索引还保存 trial、block、
repetition、retry、seed 以及 cue/stimulus/analysis/rest 时间。若 LSL 只给出
`CH1..CH8` 等通用名称，开始正式实验前会要求人工确认其真实接线确为本页固定顺序。

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

## 公开 Benchmark/BETA 八频复现

公开数据复现与项目实机模型是两条隔离的工作流。脚本只读取已经下载到本机的
Tsinghua Wang2016 Benchmark 或 Liu2020 BETA 原始 `S*.mat`，不会联网下载数据。

先检查原始文件结构和元数据：

```bash
python -m src.ml.reproduce_public_ssvep inspect \
  --benchmark-root /绝对路径/Benchmark \
  --beta-root /绝对路径/BETA
```

然后统一提取论文所用的 `O1、Oz、O2、PO3、POz、PO4、P3、P4`，按每个数据集
自己的频率元数据匹配 `8、9、10、11、12、13、14、15 Hz`。每个原始 epoch 从
刺激开始后0.5秒起取2秒，重采样到250 Hz，并执行5–45 Hz四阶带通、50 Hz陷波和
逐通道标准化：

```bash
python -m src.ml.reproduce_public_ssvep prepare \
  --benchmark-root /绝对路径/Benchmark \
  --beta-root /绝对路径/BETA \
  --output-dir artifacts/public_ssvep \
  --skip-invalid-subjects
```

BETA 中一部分被试只有2秒刺激段。统一丢弃起始0.5秒后已不足完整2秒，脚本会明确
排除并写入 `skipped_subjects`，不会把epoch末尾0.5秒的刺激后数据混进训练窗口。
如需保留这些短试次，应另建全数据统一1.5秒的实验配置，不能与本2秒复现混报。

每个留一被试（LOSO）折独立运行，支持断点式逐折完成。折号从0开始，也可以用
`--held-out-subject benchmark:S01` 指定测试被试：

```bash
python -m src.ml.reproduce_public_ssvep run-fold \
  --prepared-dir artifacts/public_ssvep \
  --fold-index 0 \
  --methods fbcca transformer
```

已完成折默认跳过；`--force` 才会覆盖。也可以先只运行 `--methods fbcca`，以后对
同一折运行 `--methods transformer`，脚本会增量合并结果。所有需要的折完成后汇总：

```bash
python -m src.ml.reproduce_public_ssvep aggregate \
  --prepared-dir artifacts/public_ssvep \
  --methods fbcca transformer
```

输出包括：

- `manifest.json`：数据来源、通道/频率元数据来源、预处理与类别计数；
- `trials_manifest.csv`：每个试次的被试、block、原目标索引和频率；
- `folds/<subject>/metrics.json`、`predictions.csv` 和 Transformer 权重；
- `metrics.json`、`fold_metrics.csv`：所有已完成折的汇总及每种方法尚缺的折。

这条公开复现严格是**8个频率、无空闲类**。其折权重会标记
`runtime_compatible: false`，不能覆盖项目的“空闲+六动作”7类实机权重，也不能用
公开离线准确率代替后续自采跨会话和在线无人机实验。

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
