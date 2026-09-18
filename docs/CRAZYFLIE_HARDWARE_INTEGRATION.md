# Crazyflie + Flow Deck 实机接入与验收

## 当前控制边界

项目只保留单架 Crazyflie + Flow Deck 控制链路：

- `src/adapters/real/crazyflie_swarm_gateway.py`：Crazyradio 连接、Flow Deck/AI Deck 检测、Flow Hover、遥测和安全停止。
- `src/services/workbench_service.py`：连接、启动操控、人工动作和脑控二次确认。
- `src/views/main_view.py`：虚拟双摇杆及状态显示；Qt 主线程不直接等待无线电。

已删除基站定位、绝对坐标、场地围栏、多机队形和逐架高度补偿代码。程序要求且只接受一个 URI。

## 运行配置

先在无桨状态确认 URI、无线电连接、Flow Deck 和遥测：

```bash
export BDS_SWARM_BACKEND=flowdeck
export BDS_CRAZYFLIE_URIS='实际单机URI'
python main.py
```

此时不设置 `BDS_HARDWARE_CONTROL_ENABLED`，只能连接并查看遥测，不能启动电机。

现场确认净空、机体、电池、Flow Deck、急停和操作人员后，再打开实机控制：

```bash
export BDS_HARDWARE_CONTROL_ENABLED=true
python main.py
```

## 控制方式

程序读取官方固件参数 `deck.bcFlow` / `deck.bcFlow2`，缺少 Flow Deck 会拒绝操控；同时读取 `deck.bcAI`，但目前只显示 AI Deck 已检测，不处理摄像头视频。

“启动 Flow Hover”后使用 cflib `send_hover_setpoint(vx, vy, yawrate, zdistance)` 持续发送控制量：

- 左杆：逐步改变离地目标高度，不直接控制电机油门。
- 右杆：控制机体系前后、左右速度。
- 松杆：水平速度归零，保持最后目标高度。
- 启动：以受限速度渐升到默认 0.4m。
- 受控降落：逐步降低目标高度，落地后停电机。

Flow V1/V2 的程序目标高度上限分别为 1.0m/2.0m；运行期间检查 `range.zrange`、`motion.squal`、电压、遥测超时和无线电事件。

## 建议验收顺序

1. 无桨连接，确认 URI、Flow Deck 版本、电压和 AI Deck 检测结果。
2. 无桨 LED 测试和电机停止/急停逻辑检查。
3. 安装桨叶后在防护环境进行低高度启动、松杆悬停和受控降落。
4. 小幅测试前后、左右与高度变化。
5. 验证无线电失联、Flow 质量丢失、电机停止和锁存急停。
6. 完成离线识别准确率/误触发统计后，才测试实时 LSL 脑控。

## 脑控执行门槛

- 数据源必须是实时 LSL，CSV 回放不能驱动实机。
- FBCCA 结果通过阈值，同一意图连续确认两次，并满足 2.5 秒冷却。
- Crazyflie 已连接，Flow Deck 质量正常，操作员已启动实机操控。
- 当前六频动作统一为：起飞、前进、后退、左转、右转、降落。

## 停止与急停

- “受控降落”：正常结束飞行。
- “停止电机”：立即关闭电机，空中使用会直接下落，界面会要求二次确认。
- “紧急停止”：触发飞控锁存急停；必须给 Crazyflie 断电重启，再在软件中复位并重连。

软件门控不能替代防护网、现场监护、独立急停和实验室审批。
