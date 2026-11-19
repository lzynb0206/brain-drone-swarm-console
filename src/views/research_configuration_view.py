"""Research collection setup and saved-data rendering."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from src.bci.montage import HYBRID, LEGACY
from src.services.collection_plans import (
    collection_schemes, hybrid_collection_config,
)

from src.bci.config import COMMANDS
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
    """Render collection modes, parameters and saved-data pages."""

    def _research_data_collection(self, c: Canvas) -> None:
        paradigm = self.actions.paradigm_type
        schemes = collection_schemes(paradigm)
        scheme = next(item for item in schemes if item.id == self.actions.collection_scheme)
        ssvep = paradigm == "SSVEP" and scheme.id == "formal"
        config = (self.actions.automatic_experiment_config if ssvep else
                  hybrid_collection_config(self.actions.hybrid_experiment_config, paradigm, scheme.id))
        montage = LEGACY if self.actions.acquisition_profile.id == "ssvep8" else HYBRID
        layout_ready = (self.actions.montage_id == montage.id
                        and tuple(self._eeg_channel_names) == montage.names)
        running = self.experiment.running
        external = self.experiment.external_recording
        metrics, labels, flow, _preparation, _saving = self._collection_plan_details(config, ssvep)

        c.text(32, 72, 420, 34, "实验数据采集", 21, TEXT, True)
        profile = self.actions.acquisition_profile
        c.text(630, 76, 90, 36, "采集模式", 11, MUTED)
        self._research_choice(c, 724, 76, 524, profile.title, "profile", self.acquisition_profile.locked)
        c.rect(32, 132, 1216, 138, PANEL, BORDER, 6)
        c.text(56, 150, 84, 34, "受试者", 11, MUTED)
        self._research_choice(c, 140, 150, 336, self.experiment.selected_subject_name or "选择受试者",
                              "subject", running)
        c.button(488, 150, 112, 34, "新增受试者", disabled=running)
        if not running:
            self._hit(488, 150, 112, 34, "collection-subject-new")
        c.text(740, 150, 84, 34, "实验类型", 11, MUTED)
        self._research_choice(c, 824, 150, 400, paradigm.replace("SSVEP+MI混合", "SSVEP＋MI 联合实验"),
                              "paradigm", running or len(profile.paradigms) == 1)
        c.text(56, 212, 84, 34, "实验方案", 11, MUTED)
        self._research_choice(c, 140, 212, 336, scheme.title, "scheme", running)
        c.text(516, 196, 684, 24, "本轮摘要", 10, MUTED)
        summary = "  ·  ".join(f"{label} {value}" for label, value in metrics[:2])
        c.text(516, 224, 684, 26, summary, 13, TEXT, True)

        y = 290
        sections = (("parameters", "实验参数"), ("wiring", "接线与信号"), ("advanced", "高级设置"))
        for key, title in sections:
            expanded = self.collection_section == key
            c.rect(32, y, 1216, 42, PANEL, BORDER, 4)
            c.text(54, y, 1136, 42, ("▾  " if expanded else "▸  ") + title, 12, TEXT, True)
            self._hit(32, y, 1216, 42, f"collection-section:{key}")
            if expanded:
                body_y = y + 42
                c.rect(32, body_y, 1216, 176, PANEL, BORDER, 4)
                if key == "parameters":
                    for index, (label, value) in enumerate(metrics):
                        x = 56 + index % 3 * 388
                        row_y = body_y + 8 + index // 3 * 30
                        c.text(x, row_y, 140, 24, label, 10, MUTED)
                        c.text(x + 144, row_y, 200, 24, value, 12, TEXT, True)
                    if paradigm == "SSVEP+MI混合" and config.mode != "rest":
                        ssvep_labels = COMMANDS[:1] if config.purpose == "pilot" else COMMANDS
                        c.text(56, body_y + 70, 1168, 22, "SSVEP 阶段：" + " / ".join(ssvep_labels), 10, TEXT)
                        c.text(56, body_y + 96, 1168, 22, "MI 阶段：" + " / ".join(config.mi_labels), 10, TEXT)
                    else:
                        c.text(56, body_y + 70, 1168, 22, "采集标签：" + " / ".join(labels), 10, TEXT)
                    c.text(56, body_y + 122, 1168, 20, flow.replace("确认保存", "保存"), 9, MUTED)
                    c.button(1032, body_y + 144, 192, 28, "修改实验参数", disabled=running)
                    if not running:
                        self._hit(1032, body_y + 144, 192, 28, "collection-config")
                elif key == "wiring":
                    if external:
                        c.text(56, body_y + 18, 1168, 30, "外部设备记录 BDF，本机记录实验事件", 13, TEXT, True)
                        c.text(56, body_y + 60, 1168, 26, "导入时选择对应事件日志，并确认时间偏移与漂移。", 11, MUTED)
                    else:
                        wiring = "   ".join(f"CH{i + 1} {name}" for i, name in enumerate(montage.names))
                        c.text(56, body_y + 14, 1168, 30, wiring, 12, TEXT, True)
                        c.text(56, body_y + 54, 1168, 26, "布局已配置，请按上方接线核对电极" if layout_ready else "请在采集页选择匹配的模式并核对电极", 11, MUTED)
                        c.button(56, body_y + 112, 204, 36, "查看实时信号")
                        self._hit(56, body_y + 112, 204, 36, "research:采集")
                else:
                    settings = self.actions.decoder_settings
                    if paradigm == "MI":
                        c.text(56, body_y + 18, 1168, 28, "MI 运动想象 · 采集原始数据，离线分析", 12, TEXT, True)
                    else:
                        c.text(56, body_y + 10, 1168, 28, f"{self.actions.algorithm} · 按采集模式自动配置", 12, TEXT, True)
                        details = (("谐波", str(settings.harmonics)),
                                   ("滤波", "FFT" if settings.filter_method == "fft" else "Butterworth 4 阶"),
                                   ("分数阈值", f"{settings.min_score:g}"), ("差值阈值", f"{settings.min_margin:g}"))
                        for index, (label, value) in enumerate(details):
                            x = 56 + index * 292
                            c.text(x, body_y + 50, 110, 28, label, 10, MUTED)
                            c.text(x + 112, body_y + 50, 178, 28, value, 11, TEXT, True)
                        locked = self.acquisition_profile.locked
                        c.button(56, body_y + 110, 204, 36, "FBCCA 参数", disabled=locked)
                        if not locked:
                            self._hit(56, body_y + 110, 204, 36, "decoder-settings")
                    if paradigm == "SSVEP":
                        for x, title, action in ((272, "刺激设置", "research-stimulus-settings"),
                                                 (488, "实时识别测试", "ssvep-command-test")):
                            c.button(x, body_y + 110, 204, 36, title, disabled=running)
                            if not running:
                                self._hit(x, body_y + 110, 204, 36, action)
                y += 176
            y += 48

        status, color = self._collection_readiness(config, layout_ready, ssvep)
        c.rect(32, 632, 1216, 64, PANEL, BORDER, 6)
        c.circle(58, 664, 5, color)
        c.text(76, 644, 650, 40, status, 12, TEXT, True)
        if paradigm != "SSVEP":
            c.button(852, 646, 172, 36, "试运行 · 2 组", disabled=running)
            if not running:
                self._hit(852, 646, 172, 36, "hybrid-pilot-start")
        c.button(1036, 646, 188, 36, "返回采集窗口" if running else "开始采集", True)
        self._hit(1036, 646, 188, 36, "collection-start")

    def _research_choice(self, c, x, y, width, label, kind, disabled=False):
        c.button(x, y, width, 34, label + "  ▾", disabled=disabled)
        if not disabled:
            self._hit(x, y, width, 34, "collection-subject" if kind == "subject" else f"research-choice:{kind}")

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
            return ("待核对枕顶 8 通道接线" if self.actions.acquisition_profile.id == "ssvep8"
                    else "待核对 5+3 通道接线"), YELLOW
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

    def _research_data(self, c: Canvas) -> None:
        browser = self.trial_analysis
        c.text(32, 72, 600, 36, "数据分析", 21, TEXT, True)
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
        c.text(54, 474, 180, 26, "3  实验类型与动作", 12, TEXT, True)
        kind_label = browser.kind_title(browser.selected_kind) if browser.selected_kind else "全部类型"
        c.button(54, 508, 156, 28, kind_label + " ▾")
        self._hit(54, 508, 156, 28, "research-choice:analysis-kind")
        counts = {label: sum(record.true_label == label and browser._record_in_selected_subject_date(record)
                             for record in browser.records) for label in browser.labels}
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
        c.text(430, 267, 420, 42, "选择实验记录，开始分析", 18, TEXT, True, Qt.AlignCenter)
        steps = (
            ("1", "选择受试者", "姓名可编辑，稳定编号不变化"),
            ("2", "选择采集日期", "同一受试者按自然日自动分组"),
            ("3", "选择类型与试次", "只显示该人该日的数据"),
            ("4", "截取并分析", "自由设置起止时间并显示图表"),
        )
        for index, (number, title, description) in enumerate(steps):
            x = 62 + index * 296
            c.rect(x, 348, 274, 134, PANEL_SOFT, BORDER, 5)
            c.circle(x + 34, 389, 18, SELECTION, BLUE)
            c.text(x + 16, 371, 36, 36, number, 11, BLUE, True, Qt.AlignCenter)
            c.text(x + 62, 360, 194, 38, title, 13, TEXT, True)
            c.text(x + 62, 400, 194, 44, description, 9, MUTED)


__all__ = ["ResearchConfigurationViewMixin"]
