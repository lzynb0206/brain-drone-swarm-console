"""Full-screen, balanced SSVEP data-acquisition experiment."""

from __future__ import annotations

import time
import uuid

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen

from src.bci.config import (
    COMMANDS,
    LABEL_TO_FREQUENCY,
    SSVEP_PROFILE_NAME,
    TARGET_FREQUENCIES_HZ,
)
from src.bci.stimulus import StimulusTarget, luminance_at
from src.services.ssvep_experiment_protocol import (
    AutomaticExperimentConfig,
    AutomaticExperimentProtocol,
    ExperimentPhase,
    ProtocolTransition,
)
from src.views.ssvep_rendering import draw_fixation_cross, target_rectangles
from src.views.stimulus_canvas import StimulusCanvas, StimulusFrameDriver


class AutomaticSSVEPExperimentWindow(StimulusCanvas):
    """Present a reproducible protocol and wait for each raw file to be saved."""

    capture_requested = Signal(object)
    phase_marker = Signal(str, float, object)
    status_changed = Signal(str)
    experiment_finished = Signal(str, object)
    SAVE_ACK_TIMEOUT_SECONDS = 10.0

    def __init__(self, config: AutomaticExperimentConfig, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.config = config.validate()
        self.protocol = AutomaticExperimentProtocol(self.config)
        self.targets = tuple(
            StimulusTarget(label, float(frequency))
            for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ)
        )
        self.run_id = f"auto-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self._phase_started_monotonic = 0.0
        self._phase_started_wall = 0.0
        self._phase_timestamps_wall: dict[str, float] = {}
        self._saved_paths: list[str] = []
        self._quality_statuses: list[str] = []
        self._last_quality_status = "unknown"
        self._early_saved_ack: tuple[str, str, str] | None = None
        self._terminal_emitted = False
        self._deferred_action = ""
        self._timer = StimulusFrameDriver(self)
        self._timer.timeout.connect(self._tick)
        self.setWindowTitle("SSVEP 自动随机带标签采集")
        self.setStyleSheet("background: #050914;")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)

    @property
    def running(self) -> bool:
        return self.protocol.phase not in {
            ExperimentPhase.IDLE,
            ExperimentPhase.FINISHED,
            ExperimentPhase.ABORTED,
        }

    @property
    def current_label(self) -> str:
        trial = self.protocol.current_trial
        return trial.label if trial is not None else ""

    @property
    def awaiting_save(self) -> bool:
        return self.protocol.phase == ExperimentPhase.SAVING

    def start_experiment(self) -> None:
        if self.protocol.phase != ExperimentPhase.IDLE:
            return
        now = time.monotonic()
        self._timer.start()
        self._handle_transition(self.protocol.start(now), now)
        self.status_changed.emit(
            f"自动采集已开始：{self.protocol.total_trials} 个平衡随机试次"
        )

    def notify_trial_saved(
        self, label: str, path: str, quality_status: str = "unknown",
    ) -> bool:
        """Advance only after the EEG worker confirms the NPZ is on disk."""

        trial = self.protocol.current_trial
        if (
            self.protocol.phase == ExperimentPhase.STIMULUS
            and trial is not None
            and str(label) == trial.label
        ):
            # N samples span (N-1)/fs seconds, so a 750-sample/250-Hz save can
            # finish a few milliseconds before the presentation timer reaches
            # 3.000 s.  Keep the acknowledgement, but preserve the mandatory
            # STIMULUS -> SAVING marker order.
            self._early_saved_ack = (
                str(label), str(path), str(quality_status or "unknown"),
            )
            self.status_changed.emit("原始 EEG 已保存，等待本次视觉刺激计时结束…")
            return True
        if (
            self.protocol.phase != ExperimentPhase.SAVING
            or trial is None
            or str(label) != trial.label
        ):
            return False
        if path:
            self._saved_paths.append(str(path))
        self._last_quality_status = str(quality_status or "unknown").lower()
        self._quality_statuses.append(self._last_quality_status)
        metadata = self._trial_metadata()
        metadata["raw_trial_path"] = str(path)
        self._emit_phase_marker("trial_saved", metadata)
        now = time.monotonic()
        self._handle_transition(self.protocol.trial_saved(now), now)
        if self.protocol.paused:
            self._deferred_action = "实验已按请求在安全边界暂停"
            self._emit_phase_marker("paused", self._trial_metadata())
            self.status_changed.emit(self._deferred_action)
        if self._last_quality_status == "bad" and self.protocol.phase == ExperimentPhase.REST:
            metadata = self._trial_metadata()
            metadata.update({
                "quality_status": self._last_quality_status,
                "collection_continued": True,
            })
            self._emit_phase_marker("quality_warning", metadata)
            self._deferred_action = "信号质量不合格：已标记并保存，本实验继续"
            self.status_changed.emit(self._deferred_action)
            self.update()
        return True

    def notify_quality_warning(self, message: str) -> None:
        """Display a non-fatal quality warning without stopping acquisition."""

        if self.protocol.phase in {
            ExperimentPhase.IDLE, ExperimentPhase.FINISHED, ExperimentPhase.ABORTED,
        }:
            return
        self._deferred_action = f"信号质量警告（不中断）：{message}"
        self.status_changed.emit(self._deferred_action)
        self.update()

    def notify_trial_save_error(self, message: str) -> None:
        if self.protocol.phase in {
            ExperimentPhase.IDLE, ExperimentPhase.FINISHED, ExperimentPhase.ABORTED,
        }:
            return
        self._deferred_action = f"实验数据链路失败：{message}"
        now = time.monotonic()
        self._handle_transition(
            self.protocol.abort_immediately(now, self._deferred_action), now,
        )

    def abort_immediately(self, reason: str) -> None:
        """Close after the owner has explicitly cancelled the worker capture."""

        if self.protocol.phase not in {ExperimentPhase.FINISHED, ExperimentPhase.ABORTED}:
            now = time.monotonic()
            self._handle_transition(self.protocol.abort_immediately(now, reason), now)
        self.close()

    def _tick(self) -> None:
        now = time.monotonic()
        if (
            self.protocol.phase == ExperimentPhase.SAVING
            and now - self._phase_started_monotonic >= self.SAVE_ACK_TIMEOUT_SECONDS
        ):
            self.notify_trial_save_error(
                f"{self.SAVE_ACK_TIMEOUT_SECONDS:g} 秒内未收到保存确认"
            )
            return
        transition = self.protocol.tick(now)
        if transition is not None:
            self._handle_transition(transition, now)
        self.update()

    def _handle_transition(self, transition: ProtocolTransition, now: float) -> None:
        self._phase_started_monotonic = float(now)
        self._phase_started_wall = time.time()
        if transition.phase == ExperimentPhase.CUE:
            # Phase timestamps belong to exactly one trial.  Keeping the
            # previous trial's analysis/rest markers would corrupt alignment.
            self._phase_timestamps_wall = {}
        self._phase_timestamps_wall[transition.phase.value] = self._phase_started_wall
        metadata = self._trial_metadata()
        metadata["reason"] = transition.reason
        self._emit_phase_marker(
            transition.phase.value, metadata, self._phase_started_wall,
        )

        if transition.phase == ExperimentPhase.CUE:
            self._deferred_action = ""
            self.status_changed.emit(
                f"提示：第 {transition.trial.trial_index}/{self.protocol.total_trials} 试次 · "
                f"{transition.trial.label}"
            )
        elif transition.phase == ExperimentPhase.STIMULUS:
            payload = self._trial_metadata()
            payload.update(
                {
                    "frequency_hz": float(LABEL_TO_FREQUENCY.get(self.current_label, 0.0)),
                    "duration_seconds": float(self.config.stimulus_seconds),
                    "discard_seconds": float(self.config.discard_seconds),
                    "analysis_duration_seconds": float(self.config.analysis_seconds),
                    "marker_timestamp": self._phase_started_wall,
                }
            )
            self.capture_requested.emit(payload)
            mode = "真实空闲负样本（六块闪烁，只看中央）" if self.current_label == "空闲" else "视觉刺激"
            self.status_changed.emit(f"正在采集 {self.current_label} · {mode}")
        elif transition.phase == ExperimentPhase.SAVING:
            self.status_changed.emit("刺激结束，正在等待原始 EEG 安全保存…")
        elif transition.phase == ExperimentPhase.REST:
            self.status_changed.emit(
                f"第 {transition.trial.trial_index}/{self.protocol.total_trials} 试次已保存 · 休息"
            )
        elif transition.phase in {ExperimentPhase.FINISHED, ExperimentPhase.ABORTED}:
            self._timer.stop()
            self._emit_terminal(transition.phase, transition.reason)
        self.update()
        if transition.phase == ExperimentPhase.SAVING and self._early_saved_ack is not None:
            label, path, quality = self._early_saved_ack
            self._early_saved_ack = None
            self.notify_trial_saved(label, path, quality)

    def _trial_metadata(self) -> dict[str, object]:
        trial = self.protocol.current_trial
        if trial is None:
            return {
                "run_id": self.run_id,
                "random_seed": int(self.config.random_seed),
            }
        return {
            "run_id": self.run_id,
            "trial_id": f"{self.run_id}-trial-{trial.trial_index:04d}",
            "block_id": f"{self.run_id}-block-{trial.block_index:03d}",
            "block_index": trial.block_index,
            "trial_index": trial.trial_index,
            "repetition": trial.repetition,
            "retry_of_trial_index": trial.retry_of_trial_index,
            "total_trials": self.protocol.total_trials,
            "true_label": trial.label,
            "stimulus_profile": SSVEP_PROFILE_NAME,
            "target_frequencies_hz": list(TARGET_FREQUENCIES_HZ),
            "idle_targets_flicker": True,
            "random_seed": int(self.config.random_seed),
            "phase_timestamps": dict(self._phase_timestamps_wall),
            "phase_timestamps_unix": dict(self._phase_timestamps_wall),
            "cue_seconds": float(self.config.cue_seconds),
            "stimulus_seconds": float(self.config.stimulus_seconds),
            "discard_seconds": float(self.config.discard_seconds),
            "analysis_duration_seconds": float(self.config.analysis_seconds),
            "rest_seconds": float(self.config.rest_seconds),
        }

    def _emit_phase_marker(
        self,
        phase: str,
        metadata: dict[str, object],
        timestamp: float | None = None,
    ) -> None:
        self.phase_marker.emit(
            f"auto_ssvep_{phase}",
            float(time.time() if timestamp is None else timestamp),
            metadata,
        )

    def _emit_terminal(self, phase: ExperimentPhase, reason: str) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        status = "completed" if phase == ExperimentPhase.FINISHED else "aborted"
        summary = {
            "run_id": self.run_id,
            "status": status,
            "reason": reason,
            "saved_trials": len(self._saved_paths),
            "valid_trials": max(
                0,
                len(self._saved_paths)
                - self.protocol.rejected_trials
                - sum(status == "bad" for status in self._quality_statuses),
            ),
            "rejected_trials": self.protocol.rejected_trials,
            "low_quality_trials": sum(
                status == "bad" for status in self._quality_statuses
            ),
            "total_trials": self.protocol.total_trials,
            "paths": list(self._saved_paths),
            "random_seed": int(self.config.random_seed),
        }
        message = (
            f"自动采集完成：已保存 {len(self._saved_paths)}/{self.protocol.total_trials} 个试次"
            if status == "completed"
            else f"自动采集已中止：已保存 {len(self._saved_paths)}/{self.protocol.total_trials} 个试次"
        )
        self.status_changed.emit(message)
        self.experiment_finished.emit(status, summary)

    def _reject_current_trial(self, reason: str) -> None:
        try:
            retry = self.protocol.reject_current_trial()
        except RuntimeError as exc:
            self.status_changed.emit(str(exc))
            return
        metadata = self._trial_metadata()
        metadata.update(
            {
                "rejection_reason": str(reason),
                "quality_status": self._last_quality_status,
                "retry_trial_index": retry.trial_index,
            }
        )
        self._emit_phase_marker("rejected", metadata)
        self._deferred_action = (
            f"本试次已剔除（{reason}），已追加同标签补采 #{retry.trial_index}"
        )
        self.status_changed.emit(self._deferred_action)
        self.update()

    def paint_stimulus(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#050914"))
        now = time.monotonic()
        phase = self.protocol.phase
        trial = self.protocol.current_trial
        label = trial.label if trial is not None else "—"
        active_stimulus = (
            phase == ExperimentPhase.STIMULUS
            and not self.protocol.paused
        )
        elapsed = max(0.0, now - self._phase_started_monotonic)

        for target, rect in zip(self.targets, target_rectangles(self.width(), self.height())):
            luminance = luminance_at(elapsed, target.frequency_hz) if active_stimulus else 0.06
            gray = int(round(255 * luminance))
            painter.fillRect(rect, QColor(gray, gray, gray))
            selected = target.label == label and phase in {
                ExperimentPhase.CUE, ExperimentPhase.STIMULUS,
            }
            painter.setPen(QPen(QColor("#00e5ff") if selected else QColor("#34435b"), 4))
            painter.drawRect(rect)
            text_color = QColor("#050914") if gray > 150 else QColor("#eef6ff")
            painter.setPen(text_color)
            painter.setFont(QFont("PingFang SC", max(17, int(rect.height() / 9)), QFont.Bold))
            painter.drawText(
                QRectF(rect.left(), rect.top() + rect.height() * 0.10, rect.width(), rect.height() * 0.24),
                Qt.AlignCenter,
                target.label,
            )
            painter.setFont(QFont("Arial", max(14, int(rect.height() / 12)), QFont.Bold))
            painter.drawText(
                QRectF(rect.left(), rect.top() + rect.height() * 0.70, rect.width(), rect.height() * 0.18),
                Qt.AlignCenter,
                f"{target.frequency_hz:g} Hz",
            )
            draw_fixation_cross(painter, rect.center(), 15.0, text_color, "#050914")

        self._draw_header(painter, now, label)
        self._draw_phase_overlay(painter, now, label)
        painter.end()

    def _draw_header(self, painter: QPainter, now: float, label: str) -> None:
        phase_labels = {
            ExperimentPhase.IDLE: "等待开始",
            ExperimentPhase.CUE: "提示",
            ExperimentPhase.STIMULUS: "刺激 / 采集",
            ExperimentPhase.SAVING: "保存中",
            ExperimentPhase.REST: "休息",
            ExperimentPhase.FINISHED: "已完成",
            ExperimentPhase.ABORTED: "已中止",
        }
        trial = self.protocol.current_trial
        progress = (
            f"第 {trial.trial_index}/{self.protocol.total_trials} 试次"
            if trial is not None else f"0/{self.protocol.total_trials}"
        )
        state = "已暂停" if self.protocol.paused else phase_labels[self.protocol.phase]
        painter.fillRect(QRectF(0, 0, self.width(), 70), QColor(5, 11, 24, 245))
        painter.setPen(QColor("#e9f2ff"))
        painter.setFont(QFont("PingFang SC", 17, QFont.Bold))
        painter.drawText(
            QRectF(20, 6, self.width() - 40, 30),
            Qt.AlignCenter,
            f"SSVEP 自动随机采集 · {progress} · 标签：{label} · {state}",
        )
        painter.setPen(QColor("#91a4bd"))
        painter.setFont(QFont("PingFang SC", 12))
        timing = (
            f"提示 {self.config.cue_seconds:g}s · 刺激 {self.config.stimulus_seconds:g}s · "
            f"分析丢弃前 {self.config.discard_seconds:g}s · 休息 {self.config.rest_seconds:g}s · "
            "P 暂停/继续 · Esc 中止"
        )
        painter.drawText(QRectF(20, 37, self.width() - 40, 26), Qt.AlignCenter, timing)

    def _draw_phase_overlay(self, painter: QPainter, now: float, label: str) -> None:
        phase = self.protocol.phase
        if phase == ExperimentPhase.STIMULUS and label != "空闲" and not self.protocol.paused:
            return
        banner = QRectF(
            self.width() * 0.25,
            self.height() * 0.43,
            self.width() * 0.50,
            self.height() * 0.15,
        )
        painter.fillRect(banner, QColor(7, 16, 32, 248))
        painter.setPen(QPen(QColor("#00d8f0"), 3))
        painter.drawRect(banner)
        remaining = self.protocol.seconds_remaining(now)
        if self.protocol.paused:
            text = f"实验已暂停\n当前标签：{label} · 按 P 继续"
        elif phase == ExperimentPhase.CUE:
            text = (
                f"准备：{label}  ·  {remaining:.1f}s\n"
                + ("请自然注视中央提示区，避免看六个目标" if label == "空闲" else f"刺激开始后持续注视“{label}”中央十字")
            )
        elif phase == ExperimentPhase.STIMULUS and label == "空闲":
            text = f"空闲负样本采集中  ·  {remaining:.1f}s\n六块持续闪烁，请只注视中央文字"
        elif phase == ExperimentPhase.SAVING:
            text = "正在保存完整原始 EEG 与逐样本时间戳…\n保存确认后才会进入下一试次"
        elif phase == ExperimentPhase.REST:
            quality = {
                "good": "良好",
                "partial": "部分通道可用",
                "bad": "不合格，已标记但继续采集",
            }.get(self._last_quality_status, "未知")
            text = (
                f"休息  ·  {remaining:.1f}s · 信号质量：{quality}\n"
                "可眨眼、放松；按 R 可剔除刚才试次并自动补采"
            )
        elif phase == ExperimentPhase.FINISHED:
            text = f"采集完成\n已保存 {len(self._saved_paths)}/{self.protocol.total_trials} 个试次 · Esc 退出"
        elif phase == ExperimentPhase.ABORTED:
            text = f"采集已中止\n已保存 {len(self._saved_paths)}/{self.protocol.total_trials} 个试次 · Esc 退出"
        else:
            text = "正在准备实验…"
        if self._deferred_action:
            text += f"\n{self._deferred_action}"
        painter.setPen(QColor("#f4f8ff"))
        painter.setFont(QFont("PingFang SC", max(20, int(self.height() * 0.027)), QFont.Bold))
        painter.drawText(banner.adjusted(16, 8, -16, -8), Qt.AlignCenter, text)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_P:
            now = time.monotonic()
            if self.protocol.paused:
                if self.protocol.resume(now):
                    self._deferred_action = ""
                    self._emit_phase_marker("resumed", self._trial_metadata())
                    self.status_changed.emit("自动采集已继续")
            else:
                paused_now = self.protocol.request_pause(now)
                if paused_now:
                    self._deferred_action = ""
                    self._emit_phase_marker("paused", self._trial_metadata())
                    self.status_changed.emit("自动采集已暂停")
                elif self.protocol.phase in {ExperimentPhase.STIMULUS, ExperimentPhase.SAVING}:
                    self._deferred_action = "本试次保存后暂停"
                    self.status_changed.emit(self._deferred_action)
            self.update()
            return
        if event.key() == Qt.Key_Escape:
            if self.protocol.phase in {ExperimentPhase.FINISHED, ExperimentPhase.ABORTED}:
                self.close()
                return
            now = time.monotonic()
            transition = self.protocol.request_abort(now)
            if transition is None:
                self._deferred_action = "本试次保存后中止"
                self.status_changed.emit(self._deferred_action)
                self.update()
            else:
                self._handle_transition(transition, now)
            return
        if event.key() == Qt.Key_R and self.protocol.phase == ExperimentPhase.REST:
            self._reject_current_trial("operator_rejected")
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.running:
            now = time.monotonic()
            transition = self.protocol.request_abort(now)
            if transition is None:
                event.ignore()
                self._deferred_action = "当前试次正在保存，完成后自动中止"
                self.update()
                return
            self._handle_transition(transition, now)
        self._timer.stop()
        self.setCursor(Qt.ArrowCursor)
        super().closeEvent(event)


__all__ = ["AutomaticSSVEPExperimentWindow"]
