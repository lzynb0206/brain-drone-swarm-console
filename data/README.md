# 实验数据目录

项目运行时只使用下面三个一级目录：

```text
data/
├── research_experiments/     # 科研采集、自动实验、校准、分析和导出
├── ssvep_realtime_tests/     # 科研界面的实时 SSVEP 指令测试
└── soldier_ssvep_flights/    # 士兵页面实机 SSVEP 与飞行命令审计
```

各目录内部会按需要自动建立 `raw_trials/`、`continuous_raw/`、`sessions/`、
`recognition_logs/`、`stimulus_logs/` 和 `exports/`。不要再直接向 `data/` 根目录增加
其他实验数据文件夹。

原始 EEG 不写入 PostgreSQL。这三个运行时目录均不纳入 Git；正式实验结束后应按日期、
被试和会话复制到受控存储，并遵循知情同意与数据脱敏要求。

科研采集的受试者归属保存在
`research_experiments/sessions/subject_catalog.json`。目录采用稳定 `subject_id`，一级显示
受试者姓名，二级按采集日期分组；编辑姓名只会原子更新该目录，不会改写原始 NPZ。
没有明确归属的旧批次统一进入“其他”分类，不会根据相邻采集时间猜测具体人员。

2026-10-22 以前的旧数据已整体迁移到桌面目录
`脑控无人机历史实验数据备份-2026-10-22/`，项目内不再保留旧目录。
