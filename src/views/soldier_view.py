"""Soldier and commander dashboard rendering."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.widgets.scifi_canvas import Canvas
from src.widgets.soldier_canvas import BORDER, MUTED, PANEL, TEXT

# Operational chrome is monochrome; words and shapes carry state as well as contrast.
CYAN = BLUE = GREEN = RED = "#303030"
YELLOW = "#787878"


class SoldierViewMixin:
    """Render combat-map, brain-control and soldier-status pages."""
    def _paint_soldier(self, c: Canvas) -> None:
        if self.soldier_root == "作战地图":
            self._soldier_combat(c)
        elif self.soldier_page == "视觉手势":
            c.fill("#f5f5f5")
        elif self.soldier_page == "状态监控":
            self._soldier_status(c)
        else:
            self._soldier_brain(c)

    def _soldier_combat(self, c: Canvas) -> None:
        c.fill("#f5f5f5")
        # tactical grid
        for x in range(24, 952, 58):
            c.line(x, 24, x, 702, "#e8e8e8", .7)
        for y in range(25, 703, 54):
            c.line(16, y, 936, y, "#e8e8e8", .7)
        c.rect(16, 24, 920, 678, None, "#dedede")
        now = datetime.now().strftime("%H:%M:%S")
        c.text(24, 5, 140, 25, now, 12, "#737373")
        uavs = list(self.swarm_gateway.list_uavs()) if self.swarm_gateway is not None else []
        average_battery = sum(uav.battery for uav in uavs) / len(uavs) if uavs else 0.0
        c.text(1124, 4, 134, 26, f"平均电量 {average_battery:.0f}%" if uavs else "无实机遥测", 12, TEXT, True, Qt.AlignRight)
        points = [
            (uav, 476 + max(-1.0, min(1.0, uav.position.x)) * 330,
             360 - max(-1.0, min(1.0, uav.position.y)) * 260)
            for uav in uavs
        ]
        for index, (_, x1, y1) in enumerate(points):
            if len(points) > 1:
                _, x2, y2 = points[(index + 1) % len(points)]
                c.line(x1, y1, x2, y2, "#b0b0b0", .8)
        if not points:
            c.text(230, 320, 500, 70, "没有真实无人机遥测\n请在脑控页连接 Crazyflie", 18, MUTED, True, Qt.AlignCenter)
        for uav, x, y in points:
            for radius in (23, 16, 9, 4):
                c.circle(x, y, radius, None, CYAN, 2)
            c.circle(x, y, 28, QColor(40, 40, 40, 18))
            c.text(x - 55, y + 25, 110, 24, uav.uav_id, 13, TEXT, True, Qt.AlignCenter)
        c.rect(936, 24, 326, 672, "#ffffff", "#dedede")
        c.rect(952, 44, 4, 13, CYAN)
        c.text(964, 35, 240, 31, "蜂群状态总览", 15, CYAN, True)
        for i, uav in enumerate(uavs[:3]):
            y = 74 + i * 70
            c.rect(953, y, 285, 60, PANEL, BORDER, 3)
            c.rect(953, y, 4, 60, CYAN)
            c.text(966, y + 7, 150, 25, uav.uav_id, 13, TEXT, True)
            c.text(966, y + 29, 130, 21, f"电量 {uav.battery:.0f}%  {uav.battery_voltage or 0:.2f}V", 10, MUTED)
            c.text(1092, y + 5, 134, 23, f"高度 {uav.altitude_m:.2f}m", 12, TEXT, True, Qt.AlignRight)
            source = "LH" if uav.positioning_source == "lighthouse" else "Flow"
            c.text(1092, y + 28, 134, 20,
                   f"{source} · {uav.control_mode}", 9, CYAN, False, Qt.AlignRight)
        c.rect(952, 303, 4, 13, CYAN)
        c.text(964, 294, 220, 31, "目标识别接口", 14, CYAN, True)
        c.rect(953, 332, 285, 62, PANEL, BORDER, 3)
        c.circle(974, 363, 7, None, YELLOW, 2)
        c.text(994, 340, 210, 28, "未接入", 14, TEXT, True)
        c.text(994, 366, 220, 20, "等待真实视觉模型和坐标接口", 10, MUTED)
        c.rect(952, 423, 4, 13, CYAN)
        c.text(964, 414, 220, 31, "操作员状态", 14, CYAN, True)
        c.circle(967, 460, 6, GREEN)
        c.text(980, 443, 220, 34, "多模态生命体征未接入", 13, TEXT, True)
        c.rect(952, 500, 4, 13, CYAN)
        c.text(964, 491, 220, 31, "指令日志", 14, CYAN, True)
        c.rect(953, 527, 285, 152, PANEL, BORDER, 3)
        for i, command in enumerate(self.actions.command_history[:5]):
            c.text(966, 535 + i * 26, 245, 25, f"指令：{command}", 11, RED if command == "紧急停止" else MUTED)

    def _soldier_brain(self, c: Canvas) -> None:
        c.fill("#f5f5f5")
        c.panel(300, 82, 606, 612)
        c.text(326, 87, 520, 30, "驾驶参数与手动操控", 14, TEXT, True)
        c.panel(920, 82, 330, 330, "任务与编队")
        gateway_status = self.swarm_gateway.status() if self.swarm_gateway is not None else None
        telemetry = tuple(self.swarm_gateway.list_uavs()) if self.swarm_gateway is not None else ()
        configured_uris = tuple(
            getattr(getattr(self.swarm_gateway, "config", None), "uris", ())
        )
        # Mock/demo gateways do not expose a hardware config; their telemetry
        # count is the best available indication of single vs. multi-aircraft.
        configured_count = len(configured_uris) if configured_uris else len(telemetry)
        multi_aircraft = configured_count > 1
        single_aircraft = configured_count == 1
        connected = bool(gateway_status and gateway_status.connected)
        armed = bool(gateway_status and gateway_status.armed)
        airborne = bool(gateway_status and gateway_status.airborne)
        flow_mode = bool(
            gateway_status and gateway_status.backend in {"flowdeck", "lighthouse_flow"}
        )
        emergency = bool(self.actions.emergency_stopped or (gateway_status and gateway_status.emergency))
        state = gateway_status.state if gateway_status is not None else "disconnected"
        status_color = (
            RED if emergency or state in {"flight_error", "stop_error", "battery_low", "battery_low_grounded"} else
            YELLOW if state in {"taking_off", "stopping_motors", "landing"} else
            GREEN if connected else YELLOW
        )
        status_text = (
            "急停锁定 · 需重启" if emergency else
            "电量过低 · 请立即受控降落" if state == "battery_low" else
            "低电压提示 · 已落地" if state == "battery_low_grounded" else
            "正在受控降落" if state == "landing" else
            "正在停止电机" if state == "stopping_motors" else
            "Lighthouse 编队保持" if state == "formation_hold" else
            "正在自动编队" if state == "forming" else
            "Lighthouse 位置保持" if state == "lighthouse_hold" else
            "Flow Hover 操控中" if state == "manual_flight" else
            "正在建立 Flow Hover" if state == "taking_off" else
            "飞行状态异常 · 请处置" if airborne and not armed else
            "飞行中 · 控制已解锁" if armed and airborne else
            "实机已解锁" if armed else
            f"蜂群已连接 · {len(telemetry)}/{len(configured_uris)}" if connected and multi_aircraft else
            "实机已连接 · 待解锁" if connected and bool(gateway_status and gateway_status.control_enabled) else
            "只读遥测已连接" if connected else
            "实机未连接"
        )
        c.circle(594, 52, 6, status_color)
        c.text(608, 36, 220, 32, status_text, 14, TEXT, True)

        target_height = self.actions.swarm_takeoff_height_m()
        c.text(385, 126, 160, 32, "启动目标高度", 13, TEXT, True, Qt.AlignCenter)
        c.button(548, 126, 46, 32, "－", False, accent=BLUE)
        c.button(601, 126, 100, 32, f"{target_height:.2f} m", False, accent=CYAN)
        c.button(708, 126, 46, 32, "＋", False, accent=GREEN)
        c.text(762, 126, 145, 32, "范围 0.20–2.00m", 10, MUTED, False, Qt.AlignCenter)
        self._hit(548, 126, 46, 32, "swarm-height-decrease")
        self._hit(601, 126, 100, 32, "swarm-height-custom")
        self._hit(708, 126, 46, 32, "swarm-height-increase")
        move_distance_cm = self.actions.swarm_move_distance_m() * 100.0
        c.text(385, 170, 160, 32, "SSVEP移动距离", 13, TEXT, True, Qt.AlignCenter)
        c.button(548, 170, 206, 32, f"设置为 {move_distance_cm:g} cm", False, accent=CYAN)
        c.text(762, 170, 145, 32, "范围 1–100cm", 10, MUTED, False, Qt.AlignCenter)
        self._hit(548, 170, 206, 32, "swarm-move-distance-custom")
        c.button(942, 130, 286, 32, "模拟结构化任务", False, accent=YELLOW)
        self._hit(942, 130, 286, 32, "mission-simulate")
        lighthouse_ready = bool(telemetry) and all(item.lighthouse_ready for item in telemetry)
        lighthouse_active = state == "lighthouse_hold"
        lighthouse_switch_enabled = (
            single_aircraft and connected and armed and airborne
            and lighthouse_ready and not emergency and not lighthouse_active
        )
        c.button(
            942, 174, 286, 32, "Lighthouse保持", lighthouse_active,
            disabled=not (lighthouse_switch_enabled or lighthouse_active), accent=CYAN,
        )
        c.button(942, 218, 286, 32, "直线编队", state == "formation_hold",
                 disabled=single_aircraft, accent=GREEN)
        c.button(942, 262, 286, 32, "三角编队", state == "formation_hold",
                 disabled=single_aircraft, accent=GREEN)
        eeg_live = self._soldier_live_chain_ready()
        ssvep_ready = (
            eeg_live and connected and self.actions.bci_control_enabled and not emergency
        )
        ssvep_startable = connected and bool(gateway_status and gateway_status.control_enabled) and not emergency
        ssvep_starting = self._soldier_bci_start_pending
        ssvep_label = (
            "进入SSVEP驾驶" if ssvep_ready else
            "正在启动实时链路…" if ssvep_starting else
            "启动SSVEP驾驶"
        )
        c.button(
            942, 306, 286, 42,
            ssvep_label,
            ssvep_ready,
            disabled=not ssvep_startable or ssvep_starting,
            accent=CYAN,
        )
        if ssvep_startable and not ssvep_starting:
            self._hit(942, 306, 286, 42, "soldier-ssvep-control")
        readiness = (
            "实时采集、LSL、FBCCA与实机均已就绪" if ssvep_ready else
            "正在连接采集盒并等待首帧EEG" if ssvep_starting else
            "点击后自动启动采集、LSL与FBCCA" if ssvep_startable else
            "请先连接实机并确认未处于急停状态"
        )
        c.text(942, 357, 286, 40, readiness, 9, CYAN if ssvep_ready else MUTED, False, Qt.AlignCenter)
        if lighthouse_switch_enabled:
            self._hit(942, 174, 286, 32, "swarm-lighthouse-hold")
        if multi_aircraft:
            self._hit(942, 218, 286, 32, "swarm-form-line")
            self._hit(942, 262, 286, 32, "swarm-form-triangle")

        c.panel(28, 82, 245, 304, "实机链路")
        c.text(45, 122, 210, 38, gateway_status.message if gateway_status else "蜂群接口未配置", 11, MUTED)
        c.button(45, 170, 96, 35, "断开" if connected else "连接", connected, accent=CYAN)
        self._hit(45, 170, 96, 35, "swarm-disconnect" if connected else "swarm-connect")
        control_label = (
            "正在起飞" if state == "taking_off" else
            "蜂群悬停" if multi_aircraft and armed else
            "启动蜂群" if multi_aircraft else
            "停止移动" if armed and flow_mode else
            "启动 Flow Hover" if flow_mode else
            "停止操控" if armed else "解锁控制"
        )
        taking_off = state == "taking_off"
        c.button(151, 170, 96, 35, control_label, armed,
                 disabled=taking_off, accent=YELLOW)
        if not taking_off:
            self._hit(
                151, 170, 96, 35,
                "swarm-stop-motion" if armed and airborne else
                "swarm-disarm" if armed else "swarm-arm",
            )
        c.button(45, 215, 96, 35, "受控降落", False, accent=GREEN)
        self._hit(45, 215, 96, 35, "swarm-command:land")
        c.button(151, 215, 96, 35, "停止电机", False, accent=RED)
        self._hit(151, 215, 96, 35, "swarm-motor-stop")
        bci_chain_label = (
            "脑控链路：启动中" if self._soldier_bci_start_pending else
            "脑控授权：开" if self.actions.bci_control_enabled else
            "脑控授权：关"
        )
        c.button(45, 260, 202, 37,
                 bci_chain_label,
                 self.actions.bci_control_enabled, accent=RED)
        self._hit(45, 260, 202, 37, "bci-control-toggle")
        c.button(45, 307, 96, 32, "配置 URI", False, accent=CYAN)
        c.button(151, 307, 96, 32, "单机控制", False, accent=YELLOW)
        self._hit(45, 307, 96, 32, "swarm-configure-uris")
        self._hit(151, 307, 96, 32, "swarm-single-control")
        # Readiness belongs to each UAV telemetry item, not SwarmStatus.
        flow_ready = connected and bool(telemetry) and all(item.positioning_ok for item in telemetry)
        lighthouse_count = sum(1 for item in telemetry if item.lighthouse_ready)
        readiness_label = (
            f"Lighthouse {lighthouse_count}/{len(telemetry)} 就绪"
            if telemetry and lighthouse_count else
            "Flow Deck 就绪" if flow_ready else "等待定位质量"
        )
        c.button(45, 349, 202, 32,
                 readiness_label,
                 flow_ready, accent=GREEN if flow_ready else YELLOW)

        # Flow mode streams body-frame speed; formation mode integrates the same
        # stick input into global Lighthouse targets while preserving slot offsets.
        height_cx, height_cy, stick_r, travel = 470.0, 386.0, 100.0, 82.0
        direction_cx, direction_cy = 760.0, 386.0
        c.text(370, 226, 200, 30, "左杆 · 高度升降", 15, TEXT, True, Qt.AlignCenter)
        c.text(650, 226, 220, 30, "右杆 · 前后左右", 15, TEXT, True, Qt.AlignCenter)
        c.circle(height_cx, height_cy, stick_r + 8, QColor(40, 40, 40, 12), CYAN, 2)
        c.circle(height_cx, height_cy, stick_r, "#f0f0f0", "#bcbcbc", 2)
        c.line(height_cx, height_cy - 72, height_cx, height_cy + 72, "#c7c7c7", 3)
        c.text(height_cx - 22, height_cy - 96, 44, 26, "升", 12, GREEN, True, Qt.AlignCenter)
        c.text(height_cx - 22, height_cy + 70, 44, 26, "降", 12, BLUE, True, Qt.AlignCenter)
        height_knob_y = height_cy - self._manual_vertical * travel
        c.circle(height_cx, height_knob_y, 32, "#303030", "#303030", 2)
        c.text(height_cx - 30, height_knob_y - 16, 60, 32,
               f"{self._manual_vertical * 100:+.0f}%", 10, "#ffffff", True, Qt.AlignCenter)
        manual_enabled = connected and armed and airborne and not emergency
        if flow_mode and manual_enabled:
            self._hit(height_cx - stick_r, height_cy - stick_r, stick_r * 2, stick_r * 2,
                      "manual-stick-height")

        c.circle(direction_cx, direction_cy, stick_r + 8, QColor(40, 40, 40, 12), BLUE, 2)
        c.circle(direction_cx, direction_cy, stick_r, "#f0f0f0", "#bcbcbc", 2)
        c.line(direction_cx - 72, direction_cy, direction_cx + 72, direction_cy, "#c7c7c7", 2)
        c.line(direction_cx, direction_cy - 72, direction_cx, direction_cy + 72, "#c7c7c7", 2)
        c.text(direction_cx - 24, direction_cy - 99, 48, 24, "前", 12, GREEN, True, Qt.AlignCenter)
        c.text(direction_cx - 24, direction_cy + 75, 48, 24, "后", 12, BLUE, True, Qt.AlignCenter)
        c.text(direction_cx - 99, direction_cy - 12, 32, 24, "左", 12, YELLOW, True, Qt.AlignCenter)
        c.text(direction_cx + 67, direction_cy - 12, 32, 24, "右", 12, YELLOW, True, Qt.AlignCenter)
        direction_knob_x = direction_cx + self._manual_lateral * travel
        direction_knob_y = direction_cy - self._manual_forward * travel
        c.circle(direction_knob_x, direction_knob_y, 32, "#303030", "#303030", 2)
        if flow_mode and manual_enabled:
            self._hit(direction_cx - stick_r, direction_cy - stick_r, stick_r * 2, stick_r * 2,
                      "manual-stick-direction")
        confidence = (
            self._latest_recognition.confidence
            if eeg_live and self._latest_recognition is not None else 0.0
        )
        if not eeg_live:
            result_label = "等待启动实时 EEG"
        elif self._latest_recognition is None:
            result_label = "等待实时 EEG"
        elif self._latest_recognition.accepted:
            result_label = f"{self._latest_recognition.label}（已接受）"
        else:
            result_label = f"候选{self._latest_recognition.label}（已拒绝）"
        decoder_name = str(getattr(self.intent_decoder, "active_name", "EEG解码"))
        source_name = (
            self._pipeline_worker.source_name
            if self._pipeline_worker is not None
            else str(getattr(self.eeg_stream, "source_name", "未配置"))
        )
        if self._soldier_bci_start_pending:
            source_text = "实时链路：正在连接 ExpandableEEG…"
        elif self._soldier_bci_chain_active:
            source_text = "实时链路：ExpandableEEG → LSL · obci_eeg1 → FBCCA"
        elif source_name.strip().lower().startswith("csv"):
            source_text = f"数据源：{source_name}（回放不能控制实机）"
        elif source_name.strip().lower().startswith(("lsl", "openbci direct", "expandableeeg native")):
            source_text = f"实时数据源：{source_name}"
        else:
            source_text = f"数据源：{source_name}"
        c.text(456, 574, 440, 25, source_text, 10, CYAN if eeg_live else MUTED)
        c.text(456, 603, 280, 30, f"{decoder_name}：{result_label}", 13, MUTED)
        c.line(448, 653, 448 + 350 * max(0.0, min(1.0, confidence)), 653, "#333333", 7)
        c.line(448 + 350 * max(0.0, min(1.0, confidence)), 653, 798, 653, "#dddddd", 6)
        score_name = "FBCCA分数" if "FBCCA" in decoder_name else "置信度"
        c.text(806, 628, 100, 51, f"{score_name}\n{confidence * 100:.1f}%", 14, TEXT, True)
        c.circle(1148, 608, 64, "#151515" if emergency else "#333333", RED, 4)
        c.circle(1148, 608, 73, QColor(40, 40, 40, 15))
        emergency_label = "确认已重启\n复位并断开" if emergency else "紧急停止"
        c.text(1084, 545, 128, 126, emergency_label, 13 if emergency else 19, "#ffffff", True, Qt.AlignCenter)
        self._hit(1075, 535, 146, 146, "swarm-emergency-reset" if emergency else "swarm-emergency-stop")

    def _soldier_status(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-04 / M3：注意力、疲劳、压力和心率应来自
        # SoldierVitalsStream（EEG/EMG/心率/体温/血氧的 LSL 多模态流）。
        # 阈值告警交给独立 AlertService，并同步写入 XDF Marker 和审计日志。
        c.fill("#f5f5f5")
        c.rect(32, 33, 1215, 57, PANEL, BORDER, 3)
        c.rect(32, 33, 81, 4, CYAN)
        c.text(56, 41, 520, 41, "状态监测预览    /    演示数据 · 生命体征接口未接入", 16, TEXT, True)
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
            c.line(98, 441 + i * 19.3, 1208, 441 + i * 19.3, "#e2e2e2", 1, Qt.DashLine)
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
        c.rect(508, 680, 264, 39, "#ebebeb", "#d8d8d8", 3)
        c.text(518, 680, 244, 38, "演示提示：疲劳度偏高，建议休息", 11, YELLOW, True, Qt.AlignCenter)
