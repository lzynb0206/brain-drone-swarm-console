# Crazyflie + Flow Deck + Lighthouse 多机接入与验收

## 当前控制边界

项目支持多架 Crazyflie 同时连接、Flow 起飞、Lighthouse 全局位置保持、三机自动编队、
编队整体移动和指定单机动作：

- `src/adapters/real/crazyflie_swarm_gateway.py`：Crazyradio 连接、Flow/Lighthouse Deck 检测、两种设定值调度、遥测和安全停止。
- `src/services/formation_service.py`：直线/三角槽位生成和最小总距离槽位分配。
- `src/services/workbench_service.py`：连接、启动操控、人工动作和脑控二次确认。
- `src/views/main_view.py`：虚拟双摇杆及状态显示；Qt 主线程不直接等待无线电。

底层使用一个固定频率的蜂群调度循环。起飞阶段发送 Flow Hover 设定值；高度遥测达到目标
且 Lighthouse 就绪后，同一个调度器切换为绝对 `x/y/z/yaw` 位置设定值，不会同时发送两种
控制流。项目尚未实现障碍物避让和场地围栏。

## 运行配置

先在无桨状态确认 URI、无线电连接、Flow Deck 和遥测：

```bash
export BDS_SWARM_BACKEND=flowdeck
export BDS_CRAZYFLIE_URIS='radio://0/80/2M/E7E7E7E701,radio://0/80/2M/E7E7E7E702'
python main.py
```

多个 URI 使用英文逗号分隔；界面配置窗口则每行填写一个。此时不设置
`BDS_HARDWARE_CONTROL_ENABLED`，可以同时连接多架并查看各机遥测，但不能启动电机。

完成无桨多机连接验收并确认净空、机体、电池、Flow Deck、急停和操作人员后，再打开
实机控制：

```bash
export BDS_HARDWARE_CONTROL_ENABLED=true
python main.py
```

## 控制方式

程序读取官方固件参数 `deck.bcFlow` / `deck.bcFlow2`、`deck.bcLighthouse4`、
`deck.bcLoco` 和 `deck.bcAI`。当前起飞仍要求 Flow Deck；Lighthouse 和 UWB 只在实际安装时
显示，AI Deck 目前不处理摄像头视频。

“启动 Flow Hover”后使用 cflib `send_hover_setpoint(vx, vy, yawrate, zdistance)` 持续发送控制量：

- 左杆：逐步改变离地目标高度，不直接控制电机油门。
- 右杆：控制机体系前后、左右速度。
- 松杆：水平速度归零，保持最后目标高度。
- 启动：以受限速度渐升到默认 0.4m。
- 受控降落：逐步降低目标高度，落地后停电机。

配置多架时，界面显示“启动蜂群”，上述控制量由同一个调度周期发送给所有活动飞机；
“单机控制”窗口可以选择一架飞机，单独起飞、移动、悬停或降落，其余已起飞飞机继续
接收零水平速度的悬停设定值。

软件读取 `lighthouse.status` 和 `lighthouse.bsActive`。只有状态为 2 且至少一个基站的标定、
几何和扫频数据正在进入状态估计器时，才允许绝对位置保持和自动编队。三架飞机必须写入
同一套基站几何，否则它们的坐标不能用于同一编队。

三机编队流程：

1. “启动蜂群”使用 Flow 同时渐升至目标高度。
2. 全部飞机达到目标高度且 Lighthouse 遥测就绪后，由操作员点击“Lighthouse保持”，
   以切换瞬间的位置锁住各自当前全局位置。
3. “直线编队”或“三角编队”以当前三机几何中心生成 0.4m 间距槽位。
4. 匈牙利分配选择总移动距离最小的“飞机—槽位”对应关系，再依次移动每架飞机。
5. 编队完成后，双摇杆移动的是整个槽位集合，飞机间相对偏移保持不变。

Flow V1/V2 的程序目标高度上限分别为 1.0m/2.0m；运行期间检查 `range.zrange`、`motion.squal`、电压、遥测超时和无线电事件。

## 建议验收顺序

1. 先用官方 Crazyflie Client 完成 Lighthouse 基站频道、标定和几何配置，并把同一套配置写入每架飞机。
2. 所有飞机拆桨，同时配置多条 URI 并连接，确认界面出现正确的飞机数量。
3. 分别确认每架飞机的 URI、Flow Deck/Lighthouse Deck、电压、位置、高度和有效基站数。
4. 完成无桨 LED、电机停止和急停逻辑检查。
5. 先只配置一架，在防护环境验证 Flow 到达目标高度，再手动点击“Lighthouse保持”，
   确认状态变为“Lighthouse 位置保持”后小幅移动和降落。
6. 三架飞机拆桨，检查三条 URI、三套遥测和三个唯一槽位编号。
7. 三架安装桨叶时初始间距至少大于编队间距，先只验证同时起飞、手动切换和同时降落。
8. 再依次验证直线编队、整体小幅移动、三角编队；一次只增加一个动作。
9. 验证基站遮挡、无线电失联、定位丢失、电机停止和锁存急停。
10. 完成离线识别准确率/误触发统计后，才测试实时 LSL 脑控。

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
