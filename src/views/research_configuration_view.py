"""Research paradigm, algorithm, controller, mapping and data rendering."""

from __future__ import annotations

from PySide6.QtCore import Qt

from src.bci.config import (
    DECISION_STEP_SECONDS,
    SSVEP_PROFILE_NAME,
    TARGET_FREQUENCIES_HZ,
    WINDOW_SECONDS,
)
from src.widgets.scifi_canvas import (
    BLUE, BORDER, CYAN, GREEN, MUTED, PANEL, RED, TEXT, YELLOW, Canvas,
)


class ResearchConfigurationViewMixin:
    """Render paradigm, decoder, controller, mapping and saved-data pages."""
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
        if self.actions.paradigm_type == "SSVEP":
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
        else:
            c.text(656, 229, 380, 42, "运动想象（MI）采集配置", 19, TEXT, True)
            c.text(656, 278, 380, 24, "当前阶段：采集与标注，不生成无人机指令", 12, YELLOW, True)
            for index, label in enumerate(("静息", "想象左手", "想象右手", "想象双脚")):
                x = 656 + (index % 2) * 188
                y = 318 + (index // 2) * 52
                c.rect(x, y, 174, 39, "#152239", "#2b3b58", 3)
                c.text(x, y, 174, 39, label, 12, TEXT, True, Qt.AlignCenter)
            c.text(656, 428, 380, 23, "建议关注：C3 / Cz / C4（以实际电极帽标注为准）", 11, CYAN)
            c.text(656, 456, 380, 46, "操作：连接 EEG 采集 → 检查信号质量 →\n点击右上角“添加标签” → 完成 4 秒想象", 11, MUTED)
            c.button(656, 515, 180, 38, "启用 MI 采集模式", True, accent=CYAN)
            c.text(848, 515, 190, 38, "FBCCA 已停用", 12, GREEN, True, Qt.AlignCenter)
            self._hit(656, 515, 180, 38, "paradigm-start")

        if self.actions.paradigm_type == "SSVEP":
            config = self.actions.automatic_experiment_config
            c.rect(48, 588, 1184, 105, "#0b1728", BORDER, 4)
            c.text(66, 596, 276, 28, "自动随机带标签采集", 14, TEXT, True)
            c.text(66, 625, 276, 23, "空闲 + 六动作 · 分块平衡随机", 9, CYAN, True)
            c.text(
                66, 650, 276, 24,
                "实时 EEG 就绪后开始；标签来自试验计划",
                8, MUTED,
            )
            metrics = (
                ("每类", f"{config.repetitions_per_label} 次"),
                ("总数", f"{config.trial_count}"),
                ("提示", f"{config.cue_seconds:g}s"),
                ("刺激", f"{config.stimulus_seconds:g}s"),
                ("丢弃", f"{config.discard_seconds:g}s"),
                ("休息", f"{config.rest_seconds:g}s"),
            )
            for index, (label, value) in enumerate(metrics):
                x = 358 + index * 89
                c.text(x, 601, 78, 18, label, 8, MUTED, True, Qt.AlignCenter)
                c.rect(x, 623, 78, 34, "#111c31", BORDER, 2)
                c.text(x, 623, 78, 34, value, 10, TEXT, True, Qt.AlignCenter)
            c.text(
                358, 663, 523, 18,
                f"随机种子 {config.random_seed} · 可分析 {config.analysis_seconds:g}s/试次",
                8, MUTED, False, Qt.AlignCenter,
            )
            self._light_button(c, 902, 610, 142, 54, "配置自动实验", False, "#477af0")
            self._light_button(c, 1058, 610, 150, 54, "开始自动采集", True, "#2d8a57")
            self._hit(902, 610, 142, 54, "automatic-experiment-config")
            self._hit(1058, 610, 150, 54, "automatic-experiment-start")

    def _research_algorithm(self, c: Canvas) -> None:
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
        transformer_selected = self.actions.algorithm == "Transformer-SSVEP"
        template_selected = self.actions.algorithm == "个人模板-SSVEP"
        selected_name = (
            "Transformer-SSVEP" if transformer_selected else
            "个人模板-SSVEP" if template_selected else "纯 FBCCA"
        )
        selected_type = (
            "监督训练模型" if transformer_selected else
            "个人多试次模板" if template_selected else "无训练模型"
        )
        metadata = [("名称:", selected_name, 73, 207), ("类型:", selected_type, 356, 207),
                    ("数据源:", "硬件 / LSL / CSV", 640, 207), ("准确率:", "待真实标签评估", 922, 207),
                    ("识别窗:", f"{WINDOW_SECONDS:g}秒", 73, 256),
                    ("更新步长:", f"{DECISION_STEP_SECONDS:g}秒", 356, 256),
                    ("频率配置:", SSVEP_PROFILE_NAME, 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#283752")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["FBCCA", "个人模板-SSVEP", "Transformer-SSVEP"]
        status_reader = getattr(self.intent_decoder, "algorithm_statuses", None)
        statuses = status_reader() if callable(status_reader) else {}
        for i, name in enumerate(algorithms):
            x = 73 + i * 176
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            available = bool(statuses.get(name, {}).get("available", name == "FBCCA"))
            c.rect(x, 441, 160, 96, "#17243a", BLUE if selected else "#2b3a55", 3, 2 if selected else 1)
            c.text(x, 452, 160, 37, name, 13, TEXT, True, Qt.AlignCenter)
            c.rect(x + 47, 504, 32, 16, GREEN if loaded else "#526078", None, 8)
            c.circle(x + 72 if loaded else x + 56, 512, 5, TEXT)
            c.circle(x + 98, 512, 8, YELLOW if default else "#2b3954")
            c.circle(x + 124, 512, 7, CYAN if available else RED)
            self._hit(x, 441, 160, 96, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 569, 112), ("卸载算法", 201, 569, 114),
                  ("设为默认", 331, 569, 114), ("性能基准测试", 461, 569, 146)]
        for i, (label, x, y, w) in enumerate(labels):
            c.button(x, y, w, 43, label, i == 0)
            self._hit(x, y, w, 43, ("algorithm-load", "algorithm-unload", "algorithm-default", "algorithm-benchmark")[i])

    def _research_controller(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-08 / M6.5：滑块和本地应用动作已可操作；正式版
        # ControllerService 还需将 Kp/Ki/Kd 校验后交给实现 BaseController 的
        # PID/MPC 控制器；参数写入 YAML，并保留版本和操作者审计记录。
        c.text(32, 72, 320, 36, "控制器参数工作台", 19, TEXT, True)
        c.text(32, 101, 640, 22, "统一管理飞行控制器、参数预设与切换策略。", 10, MUTED)
        c.rect(32, 132, 1216, 72, PANEL, BORDER, 3)
        c.text(52, 148, 106, 40, "当前控制器", 12, MUTED)
        c.button(164, 150, 128, 36, self.actions.controller)
        modes = [("● 手动选择", True), ("□ 场景自动匹配", False), ("□ 自适应切换", False), ("□ 双控制器热备", False)]
        x = 348
        for label, active in modes:
            c.button(x, 171, 126, 31, label, False, not active)
            x += 133
        c.rect(32, 220, 1216, 296, PANEL, BORDER, 3)
        c.text(56, 236, 300, 40, f"{self.actions.controller} 参数配置", 16, TEXT, True)
        for label, y in (("Kp", 310), ("Ki", 370), ("Kd", 430)):
            value = self.actions.controller_values[label]
            end = 203 + 707 * value / 100
            c.text(145, y - 18, 45, 35, label, 13, TEXT)
            c.line(203, y, 910, y, "#6d6f70", 7)
            c.line(203, y, end, y, "#eeeeee", 7)
            c.circle(end, y, 8, "#f2f2f2")
            c.text(932, y - 17, 74, 34, f"{value / 100:.2f}", 13, TEXT)
            self._hit(196, y - 14, 721, 28, f"controller-slider:{label}")
        for i, (label, action) in enumerate((("应用参数", "controller-apply"), ("保存预设", "controller-save"), ("恢复默认", "controller-reset"))):
            x = 486 + i * 137
            c.button(x, 468, 121, 34, label, i == 0)
            self._hit(x, 468, 121, 34, action)
        c.rect(32, 532, 1216, 164, PANEL, BORDER, 3)
        c.text(56, 545, 200, 42, "控制器库", 16, TEXT, True)
        controllers = ("PID", "MPC", "新增", "滑模控制", "鲁棒控制", "自适应控制")
        for i, name in enumerate(controllers):
            x = 56 + i * 144
            disabled = name == "新增"
            c.button(x, 622, 120, 42, name, name == self.actions.controller, disabled, "#69c77a")
            if not disabled:
                self._hit(x, 622, 120, 42, f"controller-select:{name}")

    def _research_mapping(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-09 / M4.5：映射表应来自 CommandMappingRepository。
        # 解码 label/频率先经本映射转换为 SwarmCommand，再由安全仲裁器检查
        # 置信度、二次确认、Flow 质量、电量、解锁和急停状态，
        # 最后才调用 swarm_gateway.send_command。
        c.text(32, 72, 320, 36, "识别与动作映射", 19, TEXT, True)
        c.text(32, 101, 640, 22, "将脑电识别结果转换为经过安全仲裁的飞行动作。", 10, MUTED)
        panels = [(32, 132, 1216, 276), (32, 424, 1216, 156), (32, 596, 1216, 100)]
        for x, y, w, h in panels:
            c.rect(x, y, w, h, PANEL, BORDER, 3)
        c.text(56, 143, 430, 42, "SSVEP 识别与安全动作映射", 16, TEXT, True)
        headers = [("SSVEP频率", 434), ("映射动作", 577), ("参数", 684), ("操作", 778)]
        for text, x in headers:
            c.text(x, 181, 104, 30, text, 12, TEXT, True, Qt.AlignCenter)
        c.line(433, 210, 847, 210, "#273752")
        for i, row in enumerate(self.actions.mappings[:6]):
            y = 209 + i * 27
            c.text(448, y, 88, 29, row.frequency, 13, TEXT, False, Qt.AlignCenter)
            c.text(564, y, 88, 29, row.action, 13, TEXT, False, Qt.AlignCenter)
            c.text(660, y, 74, 29, row.parameter, 13, TEXT, False, Qt.AlignCenter)
            c.text(742, y, 52, 29, "修改", 13, BLUE, False, Qt.AlignCenter)
            c.text(797, y, 47, 29, "删除", 13, RED, False, Qt.AlignCenter)
            self._hit(742, y, 52, 29, f"mapping-edit:{i}")
            self._hit(797, y, 47, 29, f"mapping-delete:{i}")
            c.line(433, y + 26, 847, y + 26, "#273752")
        c.button(576, 371, 128, 28, "添加映射", True)
        self._hit(576, 371, 128, 28, "mapping-add")
        c.text(56, 436, 360, 42, "Flow Deck 单机验收", 16, TEXT, True)
        acceptance = (
            ("LED 无桨测试", "led_test"),
            ("保持悬停", "hover"),
            ("受控降落", "land"),
        )
        for index, (name, action) in enumerate(acceptance):
            button_x = 310 + index * 220
            c.button(button_x, 480, 190, 40, name)
            self._hit(button_x, 480, 190, 40, f"swarm-command:{action}")
        c.text(200, 538, 880, 23, "连接、Flow Deck 测距/光流、解锁和急停检查通过后才会执行", 10, MUTED, False, Qt.AlignCenter)
        c.text(56, 606, 360, 32, "L3 战术意图", 15, TEXT, True)
        c.text(56, 640, 850, 28, "预留更高层战术意图映射；当前版本仅开放经过单机安全验收的动作。", 10, MUTED)
        c.rect(1064, 620, 150, 34, "#0b1728", BORDER, 17)
        c.text(1064, 620, 150, 34, "规划中", 10, YELLOW, True, Qt.AlignCenter)

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
        c.text(355, 599, 840, 23, "添加标签后保存：SSVEP连续2秒；MI连续4秒。", 12, CYAN)
        log_path = str(self.experiment_recorder.recognition_log_path) if self.experiment_recorder is not None else "未配置"
        c.text(355, 630, 840, 22, f"识别日志：{log_path}", 10, MUTED)

    # ---------- Administrator dashboard ----------


__all__ = ["ResearchConfigurationViewMixin"]
