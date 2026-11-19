# 三通道左右手运动想象

本目录从原始 EEG 重算两套公开数据。两套数据均可检验 MI 环节。检验同试次 SSVEP→MI 关系还需要串行成对记录。

## BCI Competition IV-2a

9 人，每人两天。保留左手、右手全部试次，每人第一天 T 有 144 条，第二天 E 有 144 条。原始标签 1/2 转为 0/1。标注试次起点后 2 秒出现提示，在提示后截取 [0,3) 秒。取 C3、Cz、C4，250 Hz。E 中 113 条带官方伪迹标记，全部纳入。此公开库已用于工作区此前的研究，在本次重新运行中，模型仅用第一天的数据拟合。[BNCI 官方目录](https://bnci-horizon-2020.eu/database/data-sets)

原项目的 CSP 先对每个 3 秒窗线性去趋势，再做 8–30 Hz 四阶零相位 Butterworth 滤波。用 T 日左右手协方差分别求广义特征向量，取两端空间滤波器，使用对数归一化方差和收缩 LDA。右手概率由二分类 LDA 的判别值求 sigmoid。四子带模型对 8–12、12–16、16–22、22–30 Hz 分别滤波，取迹归一化、向单位阵收缩 5% 的三通道协方差矩阵，取矩阵对数上三角作为特征。训练集标准化后使用 L2 逻辑回归，C=0.1。比较其原始输出和按 T 日六个采集轮次留一所得原始判别分数拟合的 Platt 输出。E 日标签仅用于最终统计。

| 第二天结果 | 正确数 / 总数 | 准确率 | Brier | 对数损失 |
| --- | ---: | ---: | ---: | ---: |
| CSP + 收缩 LDA | 886 / 1296 | 68.36% | 0.1914 | 0.5586 |
| 四子带对数协方差 + LR | 892 / 1296 | 68.83% | 0.1936 | 0.5634 |
| 四子带对数协方差 + LR + Platt | 874 / 1296 | 67.44% | 0.1986 | 0.5733 |

四子带原始模型多判对 6 次，CSP 的 Brier 和对数损失更低。额外 MI 校准在此队列降低了准确率，也增加了概率损失。后续串行实验沿用简单 CSP，跨环节假设单独在配对数据检验。各人的结果在 `results/per_subject.csv`，原始文件 SHA-256、软件版本和基于被试的区间估计见 `results/summary.json`。

在仓库根目录执行。第一行仅在新机器需要获取原始数据时运行。

```bash
.venv312/bin/python scripts/download_public_mi.py --subjects 1 2 3 4 5 6 7 8 9
.venv312/bin/python research/serial_bci/mi/run_mi.py
```

`results/predictions.csv` 每行是一个 E 日试次。原始 run 的序号从 0 起算，trial 是本次 session 所有四类任务中的试次号，从 1 起算。y_true=1 表示右手。后续主链取 `p_right_csp`，按大于 0.5 判右手，手别置信度取 max(q,1−q)。`p_right`、`y_pred`、`correct`、`p_correct` 对应四子带加 Platt 候选，`p_right_uncalibrated` 是同一四子带模型的原始概率。所有列的命中状态都可由 y_true 重新计算。对新数据可复用 `run_mi.csp_right(model, test_x)`，model 由 `CSPClassifier(fs).fit(train_x, train_y)` 拟合，数组维度为试次 × 三通道 × 采样点。训练和测试都使用相同采样率、通道顺序和三秒窗。

## PhysioNet EEGMMIDB

按公开 ID 连续纳入前 12 人。两轮训练，一轮在时间上后置测试，使用与上面相同的 CSP 和三通道，不改滤波和收缩设置。模型按人重新估计。预定取样、标签和试次排除规则见 `physionet_protocol.json`。PhysioNet 标签随任务轮次变化，只读取第 4、8、12 轮单侧手想象，T1=左、T2=右，T0=静息。任务中的视觉提示可能贡献可解码成分，该数据自身无法分离所有感觉和运动想象效应。[Schalk，数据 v1.0.0](https://doi.org/10.13026/C28G6P) · [官方实验说明](https://archive.physionet.org/physiobank/database/eegmmidb/HEADER.shtml)

```bash
.venv312/bin/python research/serial_bci/mi/download_physionet.py --last-subject 12
.venv312/bin/python research/serial_bci/mi/run_physionet.py --last-subject 12
```

`physionet_results/manifest.json` 记录每个原始文件的散列值、可用试次数和丢弃的短事件。`physionet_results/predictions.csv` 中 p_right 始终是 CSP 的右手概率。

12 人的 36 份 EDF 均成功解析，SHA-256 逐一与 PhysioNet 发布清单匹配。每人 30 个训练试次和 15 个测试试次，所有标注想象试次都满足时长要求。冻结的 CSP 在第 12 轮正确 116/180（64.44%），受试者级重采样的准确率 95% 区间为 57.78%–72.22%。Brier 为 0.2343，对数损失 0.7448。常数 0.5 给出 Brier 0.25、对数损失 0.6931，说明分类方向可用，若干高置信错误拖累对数损失。小队列依公开 ID 顺序纳入，没有随机抽样。
