# 脑控无人机蜂群作战系统

> `brain-drone-swarm-console` · 基于 Python + PySide6 的脑控无人机蜂群上位机软件演示原型。

## 当前状态

本仓库当前是**可交互的桌面演示版**，用于完成界面、角色路由、参数配置、交互流程和后续接口分层验证；不是已连接真实无人机与脑电硬件的生产系统。

已经可运行的内容：

- Firebase 邮箱注册、登录和密码重置；离线 Mock 登录模式。
- 士兵、科研人员、系统工程师、系统管理员等角色页面。
- 范式、算法、控制器和 L1 指令映射的本地交互状态。
- 工程师工作台：真实 Qt 左侧导航、模块表格、日志筛选、性能刷新和自检。
- Mock 蜂群指令、急停锁定、报告 JSON 导出、备份与缓存清理等演示流程。

尚未接入的真实能力：

- DeepBCI / LSL 实时 EEG 数据流。
- Crazyflie 无人机的真实遥测、无线电控制和硬件急停回执。
- `.pth` 脑电解码模型的后台推理、XDF 记录、SQLite 元数据和 `.npz` 数据集导出。
- 生产级 RBAC、审计、服务端身份授权和真实设备安全仲裁。

所有演示数据均通过 Mock Adapter 提供；程序运行不依赖 `artifacts/` 中的 PNG 截图。

## 技术架构

```text
PySide6 桌面界面
        ↓
Service 业务逻辑
        ↓
Port 接口定义
        ↓
Mock Adapter（当前） / Real Adapter（后续）
```

真实接入时，仅需实现 `src/adapters/real/` 下的适配器，并在 [src/container.py](src/container.py) 中替换 Mock 装配；页面层无需推倒重写。

## 运行

需要 Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 离线演示模式

默认就是离线演示模式，不需要 Firebase 配置，适合展示各角色界面：

```bash
YY_AUTH_BACKEND=mock python main.py
```

| 用户名包含 | 进入角色 |
| --- | --- |
| `research` / `科研` | 科研人员 |
| `engineer` / `工程` | 系统工程师 |
| `admin` / `管理` | 系统管理员 |
| 其他 | 作战操作员 |

### Firebase 登录模式

1. 在 Firebase Console 的 `Authentication → Sign-in method` 启用 `Email/Password`。
2. 设置 Web API Key：

```bash
export FIREBASE_API_KEY="你的 Firebase Web API Key"
export YY_AUTH_BACKEND=firebase
python main.py
```

也可以复制 `.env.example` 为 `.env` 保存配置；当前代码不会自动读取 `.env`，因此 PyCharm 中请在运行配置的环境变量中设置 `FIREBASE_API_KEY`。

注册时可选择演示身份。身份映射保存在本机 `~/.brain_control_console/firebase_roles.json`，仅用于课题演示；正式部署应使用可信后端或 Firebase Custom Claims 分配权限。

## 演示数据与真实接口位置

| 数据类型 | 当前演示数据 | 后续真实实现位置 |
| --- | --- | --- |
| EEG 脑电 | [MockEEGStream](src/adapters/mock/eeg_stream.py) | `src/adapters/real/lsl_eeg_stream.py` |
| 无人机遥测/命令 | [MockSwarmGateway](src/adapters/mock/swarm_gateway.py) | `src/adapters/real/crazyflie_swarm_gateway.py` |
| CPU/GPU/延迟 | [MockSystemMonitor](src/adapters/mock/system_monitor.py) | `src/adapters/real/real_system_monitor.py` |
| 工程模块/日志 | [MockEngineerDataProvider](src/adapters/mock/engineer_data.py) | `src/adapters/real/real_engineer_data_provider.py` |

统一接口定义位于 `src/ports/`；详细接入说明见 [docs/INTERFACE_HOOKS.md](docs/INTERFACE_HOOKS.md)。

## 项目结构

```text
src/
├── app.py                  # 主窗口和角色路由
├── container.py            # Mock / Firebase / 后续真实接口装配入口
├── domain/                 # 与 Qt、硬件无关的数据模型
├── ports/                  # EEG、蜂群、系统监控、工程数据等接口
├── adapters/
│   ├── mock/               # 当前演示数据实现
│   └── firebase/           # Firebase Auth 实现
├── services/               # 登录、工作台状态、演示身份逻辑
└── views/                  # PySide6 界面
```

## 测试

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

当前包含登录、Firebase REST、工程师交互、工作台动作和 Mock Adapter 测试。

## 安全说明

当前仓库不会连接或操纵真实无人机。将来接入实机时，急停、悬停、返航、链路中断保护与权限校验必须在网关/飞控或可信后端生效，不能仅依赖桌面界面。
