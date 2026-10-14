# Python上位机架构与React原型迁移说明

## 1. 当前React文件夹的实际状态

原型来源：用户提供的前端工程目录。

该目录是React + TypeScript + Vite制作的纯界面原型，不是可连接实验设备的完整系统：

- 原 React 登录页没有真实认证；当前 Python 版已接入本机 Spring Boot + PostgreSQL 注册登录，并保留可切换的离线 Mock。
- EEG、CPU、延迟和士兵状态来自固定值、三角函数或`Math.random()`。
- 无人机位置、任务、电量和告警全部写死在组件中。
- 大部分“应用配置”“加载算法”“紧急停止”“备份”等按钮没有事件处理。
- 项目声明了Gemini依赖和环境变量，但业务页面没有实际调用Gemini API。

当前迁移以 React 原型为唯一视觉标准：使用 PySide6 原生控件和 `QPainter` 按 1280×720 参考坐标逐页翻译，不嵌入网页，也不重新设计界面。窗口变化时整体等比映射到可用区域。

## 2. 分层原则

```text
Qt Views（界面）
       ↓
Services（登录、实验、控制等用例）
       ↓
Ports（稳定接口协议）
       ↓
Adapters（CSV / LSL / Crazyflie / Spring HTTP；Mock 仅测试）
       ↓
设备、算法进程、文件与数据库
```

硬性约束：

1. `views/`不直接导入`pylsl`、`cflib`、串口、蓝牙或数据库包。
2. 正常启动不使用 Mock 数据；`adapters/mock/` 仅供自动化测试或显式离线界面演示。
3. 接入真实设备时新增Adapter，不修改界面数据结构。
4. 实时采集必须在工作线程中运行，通过Qt Signal把数据送回主线程。
5. EEG连续数据不通过普通HTTP轮询；优先使用LSL或本机流式连接。

## 3. 当前Python结构

```text
src/
├── main.py                  # QApplication启动
├── app.py                   # 主窗口、页面路由
├── container.py             # 统一选择Mock或真实实现
├── domain/
│   └── models.py            # User、EEGFrame、UAVTelemetry等数据模型
├── ports/
│   ├── auth.py              # 登录认证接口
│   ├── eeg.py               # EEG数据流接口
│   ├── swarm.py             # 无人机状态和控制接口
│   └── monitoring.py        # 系统性能监控接口
├── adapters/
│   ├── real/                # CSV、LSL、Crazyflie/Flow Deck、系统监控
│   ├── http/                # Spring Boot JWT 认证
│   └── mock/                # 仅测试/显式演示
├── bci/                     # 六频配置、质量检测、FBCCA、混合解码
├── ml/                      # FB-EEGNet 训练、评估和可选推理
├── workers/                 # EEG 后台采集、同步试次和连续录制
├── controllers/             # 采集、回放、LSL、士兵脑控与实验工作流
├── services/                # 业务用例、实验记录和脑控安全
├── views/                   # PySide6页面
├── widgets/                 # 可复用面板、仪表、图表组件
└── resources/               # QSS、图片、字体等资源
```

### 核心模块拆分

- `adapters/real/crazyflie_swarm_gateway.py` 只保留应用侧蜂群状态机；配置与协议、
  串行命令队列、飞行动作执行、遥测转换、安全参数校验、事件日志、编队状态转换、
  Flow/Lighthouse 控制循环和 cflib 连接
  分别位于同目录的 `crazyflie_contracts.py`、`crazyflie_command_queue.py`、
  `crazyflie_command_executor.py`、`crazyflie_telemetry.py`、`crazyflie_safety.py`、
  `crazyflie_event_journal.py`、
  `crazyflie_formation_state.py`、`crazyflie_flow_control.py` 和 `crazyflie_cflib_backend.py`。
- `WorkbenchService` 是应用门面；科研配置、蜂群控制、脑控执行门控、管理员操作和
  JSON 导出由独立服务实现。
- `MainView` 负责页面装配、共享 EEG worker 生命周期和顶层事件转发。直接采集、CSV 回放、
  独立 LSL 收集、士兵脑控安全链路和自动实验分别由 `controllers/` 下的
  `eeg_acquisition_controller.py`、`csv_replay_controller.py`、
  `lsl_collection_controller.py`、`soldier_bci_controller.py` 和
  `experiment_controller.py` 管理。科研、作战、管理员和工程画布页面是
  `role_pages.py` 中的独立 `QWidget`，通过 Qt Signal 把语义动作交给主界面路由。
- 科研页面入口 `research_view.py` 只保留导航分发；采集/回放、LSL 收集和科研配置渲染
  分别在 `research_acquisition_view.py`、`research_collection_view.py` 与
  `research_configuration_view.py` 中实现。
- `EEGWorker` 保留读流、时间对齐、窗口化和解码调度；连续 EEG 的缓存、固定时长分块与
  落盘由 `workers/continuous_eeg_recorder.py` 管理。

## 4. 各页面应该连接的接口

| React原型页面 | Python页面目标 | 当前数据 | 后续接口/实现 |
| --- | --- | --- | --- |
| 登录 | UI-01 | Spring REST API、BCrypt、JWT；离线 Mock 可切换 | `AuthGateway`、`HttpAuthGateway`；后续补刷新令牌、审计与完整 RBAC |
| 作战地图 | UI-02 | Crazyflie真实遥测；目标接口仍未接入 | `CrazyflieSwarmGateway`；后续视觉/避障接口 |
| 脑控操作 | UI-03 | 实时解码、人工控制、安全门控、执行回执 | `EEGWorker` + `WorkbenchService` + `SwarmGateway` |
| 士兵状态 | UI-04 | 随机曲线 | 生理状态流；后续LSL多模态数据 |
| EEG实验台 | UI-05 | CSV/LSL真实波形、试次和连续录制 | `EEGStream`、`ExperimentRecorder` |
| 范式配置 | UI-06 | 原生六频全屏刺激、事件标记、实测频率和频谱 | `SSVEPStimulusWindow`、`StimulusSessionRecorder` |
| 算法管理 | UI-07 | FBCCA；可选FB-EEGNet训练/验证/推理 | `FBCCADecoder`、`HybridIntentDecoder`、`src/ml/` |
| 控制器配置 | UI-08 | 无实际应用 | `ControllerRegistry`；后续PID/MPC实现 |
| 指令映射 | UI-09 | 六频映射可增删改并供脑控安全路径读取 | 后续增加服务端版本化持久化 |
| 数据工坊 | UI-10 | NPZ原始试次/连续数据与CSV/JSON索引 | 后续实验元数据PostgreSQL API |
| 开发者监控 | UI-11 | 本机性能与运行时适配器状态 | `SystemMonitor` + 运行时数据提供器 |
| 运维后台 | UI-12 | 固定设备/用户 | `DeviceRegistry`、`UserRepository`、`BackupService` |
| 系统诊断 | UI-13 | 随机频谱 | `SystemMonitor`、链路诊断和自检服务 |

尚未创建的Port应在对应页面开始实现时再添加，避免一次性设计大量用不到的接口。

## 5. API与设备连接边界

- **LSL**：脑电、事件标记、同步生理信号等高频时序数据。
- **Crazyflie/cflib**：本机直接控制无人机和读取遥测。
- **串口/BLE**：DeepBCI或其他设备没有LSL驱动时，由独立Adapter读取，再转换为`EEGFrame`。
- **WebSocket**：算法或设备服务运行在独立进程/计算机时，用于实时状态和指令推送。
- **HTTP API**：适合登录、用户管理、实验配置、任务记录等低频请求，不用于250Hz EEG波形。
- **SQLite/XDF/NPZ**：本机实验记录、原始时序数据和训练集导出。

## 6. 当前迁移状态

1. **已完成**：UI-01 至 UI-13 的前端翻译、角色路由和主要科研交互。
2. **已完成**：Spring认证、CSV/LSL、六频刺激、FBCCA、记录、单架 Crazyflie/Flow Deck 适配和安全命令队列。
3. **需要现场验收**：Crazyradio、Flow Deck、Flow Hover 和硬件急停；代码存在不等于已经通过实机测试。
4. **仍待外部协议**：视觉/避障、多模态生命体征、实验元数据API、生产RBAC与不可篡改审计。
