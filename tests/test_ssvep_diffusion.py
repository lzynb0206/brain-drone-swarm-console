import unittest

import numpy as np
import torch

from src.ml.ssvep_diffusion import FrequencyDDPM, SpectralFidelity, standardize_trials
from scripts.train_ssvep_diffusion import subject_split


class SSVEPDiffusionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_constraint_is_relative_to_background_not_absolute_power(self):
        torch.manual_seed(5)
        t = torch.arange(500) / 250.
        eeg = torch.randn(2, 6, 500) + 4 * torch.sin(2 * torch.pi * 10 * t)
        fidelity = SpectralFidelity()
        fit_frequencies = torch.fft.rfftfreq(500, 1 / 250.)[fidelity.fit_indices]
        self.assertTrue(torch.all(fit_frequencies >= 6.5))
        original = fidelity.excess_db(eeg)
        scaled = fidelity.excess_db(eeg * 100)
        torch.testing.assert_close(original, scaled, atol=1e-4, rtol=1e-5)
        self.assertTrue(torch.all(original[:, 2] > 3))
        self.assertTrue(torch.all(original.argmax(1) == 2))

    def test_spectral_loss_has_finite_gradients_for_missing_target(self):
        torch.manual_seed(6)
        eeg = torch.randn(2, 6, 500, requires_grad=True)
        fidelity = SpectralFidelity(margin_db=20)
        loss = fidelity(eeg, torch.tensor([0, 5])).mean()
        loss.backward()
        self.assertTrue(torch.isfinite(eeg.grad).all())
        self.assertGreater(eeg.grad.abs().sum().item(), 0.)

    def test_diffusion_train_sample_and_reload(self):
        torch.manual_seed(7)
        model = FrequencyDDPM(width=8, steps=4)
        labels = torch.tensor([0, 5])
        loss, _, _ = model.loss(torch.randn(2, 6, 500), labels)
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None))
        clone = FrequencyDDPM(**model.config)
        clone.load_state_dict(model.state_dict())
        first = model.sample(labels, torch.Generator().manual_seed(2))
        second = clone.sample(labels, torch.Generator().manual_seed(2))
        torch.testing.assert_close(first, second)
        self.assertEqual(first.shape, (2, 6, 500))
        self.assertTrue(torch.isfinite(first).all())

    def test_subject_split_is_disjoint_and_complete(self):
        subjects = np.arange(1, 36)
        masks = subject_split(subjects)
        np.testing.assert_array_equal(sum(masks), np.ones(35))
        self.assertEqual([int(m.sum()) for m in masks], [25, 5, 5])

    def test_preprocessing_cannot_use_other_trials(self):
        rng = np.random.default_rng(3)
        eeg = rng.normal(size=(2, 6, 500))
        first = standardize_trials(eeg)
        eeg[1] *= 1000
        second = standardize_trials(eeg)
        np.testing.assert_allclose(first[0], second[0])
        with self.assertRaises(ValueError):
            standardize_trials(np.zeros((1, 6, 500)))


if __name__ == "__main__":
    unittest.main()
