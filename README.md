# 脑控无人机蜂群作战系统

> `brain-drone-swarm-console` · 基于 Python + PySide6 的脑控无人机蜂群科研上位机。

## 当前状态

本仓库处于**真实接口逐步联调阶段**。账号系统连接 Spring Boot/PostgreSQL；科研界面可回放真实 CSV 或读取 LSL、运行纯 FBCCA 并保存实验记录；Crazyflie/Lighthouse 软件适配层和安全命令队列已经加入，但尚未在本仓库所在电脑上完成实验室实机验收。

已经可运行的内容：

- Spring Boot + PostgreSQL 邮箱注册和登录，密码使用 BCrypt 哈希并返回 JWT；也可切换为离线 Mock 模式。
- 士兵、科研人员、系统工程师、系统管理员等角色页面。
- 范式、算法、控制器和 L1 指令映射的本地交互状态。
- 工程师工作台：真实 Qt 左侧导航、运行时接口状态、真实本机性能和设备日志。
- Crazyflie 蜂群连接、只读遥测、解锁/锁定、起降、前后左右、悬停和硬件急停调用。
- 飞行安全门控：实时遥测、最低电压、Lighthouse 状态、场地围栏、最小机距和串行命令队列。
- 脑控执行默认关闭；只允许实时 LSL，且同一意图连续确认两次并经过冷却后提交。
- `start-1.csv` 8 通道回放、真实 `LSLEEGStream` 和后台实时波形。
- 纯 FBCCA：暂用传承单机五频配置，仅显示识别结果；最终频率待老师确认。
- 识别日志，以及包含被试、会话、真实标签、频率、质量和门控结果的 4 秒原始 EEG `.npz` 试次。

尚待现场或外部团队提供的能力：

- Crazyflie URI、实验室实测场地围栏、固件参数和实机安全验收。
- 目标识别/跟踪、生命体征和避障服务的协议、地址及数据字典。
- 实验元数据 PostgreSQL 接口、XDF 长时间记录及正式训练/验证数据集划分。
- FB-EEGNet（当前明确 `FB_EEGNET_ENABLED = False`）。
- 生产级 RBAC、审计、服务端身份授权和真实设备安全仲裁。

科研页默认回放本地 `data/samples/start-1.csv`。文件不存在时会明确报告数据源不可用，不再生成 Mock EEG。蜂群后端默认禁用并显示“未连接”，不会绘制虚假无人机。程序不依赖 `artifacts/` 中的 PNG 截图。

## 技术架构

```text
CSV / LSL → EEG QThread → 纯 FBCCA → 安全确认 → 可选蜂群命令
PySide6 → HTTP + JWT → Spring Boot → PostgreSQL（账号与身份）
PySide6 → CrazyflieSwarmGateway 后台队列 → cflib/Crazyradio → Crazyflie + Lighthouse
```

真实接入集中在 `src/adapters/real/`，装配入口为 [src/container.py](src/container.py)。界面不会直接等待无线电或执行飞行动作。

## 运行

需要 Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
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
YY_AUTH_BACKEND=mock python main.py
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
export YY_AUTH_BACKEND=http
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
├── adapters/
│   ├── mock/               # 仅供自动化测试和显式离线 UI 测试
│   ├── http/               # Spring Boot JWT 认证客户端
│   └── real/               # CSV、LSL、Crazyflie、运行时监控
├── workers/                # QThread EEG 持续采集与4秒窗口
├── services/               # 登录、意图安全门控、记录与工作台状态
└── views/                  # PySide6 界面
```

## 测试

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

当前包含 Spring REST 登录客户端、CSV/LSL Adapter、纯 FBCCA、实验记录器、工程师交互和工作台测试。详细运行方法见 [EEG/FBCCA 接入说明](docs/EEG_FBCCA_INTEGRATION.md)。

## 安全说明

FBCCA 已具备通往 `SwarmGateway` 的受控路径，但默认关闭，CSV 回放永远不能开启实机执行。桌面端的安全门控不能替代飞控固件、物理急停、现场监护和实验室审批。
