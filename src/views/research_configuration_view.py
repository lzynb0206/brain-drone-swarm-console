"""Research paradigm, algorithm, collection and data rendering."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt

from src.bci.config import (
    DECISION_STEP_SECONDS,
    SSVEP_PROFILE_NAME,
    TARGET_FREQUENCIES_HZ,
    TRUE_LABELS,
    WINDOW_SECONDS,
)
from src.widgets.scifi_canvas import (
    RESEARCH_BLUE as BLUE,
    RESEARCH_BORDER as BORDER,
    RESEARCH_CYAN as CYAN,
    RESEARCH_GREEN as GREEN,
    RESEARCH_MUTED as MUTED,
    RESEARCH_PANEL as PANEL,
    RESEARCH_PANEL_SOFT as PANEL_SOFT,
    RESEARCH_RED as RED,
    RESEARCH_TEXT as TEXT,
    RESEARCH_YELLOW as YELLOW,
    Canvas,
)


class ResearchConfigurationViewMixin:
    """Render paradigm, decoder, collection and saved-data pages."""

    def _research_paradigm_algorithm(self, c: Canvas) -> None:
        """Render paradigm configuration and the algorithm library as one canvas."""

        self._research_paradigm(c)
        c.text(32, 578, 360, 34, "算法与解码", 19, TEXT, True)
        c.text(32, 608, 560, 22, "选择实时解码算法，并检查模型可用性与安全准入状态。", 10, MUTED)
        c.text(1020, 590, 210, 24, "滚轮向下查看完整算法库 ↓", 9, CYAN, True, Qt.AlignRight)
        c.line(32, 640, 1248, 640, BORDER)

        section_offset = 552.0
        previous_offset = float(getattr(self, "_research_section_y_offset", 0.0))
        c.p.save()
        try:
            c.p.translate(0, section_offset)
            self._research_section_y_offset = section_offset
            self._research_algorithm(c)
        finally:
            self._research_section_y_offset = previous_offset
            c.p.restore()

    def _research_paradigm(self, c: Canvas) -> None:
        # 【真实接口接入点】UI-06 / M4.6：“应用配置”应调用 ParadigmService：
        # 校验 SSVEP 频率/刺激尺寸/占空比，保存到 YAML/JSON，并通知刺激程序；
        # 每次范式开始/结束还应向 LSL 写入 SSVEP_8Hz_start 等事件标记。
        c.text(32, 72, 360, 36, "范式配置", 19, TEXT, True)
        c.text(32, 101, 720, 22, "配置 SSVEP 或 MI 实验范式；采集计划已移至“数据采集”页面。", 10, MUTED)
        c.rect(207, 262, 384, 252, PANEL, BORDER, 4)
        c.rect(232, 287, 47, 202, "#eaf2ff", BLUE, 3)
        c.p.save()
        c.p.translate(255, 388)
        c.p.rotate(90)
        c.text(-55, -17, 110, 34, "SSVEP", 15, CYAN, True, Qt.AlignCenter)
        c.p.restore()
        options = [("SSVEP", 287), ("MI", 361), ("SSVEP+MI混合", 434)]
        for name, y in options:
            active = self.actions.paradigm_type == name
            c.rect(296, y, 270, 57, "#eaf2ff" if active else PANEL, BLUE if active else BORDER, 3)
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
            c.text(656, 278, 380, 24, "当前阶段：参数预览，不生成无人机指令", 12, YELLOW, True)
            for index, label in enumerate(("静息", "想象左手", "想象右手", "想象双脚")):
                x = 656 + (index % 2) * 188
                y = 318 + (index // 2) * 52
                c.rect(x, y, 174, 39, PANEL_SOFT, BORDER, 3)
                c.text(x, y, 174, 39, label, 12, TEXT, True, Qt.AlignCenter)
            c.text(656, 428, 380, 23, "建议关注：C3 / Cz / C4（以实际电极帽标注为准）", 11, CYAN)
            c.text(656, 456, 380, 46, "当前版本未开放 MI 手动带标签采集；\nSSVEP 数据请前往“数据采集”页面。", 11, MUTED)
            c.button(656, 515, 180, 38, "查看 MI 参数", True, accent=CYAN)
            c.text(848, 515, 190, 38, "FBCCA 已停用", 12, GREEN, True, Qt.AlignCenter)
            self._hit(656, 515, 180, 38, "paradigm-start")


    def _research_data_collection(self, c: Canvas) -> None:
        """Dedicated automatic labeled-data collection workspace."""

        config = self.actions.automatic_experiment_config
        ssvep_ready = self.actions.paradigm_type == "SSVEP"
        c.text(32, 72, 360, 36, "实验数据采集", 19, TEXT, True)
        c.text(
            32, 101, 850, 22,
            "独立管理自动随机带标签采集；原始试次、时间戳和质量信息会安全落盘。",
            10, MUTED,
        )
        c.rect(32, 132, 1216, 532, PANEL, BORDER, 5)
        c.text(56, 150, 320, 34, "SSVEP 平衡随机采集计划", 16, TEXT, True)
        status_text = "SSVEP 范式已就绪" if ssvep_ready else "请先在“范式算法”页切换为 SSVEP"
        status_color = GREEN if ssvep_ready else RED
        c.rect(932, 151, 286, 32, "#eaf7ef" if ssvep_ready else "#fff0f2", BORDER, 16)
        c.circle(952, 167, 5, status_color)
        c.text(966, 151, 238, 32, status_text, 9, status_color, True)

        metrics = (
            ("每类次数", f"{config.repetitions_per_label} 次"),
            ("试次总数", str(config.trial_count)),
            ("提示时间", f"{config.cue_seconds:g} s"),
            ("刺激时间", f"{config.stimulus_seconds:g} s"),
            ("分析起点", f"{config.discard_seconds:g} s"),
            ("休息时间", f"{config.rest_seconds:g} s"),
        )
        for index, (label, value) in enumerate(metrics):
            x = 56 + index * 193
            c.rect(x, 208, 176, 76, PANEL_SOFT, BORDER, 4)
            c.text(x + 12, 218, 152, 22, label, 9, MUTED, True)
            c.text(x + 12, 242, 152, 30, value, 16, TEXT, True)

        c.text(56, 310, 220, 26, "真实标签计划", 13, TEXT, True)
        label_width = 150
        for index, label in enumerate(TRUE_LABELS):
            x = 56 + index * 164
            c.rect(x, 346, label_width, 44, "#eef4ff", "#b7cae5", 4)
            c.text(x, 346, label_width, 44, label, 11, BLUE, True, Qt.AlignCenter)

        c.text(56, 420, 220, 26, "安全保存流程", 13, TEXT, True)
        stages = (
            ("1", "实时 EEG 就绪", "固定 8 通道 / 250 Hz"),
            ("2", "按计划采集", "标签来自随机计划，不来自预测"),
            ("3", "逐试次确认保存", "NPZ + 时间戳 + 质量状态"),
        )
        for index, (number, title, description) in enumerate(stages):
            x = 56 + index * 382
            c.rect(x, 456, 354, 84, PANEL_SOFT, BORDER, 4)
            c.circle(x + 29, 498, 16, "#e7f1ff", BLUE)
            c.text(x + 13, 482, 32, 32, number, 10, BLUE, True, Qt.AlignCenter)
            c.text(x + 58, 468, 274, 25, title, 11, TEXT, True)
            c.text(x + 58, 496, 274, 29, description, 9, MUTED)

        c.text(
            56, 562, 620, 24,
            f"随机种子 {config.random_seed} · 每试次可分析 {config.analysis_seconds:g} 秒 · "
            "采集完成后可在“数据”页检查索引",
            9, MUTED,
        )
        self._light_button(c, 836, 574, 176, 54, "配置采集计划", False, "#477af0")
        self._light_button(c, 1024, 574, 194, 54, "开始自动采集", ssvep_ready, "#2d8a57")
        self._hit(836, 574, 176, 54, "automatic-experiment-config")
        if ssvep_ready:
            self._hit(1024, 574, 194, 54, "automatic-experiment-start")

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
        score_calibration_selected = self.actions.algorithm == "个人校准融合"
        selected_name = (
            "Transformer-SSVEP" if transformer_selected else
            "个人模板-SSVEP" if template_selected else "纯 FBCCA"
        )
        if score_calibration_selected:
            selected_name = "双FBCCA分数＋个人LogReg"
        selected_window_seconds = 5.0 if score_calibration_selected else WINDOW_SECONDS
        if selected_window_seconds <= 0.0:
            selected_window_seconds = WINDOW_SECONDS
        status_reader = getattr(self.intent_decoder, "algorithm_statuses", None)
        statuses = status_reader() if callable(status_reader) else {}
        selected_type = (
            "监督训练模型" if transformer_selected else
            "个人多试次模板" if template_selected else
            "个人分数校准" if score_calibration_selected else "无训练模型"
        )
        reported_accuracy = statuses.get("个人校准融合", {}).get("evaluation_accuracy")
        accuracy_text = (
            f"{float(reported_accuracy) * 100:.1f}%（内部）"
            if score_calibration_selected and isinstance(reported_accuracy, (float, int))
            else "待真实标签评估"
        )
        metadata = [("名称:", selected_name, 73, 207), ("类型:", selected_type, 356, 207),
                    ("数据源:", "硬件 / LSL / CSV", 640, 207), ("准确率:", accuracy_text, 922, 207),
                    ("识别窗:", f"{selected_window_seconds:g}秒", 73, 256),
                    ("更新步长:", f"{DECISION_STEP_SECONDS:g}秒", 356, 256),
                    ("频率配置:", SSVEP_PROFILE_NAME, 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#dce3eb")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = ["FBCCA", "个人校准融合", "个人模板-SSVEP", "Transformer-SSVEP"]
        for i, name in enumerate(algorithms):
            x = 73 + i * 176
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            available = bool(statuses.get(name, {}).get("available", name == "FBCCA"))
            c.rect(x, 441, 160, 96, "#eef4ff" if selected else PANEL, BLUE if selected else BORDER, 3, 2 if selected else 1)
            c.text(x, 452, 160, 37, name, 13, TEXT, True, Qt.AlignCenter)
            c.rect(x + 47, 504, 32, 16, GREEN if loaded else "#d3dae3", None, 8)
            c.circle(x + 72 if loaded else x + 56, 512, 5, "#ffffff" if loaded else TEXT)
            c.circle(x + 98, 512, 8, YELLOW if default else "#d3dae3")
            c.circle(x + 124, 512, 7, CYAN if available else RED)
            self._hit(x, 441, 160, 96, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 569, 112), ("卸载算法", 201, 569, 114),
                  ("设为默认", 331, 569, 114), ("性能基准测试", 461, 569, 146)]
        for i, (label, x, y, w) in enumerate(labels):
            c.button(x, y, w, 43, label, i == 0)
            self._hit(x, y, w, 43, ("algorithm-load", "algorithm-unload", "algorithm-default", "algorithm-benchmark")[i])

    def _research_data(self, c: Canvas) -> None:
        browser = self.trial_analysis
        c.text(32, 72, 460, 36, "带标签试次分析工作台", 19, TEXT, True)
        c.text(
            32,
            101,
            850,
            22,
            "导入采集数据，按动作与具体试次筛选，再自由截取时间段并在本页查看分析图表。",
            10,
            MUTED,
        )
        c.rect(32, 132, 1216, 82, PANEL, BORDER, 4)
        c.text(54, 143, 150, 24, "当前数据源", 10, MUTED, True)
        c.text(54, 167, 650, 29, browser.source_summary, 12, TEXT, True)
        self._light_button(c, 822, 150, 126, 46, "载入项目数据", False, BLUE)
        self._light_button(c, 958, 150, 126, 46, "导入目录", False, BLUE)
        self._light_button(c, 1094, 150, 126, 46, "导入试次", False, BLUE)
        self._hit(822, 150, 126, 46, "analysis-load-project")
        self._hit(958, 150, 126, 46, "analysis-import-directory")
        self._hit(1094, 150, 126, 46, "analysis-import-files")

        if not browser.records:
            self._research_data_empty_state(c)
            return

        c.rect(32, 230, 1216, 82, PANEL, BORDER, 4)
        c.text(54, 240, 180, 26, "1  选择动作标签", 12, TEXT, True)
        counts = {
            label: sum(record.true_label == label for record in browser.records)
            for label in browser.labels
        }
        for index, label in enumerate(browser.labels[:9]):
            x = 228 + index * 108
            selected = label == browser.selected_label
            c.button(x, 248, 98, 45, f"{label}  {counts[label]}", selected)
            self._hit(x, 248, 98, 45, f"analysis-label:{label}")

        c.rect(32, 326, 1216, 198, PANEL, BORDER, 4)
        c.text(54, 338, 250, 27, "2  选择该动作的具体试次", 12, TEXT, True)
        c.text(
            895,
            338,
            170,
            27,
            f"第 {browser.page + 1}/{browser.page_count} 页",
            10,
            MUTED,
            False,
            Qt.AlignRight,
        )
        c.button(1077, 336, 64, 30, "上一页", disabled=browser.page <= 0)
        c.button(
            1149,
            336,
            64,
            30,
            "下一页",
            disabled=browser.page >= browser.page_count - 1,
        )
        if browser.page > 0:
            self._hit(1077, 336, 64, 30, "analysis-page-previous")
        if browser.page < browser.page_count - 1:
            self._hit(1149, 336, 64, 30, "analysis-page-next")

        for index, (record_index, record) in enumerate(browser.visible_records):
            column, row = index % 3, index // 3
            x, y = 54 + column * 393, 377 + row * 66
            selected = record_index == browser.selected_record_index
            c.rect(
                x,
                y,
                372,
                56,
                "#eef4ff" if selected else PANEL_SOFT,
                BLUE if selected else BORDER,
                4,
                2 if selected else 1,
            )
            sequence = browser.page * browser.PAGE_SIZE + index + 1
            identity = record.trial_index or f"{sequence:02d}"
            short_id = record.trial_id[-28:]
            c.text(x + 14, y + 4, 338, 25, f"第 {identity} 段 · {short_id}", 10, TEXT, True)
            c.text(
                x + 14,
                y + 29,
                338,
                19,
                f"{record.duration_seconds:.2f} s · {record.sample_rate_hz:g} Hz · "
                f"{record.channel_count} 通道",
                9,
                MUTED,
            )
            self._hit(x, y, 372, 56, f"analysis-trial:{record_index}")

        c.rect(32, 540, 1216, 128, PANEL, BORDER, 4)
        c.text(54, 552, 300, 26, "3  截取时间段并在本页分析", 12, TEXT, True)
        record = browser.selected_record
        if record is not None:
            c.text(
                54,
                583,
                690,
                23,
                f"已选：{record.true_label} · {record.trial_id}",
                11,
                TEXT,
                True,
            )
            c.text(
                54,
                610,
                690,
                22,
                f"分析窗口：{browser.window_start_seconds:.2f}–"
                f"{browser.window_end_seconds:.2f} s（完整试次 {record.duration_seconds:.2f} s）",
                10,
                CYAN,
                True,
            )
            if browser.last_report is not None:
                report = browser.last_report
                c.text(
                    54,
                    637,
                    690,
                    20,
                    f"当前结果：候选 {report.candidate_label} · 分数 {report.confidence:.3f} · "
                    f"可用通道 {report.usable_channel_count}/{report.total_channel_count}",
                    9,
                    GREEN if report.accepted else YELLOW,
                )
        self._light_button(c, 834, 574, 170, 54, "设置时间段", False, BLUE)
        self._light_button(c, 1018, 574, 202, 54, "在本页显示图表", True, "#2d8a57")
        self._hit(834, 574, 170, 54, "analysis-window")
        self._hit(1018, 574, 202, 54, "analysis-generate")

        pixmap = browser.report_pixmap
        if pixmap is None or pixmap.isNull():
            return
        report_width = 1184.0
        report_height = browser.report_display_height(report_width)
        c.text(32, 692, 360, 34, "当前试次分析结果", 18, TEXT, True)
        c.text(
            760,
            696,
            468,
            25,
            "保持原六联图格式 · 使用滚轮向下查看完整内容 ↓",
            10,
            CYAN,
            True,
            Qt.AlignRight,
        )
        c.rect(32, 732, 1216, report_height + 32, PANEL, BORDER, 5)
        c.p.drawPixmap(
            QRectF(48, 748, report_width, report_height),
            pixmap,
            QRectF(pixmap.rect()),
        )

    def _research_data_max_scroll(self) -> float:
        report_height = self.trial_analysis.report_display_height(1184.0)
        if report_height <= 0:
            return 0.0
        content_bottom = 748.0 + report_height + 16.0
        return max(0.0, content_bottom - 684.0)

    def _research_data_empty_state(self, c: Canvas) -> None:
        c.rect(32, 230, 1216, 438, PANEL, BORDER, 5)
        c.text(430, 267, 420, 42, "导入后按三步完成分析", 18, TEXT, True, Qt.AlignCenter)
        steps = (
            ("1", "选择动作", "例如起飞、右移或降落"),
            ("2", "选择具体试次", "同一动作的每一段都可单独选择"),
            ("3", "截取并分析", "自由设置起止时间，本页显示六联图"),
        )
        for index, (number, title, description) in enumerate(steps):
            x = 112 + index * 374
            c.rect(x, 348, 330, 134, PANEL_SOFT, BORDER, 5)
            c.circle(x + 40, 389, 18, "#e7f1ff", BLUE)
            c.text(x + 22, 371, 36, 36, number, 11, BLUE, True, Qt.AlignCenter)
            c.text(x + 72, 360, 226, 38, title, 14, TEXT, True)
            c.text(x + 72, 400, 226, 44, description, 10, MUTED)
        c.text(
            180,
            518,
            920,
            56,
            "支持项目采集产生的带标签 NPZ：真实标签、原始 EEG、采样率和通道名都会被读取。\n"
            "页面内容包括原始波形、通道质量表、阈前/陷波后 FFT、滤波子带和六类 FBCCA 分数。",
            11,
            MUTED,
            False,
            Qt.AlignCenter,
        )

    # ---------- Administrator dashboard ----------


__all__ = ["ResearchConfigurationViewMixin"]
