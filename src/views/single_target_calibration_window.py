"""Full-screen calibration window showing exactly one flickering target."""

from __future__ import annotations

import time
import uuid

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen

from src.bci.config import COMMANDS, LABEL_TO_FREQUENCY, SSVEP_STIMULUS_LAYOUT
from src.bci.stimulus import luminance_at
from src.ml.personal_template_model import GUIDED_CALIBRATION_CONDITION
from src.services.single_target_calibration_protocol import (
    SingleTargetCalibrationConfig,
    SingleTargetTrial,
    build_single_target_plan,
)
from src.views.ssvep_rendering import draw_fixation_cross, target_rectangles
from src.views.stimulus_canvas import StimulusCanvas, StimulusFrameDriver


class SingleTargetCalibrationWindow(StimulusCanvas):
    """Run an isolated one-target protocol; recording is owned by its controller."""

    capture_requested = Signal(object)
    status_changed = Signal(str)
    run_finished = Signal(str, object)
    SAVE_TIMEOUT_SECONDS = 8.0

    def __init__(
        self,
        config: SingleTargetCalibrationConfig | None = None,
        parent=None,
        *,
        mode: str = "isolated",
    ) -> None:
        super().__init__(parent, Qt.Window)
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in {"isolated", "guided"}:
            raise ValueError("calibration mode must be isolated or guided")
        self.mode = normalized_mode
        self.config = (config or SingleTargetCalibrationConfig()).validate()
        self.plan = build_single_target_plan(self.config)
        prefix = "guided" if self.mode == "guided" else "single"
        self.run_id = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.phase = "idle"
        self.current_index = -1
        self._phase_started = 0.0
        self._deadline: float | None = None
        self._early_saved: tuple[str, str, str] | None = None
        self._saved_paths: list[str] = []
        self._last_quality = "unknown"
        self._terminal_emitted = False
        self._timer = StimulusFrameDriver(self)
        self._timer.timeout.connect(self._tick)
        self.setWindowTitle(
            "SSVEP 六目标引导校准" if self.mode == "guided" else "SSVEP 单目标诊断采集"
        )
        self.setStyleSheet("background: #050914;")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)

    @property
    def running(self) -> bool:
        return self.phase not in {"idle", "finished", "aborted"}

    @property
    def current_trial(self) -> SingleTargetTrial | None:
        if 0 <= self.current_index < len(self.plan):
            return self.plan[self.current_index]
        return None

    def start_run(self) -> None:
        if self.phase != "idle":
            return
        self.current_index = 0
        self._timer.start()
        self._enter("cue")
        name = "六目标引导校准" if self.mode == "guided" else "单目标诊断"
        self.status_changed.emit(f"{name}开始：共 {len(self.plan)} 轮")

    def notify_trial_saved(
        self, label: str, path: str, quality_status: str = "unknown",
    ) -> bool:
        trial = self.current_trial
        if trial is None or label != trial.label:
            return False
        ack = (str(label), str(path), str(quality_status or "unknown"))
        if self.phase == "stimulus":
            self._early_saved = ack
            return True
        if self.phase != "saving":
            return False
        self._saved_paths.append(str(path))
        self._last_quality = ack[2]
        self._enter("rest")
        return True

    def abort(self, reason: str) -> None:
        if self.phase not in {"finished", "aborted"}:
            self._finish("aborted", reason)
        self.close()

    def _tick(self) -> None:
        now = time.monotonic()
        if self.phase == "saving" and now - self._phase_started >= self.SAVE_TIMEOUT_SECONDS:
            self._finish("aborted", "等待原始 EEG 保存超时")
            return
        if self._deadline is None or now < self._deadline:
            self.update()
            return
        if self.phase == "cue":
            self._enter("stimulus")
        elif self.phase == "stimulus":
            self._enter("saving")
        elif self.phase == "rest":
            if self.current_index + 1 >= len(self.plan):
                self._finish(
                    "completed",
                    "全部六目标引导试次已保存"
                    if self.mode == "guided" else "全部单目标诊断试次已保存",
                )
            else:
                self.current_index += 1
                self._enter("cue")
        self.update()

    def _enter(self, phase: str) -> None:
        self.phase = phase
        self._phase_started = time.monotonic()
        duration = {
            "cue": self.config.cue_seconds,
            "stimulus": self.config.stimulus_seconds,
            "rest": self.config.rest_seconds,
        }.get(phase)
        self._deadline = None if duration is None else self._phase_started + float(duration)
        trial = self.current_trial
        if trial is None:
            return
        if phase == "cue":
            self.status_changed.emit(
                f"准备 {trial.trial_index}/{len(self.plan)}：{trial.label}"
            )
        elif phase == "stimulus":
            guided = self.mode == "guided"
            self.capture_requested.emit({
                "run_id": self.run_id,
                "trial_id": f"{self.run_id}-trial-{trial.trial_index:03d}",
                "trial_index": trial.trial_index,
                "repetition": trial.repetition,
                "true_label": trial.label,
                "frequency_hz": float(LABEL_TO_FREQUENCY[trial.label]),
                "duration_seconds": float(self.config.stimulus_seconds),
                "discard_seconds": float(self.config.discard_seconds),
                "stimulus_layout": SSVEP_STIMULUS_LAYOUT,
                "calibration_condition": (
                    GUIDED_CALIBRATION_CONDITION if guided else "isolated_single_target_v1"
                ),
                "visible_target_count": len(COMMANDS) if guided else 1,
                "target_slot_index": COMMANDS.index(trial.label),
                "stimulus_started_unix": time.time(),
            })
            self.status_changed.emit(
                f"六目标同时闪烁，请只看：{trial.label}"
                if guided else f"正在记录唯一目标：{trial.label}"
            )
        elif phase == "saving":
            self.status_changed.emit("正在保存本轮原始 EEG…")
            if self._early_saved is not None:
                ack, self._early_saved = self._early_saved, None
                self.notify_trial_saved(*ack)
        elif phase == "rest":
            self.status_changed.emit(
                f"已保存 {len(self._saved_paths)}/{len(self.plan)}；质量 {self._last_quality}"
            )

    def _finish(self, status: str, reason: str) -> None:
        if self._terminal_emitted:
            return
        self.phase = "finished" if status == "completed" else "aborted"
        self._deadline = None
        self._timer.stop()
        self.setCursor(Qt.ArrowCursor)
        self._terminal_emitted = True
        summary = {
            "run_id": self.run_id,
            "status": status,
            "reason": str(reason),
            "saved_trials": len(self._saved_paths),
            "total_trials": len(self.plan),
            "paths": list(self._saved_paths),
        }
        name = "六目标引导校准" if self.mode == "guided" else "单目标诊断"
        self.status_changed.emit(
            f"{name}{'完成' if status == 'completed' else '中止'}："
            f"{len(self._saved_paths)}/{len(self.plan)}"
        )
        self.run_finished.emit(status, summary)
        self.update()

    def paint_stimulus(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#000000"))
        trial = self.current_trial
        if trial is None:
            painter.end()
            return
        active = self.phase == "stimulus"
        elapsed = max(0.0, time.monotonic() - self._phase_started)
        rects = target_rectangles(self.width(), self.height())
        if self.phase in {"cue", "stimulus"}:
            labels = COMMANDS if self.mode == "guided" else (trial.label,)
            for label in labels:
                rect = rects[COMMANDS.index(label)]
                luminance = (
                    luminance_at(elapsed, LABEL_TO_FREQUENCY[label]) if active else 0.06
                )
                gray = int(round(255 * luminance))
                painter.fillRect(rect, QColor(gray, gray, gray))
                is_target = label == trial.label
                painter.setPen(QPen(
                    QColor("#00e5ff") if is_target else QColor("#4b5563"),
                    6 if is_target else 4,
                ))
                painter.drawRect(rect)
                color = QColor("#000000") if gray > 150 else QColor("#eef6ff")
                painter.setPen(color)
                painter.setFont(QFont(
                    "PingFang SC", max(18, int(rect.height() / 7)), QFont.Bold,
                ))
                painter.drawText(
                    QRectF(
                        rect.left(), rect.top() + rect.height() * 0.08,
                        rect.width(), rect.height() * 0.28,
                    ),
                    Qt.AlignCenter,
                    label,
                )
                painter.setFont(QFont(
                    "Arial", max(15, int(rect.height() / 13)), QFont.Bold,
                ))
                painter.drawText(
                    QRectF(
                        rect.left(), rect.top() + rect.height() * 0.73,
                        rect.width(), rect.height() * 0.15,
                    ),
                    Qt.AlignCenter,
                    f"{LABEL_TO_FREQUENCY[label]:g} Hz",
                )
                draw_fixation_cross(
                    painter, rect.center(), max(16.0, rect.height() * 0.06),
                    color, "#000000",
                )
        if not active:
            self._draw_overlay(painter, trial)
        painter.end()

    def _draw_overlay(self, painter: QPainter, trial: SingleTargetTrial) -> None:
        banner = QRectF(
            self.width() * 0.24,
            self.height() * 0.43,
            self.width() * 0.52,
            self.height() * 0.16,
        )
        painter.fillRect(banner, QColor(7, 16, 32, 248))
        painter.setPen(QPen(QColor("#00d8f0"), 3))
        painter.drawRect(banner)
        if self.phase == "cue":
            remaining = max(0.0, (self._deadline or time.monotonic()) - time.monotonic())
            text = (
                f"准备：只看 {trial.label} · {remaining:.1f}s\n"
                "六块会同时闪烁，只盯蓝框内十字"
                if self.mode == "guided" else
                f"准备：{trial.label} · {remaining:.1f}s\n开始闪烁后只盯中央十字"
            )
        elif self.phase == "saving":
            text = "正在保存原始 EEG…"
        elif self.phase == "rest":
            text = f"休息 · 已保存 {len(self._saved_paths)}/{len(self.plan)}"
        elif self.phase == "finished":
            text = f"采集完成 · 已保存 {len(self._saved_paths)}/{len(self.plan)} · Esc退出"
        else:
            text = f"采集已中止 · 已保存 {len(self._saved_paths)}/{len(self.plan)} · Esc退出"
        painter.setPen(QColor("#f4f8ff"))
        painter.setFont(QFont("PingFang SC", max(20, int(self.height() * 0.027)), QFont.Bold))
        painter.drawText(banner.adjusted(16, 8, -16, -8), Qt.AlignCenter, text)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            if self.phase in {"finished", "aborted"}:
                self.close()
            else:
                self.abort("用户中止")
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.running:
            self._finish("aborted", "窗口关闭")
        self._timer.stop()
        self.setCursor(Qt.ArrowCursor)
        super().closeEvent(event)


__all__ = ["SingleTargetCalibrationWindow"]
