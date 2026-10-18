# 实验数据目录

运行时自动建立：

```text
data/
├── raw_trials/               # 带真实标签的原始 EEG（NPZ）
├── continuous_raw/           # 连续采集的原始 EEG 分块
├── csv_sessions/             # LSL/硬件采集导出的 CSV 会话
├── single_target_calibration/ # 个人模板单目标校准原始试次
├── sessions/                 # 会话 JSON、事件标记与原始试次索引
├── recognition_logs/         # 在线判定与飞控命令审计
├── stimulus_logs/            # 刺激帧时间和频率记录
├── exports/                  # 数据集导出
└── samples/                  # 本地 CSV 回放样例
```

原始 EEG 不写入 PostgreSQL。后续增加实验元数据接口时，数据库只保存被试、
会话、标签、质量、识别结果和文件路径。

这些运行时数据默认不纳入 Git。把目录从 Git 索引移出不会删除本机文件，但也意味着
重新克隆仓库不会带回实验数据，正式实验必须另做受控备份。当前最新个人模板对应的
60 轮来源数据保留在 `single_target_calibration/single-20261018-210353-640369/`。

`recognition_logs/recognition_log.csv` 只保存紧凑质量摘要，达到 16 MiB 后自动轮转为
带时间戳的文件。逐通道完整质量信息与原始波形保存在带标签的 NPZ 试次中。
