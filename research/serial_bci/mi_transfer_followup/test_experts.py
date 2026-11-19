"""Regression checks for grouped fitting and switching of the hand decision."""
import unittest

import numpy as np
from numpy.testing import assert_allclose

from research.serial_bci.mi_transfer_followup.experts import expert_evidence, fit_gate


class ExpertTests(unittest.TestCase):
    def test_an_oof_blocks_own_labels_have_no_effect_on_its_predictions(self):
        rng = np.random.default_rng(407)
        train = rng.standard_normal((24, 3, 250))
        test = rng.standard_normal((5, 3, 250))
        blocks = np.repeat([1, 2, 3], 8)
        labels = np.tile([0, 1], 12)
        original, held = expert_evidence(train, labels, blocks, test)
        changed = np.where(blocks == 2, 1 - labels, labels)
        modified, _ = expert_evidence(train, changed, blocks, test)
        assert_allclose(original[blocks == 2], modified[blocks == 2], rtol=0, atol=0)
        self.assertGreater(np.max(abs(original[blocks == 1] - modified[blocks == 1])), 1e-4)
        single_oof, single = expert_evidence(train, labels, blocks, test[:1])
        assert_allclose(single_oof, original)
        assert_allclose(single[0], held[0], atol=1e-11)

    def test_cannot_fit_an_inner_fold_with_one_hand(self):
        x = np.random.default_rng(2).standard_normal((8, 3, 250))
        with self.assertRaisesRegex(ValueError, "two trials per hand"):
            expert_evidence(x, [0]*4 + [1]*4, [1]*4 + [2]*4, x[:2])

    def test_confidence_can_operationally_switch_hand_identity(self):
        # A constructed positive control in which P determines which band is reliable.
        # The pure MI probabilities and their relative certainty are uninformative here.
        train = np.tile([[0.9, 0.1]], (40, 1))
        p = np.tile([0.05, 0.95], 20)
        labels = (p > 0.5).astype(int)
        held = np.array([[0.85, 0.15], [0.85, 0.15]])
        pred = fit_gate(train, labels, p, held, [0.05, 0.95])
        for arm in ("with_p", "mi_plus_p"):
            np.testing.assert_array_equal(pred[arm] > 0.5, [False, True])
        for arm in ("no_p", "mi_only"):
            assert_allclose(pred[arm][0], pred[arm][1])
        swapped = fit_gate(train, labels, p, held, [0.95, 0.05])
        assert_allclose(swapped["with_p"], pred["with_p"][::-1])
        self.assertEqual(swapped["info"]["arms"]["with_p"]["coefficients"],
                         pred["info"]["arms"]["with_p"]["coefficients"])

    def test_constant_confidence_recovers_each_matched_baseline(self):
        rng = np.random.default_rng(271)
        train, test = rng.random((34, 2)), rng.random((9, 2))
        y = rng.integers(0, 2, len(train))
        pred = fit_gate(train, y, np.full(len(train), 0.4), test, rng.random(len(test)))
        assert_allclose(pred["with_p"], pred["no_p"], atol=1e-12)
        assert_allclose(pred["mi_plus_p"], pred["mi_only"], atol=1e-12)

    def test_no_extrapolation_or_flips_where_experts_agree(self):
        rng = np.random.default_rng(2701)
        train = rng.random((200, 2))
        y = (train[:, 0] > 0.5).astype(int)
        held = np.array([[0.81, 0.98], [0.29, 0.43], [0.15, 0.86]])
        pred = fit_gate(train, y, rng.random(len(train)), held, [0., 1., 0.5])
        for arm in ("no_p", "mi_only", "with_p", "mi_plus_p"):
            self.assertTrue(np.all(pred[arm] >= held.min(axis=1) - 1e-12))
            self.assertTrue(np.all(pred[arm] <= held.max(axis=1) + 1e-12))
            np.testing.assert_array_equal(pred[arm][:2] > 0.5, [True, False])
            self.assertLessEqual(np.linalg.norm(pred["info"]["arms"][arm]["coefficients"], 1),
                                 0.50000001)


if __name__ == "__main__":
    unittest.main()
