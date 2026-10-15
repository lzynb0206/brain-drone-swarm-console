from __future__ import annotations

import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.bci.preprocessing import assess_signal_quality
from src.bci.stimulus import measure_frequency
from src.domain import IntentResult
from src.views.ssvep_rendering import target_rectangles
from src.views.ssvep_stimulus_window import SSVEPStimulusWindow


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
            self.assertFalse(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window.close()

    def test_command_test_starts_without_authorizing_online_control(self) -> None:
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
            self.assertFalse(window.calibrating_idle)
            self.assertTrue(window.stimuli_active)
        finally:
            window._running = False
            window._timer.stop()
            window.close()

    def test_command_test_displays_decoder_evidence(self) -> None:
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
            window.show_recognition_result(result)

            self.assertIs(result, window._latest_recognition)
            self.assertEqual(1, window._recognition_count)
            self.assertEqual(1, window._accepted_count)
        finally:
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
            self.assertAlmostEqual(2.0, requested[0][0])
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
