"""Regression checks for calibration provenance in the offline BN audit."""
import unittest

import numpy as np

from scripts.audit_subject1_bn_fbcca import (
    RUNS, center_against_other_classes, chronological_idle_evaluation,
    evaluate, fit_idle_background,
)
from src.bci.config import COMMANDS


class BNAuditTest(unittest.TestCase):
    def test_document_a_cannot_change_ranking(self):
        values = np.random.default_rng(1).normal(size=(1000, 6))
        actual = center_against_other_classes(values)
        np.testing.assert_array_equal(values.argsort(axis=1), actual.argsort(axis=1))

    def test_calibration_excludes_commands_invalid_and_test_idle(self):
        scores = np.asarray([[1.] * 6, [20.] * 6, [300.] * 6, [4000.] * 6])
        rows = [{"label": "空闲"}, {"label": COMMANDS[0]},
                {"label": "空闲"}, {"label": "空闲"}]
        background, sources = fit_idle_background(scores, rows, [True, True, False, True], [0, 1, 2])
        np.testing.assert_array_equal(background, np.ones(6))
        self.assertEqual(sources, [0])

    def test_future_idle_and_previous_run_do_not_change_past_prediction(self):
        rows = [{"run": RUNS[0], "label": label}
                for label in [COMMANDS[0], "空闲", COMMANDS[0], "空闲"]]
        rows.append({"run": RUNS[1], "label": COMMANDS[0]})
        scores = np.arange(30, dtype=float).reshape(5, 6)
        valid = np.ones(5, dtype=bool)
        before, sources = chronological_idle_evaluation(scores, rows, valid)
        changed = scores.copy()
        changed[3] += 10000
        after, _ = chronological_idle_evaluation(changed, rows, valid)
        np.testing.assert_array_equal(before[:3], after[:3])
        np.testing.assert_array_equal(before[4], scores[4])
        self.assertEqual(sources[1]["idle_indices"], [])
        self.assertEqual(sources[2]["idle_indices"], [1])
        self.assertEqual(sources[4]["idle_indices"], [])

    def test_grouped_and_historical_calibration_are_disjoint(self):
        rows = []
        for run in RUNS:
            for block in range(5):
                for label in ("空闲",) + COMMANDS:
                    rows.append({"run": run, "label": label, "block": f"{run}-{block}"})
        rng = np.random.default_rng(2)
        matrix = rng.random((len(rows), 6))
        _, _, provenance = evaluate({3: matrix, 4: matrix}, rows, np.ones(len(rows), dtype=bool))
        for folds in provenance.values():
            for fold in folds:
                train_blocks = {rows[i]["block"] for i in fold["idle_indices"]}
                test_blocks = {rows[i]["block"] for i in fold["test_indices"]}
                self.assertFalse(train_blocks & test_blocks)
            self.assertTrue(all(rows[i]["run"] in RUNS[:2] for i in folds[-1]["idle_indices"]))


if __name__ == "__main__":
    unittest.main()
