# 脑控无人机蜂群作战系统

> `brain-drone-swarm-console` · 基于 Python + PySide6 的脑控无人机蜂群科研上位机。

## 当前状态

本仓库处于**真实接口逐步联调阶段**。账号系统连接 Spring Boot/PostgreSQL；科研界面可回放真实 CSV 或读取 LSL、运行纯 FBCCA 并保存实验记录；Crazyflie + Flow Deck 软件适配层和安全命令队列已经加入，但仍需完成现场实机验收。

已经可运行的内容：

- Spring Boot + PostgreSQL 邮箱注册和登录，密码使用 BCrypt 哈希并返回 JWT；也可切换为离线 Mock 模式。
- 士兵、科研人员、系统工程师、系统管理员等角色页面。
- 原生 PySide6 全屏六频 SSVEP 刺激：`11 / 13.75 / 15 / 16.5 / 18.33 / 20.625 Hz`，支持 J 开始、K 停止、0 空闲、1–6 标签和 B 急停。
- 刺激帧时间、实际刷新率、实测频率、频谱 `.npz/.png` 和开始/停止事件标记。
- 工程师工作台：真实 Qt 左侧导航、运行时接口状态、真实本机性能和设备日志。
- 单架 Crazyflie 连接、Flow Hover、前后左右/高度摇杆、受控降落、停止移动、可恢复电机停转和锁存急停。
- Flow Deck V1/V2 与 AI Deck 参数检测；AI Deck 摄像头数据尚未接入。
- 飞行安全门控：实时遥测、3.2V 低电压提示与起飞限制、Flow 下视距离/光流质量和串行命令队列。
- 脑控执行默认关闭；只允许实时 LSL，且同一意图连续确认两次并经过冷却后提交。
- `start-1.csv` 8 通道回放、真实 `LSLEEGStream`、按 LSL 时间戳校准采样率、刺激开始时清空历史缓存和后台实时波形。
- 纯 FBCCA 六频实时识别；包含 NaN、平坦、振幅、方差、坏通道和 50Hz 工频占比检测。
- 可选 FB-EEGNet 训练/验证/实时推理；没有匹配权重时自动保留真实 FBCCA，不伪造模型结果。
- 识别日志、命令提交/完成/失败日志、连续原始 EEG 分块，以及包含真实标签和质量信息的同步 4 秒 `.npz` 试次。

尚待现场或外部团队提供的能力：

- 单架 Crazyflie URI、Flow Deck/固件参数和实机安全验收。
- 目标识别/跟踪、生命体征和避障服务的协议、地址及数据字典。
- 实验元数据 PostgreSQL 接口（当前原始 EEG 和索引保存在本地文件）。
- 足量、平衡且由实验人员确认的六频加空闲训练数据；训练程序已实现，但仓库不附带可冒充有效模型的权重。
- 生产级 RBAC、审计、服务端身份授权和真实设备安全仲裁。

科研页默认回放本地 `data/samples/start-1.csv`。文件不存在时会明确报告数据源不可用，不再生成 Mock EEG。蜂群后端默认禁用并显示“未连接”，不会绘制虚假无人机。程序不依赖 `artifacts/` 中的 PNG 截图。

## 技术架构

```text
CSV / LSL → EEG QThread → 质量检查 → FBCCA / 可选 FB-EEGNet → 安全确认 → 可选蜂群命令
PySide6 → HTTP + JWT → Spring Boot → PostgreSQL（账号与身份）
PySide6 → CrazyflieSwarmGateway 后台队列 → cflib/Crazyradio → Crazyflie + Flow Deck
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

### Crazyflie + Flow Deck 只读遥测与实机控制

第一次只启用只读遥测，不设置 `BDS_HARDWARE_CONTROL_ENABLED`：

```bash
export BDS_SWARM_BACKEND=flowdeck
export BDS_CRAZYFLIE_URIS='实际单机URI'
python main.py
```

现场完成净空、急停、Flow Deck 测距/光流和电量检查后，才可打开实机控制：

```bash
export BDS_HARDWARE_CONTROL_ENABLED=true
python main.py
```

Crazyradio 连接必须使用无人机实际配置的 Radio URI，例如
`radio://0/80/2M/E7E7E7E701`；`usb://...` 不是 Crazyradio 无线链路 URI。URI 也可以在
“单兵面板 → 脑控 → 配置 URI”中修改，修改前需要先断开当前连接。

打开“单兵面板 → 脑控”后的基本流程：

1. 点击“连接”，等待界面显示“Flow Deck 就绪”。
2. 在“启动目标高度”处用 `-` / `+` 调整，或点击数值直接输入高度。
3. 将无人机水平放置在有纹理、光照正常的地面，点击“启动 Flow Hover”。
4. 左杆控制目标高度升降，右杆控制前后左右速度；松开摇杆后，水平速度归零并保持最后目标高度。
5. 正常结束飞行使用“受控降落”；“停止电机”和“紧急停止”只用于异常情况。

程序连接后读取 `deck.bcFlow` / `deck.bcFlow2`，未检测到 Flow Deck 会拒绝操控。Flow
Hover 使用 Crazyflie 官方 Hover setpoint：持续发送机体系 `vx/vy`、偏航速度和离地目标
高度；启动时以最高 0.2m/s 渐升到界面设置的高度。可设置范围默认为 0.20–2.00m，
实际高度上限还会按硬件限制为 Flow Deck V1 的 1.0m 或 Flow Deck V2 的 2.0m。AI Deck
目前只做硬件识别，摄像头数据尚未接入。完整流程见
[硬件接入说明](docs/CRAZYFLIE_HARDWARE_INTEGRATION.md)。

#### 遥测字段说明

| 界面字段 | 数据来源 | 说明 |
| --- | --- | --- |
| 高度 | `stateEstimate.z` | 飞控状态估计器给出的估计高度，Flow Deck 下视测距与惯性数据会参与估计；不是单独的激光测距原值。 |
| 速度 | `stateEstimate.vx/vy/vz` | 软件计算三维合速度 `sqrt(vx² + vy² + vz²)`，单位 m/s；这是估计速度，不是 Flow Deck 直接测得的单一速度。 |
| 电压 | `pm.vbat` | 飞控上报的实时电池端电压。电机负载变化时电压会波动。 |
| 电量 | 由 `pm.vbat` 换算 | 当前按 3.0–4.2V 线性换算为 0–100%，只适合界面参考，不是电量计测得的精确剩余容量。 |

速度当前直接显示飞控估计值，没有进行界面滤波。无人机贴地、被手拿起、明显倾斜、地面
纹理不足或状态估计器刚初始化时，速度可能瞬间偏大；这不等于无人机真的以该速度移动。
判断飞行状态时应同时观察机体实际运动、Flow 质量和高度，不能只看单个速度数值。

#### 低电压、降落和停止逻辑

- 起飞前低于 3.2V 会拒绝启动 Flow Hover。
- 飞行中电压连续约 1 秒低于 3.2V 时，界面提示尽快受控降落，但不会剥夺摇杆控制，也不会阻止受控降落。
- 低电压飞行受控降落后，本次连接会禁止再次起飞；应更换或充电后重新连接。
- “受控降落”接管当前正在运行的 Flow Hover 控制流，根据当前目标高度下降，落地后停止电机并解除普通解锁状态，可以在电量正常时再次启动。
- “停止电机”会立即停止当前控制流，属于可恢复的异常处置；确认安全后可以重新启动操控。
- “紧急停止”是锁存式安全动作，不能当作普通降落使用；触发后需要给无人机断电重启，再在软件中复位并重新连接。

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
├── services/               # 登录、实验记录、脑控安全与工作台状态
└── views/                  # PySide6 主界面及全屏 SSVEP 刺激
```

## 测试

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

当前包含 Spring REST 登录客户端、CSV/LSL、六频 FBCCA、刺激测频、信号质量、实验记录、Flow Deck 安全门控和界面交互测试。详细运行方法见 [EEG/FBCCA 接入说明](docs/EEG_FBCCA_INTEGRATION.md)。

## 安全说明

FBCCA 已具备通往 `SwarmGateway` 的受控路径，但默认关闭，CSV 回放永远不能开启实机执行。桌面端的安全门控不能替代飞控固件、物理急停、现场监护和实验室审批。
