"""Native full-screen six-target SSVEP stimulus for the PySide6 application."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter, QPen

from src.bci.config import (
    COMMANDS,
    FBCCA_IDLE_CALIBRATION_SECONDS,
    RESEARCH_DATA_ROOT,
    SOLDIER_SSVEP_DATA_ROOT,
    SSVEP_TEST_DATA_ROOT,
    TARGET_FREQUENCIES_HZ,
    WINDOW_SECONDS,
)
from src.bci.stimulus import StimulusSessionRecorder, StimulusTarget, luminance_at
from src.domain import IntentResult
from src.views.ssvep_rendering import (
    SSVEP_ACCENT,
    SSVEP_CANVAS_BACKGROUND,
    SSVEP_TARGET_BORDER,
    draw_ssvep_header,
    draw_ssvep_status_banner,
    draw_ssvep_target,
    target_rectangles,
)
from src.views.stimulus_canvas import StimulusCanvas, StimulusFrameDriver


class SSVEPStimulusWindow(StimulusCanvas):
    """Render all configured targets for experiments, tests or control.

    Experiment mode accepts 0/1..6 ground-truth labels.  Control mode never
    emits a non-empty true label, activates execution only after J, and revokes
    authorization on K/Escape.  Test mode mirrors the complete control-mode
    visual lifecycle (idle calibration, fresh window and command cooldown)
    while never authorizing hardware execution.  The EEG worker remains
    outside this widget and receives synchronization markers through Qt signals.
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
        recording_root: Path | str | None = None,
        recording_roots: Mapping[str, Path | str] | None = None,
        parent=None,
        mode: str = "experiment",
    ) -> None:
        super().__init__(parent, Qt.Window)
        if recording_root is not None and recording_roots is not None:
            raise ValueError("use recording_root or recording_roots, not both")
        default_roots = {
            "experiment": RESEARCH_DATA_ROOT / "stimulus_logs",
            "test": SSVEP_TEST_DATA_ROOT / "stimulus_logs",
            "control": SOLDIER_SSVEP_DATA_ROOT / "stimulus_logs",
        }
        if recording_root is not None:
            default_roots = {key: Path(recording_root) for key in default_roots}
        elif recording_roots is not None:
            default_roots.update({key: Path(value) for key, value in recording_roots.items()})
        self._recording_roots = default_roots
        self.setStyleSheet(f"background: {SSVEP_CANVAS_BACKGROUND};")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)
        self.targets = tuple(
            StimulusTarget(label, float(frequency))
            for label, frequency in zip(COMMANDS, TARGET_FREQUENCIES_HZ)
        )
        self._recorder: StimulusSessionRecorder | None = None
        self._clock = QElapsedTimer()
        self._timer = StimulusFrameDriver(self)
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
        self._idle_calibration_seconds = FBCCA_IDLE_CALIBRATION_SECONDS
        self._fresh_window_until = 0.0
        self._started_at_unix = 0.0
        self._frame_times: list[float] = []
        self._luminance: list[list[float]] = [[] for _ in self.targets]
        self._last_payload: dict[str, object] | None = None
        self._latest_recognition: IntentResult | None = None
        self._recognition_count = 0
        self._accepted_count = 0
        self._simulation_pending_label = ""
        self._simulation_pending_count = 0
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
        self._recorder = StimulusSessionRecorder(self._recording_roots[normalized])
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
        return self._running and (self.cooldown_remaining <= 0.0 or self.calibrating_idle)

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

    def set_idle_calibration_seconds(self, seconds: float) -> None:
        """Use decoder-specific startup behavior without changing the page."""

        self._idle_calibration_seconds = max(0.0, float(seconds))

    def begin_cooldown(self, seconds: float | None = None, label: str = "") -> None:
        """Pause every visual target after one command has been admitted."""

        if not (self.control_mode or self.test_mode) or not self._running:
            return
        duration = self._command_cooldown_seconds if seconds is None else max(0.0, float(seconds))
        if duration <= 0.0:
            return
        self._cooldown_until = time.monotonic() + duration
        self._cooldown_label = str(label).strip()
        self._reset_simulation_confirmation()
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
                f"正在进行 {duration:.1f}s 空闲校准：六块持续闪烁，请只看屏幕中央文字"
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
        if self.control_mode or self.test_mode:
            if self._idle_calibration_seconds > 0.0:
                self.begin_cooldown(self._idle_calibration_seconds, "空闲校准")
            else:
                self._fresh_window_until = time.monotonic() + WINDOW_SECONDS
                self.fresh_window_requested.emit(WINDOW_SECONDS, "个人模板就绪")
                self.status_changed.emit(
                    f"个人模板已加载：请注视目标，先采集完整 {WINDOW_SECONDS:g}s 新窗口"
                )
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
            if self._recorder is None:
                raise RuntimeError("刺激记录目录尚未初始化")
            payload = self._recorder.save(
                self.targets,
                self._frame_times,
                self._luminance,
                self._selected_label,
                self._started_at_unix,
                display_refresh_rate_hz=float(self.screen().refreshRate()),
                timing_source=self.presentation_source,
            )
            self._last_payload = payload
            png_paths = self._save_spectrum_images(payload)
            summary = ", ".join(
                f"{item['label']} {float(item['measured_hz']):.2f}Hz"
                for item in payload["measurements"]
            )
            message = f"刺激已停止；绘制时序估计 {summary}（非光电实测）；频谱图 {len(png_paths)} 张"
            self.status_changed.emit(message)
            self.trial_stopped.emit(self._selected_label, str(payload["metadata_path"]))
        except Exception as exc:
            self.status_changed.emit(f"刺激停止，但测频保存失败：{exc}")
            self.trial_stopped.emit(self._selected_label, "")
        self.update()

    def paint_stimulus(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(SSVEP_CANVAS_BACKGROUND))
        elapsed = self._clock.nsecsElapsed() / 1_000_000_000.0 if self._running else 0.0
        stimuli_active = self.stimuli_active
        if self._running:
            self._frame_times.append(elapsed)

        target_rects = target_rectangles(self.width(), self.height())
        for index, (target, rect) in enumerate(zip(self.targets, target_rects)):
            luminance = luminance_at(elapsed, target.frequency_hz) if stimuli_active else 0.06
            if self._running:
                self._luminance[index].append(luminance)
            border_color, border_width, border_style = self._target_frame_style(target.label)
            draw_ssvep_target(
                painter,
                rect,
                target.label,
                target.frequency_hz,
                luminance,
                border_color=border_color,
                border_width=border_width,
                border_style=border_style,
            )

        state = self._display_state(stimuli_active)
        self._draw_header(painter, state)
        if self._running and self.cooldown_remaining > 0.0:
            self._draw_cooldown_overlay(painter)
        elif self._running and self.fresh_window_remaining > 0.0:
            self._draw_fresh_window_overlay(painter)
        elif self._running and (self.control_mode or self.test_mode):
            self._draw_online_result(painter)
        elif not self._running or self._selected_label == "空闲":
            self._draw_experiment_overlay(painter)
        painter.end()

    def _display_state(self, stimuli_active: bool) -> str:
        return "空闲校准中" if self.calibrating_idle else (
            "冷却中（刺激已暂停）" if self._running and not stimuli_active else
            "正在建立新的完整窗口" if self.fresh_window_remaining > 0.0 else
            "刺激运行中" if self._running else "等待开始"
        )

    def _draw_header(self, painter: QPainter, state: str) -> None:
        if self.control_mode:
            title = f"SSVEP 在线驾驶 · 实机操纵 · {state}"
            subtitle = (
                f"移动 {self._move_distance_m:.2f}m/次 · 指令冷却 {self._command_cooldown_seconds:.1f}s · "
                "J 开始 · K 暂停 · B 急停 · Esc 退出"
            )
        elif self.test_mode:
            title = f"SSVEP 指令识别测试 · 科研安全模拟 · {state}"
            subtitle = (
                f"移动 {self._move_distance_m:.2f}m/次 · 指令冷却 {self._command_cooldown_seconds:.1f}s · "
                "J 开始 · K 暂停 · B 停止 · Esc 退出"
            )
        else:
            title = f"SSVEP 带标签实验 · 标签：{self._selected_label} · {state}"
            subtitle = (
                "J 开始 · K 停止 · 0 空闲 · 1–6 切换标签 · "
                "B 急停 · Esc 退出"
            )
        draw_ssvep_header(painter, self.width(), title, subtitle)

    def _draw_experiment_overlay(self, painter: QPainter) -> None:
        if not self._running:
            if self.control_mode:
                text = "在线驾驶待开始\n按 J 启动视觉刺激与实机脑控"
            elif self.test_mode:
                text = "指令识别测试待开始\n按 J 启动；测试模式不会发送无人机指令"
            else:
                text = "带标签实验待开始\n按 0 / 1–6 选择真实标签，按 J 开始"
        else:
            text = "空闲负样本采集中\n六块持续闪烁，请只注视中央文字"
        draw_ssvep_status_banner(
            painter,
            self.width(),
            self.height(),
            text,
        )

    def show_recognition_result(self, result: IntentResult) -> None:
        """Show the same live candidate frame in control and safe test modes."""

        if not (self.control_mode or self.test_mode):
            return
        self._latest_recognition = result
        self._recognition_count += 1
        if result.accepted:
            self._accepted_count += 1
        self.update()

    def _target_frame_style(self, label: str) -> tuple[QColor, int, Qt.PenStyle]:
        """Return the visible frame used to point out the decoder's candidate."""

        result = self._latest_recognition
        if (
            (self.control_mode or self.test_mode)
            and self._running
            and result is not None
            and result.label == label
            and result.label != "空闲"
        ):
            if result.accepted:
                return QColor("#39f39b"), 11, Qt.SolidLine
            return QColor("#ffb020"), 8, Qt.DashLine
        if label == self._selected_label:
            return QColor(SSVEP_ACCENT), 4, Qt.SolidLine
        return QColor(SSVEP_TARGET_BORDER), 4, Qt.SolidLine

    def process_simulated_control_result(self, result: IntentResult) -> bool:
        """Mirror command admission and cooldown without touching a gateway."""

        if (
            not self.test_mode
            or not self._running
            or not self.stimuli_active
            or self.calibrating_idle
            or self.fresh_window_remaining > 0.0
        ):
            return False
        self.show_recognition_result(result)
        if not result.accepted or result.label == "空闲":
            self._reset_simulation_confirmation()
            return False
        if result.safety_verified:
            confirmed = True
        else:
            if result.label == self._simulation_pending_label:
                self._simulation_pending_count += 1
            else:
                self._simulation_pending_label = result.label
                self._simulation_pending_count = 1
            confirmed = self._simulation_pending_count >= 2
        if not confirmed:
            return False
        label = result.label
        self._reset_simulation_confirmation()
        self.begin_cooldown(self._command_cooldown_seconds, label)
        return True

    def _reset_simulation_confirmation(self) -> None:
        self._simulation_pending_label = ""
        self._simulation_pending_count = 0

    def _clear_recognition_results(self) -> None:
        self._latest_recognition = None
        self._recognition_count = 0
        self._accepted_count = 0
        self._reset_simulation_confirmation()

    def _draw_cooldown_overlay(self, painter: QPainter) -> None:
        remaining = self.cooldown_remaining
        if self._cooldown_label == "空闲校准":
            message = f"空闲校准 {remaining:.1f}s\n只看这里 · 不注视周围色块"
        elif self.test_mode:
            command = f" · 模拟 {self._cooldown_label}" if self._cooldown_label else ""
            message = (
                f"冷却中 {remaining:.1f}s{command}\n"
                "全部刺激已停止 · 请移开视线"
            )
        else:
            command = f" · 已执行 {self._cooldown_label}" if self._cooldown_label else ""
            message = f"冷却中 {remaining:.1f}s{command}\n全部刺激已停止 · 请移开视线"
        draw_ssvep_status_banner(
            painter,
            self.width(),
            self.height(),
            message,
            border_color="#ffb020",
        )

    def _draw_online_result(self, painter: QPainter) -> None:
        result = self._latest_recognition
        accepted = result is not None and result.accepted
        if result is None:
            title = "请持续注视一个目标框"
            detail = "正在等待完整脑电窗口"
        else:
            quality = result.signal_quality or {}
            score_threshold = float(quality.get("score_threshold", 0.0) or 0.0)
            margin_threshold = float(quality.get("margin_threshold", 0.0) or 0.0)
            used_count = len(result.used_channels_1based)
            total_count = int(quality.get("total_channel_count", used_count) or used_count)
            title = (
                f"已通过识别门限：{result.label}"
                if accepted else
                f"实时候选：{result.label}（未通过门限）"
            )
            detail = (
                f"分数 {result.confidence:.3f}/{score_threshold:.3f} · "
                f"差值 {result.margin:.3f}/{margin_threshold:.3f} · "
                f"可用通道 {used_count}/{total_count}"
            )
            if not accepted and used_count <= 2:
                detail += " · 通道不足/工频干扰，暂不确认"
        if self.test_mode:
            detail += " · 测试模式不发送无人机指令"
        frame_color = QColor("#39f39b") if accepted else QColor("#cbd5e1")
        draw_ssvep_status_banner(
            painter,
            self.width(),
            self.height(),
            f"{title}\n{detail}",
            border_color=frame_color,
            text_color=frame_color,
        )

    def _draw_fresh_window_overlay(self, painter: QPainter) -> None:
        remaining = self.fresh_window_remaining
        draw_ssvep_status_banner(
            painter,
            self.width(),
            self.height(),
            f"正在建立新窗口 {remaining:.1f}s\n请持续注视目标 · 暂不执行指令",
        )

    def _finish_cooldown(self) -> None:
        if self._cooldown_until <= 0.0:
            return
        finished_label = self._cooldown_label
        self._cooldown_until = 0.0
        self._cooldown_label = ""
        self._reset_simulation_confirmation()
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
