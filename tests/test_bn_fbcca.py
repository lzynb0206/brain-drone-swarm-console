import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.bci.bn_fbcca import BNFBCCADecoder, normalize_scores, gate_scores
from src.bci.config import COMMANDS, TARGET_FREQUENCIES_HZ, FBCCA_MIN_SCORE, FBCCA_MIN_MARGIN
from src.bci.runtime_decoder import RuntimeIntentDecoder
from scripts.evaluate_bn_fbcca import replay, calibrate
from scripts.audit_subject1_bn_fbcca import RUNS


class BNFBCCATest(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(31)

    def test_a_preserves_order_and_original_formula(self):
        for scores in self.rng.random((100, 6)):
            result = normalize_scores(scores, "a")
            self.assertEqual(result.prediction, scores.argmax())
            expected = [scores[i] - np.delete(scores, i).mean() for i in range(6)]
            np.testing.assert_allclose(result.normalized, expected)

    def test_b_changes_biased_prediction(self):
        raw = [.8, .7, .1, .1, .1, .1]
        result = normalize_scores(raw, "b", [.7, .1, .05, .05, .05, .05])
        self.assertEqual(result.prediction, 1)
        self.assertTrue(gate_scores(result, raw, .7))

    def test_confidence_handles_negative_zero_and_unbounded_ratio(self):
        result = normalize_scores([.2, .1], "b", [.1, .2])
        self.assertAlmostEqual(result.formula_confidence, 2.)
        self.assertEqual(result.confidence, 1.)
        result = normalize_scores([.1, .2], "b", [.2, .4])
        self.assertLess(result.formula_confidence, 0.)
        self.assertEqual(result.confidence, 0.)
        self.assertFalse(gate_scores(result, [.1, .2], 0.))
        zero = normalize_scores([1., 1.], "a")
        self.assertIsNone(zero.formula_confidence)
        self.assertEqual(zero.confidence, 0.)

    def test_live_idle_updates_are_explicit_and_window_specific(self):
        decoder = BNFBCCADecoder("b")
        manager = RuntimeIntentDecoder({"BN": decoder}, "BN")
        for _ in range(8):
            idle = manager.decode(self.rng.normal(0, 5, (8, 1000)), 250.)
            self.assertFalse(idle.accepted)
            manager.observe_idle(idle)
        self.assertEqual(len(decoder._idle), 8)
        t = np.arange(1000) / 250.
        eeg = self.rng.normal(0, 1, (8, 1000)) + 5 * np.sin(2 * np.pi * 10 * t)
        result = manager.decode(eeg, 250.)
        self.assertEqual(result.label, COMMANDS[2])
        self.assertTrue(result.accepted)
        self.assertEqual(len(decoder._idle), 8)
        self.assertEqual(result.scores.keys(), set(COMMANDS))
        self.assertFalse(result.signal_quality["confidence_is_probability"])
        short = manager.decode(eeg[:, -500:], 250.)
        self.assertFalse(short.accepted)
        self.assertEqual(len(decoder._idle), 0)
        self.assertFalse(manager.hardware_control_allowed)

    def test_gate_artifact_contract(self):
        payload = dict(schema_version=1, mode="a", threshold=.5,
                       frequencies_hz=list(TARGET_FREQUENCIES_HZ), sample_rate_hz=250., sample_count=1000,
                       confidence_definition="positive_top_clipped_relative_margin_v1",
                       background_policy="none", min_idle_windows=8,
                       min_raw_score=FBCCA_MIN_SCORE, min_margin=FBCCA_MIN_MARGIN)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            path.write_text(json.dumps(payload))
            decoder = BNFBCCADecoder("a", calibration_path=path)
            result = decoder.decode(self.rng.normal(0, 5, (8, 500)), 250.)
            self.assertFalse(result.accepted)
            self.assertIn("窗口不匹配", result.reason)
            with self.assertRaises(ValueError):
                BNFBCCADecoder("b", calibration_path=path)

    def test_replay_never_uses_current_or_future_idle(self):
        rows = [{"run": RUNS[0], "label": label} for label in ("空闲", COMMANDS[0], "空闲")]
        raw = self.rng.random((3, 6))
        before = replay(raw, rows, [True] * 3, "b", 1)
        changed = raw.copy()
        changed[2] *= 1000
        after = replay(changed, rows, [True] * 3, "b", 1)
        np.testing.assert_allclose(before[1]["result"].normalized, after[1]["result"].normalized)
        self.assertEqual(before[2]["idle_indices"], [0])

    def test_calibration_does_not_read_test_labels(self):
        rows = [{"run": RUNS[0], "label": "空闲"}] + [
            {"run": RUNS[0], "label": COMMANDS[0]} for _ in range(12)]
        rows += [{"run": RUNS[2], "label": COMMANDS[1]}]
        raw = np.tile([.8, .2, .1, .1, .1, .1], (len(rows), 1))
        raw[0] = .01
        valid = [True] * len(rows)
        dev = list(range(len(rows) - 1))
        first = calibrate(raw, replay(raw, rows, valid, "a"), rows, valid, dev)
        rows[-1]["label"] = COMMANDS[0]
        second = calibrate(raw, replay(raw, rows, valid, "a"), rows, valid, dev)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
