from __future__ import annotations

import unittest

import numpy as np
import torch

from scripts.benchmark_recent_ssvep_methods import _complex_spectrum
from src.ml.fb_eegnet import FBEEGNetFusion
from src.ml.recent_ssvep_models import (
    BrainWaveDecoderAdaptation,
    SSVEPTFFNet,
)


class RecentSSVEPModelsTest(unittest.TestCase):
    def test_tffnet_complex_spectrum_contract(self):
        timeline = np.arange(1250, dtype=float) / 250.0
        values = np.stack([
            np.sin(2.0 * np.pi * 10.0 * timeline + channel * 0.1)
            for channel in range(8)
        ])
        spectrum = _complex_spectrum(values)
        self.assertEqual((8, 570), spectrum.shape)
        self.assertTrue(np.isfinite(spectrum).all())

    def test_all_neural_models_return_six_logits(self):
        temporal = torch.randn(2, 8, 1250)
        spectrum = torch.randn(2, 8, 570)
        banks = torch.randn(2, 4, 8, 1250)
        with torch.no_grad():
            tff_logits = SSVEPTFFNet()(temporal, spectrum)
            brainwave_logits = BrainWaveDecoderAdaptation()(temporal)
            eegnet_logits = FBEEGNetFusion(
                channels=8, bands=4, classes=6
            )(temporal, banks)
        self.assertEqual((2, 6), tuple(tff_logits.shape))
        self.assertEqual((2, 6), tuple(brainwave_logits.shape))
        self.assertEqual((2, 6), tuple(eegnet_logits.shape))
        self.assertTrue(torch.isfinite(tff_logits).all())
        self.assertTrue(torch.isfinite(brainwave_logits).all())
        self.assertTrue(torch.isfinite(eegnet_logits).all())


if __name__ == "__main__":
    unittest.main()
