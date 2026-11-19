import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from scripts import refine_subject1_cca_stability as stability
from scripts import refine_subject1_harmonic_evidence as harmonic
from scripts import refine_subject1_channel_scope as channels
from scripts.audit_subject1_bn_fbcca import RUNS
from src.bci.config import COMMANDS
from src.bci.fbcca_components import fbcca_components, _reference_bases
from src.bci.regularized_fbcca import regularized_components
from src.bci.harmonic_fbcca import individual_harmonic_components, combine_harmonic_evidence, harmonic_mask
from src.bci.preprocessing import filter_bank


class FBCCAInternalAblationsTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(23)
        self.data = self.rng.normal(size=(5, 1250))

    def test_ridge_zero_reproduces_original_and_shrinkage_is_monotonic(self):
        expected = fbcca_components(self.data, 250.)[3]
        values, _ = regularized_components(self.data, 250., (0., .0001, .01, 1.))
        np.testing.assert_allclose(values[0], expected, atol=1e-12, rtol=1e-12)
        self.assertTrue(np.all(np.diff(values, axis=0) <= 1e-12))

    def test_ridge_matches_direct_covariance_eigenvalue_solution(self):
        ridge = .1
        actual, _ = regularized_components(self.data, 250., (ridge,))
        references = _reference_bases(250., 1250)[3]
        for b, (x, _) in enumerate(filter_bank(self.data, 250.)):
            x = x - x.mean(axis=1, keepdims=True)
            cxx = x@x.T
            penalized = cxx + ridge*np.trace(cxx)/len(x)*np.eye(len(x))
            for k, y in enumerate(references):
                cxy = x@y
                eigenvalues = np.linalg.eigvals(np.linalg.solve(penalized, cxy@cxy.T))
                self.assertAlmostEqual(actual[0,b,k], float(np.max(eigenvalues.real)), places=11)

    def test_harmonic_mask_and_zero_mixture_contract(self):
        joint = fbcca_components(self.data, 250.)[3]
        separate = individual_harmonic_components(self.data, 250.)
        self.assertTrue(np.all(separate[~harmonic_mask()] == 0))
        self.assertFalse(harmonic_mask()[0,5,3])  # 13*4 > 50 Hz.
        weights = (1., 1., .5)
        actual = combine_harmonic_evidence(joint, separate, mix=0., exponent=1., bank_weights=weights)
        np.testing.assert_allclose(actual, np.einsum('bk,b->k',joint,weights), atol=1e-12)

    def test_pure_sinusoid_has_correct_individual_harmonic(self):
        t = np.arange(1250)/250.
        x = np.array([np.sin(2*np.pi*13*t), np.cos(2*np.pi*13*t)])
        score = individual_harmonic_components(x, 250.)
        self.assertAlmostEqual(score[0,5,0], 1., places=10)
        self.assertLess(score[0,3,0], 1e-20)

    def test_new_batch_cannot_affect_any_of_three_selections(self):
        records = [{"run":RUNS[i//6],"label":COMMANDS[i%6]} for i in range(18)]
        v = self.rng.random((18,len(stability.OFFSETS),len(stability.RIDGES),3,6))
        b = np.zeros_like(v);ok = np.ones((18,len(stability.OFFSETS)),bool)
        j = self.rng.random((18,3,6));s = self.rng.random((18,3,6,4));valid = np.ones(18,bool)
        scoped = {k:self.rng.random((18,3,6)) for k in channels.SCOPES}
        scope_valid = {k:valid.copy() for k in channels.SCOPES}
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), patch.object(stability,'OUT',Path(d)), patch.object(harmonic,'OUT',Path(d)), patch.object(channels,'OUT',Path(d)):
            before = [stability.select(v,b,records,ok)[0],harmonic.select(j,s,records,valid)[0],channels.select(scoped,scope_valid,records)[0]]
            v[12:]*=10;j[12:]*=10;s[12:]*=10
            for x in scoped.values():x[12:]*=10
            for i in range(12,18):records[i]["label"]=COMMANDS[(i+1)%6]
            after = [stability.select(v,b,records,ok)[0],harmonic.select(j,s,records,valid)[0],channels.select(scoped,scope_valid,records)[0]]
        self.assertEqual(before, after)


if __name__=='__main__':
    unittest.main()
