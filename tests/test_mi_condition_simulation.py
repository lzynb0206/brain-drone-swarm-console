import unittest
import numpy as np
from src.ml.mi_condition_simulation import SOURCE, confidence_pools, assign_confidence, simulation_condition
from src.ml.mi_diffusion import condition_values
from scripts.research_paired_mi import audit_data


class SimulationTest(unittest.TestCase):
    def test_pools_have_separate_sources_and_do_not_use_labels(self):
        rows = [dict(file=str(i), run=f"run{i//4}", p=i/12, p_eligible=i%3 != 0,
                     true_frequency=8+i%6) for i in range(12)]
        pools = confidence_pools(rows)
        self.assertFalse({r["file"] for r in pools["T"]} & {r["file"] for r in pools["E"]})
        p, source = assign_confidence(pools["T"], 144, 42)
        for r in rows:
            r["true_frequency"] = -99
        q, source2 = assign_confidence(confidence_pools(rows)["T"], 144, 42)
        np.testing.assert_array_equal(p, q)
        self.assertEqual(source, source2)
        self.assertEqual(144, len(p))

    def test_constant_comes_from_training_and_shuffle_stays_in_run(self):
        p = np.arange(20, dtype=np.float32)/20
        groups = np.repeat([0, 1], 10)
        q, m = simulation_condition("constant", p, groups, constant=.123, seed=1)
        np.testing.assert_allclose(q, .123)
        np.testing.assert_array_equal(m, np.ones(20))
        q, _ = simulation_condition("shuffled", p, groups, constant=.5, seed=1)
        self.assertFalse(np.array_equal(p, q))
        for g in [0, 1]:
            np.testing.assert_array_equal(np.sort(q[groups == g]), p[groups == g])
        z, m = simulation_condition("none", p, groups, constant=.5, seed=1)
        self.assertEqual(0., float(z.sum()+m.sum()))

    def test_simulated_values_cannot_pass_measured_pair_guards(self):
        with self.assertRaises(ValueError):
            audit_data(dict(condition_source=np.array(SOURCE)))
        with self.assertRaises(ValueError):
            condition_values("true", SOURCE, [.3, .7], [True, True], [0, 0])
        with self.assertRaises(ValueError):
            simulation_condition("true", [.3, .7], [0, 0], constant=.5, seed=1)


if __name__ == "__main__":
    unittest.main()
