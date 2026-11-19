import unittest

import numpy as np
import torch

from src.ml.ssvep_regularized import build_model, ValidationStopper, validation_choice
from scripts.regularize_subject24_neural import subset, metrics


class RegularizedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_shapes_gradients_parameter_reduction_and_originals_unchanged(self):
        expected = {"SSVEP-TFFNet": 886972, "CNN-BiGRU": 62471, "FB-EEGNet": 5191}
        torch.manual_seed(7)
        x = torch.randn(2, 8, 500)
        spectrum = torch.randn(2, 8, 570)
        banks = torch.randn(2, 4, 8, 500)
        for method in expected:
            old = build_model(method, "regularized_original")
            small = build_model(method, "compact_regularized")
            self.assertEqual(sum(p.numel() for p in old.parameters()), expected[method])
            self.assertLess(sum(p.numel() for p in small.parameters()), expected[method] / 2)
            args = (x, spectrum) if method == "SSVEP-TFFNet" else (x, banks) if method == "FB-EEGNet" else (x,)
            for model in (old, small):
                z = model(*args)
                self.assertEqual(tuple(z.shape), (2, 6))
                loss = torch.nn.functional.cross_entropy(z, torch.tensor([0, 1]), label_smoothing=.1)
                loss.backward()
                self.assertTrue(all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters()))
            small.eval()
            twin = build_model(method, "compact_regularized")
            twin.load_state_dict(small.state_dict())
            twin.eval()
            torch.testing.assert_close(small(*args), twin(*args), rtol=0, atol=0)

    def test_loss_based_stop_restores_early_epoch_not_accuracy_peak(self):
        s = ValidationStopper()
        self.assertEqual(s.update(5, 1.7), (True, False))
        self.assertEqual(s.update(10, 1.5), (True, False))
        self.assertEqual(s.update(15, 1.6), (False, False))
        self.assertEqual(s.update(20, 1.7), (False, False))
        self.assertEqual(s.update(25, 1.8), (False, True))
        self.assertEqual(s.best_epoch, 10)
        with self.assertRaises(ValueError):
            s.update(30, float("nan"))

    def test_selection_never_uses_test_accuracy(self):
        a = dict(variant="regularized_original", validation_nll=1.6, parameters=100,
                 selected_epoch=5, test_correct=999)
        b = dict(variant="compact_regularized", validation_nll=1.5, parameters=10,
                 selected_epoch=10, test_correct=0)
        self.assertIs(validation_choice([a, b]), b)
        b["validation_nll"] = 1.6
        self.assertIs(validation_choice([a, b]), b)

    def test_subset_physically_excludes_test_and_never_mutates_source(self):
        x = np.arange(4 * 8 * 100).reshape(4, 8, 100)
        data = dict(clean=x, eegnet=(x, x[:, None]), y=np.arange(4),
                    valid=np.ones(4, bool), records=[dict(source_file=str(i)) for i in range(4)])
        view = subset(data, [0, 2])
        np.testing.assert_array_equal(view["y"], [0, 2])
        self.assertEqual([r["source_file"] for r in view["records"]], ["0", "2"])
        view["clean"][:] = -1
        self.assertTrue((data["clean"] >= 0).all())

    def test_invalid_test_retained_as_failure(self):
        p = np.eye(6)[[0, 1]] * .9 + .1 / 6
        result = metrics(p, dict(y=np.array([0, 1]), valid=np.array([True, False])))
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["correct"], 1)

    def test_unknown_architecture_rejected(self):
        with self.assertRaises(ValueError):
            build_model("LR", "compact_regularized")
        with self.assertRaises(ValueError):
            build_model("SSVEP-TFFNet", "unknown")


if __name__ == "__main__":
    unittest.main()
