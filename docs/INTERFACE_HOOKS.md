# 真实功能与接口接入索引

当前项目是带业务交互的本地演示。界面、导航、角色路由和主要按钮均可运行；
配置、映射、命令和操作日志保存在内存状态中，部分操作通过 Mock Port 执行。
脑电、无人机、系统状态、用户、持久化配置和数据仍未连接真实设备或服务器。

在 PyCharm 中全局搜索 `【真实接口接入点】`，可以定位到所有预留位置。

## 总体数据流

```text
DeepBCI / 智能头盔 / Crazyflie
              ↓ 设备驱动
          LSL 数据总线
              ↓
预处理 → 解码算法 → 指令映射 → 安全仲裁 → 无人机网关
              ↓                         ↓
       XDF + SQLite                遥测与执行回执
```

界面只负责展示、收集用户操作和调用 Service。设备读取、算法推理、网络请求、
XDF 文件读写必须放在 `QThread`、线程池或独立进程中，不能写进 `paintEvent()`。

## 界面与接口对应关系

| 界面 | 当前状态 | 真实接入内容 | 代码位置 |
| --- | --- | --- | --- |
| UI-01 登录 | Firebase 邮箱注册/登录/重置密码；本机 UID 身份表；Mock 可切换 | 用服务端 Custom Claims/RBAC 替换本机身份表，补 Token 刷新与审计 | `LoginView`、`FirebaseAuthGateway`、`LocalRoleProfileStore` |
| UI-02 作战地图 | 固定 UAV/目标 | Crazyflie 遥测、Lighthouse 位置、目标识别、蜂群任务 | `MainView._soldier_combat`、`SwarmGateway` |
| UI-03 脑控操作 | 方向/悬停/急停已接 Mock 网关 | EEG 解码、指令映射、安全仲裁、急停回执 | `MainView._soldier_brain`、`WorkbenchService`、`SwarmGateway` |
| UI-04 士兵状态 | 固定指标 | EEG/EMG/心率/体温/血氧 LSL 流与告警 | `MainView._soldier_status` |
| UI-05 实验台 | 绘制演示波形 | 8 通道 250 Hz EEG、LSL Marker、UAV 遥测 | `MainView._research_eeg`、`EEGStream` |
| UI-06 范式配置 | 控件可调并可应用/重置 | YAML/JSON 配置、刺激程序、范式 Marker | `MainView._research_paradigm`、`WorkbenchService` |
| UI-07 算法配置 | 选择/加载/默认/测试可操作 | 模型注册、`.pth` 加载、后台推理、A/B 测试 | `MainView._research_algorithm`、`WorkbenchService` |
| UI-08 控制器配置 | 滑块、应用、保存和重置可操作 | PID/MPC 参数校验、应用、版本化保存 | `MainView._research_controller`、`WorkbenchService` |
| UI-09 指令映射 | L1 映射可增删改 | 映射持久化、安全仲裁；L2/L3 暂不实飞 | `MainView._research_mapping`、`WorkbenchService` |
| UI-10 数据工坊 | 页签/会话/Marker 可操作 | XDF 读取、SQLite 索引、标签编辑、`.npz` 导出 | `MainView._research_data`、`WorkbenchService` |
| UI-11 工程监控 | Qt 控件页面；左侧模块、日志筛选、定时指标均可操作 | LSL 流发现、资源指标、真实日志队列 | `EngineerDashboard`、`EngineerDataProvider`、`SystemMonitor` |
| UI-12 管理后台 | 本地清理/备份状态可操作 | 设备、用户/RBAC、存储、备份服务 | `MainView._paint_admin`、`WorkbenchService` |
| UI-13 系统诊断 | Qt 进度条和自检按钮调用监控 Port | 丢包/延迟、50 Hz 干扰、完整诊断服务 | `EngineerDashboard`、`SystemMonitor` |

## 已存在的稳定接口

- `src/ports/auth.py`：登录认证。
- `src/ports/eeg.py`：EEG 数据帧。
- `src/ports/swarm.py`：无人机遥测、指令和急停。
- `src/ports/monitoring.py`：系统资源快照。
- `src/ports/engineering.py`：工程页数据流、模块记录和日志接口。
- `src/container.py`：决定本次运行使用 Mock 还是真实实现。

正式实现建议放到 `src/adapters/real/`，不要覆盖 `adapters/mock/`：

```text
src/adapters/real/
├── sqlite_auth_gateway.py
├── lsl_eeg_stream.py
├── crazyflie_swarm_gateway.py
├── real_system_monitor.py
└── real_engineer_data_provider.py
```

这些类实现与 Mock 相同的 Port，然后在新增的 `create_production_container()`
中替换装配即可。这样切换真实环境时，不需要修改界面的绘制代码。

## 推荐的第一条真实链路

先只接 EEG，不要一次接完所有设备：

1. DeepBCI 驱动把 8 通道、250 Hz 数据推到 LSL。
2. 实现 `LSLEEGStream.connect/read_frame/disconnect`。
3. 在 `QThread` 中读取数据，通过 Signal 发送 `EEGFrame`。
4. UI-05 用最新帧替换演示曲线。
5. 同时写入测试 Marker，并保存一段 XDF。
6. 通过后再接解码算法与无人机 Mock，最后才连接真实 Crazyflie。

> 安全说明：真实无人机接入后，急停、返航、悬停和链路中断保护必须在
> 网关/飞控侧生效，不能依赖 GUI 是否正常运行。
