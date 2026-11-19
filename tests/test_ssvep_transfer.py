import unittest
import numpy as np
import torch

from scripts.transfer_subject24_tff import fit_head, head_predict, choose, embedding
from src.ml.recent_ssvep_models import SSVEPTFFNet


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_head_training_only_statistics_and_probability_reconstruction(self):
        rng = np.random.default_rng(22)
        x = rng.normal(size=(90, 198))
        y = np.tile(np.arange(6), 15)
        h = fit_head(x, y, 16, .01)
        np.testing.assert_allclose(h["mean"], x.mean(0))
        self.assertEqual(h["weights"].shape, (6, 16))
        p = head_predict(x[:4], h)
        np.testing.assert_allclose(p.sum(1), 1)
        self.assertTrue(np.isfinite(p).all())
        self.assertTrue((p > 0).all())
        huge_test = rng.normal(100000, 100, (10, 198))
        before = {k: v.copy() for k, v in h.items()}
        p = head_predict(huge_test, h)
        for k in h: np.testing.assert_array_equal(h[k], before[k])
        self.assertTrue(np.isfinite(p).all())

    def test_frozen_embedding_changes_neither_weights_nor_buffers(self):
        torch.manual_seed(17)
        model = SSVEPTFFNet(channels=6)
        original = {k: v.clone() for k, v in model.state_dict().items()}
        x = np.random.default_rng(5).normal(size=(2, 6, 1250)).astype(np.float32)
        z = embedding(model, x)
        self.assertEqual(z.shape, (2, 198))
        self.assertTrue(np.isfinite(z).all())
        self.assertFalse(any(p.requires_grad for p in model.parameters()))
        for k, value in model.state_dict().items():
            torch.testing.assert_close(value, original[k], rtol=0, atol=0)
        np.testing.assert_array_equal(z, embedding(model, x))

    def test_validation_choice_ignores_outer_test(self):
        a = dict(components=32, C=.1, validation_correct=10, validation_nll=1.8, test_correct=999)
        b = dict(components=8, C=.001, validation_correct=11, validation_nll=1.9, test_correct=0)
        self.assertIs(choose([a, b]), b)


if __name__ == "__main__": unittest.main()
