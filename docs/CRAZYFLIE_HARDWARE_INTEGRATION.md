# Crazyflie / Lighthouse 实机接入与验收

## 已迁移的代码边界

本项目没有把传承目录里的 Tkinter 上位机整体塞进 PySide6。可复用的连接、估计器、Lighthouse 遥测和高层飞行动作已经整理到：

- `src/adapters/real/crazyflie_swarm_gateway.py`：Crazyradio、Swarm、高层指令与安全门控。
- `src/services/workbench_service.py`：连接、解锁、人工动作和脑控二次确认。
- `src/views/main_view.py`：只读运行时缓存，不在 Qt 主线程执行 cflib。

传承目录中的 OpenBCI GUI、Windows 虚拟环境、build 输出、模型缓存和旧 Tkinter 页面不应复制进本项目。

## 必须由老师或硬件负责人确认

在任何带桨飞行前，必须提供并记录：

1. 实际 Crazyflie URI 清单以及每个 URI 对应的机身编号。
2. Crazyflie、Crazyradio、Lighthouse deck 和基站固件版本。
3. 本实验室坐标系方向、原点、可飞边界和最大高度。
4. 最低允许电压、最小机间距、失联策略和物理急停负责人。
5. 最终 SSVEP 频率—动作映射；刺激端、FBCCA 与动作端必须使用同一配置。
6. 目标识别、避障、生命体征等外部模块的协议文档、示例包、时间戳和坐标定义。

代码不会内置传承文件中的 radio 地址，也不会猜测场地围栏。

## 环境

cflib 需要 Python 3.10+。macOS 若使用较新的 Python 且找不到 USB 库，需要通过 Homebrew 安装 libusb。建议建立新的 Python 3.11 虚拟环境后安装 `requirements.txt`。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 分阶段启用

### 1. 无桨、只读遥测

```bash
export BDS_SWARM_BACKEND=crazyflie
export BDS_CRAZYFLIE_URIS='实际URI1,实际URI2'
python main.py
```

不要设置 `BDS_HARDWARE_CONTROL_ENABLED`。此时可以连接和查看位置、电压、速度、Lighthouse 状态，但任何飞行动作都会被拒绝。

### 2. 单机人工动作

先测量场地围栏，再设置：

```bash
export BDS_SWARM_GEOFENCE='minX,maxX,minY,maxY,minZ,maxZ'
export BDS_HARDWARE_CONTROL_ENABLED=true
```

建议顺序：无桨连接 → Lighthouse 状态 → 有桨单机低高度起降 → 前进/后退 → 左右 → 降落 → 急停验证。每一步都必须由现场安全负责人确认。

### 3. 多机人工动作

依次进行单机悬停、逐机悬停、同步悬停、相对前后移动。当前代码还没有开放队形切换，必须先增加目标点机间距和轨迹冲突检查。

### 4. 实时脑控

必须把 `BDS_EEG_BACKEND=lsl`，确认流名 `obci_eeg1`、8 通道、250 Hz，并完成离线准确率/误触发统计。界面中的“脑控执行”要求：

- 实时 LSL，拒绝 CSV 回放；
- FBCCA 结果通过阈值；
- 同一意图连续出现两次；
- 2.5 秒冷却；
- Crazyflie 已连接并由操作员解锁；
- 网关再次检查遥测、电压、Lighthouse、围栏和机间距。

## 尚未完成、不能假装完成的接口

- 避障传感器/视觉服务及其坐标融合。
- 目标识别、跟踪和任务规划服务。
- 心率、疲劳、压力等多模态 LSL 数据。
- 返航策略（Crazyflie 室内环境通常不能照搬 GPS 返航）。
- 实验元数据写入 PostgreSQL 的后端 API。
- 飞行命令服务端 RBAC 与不可篡改审计。

这些模块在界面中应显示“未接入”，不得用随机数冒充真实状态。
