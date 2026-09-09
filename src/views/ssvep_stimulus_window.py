"""Native full-screen six-target SSVEP stimulus for the PySide6 application."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from src.bci.config import COMMANDS, STIMULUS_RECORDING_ROOT, TARGET_FREQUENCIES_HZ
from src.bci.stimulus import StimulusSessionRecorder, StimulusTarget, luminance_at


class SSVEPStimulusWindow(QWidget):
    """Render all configured targets from a monotonic clock and record frame timing.

    J starts a synchronized trial, K stops it, 0 selects idle, 1..6 select a
    true label, and Escape closes the stimulus window.  The EEG worker remains
    outside this widget and receives trial markers through Qt signals.
    """

    trial_started = Signal(str, float, float)
    trial_stopped = Signal(str, str)
    event_marker = Signal(str, float)
    status_changed = Signal(str)
    emergency_requested = Signal()

    def __init__(self, recording_root: Path | str = STIMULUS_RECORDING_ROOT, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("脑控无人机蜂群作战系统 · SSVEP 六频刺激")
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
        self._running = False
        self._selected_label = "空闲"
        self._started_at_unix = 0.0
        self._frame_times: list[float] = []
        self._luminance: list[list[float]] = [[] for _ in self.targets]
        self._last_payload: dict[str, object] | None = None

    @property
    def selected_label(self) -> str:
        return self._selected_label

    @property
    def running(self) -> bool:
        return self._running

    def begin_trial(self) -> None:
        if self._running:
            return
        self._frame_times.clear()
        self._luminance = [[] for _ in self.targets]
        self._clock.restart()
        self._started_at_unix = time.time()
        self._running = True
        frequency = self._frequency_for_label(self._selected_label)
        marker = f"SSVEP_{frequency:g}Hz_start" if frequency > 0 else "SSVEP_idle_start"
        self.event_marker.emit(marker, self._started_at_unix)
        self.trial_started.emit(self._selected_label, frequency, self._started_at_unix)
        self.status_changed.emit(f"刺激已开始：{self._selected_label}（{frequency:g}Hz）")
        self.update()

    def end_trial(self) -> None:
        if not self._running:
            return
        self._running = False
        finished_at = time.time()
        frequency = self._frequency_for_label(self._selected_label)
        marker = f"SSVEP_{frequency:g}Hz_stop" if frequency > 0 else "SSVEP_idle_stop"
        self.event_marker.emit(marker, finished_at)
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
        if self._running:
            self._frame_times.append(elapsed)

        margin_x = max(30, self.width() // 18)
        margin_y = max(70, self.height() // 11)
        gap_x = max(24, self.width() // 40)
        gap_y = max(28, self.height() // 22)
        block_width = (self.width() - 2 * margin_x - 2 * gap_x) / 3.0
        block_height = (self.height() - 2 * margin_y - gap_y) / 2.0
        for index, target in enumerate(self.targets):
            row, column = divmod(index, 3)
            rect = QRectF(
                margin_x + column * (block_width + gap_x),
                margin_y + row * (block_height + gap_y),
                block_width,
                block_height,
            )
            luminance = luminance_at(elapsed, target.frequency_hz) if self._running else 0.12
            if self._running:
                self._luminance[index].append(luminance)
            gray = int(round(255 * luminance))
            painter.fillRect(rect, QColor(gray, gray, gray))
            painter.setPen(QPen(QColor("#00e5ff") if target.label == self._selected_label else QColor("#4b5563"), 4))
            painter.drawRect(rect)
            text_color = QColor("#000000") if gray > 150 else QColor("#ffffff")
            painter.setPen(text_color)
            painter.setFont(QFont("PingFang SC", max(18, int(block_height / 11)), QFont.Bold))
            painter.drawText(rect, Qt.AlignCenter, f"{index + 1}  {target.label}\n{target.frequency_hz:g} Hz")
            self._draw_fixation_cross(painter, rect.center(), max(16.0, min(block_width, block_height) * 0.055), text_color)

        painter.setPen(QColor("#d1d5db"))
        painter.setFont(QFont("PingFang SC", 15))
        state = "刺激运行中" if self._running else "等待开始"
        painter.drawText(
            QRectF(0, 12, self.width(), 42), Qt.AlignCenter,
            f"{state} ｜真实标签：{self._selected_label} ｜J 开始  K 停止  0 空闲  1-6 切换标签  B 急停  Esc 退出",
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        if key == Qt.Key_J:
            self.begin_trial()
            return
        if key == Qt.Key_K:
            self.end_trial()
            return
        if key == Qt.Key_B:
            self.end_trial()
            self.emergency_requested.emit()
            return
        if key == Qt.Key_Escape:
            self.close()
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
        self.setCursor(Qt.ArrowCursor)
        super().closeEvent(event)

    def _frequency_for_label(self, label: str) -> float:
        for target in self.targets:
            if target.label == label:
                return target.frequency_hz
        return 0.0

    @staticmethod
    def _draw_fixation_cross(painter: QPainter, center: QPointF, half_length: float, color: QColor) -> None:
        outer = QColor("#000000") if color.lightness() > 128 else QColor("#ffffff")
        painter.setPen(QPen(outer, 6))
        painter.drawLine(QPointF(center.x() - half_length, center.y()), QPointF(center.x() + half_length, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - half_length), QPointF(center.x(), center.y() + half_length))
        painter.setPen(QPen(color, 3))
        painter.drawLine(QPointF(center.x() - half_length, center.y()), QPointF(center.x() + half_length, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - half_length), QPointF(center.x(), center.y() + half_length))

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
