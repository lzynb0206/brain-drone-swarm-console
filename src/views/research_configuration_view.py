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
    RESEARCH_SELECTION as SELECTION,
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
        c.rect(232, 287, 47, 202, SELECTION, BLUE, 3)
        c.p.save()
        c.p.translate(255, 388)
        c.p.rotate(90)
        c.text(-55, -17, 110, 34, "SSVEP", 15, CYAN, True, Qt.AlignCenter)
        c.p.restore()
        options = [("SSVEP", 287), ("MI", 361), ("SSVEP+MI混合", 434)]
        for name, y in options:
            active = self.actions.paradigm_type == name
            c.rect(296, y, 270, 57, SELECTION if active else PANEL, BLUE if active else BORDER, 3)
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
                c.line(739, y + 7, 1038, y + 7, "#e5e5e5", 7)
                c.line(739, y + 7, knob, y + 7, BLUE, 7)
                c.circle(knob, y + 7, 8, BLUE)
                c.text(1044, y - 8, 45, 30, f"{self.actions.stimulus_size if action == 'paradigm-size' else self.actions.duty_cycle}%", 11, TEXT)
                self._hit(730, y - 5, 318, 25, action)
            c.button(656, 492, 116, 46, "应用配置", True)
            c.button(783, 492, 116, 46, "恢复默认")
            c.button(910, 492, 130, 46, "全屏刺激", True, accent=CYAN)
            self._hit(656, 492, 116, 46, "paradigm-apply")
            self._hit(783, 492, 116, 46, "paradigm-reset")
            self._hit(910, 492, 130, 46, "paradigm-start")
        else:
            c.text(656, 229, 380, 42, "MI 与 SSVEP＋MI 混合采集", 19, TEXT, True)
            if self.experiment.external_recording:
                c.text(656, 278, 380, 30, "独立呈现 · 外部 32 路 BDF", 14, CYAN, True)
                c.text(656, 320, 380, 95, "无需本机连接 EEG。\n本机记录任务标签、阶段时间和配对编号。\n导入时选择 SSVEP / MI 分析通道。\n无跨机触发：需确认时间对齐，P 离线计算。", 11, TEXT)
                c.button(656, 452, 372, 38, "前往数据采集 · 配置并开始实验", True, accent=CYAN)
                self._hit(656, 452, 372, 38, "research:数据采集")
                return
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
        ssvep = paradigm == "SSVEP" and scheme.id == "formal"
        config = (self.actions.automatic_experiment_config if ssvep else
                  hybrid_collection_config(self.actions.hybrid_experiment_config, paradigm, scheme.id))
        montage = LEGACY if ssvep else HYBRID
        layout_ready = (self.actions.montage_id == montage.id
                        and tuple(self._eeg_channel_names) == montage.names)
        running = self.experiment.running
        external = self.experiment.external_recording

        c.text(32, 72, 420, 34, "实验数据采集", 20, TEXT, True)
        c.text(32, 116, 80, 32, "本轮受试者", 10, TEXT, True)
        subject_name = self.experiment.selected_subject_name
        self._light_button(c, 120, 116, 276, 32, f"{subject_name}  ▾" if subject_name else "选择并确认受试者  ▾", False, BLUE)
        self._light_button(c, 408, 116, 120, 32, "新增受试者", False, BLUE)
        if not running:
            self._hit(120, 116, 276, 32, "collection-subject")
            self._hit(408, 116, 120, 32, "collection-subject-new")
        c.text(550, 116, 678, 32,
               (f"已确认 {self.experiment.selected_subject_id} · 自动归档" if subject_name else "开始前请确认受试者")
               + f"  |  数据子标签：{paradigm}", 10, CYAN if subject_name else MUTED)
        for index, (value, title) in enumerate(COLLECTION_PARADIGMS):
            x = 648 + index * 204
            active = paradigm == value
            c.rect(x, 76, 192, 34, SELECTION if active else PANEL, BLUE if active else BORDER, 4)
            c.text(x, 76, 192, 34, title, 11, BLUE if active else MUTED, active, Qt.AlignCenter)
            if not running:
                self._hit(x, 76, 192, 34, f"paradigm-type:{value}")

        c.rect(32, 160, 1216, 86, PANEL, BORDER, 5)
        c.text(52, 164, 240, 22, "实验方案", 11, TEXT, True)
        c.text(858, 164, 370, 22, "采集中 · 结束后可切换方案" if running else "切换范式会保留已配置的采集参数", 9, MUTED, False, Qt.AlignRight)
        width = (1176 - 12 * (len(schemes) - 1)) / len(schemes)
        for index, item in enumerate(schemes):
            x = 52 + index * (width + 12)
            active = scheme.id == item.id
            c.rect(x, 190, width, 46, SELECTION if active else PANEL_SOFT, BLUE if active else BORDER, 4)
            c.circle(x + 18, 213, 5, BLUE if active else PANEL, BLUE if active else BORDER)
            c.text(x + 34, 192, width - 48, 22, item.title, 11, BLUE if active else TEXT, True)
            c.text(x + 34, 214, width - 48, 18, item.description, 9, MUTED)
            if not running:
                self._hit(x, 190, width, 46, f"collection-scheme:{item.id}")

        c.rect(32, 256, 1216, 440, PANEL, BORDER, 5)
        name = "静息" if not ssvep and config.mode == "rest" else "SSVEP" if ssvep else "MI" if paradigm == "MI" else "SSVEP＋MI 混合"
        c.text(56, 268, 820, 30, f"{name} · {scheme.title}", 16, TEXT, True)
        status, color = self._collection_readiness(config, layout_ready, ssvep)
        c.rect(930, 268, 294, 30, SELECTION if color == GREEN else PANEL_SOFT, BORDER, 15)
        c.circle(948, 283, 5, color)
        c.text(962, 268, 248, 30, status, 9, color, True)

        metrics, labels, flow, preparation, saving = self._collection_plan_details(config, ssvep)
        if external:
            flow = flow.replace("确认保存", "记录阶段")
            saving = ("本机保存本次静息的眼状态与事件日志；请在采集电脑单独保存对应 BDF 文件。"
                      if not ssvep and config.mode == "rest" else
                      "本机仅保存事件日志与真实标签；另一台电脑录制 BDF。SSVEP 的 P 与 MI 均在导入后离线分析。")
        for index, (label, value) in enumerate(metrics):
            x = 56 + index * 196
            c.rect(x, 312, 184, 64, PANEL_SOFT, BORDER, 4)
            c.text(x + 12, 320, 160, 20, label, 9, MUTED, True)
            c.text(x + 12, 343, 160, 25, value, 16, TEXT, True)

        c.text(56, 386, 220, 22, "采集标签" if not ssvep and config.mode == "rest" else "真实标签计划", 12, TEXT, True)
        label_width = (1164 - 12 * (len(labels) - 1)) / len(labels)
        for index, label in enumerate(labels):
            x = 56 + index * (label_width + 12)
            c.rect(x, 416, label_width, 32, SELECTION, BORDER, 4)
            c.text(x, 416, label_width, 32, label, 11, BLUE, True, Qt.AlignCenter)

        c.text(56, 460, 220, 22, "实验流程", 12, TEXT, True)
        c.text(56, 486, 1164, 24, flow, 12, BLUE, True)
        c.text(56, 514, 1164, 22, preparation, 10, MUTED)
        c.text(56, 540, 1164, 22, saving, 10, MUTED)
        c.line(56, 576, 1224, 576, BORDER)
        wiring = "   ".join(f"CH{i + 1} {name}" for i, name in enumerate(montage.names))
        c.text(56, 584, 1164, 22, "外部 BDF：保留全部脑电通道及原采样率；分析通道按电极名自由选择" if external else f"所需接线  {wiring}   ·   250 Hz", 11, TEXT, True)
        note = "更换布局前请先停止数据流并调整物理电极。" if not layout_ready else "当前通道布局匹配；请核对物理电极与通道顺序。"
        if external:
            note = "请先启动另一台电脑的连续记录；本机日志不会自动同步到 BDF，导入时须确认偏移与漂移。"
        c.text(56, 610, 1164, 20, f"{note}  随机种子 {config.random_seed} · 完成后在“数据分析”页查看。", 9, MUTED)

        buttons = [
            (56, 144, "查看实时采集", "research:采集", False),
            (212, 176, "恢复枕顶 8 通道" if ssvep else "应用候选 5+3 布局", f"montage:{montage.id}", False),
            (852, 172, "配置采集计划", "collection-config", False),
            (1036, 188, "返回采集窗口" if running else scheme.start_label, "collection-start", True),
        ]
        if external:
            buttons = [button for button in buttons if not button[3].startswith("montage:")]
        buttons.extend([
            (400, 200, "独立呈现 / 外部 BDF", "collection-recording:external_bdf", external),
            (612, 170, "本机实时 EEG", "collection-recording:local_eeg", not external),
        ])
        for x, button_width, label, action, primary in buttons:
            enabled = not running or action in ("research:采集", "collection-start")
            self._light_button(c, x, 642, button_width, 36, label, primary, GREEN if primary else BLUE if enabled else MUTED)
            if enabled:
                self._hit(x, 642, button_width, 36, action)

    def _collection_readiness(self, config, layout_ready, ssvep):
        if self.experiment.running:
            window = self.experiment.automatic_window
            if getattr(window, "completed_pairs", None) is not None and window.config.pair_count:
                return window.progress_text, GREEN
            return "正在采集 · 当前方案已锁定", GREEN
        if not self.experiment.selected_subject_id:
            return "待选择并确认受试者", YELLOW
        if self.experiment.external_recording:
            return "独立呈现 · 无需本机 EEG", GREEN
        if not layout_ready:
            return "待应用枕顶 8 通道布局" if ssvep else "待应用 5+3 通道布局", YELLOW
        if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
            return "待启动实时 EEG 数据流", YELLOW
        return "数据流已启动 · 可直接开始采集", GREEN

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
            closed = config.rest_eye_state == "closed"
            label = "闭眼静息" if closed else "睁眼静息"
            duration = config.resting_closed_seconds if closed else config.resting_open_seconds
            metrics = (
                ("采集段数", "1 段"), ("静息时间", f"{duration:g} s"),
                ("眼睛状态", "闭眼" if closed else "睁眼"), ("准备提示", "5 s"),
                ("结束方式", "自动结束"), ("任务试次", "0"),
            )
            flow = f"准备提示 5s  →  {label} {duration:g}s  →  提示音并自动结束"
            return (metrics, (label,), flow,
                    "每次只运行所选静息，不切换眼状态、不等待空格，也不进入 SSVEP / MI。",
                    "按本次眼状态独立保存，后续可在数据分析中区分睁眼与闭眼静息。")
        serial = config.mode == "serial"
        if serial:
            metrics = (
                (("每动作次数", "1 次") if config.purpose == "pilot" else ("采集方式", "串行配对")), ("任务组数", f"{config.pair_count} 组"),
                ("SSVEP 时间", f"{config.ssvep_seconds:g} s"), ("MI 动作提示", f"{config.mi_cue_seconds:g} s"),
                ("MI 时间", f"{config.mi_seconds:g} s"), ("休息时间", f"{config.rest_seconds:g} s"),
            )
            # A serial formal run also inserts an idle window between blocks.
            idle_count = config.idle_trials + (config.block_count - 1 if config.purpose == "formal" and config.idle_trials else 0)
            labels = tuple(COMMANDS[:1] if config.purpose == "pilot" else COMMANDS) + tuple(config.mi_labels)
            flow = f"提示 {config.cue_seconds:g}s → SSVEP {config.ssvep_seconds:g}s → 间隔 {config.gap_seconds:g}s → MI 提示 {config.mi_cue_seconds:g}s → MI {config.mi_seconds:g}s（十字）→ 休息 {config.rest_seconds:g}s"
            saving = (f"另采 {idle_count} 个空闲窗；" if idle_count else "") + "逐阶段保存 EEG、配对编号及 SSVEP 置信度，低置信度仍继续采 MI。"
        else:
            metrics = (
                ("每类次数", f"{config.repetitions} 次"), ("任务组数", f"{config.pair_count} 组"),
                ("MI 动作提示", f"{config.mi_cue_seconds:g} s"), ("MI 时间", f"{config.mi_seconds:g} s"),
                ("短基线", f"{config.baseline_seconds:g} s"), ("休息时间", f"{config.rest_seconds:g} s"),
            )
            labels = config.mi_labels
            flow = f"MI 动作提示 {config.mi_cue_seconds:g}s  →  MI {config.mi_seconds:g}s（中央十字）  →  确认保存  →  休息 {config.rest_seconds:g}s"
            saving = "采集全 8 通道，MI 分析使用 C3 / Cz / C4；逐段保存原始 EEG、真实标签、时间戳及质量状态。"
        preparation = f"开始前休息 {config.baseline_seconds:g}s（中央十字），随后自动进入上述流程；睁眼、闭眼静息单独采集。"
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
            c.circle(x + 9, 139, 7, BLUE if active else None, MUTED, 2)
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
        c.line(73, 310, 1207, 310, BORDER)
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
            c.rect(x, y, 260, 70, SELECTION if selected else PANEL, BLUE if selected else BORDER, 3, 2 if selected else 1)
            c.text(x, y + 3, 260, 32, name, 13, TEXT, True, Qt.AlignCenter)
            c.rect(x + 92, y + 46, 32, 16, GREEN if loaded else BORDER, None, 8)
            c.circle(x + 117 if loaded else x + 101, y + 54, 5, "#ffffff" if loaded else TEXT)
            c.circle(x + 143, y + 54, 8, YELLOW if default else BORDER)
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
        c.text(54, 167, 610, 29, browser.source_summary, 12, TEXT, True)
        self._light_button(c, 680, 150, 130, 46, "导入 BDF", True, BLUE)
        self._hit(680, 150, 130, 46, "analysis-import-bdf")
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
        c.text(54, 240, 230, 27, "1  选择受试者 · 拖动卡片排序", 12, TEXT, True)
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
                SELECTION if selected else PANEL_SOFT,
                BLUE if selected else BORDER,
                4,
                2 if selected else 1,
            )
            c.text(x + 12, y + 2, 226, 24, group.display_name, 10, TEXT, True)
            for dx in (0, 5):
                for dy in (0, 5, 10):
                    c.circle(x + 254 + dx, y + 12 + dy, 1, MUTED)
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
            drop = getattr(self, "_subject_drop_target", None)
            if drop is not None and drop[0] == group.subject_id:
                indicator_x = x + 280 if drop[1] else x - 6
                c.line(indicator_x, y - 3, indicator_x, y + 53, BLUE, 3)

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
                SELECTION if selected else PANEL_SOFT,
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
        two_rows = len(browser.labels) > 9
        for index, label in enumerate(browser.labels):
            x = 228 + (index % 9) * 108
            y = 478 + (index // 9) * 32 if two_rows else 490
            height = 28 if two_rows else 45
            selected = label == browser.selected_label
            c.button(x, y, 98, height, f"{label}  {counts[label]}", selected)
            self._hit(x, y, 98, height, f"analysis-label:{label}")

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
                SELECTION if selected else PANEL_SOFT,
                BLUE if selected else BORDER,
                4,
                2 if selected else 1,
            )
            sequence = browser.page * browser.PAGE_SIZE + index + 1
            identity = record.trial_index or f"{sequence:02d}"
            c.text(x + 14, y + 1, 338, 23, f"第 {identity} 段 · {record.collection_label}", 10, TEXT, True)
            c.text(
                x + 14,
                y + 23,
                338,
                18,
                f"{'静息' if record.analysis_kind == 'rest' else 'MI 阶段' if record.analysis_kind == 'mi' else 'SSVEP 阶段'} · "
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
                f"已选：{record.true_label} · " + ("外部 BDF · 人工对齐，未验证" if getattr(record, "alignment_status", "") else record.trial_id),
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
                result_text = (f"当前结果：候选 {report.candidate_label} · 分数 {report.confidence:.3f}"
                               if report.analysis_kind == "ssvep" else "当前结果：运动区波形、功率与时频图 · 未运行 MI 分类")
                c.text(
                    54,
                    830,
                    690,
                    20,
                    result_text + " · " +
                    f"可用通道 {report.usable_channel_count}/{report.total_channel_count}",
                    9,
                    GREEN if report.accepted else CYAN if report.analysis_kind != "ssvep" else YELLOW,
                )
            elif getattr(record, "is_serial_pair", False):
                c.text(54, 830, 740, 20, "SSVEP＋MI 混合试次：分析时自动查找同组另一阶段，按 SSVEP → MI 顺序展示。", 9, MUTED)
        self._light_button(c, 834, 779, 170, 54, "设置时间段", False, BLUE)
        self._light_button(c, 1018, 779, 202, 54, "在本页显示图表", True, GREEN)
        self._hit(834, 779, 170, 54, "analysis-window")
        self._hit(1018, 779, 202, 54, "analysis-generate")

        pixmap = browser.report_pixmap
        if pixmap is None or pixmap.isNull():
            return
        report_width = 1184.0
        report_height = browser.report_display_height(report_width)
        paired = browser.paired_report_pixmap
        c.text(32, 896, 720, 34, "SSVEP+MI混合 · 完整配对分析" if paired is not None else "当前试次分析结果", 18, TEXT, True)
        c.text(
            760,
            900,
            468,
            25,
            "上方 SSVEP · 下方 MI · 滚轮查看完整内容 ↓" if paired is not None else "波形、通道质量与频域分析 · 滚轮查看完整内容 ↓",
            10,
            CYAN,
            True,
            Qt.AlignRight,
        )
        c.rect(32, 936, 1216, report_height + 32, PANEL, BORDER, 5)
        first_height = report_width * pixmap.height() / pixmap.width()
        c.p.drawPixmap(
            QRectF(48, 952, report_width, first_height),
            pixmap,
            QRectF(pixmap.rect()),
        )
        if paired is not None and not paired.isNull():
            y = 952 + first_height
            c.text(56, y + 6, 1164, 30, f"第二阶段 · MI 运动想象  |  配对编号：{browser.report_pair_id}", 12, CYAN, True)
            c.p.drawPixmap(QRectF(48, y + 42, report_width, report_width * paired.height() / paired.width()),
                           paired, QRectF(paired.rect()))
        if browser.report_note:
            c.text(54, 851, 1164, 18, browser.report_note, 9, YELLOW)

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
            c.circle(x + 34, 389, 18, SELECTION, BLUE)
            c.text(x + 16, 371, 36, 36, number, 11, BLUE, True, Qt.AlignCenter)
            c.text(x + 62, 360, 194, 38, title, 13, TEXT, True)
            c.text(x + 62, 400, 194, 44, description, 9, MUTED)
        c.text(
            180,
            518,
            920,
            56,
            "支持带标签 NPZ；外部连续 BDF 请点“导入 BDF”，选择同轮实验日志并确认时间对齐。\n"
            "页面内容包括原始波形、通道质量表、阈前/陷波后 FFT、滤波子带和六类 FBCCA 分数。",
            11,
            MUTED,
            False,
            Qt.AlignCenter,
        )


__all__ = ["ResearchConfigurationViewMixin"]
