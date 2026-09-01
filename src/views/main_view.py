"""Native PySide6 translation of every dashboard in the supplied React UI."""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QInputDialog, QLabel, QMessageBox, QWidget

from src.domain import Role, User
from src.ports import EEGStream, EngineerDataProvider, SwarmGateway, SystemMonitor
from src.services import WorkbenchService
from src.views.engineer_dashboard import EngineerDashboard
from src.widgets.scifi_canvas import (
    BG,
    BLUE,
    BORDER,
    CYAN,
    GREEN,
    MUTED,
    PANEL,
    PANEL_DARK,
    RED,
    TEXT,
    YELLOW,
    Canvas,
)


class MainView(QWidget):
    """A 1280×720 interactive canvas matching the reference front-end."""

    logout_requested = Signal()

    def __init__(
        self,
        eeg_stream: EEGStream | None = None,
        swarm_gateway: SwarmGateway | None = None,
        system_monitor: SystemMonitor | None = None,
        engineer_data_provider: EngineerDataProvider | None = None,
    ) -> None:
        super().__init__()
        # 【真实接口接入点】依赖从 app.py/container.py 注入。按钮状态由
        # WorkbenchService 管理；曲线和遥测仍是演示数据。后续实时刷新时从
        # 这些协议读取，不要让 paintEvent 直接访问硬件或执行网络请求。
        self.eeg_stream = eeg_stream
        self.swarm_gateway = swarm_gateway
        self.system_monitor = system_monitor
        self.engineer_data_provider = engineer_data_provider
        self.actions = WorkbenchService(swarm_gateway, system_monitor)
        self.user: User | None = None
        self.research_page = "实验"
        self.engineer_page = "系统监控"
        self.soldier_root = "作战地图"
        self.soldier_page = "脑控"
        self._regions: list[tuple[QRectF, str]] = []
        self._toast = ""
        self._toast_generation = 0
        # Kept as a small compatibility surface for integrations/tests.
        self.user_label = QLabel("", self)
        self.user_label.hide()
        self.engineer_dashboard: EngineerDashboard | None = None
        if system_monitor is not None and engineer_data_provider is not None:
            self.engineer_dashboard = EngineerDashboard(system_monitor, engineer_data_provider, self)
            self.engineer_dashboard.logout_requested.connect(self.logout_requested.emit)
            self.engineer_dashboard.hide()
        self.setMouseTracking(True)

    def set_user(self, user: User) -> None:
        self.user = user
        self.user_label.setText(f"{user.display_name}  ·  {user.role_label}")
        self.research_page = "实验"
        self.engineer_page = "系统监控"
        self.soldier_root = "作战地图"
        self.soldier_page = "脑控"
        if self.engineer_dashboard is not None:
            self.engineer_dashboard.setVisible(user.role == Role.ENGINEER)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        c = Canvas(painter, self.width(), self.height())
        self._regions = []
        role = self.user.role if self.user else Role.SOLDIER
        if role == Role.ENGINEER and self.engineer_dashboard is not None:
            c.fill("#071020")
            return
        if role == Role.ADMIN:
            self._paint_admin(c)
        elif role == Role.RESEARCHER:
            self._paint_research(c)
        elif role == Role.ENGINEER:
            self._paint_engineer(c)
        else:
            self._paint_soldier(c)
        c.logout()
        self._hit(1224, 663, 56, 57, "logout")
        if self._toast:
            c.rect(390, 655, 500, 45, "#15243a", CYAN, 7, 1.5)
            c.text(410, 655, 460, 45, self._toast, 12, TEXT, True, Qt.AlignCenter)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        if self.engineer_dashboard is not None:
            self.engineer_dashboard.setGeometry(self.rect())
        super().resizeEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        x = event.position().x() * 1280 / max(1, self.width())
        y = event.position().y() * 720 / max(1, self.height())
        point = QPointF(x, y)
        for rect, action in reversed(self._regions):
            if rect.contains(point):
                self._activate(action, x, y)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        x = event.position().x() * 1280 / max(1, self.width())
        y = event.position().y() * 720 / max(1, self.height())
        interactive = any(rect.contains(QPointF(x, y)) for rect, _ in self._regions)
        self.setCursor(Qt.PointingHandCursor if interactive else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def _hit(self, x: float, y: float, w: float, h: float, action: str) -> None:
        self._regions.append((QRectF(x, y, w, h), action))

    def _notify(self, message: str) -> None:
        self._toast = message
        self._toast_generation += 1
        generation = self._toast_generation
        QTimer.singleShot(2600, lambda: self._clear_toast(generation))
        self.update()

    def _clear_toast(self, generation: int) -> None:
        if generation == self._toast_generation:
            self._toast = ""
            self.update()

    def _activate(self, action: str, x: float = 0, y: float = 0) -> None:
        """Route every painted control to a stateful application action."""
        if action == "logout":
            self.logout_requested.emit()
        elif action.startswith("research:"):
            self.research_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("engineer:"):
            self.engineer_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("soldier-root:"):
            self.soldier_root = action.split(":", 1)[1]
            self.update()
        elif action.startswith("soldier-page:"):
            self.soldier_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("paradigm-frequency:"):
            self.actions.paradigm_frequency = int(action.rsplit(":", 1)[1])
            self.update()
        elif action.startswith("paradigm-type:"):
            self.actions.paradigm_type = action.split(":", 1)[1]
            self._notify(f"脑控范式已切换：{self.actions.paradigm_type}")
        elif action == "paradigm-size":
            self.actions.stimulus_size = max(0, min(100, round((x - 739) / 299 * 100)))
            self.update()
        elif action == "paradigm-duty":
            self.actions.duty_cycle = max(0, min(100, round((x - 739) / 299 * 100)))
            self.update()
        elif action == "paradigm-apply":
            self._notify(self.actions.apply_paradigm())
        elif action == "paradigm-reset":
            self._notify(self.actions.reset_paradigm())
        elif action.startswith("algorithm-select:"):
            self._notify(self.actions.select_algorithm(action.split(":", 1)[1]))
        elif action.startswith("algorithm-mode:"):
            self.actions.algorithm_mode = action.split(":", 1)[1]
            self._notify(f"算法模式已切换：{self.actions.algorithm_mode}")
        elif action == "algorithm-load":
            self._notify(self.actions.load_algorithm())
        elif action == "algorithm-unload":
            self._notify(self.actions.unload_algorithm())
        elif action == "algorithm-default":
            self._notify(self.actions.set_default_algorithm())
        elif action == "algorithm-benchmark":
            self._notify(self.actions.benchmark_algorithm())
        elif action.startswith("controller-select:"):
            self.actions.controller = action.split(":", 1)[1]
            self._notify(f"控制器已选择：{self.actions.controller}")
        elif action.startswith("controller-slider:"):
            key = action.rsplit(":", 1)[1]
            self.actions.controller_values[key] = max(0, min(100, round((x - 203) / 707 * 100)))
            self.update()
        elif action == "controller-apply":
            self._notify(self.actions.apply_controller())
        elif action == "controller-save":
            self._notify(self.actions.save_controller_preset())
        elif action == "controller-reset":
            self._notify(self.actions.reset_controller())
        elif action == "mapping-add":
            self._add_mapping_dialog()
        elif action.startswith("mapping-edit:"):
            self._edit_mapping_dialog(int(action.rsplit(":", 1)[1]))
        elif action.startswith("mapping-delete:"):
            self._notify(self.actions.delete_mapping(int(action.rsplit(":", 1)[1])))
        elif action.startswith("data-tab:"):
            self.actions.data_tab = action.split(":", 1)[1]
            if self.actions.data_tab == "数据集生成":
                self._notify(self.actions.generate_dataset())
            else:
                self.update()
        elif action.startswith("data-session:"):
            self.actions.selected_session = int(action.rsplit(":", 1)[1])
            self.update()
        elif action == "marker-add":
            self._notify(self.actions.add_marker())
        elif action.startswith("log-filter:"):
            self.actions.log_filter = action.split(":", 1)[1]
            self.update()
        elif action == "self-test":
            self._notify(self.actions.run_self_test())
        elif action == "admin-clean-cache":
            self._notify(self.actions.clean_cache())
        elif action == "admin-backup":
            self._notify(self.actions.create_backup())
        elif action == "admin-restore":
            self._notify(self.actions.restore_backup())
        elif action.startswith("admin-device:"):
            self._show_device_dialog(action.split(":", 1)[1])
        elif action.startswith("admin-user:"):
            self._edit_user_dialog(action.split(":", 1)[1])
        elif action.startswith("swarm-command:"):
            self._notify(self.actions.send_swarm_command(action.split(":", 1)[1]))
        elif action == "swarm-emergency-stop":
            self._notify(self.actions.emergency_stop())
        elif action == "soldier-export":
            path = self.actions.export_soldier_report()
            self._notify(f"报告已导出：{path}")
        elif action == "workspace-export":
            path = self.actions.export_workspace_config()
            self._notify(f"配置已导出：{path}")
        elif action == "workspace-summary":
            self._notify(self.actions.workspace_summary())
        elif action == "workspace-layout":
            self._notify(self.actions.toggle_layout())
        elif action == "workspace-help":
            self._notify("操作提示：选择模块后点击控件；所有结果会在底部提示")

    def _add_mapping_dialog(self) -> None:
        frequency, ok = QInputDialog.getItem(self, "添加映射", "SSVEP频率", ["8Hz", "12Hz", "15Hz", "18Hz"], 3, False)
        if not ok:
            return
        command, ok = QInputDialog.getItem(self, "添加映射", "映射动作", ["左转", "右转", "前进", "后退", "悬停"], 0, False)
        if not ok:
            return
        parameter, ok = QInputDialog.getText(self, "添加映射", "参数", text="15°")
        if ok and parameter.strip():
            self._notify(self.actions.add_mapping(frequency, command, parameter.strip()))

    def _edit_mapping_dialog(self, index: int) -> None:
        entry = self.actions.mappings[index]
        commands = ["左转", "右转", "前进", "后退", "悬停"]
        command, ok = QInputDialog.getItem(
            self, "修改映射", "映射动作", commands,
            commands.index(entry.action) if entry.action in commands else 0, False,
        )
        if not ok:
            return
        parameter, ok = QInputDialog.getText(self, "修改映射", "参数", text=entry.parameter)
        if ok and parameter.strip():
            self._notify(self.actions.update_mapping(index, command, parameter.strip()))

    def _show_device_dialog(self, name: str) -> None:
        details = {
            "DeepBCI采集板": "类型：脑电采集设备\n固件：v2.1\n状态：在线\n采样率：250Hz",
            "Crazyflie-01": "类型：无人机\n固件：v1.3\n状态：在线\n链路：MockSwarmGateway",
            "智能头盔": "类型：多模态头盔\n固件：v1.0\n状态：离线\n最近在线：2026-08-30",
        }
        QMessageBox.information(self, f"设备详情 · {name}", details.get(name, "暂无设备信息"))

    def _edit_user_dialog(self, username: str) -> None:
        roles = ["士兵", "科研人员", "系统工程师", "管理员"]
        current = self.actions.user_roles.get(username, roles[0])
        role, ok = QInputDialog.getItem(
            self, f"编辑用户 · {username}", "角色", roles,
            roles.index(current) if current in roles else 0, False,
        )
        if ok:
            self._notify(self.actions.update_user_role(username, role))

    # ---------- Researcher dashboard ----------

    def _paint_research(self, c: Canvas) -> None:
        c.fill(BG)
        c.top_nav(self.research_page)
        nav = [("实验", 234), ("范式", 302), ("算法", 370), ("控制", 438), ("映射", 506), ("数据", 574)]
        for name, x in nav:
            self._hit(x, 0, 62, 57, f"research:{name}")
        self._hit(642, 0, 60, 56, "self-test")
        self._hit(710, 0, 60, 56, "workspace-help")
        self._hit(1020, 8, 36, 38, "logout")
        self._hit(1068, 8, 36, 38, "workspace-summary")
        self._hit(1117, 8, 36, 38, "workspace-layout")
        self._hit(1166, 10, 89, 34, "marker-add")
        {
            "实验": self._research_eeg,
            "范式": self._research_paradigm,
            "算法": self._research_algorithm,
            "控制": self._research_controller,
            "映射": self._research_mapping,
            "数据": self._research_data,
        }[self.research_page](c)

    def _research_eeg(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-05：当前曲线、热力图和无人机卡片都是演示数据。
        # 正式版：QThread 调用 self.eeg_stream.connect/read_frame，把最新
        # EEGFrame 缓存在模型中；Qt 主线程按 20~30 FPS 只绘制缓存。
        # UAV 卡片从 self.swarm_gateway.list_uavs() 的缓存读取；实验事件同时
        # 写入 LSL Marker，最终由记录器保存为 XDF。严禁在 paintEvent 阻塞读流。
        c.panel(17, 73, 824, 305, "EEG 8通道实时波形")
        c.chart(98, 130, 723, 230, ["#ef514d", "#f1c64b", "#62df8a", "#65d9e9"], labels=True)
        c.panel(859, 73, 404, 305, "频谱热力图")
        for row in range(23):
            for col in range(37):
                strength = (math.sin(row * 0.7 + col * 0.23) + 1) / 2
                color = QColor(21 + int(strength * 10), 29 + int(strength * 12), 47 + int(strength * 14))
                c.rect(875 + col * 10, 126 + row * 10, 6, 6, color)

        c.panel(17, 398, 824, 304, "无人机状态")
        cards = [("UAV-01", 41, 458), ("UAV-02", 305, 458), ("UAV-03", 569, 458),
                 ("UAV-04", 41, 554), ("UAV-01", 305, 554), ("UAV-02", 569, 554)]
        for name, x, y in cards:
            c.rect(x, y, 248, 80, "#152239", "#2b3b58", 3)
            c.text(x + 13, y + 10, 100, 25, name, 14, TEXT, True)
            c.text(x + 13, y + 35, 150, 16, "6:30:10:35", 9, "#536076")
            c.text(x + 13, y + 52, 170, 16, "25.00 10:5503-10电量", 9, "#536076")
            c.circle(x + 224, y + 40, 9, None, GREEN, 2)
            c.circle(x + 224, y + 40, 3, GREEN)
        c.panel(859, 398, 404, 304, "控制台输出")
        for i in range(12):
            c.text(875, 445 + i * 20, 360, 18, "INFO:  LSL流已连接  14:30:01", 11, TEXT)

    def _research_paradigm(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-06 / M4.6：“应用配置”应调用 ParadigmService：
        # 校验 SSVEP 频率/刺激尺寸/占空比，保存到 YAML/JSON，并通知刺激程序；
        # 每次范式开始/结束还应向 LSL 写入 SSVEP_8Hz_start 等事件标记。
        c.rect(207, 262, 384, 252, PANEL, BORDER, 4)
        c.rect(232, 287, 47, 202, "#111c31", BLUE, 3)
        c.p.save()
        c.p.translate(255, 388)
        c.p.rotate(90)
        c.text(-55, -17, 110, 34, "SSVEP", 15, CYAN, True, Qt.AlignCenter)
        c.p.restore()
        options = [("SSVEP", 287), ("MI", 361), ("SSVEP+MI混合", 434)]
        for name, y in options:
            active = self.actions.paradigm_type == name
            c.rect(296, y, 270, 57, "#1a263a" if active else "#172236", BLUE if active else None, 3)
            c.text(312, y, 210, 57, name, 15, TEXT if active else MUTED)
            c.text(528, y, 22, 57, "⌄", 16, MUTED, False, Qt.AlignCenter)
            self._hit(296, y, 270, 57, f"paradigm-type:{name}")
        c.rect(623, 205, 450, 366, PANEL, BORDER, 4)
        c.text(656, 229, 380, 42, "SSVEP参数配置", 19, TEXT, True)
        c.text(656, 282, 130, 27, "频率配置", 14, MUTED)
        for frequency, x, width in ((8, 656, 117), (12, 788, 117), (15, 922, 118)):
            c.button(x, 322, width, 42, f"{frequency}Hz", self.actions.paradigm_frequency == frequency)
            self._hit(x, 322, width, 42, f"paradigm-frequency:{frequency}")
        knobs = (
            ("刺激尺寸", 390, 739 + 299 * self.actions.stimulus_size / 100, "paradigm-size"),
            ("占空比", 437, 739 + 299 * self.actions.duty_cycle / 100, "paradigm-duty"),
        )
        for label, y, knob, action in knobs:
            c.text(656, y - 8, 88, 34, label, 14, MUTED)
            c.line(739, y + 7, 1038, y + 7, "#e6e8eb", 7)
            c.line(739, y + 7, knob, y + 7, BLUE, 7)
            c.circle(knob, y + 7, 8, "#477af0")
            c.text(1044, y - 8, 45, 30, f"{self.actions.stimulus_size if action == 'paradigm-size' else self.actions.duty_cycle}%", 11, TEXT)
            self._hit(730, y - 5, 318, 25, action)
        c.button(656, 492, 183, 46, "应用配置", True)
        c.button(855, 492, 185, 46, "恢复默认")
        self._hit(656, 492, 183, 46, "paradigm-apply")
        self._hit(855, 492, 185, 46, "paradigm-reset")

    def _research_algorithm(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-07 / M4：算法列表应来自 AlgorithmRegistry，
        # “加载/卸载/设为默认”交给 AlgorithmService。分类器统一实现
        # predict(samples)->{label, confidence, latency_ms}，模型权重来自 .pth；
        # 模型加载和推理必须放后台线程/独立进程，不能卡住 GUI。
        c.rect(48, 104, 1184, 69, PANEL, BORDER, 3)
        c.text(66, 116, 92, 40, "当前算法:", 14, TEXT)
        c.button(158, 121, 191, 36, self.actions.algorithm)
        radios = ["手动选择", "系统默认", "自适应切换", "A/B测试"]
        x = 404
        for label in radios:
            active = self.actions.algorithm_mode == label
            c.circle(x + 9, 139, 7, BLUE if active else None, "#738098", 2)
            c.text(x + 23, 121, 87, 36, label, 13, TEXT)
            self._hit(x, 116, 101, 46, f"algorithm-mode:{label}")
            x += 101
        c.rect(48, 189, 1184, 171, PANEL, BORDER, 3)
        metadata = [("名称:", self.actions.algorithm, 73, 207), ("版本:", "v2.3", 356, 207),
                    ("训练集:", "BCIC-IV-2a", 640, 207), ("准确率:", "91.2%", 922, 207),
                    ("平均延迟:", "68ms", 73, 256), ("稀疏率:", "45%", 356, 256),
                    ("适用范式:", "SSVEP", 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#283752")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["ResMTN", "GCD", "GraSPNet", "PathoGeoNet", "CNN", "SVM", "LSTM"]
        for i, name in enumerate(algorithms):
            x = 73 + i * 144
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            c.rect(x, 441, 128, 96, "#17243a", BLUE if selected else "#2b3a55", 3, 2 if selected else 1)
            c.text(x, 452, 128, 37, name, 14, TEXT, True, Qt.AlignCenter)
            c.rect(x + 36, 504, 32, 16, GREEN if loaded else "#526078", None, 8)
            c.circle(x + 61 if loaded else x + 45, 512, 5, TEXT)
            c.circle(x + 84, 512, 8, YELLOW if default else "#2b3954")
            self._hit(x, 441, 128, 96, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 569, 112), ("卸载算法", 201, 569, 114),
                  ("设为默认", 331, 569, 114), ("性能基准测试", 461, 569, 146)]
        for i, (label, x, y, w) in enumerate(labels):
            c.button(x, y, w, 43, label, i == 0)
            self._hit(x, y, w, 43, ("algorithm-load", "algorithm-unload", "algorithm-default", "algorithm-benchmark")[i])

    def _research_controller(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-08 / M6.5：滑块和本地应用动作已可操作；正式版
        # ControllerService 还需将 Kp/Ki/Kd 校验后交给实现 BaseController 的
        # PID/MPC 控制器；参数写入 YAML，并保留版本和操作者审计记录。
        c.rect(16, 72, 64, 648, "#101b30", "#1d2d4b")
        c.rect(31, 96, 32, 3, BLUE)
        for y in (124, 172, 220, 268, 316, 364):
            c.rect(36, y, 24, 24, "#253b60", None, 4)
        c.text(112, 97, 260, 36, "控制器参数工作台", 15, TEXT, True)
        c.rect(112, 152, 1120, 69, PANEL, BORDER, 3)
        c.text(130, 165, 106, 40, "当前控制器:", 14, TEXT)
        c.button(238, 169, 128, 36, self.actions.controller)
        modes = [("● 手动选择", True), ("□ 场景自动匹配", False), ("□ 自适应切换", False), ("□ 双控制器热备", False)]
        x = 402
        for label, active in modes:
            c.button(x, 171, 126, 31, label, False, not active)
            x += 133
        c.rect(112, 246, 1120, 324, PANEL, BORDER, 3)
        c.text(145, 268, 300, 40, f"{self.actions.controller}参数配置", 17, TEXT, True)
        for label, y in (("Kp", 346), ("Ki", 402), ("Kd", 458)):
            value = self.actions.controller_values[label]
            end = 203 + 707 * value / 100
            c.text(145, y - 18, 45, 35, label, 14, TEXT)
            c.line(203, y, 910, y, "#6d6f70", 7)
            c.line(203, y, end, y, "#eeeeee", 7)
            c.circle(end, y, 8, "#f2f2f2")
            c.text(932, y - 17, 74, 34, f"{value / 100:.2f}", 13, TEXT)
            self._hit(196, y - 14, 721, 28, f"controller-slider:{label}")
        for i, (label, action) in enumerate((("应用参数", "controller-apply"), ("保存预设", "controller-save"), ("恢复默认", "controller-reset"))):
            x = 486 + i * 137
            c.button(x, 502, 121, 36, label, i == 0)
            self._hit(x, 502, 121, 36, action)
        c.rect(112, 595, 1120, 160, PANEL, BORDER, 3)
        c.text(137, 606, 200, 42, "控制器库", 16, TEXT, True)
        controllers = ("PID", "MPC", "新增", "滑模控制", "鲁棒控制", "自适应控制")
        for i, name in enumerate(controllers):
            x = 137 + i * 128
            disabled = name == "新增"
            c.button(x, 659, 104, 40, name, name == self.actions.controller, disabled, "#69c77a")
            if not disabled:
                self._hit(x, 659, 104, 40, f"controller-select:{name}")

    def _research_mapping(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-09 / M4.5：映射表应来自 CommandMappingRepository。
        # 解码 label/频率先经本映射转换为 SwarmCommand，再由安全仲裁器检查
        # 权限、置信度、避障和急停状态，最后才调用 swarm_gateway.send_command。
        # L2/L3 目前仍按设计文档保持预留，不能直接连真实飞行器。
        panels = [(192, 112, 896, 302), (192, 438, 896, 184), (192, 646, 896, 150)]
        for x, y, w, h in panels:
            c.rect(x, y, w, h, PANEL, BORDER, 3)
        c.text(217, 128, 360, 42, "L1 基础指令 (当前生效)", 16, TEXT, True)
        headers = [("SSVEP频率", 434), ("映射动作", 577), ("参数", 684), ("操作", 778)]
        for text, x in headers:
            c.text(x, 185, 104, 34, text, 13, TEXT, True, Qt.AlignCenter)
        c.line(433, 216, 847, 216, "#273752")
        for i, row in enumerate(self.actions.mappings[:4]):
            y = 218 + i * 31
            c.text(448, y, 88, 29, row.frequency, 13, TEXT, False, Qt.AlignCenter)
            c.text(564, y, 88, 29, row.action, 13, TEXT, False, Qt.AlignCenter)
            c.text(660, y, 74, 29, row.parameter, 13, TEXT, False, Qt.AlignCenter)
            c.text(742, y, 52, 29, "修改", 13, BLUE, False, Qt.AlignCenter)
            c.text(797, y, 47, 29, "删除", 13, RED, False, Qt.AlignCenter)
            self._hit(742, y, 52, 29, f"mapping-edit:{i}")
            self._hit(797, y, 47, 29, f"mapping-delete:{i}")
            c.line(433, y + 30, 847, y + 30, "#273752")
        c.button(576, 352, 128, 38, "添加映射", True)
        self._hit(576, 352, 128, 38, "mapping-add")
        c.text(217, 454, 360, 42, "L2 行为级指令 (预留)", 16, TEXT, True)
        for i, name in enumerate(("侦察队形", "菱形编队", "左翼包抄", "垂直爬升")):
            c.button(360 + i * 144, 511, 128, 51, name, False, True)
        c.text(459, 569, 320, 34, "当前不可用 (需切换至行为控制模式)", 13, "#596477", False, Qt.AlignCenter)
        c.text(217, 663, 360, 42, "L3 战术意图 (占位)", 16, TEXT, True)

    def _research_data(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-10 / M11：会话列表应查询 SQLite 元数据；波形和
        # Marker 从 XDF 读取。标签编辑后更新事件索引；“数据集生成”按窗口切片、
        # 剔除 Bad_Epoch、8:2 划分并导出 .npz。大文件读写必须放工作线程。
        c.rect(16, 72, 1248, 35, "#0b1425")
        c.text(39, 72, 360, 35, "实验数据工作台", 17, TEXT, True)
        for i, name in enumerate(("数据浏览", "标签管理", "数据集生成")):
            x = 543 + i * 106
            c.button(x, 72, 90, 34, name, self.actions.data_tab == name)
            self._hit(x, 72, 90, 34, f"data-tab:{name}")
        c.text(1185, 72, 62, 34, "⌕  ⇧", 20, MUTED, False, Qt.AlignCenter)
        c.rect(32, 123, 254, 597, "#0b1425", BORDER)
        c.text(40, 125, 190, 43, "数据浏览", 14, TEXT, True)
        c.text(260, 125, 20, 43, "×", 16, TEXT, True, Qt.AlignCenter)
        c.line(32, 168, 286, 168, BORDER)
        c.text(40, 169, 210, 38, "实验会话", 13, MUTED)
        names = ["2026-08-02 实验1", "2026-08-02 实验2", "2026-08-02 实验2", "2026-08-02 实验2",
                 "2026-08-02 实验2", "2026-08-02 实验3", "2026-08-02 实验2", "2026-08-02 实验3",
                 "2026-08-02 实验1", "2026-08-02 实验2", "2026-08-02 实验3", "2026-08-02 实验1"]
        for i, name in enumerate(names):
            y = 211 + i * 40
            if i == self.actions.selected_session:
                c.rect(41, y, 238, 36, BLUE, None, 3)
            c.text(52, y, 195, 36, name, 13, TEXT)
            c.text(250, y, 20, 36, "›", 18, MUTED, False, Qt.AlignCenter)
            self._hit(41, y, 238, 36, f"data-session:{i}")
        c.rect(304, 123, 944, 597, "#071020", BORDER)
        c.text(317, 124, 220, 44, "脑中会话", 14, TEXT, True)
        c.text(1203, 124, 34, 44, "⌕×", 18, MUTED, False, Qt.AlignCenter)
        c.line(304, 168, 1248, 168, BORDER)
        c.chart(340, 208, 872, 492, ["#6dd28b", "#f0d052", "#6c9bd9", "#987bd8"], amp=.18, points=78)
        markers = [(494, "左转", GREEN), (523, "左转", GREEN), (636, "左转", GREEN),
                   (824, "右转", GREEN), (1013, "MI开始", RED), (1060, "MI开始", CYAN)]
        for x, label, color in markers:
            c.line(x, 212, x, 697, color, 4)
            c.text(x - 36, 190 if x < 900 else 191, 80, 20, label, 11, color, True, Qt.AlignCenter)

    # ---------- Administrator dashboard ----------

    def _paint_admin(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-12：四类真实服务：DeviceRegistry（设备/固件/在线）、
        # UserRepository（用户与 RBAC）、StorageService（XDF/SQLite 容量）、
        # BackupService（备份/恢复）。这些是高风险管理操作，必须二次确认、
        # 角色鉴权和写审计日志，不能只修改当前画面上的数字。
        c.fill("#070d1f")
        stats = [("▦", "设备总数", "12"), ("▤", "在线设备", "10"), ("!", "告警", "2"),
                 ("▱", "存储使用率", f"{self.actions.storage_percent}%")]
        for i, (icon, label, value) in enumerate(stats):
            x = 33 + i * 310
            c.rect(x, 32, 285, 90, PANEL, BORDER, 3)
            c.rect(x + 16, 53, 48, 48, "#13264a", "#254783", 3)
            c.text(x + 16, 53, 48, 48, icon, 24, BLUE, False, Qt.AlignCenter)
            c.text(x + 80, 48, 175, 28, label, 13, MUTED)
            c.text(x + 80, 72, 175, 34, value, 25, TEXT, True)
        c.panel(33, 155, 590, 263, "设备管理")
        self._table(c, 49, 224, 558, ["名称", "类型", "固件版本", "在线状态", "操作"],
                    [["1   DeepBCI 采集板", "无", "v2.1", "在线", "查看"],
                     ["2   Crazyflie-01", "无人机", "v1.3", "在线", "查看"],
                     ["3   智能头盔", "头盔", "v1.0", "离线", "查看"]],
                    [190, 88, 104, 104, 70])
        for i, name in enumerate(("DeepBCI采集板", "Crazyflie-01", "智能头盔")):
            self._hit(537, 268 + i * 45, 70, 44, f"admin-device:{name}")
        c.panel(657, 155, 590, 263, "用户管理")
        self._table(c, 673, 224, 558, ["用户名", "角色", "最后登录", "操作"],
                    [["张伟", self.actions.user_roles["张伟"], "2026-08-30", "编辑"],
                     ["李明", self.actions.user_roles["李明"], "2026-08-29", "编辑"],
                     ["王强", self.actions.user_roles["王强"], "2026-08-31", "编辑"]],
                    [120, 145, 194, 99])
        for i, name in enumerate(("张伟", "李明", "王强")):
            self._hit(1132, 268 + i * 45, 99, 44, f"admin-user:{name}")
        c.panel(33, 452, 590, 263, "存储管理")
        c.text(151, 590, 100, 50, f"{self.actions.storage_percent}%", 26, TEXT, True, Qt.AlignCenter)
        c.text(245, 674, 75, 35, f"{100 - self.actions.storage_percent}%", 13, MUTED, False, Qt.AlignCenter)
        c.button(412, 599, 139, 44, "▱  清理缓存")
        self._hit(412, 599, 139, 44, "admin-clean-cache")
        c.panel(657, 452, 590, 263, "系统备份")
        c.text(822, 547, 260, 32, "最近备份时间", 13, MUTED, False, Qt.AlignCenter)
        c.text(807, 581, 290, 35, self.actions.last_backup, 18, TEXT, False, Qt.AlignCenter)
        c.button(760, 642, 179, 46, "立即备份", True)
        c.button(963, 642, 181, 46, "恢复")
        self._hit(760, 642, 179, 46, "admin-backup")
        self._hit(963, 642, 181, 46, "admin-restore")

    def _table(self, c: Canvas, x: float, y: float, w: float, headers: list[str], rows: list[list[str]], widths: list[int]) -> None:
        c.rect(x, y, w, 44, "#17243a")
        cursor = x
        for title, width in zip(headers, widths):
            c.text(cursor + 11, y, width - 12, 44, title, 13, TEXT, True)
            cursor += width
        for r, row in enumerate(rows):
            row_y = y + 44 + r * 45
            cursor = x
            for col, (value, width) in enumerate(zip(row, widths)):
                color = BLUE if col == len(row) - 1 else ("#606b80" if value == "离线" else TEXT)
                c.text(cursor + 11, row_y, width - 12, 44, value, 13, color)
                cursor += width
            c.line(x, row_y + 44, x + w, row_y + 44, "#273752")

    # ---------- Engineering dashboard ----------

    def _paint_engineer(self, c: Canvas) -> None:
        if self.engineer_page == "深度诊断":
            self._engineer_diagnostics(c)
        else:
            self._engineer_monitor(c)
        self._hit(550, 674, 96, 40, "engineer:系统监控")
        self._hit(648, 674, 94, 40, "engineer:深度诊断")

    def _engineer_monitor(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-11：CPU/GPU/内存/延迟/吞吐量来自
        # self.system_monitor.snapshot() 的定时缓存；LSL 卡片来自流发现服务，
        # 日志来自 logging/loguru 的队列处理器。不要在绘图线程读取日志文件。
        c.fill("#080e20")
        c.rect(0, 0, 255, 720, "#0d172a")
        c.text(24, 18, 180, 42, "模块树", 17, TEXT, True)
        modules = ["M1  蜂群作战管理", "M2  目标识别与打击", "M4  脑电解码引擎", "M4  脑电解码引擎",
                   "M6  设备协同管理", "M7  设备协同监控", "M7  信号质量监控", "M11  数据策管"]
        for i, name in enumerate(modules):
            y = 70 + i * 32
            if i == 6:
                c.rect(24, y, 231, 29, "#1a2c58")
                c.rect(24, y, 4, 29, BLUE)
            c.text(39, y, 205, 29, "• " + name, 12, TEXT if i == 6 else "#aeb7c8")
        c.text(280, 17, 520, 43, "LSL数据流监控", 20, TEXT, True)
        c.panel(280, 77, 654, 249, "LSL数据流监控")
        streams = [("EEG", "250Hz", "0.1%"), ("Crazyflie\n遥测流", "100Hz", "0.3%"), ("fNIRS\n流", "50Hz", "0.0%")]
        for i, (name, rate, loss) in enumerate(streams):
            x = 297 + i * 215
            c.rect(x, 133, 191, 178, "#17243a", "#2a3a57", 3)
            c.text(x + 16, 145, 104, 39, name, 14, TEXT, True)
            c.text(x + 109, 145, 70, 25, "采样率", 14, TEXT, True, Qt.AlignRight)
            c.text(x + 109, 175, 70, 27, rate, 20, TEXT, True, Qt.AlignRight)
            c.line(x + 16, 236, x + 175, 236, "#263750")
            c.text(x + 16, 242, 80, 28, f"丢包率{loss}", 11, MUTED)
            c.text(x + 126, 242, 49, 28, loss, 13, CYAN, False, Qt.AlignRight)
        c.rect(314, 174, 127, 114, "#07101f", BLUE, 3)
        for i, name in enumerate(("实时预处理", "实时预处理", "算法推理", "置信度输出")):
            c.text(334, 177 + i * 25, 102, 23, "• " + name, 11, TEXT if i < 2 else MUTED)
        c.panel(280, 354, 654, 335, "性能监控")
        c.text(767, 357, 151, 32, "━ CPU    ━ 内存    ━ 延迟", 11, "#72dc83", False, Qt.AlignRight)
        c.chart(361, 421, 553, 210, [BLUE, "#efaa42", GREEN], amp=.19, points=40)
        c.text(280, 689, 260, 27, "运行时间 01:23:45", 11, MUTED)
        c.text(530, 689, 160, 27, "帧率 60 FPS", 11, TEXT, True)
        c.text(788, 689, 146, 27, "数据吞吐量 4.2 MB/s", 11, MUTED, False, Qt.AlignRight)
        c.rect(960, 0, 320, 720, "#0d172a")
        for i, name in enumerate(("INFO", "WARN", "ERROR")):
            active = self.actions.log_filter == name
            c.text(960 + i * 106, 0, 106, 53, name, 13, TEXT if active else MUTED, active, Qt.AlignCenter)
            if active:
                c.rect(960 + i * 106, 52, 106, 3, BLUE)
            self._hit(960 + i * 106, 0, 106, 55, f"log-filter:{name}")
        log_sets = {
            "INFO": ["INFO: 算法已加载 GraSPNet  14:30:05", "INFO: LSL流已连接  14:30:01"],
            "WARN": ["WARN: 信号质量下降  14:30:12", "WARN: UAV-03 丢包率偏高  14:31:08"],
            "ERROR": ["ERROR: 暂无未处理错误  14:31:30"],
        }
        logs = log_sets[self.actions.log_filter]
        for i in range(19):
            value = logs[i % len(logs)]
            color = RED if value.startswith("ERROR") else (YELLOW if value.startswith("WARN") else TEXT)
            c.text(977, 67 + i * 24, 290, 22, value, 10, color, "算法" in value)
        c.button(550, 674, 96, 31, "系统监控", True, accent=CYAN)
        c.button(648, 674, 94, 31, "深度诊断")

    def _engineer_diagnostics(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-13 / M10：资源仪表复用 SystemMonitor；通信诊断
        # 读取各设备丢包率和往返延迟；干扰曲线来自 EEG 频谱/50 Hz 检测。
        # “一键自检”应调用 DiagnosticService，异步返回逐项结果和错误详情。
        c.fill("#050b16")
        c.rect(8, 8, 1263, 704, None, BORDER)
        c.rect(440, 0, 400, 40, "#0f1c31", CYAN, 12, 2)
        c.text(440, 0, 400, 39, "系统深度诊断", 17, CYAN, True, Qt.AlignCenter)
        c.panel(33, 66, 594, 296, "资源监控")
        c.gauge(168, 211, 70, 62, "CPU", "#5dbcd4")
        c.gauge(356, 211, 70, 45, "GPU", "#66c88a")
        c.gauge(549, 211, 70, 58, "内存", "#5dbcd4")
        c.panel(653, 66, 594, 296, "通信诊断")
        # bars
        bars = [(42, 67), (20, 53), (52, 56), (44, 40), (31, 45)]
        for i, (a, b) in enumerate(bars):
            x = 744 + i * 98
            c.rect(x, 242 - a * 1.3, 37, a * 1.3, CYAN)
            c.rect(x + 42, 242 - b * 1.3, 37, b * 1.3, GREEN)
        c.line(734, 242, 1224, 242, "#263752")
        path = QPainterPath(QPointF(734, 312))
        for i in range(110):
            xx = 734 + i
            yy = 312 - 31 * math.exp(-((i - 52) / 19) ** 2) + max(0, i - 84) * .25
            path.lineTo(xx, yy)
        c.p.setPen(QPen(QColor(YELLOW), 1.2))
        c.p.setBrush(QColor(219, 154, 55, 45))
        path.lineTo(843, 362)
        path.lineTo(734, 362)
        path.closeSubpath()
        c.p.drawPath(path)
        c.panel(33, 389, 594, 298, "干扰检测")
        c.chart(94, 444, 513, 228, [CYAN], amp=.05, points=90)
        c.rect(320, 458, 62, 196, QColor(241, 78, 80, 25))
        c.line(332, 444, 332, 663, RED, 1, Qt.DashLine)
        c.text(287, 462, 100, 42, "50Hz工频干扰\n幅值 12.5µV", 11, RED, True, Qt.AlignCenter)
        c.panel(653, 389, 594, 298, "自检报告")
        c.button(831, 473, 238, 46, "重新自检" if self.actions.self_test_run else "一键自检", True)
        self._hit(831, 473, 238, 46, "self-test")
        checks = [("电源系统  通过", "通过", GREEN), ("通信链路  通过", "通过", GREEN),
                  ("信号质量  已复测" if self.actions.self_test_run else "信号质量  警告 (阻抗偏高)",
                   "通过" if self.actions.self_test_run else "警告",
                   GREEN if self.actions.self_test_run else YELLOW)]
        for i, (label, state, color) in enumerate(checks):
            y = 551 + i * 66
            c.rect(831, y, 238, 49, "#0d172a", BORDER, 3)
            c.text(845, y, 174, 49, label, 13, TEXT)
            c.text(1018, y, 39, 49, state, 13, color, True, Qt.AlignRight)
        c.button(550, 674, 96, 31, "系统监控")
        c.button(648, 674, 94, 31, "深度诊断", True, accent=CYAN)

    # ---------- Soldier/commander dashboards ----------

    def _paint_soldier(self, c: Canvas) -> None:
        if self.soldier_root == "作战地图":
            self._soldier_combat(c)
        elif self.soldier_page == "状态监控":
            self._soldier_status(c)
        else:
            self._soldier_brain(c)
        # top right shell switch
        c.button(1103, 18, 81, 32, "作战地图", self.soldier_root == "作战地图", accent=CYAN)
        c.button(1185, 18, 76, 32, "单兵面板", self.soldier_root == "单兵面板", accent=CYAN)
        self._hit(1103, 18, 81, 34, "soldier-root:作战地图")
        self._hit(1185, 18, 78, 34, "soldier-root:单兵面板")

    def _soldier_combat(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-02 / M1/M6：地图上的 UAV 位置、状态卡和电量应
        # 从 self.swarm_gateway.list_uavs() 的定时缓存读取；目标信息来自目标识别
        # 服务。任务按钮只能提交 TacticalTask 给 SwarmManager，不能从 UI 绕过
        # 指令仲裁直接控制电机。链路中断时由网关执行悬停/返航失效保护。
        c.fill("#03101d")
        # tactical grid
        for x in range(24, 952, 58):
            c.line(x, 24, x, 702, "#102536", .7)
        for y in range(25, 703, 54):
            c.line(16, y, 936, y, "#102536", .7)
        c.rect(16, 24, 920, 678, None, "#20354b")
        now = datetime.now().strftime("%H:%M:%S")
        c.text(24, 5, 140, 25, now, 12, "#c4d7e5")
        c.text(85, 5, 140, 25, now, 12, "#59697a")
        # centered network tab
        c.line(568, 0, 584, 27, CYAN, 1)
        c.line(584, 27, 872, 27, CYAN, 1)
        c.line(872, 27, 888, 0, CYAN, 1)
        c.circle(605, 18, 6, GREEN)
        c.text(617, 3, 120, 28, "网络正常", 13, TEXT, True)
        c.text(1164, 4, 94, 26, "总电量 86%", 12, TEXT, True, Qt.AlignRight)
        # links and central target
        links = [(204, 485, 328, 176), (328, 176, 703, 153), (703, 153, 825, 384), (825, 384, 204, 485)]
        for x1, y1, x2, y2 in links:
            c.line(x1, y1, x2, y2, "#355160", .8)
        c.circle(577, 394, 120, None, "#a88b49", 1)
        c.circle(577, 394, 41, None, "#a88b49", 1)
        c.circle(577, 394, 5, "#f1a427")
        c.text(649, 280, 66, 25, "目标A", 12, RED, True)
        c.text(585, 445, 66, 25, "目标B", 12, "#e2a93a", True)
        for name, x, y in (("UAV-01", 204, 485), ("UAV-02", 328, 176), ("UAV-03", 703, 153), ("UAV-04", 825, 384)):
            for radius in (23, 16, 9, 4):
                c.circle(x, y, radius, None, CYAN, 2)
            c.circle(x, y, 28, QColor(72, 231, 248, 22))
            c.text(x - 45, y + 25, 90, 24, name, 13, TEXT, True, Qt.AlignCenter)
        c.rect(936, 24, 326, 672, "#07111f", "#22354c")
        c.rect(952, 44, 4, 13, CYAN)
        c.text(964, 35, 240, 31, "蜂群状态总览", 15, CYAN, True)
        entries = [("UAV-01", "电量82%", "速度2.1m/s", "任务-侦察"),
                   ("UAV-02", "电量75%", "高度1.5m", "任务-编队"),
                   ("UAV-03", "速度 1.8m/s", "高度1.2m", "任务-编队")]
        for i, row in enumerate(entries):
            y = 74 + i * 70
            c.rect(953, y, 285, 60, PANEL, BORDER, 3)
            c.rect(953, y, 4, 60, CYAN)
            c.text(966, y + 7, 120, 25, row[0], 13, TEXT, True)
            c.text(966, y + 29, 105, 21, row[1], 10, MUTED)
            c.text(1112, y + 5, 114, 23, row[2], 12, TEXT, True, Qt.AlignRight)
            c.text(1112, y + 28, 114, 20, row[3], 10, CYAN, False, Qt.AlignRight)
        c.rect(952, 303, 4, 13, CYAN)
        c.text(964, 294, 220, 31, "目标信息", 14, CYAN, True)
        c.rect(953, 332, 285, 62, PANEL, BORDER, 3)
        c.circle(974, 363, 7, None, RED, 2)
        c.circle(974, 363, 2, RED)
        c.text(994, 340, 150, 28, "目标A", 14, TEXT, True)
        c.text(994, 366, 220, 20, "类型-导弹车   距离45m", 10, MUTED)
        c.rect(952, 423, 4, 13, CYAN)
        c.text(964, 414, 220, 31, "操作员状态", 14, CYAN, True)
        c.circle(967, 460, 6, GREEN)
        c.text(980, 443, 170, 34, "注意力：85%", 13, TEXT, True)
        c.rect(952, 500, 4, 13, CYAN)
        c.text(964, 491, 220, 31, "指令日志", 14, CYAN, True)
        c.rect(953, 527, 285, 152, PANEL, BORDER, 3)
        for i, command in enumerate(self.actions.command_history[:5]):
            c.text(966, 535 + i * 26, 245, 25, f"指令：{command}", 11, RED if command == "紧急停止" else MUTED)

    def _soldier_brain(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-03 / M4→M4.5→M6：方向和置信度来自解码服务：
        # EEGFrame -> classifier.predict -> 指令映射 -> 安全仲裁 ->
        # swarm_gateway.send_command。中心“悬停/确认”也走同一命令通道。
        # “紧急停止”必须直接调用 swarm_gateway.emergency_stop()，并要求硬件
        # 回执、超时处理和审计日志；不能只把红色按钮画出来。
        c.fill("#111420")
        c.circle(594, 52, 6, RED if self.actions.emergency_stopped else GREEN)
        c.text(608, 36, 180, 32, "控制已锁定" if self.actions.emergency_stopped else "脑控已激活", 14, TEXT, True)
        # diamond connector
        c.line(640, 267, 752, 380, "#252c3a")
        c.line(752, 380, 640, 493, "#252c3a")
        c.line(640, 493, 528, 380, "#252c3a")
        c.line(528, 380, 640, 267, "#252c3a")
        directions = [(640, 267, 48, "#6ac584", "↑", "forward"), (528, 380, 48, "#f0d15a", "←", "left"),
                      (752, 380, 48, "#ee8d43", "→", "right"), (640, 493, 48, "#5799dd", "↓", "backward")]
        for x, y, r, color, arrow, action in directions:
            # restrained glow
            c.circle(x, y, r + 12, QColor(QColor(color).red(), QColor(color).green(), QColor(color).blue(), 22))
            c.circle(x, y, r, color)
            c.text(x - r, y - r, r * 2, r * 2, arrow, 35, TEXT, False, Qt.AlignCenter)
            self._hit(x - r, y - r, r * 2, r * 2, f"swarm-command:{action}")
        c.circle(640, 380, 33, "#df4c4e")
        c.text(612, 350, 56, 62, "悬停\n/确认", 10, TEXT, True, Qt.AlignCenter)
        self._hit(607, 347, 66, 66, "swarm-command:hover")
        c.text(456, 613, 280, 30, "解码置信度", 13, MUTED)
        c.line(448, 653, 728, 653, "#63a9e9", 7)
        c.text(736, 628, 80, 51, "置信度\n82%", 14, TEXT, True)
        c.line(796, 653, 832, 653, "#35425a", 6)
        c.circle(1168, 608, 64, "#a52d32" if self.actions.emergency_stopped else "#ee8284", RED, 4)
        c.circle(1168, 608, 73, QColor(239, 78, 80, 28))
        c.text(1104, 545, 128, 126, "已急停" if self.actions.emergency_stopped else "紧急停止", 19, TEXT, True, Qt.AlignCenter)
        self._hit(1095, 535, 146, 146, "swarm-emergency-stop")
        self._paint_soldier_bottom_toggle(c)

    def _soldier_status(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-04 / M3：注意力、疲劳、压力和心率应来自
        # SoldierVitalsStream（EEG/EMG/心率/体温/血氧的 LSL 多模态流）。
        # 阈值告警交给独立 AlertService，并同步写入 XDF Marker 和审计日志。
        c.fill("#071020")
        c.rect(32, 33, 1215, 57, PANEL, BORDER, 3)
        c.rect(32, 33, 81, 4, CYAN)
        c.text(56, 41, 520, 41, "ID: S-07    |    姓名: 张伟    |    心率: 72 bpm 🏃", 16, TEXT, True)
        c.button(1135, 42, 88, 36, "导出报告", True)
        self._hit(1135, 42, 88, 36, "soldier-export")
        gauges = [(33, "85", "注意力", GREEN), (445, "62", "疲劳度", YELLOW), (859, "40", "情绪压力", RED)]
        for x, value, label, color in gauges:
            c.panel(x, 116, 388, 253)
            c.gauge(x + 194, 238, 66, int(value), label, color)
        c.panel(33, 395, 1214, 292)
        c.text(48, 401, 110, 34, "数值", 12, MUTED)
        # The reference chart intentionally contains only a short 60-second
        # sample on the left, leaving the remainder of the panel empty.
        for i in range(1, 10):
            c.line(98, 441 + i * 19.3, 1208, 441 + i * 19.3, "#263249", 1, Qt.DashLine)
        samples = [
            [12, 12.5, 13.2, 13.0, 14.2, 15.4, 15.8, 16.0, 15.2, 15.4, 14.8, 15.1, 14.5, 13.9, 13.0, 12.0, 11.5],
            [9.8, 10.5, 10.3, 9.7, 9.6, 10.0, 9.0, 8.4, 7.9, 6.8, 7.4, 7.8, 5.6, 7.1, 6.0, 5.8, 3.7],
            [2.0, 3.0, 4.0, 4.4, 4.3, 4.5, 3.9, 4.1, 3.3, 2.5, 2.0, 1.7, 0.7, 0.3, 1.0, 0.5, 0.3],
        ]
        for values, color in zip(samples, (GREEN, YELLOW, CYAN)):
            path = QPainterPath()
            for i, value in enumerate(values):
                px = 98 + i * 19.2
                py = 634 - value * 9.7
                path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
            c.p.setPen(QPen(QColor(color), 1.8))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.text(563, 644, 160, 30, "最近60秒", 12, MUTED, False, Qt.AlignCenter)
        c.rect(508, 680, 264, 39, "#252414", "#5f5424", 3)
        c.text(518, 680, 244, 38, "⚠️  注意: 疲劳度偏高，建议休息", 11, YELLOW, True, Qt.AlignCenter)
        self._paint_soldier_bottom_toggle(c)

    def _paint_soldier_bottom_toggle(self, c: Canvas) -> None:
        c.text(24, 674, 45, 32, "脑控", 13, TEXT if self.soldier_page == "脑控" else MUTED, self.soldier_page == "脑控")
        c.text(68, 674, 14, 32, "|", 13, MUTED, False, Qt.AlignCenter)
        c.text(87, 674, 78, 32, "状态监控", 13, TEXT if self.soldier_page == "状态监控" else MUTED,
               self.soldier_page == "状态监控")
        self._hit(17, 669, 55, 42, "soldier-page:脑控")
        self._hit(82, 669, 94, 42, "soldier-page:状态监控")
