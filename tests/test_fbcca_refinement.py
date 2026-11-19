import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.refine_subject1_bn_fbcca import select_on_old, select_robustness_on_old, select_bank_harmonics_on_old
from scripts.audit_subject1_bn_fbcca import RUNS
from src.bci.config import COMMANDS
from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.fbcca_components import fbcca_components, combine_components, causal_idle_background
from src.bci.preprocessing import prepare_ssvep_channels, FILTER_BANK_WEIGHTS


class FBCCARefinementTests(unittest.TestCase):
    def test_components_reproduce_real_decoder_for_harmonics_and_weights(self):
        rng = np.random.default_rng(8)
        t = np.arange(1250) / 250.
        signal = rng.normal(size=(8, 1250)) + 3 * np.sin(2 * np.pi * 11 * t)
        prepared, _, _ = prepare_ssvep_channels(signal, range(1, 9), 250.)
        components = fbcca_components(prepared, 250.)
        for h in (1, 2, 3, 4):
            for weights in (FILTER_BANK_WEIGHTS, (1., 1., .5), (0., 1., 1.)):
                output = FBCCADecoder(harmonics=h, bank_weights=weights).decode(signal, 250.)
                expected = [output.scores[label] for label in COMMANDS]
                actual = combine_components(components, {"harmonics": h, "bank_weights": weights})
                np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=1e-12)
        config = {"harmonics": 4, "harmonics_by_bank": (2, 3, 4), "bank_weights": (1., 1., .5)}
        actual = combine_components(components, config)
        expected = np.zeros(6)
        for bank, h in enumerate(config["harmonics_by_bank"]):
            weights = np.zeros(3)
            weights[bank] = config["bank_weights"][bank]
            output = FBCCADecoder(harmonics=h, bank_weights=weights).decode(signal, 250.)
            expected += np.array([output.scores[label] for label in COMMANDS])
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=1e-12)

    def test_background_is_causal_session_scoped_and_ignores_invalid_idle(self):
        values = np.arange(18, dtype=float).reshape(6, 3)
        runs = ["a", "a", "b", "a", "b", "a"]
        idle = [True, True, True, False, False, False]
        valid = [True, False, True, True, True, True]
        for reducer in ("mean", "median", "ewma"):
            background, counts = causal_idle_background(values, runs, idle, valid, reducer=reducer)
            np.testing.assert_array_equal(counts, [0, 1, 0, 1, 1, 1])
            np.testing.assert_array_equal(background[0], np.zeros(3))
            np.testing.assert_array_equal(background[2], np.zeros(3))
            np.testing.assert_array_equal(background[3], values[0])
            np.testing.assert_array_equal(background[4], values[2])
            future = values.copy()
            future[2:] += 1000
            changed, _ = causal_idle_background(future, runs, idle, valid, reducer=reducer)
            np.testing.assert_array_equal(changed[:3], background[:3])

    def test_new_batch_cannot_change_selected_parameters(self):
        rng = np.random.default_rng(19)
        raw = rng.uniform(size=(18, 4, 3, 6))
        backgrounds = {k: np.zeros_like(raw) for k in ("mean", "median", "ewma")}
        records = [{"run": RUNS[i // 6], "label": COMMANDS[i % 6]} for i in range(18)]
        valid = np.ones(18, dtype=bool)
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            first, _ = select_on_old(raw, backgrounds, records, valid, Path(d))
            robust_first = select_robustness_on_old(raw, backgrounds, records, valid, first, Path(d))
            bank_first = select_bank_harmonics_on_old(raw, backgrounds, records, valid, robust_first, Path(d))
            raw[12:] = rng.uniform(0, 100, size=raw[12:].shape)
            for i in range(12, 18):
                records[i]["label"] = COMMANDS[(i+2) % 6]
            second, _ = select_on_old(raw, backgrounds, records, valid, Path(d))
            robust_second = select_robustness_on_old(raw, backgrounds, records, valid, second, Path(d))
            bank_second = select_bank_harmonics_on_old(raw, backgrounds, records, valid, robust_second, Path(d))
        self.assertEqual(first, second)
        self.assertEqual(robust_first, robust_second)
        self.assertEqual(bank_first, bank_second)

    def test_invalid_bank_weights_rejected(self):
        for weights in ((0, 0, 0), (-1, 1, 1), (1, 2), (1, np.nan, 1)):
            with self.assertRaises(ValueError):
                FBCCADecoder(bank_weights=weights)


if __name__ == "__main__":
    unittest.main()
