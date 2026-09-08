# 实验数据目录

运行时自动建立：

```text
data/
├── raw_trials/          # 带真实标签的 8×1000 原始 EEG（NPZ）
├── sessions/            # 会话 JSON 与原始试次索引
├── recognition_logs/    # 每次 FBCCA 判定日志
├── exports/             # 后续数据集导出
└── samples/             # 本地 CSV 回放样例
```

原始 EEG 不写入 PostgreSQL。后续增加实验元数据接口时，数据库只保存被试、
会话、标签、质量、识别结果和文件路径。
