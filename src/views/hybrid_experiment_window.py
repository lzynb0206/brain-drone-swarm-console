"""Passive paired research acquisition with presentation-aligned software markers."""
from dataclasses import asdict
from pathlib import Path
import time
import uuid

from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication

from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ, LABEL_TO_FREQUENCY
from src.bci.montage import HYBRID
from src.bci.stimulus import luminance_at
from src.services.hybrid_experiment_protocol import HybridPhase as P, HybridExperimentProtocol
from src.views.stimulus_canvas import StimulusCanvas, StimulusFrameDriver
from src.views.ssvep_rendering import draw_ssvep_header, draw_ssvep_target, draw_ssvep_status_banner, target_rectangles

STAGES = {P.BASELINE: "baseline", P.REST_OPEN: "rest_open", P.REST_CLOSED: "rest_closed",
          P.SSVEP: "ssvep", P.MI: "mi"}
SAVE_STAGES = {P.SAVE_BASELINE: "baseline", P.SAVE_REST_OPEN: "rest_open",
               P.SAVE_REST_CLOSED: "rest_closed", P.SAVE_SSVEP: "ssvep", P.SAVE_MI: "mi"}


class HybridExperimentWindow(StimulusCanvas):
    capture_requested = Signal(object)
    phase_marker = Signal(str, float, object)
    status_changed = Signal(str)
    experiment_finished = Signal(str, object)

    def __init__(self, config, parent=None, *, preflight_id="", external_recording=False):
        super().__init__(parent, Qt.Window)
        self.config = config.validate()
        self.protocol = HybridExperimentProtocol(config)
        self.run_id = f"hybrid-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.preflight_id = preflight_id
        self.external_recording = external_recording
        self.external_log_failed = False
        self.logged_stage_ids = []
        self.phase_started = 0.
        self.phase_wall = 0.
        self.saved_paths = []
        self.completed_pairs = 0
        self.ssvep_context = {}
        self.baseline_trial_id = None
        self.rest_trial_ids = {}
        self._early_ack = None
        self._terminal = False
        self._epoch = 0
        self._waiting_presentation = False
        self._painted_epoch = -1
        self._stage_context = None
        self._frame_times = []
        self.presentation_events = []
        self._timer = StimulusFrameDriver(self)
        self._timer.timeout.connect(self._tick)
        self._has_swap = hasattr(self, "frameSwapped")
        if self._has_swap:
            self.frameSwapped.connect(self._presented)
        self.setWindowTitle("脑电科研实验 · 独立呈现／外部 BDF" if external_recording else "脑电科研采集 · 5+3 候选布局")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)

    @property
    def running(self):
        return self.protocol.running

    @property
    def awaiting_save(self):
        return self.protocol.phase in SAVE_STAGES

    @property
    def progress_text(self):
        return f"已完成 {self.completed_pairs} / {self.config.pair_count} 组"

    def start_experiment(self):
        if self.protocol.phase == P.IDLE:
            self._transition(self.protocol.start(time.monotonic()))
            self._timer.start()

    def stage(self):
        phase = self.protocol.phase
        if phase in STAGES:
            return STAGES[phase]
        if phase in SAVE_STAGES:
            return SAVE_STAGES[phase]
        if phase == P.REST_OPEN_CUE:
            return "rest_open"
        if phase == P.REST_CLOSED_CUE:
            return "rest_closed"
        trial = self.protocol.trial
        return "ssvep" if phase == P.CUE and trial and trial.ssvep_label is not None else "mi"

    def metadata(self, stage=None):
        stage = stage or self.stage()
        common = {"run_id": self.run_id, "stage": stage, "montage_id": HYBRID.id,
                  "channel_names": list(HYBRID.names), "config": asdict(self.config),
                  "preflight_id": self.preflight_id, "research_only": True,
                  "acquisition_purpose": self.config.purpose,
                  "presentation_is_physical_measurement": False,
                  "dedicated_rest_trial_ids": dict(self.rest_trial_ids)}
        if self.external_recording:
            common.update(montage_id="external_bdf_named_v1", channel_names=[],
                          recording_mode="external_bdf", eeg_recorded_locally=False)
        if stage in ("baseline", "rest_open", "rest_closed"):
            closed = stage == "rest_closed"
            return {**common, "trial_id": f"{self.run_id}-{stage}", "paradigm_type": "MI",
                    "true_label": "闭眼静息" if closed else "睁眼静息",
                    "record_kind": "rest_baseline" if stage == "baseline" else "dedicated_rest",
                    "eye_state": "closed" if closed else "open",
                    "rest_purpose": "short_pre_run_MI_reference" if stage == "baseline" else "dedicated_resting_session"}
        trial = self.protocol.trial
        if trial is None:
            return common
        pair = f"{self.run_id}-pair-{trial.index:04d}"
        label = trial.ssvep_label if stage == "ssvep" else trial.mi_label
        return {**common, "pair_id": pair, "trial_id": f"{pair}-{stage}",
                "trial_index": trial.index, "block_index": trial.block,
                "block_id": f"{self.run_id}-block-{trial.block:03d}",
                "record_kind": "pilot_pair" if self.config.purpose == "pilot" and trial.mi_label else "serial_pair" if trial.mi_label and trial.ssvep_label else "mi_only" if trial.mi_label else "ssvep_idle",
                "paradigm_type": stage.upper(), "true_label": label,
                "ssvep_true_label": trial.ssvep_label, "mi_true_label": trial.mi_label,
                "ssvep_channel_names": [] if self.external_recording else list(HYBRID.ssvep),
                "mi_channel_names": [] if self.external_recording else list(HYBRID.mi),
                "random_seed": self.config.random_seed, "baseline_trial_id": self.baseline_trial_id,
                "idle_targets_flicker": stage == "ssvep",
                "target_frequencies_hz": list(TARGET_FREQUENCIES_HZ),
                "mi_visual_during_capture": "central_fixation_with_class_independent_completed_count_no_countdown",
                "completed_pairs_at_stage_start": self.completed_pairs,
                **(self.ssvep_context if stage == "mi" else {})}

    def _transition(self, phase):
        if phase is None:
            return
        now = time.monotonic()
        if self._stage_context is not None:
            context = {**self._stage_context, "presentation_end_monotonic": now,
                       "presentation_duration_seconds": now - self.phase_started,
                       "render_frame_monotonic_seconds": list(self._frame_times)}
            self.presentation_events.append(context)
            self.phase_marker.emit("hybrid_stage_end", time.time(), context)
            self._stage_context = None
        self.phase_started, self.phase_wall = now, time.time()
        self._epoch += 1
        self._frame_times = []
        if phase == P.CUE:
            self.ssvep_context = {}
            self._early_ack = None
        # Start the duration only after the first frame is presented. A hidden
        # or stalled window cannot silently collect a task that was not shown.
        self._waiting_presentation = self.protocol.deadline is not None
        if self._waiting_presentation:
            self.protocol.deadline = None
        self.phase_marker.emit("hybrid_" + phase.value, self.phase_wall, self.metadata())
        if phase == P.REST_READY or (phase == P.FINISHED and self.config.mode == "rest"):
            QApplication.beep()
        if phase in (P.FINISHED, P.ABORTED):
            self._timer.stop()
            self._finish("completed" if phase == P.FINISHED else "aborted")
        self.status_changed.emit(f"科研采集：{phase.value} · 已完成 {self.completed_pairs}/{self.config.pair_count} 组")
        self.update()
        if self.awaiting_save and self.external_recording and not self.external_log_failed:
            self._advance_external_stage()
        if self.awaiting_save and self._early_ack is not None:
            args, self._early_ack = self._early_ack, None
            self.notify_trial_saved(*args)

    def _advance_external_stage(self):
        """The journal is saved; EEG stays on the other computer."""
        metadata = self.metadata()
        stage = metadata['stage']
        trial_id = metadata['trial_id']
        self.logged_stage_ids.append(trial_id)
        if stage == 'ssvep':
            self.ssvep_context = {'preceding_ssvep_trial_id': trial_id,
                                  'p_ssvep': None, 'p_ssvep_valid': False}
        elif stage == 'mi':
            self.completed_pairs += 1
        elif stage == 'baseline':
            self.baseline_trial_id = trial_id
        else:
            self.rest_trial_ids[stage] = trial_id
        self._transition(self.protocol.saved(stage, time.monotonic()))

    def _presented(self):
        if self._painted_epoch != self._epoch or not self.running:
            return
        now = time.monotonic()
        if not self._waiting_presentation:
            if self._stage_context is not None:
                self._frame_times.append(now)
            return
        self._waiting_presentation = False
        self.phase_started, self.phase_wall = now, time.time()
        self.protocol.enter(self.protocol.phase, now)
        context = {**self.metadata(), "presentation_monotonic": now,
                   "presentation_source": self.presentation_source,
                   "onset_estimate": "qt_frame_swapped" if self._has_swap else "headless_paint_test",
                   "presentation_is_physical_measurement": False}
        self.phase_marker.emit("hybrid_presented", self.phase_wall, context)
        if self.protocol.phase in STAGES:
            duration = self.protocol.deadline - now
            context.update(duration_seconds=duration, discard_seconds=0., analysis_duration_seconds=duration,
                           marker_timestamp=self.phase_wall,
                           frequency_hz=LABEL_TO_FREQUENCY.get(context["true_label"], 0.))
            self._stage_context = context.copy()
            self._frame_times = [now]
            self.capture_requested.emit(context)

    def notify_trial_saved(self, label, path, quality_status="unknown", evidence=None):
        if self.protocol.phase not in STAGES and not self.awaiting_save:
            return False
        metadata = self.metadata()
        if label != metadata.get("true_label") or Path(path).stem != metadata.get("trial_id"):
            return False
        if not self.awaiting_save:
            self._early_ack = (label, path, quality_status, evidence)
            return True
        stage = metadata["stage"]
        if stage == "ssvep":
            self.ssvep_context = {"preceding_ssvep_trial_id": metadata["trial_id"],
                                  "preceding_ssvep_path": str(path), **dict(evidence or {})}
        elif stage == "mi":
            self.completed_pairs += 1
        elif stage == "baseline":
            self.baseline_trial_id = metadata["trial_id"]
        else:
            self.rest_trial_ids[stage] = metadata["trial_id"]
        self.saved_paths.append(str(path))
        self.phase_marker.emit("hybrid_stage_saved", time.time(),
                               {**metadata, "raw_trial_path": str(path), "quality_status": quality_status})
        self._transition(self.protocol.saved(stage, time.monotonic()))
        return True

    def _tick(self):
        now = time.monotonic()
        if self._waiting_presentation and now - self.phase_started > 2:
            self.notify_trial_save_error("2 秒内没有收到画面呈现确认，本次采集中止")
            return
        if self.awaiting_save and now - self.phase_started > 10:
            self.notify_trial_save_error("10 秒内未收到采集保存确认")
            return
        phase = self.protocol.tick(now)
        if phase is not None:
            self._transition(phase)
        self.update()

    def notify_quality_warning(self, message):
        self.status_changed.emit(f"质量提示：{message}；试次仍保存")

    def notify_trial_save_error(self, message):
        self.status_changed.emit(str(message))
        self.abort_immediately(str(message), close=False)

    def abort_immediately(self, reason, close=True):
        if self.running:
            self.phase_marker.emit("hybrid_abort_reason", time.time(), {**self.metadata(), "reason": reason})
            self._transition(self.protocol.abort(time.monotonic()))
        if close:
            self.close()

    def _finish(self, status):
        if self._terminal:
            return
        self._terminal = True
        self.experiment_finished.emit(status, {"run_id": self.run_id, "status": status,
            "saved_trials": len(self.saved_paths), "valid_trials": None,
            "logged_segments": len(self.logged_stage_ids), "eeg_recorded_locally": not self.external_recording,
            "rejected_trials": 0, "completed_pairs": self.completed_pairs,
            "planned_pairs": self.config.pair_count, "saved_paths": self.saved_paths,
            "montage_id": "external_bdf_named_v1" if self.external_recording else HYBRID.id,
            "config": asdict(self.config), "preflight_id": self.preflight_id,
            "dedicated_rest_trial_ids": self.rest_trial_ids, "presentation_events": self.presentation_events,
            "physical_display_timing_verified": False})

    def paint_stimulus(self):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080d17"))
        phase, trial = self.protocol.phase, self.protocol.trial
        self._painted_epoch = self._epoch
        elapsed = 0. if self._waiting_presentation else time.monotonic() - self.phase_started
        remaining = max(0., (self.protocol.deadline or time.monotonic()) - time.monotonic())
        mi_cue = phase == P.MI_CUE or (phase == P.CUE and trial and trial.ssvep_label is None)
        if mi_cue and self._waiting_presentation:
            remaining = self.config.mi_cue_seconds
        # MI shows no class label, countdown or flicker. The completed count
        # remains fixed throughout a task and only advances after its MI ends.
        if phase in (P.MI, P.BASELINE, P.REST_OPEN, P.GAP):
            painter.setPen(QPen(QColor("#eeeeee"), 3))
            x, y = self.width() // 2, self.height() // 2
            painter.drawLine(x - 14, y, x + 14, y)
            painter.drawLine(x, y - 14, x, y + 14)
            if self.config.pair_count:
                self._paint_progress(painter)
        elif mi_cue:
            self._paint_header(painter, "MI 动作准备 · 看清后等待中央十字",
                              "提示结束后开始想象 · 身体保持放松不动 · P 请求暂停 · Esc 中止")
            if self.protocol.paused:
                draw_ssvep_status_banner(painter, self.width(), self.height(), "已暂停，按 P 继续")
            else:
                self._paint_mi_cue(painter, trial.mi_label, remaining)
        elif phase != P.REST_CLOSED:
            if phase in (P.CUE, P.SSVEP) and trial and trial.ssvep_label is not None:
                for label, freq, rect in zip(COMMANDS, TARGET_FREQUENCIES_HZ, target_rectangles(self.width(), self.height())):
                    luminance = luminance_at(elapsed, freq) if phase == P.SSVEP else .06
                    draw_ssvep_target(painter, rect, label, freq, luminance,
                                      border_color="#41d6d1" if trial.ssvep_label == label else "#384255")
            title = "专门静息采集" if self.config.mode == "rest" else "SSVEP＋MI 混合采集" if self.config.mode == "serial" else "MI 单独采集"
            if self.config.mode == "serial" and trial and trial.mi_label:
                if phase == P.SSVEP:
                    title = "阶段 1 / 2 · SSVEP 视觉刺激"
            self._paint_header(painter, title,
                "独立呈现 · 脑电由采集电脑记录  |  P 请求暂停  Esc 中止" if self.external_recording else
                "CH1–5：O1 Oz O2 P3 P4  |  CH6–8：C3 Cz C4  |  P 请求暂停  Esc 中止")
            text = ""
            if self.protocol.paused:
                text = "已暂停，按 P 继续"
            elif phase == P.REST_OPEN_CUE:
                text = f"准备睁眼静息 {self.config.resting_open_seconds:g} 秒\n看中央十字，放松，不想象动作"
            elif phase == P.REST_CLOSED_CUE:
                text = f"请闭眼静息 {self.config.resting_closed_seconds:g} 秒\n保持清醒、放松；结束有提示音，等待实验员提示睁眼"
            elif phase == P.REST_READY:
                text = "专门静息已保存，请睁眼\n确认已经睁眼并准备好后，由实验员按空格继续"
            elif phase == P.CUE:
                text = f"准备：{trial.ssvep_label or trial.mi_label} · {remaining:.1f}s"
                if trial.ssvep_label is None:
                    text += "\n十字出现后开始想象动作的感觉，身体不动"
            elif phase == P.SSVEP and trial.ssvep_label == "空闲":
                text = "看中央，不注视闪烁目标（空闲背景）"
            elif self.awaiting_save:
                text = "正在保存本阶段原始脑电…"
            elif phase == P.REST:
                text = f"休息 · {remaining:.1f}s\n可眨眼、放松"
            elif phase in (P.FINISHED, P.ABORTED):
                result = f"已记录 {len(self.logged_stage_ids)} 段日志" if self.external_recording else f"已保存 {len(self.saved_paths)} 段"
                text = f"{'完成' if phase == P.FINISHED else '已中止'} · {result}\nEsc 退出"
                if phase == P.FINISHED and self.config.mode == "rest":
                    text = "静息已结束，可以睁眼\n" + result + " · Esc 返回"
                    if self.external_recording:
                        text += "\n请在采集电脑停止并保存本次 BDF"
            if text:
                draw_ssvep_status_banner(painter, self.width(), self.height(), text, text_color="#f4f8ff")
        painter.end()
        if not self._has_swap:
            epoch = self._epoch
            QTimer.singleShot(0, lambda: self._presented() if epoch == self._epoch else None)

    def _paint_header(self, painter, title, subtitle):
        if self.config.pair_count:
            title = f"{self.progress_text} · {title}"
        draw_ssvep_header(painter, self.width(), title, subtitle)

    def _paint_progress(self, painter):
        painter.save()
        painter.fillRect(QRectF(0, 0, self.width(), 42), QColor("#080d17"))
        painter.setPen(QColor("#e9f2ff"))
        painter.setFont(QFont("PingFang SC", 17, QFont.Bold))
        painter.drawText(QRectF(20, 6, self.width()-40, 30), Qt.AlignCenter, self.progress_text)
        painter.restore()

    def _paint_mi_cue(self, painter, label, remaining):
        """Readable class cue, shown only before the MI recording window."""
        width, height = self.width(), self.height()
        arrow = "←  " if label == "想象左手" else "→  " if label == "想象右手" else ""
        font = QFont(self.font())
        font.setPointSizeF(min(44., max(22., height * .055)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#f4f8ff"))
        painter.drawText(QRectF(24, height * .27, width - 48, height * .16), Qt.AlignCenter, arrow + label)
        font.setPointSizeF(min(32., max(18., height * .04)))
        painter.setFont(font)
        painter.setPen(QColor("#41d6d1"))
        painter.drawText(QRectF(24, height * .45, width - 48, height * .12), Qt.AlignCenter,
                         f"准备倒计时  {remaining:.1f} 秒")
        font.setPointSizeF(min(20., max(12., height * .025)))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#d6deeb"))
        painter.drawText(QRectF(24, height * .63, width - 48, height * .14), Qt.AlignCenter | Qt.TextWordWrap,
                         f"先看清动作，中央十字出现后开始\n持续想象动作的感觉 {self.config.mi_seconds:g} 秒，手保持放松不动")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.protocol.phase == P.REST_READY:
            self._transition(self.protocol.resume_after_rest(time.monotonic()))
        elif event.key() == Qt.Key_P and self.protocol.phase != P.REST_READY and not self._waiting_presentation:
            self.protocol.toggle_pause(time.monotonic())
            self.phase_marker.emit("hybrid_pause_request", time.time(), self.metadata())
            self.update()
        elif event.key() == Qt.Key_Escape:
            self.abort_immediately("operator_abort")
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.running:
            self._transition(self.protocol.abort(time.monotonic()))
        event.accept()
