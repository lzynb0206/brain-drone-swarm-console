"""Native full-screen six-target SSVEP stimulus for the PySide6 application."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from src.bci.config import (
    COMMANDS,
    FBCCA_IDLE_CALIBRATION_SECONDS,
    STIMULUS_RECORDING_ROOT,
    TARGET_FREQUENCIES_HZ,
    WINDOW_SECONDS,
)
from src.bci.stimulus import StimulusSessionRecorder, StimulusTarget, luminance_at
from src.domain import IntentResult
from src.views.ssvep_rendering import draw_fixation_cross, target_rectangles


class SSVEPStimulusWindow(QWidget):
    """Render all configured targets for experiments, tests or control.

    Experiment mode accepts 0/1..6 ground-truth labels.  Control mode never
    emits a non-empty true label, activates execution only after J, and revokes
    authorization on K/Escape.  Test mode uses the same six simultaneous
    stimuli while displaying decoder evidence and never authorizing hardware
    execution.  The EEG worker remains outside this widget and receives
    synchronization markers through Qt signals.
    """

    trial_started = Signal(str, float, float)
    trial_stopped = Signal(str, str)
    event_marker = Signal(str, float)
    status_changed = Signal(str)
    emergency_requested = Signal()
    control_started = Signal()
    control_stopped = Signal()
    fresh_window_requested = Signal(float, str)

    def __init__(
        self,
        recording_root: Path | str = STIMULUS_RECORDING_ROOT,
        parent=None,
        mode: str = "experiment",
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setStyleSheet("background: black;")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)
        self.targets = tuple(
            StimulusTarget(label, float(frequency))
            for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ)
        )
        self._recorder = StimulusSessionRecorder(recording_root)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.setSingleShot(True)
        self._cooldown_timer.timeout.connect(self._finish_cooldown)
        self._running = False
        self._mode = "experiment"
        self._selected_label = "空闲"
        self._cooldown_until = 0.0
        self._cooldown_label = ""
        self._move_distance_m = 0.05
        self._command_cooldown_seconds = 2.5
        self._fresh_window_until = 0.0
        self._started_at_unix = 0.0
        self._frame_times: list[float] = []
        self._luminance: list[list[float]] = [[] for _ in self.targets]
        self._last_payload: dict[str, object] | None = None
        self._latest_recognition: IntentResult | None = None
        self._recognition_count = 0
        self._accepted_count = 0
        self.set_mode(mode)

    @property
    def control_mode(self) -> bool:
        return self._mode == "control"

    @property
    def test_mode(self) -> bool:
        return self._mode == "test"

    @property
    def blocks_hardware_execution(self) -> bool:
        """Whether this visible research window must suppress flight actions."""

        return self.test_mode and (self._running or self.isVisible())

    def set_mode(self, mode: str) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in {"experiment", "control", "test"}:
            raise ValueError(f"unsupported SSVEP window mode: {mode}")
        if self._running and normalized != self._mode:
            self.end_trial()
        self._mode = normalized
        self._selected_label = (
            "在线控制" if self.control_mode else
            "指令测试" if self.test_mode else
            "空闲"
        )
        title = (
            "SSVEP 在线驾驶" if self.control_mode else
            "SSVEP 指令识别测试" if self.test_mode else
            "SSVEP 六频刺激实验"
        )
        self._clear_recognition_results()
        self.setWindowTitle(f"脑控无人机蜂群作战系统 · {title}")
        self.update()

    @property
    def selected_label(self) -> str:
        return self._selected_label

    @property
    def running(self) -> bool:
        return self._running

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    @property
    def stimuli_active(self) -> bool:
        return self._running and self.cooldown_remaining <= 0.0

    @property
    def calibrating_idle(self) -> bool:
        return self._running and self._cooldown_label == "空闲校准" and self.cooldown_remaining > 0.0

    @property
    def fresh_window_remaining(self) -> float:
        return max(0.0, self._fresh_window_until - time.monotonic())

    def set_control_profile(
        self,
        *,
        move_distance_m: float,
        command_cooldown_seconds: float,
    ) -> None:
        """Show the same movement and cooldown values used by execution."""

        self._move_distance_m = max(0.0, float(move_distance_m))
        self._command_cooldown_seconds = max(0.0, float(command_cooldown_seconds))
        self.update()

    def begin_cooldown(self, seconds: float | None = None, label: str = "") -> None:
        """Pause every visual target after one command has been admitted."""

        if not self.control_mode or not self._running:
            return
        duration = self._command_cooldown_seconds if seconds is None else max(0.0, float(seconds))
        if duration <= 0.0:
            return
        self._cooldown_until = time.monotonic() + duration
        self._cooldown_label = str(label).strip()
        self._cooldown_timer.start(max(1, int(round(duration * 1000.0))))
        marker = (
            "SSVEP_idle_calibration_start"
            if self._cooldown_label == "空闲校准"
            else "SSVEP_command_cooldown_start"
        )
        self.event_marker.emit(marker, time.time())
        command = f"（{self._cooldown_label}）" if self._cooldown_label else ""
        if self._cooldown_label == "空闲校准":
            self.status_changed.emit(
                f"正在进行 {duration:.1f}s 空闲校准：全部刺激保持关闭，请自然看向屏幕中央"
            )
        else:
            self.status_changed.emit(
                f"脑控指令已触发{command}：视觉刺激暂停 {duration:.1f}s，请移开视线"
            )
        self.update()

    def begin_trial(self) -> None:
        if self._running:
            return
        self._clear_recognition_results()
        self._frame_times.clear()
        self._luminance = [[] for _ in self.targets]
        self._clock.restart()
        self._started_at_unix = time.time()
        self._clear_cooldown()
        self._running = True
        frequency = self._frequency_for_label(self._selected_label)
        marker = (
            "SSVEP_control_start" if self.control_mode else
            "SSVEP_command_test_start" if self.test_mode else
            f"SSVEP_{frequency:g}Hz_start" if frequency > 0 else
            "SSVEP_idle_start"
        )
        self.event_marker.emit(marker, self._started_at_unix)
        # An empty label flushes buffered EEG and resets the decoder without
        # creating a fake labelled training trial during online control or the
        # hardware-isolated research command test.
        trial_label = "" if self.control_mode or self.test_mode else self._selected_label
        self.trial_started.emit(trial_label, frequency, self._started_at_unix)
        if self.control_mode:
            self.control_started.emit()
        message = (
            "SSVEP 在线驾驶已开始" if self.control_mode else
            "SSVEP 指令识别测试已开始：仅显示识别结果，不执行无人机" if self.test_mode else
            f"刺激已开始：{self._selected_label}（{frequency:g}Hz）"
        )
        self.status_changed.emit(message)
        if self.control_mode:
            self.begin_cooldown(FBCCA_IDLE_CALIBRATION_SECONDS, "空闲校准")
        self.update()

    def end_trial(self) -> None:
        if not self._running:
            return
        self._running = False
        self._clear_cooldown()
        finished_at = time.time()
        frequency = self._frequency_for_label(self._selected_label)
        marker = (
            "SSVEP_control_stop" if self.control_mode else
            "SSVEP_command_test_stop" if self.test_mode else
            f"SSVEP_{frequency:g}Hz_stop" if frequency > 0 else
            "SSVEP_idle_stop"
        )
        self.event_marker.emit(marker, finished_at)
        # Revoke execution before spectrum rendering or file I/O so a stopped
        # stimulus can never leave a brief command-acceptance window.
        if self.control_mode:
            self.control_stopped.emit()
        try:
            payload = self._recorder.save(
                self.targets,
                self._frame_times,
                self._luminance,
                self._selected_label,
                self._started_at_unix,
            )
            self._last_payload = payload
            png_paths = self._save_spectrum_images(payload)
            summary = ", ".join(
                f"{item['label']} {float(item['measured_hz']):.2f}Hz"
                for item in payload["measurements"]
            )
            message = f"刺激已停止；实测 {summary}；频谱图 {len(png_paths)} 张"
            self.status_changed.emit(message)
            self.trial_stopped.emit(self._selected_label, str(payload["metadata_path"]))
        except Exception as exc:
            self.status_changed.emit(f"刺激停止，但测频保存失败：{exc}")
            self.trial_stopped.emit(self._selected_label, "")
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#000000"))
        elapsed = self._clock.elapsed() / 1000.0 if self._running else 0.0
        stimuli_active = self.stimuli_active
        if self._running:
            self._frame_times.append(elapsed)

        target_rects = target_rectangles(self.width(), self.height())
        for index, (target, rect) in enumerate(zip(self.targets, target_rects)):
            luminance = luminance_at(elapsed, target.frequency_hz) if stimuli_active else 0.08
            if self._running:
                self._luminance[index].append(luminance)
            gray = int(round(255 * luminance))
            painter.fillRect(rect, QColor(gray, gray, gray))
            border_color = QColor("#4b5563")
            if target.label == self._selected_label:
                border_color = QColor("#00e5ff")
            if (
                self.test_mode
                and self._latest_recognition is not None
                and target.label == self._latest_recognition.label
            ):
                border_color = QColor("#27d17f" if self._latest_recognition.accepted else "#ffb020")
            painter.setPen(QPen(border_color, 6 if border_color != QColor("#4b5563") else 4))
            painter.drawRect(rect)
            text_color = QColor("#000000") if gray > 150 else QColor("#ffffff")
            painter.setPen(text_color)
            painter.setFont(QFont("PingFang SC", max(18, int(rect.height() / 10)), QFont.Bold))
            painter.drawText(
                QRectF(rect.left(), rect.top() + rect.height() * 0.08, rect.width(), rect.height() * 0.24),
                Qt.AlignCenter,
                f"{index + 1}  {target.label}",
            )
            painter.setFont(QFont("Arial", max(16, int(rect.height() / 12)), QFont.Bold))
            painter.drawText(
                QRectF(rect.left(), rect.top() + rect.height() * 0.68, rect.width(), rect.height() * 0.22),
                Qt.AlignCenter,
                f"{target.frequency_hz:g} Hz",
            )
            draw_fixation_cross(
                painter,
                rect.center(),
                max(16.0, min(rect.width(), rect.height()) * 0.06),
                text_color,
                "#000000",
            )

        if self._running and not stimuli_active:
            self._draw_cooldown_overlay(painter)
        elif self._running and self.fresh_window_remaining > 0.0:
            self._draw_fresh_window_overlay(painter)
        elif self.test_mode:
            self._draw_recognition_test_overlay(painter)

        painter.setPen(QColor("#d1d5db"))
        painter.setFont(QFont("PingFang SC", 15))
        state = "空闲校准中" if self.calibrating_idle else (
            "冷却中（刺激已暂停）" if self._running and not stimuli_active else
            "正在建立新的完整窗口" if self.fresh_window_remaining > 0.0 else
            "刺激运行中" if self._running else "等待开始"
        )
        if self.control_mode:
            instructions = (
                f"{state} ｜在线脑控 ｜移动 {self._move_distance_m:.2f}m/次 ｜"
                f"CD {self._command_cooldown_seconds:.1f}s ｜J 开始  K 暂停  B 急停  Esc 退出"
            )
        elif self.test_mode:
            instructions = (
                f"{state} ｜科研指令测试 ｜无人机执行已禁用 ｜"
                "持续注视一个色块中央十字 ｜J 开始  K 暂停  Esc 退出"
            )
        else:
            instructions = (
                f"{state} ｜真实标签：{self._selected_label} ｜"
                "J 开始  K 停止  0 空闲  1-6 切换标签  B 急停  Esc 退出"
            )
        painter.drawText(
            QRectF(0, 12, self.width(), 42), Qt.AlignCenter,
            instructions,
        )

    def show_recognition_result(self, result: IntentResult) -> None:
        """Display one decoder decision without forwarding it to execution."""

        if not self.test_mode:
            return
        self._latest_recognition = result
        self._recognition_count += 1
        if result.accepted:
            self._accepted_count += 1
        self.update()

    def _clear_recognition_results(self) -> None:
        self._latest_recognition = None
        self._recognition_count = 0
        self._accepted_count = 0

    def _draw_recognition_test_overlay(self, painter: QPainter) -> None:
        banner = QRectF(
            self.width() * 0.13,
            self.height() * 0.43,
            self.width() * 0.74,
            self.height() * 0.15,
        )
        painter.fillRect(banner, QColor(8, 15, 28, 245))
        result = self._latest_recognition
        border = QColor("#607087")
        if result is not None:
            border = QColor("#27d17f" if result.accepted else "#ffb020")
        painter.setPen(QPen(border, 3))
        painter.drawRect(banner)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("PingFang SC", max(18, int(self.height() * 0.024)), QFont.Bold))
        message_rect = QRectF(
            banner.x() + 10,
            banner.y() + 3,
            banner.width() - 20,
            banner.height() - (32 if result is not None else 6),
        )
        if result is None:
            message = "等待第一个完整 EEG 窗口\n请持续注视一个目标中央十字"
        else:
            decision = "通过" if result.accepted else f"拒绝：{result.reason}"
            used_channels = len(result.used_channels_1based)
            message = (
                f"识别：{result.label}  {result.frequency_hz:g} Hz  ｜{decision} ｜"
                f"分数 {result.best_score:.3f}  差值 {result.margin:.3f}\n"
                f"本次测试 {self._recognition_count} 窗口 / {self._accepted_count} 通过  ｜"
                f"可用通道 {used_channels}  ｜只显示结果，不执行无人机"
            )
        painter.drawText(message_rect, Qt.AlignCenter, message)
        if result is not None:
            score_parts = [
                f"{target.label} {float(result.scores[target.label]):.3f}"
                for target in self.targets
                if target.label in result.scores
            ]
            score_text = "各指令分数：" + "  ｜  ".join(score_parts)
            painter.setPen(QColor("#a8c7fa"))
            painter.setFont(QFont("PingFang SC", max(11, int(self.height() * 0.015))))
            painter.drawText(
                QRectF(
                    banner.x() + 10,
                    banner.bottom() - 31,
                    banner.width() - 20,
                    26,
                ),
                Qt.AlignCenter,
                score_text,
            )

    def _draw_cooldown_overlay(self, painter: QPainter) -> None:
        remaining = self.cooldown_remaining
        banner = QRectF(
            self.width() * 0.30,
            self.height() * 0.455,
            self.width() * 0.40,
            self.height() * 0.105,
        )
        painter.fillRect(banner, QColor(8, 15, 28, 245))
        painter.setPen(QPen(QColor("#ffb020"), 3))
        painter.drawRect(banner)
        painter.setFont(QFont("PingFang SC", max(22, int(self.height() * 0.030)), QFont.Bold))
        if self._cooldown_label == "空闲校准":
            message = f"空闲校准 {remaining:.1f}s\n刺激已关闭 · 自然看向屏幕中央"
        else:
            command = f" · 已执行 {self._cooldown_label}" if self._cooldown_label else ""
            message = f"冷却中 {remaining:.1f}s{command}\n全部刺激已停止 · 请移开视线"
        painter.drawText(
            banner,
            Qt.AlignCenter,
            message,
        )

    def _draw_fresh_window_overlay(self, painter: QPainter) -> None:
        remaining = self.fresh_window_remaining
        banner = QRectF(
            self.width() * 0.34,
            self.height() * 0.455,
            self.width() * 0.32,
            self.height() * 0.105,
        )
        painter.fillRect(banner, QColor(8, 15, 28, 230))
        painter.setPen(QPen(QColor("#00e5ff"), 3))
        painter.drawRect(banner)
        painter.setFont(QFont("PingFang SC", max(20, int(self.height() * 0.027)), QFont.Bold))
        painter.drawText(
            banner,
            Qt.AlignCenter,
            f"正在建立新窗口 {remaining:.1f}s\n请持续注视目标 · 暂不执行指令",
        )

    def _finish_cooldown(self) -> None:
        if self._cooldown_until <= 0.0:
            return
        finished_label = self._cooldown_label
        self._cooldown_until = 0.0
        self._cooldown_label = ""
        marker = (
            "SSVEP_idle_calibration_end"
            if finished_label == "空闲校准"
            else "SSVEP_command_cooldown_end"
        )
        self.event_marker.emit(marker, time.time())
        if self._running:
            self._fresh_window_until = time.monotonic() + WINDOW_SECONDS
            self.fresh_window_requested.emit(WINDOW_SECONDS, finished_label)
            self.status_changed.emit(
                f"视觉刺激已恢复：请注视目标，先采集完整 {WINDOW_SECONDS:g}s 新窗口"
            )
        self.update()

    def _clear_cooldown(self) -> None:
        self._cooldown_timer.stop()
        self._cooldown_until = 0.0
        self._cooldown_label = ""
        self._fresh_window_until = 0.0

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if key == Qt.Key_J:
            self.begin_trial()
            return
        if key == Qt.Key_K:
            if self._running:
                self.end_trial()
            elif self.control_mode:
                # K is also a safety revoke before J has started a trial.  It
                # must not leave Bluetooth/LSL acquisition or authorization
                # running merely because the stimulus was still idle.
                self.control_stopped.emit()
                self.status_changed.emit("SSVEP 在线驾驶已暂停，脑控授权已关闭")
            return
        if key == Qt.Key_B:
            self.end_trial()
            if self.test_mode:
                self.status_changed.emit("SSVEP 指令测试已停止；未向无人机发送指令")
            else:
                self.emergency_requested.emit()
            return
        if key == Qt.Key_Escape:
            self.close()
            return
        if (self.control_mode or self.test_mode) and (key == Qt.Key_0 or Qt.Key_1 <= key <= Qt.Key_6):
            mode_label = "在线驾驶" if self.control_mode else "指令测试"
            self.status_changed.emit(f"{mode_label}由注视触发，数字键仅用于带标签实验")
            return
        if key == Qt.Key_0:
            self._selected_label = "空闲"
            self.status_changed.emit("真实标签切换为空闲")
            self.update()
            return
        if Qt.Key_1 <= key <= Qt.Key_6:
            index = key - Qt.Key_1
            if index < len(self.targets):
                self._selected_label = self.targets[index].label
                self.status_changed.emit(f"真实标签切换为：{self._selected_label}")
                self.update()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._running:
            self.end_trial()
        elif self.control_mode:
            self.control_stopped.emit()
        self.setCursor(Qt.ArrowCursor)
        super().closeEvent(event)

    def _frequency_for_label(self, label: str) -> float:
        for target in self.targets:
            if target.label == label:
                return target.frequency_hz
        return 0.0

    def _save_spectrum_images(self, payload: dict[str, object]) -> list[str]:
        archive_path = Path(str(payload["raw_timing_path"]))
        paths: list[str] = []
        with np.load(archive_path, allow_pickle=False) as archive:
            for index, target in enumerate(self.targets):
                frequencies = archive[f"frequency_{index}"]
                power = archive[f"power_{index}"]
                path = archive_path.with_name(f"{archive_path.stem}-{index + 1}-{target.frequency_hz:g}Hz.png")
                self._render_spectrum_png(path, frequencies, power, target)
                paths.append(str(path))
        return paths

    @staticmethod
    def _render_spectrum_png(path: Path, frequencies: np.ndarray, power: np.ndarray, target: StimulusTarget) -> None:
        image = QImage(960, 540, QImage.Format_ARGB32)
        image.fill(QColor("#071020"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = QRectF(80, 70, 820, 390)
        painter.setPen(QPen(QColor("#607087"), 2))
        painter.drawRect(plot)
        band = (frequencies >= 0.0) & (frequencies <= 40.0)
        x_values, y_values = frequencies[band], power[band]
        maximum = float(np.max(y_values)) if len(y_values) else 1.0
        maximum = max(maximum, 1e-12)
        painter.setPen(QPen(QColor("#00e5ff"), 2))
        previous: QPointF | None = None
        for frequency, value in zip(x_values, y_values):
            point = QPointF(
                plot.left() + float(frequency) / 40.0 * plot.width(),
                plot.bottom() - float(value) / maximum * plot.height(),
            )
            if previous is not None:
                painter.drawLine(previous, point)
            previous = point
        target_x = plot.left() + target.frequency_hz / 40.0 * plot.width()
        painter.setPen(QPen(QColor("#ff5252"), 2, Qt.DashLine))
        painter.drawLine(QPointF(target_x, plot.top()), QPointF(target_x, plot.bottom()))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("PingFang SC", 18, QFont.Bold))
        painter.drawText(QRectF(80, 20, 820, 38), Qt.AlignCenter, f"{target.label} · 目标 {target.frequency_hz:g} Hz 刺激频谱")
        painter.setFont(QFont("Arial", 12))
        painter.drawText(QRectF(80, 468, 820, 32), Qt.AlignCenter, "Frequency (Hz)")
        painter.end()
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"无法保存频谱图：{path}")
