from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.bci.preprocessing import assess_signal_quality
from src.bci.stimulus import measure_frequency
from src.domain import IntentResult
from src.services.ssvep_experiment_protocol import AutomaticExperimentConfig
from src.views.automatic_ssvep_experiment_window import AutomaticSSVEPExperimentWindow
from src.views.ssvep_rendering import (
    SSVEP_CANVAS_BACKGROUND,
    SSVEP_TARGET_BORDER,
    ssvep_status_banner_rect,
    target_rectangles,
)
from src.views.ssvep_stimulus_window import SSVEPStimulusWindow
from src.views.stimulus_canvas import StimulusFrameDriver


class StimulusMeasurementTest(unittest.TestCase):
    def test_measures_rendered_frequency_from_frame_timestamps(self) -> None:
        refresh_hz = 120.0
        times = np.arange(0.0, 4.0, 1.0 / refresh_hz)
        values = 0.5 + 0.5 * np.sin(2.0 * np.pi * 13.75 * times)
        result, _, _ = measure_frequency(times, values, 13.75)
        self.assertAlmostEqual(13.75, result.measured_hz, delta=0.3)
        self.assertAlmostEqual(refresh_hz, result.refresh_rate_hz, delta=0.5)


class SignalQualityTest(unittest.TestCase):
    def test_flags_flat_and_50hz_dominated_channels(self) -> None:
        fs = 250.0
        timeline = np.arange(1000) / fs
        data = np.vstack((
            np.zeros_like(timeline),
            20.0 * np.sin(2.0 * np.pi * 50.0 * timeline),
            10.0 * np.sin(2.0 * np.pi * 13.75 * timeline),
        ))
        quality = assess_signal_quality(data, fs)
        self.assertIn("平坦/方差过低", quality["channels"][0]["issues"])
        self.assertIn("50Hz工频占比过高", quality["channels"][1]["issues"])
        self.assertTrue(quality["channels"][2]["usable"])


class StimulusWindowModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_frame_driver_caps_repeated_swap_callbacks_and_stops_cleanly(self) -> None:
        from PySide6.QtWidgets import QWidget
        canvas = QWidget()
        driver = StimulusFrameDriver(canvas)
        emitted = []
        driver.timeout.connect(lambda: emitted.append(True))
        try:
            with patch("src.views.stimulus_canvas.time.monotonic", return_value=10.0):
                driver.start()
                for _ in range(10):
                    driver._frame()
                self.assertEqual(1, len(emitted))
                self.assertGreater(driver._next_frame, 10.0)
                driver.stop()
                self.assertFalse(driver._request_timer.isActive())
                driver._frame()
                self.assertEqual(1, len(emitted))
        finally:
            driver.stop()
            canvas.close()

    def test_all_ssvep_modes_share_the_acquisition_visual_system(self) -> None:
        windows = [
            AutomaticSSVEPExperimentWindow(AutomaticExperimentConfig()),
            SSVEPStimulusWindow(mode="experiment"),
            SSVEPStimulusWindow(mode="test"),
            SSVEPStimulusWindow(mode="control"),
        ]
        try:
            header_colors = []
            canvas_colors = []
            for window in windows:
                self.assertIn(SSVEP_CANVAS_BACKGROUND, window.styleSheet())
                window.resize(1280, 720)
                window.show()
                self.app.processEvents()
                image = window.grab().toImage()
                header_colors.append(image.pixelColor(4, 4).name())
                canvas_colors.append(image.pixelColor(4, 100).name())

            self.assertEqual(1, len(set(header_colors)))
            self.assertEqual([SSVEP_CANVAS_BACKGROUND] * len(windows), canvas_colors)
            banner = ssvep_status_banner_rect(1280, 720)
            self.assertAlmostEqual(320.0, banner.left())
            self.assertAlmostEqual(309.6, banner.top())
            self.assertAlmostEqual(640.0, banner.width())
            self.assertAlmostEqual(108.0, banner.height())
            self.assertEqual(
                SSVEP_TARGET_BORDER,
                windows[-1]._target_frame_style("起飞")[0].name(),
            )
        finally:
            for window in windows:
                window._timer.stop()
                if isinstance(window, SSVEPStimulusWindow):
                    window._cooldown_timer.stop()
                window.close()

    def test_each_mode_uses_its_dedicated_stimulus_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roots = {
                "experiment": root / "research",
                "test": root / "test",
                "control": root / "soldier",
            }
            window = SSVEPStimulusWindow(recording_roots=roots, mode="test")
            try:
                self.assertEqual(roots["test"].resolve(), window._recorder.root)
                window.set_mode("control")
                self.assertEqual(roots["control"].resolve(), window._recorder.root)
                window.set_mode("experiment")
                self.assertEqual(roots["experiment"].resolve(), window._recorder.root)
            finally:
                window._timer.stop()
                window.close()

    def test_control_mode_flushes_decoder_without_creating_true_label(self) -> None:
        window = SSVEPStimulusWindow(mode="control")
        started = []
        control_started = []
        window.trial_started.connect(lambda *args: started.append(args))
        window.control_started.connect(lambda: control_started.append(True))
        try:
            window.begin_trial()

            self.assertTrue(window.control_mode)
            self.assertEqual("在线控制", window.selected_label)
            self.assertEqual("", started[0][0])
            self.assertEqual(0.0, started[0][1])
            self.assertEqual([True], control_started)
            self.assertTrue(window.calibrating_idle)
            self.assertTrue(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window.close()

    def test_command_test_starts_with_same_idle_calibration_as_control(self) -> None:
        window = SSVEPStimulusWindow(mode="test")
        started = []
        control_started = []
        window.trial_started.connect(lambda *args: started.append(args))
        window.control_started.connect(lambda: control_started.append(True))
        try:
            window.begin_trial()

            self.assertTrue(window.test_mode)
            self.assertTrue(window.blocks_hardware_execution)
            self.assertEqual("指令测试", window.selected_label)
            self.assertEqual("", started[0][0])
            self.assertEqual(0.0, started[0][1])
            self.assertEqual([], control_started)
            self.assertTrue(window.calibrating_idle)
            self.assertTrue(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window.close()

    def test_command_test_requires_same_two_window_confirmation_then_starts_cd(self) -> None:
        window = SSVEPStimulusWindow(mode="test")
        result = IntentResult(
            timestamp=1.0,
            label="前进",
            confidence=0.82,
            margin=0.21,
            accepted=True,
            reason="",
            frequency_hz=11.25,
            scores={"起飞": 0.22, "前进": 0.82},
            sample_rate_hz=250.0,
            sample_count=500,
            used_channels_1based=(1, 2, 3, 4),
        )
        try:
            window.begin_trial()
            window._finish_cooldown()
            window._fresh_window_until = 0.0

            self.assertFalse(window.process_simulated_control_result(result))
            self.assertTrue(window.process_simulated_control_result(result))
            self.assertIs(result, window._latest_recognition)
            self.assertEqual(2, window._recognition_count)
            self.assertEqual(2, window._accepted_count)
            self.assertGreater(window.cooldown_remaining, 2.0)
            self.assertFalse(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window._cooldown_timer.stop()
            window.close()

    def test_command_test_keeps_rejected_candidate_visible_for_diagnosis(self) -> None:
        window = SSVEPStimulusWindow(mode="test")
        result = IntentResult(
            timestamp=1.0,
            label="前进",
            confidence=0.07,
            margin=0.01,
            accepted=False,
            reason="未超过该频率空闲背景",
            frequency_hz=16.0,
            scores={"前进": 0.07},
            sample_rate_hz=250.0,
            sample_count=500,
            used_channels_1based=(4,),
            signal_quality={
                "score_threshold": 0.35,
                "margin_threshold": 0.177,
                "total_channel_count": 8,
            },
        )
        try:
            window.show_recognition_result(result)
            self.assertIs(result, window._latest_recognition)
            self.assertEqual(1, window._recognition_count)
            self.assertEqual(0, window._accepted_count)
        finally:
            window._timer.stop()
            window.close()

    def test_control_and_test_use_the_same_recognition_prompt_and_target_frame(self) -> None:
        result = IntentResult(
            timestamp=1.0,
            label="前进",
            confidence=0.72,
            margin=0.18,
            accepted=True,
            reason="",
            frequency_hz=9.0,
            scores={"前进": 0.72},
            sample_rate_hz=250.0,
            sample_count=1000,
            used_channels_1based=(2, 3),
        )
        windows = [
            SSVEPStimulusWindow(mode="control"),
            SSVEPStimulusWindow(mode="test"),
        ]
        try:
            styles = []
            for window in windows:
                window.set_idle_calibration_seconds(0.0)
                window.begin_trial()
                window._fresh_window_until = 0.0
                window.show_recognition_result(result)
                styles.append(window._target_frame_style("前进"))
                self.assertIs(result, window._latest_recognition)
                self.assertEqual(1, window._recognition_count)

            self.assertEqual(styles[0], styles[1])
            self.assertEqual("#39f39b", styles[0][0].name())
            self.assertEqual(11, styles[0][1])
            self.assertEqual(Qt.SolidLine, styles[0][2])
        finally:
            for window in windows:
                window._running = False
                window._timer.stop()
                window.close()

    def test_command_test_cd_finishes_with_same_fresh_window_as_control(self) -> None:
        window = SSVEPStimulusWindow(mode="test")
        requested = []
        window.fresh_window_requested.connect(lambda *args: requested.append(args))
        try:
            window.begin_trial()
            window.begin_cooldown(2.5, "起飞")
            window._finish_cooldown()

            self.assertEqual(1, len(requested))
            self.assertAlmostEqual(4.0, requested[0][0])
            self.assertEqual("起飞", requested[0][1])
            self.assertGreater(window.fresh_window_remaining, 1.5)
            self.assertTrue(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window._cooldown_timer.stop()
            window.close()

    def test_b_key_stops_command_test_without_requesting_emergency_stop(self) -> None:
        window = SSVEPStimulusWindow(mode="test")
        emergencies = []
        window.emergency_requested.connect(lambda: emergencies.append(True))
        try:
            window.show()
            window.begin_trial()
            self.app.processEvents()

            QTest.keyClick(window, Qt.Key_B)

            self.assertFalse(window.running)
            self.assertEqual([], emergencies)
        finally:
            window._timer.stop()
            window.close()

    def test_k_revokes_control_even_before_j_starts_stimulus(self) -> None:
        window = SSVEPStimulusWindow(mode="control")
        stopped = []
        window.control_stopped.connect(lambda: stopped.append(True))
        try:
            window.show()
            self.app.processEvents()

            QTest.keyClick(window, Qt.Key_K)

            self.assertFalse(window.running)
            self.assertEqual([True], stopped)
        finally:
            window._timer.stop()
            window.close()

    def test_targets_are_spatially_separated_and_directionally_arranged(self) -> None:
        rectangles = target_rectangles(1920, 1080)

        takeoff, forward, backward, left, right, land = rectangles
        self.assertLess(takeoff.center().x(), forward.center().x())
        self.assertLess(forward.center().x(), land.center().x())
        self.assertLess(left.center().x(), backward.center().x())
        self.assertLess(backward.center().x(), right.center().x())
        self.assertLess(forward.center().y(), backward.center().y())
        self.assertGreater(forward.left() - takeoff.right(), 0.10 * 1920)
        self.assertGreater(backward.top() - forward.bottom(), 0.15 * 1080)
        self.assertLess(takeoff.left(), 0.03 * 1920)
        self.assertGreater(land.right(), 0.97 * 1920)
        self.assertLess(takeoff.top(), 0.10 * 1080)
        self.assertGreater(right.bottom(), 0.90 * 1080)
        self.assertAlmostEqual(0.24 * 1920, takeoff.width())
        self.assertAlmostEqual(0.28 * 1080, takeoff.height())

    def test_control_cooldown_pauses_all_stimuli(self) -> None:
        window = SSVEPStimulusWindow(mode="control")
        try:
            window.begin_trial()
            window.begin_cooldown(2.5, "前进")

            self.assertFalse(window.stimuli_active)
            self.assertGreater(window.cooldown_remaining, 2.0)
        finally:
            window._running = False
            window._timer.stop()
            window._cooldown_timer.stop()
            window.close()

    def test_cooldown_end_requests_a_fresh_full_decoding_window(self) -> None:
        window = SSVEPStimulusWindow(mode="control")
        requested = []
        window.fresh_window_requested.connect(lambda *args: requested.append(args))
        try:
            window.begin_trial()
            window._finish_cooldown()

            self.assertEqual(1, len(requested))
            self.assertAlmostEqual(4.0, requested[0][0])
            self.assertEqual("空闲校准", requested[0][1])
            self.assertGreater(window.fresh_window_remaining, 1.5)
            self.assertTrue(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window._cooldown_timer.stop()
            window.close()

if __name__ == "__main__":
    unittest.main()
