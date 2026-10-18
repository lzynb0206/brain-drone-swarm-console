# 脑控无人机蜂群作战系统

> `brain-drone-swarm-console` · 基于 Python 3.12 + PySide6 的脑控无人机蜂群科研上位机。

## 当前状态

本仓库处于**真实接口逐步联调阶段**。账号系统连接 Spring Boot/PostgreSQL；科研界面可通过原生串口协议直接连接 ExpandableEEG，也可读取 LSL 或回放真实 CSV，并保存实验记录；Crazyflie + Flow Deck 软件适配层和安全命令队列已经加入，但仍需完成现场实机验收。

已经可运行的内容：

- Spring Boot + PostgreSQL 邮箱注册和登录，密码使用 BCrypt 哈希并返回 JWT；也可切换为离线 Mock 模式。
- 士兵、科研人员、系统工程师、系统管理员等角色页面。
- 原生 PySide6 全屏六频 SSVEP 刺激：`8 / 9 / 10 / 11 / 12 / 13 Hz`（依次为起飞、前进、后退、左移、右移、降落），使用传承版大色块、4秒识别窗与 Oz/PO3/PO4 三个枕-顶枕区通道，支持 J 开始、K 停止、0 空闲、1–6 标签和 B 急停。单目标页面按正式布局逐类独闪采集10次，生成个人相似度模板并由FBCCA做一致性校验。
- 刺激帧时间、实际刷新率、实测频率、频谱 `.npz/.png` 和开始/停止事件标记。
- 工程师工作台：真实 Qt 左侧导航、运行时接口状态、真实本机性能和设备日志。
- 多架 Crazyflie 连接、Flow 起飞、Lighthouse 绝对位置保持、三机自动编队、整体移动、受控降落、停止移动和锁存急停。
- Flow Deck V1/V2 与 AI Deck 参数检测；AI Deck 摄像头数据尚未接入。
- 飞行安全门控：实时遥测、3.2V 低电压提示、Flow 下视距离/光流质量和串行命令队列。
- 士兵脑控页提供一键“启动 SSVEP 驾驶”：自动连接 ExpandableEEG、发布 `obci_eeg1`、由独立 LSL 接收线程运行 FBCCA，并在收到首帧后才开放脑控授权；不需要进入科研页面。地面授权不会启动电机，只有识别到“起飞”才进入现有 Flow Hover 起飞与悬停链路。
- 脑控执行默认关闭；只允许实时硬件或 LSL 数据源，且同一意图连续确认两次、经过冷却并在每次动作后检测到注视释放才可再次提交。
- 科研导航按真实数据链拆成三个连续页面：`采集`用 ExpandableEEG 原生数据包协议直连蓝牙虚拟 COM/串口（固定8通道、250 Hz）；macOS 自动绕过失效的伪串口并直连 RFCOMM channel 1，Windows 使用传出 COM 口；`LSL收集`独立接收 EEG 流、显示波形/时间戳质量并保存 CSV；`实验`专门回放 CSV。
- `采集`页采用 OpenBCI GUI 的 System Control Panel 与 Time Series + FFT + Accelerometer 结构，但只保留本项目实际使用的 Cyton 兼容配置，避免实验人员误选 Ganglion、Synthetic 或 Playback。
- “实验”页专用于 CSV 导入、暂停/继续、进度定位、倍速回放和算法复盘，CSV 回放永远不能启用实机脑控。
- `start-1.csv` 与 LSL采集上位机 `lsl_raw_*.csv` 回放、真实 `LSLEEGStream`、按 LSL 时间戳校准采样率、刺激开始时清空历史缓存和后台实时波形。
- MI 模式支持静息/左手/右手/双脚想象的 4 秒标签采集；MI 模式不运行仅适用于 SSVEP 的 FBCCA，也不输出无人机指令。
- 纯 FBCCA 六频基线，以及可训练的 Transformer-SSVEP：2秒/250 Hz八通道输入、
  频谱分片、自注意力分类、0.60置信度、连续双窗口确认和0.50秒冷却。
- 解码器可在科研“算法”页真实切换；缺少 Transformer 权重时明确拒绝加载，不静默伪装。
- 包含 NaN、平坦、振幅、方差、坏通道和 50Hz 工频占比检测。
- 可选 FB-EEGNet 训练/验证/实时推理；没有匹配权重时自动保留真实 FBCCA，不伪造模型结果。
- 识别日志、命令提交/完成/失败日志、连续原始 EEG 分块，以及包含真实标签和质量信息的同步 SSVEP 4秒/MI 4秒 `.npz` 试次。

尚待现场或外部团队提供的能力：

- 单架 Crazyflie URI、Flow Deck/固件参数和实机安全验收。
- 目标识别/跟踪、生命体征和避障服务的协议、地址及数据字典。
- 实验元数据 PostgreSQL 接口（当前原始 EEG 和索引保存在本地文件）。
- 足量、平衡且由实验人员确认的六频加空闲训练数据；训练程序已实现，但仓库不附带可冒充有效模型的权重。
- 生产级 RBAC、审计、服务端身份授权和真实设备安全仲裁。

正常启动默认等待实时 `LSL · obci_eeg1`，不会自动播放任何 CSV。本地 `data/samples/start-1.csv` 只作为“实验”页主动导入后的回放文件；CSV 回放不会出现在实时“采集”链路，也不能驱动实机。蜂群后端默认禁用并显示“未连接”，不会绘制虚假无人机。程序不依赖 `artifacts/` 中的 PNG 截图。

## 技术架构

```text
ExpandableEEG（原生串口直连） / LSL / CSV
    ↓
EEGWorker（后台采集、4秒窗口化、记录）
    ↓
FBCCA / Transformer-SSVEP → 连续窗口安全门 → BCIExecutionService
                                               ↓
角色 QWidget → WorkbenchService 应用门面 → SwarmControlService
                                               ↓
CrazyflieSwarmGateway 状态机 → 命令队列 → Flow/Lighthouse 控制 → cflib/Crazyradio

LoginView → AuthService → HTTP + JWT → Spring Boot → PostgreSQL
```

士兵在线驾驶固定使用更严格的单向链路：`ExpandableEEG → 蓝牙采集线程 → LSL
obci_eeg1 → EEGWorker → FBCCA/Transformer`。科研页仍可独立选择直接硬件、外部 LSL
或 CSV 实验回放；二者不要求跨角色切换。

真实接入集中在 `src/adapters/real/`，装配入口为 [src/container.py](src/container.py)。界面不会直接等待无线电或执行飞行动作。

当前核心模块已经按职责拆分：

- `CrazyflieSwarmGateway` 只保留应用侧蜂群状态机；配置、命令队列、飞行动作执行、
  遥测转换、遥测安全判定、紧急停机协调、事件日志、编队状态转换、Flow 控制循环和
  cflib 连接分别放在独立模块中。
- `WorkbenchService` 保留原有界面调用方式，内部组合科研配置、蜂群控制、脑控执行、
  管理操作和导出服务。
- 科研、作战、管理员和工程画布页面都是独立 `QWidget`，通过 Qt Signal/Slot
  把语义动作交给 `MainActionRouter`，不再让 `MainView` 继承点击路由 mixin。
- 科研页入口只负责导航分发；硬件采集/CSV、独立 LSL 收集以及范式/算法/控制/映射/数据
  渲染分别位于独立模块，避免继续扩张单个 `research_view.py`。
- `MainView` 只负责页面装配、共享 EEG worker 生命周期和顶层事件转发。直接硬件采集、
  CSV 回放、独立 LSL 收集、士兵脑控安全链路和自动实验分别由
  `EEGAcquisitionController`、`CSVReplayController`、`LSLCollectionController`、
  `SoldierBCIController`、`ExperimentController`、`RecognitionController` 和
  `ManualFlightController` 管理，不再在视图中维护重复状态。
- `EEGWorker` 专注读流、窗口化和调度；连续 EEG 的缓存/10 秒分块由
  `ContinuousEEGRecorder` 处理，刺激时间戳对齐、标注试次解码与保存由
  `LabeledTrialRecorder` 处理。
- `ExperimentRecorder` 保留实验记录门面；Marker、连续 EEG 索引和飞行命令审计分别由
  独立 repository 负责。
- 实验记录字段与数据清洗规则位于独立 schema 模块；两个 SSVEP 窗口共用目标几何和
  注视点绘制，避免实验模式与在线控制模式产生布局偏差。

## 运行

推荐 Python 3.12：

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install -r requirements.txt
```

环境变量示例见 [.env.example](.env.example)。该文件是配置说明，不会被程序自动加载；
启动前请在终端、IDE Run Configuration 或部署环境中设置所需变量。

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

### Crazyflie + Flow Deck 多机遥测、群控与逐机控制

第一次只启用只读遥测，不设置 `BDS_HARDWARE_CONTROL_ENABLED`：

```bash
export BDS_SWARM_BACKEND=flowdeck
export BDS_CRAZYFLIE_URIS='radio://0/80/2M/E7E7E7E701,radio://0/80/2M/E7E7E7E702'
python main.py
```

`BDS_CRAZYFLIE_URIS` 使用英文逗号分隔多架 Crazyflie。也可以在“单兵面板 → 脑控
→ 配置 URI”中每行填写一个 URI。程序会同时连接各机并分别显示位置、电量、速度、
高度和 Flow Deck 状态。

现场完成净空、急停、每架飞机的 Flow Deck 测距/光流和电量检查后，才可打开实机控制：

```bash
export BDS_HARDWARE_CONTROL_ENABLED=true
python main.py
```

Crazyradio 连接必须使用无人机实际配置的 Radio URI，例如
`radio://0/80/2M/E7E7E7E701`；`usb://...` 不是 Crazyradio 无线链路 URI。修改 URI 前
需要先断开当前连接。

打开“单兵面板 → 脑控”后的基本流程：

1. 点击“连接”，等待界面显示 Flow 定位就绪；装有 Lighthouse Deck 时还会显示已就绪的飞机数。
2. 在“启动目标高度”处用 `-` / `+` 调整，或点击数值直接输入高度。
3. 将所有无人机水平放置在有纹理、光照正常的地面；配置多架时点击“启动蜂群”，
   配置单架时点击“启动 Flow Hover”。
4. 单机达到设定高度且界面显示 Lighthouse 就绪后，“Lighthouse保持”按钮才会开放；
   点击后以切换瞬间的实时位置作为绝对位置目标，不会自动切换。
5. 单机进入 Lighthouse 位置保持后，左杆上下改变全局目标高度，右杆按照机头方向控制
   前后左右；松杆时保持最后的全局目标位置。
6. 连接 3 架飞机后，可点击“直线编队”或“三角编队”。软件以三机当前位置中心生成
   0.4m 间距槽位，用最小总移动距离分配槽位，并依次移动各机以降低交叉航迹风险。
7. 编队完成后，左杆控制编队整体升降，右杆控制编队整体前后左右；每架飞机的槽位偏移
   保持不变。未形成编队时仍使用原有 Flow Hover 摇杆语义。
8. “单机控制”可选择一架飞机，单独执行起飞、移动、悬停或受控降落。
9. 主界面的“受控降落”作用于全部已起飞飞机；“停止电机”和“紧急停止”只用于异常情况。

Flow Deck 的位置是各机自己的局部坐标；只有 `lighthouse.status=2` 且
`lighthouse.bsActive` 至少包含一个有效基站时，软件才允许切换到共享全局坐标和自动编队。
Lighthouse 几何、标定和基站频道仍须先在官方 Crazyflie Client 的 Lighthouse 页面完成。

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
| Lighthouse就绪 | `lighthouse.status` + `lighthouse.bsActive` | `status=2` 表示有效基站数据已送入状态估计器；`bsActive` 的置位数是当前有效基站数量。 |

速度当前直接显示飞控估计值，没有进行界面滤波。无人机贴地、被手拿起、明显倾斜、地面
纹理不足或状态估计器刚初始化时，速度可能瞬间偏大；这不等于无人机真的以该速度移动。
判断飞行状态时应同时观察机体实际运动、Flow 质量和高度，不能只看单个速度数值。

#### 低电压、降落和停止逻辑

- 电压与换算电量只用于界面显示和低电压告警，不再阻止任务规划、解锁或起飞。
- 飞行中电压连续约 1 秒低于配置告警值时，界面仍会提示低电压，但不锁定控制或禁止再次起飞。
- “受控降落”接管当前正在运行的 Flow Hover 控制流，根据当前目标高度下降，落地后停止电机并解除普通解锁状态，可以在电量正常时再次启动。
- “停止电机”会立即停止当前控制流，属于可恢复的异常处置；确认安全后可以重新启动操控。
- “紧急停止”是锁存式安全动作，不能当作普通降落使用；触发后需要给无人机断电重启，再在软件中复位并重新连接。

### 训练并启用 Transformer-SSVEP

完整通道表、训练命令、安全门和实验限制见
[Transformer-SSVEP复现说明](docs/TRANSFORMER_SSVEP_REPRODUCTION.md)。

```bash
python -m pip install -r requirements-ml.txt
python -m src.ml.train_transformer_ssvep \
  --index data/sessions/raw_trials_index.csv \
  --split-mode session
```

训练输出 `models/transformer_ssvep.pt` 和
`models/transformer_ssvep_metadata.json`。随后可在科研“算法”页加载，或使用
`BDS_INTENT_DECODER=transformer python main.py` 显式启用。

### 训练并启用旧版 FB-EEGNet

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
| EEG 脑电 | ExpandableEEG 原生串口（macOS 自动直连 RFCOMM）、LSL 实时流或 CSV 回放 | [ExpandableEEGSerialStream](src/adapters/real/expandable_eeg_serial_stream.py)、[LSLEEGStream](src/adapters/real/lsl_eeg_stream.py) |
| 无人机遥测/命令 | 默认禁用；不生成虚假飞机 | [CrazyflieSwarmGateway](src/adapters/real/crazyflie_swarm_gateway.py) |
| CPU/内存 | 本机实时指标 | [RealSystemMonitor](src/adapters/real/real_system_monitor.py) |
| 工程模块/日志 | 运行时适配器缓存 | [RuntimeEngineerDataProvider](src/adapters/real/runtime_engineer_data.py) |

统一接口定义位于 `src/ports/`；详细接入说明见 [docs/INTERFACE_HOOKS.md](docs/INTERFACE_HOOKS.md)。

## 项目结构

```text
brain-drone-swarm-console/
├── main.py                       # 桌面端启动入口
├── requirements.txt              # 基础运行依赖
├── requirements-ml.txt           # Transformer/FB-EEGNet 训练与推理依赖
├── .env.example                  # 环境变量模板
├── src/
│   ├── app.py                    # 登录页与主工作台切换
│   ├── container.py              # 真实/Mock 实现装配入口
│   ├── domain/                   # 与 Qt、硬件无关的数据模型
│   ├── ports/                    # EEG、蜂群、认证、监控等稳定接口
│   ├── controllers/              # 页面动作、采集、回放、脑控、识别、摇杆与实验工作流
│   ├── bci/                      # SSVEP 配置、FBCCA、Transformer适配与安全门
│   ├── ml/                       # FB-EEGNet 网络、预处理、训练和推理
│   ├── adapters/
│   │   ├── mock/                 # 自动化测试和显式离线演示
│   │   ├── http/                 # Spring Boot JWT 认证客户端
│   │   └── real/                 # ExpandableEEG原生协议、CSV、LSL、Crazyflie、运行时监控
│   ├── services/                 # 业务用例和拆分后的工作台子服务
│   ├── workers/                  # EEG 后台采集、窗口化和记录
│   ├── views/                    # 独立角色 QWidget、登录和刺激窗口
│   ├── widgets/                  # 可复用 QPainter 绘制组件
│   └── resources/                # QSS 等界面资源
├── tests/                        # 单元测试和离屏 Qt 交互测试
├── data/                         # EEG 试次、连续数据、会话和日志
├── docs/                         # 架构、设备接入和科研路线文档
├── scripts/                      # 辅助脚本，例如界面预览截图
└── artifacts/                    # 自动生成的界面预览，不参与运行
```

### Crazyflie 模块

```text
src/adapters/real/
├── crazyflie_swarm_gateway.py    # 应用侧状态机和命令执行编排
├── crazyflie_contracts.py        # 配置和底层驱动协议
├── crazyflie_command_queue.py    # 串行队列与代际取消
├── crazyflie_telemetry.py        # cflib 日志到领域遥测的纯转换
├── crazyflie_safety.py           # 飞行参数安全校验
├── crazyflie_flow_control.py     # Flow/Lighthouse 连续控制循环
└── crazyflie_cflib_backend.py    # 无线连接、Deck 检测和固件命令
```

### Workbench 模块

```text
src/services/
├── workbench_service.py           # 保持旧 API 的组合门面
├── research_workbench_service.py  # 范式、算法、控制器和映射
├── swarm_control_service.py       # 蜂群命令、回执、审计和急停
├── bci_execution_service.py       # 实时源限制、二次确认和冷却
├── admin_operations_service.py    # 管理页面操作状态
└── export_service.py              # 工作台配置和报告导出
```

运行时实验数据默认写入 `data/`。原始 EEG、识别日志、模型权重和预览图片已在
`.gitignore` 中排除，不应当作普通源码提交；重要实验数据需要另行备份。

## 测试

```bash
QT_QPA_PLATFORM=offscreen .venv312/bin/python -m unittest discover -s tests -v
```

当前自动化测试覆盖 Spring REST 登录客户端、ExpandableEEG原生串口与配置协议、CSV/LSL、OpenBCI 控制面板状态、六频 FBCCA、Transformer安全门、
刺激测频、信号质量、实验记录、Flow/Lighthouse 安全门控、编队、独立角色 QWidget、
工作台服务组合和界面交互。详细运行方法见
[EEG/FBCCA 接入说明](docs/EEG_FBCCA_INTEGRATION.md)。

可选静态检查：

```bash
ruff check src tests
python -m compileall -q src tests
```

## 安全说明

FBCCA 已具备通往 `SwarmGateway` 的受控路径，但默认关闭，CSV 回放永远不能开启实机执行。桌面端的安全门控不能替代飞控固件、物理急停、现场监护和实验室审批。
