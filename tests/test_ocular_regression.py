from __future__ import annotations

import unittest

import numpy as np

from src.bci.ocular_regression import apply_ocular_regression, fit_ocular_regression


class OcularRegressionTest(unittest.TestCase):
    def test_idle_fitted_regression_reduces_blink_and_preserves_ssvep(self):
        fs = 250.0
        timeline = np.arange(500) / fs
        blink = np.sin(2 * np.pi * 2.0 * timeline)
        horizontal = np.sin(2 * np.pi * 3.0 * timeline + 0.3)
        idle = []
        for scale in (0.8, 1.0, 1.2, 1.4):
            window = np.zeros((8, 500), dtype=float)
            window[0] = scale * (blink + 0.4 * horizontal)
            window[7] = scale * (blink - 0.4 * horizontal)
            for target in range(1, 7):
                window[target] = (0.2 + 0.05 * target) * blink
            idle.append(window)
        model = fit_ocular_regression(np.asarray(idle), fs)

        active = idle[1].copy()
        ssvep = 0.5 * np.sin(2 * np.pi * 10.0 * timeline)
        active[1:7] += ssvep
        cleaned = apply_ocular_regression(active, model)

        before = abs(float(np.corrcoef(active[3], blink)[0, 1]))
        after = abs(float(np.corrcoef(cleaned[3], blink)[0, 1]))
        self.assertLess(after, before * 0.25)
        reference_amplitude = abs(np.vdot(active[3], np.exp(-2j * np.pi * 10.0 * timeline)))
        cleaned_amplitude = abs(np.vdot(cleaned[3], np.exp(-2j * np.pi * 10.0 * timeline)))
        self.assertGreater(cleaned_amplitude, reference_amplitude * 0.95)


if __name__ == "__main__":
    unittest.main()
