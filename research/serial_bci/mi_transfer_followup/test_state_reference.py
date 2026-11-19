import unittest

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal

from research.serial_bci.mi_transfer_followup.state_reference import designs, lateralization, shifts
from src.bci.montage import HYBRID


class ReferenceTests(unittest.TestCase):
    def test_outside_window_cannot_enter_filter_and_swapping_hemispheres_changes_sign(self):
        fs = 250
        times = np.arange(1250)/fs
        eeg = np.random.default_rng(26).standard_normal((8, len(times)))
        stage = dict(fs=fs, timestamps=times,
                     context=dict(channel_names=list(HYBRID.names), montage_id=HYBRID.id, stimulus_lsl_time=0.),
                     eeg=eeg)
        before = lateralization(stage)
        altered = eeg.copy()
        at = np.searchsorted(times, .25)
        altered[:, :at], altered[:, at+625:] = 1e8, -1e8
        assert_allclose(lateralization(dict(stage, eeg=altered)), before, atol=0, rtol=0)
        altered[[5,7]] = altered[[7,5]]
        assert_allclose(lateralization(dict(stage, eeg=altered)), -before, atol=1e-12)

    def test_constant_p_is_same_capacity_reference_and_shifts_stay_within_block(self):
        rng = np.random.default_rng(1)
        front, motor = rng.normal(size=(2, 12, 2))
        p = np.full(len(front), .42)
        design = designs(motor, front, p, .42)
        assert_allclose(design["mean_p"], design["paired_p"], rtol=0, atol=0)
        block = np.repeat([1,2,3], [5,3,4])
        for _ in range(10):
            order = shifts(block, rng)
            assert_array_equal(block[order], block)
            self.assertFalse(np.any(order == np.arange(len(order))))


if __name__ == "__main__":
    unittest.main()
