# Python上位机架构与React原型迁移说明

## 1. 当前React文件夹的实际状态

原型来源：用户提供的前端工程目录。

该目录是React + TypeScript + Vite制作的纯界面原型，不是可连接实验设备的完整系统：

- 原 React 登录页没有真实认证；当前 Python 版已接入 Firebase 邮箱注册、登录与密码重置，并保留可切换的离线 Mock。
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
Adapters（Mock / LSL / Crazyflie / SQLite / WebSocket）
       ↓
设备、算法进程、文件与数据库
```

硬性约束：

1. `views/`不直接导入`pylsl`、`cflib`、串口、蓝牙或数据库包。
2. 界面开发阶段统一使用`adapters/mock/`。
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
│   └── mock/                # 当前界面演示所用模拟实现
├── services/                # 业务用例
├── views/                   # PySide6页面
├── widgets/                 # 可复用面板、仪表、图表组件
└── resources/               # QSS、图片、字体等资源
```

## 4. 各页面应该连接的接口

| React原型页面 | Python页面目标 | 当前数据 | 后续接口/实现 |
| --- | --- | --- | --- |
| 登录 | UI-01 | Firebase Auth；离线 Mock 可切换 | `AuthGateway`、`FirebaseAuthGateway`；后续补 Custom Claims/RBAC |
| 作战地图 | UI-02 | 固定无人机/目标 | `SwarmGateway`；后续Crazyflie/cflib或WebSocket |
| 脑控操作 | UI-03 | 固定置信度 | 解码结果流 + `SwarmGateway.send_command()` |
| 士兵状态 | UI-04 | 随机曲线 | 生理状态流；后续LSL多模态数据 |
| EEG实验台 | UI-05 | 随机波形 | `EEGStream`；后续DeepBCI → LSL |
| 范式配置 | UI-06 | 无持久化 | `ParadigmRepository`；后续YAML/JSON |
| 算法管理 | UI-07 | 固定卡片 | `AlgorithmRegistry`；后续Python模型插件 |
| 控制器配置 | UI-08 | 无实际应用 | `ControllerRegistry`；后续PID/MPC实现 |
| 指令映射 | UI-09 | 内存表格可增删改 | `CommandMappingRepository`；后续YAML/SQLite持久化 |
| 数据工坊 | UI-10 | 随机波形/固定会话 | `SessionRepository`；后续XDF、NPZ和SQLite |
| 开发者监控 | UI-11 | 随机性能值 | `SystemMonitor` + LSL流目录 |
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

1. **已完成**：UI-01 至 UI-13 的静态前端翻译和角色路由。
2. **已完成**：科研顶部导航、工程监控/诊断切换、作战地图/单兵面板切换、脑控/状态监控切换。
3. **待后续明确**：将原型中的按钮连接到现有 Port 或新增业务接口。
4. **接设备时**：新增 LSL、Crazyflie、串口或 WebSocket Adapter，以真实数据替换当前模拟内容，不重做前端。
