# 脑控无人机蜂群作战系统

> `brain-drone-swarm-console` · 基于 Python + PySide6 的脑控无人机蜂群科研上位机。

## 当前状态

本仓库处于**真实接口逐步联调阶段**。账号系统连接 Spring Boot/PostgreSQL；科研界面可回放真实 CSV 或读取 LSL、运行纯 FBCCA 并保存实验记录；Crazyflie/Lighthouse 软件适配层和安全命令队列已经加入，但尚未在本仓库所在电脑上完成实验室实机验收。

已经可运行的内容：

- Spring Boot + PostgreSQL 邮箱注册和登录，密码使用 BCrypt 哈希并返回 JWT；也可切换为离线 Mock 模式。
- 士兵、科研人员、系统工程师、系统管理员等角色页面。
- 原生 PySide6 全屏六频 SSVEP 刺激：`11 / 13.75 / 15 / 16.5 / 18.33 / 20.625 Hz`，支持 J 开始、K 停止、0 空闲、1–6 标签和 B 急停。
- 刺激帧时间、实际刷新率、实测频率、频谱 `.npz/.png` 和开始/停止事件标记。
- 工程师工作台：真实 Qt 左侧导航、运行时接口状态、真实本机性能和设备日志。
- Crazyflie 蜂群连接、真实飞控解锁/锁定、起降、前后左右、停止移动、可恢复电机停转和锁存急停。
- LED 无桨测试、逐架悬停、同步悬停、相对方形轨迹，以及一字/菱形/方形的分阶段绝对坐标转换和到位误差检查。
- 单架 Flow Deck 人工验收模式；该模式禁止打开自动脑控执行。
- 飞行安全门控：实时遥测、最低电压、Lighthouse 状态、场地围栏、最小机距和串行命令队列。
- 脑控执行默认关闭；只允许实时 LSL，且同一意图连续确认两次并经过冷却后提交。
- `start-1.csv` 8 通道回放、真实 `LSLEEGStream`、按 LSL 时间戳校准采样率、刺激开始时清空历史缓存和后台实时波形。
- 纯 FBCCA 六频实时识别；包含 NaN、平坦、振幅、方差、坏通道和 50Hz 工频占比检测。
- 可选 FB-EEGNet 训练/验证/实时推理；没有匹配权重时自动保留真实 FBCCA，不伪造模型结果。
- 识别日志、命令提交/完成/失败日志、连续原始 EEG 分块，以及包含真实标签和质量信息的同步 4 秒 `.npz` 试次。
- 每架高度校准采样与建议补偿文件；结果必须人工复核，不会自动套用旧补偿值。

尚待现场或外部团队提供的能力：

- Crazyflie URI、实验室实测场地围栏、固件参数和实机安全验收。
- 目标识别/跟踪、生命体征和避障服务的协议、地址及数据字典。
- 实验元数据 PostgreSQL 接口（当前原始 EEG 和索引保存在本地文件）。
- 足量、平衡且由实验人员确认的六频加空闲训练数据；训练程序已实现，但仓库不附带可冒充有效模型的权重。
- 生产级 RBAC、审计、服务端身份授权和真实设备安全仲裁。

科研页默认回放本地 `data/samples/start-1.csv`。文件不存在时会明确报告数据源不可用，不再生成 Mock EEG。蜂群后端默认禁用并显示“未连接”，不会绘制虚假无人机。程序不依赖 `artifacts/` 中的 PNG 截图。

## 技术架构

```text
CSV / LSL → EEG QThread → 质量检查 → FBCCA / 可选 FB-EEGNet → 安全确认 → 可选蜂群命令
PySide6 → HTTP + JWT → Spring Boot → PostgreSQL（账号与身份）
PySide6 → CrazyflieSwarmGateway 后台队列 → cflib/Crazyradio → Crazyflie + Lighthouse
```

真实接入集中在 `src/adapters/real/`，装配入口为 [src/container.py](src/container.py)。界面不会直接等待无线电或执行飞行动作。

## 运行

需要 Python 3.10+：

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install -r requirements.txt
```

### 默认本地数据库登录

先启动 PostgreSQL 和 `/Users/geniusalu/Desktop/brain-drone-swarm-server` 中的 Spring Boot 服务，再运行桌面端：

```bash
python main.py
```

### 离线演示模式

如需不联网演示各角色界面，可临时切换为 Mock 模式：

```bash
BDS_AUTH_BACKEND=mock python main.py
```

| 用户名包含 | 进入角色 |
| --- | --- |
| `research` / `科研` | 科研人员 |
| `engineer` / `工程` | 系统工程师 |
| `admin` / `管理` | 系统管理员 |
| 其他 | 作战操作员 |

### 覆盖后端地址

默认认证地址为 `http://127.0.0.1:8080`，可以通过环境变量覆盖：

```bash
export AUTH_API_URL="http://127.0.0.1:8080"
export BDS_AUTH_BACKEND=http
python main.py
```

注册时选择的演示身份会直接保存在 PostgreSQL 的 `app_users.role`。正式部署应关闭注册身份选择，由管理员接口授权。

### Crazyflie 只读遥测与实机控制

第一次只启用只读遥测，不设置 `BDS_HARDWARE_CONTROL_ENABLED`：

```bash
export BDS_SWARM_BACKEND=crazyflie
export BDS_CRAZYFLIE_URIS='radio://.../CF1,radio://.../CF2'
python main.py
```

现场完成净空、急停、Lighthouse、坐标轴和电量检查后，才可增加以下配置。围栏顺序为 `minX,maxX,minY,maxY,minZ,maxZ`，示例值不能直接照搬到真实场地：

```bash
export BDS_SWARM_GEOFENCE='现场测量后填写'
export BDS_HARDWARE_CONTROL_ENABLED=true
python main.py
```

打开“单兵面板 → 脑控”，依次点击连接、解锁，再进行单机/多机分阶段测试。完整流程见 [硬件接入说明](docs/CRAZYFLIE_HARDWARE_INTEGRATION.md)。

没有 Lighthouse/Flow Deck 时只能使用无定位单机模式：控制页改为双虚拟摇杆，左杆控制
气压定高的升降速度，右杆控制横滚/俯仰；松手后摇杆回中。该模式无法锁定水平位置，飞机
仍会漂移，不能把“气压保高”理解成定点悬停。普通“停止电机”可重新解锁；真正的
“紧急停止”会锁住飞控，必须断电重启无人机后再在界面复位并重新连接。

单架 Flow Deck 只开放人工验收：

```bash
export BDS_SWARM_BACKEND=flowdeck
export BDS_CRAZYFLIE_URIS='实际单机URI'
export BDS_SWARM_GEOFENCE='以起飞点为原点实测填写'
export BDS_HARDWARE_CONTROL_ENABLED=true
python main.py
```

### 训练并启用 FB-EEGNet

采集每个类别（含“空闲”）的足量真实试次后：

```bash
python -m pip install -r requirements-ml.txt
python -m src.ml.train_fb_eegnet --index data/sessions/raw_trials_index.csv
BDS_FB_EEGNET_ENABLED=true python main.py
```

训练输出 `models/fb_eegnet_fusion.pt` 和与其匹配的 `models/fb_eegnet_metadata.json`，metadata 内含训练/验证数量、准确率、宏 F1、混淆矩阵和空闲误触发率。

## 运行时数据与接口位置

| 数据类型 | 默认状态 | 真实实现位置 |
| --- | --- | --- |
| EEG 脑电 | CSV 真实记录回放或 LSL 实时流 | [LSLEEGStream](src/adapters/real/lsl_eeg_stream.py) |
| 无人机遥测/命令 | 默认禁用；不生成虚假飞机 | [CrazyflieSwarmGateway](src/adapters/real/crazyflie_swarm_gateway.py) |
| CPU/内存 | 本机实时指标 | [RealSystemMonitor](src/adapters/real/real_system_monitor.py) |
| 工程模块/日志 | 运行时适配器缓存 | [RuntimeEngineerDataProvider](src/adapters/real/runtime_engineer_data.py) |

统一接口定义位于 `src/ports/`；详细接入说明见 [docs/INTERFACE_HOOKS.md](docs/INTERFACE_HOOKS.md)。

## 项目结构

```text
src/
├── app.py                  # 主窗口和角色路由
├── container.py            # Spring HTTP / EEG / 硬件接口装配入口
├── domain/                 # 与 Qt、硬件无关的数据模型
├── ports/                  # EEG、蜂群、系统监控、工程数据等接口
├── bci/                    # 统一频率配置、预处理和纯 FBCCA
├── ml/                     # 可选 FB-EEGNet、预处理、训练评估与推理
├── adapters/
│   ├── mock/               # 仅供自动化测试和显式离线 UI 测试
│   ├── http/               # Spring Boot JWT 认证客户端
│   └── real/               # CSV、LSL、Crazyflie、运行时监控
├── workers/                # QThread EEG 持续采集与4秒窗口
├── services/               # 登录、记录、实机验收、高度校准与工作台状态
└── views/                  # PySide6 主界面及全屏 SSVEP 刺激
```

## 测试

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

当前包含 Spring REST 登录客户端、CSV/LSL、六频 FBCCA、刺激测频、信号质量、实验记录、高度校准、Crazyflie 安全门控和界面交互测试。详细运行方法见 [EEG/FBCCA 接入说明](docs/EEG_FBCCA_INTEGRATION.md)。

## 安全说明

FBCCA 已具备通往 `SwarmGateway` 的受控路径，但默认关闭，CSV 回放永远不能开启实机执行。桌面端的安全门控不能替代飞控固件、物理急停、现场监护和实验室审批。
