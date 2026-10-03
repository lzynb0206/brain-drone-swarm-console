"""Researcher dashboard rendering."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from src.bci.config import SSVEP_PROFILE_NAME, TARGET_FREQUENCIES_HZ
from src.widgets.scifi_canvas import (
    BG, BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class ResearchViewMixin:
    """Render researcher EEG, paradigm, algorithm, control, mapping and data pages."""
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
        # EEG is acquired and decoded in EEGWorker. paintEvent only
        # renders cached values and never blocks on CSV/LSL or performs inference.
        c.panel(17, 73, 824, 305, "EEG 8通道实时波形")
        self._draw_live_eeg(c, 62, 124, 756, 232)

        c.panel(859, 73, 404, 305, "纯 FBCCA 识别结果")
        c.text(879, 116, 360, 25, self._eeg_status, 12, CYAN, True)
        if self._latest_recognition is None:
            c.text(879, 158, 360, 42, "等待积累完整 4 秒 EEG 窗口…", 14, MUTED)
        else:
            result = self._latest_recognition
            prediction = result.label if result.accepted else f"候选：{result.label}"
            c.text(879, 151, 150, 45, prediction, 24, GREEN if result.accepted else YELLOW, True)
            c.text(1040, 157, 195, 30, result.reason, 12, MUTED, False, Qt.AlignRight)
            c.text(879, 195, 350, 25, f"最高分 {result.best_score:.3f}   区分度 {result.margin:.3f}", 12, TEXT)
            for index, (label, score) in enumerate(result.scores.items()):
                y = 228 + index * 25
                c.text(879, y, 70, 20, label, 11, TEXT)
                c.rect(947, y + 5, 225, 9, "#1c2940", None, 4)
                c.rect(947, y + 5, min(225, score / max(0.001, result.best_score) * 225), 9, BLUE, None, 4)
                c.text(1178, y - 2, 55, 22, f"{score:.3f}", 10, MUTED, False, Qt.AlignRight)

        c.panel(17, 398, 824, 304, "采集与试次保存")
        source = self._pipeline_worker.source_name if self._pipeline_worker is not None else "未配置"
        pending = self._pipeline_worker.pending_true_label() if self._pipeline_worker is not None else ""
        stats = self.experiment_recorder.stats() if self.experiment_recorder is not None else {"recognitions": 0, "labeled_trials": 0}
        cards = (
            ("数据源", source, 41),
            ("采样率", f"{self._eeg_sample_rate or 250:.0f} Hz", 305),
            ("已记录识别", str(stats["recognitions"]), 569),
        )
        for title, value, x in cards:
            c.rect(x, 455, 248, 70, "#152239", "#2b3b58", 3)
            c.text(x + 14, 461, 220, 22, title, 11, MUTED)
            c.text(x + 14, 486, 220, 26, value, 14, TEXT, True)
        c.rect(41, 544, 760, 116, "#0c1628", "#2b3b58", 3)
        c.text(58, 553, 300, 28, f"已保存带标签原始试次：{stats['labeled_trials']}", 13, TEXT, True)
        c.text(58, 584, 700, 24, f"待保存真实标签：{pending or '无（点击右上角“添加标签”）'}", 12, CYAN)
        c.text(58, 605, 700, 20, f"会话：{self._experiment_session_id or '未建立'}", 10, MUTED)
        root = str(self.experiment_recorder.root) if self.experiment_recorder is not None else "未配置"
        c.text(58, 625, 720, 24, f"保存目录：{root}", 10, MUTED)
        c.button(58, 665, 198, 27,
                 "停止连续 EEG 录制" if self._continuous_recording else "开始连续 EEG 录制",
                 self._continuous_recording, accent=RED)
        self._hit(58, 662, 198, 34, "continuous-record-toggle")
        c.text(272, 665, 520, 27, "连续数据按 10 秒分块保存，停止时写入最后一块", 10, GREEN)

        c.panel(859, 398, 404, 304, "实时事件")
        if not self._eeg_events:
            c.text(879, 448, 345, 24, "暂无采集事件", 12, MUTED)
        for index, message in enumerate(list(self._eeg_events)[:11]):
            color = RED if "ERROR" in message else TEXT
            c.text(879, 438 + index * 22, 354, 20, message, 10, color)

    def _draw_live_eeg(self, c: Canvas, x: float, y: float, w: float, h: float) -> None:
        histories = [list(values) for values in self._eeg_history]
        if not histories or not any(histories):
            c.text(x, y, w, h, "等待 EEG 数据…", 15, MUTED, False, Qt.AlignCenter)
            return
        palette = ("#ef514d", "#f1c64b", "#62df8a", "#65d9e9", "#6c9bd9", "#987bd8", "#ef7ac8", "#e5e9f0")
        row_height = h / max(1, len(histories))
        c.p.save()
        c.p.setClipRect(QRectF(x, y, w, h))
        for index, values in enumerate(histories):
            baseline = y + row_height * (index + 0.5)
            c.line(x, baseline, x + w, baseline, "#1d2c45")
            c.text(x, baseline - 11, 35, 20, f"CH{index + 1}", 9, MUTED)
            if len(values) < 2:
                continue
            center = sum(values) / len(values)
            amplitude = max(max(abs(value - center) for value in values), 1e-9)
            path = QPainterPath()
            for sample_index, value in enumerate(values):
                px = x + 40 + sample_index / max(1, len(values) - 1) * (w - 42)
                py = baseline - (value - center) / amplitude * row_height * 0.39
                if sample_index == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            c.p.setPen(QPen(QColor(palette[index % len(palette)]), 1.1))
            c.p.setBrush(Qt.NoBrush)
            c.p.drawPath(path)
        c.p.restore()

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
        for index, frequency in enumerate(TARGET_FREQUENCIES_HZ):
            x, width = 656 + index * 63, 58
            c.button(x, 322, width, 42, f"{frequency:g}Hz", self.actions.paradigm_frequency == frequency)
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
        c.button(656, 492, 116, 46, "应用配置", True)
        c.button(783, 492, 116, 46, "恢复默认")
        c.button(910, 492, 130, 46, "全屏刺激", True, accent=CYAN)
        self._hit(656, 492, 116, 46, "paradigm-apply")
        self._hit(783, 492, 116, 46, "paradigm-reset")
        self._hit(910, 492, 130, 46, "paradigm-start")

    def _research_algorithm(self, c: Canvas) -> None:
        # This build intentionally uses only the non-trained FBCCA path.
        c.rect(48, 104, 1184, 69, PANEL, BORDER, 3)
        c.text(66, 116, 92, 40, "当前算法:", 14, TEXT)
        c.button(158, 121, 191, 36, self.actions.algorithm)
        radios = ["固定解码"]
        x = 404
        for label in radios:
            active = self.actions.algorithm_mode == label
            c.circle(x + 9, 139, 7, BLUE if active else None, "#738098", 2)
            c.text(x + 23, 121, 87, 36, label, 13, TEXT)
            self._hit(x, 116, 101, 46, f"algorithm-mode:{label}")
            x += 101
        c.rect(48, 189, 1184, 171, PANEL, BORDER, 3)
        metadata = [("名称:", "纯 FBCCA", 73, 207), ("类型:", "无训练模型", 356, 207),
                    ("数据源:", "CSV / LSL", 640, 207), ("准确率:", "待真实标签评估", 922, 207),
                    ("识别窗:", "4秒", 73, 256), ("更新步长:", "1秒", 356, 256),
                    ("频率配置:", SSVEP_PROFILE_NAME, 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#283752")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["FBCCA"]
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
        # 置信度、二次确认、Flow 质量、电量、解锁和急停状态，
        # 最后才调用 swarm_gateway.send_command。
        panels = [(192, 112, 896, 302), (192, 438, 896, 184), (192, 646, 896, 150)]
        for x, y, w, h in panels:
            c.rect(x, y, w, h, PANEL, BORDER, 3)
        c.text(217, 128, 430, 42, "SSVEP 识别与安全动作映射", 16, TEXT, True)
        headers = [("SSVEP频率", 434), ("映射动作", 577), ("参数", 684), ("操作", 778)]
        for text, x in headers:
            c.text(x, 185, 104, 34, text, 13, TEXT, True, Qt.AlignCenter)
        c.line(433, 216, 847, 216, "#273752")
        for i, row in enumerate(self.actions.mappings[:6]):
            y = 214 + i * 27
            c.text(448, y, 88, 29, row.frequency, 13, TEXT, False, Qt.AlignCenter)
            c.text(564, y, 88, 29, row.action, 13, TEXT, False, Qt.AlignCenter)
            c.text(660, y, 74, 29, row.parameter, 13, TEXT, False, Qt.AlignCenter)
            c.text(742, y, 52, 29, "修改", 13, BLUE, False, Qt.AlignCenter)
            c.text(797, y, 47, 29, "删除", 13, RED, False, Qt.AlignCenter)
            self._hit(742, y, 52, 29, f"mapping-edit:{i}")
            self._hit(797, y, 47, 29, f"mapping-delete:{i}")
            c.line(433, y + 26, 847, y + 26, "#273752")
        c.button(576, 380, 128, 28, "添加映射", True)
        self._hit(576, 380, 128, 28, "mapping-add")
        c.text(217, 454, 360, 42, "Flow Deck 单机验收", 16, TEXT, True)
        acceptance = (
            ("LED 无桨测试", "led_test"),
            ("保持悬停", "hover"),
            ("受控降落", "land"),
        )
        for index, (name, action) in enumerate(acceptance):
            button_x = 310 + index * 190
            c.button(button_x, 510, 170, 42, name)
            self._hit(button_x, 510, 170, 42, f"swarm-command:{action}")
        c.text(235, 575, 830, 23, "连接、Flow Deck 测距/光流、解锁和急停检查通过后才会执行", 11, "#596477", False, Qt.AlignCenter)
        c.text(217, 663, 360, 42, "L3 战术意图 (占位)", 16, TEXT, True)

    def _research_data(self, c: Canvas) -> None:
        # The left list and counts come from the recorder index, not painted demo sessions.
        c.rect(16, 72, 1248, 35, "#0b1425")
        c.text(39, 72, 360, 35, "实验数据工作台", 17, TEXT, True)
        for i, name in enumerate(("数据浏览", "标签管理", "采集说明")):
            x = 543 + i * 106
            c.button(x, 72, 90, 34, name, self.actions.data_tab == name)
            self._hit(x, 72, 90, 34, f"data-tab:{name}")
        c.text(1185, 72, 62, 34, "⌕  ⇧", 20, MUTED, False, Qt.AlignCenter)
        c.rect(32, 123, 254, 597, "#0b1425", BORDER)
        c.text(40, 125, 190, 43, "数据浏览", 14, TEXT, True)
        c.text(260, 125, 20, 43, "×", 16, TEXT, True, Qt.AlignCenter)
        c.line(32, 168, 286, 168, BORDER)
        c.text(40, 169, 210, 38, "实验会话", 13, MUTED)
        trials = self.experiment_recorder.recent_trials(12) if self.experiment_recorder is not None else []
        names = [f"{row.get('timestamp', '')[5:19]}  {row.get('true_label', '')}" for row in trials]
        if not names:
            names = ["暂无带标签原始试次"]
        for i, name in enumerate(names):
            y = 211 + i * 40
            if trials and i == self.actions.selected_session:
                c.rect(41, y, 238, 36, BLUE, None, 3)
            c.text(52, y, 195, 36, name, 13, TEXT)
            if trials:
                c.text(250, y, 20, 36, "›", 18, MUTED, False, Qt.AlignCenter)
                self._hit(41, y, 238, 36, f"data-session:{i}")
        c.rect(304, 123, 944, 597, "#071020", BORDER)
        c.text(317, 124, 420, 44, "当前 EEG 缓存与保存状态", 14, TEXT, True)
        c.text(1203, 124, 34, 44, "⌕×", 18, MUTED, False, Qt.AlignCenter)
        c.line(304, 168, 1248, 168, BORDER)
        self._draw_live_eeg(c, 335, 205, 880, 330)
        stats = self.experiment_recorder.stats() if self.experiment_recorder is not None else {"recognitions": 0, "labeled_trials": 0}
        c.rect(335, 557, 880, 116, "#0c1628", "#263752", 3)
        c.text(355, 567, 840, 27, f"识别日志：{stats['recognitions']} 条    带真实标签试次：{stats['labeled_trials']} 条", 13, TEXT, True)
        c.text(355, 599, 840, 23, "点击右上角“添加标签”后，将重新采集并保存连续 4 秒带标签 EEG。", 12, CYAN)
        log_path = str(self.experiment_recorder.recognition_log_path) if self.experiment_recorder is not None else "未配置"
        c.text(355, 630, 840, 22, f"识别日志：{log_path}", 10, MUTED)

    # ---------- Administrator dashboard ----------


