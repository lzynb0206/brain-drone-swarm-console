# EEG 样例数据

本机的 `start-1.csv` 放在此目录，程序默认以 250 Hz、8 通道循环回放。
CSV 文件被 `.gitignore` 排除，避免误把实验原始数据提交到 Git。

也可以不复制文件，运行前设置：

```bash
export BDS_EEG_BACKEND=csv
export BDS_EEG_CSV_PATH="/绝对路径/start-1.csv"
```

该文件没有时间戳或真实标签。真实标签必须在科研界面点击“添加标签”选择，
程序会从选择标签之后重新采满连续 4 秒，并保存为独立 EEG 试次。
