# 真实功能与接口接入索引

当前项目已经进入真实接口逐步联调阶段。默认容器不生成 Mock EEG、无人机、
工程指标或设备日志；未配置的能力会明确显示“未接入”。

在 PyCharm 中全局搜索 `【真实接口接入点】`，可以定位到所有预留位置。

## 总体数据流

```text
DeepBCI / 智能头盔 / Crazyflie
              ↓ 设备驱动
          LSL 数据总线
              ↓
预处理 → 解码算法 → 指令映射 → 安全仲裁 → 无人机网关
              ↓                         ↓
       NPZ + CSV/JSON 索引         遥测与执行回执
```

界面只负责展示、收集用户操作和调用 Service。设备读取、算法推理、网络请求、
XDF 文件读写必须放在 `QThread`、线程池或独立进程中，不能写进 `paintEvent()`。

## 界面与接口对应关系

| 界面 | 当前状态 | 真实接入内容 | 代码位置 |
| --- | --- | --- | --- |
| UI-01 登录 | Spring Boot + PostgreSQL 注册/登录；BCrypt 哈希；短期 JWT；Mock 可切换 | 补刷新令牌、管理员授权、登录限流、审计和密码重置邮件 | `LoginView`、`HttpAuthGateway`、`brain-drone-swarm-server` |
| UI-02 作战地图 | Crazyflie 实时缓存；目标接口未接入 | 目标识别、避障、蜂群任务 | `MainView._soldier_combat`、`SwarmGateway` |
| UI-03 脑控操作 | 连接/解锁/起降/移动/单机控制/队形/急停和受控脑控路径 | 实验室实机验收 | `MainView._soldier_brain`、`WorkbenchService`、`CrazyflieSwarmGateway` |
| UI-04 士兵状态 | 固定指标 | EEG/EMG/心率/体温/血氧 LSL 流与告警 | `MainView._soldier_status` |
| UI-05 实验台 | CSV/LSL、连续录制、刺激同步4秒试次、质量检测和识别日志 | 实验元数据API | `MainView._research_eeg`、`EEGWorker`、`ExperimentRecorder` |
| UI-06 范式配置 | 原生六频全屏刺激、快捷键、Marker、实测频率和频谱保存 | 多显示器/光电二极管现场校验 | `SSVEPStimulusWindow`、`StimulusSessionRecorder` |
| UI-07 算法配置 | 纯FBCCA和可选FB-EEGNet训练/验证/推理 | 真实数据评估、模型审批和版本注册 | `src/bci/`、`src/ml/` |
| UI-08 控制器配置 | 滑块、应用、保存和重置可操作 | PID/MPC 参数校验、应用、版本化保存 | `MainView._research_controller`、`WorkbenchService` |
| UI-09 指令映射 | 六频映射可增删改并进入受控动作路径；队形/验收按钮已接网关 | 映射服务端持久化和版本审批 | `MainView._research_mapping`、`WorkbenchService` |
| UI-10 数据工坊 | 会话、Marker、原始试次、连续分块和CSV/JSON索引 | 实验元数据PostgreSQL API | `MainView._research_data`、`ExperimentRecorder` |
| UI-11 工程监控 | 本机资源、适配器状态和 Crazyflie 日志 | GPU/链路吞吐量采集器 | `EngineerDashboard`、`RuntimeEngineerDataProvider`、`RealSystemMonitor` |
| UI-12 管理后台 | 本地清理/备份状态可操作 | 设备、用户/RBAC、存储、备份服务 | `MainView._paint_admin`、`WorkbenchService` |
| UI-13 系统诊断 | Qt 进度条、自检和算法侧50Hz质量指标 | 设备链路丢包/延迟和完整诊断服务 | `EngineerDashboard`、`SystemMonitor`、`assess_signal_quality` |

## 已存在的稳定接口

- `src/ports/auth.py`：登录认证。
- `src/ports/eeg.py`：EEG 数据帧。
- `src/ports/swarm.py`：无人机遥测、指令和急停。
- `src/ports/monitoring.py`：系统资源快照。
- `src/ports/engineering.py`：工程页数据流、模块记录和日志接口。
- `src/container.py`：默认装配真实/禁用适配器；Mock 仅供显式测试。

正式实现建议放到 `src/adapters/real/`，不要覆盖 `adapters/mock/`：

```text
src/adapters/real/
├── csv_eeg_stream.py
├── lsl_eeg_stream.py
├── crazyflie_swarm_gateway.py
├── real_system_monitor.py
└── runtime_engineer_data.py
```

这些类已经由 `create_default_container()` 装配。Crazyflie 默认禁用，只有明确
设置环境变量时才建立无线电连接。

## 当前真实链路

EEG 已按以下边界接入：

1. DeepBCI 驱动把 8 通道、250 Hz 数据推到 LSL。
2. `LSLEEGStream.connect/read_frame/disconnect` 已实现。
3. `src/workers/eeg_worker.py` 已在 `QThread` 中读取并通过 Signal 发送 `EEGFrame`。
4. UI-05 使用最新帧绘制波形并保存 NPZ/CSV 记录。
5. FBCCA 结果默认只显示；实时 LSL 下可由操作员显式开启脑控执行。
6. Crazyflie 网关再次执行电量、定位、围栏、机距、解锁和急停检查。

> 安全说明：真实无人机接入后，急停、返航、悬停和链路中断保护必须在
> 网关/飞控侧生效，不能依赖 GUI 是否正常运行。
