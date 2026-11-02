"""Research paradigm, algorithm, collection and data rendering."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from src.bci.montage import HYBRID, LEGACY
from src.services.collection_plans import (
    COLLECTION_PARADIGMS, collection_schemes, hybrid_collection_config,
)

from src.bci.config import (
    DECISION_STEP_SECONDS,
    SSVEP_PROFILE_NAME,
    TARGET_FREQUENCIES_HZ,
    COMMANDS,
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
            c.text(656, 229, 380, 42, "MI / 串行配对采集", 19, TEXT, True)
            ready = self.actions.montage_id == HYBRID.id
            c.text(656, 278, 380, 24, "已选 5+3 布局" if ready else "先停流、调整电极，再应用 5+3 布局", 11, GREEN if ready else YELLOW, True)
            c.text(656, 316, 380, 30, "CH1–5：O1 / Oz / O2 / P3 / P4", 12, TEXT, True)
            c.text(656, 350, 380, 30, "CH6–8：C3 / Cz / C4", 12, TEXT, True)
            c.text(656, 392, 380, 48, "SSVEP 自动解码并保存置信度；MI 保存原始脑电。\n两个阶段按同一配对编号关联；试采检查在数据采集页。", 10, MUTED)
            c.button(656, 452, 180, 38, "应用候选 5+3 布局", ready, accent=CYAN)
            self._hit(656, 452, 180, 38, f"montage:{HYBRID.id}")
            c.button(848, 452, 180, 38, "恢复枕顶 8 通道")
            self._hit(848, 452, 180, 38, "montage:posterior8_v1")
            c.button(656, 510, 180, 38, "配置采集计划")
            self._hit(656, 510, 180, 38, "hybrid-experiment-config")
            c.button(848, 510, 180, 38, "开始科研采集", True, accent=CYAN)
            self._hit(848, 510, 180, 38, "hybrid-experiment-start")


    def _research_data_collection(self, c: Canvas) -> None:
        """One workspace for paradigm selection, plan preview and acquisition."""
        paradigm = self.actions.paradigm_type
        schemes = collection_schemes(paradigm)
        scheme = next(item for item in schemes if item.id == self.actions.collection_scheme)
        ssvep = paradigm == "SSVEP"
        config = (self.actions.automatic_experiment_config if ssvep else
                  hybrid_collection_config(self.actions.hybrid_experiment_config, paradigm, scheme.id))
        montage = LEGACY if ssvep else HYBRID
        layout_ready = (self.actions.montage_id == montage.id
                        and tuple(self._eeg_channel_names) == montage.names)
        running = self.experiment.running

        c.text(32, 72, 420, 34, "实验数据采集", 20, TEXT, True)
        c.text(32, 108, 1160, 22, "选择范式与实验方案，核对参数和接线后开始采集；原始 EEG、真实标签与时间戳逐段保存。", 10, MUTED)
        for index, (value, title) in enumerate(COLLECTION_PARADIGMS):
            x = 648 + index * 204
            active = paradigm == value
            c.rect(x, 76, 192, 34, "#eaf2ff" if active else PANEL, BLUE if active else BORDER, 4)
            c.text(x, 76, 192, 34, title, 11, BLUE if active else MUTED, active, Qt.AlignCenter)
            if not running:
                self._hit(x, 76, 192, 34, f"paradigm-type:{value}")

        c.rect(32, 144, 1216, 98, PANEL, BORDER, 5)
        c.text(52, 152, 240, 22, "实验方案", 11, TEXT, True)
        c.text(858, 152, 370, 22, "采集中 · 结束后可切换方案" if running else "切换范式会保留已配置的采集参数", 9, MUTED, False, Qt.AlignRight)
        width = (1176 - 12 * (len(schemes) - 1)) / len(schemes)
        for index, item in enumerate(schemes):
            x = 52 + index * (width + 12)
            active = scheme.id == item.id
            c.rect(x, 180, width, 46, "#eef4ff" if active else PANEL_SOFT, BLUE if active else BORDER, 4)
            c.circle(x + 18, 203, 5, BLUE if active else PANEL, BLUE if active else BORDER)
            c.text(x + 34, 182, width - 48, 22, item.title, 11, BLUE if active else TEXT, True)
            c.text(x + 34, 204, width - 48, 18, item.description, 9, MUTED)
            if not running:
                self._hit(x, 180, width, 46, f"collection-scheme:{item.id}")

        c.rect(32, 256, 1216, 440, PANEL, BORDER, 5)
        name = "SSVEP" if ssvep else "MI" if paradigm == "MI" else "SSVEP → MI"
        c.text(56, 268, 820, 30, f"{name} · {scheme.title}", 16, TEXT, True)
        status, color = self._collection_readiness(config, layout_ready, ssvep)
        c.rect(930, 268, 294, 30, "#eaf7ef" if color == GREEN else PANEL_SOFT, BORDER, 15)
        c.circle(948, 283, 5, color)
        c.text(962, 268, 248, 30, status, 9, color, True)

        metrics, labels, flow, preparation, saving = self._collection_plan_details(config, ssvep)
        for index, (label, value) in enumerate(metrics):
            x = 56 + index * 196
            c.rect(x, 312, 184, 64, PANEL_SOFT, BORDER, 4)
            c.text(x + 12, 320, 160, 20, label, 9, MUTED, True)
            c.text(x + 12, 343, 160, 25, value, 16, TEXT, True)

        c.text(56, 386, 220, 22, "采集标签" if not ssvep and config.mode == "rest" else "真实标签计划", 12, TEXT, True)
        label_width = (1164 - 12 * (len(labels) - 1)) / len(labels)
        for index, label in enumerate(labels):
            x = 56 + index * (label_width + 12)
            c.rect(x, 416, label_width, 32, "#eef4ff", "#b7cae5", 4)
            c.text(x, 416, label_width, 32, label, 11, BLUE, True, Qt.AlignCenter)

        c.text(56, 460, 220, 22, "实验流程", 12, TEXT, True)
        c.text(56, 486, 1164, 24, flow, 12, BLUE, True)
        c.text(56, 514, 1164, 22, preparation, 10, MUTED)
        c.text(56, 540, 1164, 22, saving, 10, MUTED)
        c.line(56, 576, 1224, 576, BORDER)
        wiring = "   ".join(f"CH{i + 1} {name}" for i, name in enumerate(montage.names))
        c.text(56, 584, 1164, 22, f"所需接线  {wiring}   ·   250 Hz", 11, TEXT, True)
        note = "更换布局前请先停止数据流并调整物理电极。" if not layout_ready else "当前通道布局匹配；请核对物理电极与通道顺序。"
        c.text(56, 610, 1164, 20, f"{note}  随机种子 {config.random_seed} · 完成后在“数据分析”页查看。", 9, MUTED)

        buttons = [
            (56, 144, "查看实时采集", "research:采集", False),
            (212, 176, "恢复枕顶 8 通道" if ssvep else "应用候选 5+3 布局", f"montage:{montage.id}", False),
            (852, 172, "配置采集计划", "collection-config", False),
            (1036, 188, "返回采集窗口" if running else scheme.start_label, "collection-start", True),
        ]
        if not ssvep:
            buttons.insert(2, (400, 204, "硬件／时间检查（约 6 秒）", "hybrid-preflight", False))
        for x, button_width, label, action, primary in buttons:
            enabled = not running or action in ("research:采集", "collection-start")
            self._light_button(c, x, 642, button_width, 36, label, primary, GREEN if primary else BLUE if enabled else MUTED)
            if enabled:
                self._hit(x, 642, button_width, 36, action)

    def _collection_readiness(self, config, layout_ready, ssvep):
        if self.experiment.running:
            return "正在采集 · 当前方案已锁定", GREEN
        if not layout_ready:
            return "待应用枕顶 8 通道布局" if ssvep else "待应用 5+3 通道布局", YELLOW
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            return "待启动实时 EEG 数据流", YELLOW
        if ssvep:
            return "数据流已启动 · 启动时校验信号", GREEN
        if not self.experiment.preflight_ready():
            return "待完成硬件／时间检查", YELLOW
        if config.mode != "rest" and config.purpose != "pilot":
            audit = self.experiment.pilot_audit
            if not audit or not audit.get("passed") or audit.get("signature") != self.experiment.pilot_signature(config):
                return "待完成当前范式的 2 组试跑", YELLOW
        return "采集检查已通过", GREEN

    def _collection_plan_details(self, config, ssvep):
        """Use the same effective config that will be passed to the runner."""
        if ssvep:
            metrics = (
                ("每类次数", f"{config.repetitions_per_label} 次"), ("试次总数", str(config.trial_count)),
                ("提示时间", f"{config.cue_seconds:g} s"), ("刺激时间", f"{config.stimulus_seconds:g} s"),
                ("分析起点", f"{config.discard_seconds:g} s"), ("休息时间", f"{config.rest_seconds:g} s"),
            )
            flow = f"提示 {config.cue_seconds:g}s  →  SSVEP {config.stimulus_seconds:g}s  →  确认保存  →  休息 {config.rest_seconds:g}s"
            preparation = f"每个重复块覆盖全部标签；每试次可分析 {config.analysis_seconds:g}s，真实标签来自随机计划。"
            saving = "逐试次保存原始 EEG、通道名、时间戳、真实标签和质量状态。"
            return metrics, config.labels, flow, preparation, saving
        if config.mode == "rest":
            metrics = (
                ("采集段数", "2 段"), ("静息总时长", f"{config.resting_open_seconds + config.resting_closed_seconds:g} s"),
                ("睁眼静息", f"{config.resting_open_seconds:g} s"), ("闭眼静息", f"{config.resting_closed_seconds:g} s"),
                ("每段提示", "5 s"), ("任务试次", "0"),
            )
            flow = f"睁眼提示 5s  →  睁眼静息 {config.resting_open_seconds:g}s  →  闭眼提示 5s  →  闭眼静息 {config.resting_closed_seconds:g}s  →  睁眼确认"
            return (metrics, ("睁眼静息", "闭眼静息"), flow,
                    "闭眼静息结束后，按空格确认已经睁眼；本方案不采集 SSVEP / MI 任务。",
                    "睁眼、闭眼分段保存原始 EEG、眼状态与时间戳，单独标记为专门静息。")
        serial = config.mode == "serial"
        if serial:
            metrics = (
                ("每动作次数" if config.purpose == "pilot" else "每组合次数", f"{config.repetitions} 次"), ("任务组数", f"{config.pair_count} 组"),
                ("SSVEP 时间", f"{config.ssvep_seconds:g} s"), ("阶段间隔", f"{config.gap_seconds:g} s"),
                ("MI 时间", f"{config.mi_seconds:g} s"), ("休息时间", f"{config.rest_seconds:g} s"),
            )
            # A serial formal run also inserts an idle window between blocks.
            idle_count = config.idle_trials + (config.repetitions - 1 if config.purpose == "formal" else 0)
            labels = tuple(COMMANDS[:1] if config.purpose == "pilot" else COMMANDS) + tuple(config.mi_labels)
            flow = f"提示 {config.cue_seconds:g}s  →  SSVEP {config.ssvep_seconds:g}s  →  间隔 {config.gap_seconds:g}s  →  MI {config.mi_seconds:g}s  →  休息 {config.rest_seconds:g}s"
            saving = f"另采 {idle_count} 个空闲窗；逐阶段保存 EEG、配对编号及 SSVEP 置信度，低置信度仍继续采 MI。"
        else:
            metrics = (
                ("每类次数", f"{config.repetitions} 次"), ("任务组数", f"{config.pair_count} 组"),
                ("提示时间", f"{config.cue_seconds:g} s"), ("MI 时间", f"{config.mi_seconds:g} s"),
                ("短基线", f"{config.baseline_seconds:g} s"), ("休息时间", f"{config.rest_seconds:g} s"),
            )
            labels = config.mi_labels
            flow = f"提示 {config.cue_seconds:g}s  →  MI {config.mi_seconds:g}s（中央十字）  →  确认保存  →  休息 {config.rest_seconds:g}s"
            saving = "采集全 8 通道，MI 分析使用 C3 / Cz / C4；逐段保存原始 EEG、真实标签、时间戳及质量状态。"
        preparation = (f"先采睁眼 {config.resting_open_seconds:g}s + 闭眼 {config.resting_closed_seconds:g}s 专门静息，再采 {config.baseline_seconds:g}s 短基线。"
                       if config.include_dedicated_rest else f"先采 {config.baseline_seconds:g}s 短基线；本轮不包含专门静息。")
        if config.purpose == "pilot":
            preparation += "  试跑仅 2 组，数据单独标记，不进入正式分析。"
        return metrics, labels, flow, preparation, saving

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
        lzy_selected = self.actions.algorithm == "个人算法-Lzy"
        xz_selected = self.actions.algorithm == "个人算法-Xz"
        bn_selected = self.actions.algorithm in ("BN-FBCCA-A", "BN-FBCCA-B")
        selected_name = (
            self.actions.algorithm if bn_selected else
            "Transformer-SSVEP" if transformer_selected else
            "个人模板-SSVEP" if template_selected else
            "Xz个人双FBCCA校准" if xz_selected else
            "Lzy重叠窗个人校准" if lzy_selected else "纯 FBCCA"
        )
        if score_calibration_selected:
            selected_name = "双FBCCA分数＋个人LogReg"
        selected_window_seconds = 5.0 if (score_calibration_selected or lzy_selected or xz_selected) else WINDOW_SECONDS
        if selected_window_seconds <= 0.0:
            selected_window_seconds = WINDOW_SECONDS
        status_reader = getattr(self.intent_decoder, "algorithm_statuses", None)
        statuses = status_reader() if callable(status_reader) else {}
        selected_type = (
            "空闲背景校准" if self.actions.algorithm == "BN-FBCCA-B" else
            "监督训练模型" if transformer_selected else
            "个人多试次模板" if template_selected else
            "个人非目标标定" if lzy_selected else
            "个人分数校准" if (score_calibration_selected or xz_selected) else "无训练模型"
        )
        status_name = (
            "个人算法-Xz" if xz_selected else
            "个人算法-Lzy" if lzy_selected else "个人校准融合"
        )
        reported_accuracy = statuses.get(status_name, {}).get("evaluation_accuracy")
        accuracy_text = (
            f"{float(reported_accuracy) * 100:.1f}%（内部）"
            if (score_calibration_selected or lzy_selected or xz_selected) and isinstance(reported_accuracy, (float, int))
            else "待真实标签评估"
        )
        metadata = [("名称:", selected_name, 73, 207), ("类型:", selected_type, 356, 207),
                    ("数据源:", "硬件 / LSL / CSV", 640, 207), ("准确率:", accuracy_text, 922, 207),
                    ("识别窗:", f"{selected_window_seconds:g}秒", 73, 256),
                    ("更新步长:", f"{DECISION_STEP_SECONDS:g}秒", 356, 256),
                    ("频率配置:", f"{min(TARGET_FREQUENCIES_HZ):g}–{max(TARGET_FREQUENCIES_HZ):g} Hz · {len(TARGET_FREQUENCIES_HZ)}类", 640, 256)]
        for label, value, x, y in metadata:
            c.text(x, y, 86, 38, label, 14, TEXT)
            c.text(x + 50 if len(label) <= 3 else x + 80, y, 145, 38, value, 14, TEXT, True)
        c.line(73, 310, 1207, 310, "#dce3eb")
        c.rect(48, 376, 1184, 295, PANEL, BORDER, 3)
        c.text(73, 394, 200, 37, "算法库", 17, TEXT, True)
        algorithms = [
            "FBCCA", "BN-FBCCA-A", "BN-FBCCA-B", "个人算法-Lzy", "个人算法-Xz",
            "个人校准融合", "个人模板-SSVEP", "Transformer-SSVEP",
        ]
        for i, name in enumerate(algorithms):
            x = 73 + (i % 4) * 284
            y = 438 + (i // 4) * 80
            selected = name == self.actions.algorithm
            loaded = name in self.actions.loaded_algorithms
            default = name == self.actions.default_algorithm
            available = bool(statuses.get(name, {}).get("available", name == "FBCCA"))
            c.rect(x, y, 260, 70, "#eef4ff" if selected else PANEL, BLUE if selected else BORDER, 3, 2 if selected else 1)
            c.text(x, y + 3, 260, 32, name, 13, TEXT, True, Qt.AlignCenter)
            c.rect(x + 92, y + 46, 32, 16, GREEN if loaded else "#d3dae3", None, 8)
            c.circle(x + 117 if loaded else x + 101, y + 54, 5, "#ffffff" if loaded else TEXT)
            c.circle(x + 143, y + 54, 8, YELLOW if default else "#d3dae3")
            c.circle(x + 169, y + 54, 7, CYAN if available else RED)
            self._hit(x, y, 260, 70, f"algorithm-select:{name}")
        labels = [("加载算法", 73, 610, 112), ("卸载算法", 201, 610, 114),
                  ("设为默认", 331, 610, 114), ("性能基准测试", 461, 610, 146)]
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
            "按受试者、采集日期、动作和具体试次逐级筛选；紧邻采集的不同人员不会再被混合。",
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

        c.rect(32, 230, 1216, 112, PANEL, BORDER, 4)
        c.text(54, 240, 230, 27, "1  选择受试者", 12, TEXT, True)
        selected_subject = browser.selected_subject
        if selected_subject is not None:
            c.text(
                256,
                240,
                520,
                27,
                f"当前：{selected_subject.display_name} · {selected_subject.record_count} 个试次 · "
                f"{selected_subject.date_count} 个采集日",
                10,
                CYAN,
                True,
            )
        self._light_button(c, 820, 237, 150, 31, "编辑受试者名称", False, BLUE)
        self._hit(820, 237, 150, 31, "analysis-subject-rename")
        c.text(
            972,
            239,
            96,
            27,
            f"{browser.subject_page + 1}/{browser.subject_page_count} 页",
            9,
            MUTED,
            False,
            Qt.AlignRight,
        )
        c.button(1077, 237, 64, 30, "上一页", disabled=browser.subject_page <= 0)
        c.button(
            1149,
            237,
            64,
            30,
            "下一页",
            disabled=browser.subject_page >= browser.subject_page_count - 1,
        )
        if browser.subject_page > 0:
            self._hit(1077, 237, 64, 30, "analysis-subject-page-previous")
        if browser.subject_page < browser.subject_page_count - 1:
            self._hit(1149, 237, 64, 30, "analysis-subject-page-next")
        for index, group in enumerate(browser.visible_subjects):
            x, y = 54 + index * 290, 278
            selected = group.subject_id == browser.selected_subject_id
            c.rect(
                x,
                y,
                274,
                50,
                "#eef4ff" if selected else PANEL_SOFT,
                BLUE if selected else BORDER,
                4,
                2 if selected else 1,
            )
            c.text(x + 12, y + 2, 250, 24, group.display_name, 10, TEXT, True)
            c.text(
                x + 12,
                y + 25,
                250,
                18,
                f"{group.date_count} 个日期 · {group.record_count} 个试次 · {group.subject_id}",
                8,
                MUTED,
            )
            self._hit(x, y, 274, 50, f"analysis-subject:{group.subject_id}")

        c.rect(32, 356, 1216, 94, PANEL, BORDER, 4)
        c.text(54, 366, 260, 27, "2  选择采集日期", 12, TEXT, True)
        c.text(
            972,
            366,
            96,
            27,
            f"{browser.date_page + 1}/{browser.date_page_count} 页",
            9,
            MUTED,
            False,
            Qt.AlignRight,
        )
        c.button(1077, 364, 64, 30, "上一页", disabled=browser.date_page <= 0)
        c.button(
            1149,
            364,
            64,
            30,
            "下一页",
            disabled=browser.date_page >= browser.date_page_count - 1,
        )
        if browser.date_page > 0:
            self._hit(1077, 364, 64, 30, "analysis-date-page-previous")
        if browser.date_page < browser.date_page_count - 1:
            self._hit(1149, 364, 64, 30, "analysis-date-page-next")
        for index, group in enumerate(browser.visible_dates):
            x, y = 54 + index * 230, 399
            selected = group.acquisition_date == browser.selected_date
            c.rect(
                x,
                y,
                214,
                38,
                "#eef4ff" if selected else PANEL_SOFT,
                BLUE if selected else BORDER,
                4,
                2 if selected else 1,
            )
            c.text(
                x + 10,
                y,
                194,
                38,
                f"{group.display_date} · {group.record_count} 条",
                9,
                TEXT,
                selected,
                Qt.AlignCenter,
            )
            self._hit(x, y, 214, 38, f"analysis-date:{group.acquisition_date}")

        c.rect(32, 464, 1216, 82, PANEL, BORDER, 4)
        c.text(54, 474, 180, 26, "3  选择动作标签", 12, TEXT, True)
        counts = {
            label: len([
                record for record in browser.records
                if record.true_label == label
                and str(getattr(record, "subject_id", "")) == browser.selected_subject_id
                and (
                    browser.selected_date == browser.ALL_DATES
                    or str(getattr(record, "acquisition_date", "")) == browser.selected_date
                )
            ])
            for label in browser.labels
        }
        for index, label in enumerate(browser.labels[:9]):
            x = 228 + index * 108
            selected = label == browser.selected_label
            c.button(x, 490, 98, 45, f"{label}  {counts[label]}", selected)
            self._hit(x, 490, 98, 45, f"analysis-label:{label}")

        c.rect(32, 560, 1216, 166, PANEL, BORDER, 4)
        c.text(54, 570, 250, 27, "4  选择具体试次", 12, TEXT, True)
        c.text(
            895,
            570,
            170,
            27,
            f"第 {browser.page + 1}/{browser.page_count} 页",
            10,
            MUTED,
            False,
            Qt.AlignRight,
        )
        c.button(1077, 568, 64, 30, "上一页", disabled=browser.page <= 0)
        c.button(
            1149,
            568,
            64,
            30,
            "下一页",
            disabled=browser.page >= browser.page_count - 1,
        )
        if browser.page > 0:
            self._hit(1077, 568, 64, 30, "analysis-page-previous")
        if browser.page < browser.page_count - 1:
            self._hit(1149, 568, 64, 30, "analysis-page-next")

        for index, (record_index, record) in enumerate(browser.visible_records):
            column, row = index % 3, index // 3
            x, y = 54 + column * 393, 606 + row * 56
            selected = record_index == browser.selected_record_index
            c.rect(
                x,
                y,
                372,
                46,
                "#eef4ff" if selected else PANEL_SOFT,
                BLUE if selected else BORDER,
                4,
                2 if selected else 1,
            )
            sequence = browser.page * browser.PAGE_SIZE + index + 1
            identity = record.trial_index or f"{sequence:02d}"
            short_id = record.trial_id[-28:]
            c.text(x + 14, y + 1, 338, 23, f"第 {identity} 段 · {short_id}", 10, TEXT, True)
            c.text(
                x + 14,
                y + 23,
                338,
                18,
                f"{record.duration_seconds:.2f} s · {record.sample_rate_hz:g} Hz · "
                f"{record.channel_count} 通道",
                9,
                MUTED,
            )
            self._hit(x, y, 372, 46, f"analysis-trial:{record_index}")

        c.rect(32, 740, 1216, 132, PANEL, BORDER, 4)
        c.text(54, 750, 300, 26, "5  截取时间段并在本页分析", 12, TEXT, True)
        record = browser.selected_record
        if record is not None:
            c.text(
                54,
                780,
                690,
                23,
                f"已选：{record.true_label} · {record.trial_id}",
                11,
                TEXT,
                True,
            )
            c.text(
                54,
                805,
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
                    830,
                    690,
                    20,
                    f"当前结果：候选 {report.candidate_label} · 分数 {report.confidence:.3f} · "
                    f"可用通道 {report.usable_channel_count}/{report.total_channel_count}",
                    9,
                    GREEN if report.accepted else YELLOW,
                )
        self._light_button(c, 834, 779, 170, 54, "设置时间段", False, BLUE)
        self._light_button(c, 1018, 779, 202, 54, "在本页显示图表", True, "#2d8a57")
        self._hit(834, 779, 170, 54, "analysis-window")
        self._hit(1018, 779, 202, 54, "analysis-generate")

        pixmap = browser.report_pixmap
        if pixmap is None or pixmap.isNull():
            return
        report_width = 1184.0
        report_height = browser.report_display_height(report_width)
        c.text(32, 896, 360, 34, "当前试次分析结果", 18, TEXT, True)
        c.text(
            760,
            900,
            468,
            25,
            "保持原六联图格式 · 使用滚轮向下查看完整内容 ↓",
            10,
            CYAN,
            True,
            Qt.AlignRight,
        )
        c.rect(32, 936, 1216, report_height + 32, PANEL, BORDER, 5)
        c.p.drawPixmap(
            QRectF(48, 952, report_width, report_height),
            pixmap,
            QRectF(pixmap.rect()),
        )

    def _research_data_max_scroll(self) -> float:
        report_height = self.trial_analysis.report_display_height(1184.0)
        if not self.trial_analysis.records and report_height <= 0:
            return 0.0
        content_bottom = (
            872.0 if report_height <= 0 else 952.0 + report_height + 16.0
        )
        return max(0.0, content_bottom - 684.0)

    def _research_data_empty_state(self, c: Canvas) -> None:
        c.rect(32, 230, 1216, 438, PANEL, BORDER, 5)
        c.text(430, 267, 420, 42, "导入后按四步完成分析", 18, TEXT, True, Qt.AlignCenter)
        steps = (
            ("1", "选择受试者", "姓名可编辑，稳定编号不变化"),
            ("2", "选择采集日期", "同一受试者按自然日自动分组"),
            ("3", "选择动作与试次", "只显示该人该日的数据"),
            ("4", "截取并分析", "自由设置起止时间并显示图表"),
        )
        for index, (number, title, description) in enumerate(steps):
            x = 62 + index * 296
            c.rect(x, 348, 274, 134, PANEL_SOFT, BORDER, 5)
            c.circle(x + 34, 389, 18, "#e7f1ff", BLUE)
            c.text(x + 16, 371, 36, 36, number, 11, BLUE, True, Qt.AlignCenter)
            c.text(x + 62, 360, 194, 38, title, 13, TEXT, True)
            c.text(x + 62, 400, 194, 44, description, 9, MUTED)
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
