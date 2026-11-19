"""Software fixtures for label isolation, the prospective CLI data flow and human weighting."""

from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest
from unittest.mock import patch

import numpy as np

from research.serial_bci.mi_transfer_followup import run as pipeline


def recording():
    rng = np.random.default_rng(770)
    records = []
    for block in range(1, 8):
        for j in range(12):
            i = len(records)
            key = f"fixture-pair-{i:04}"
            records.append(
                dict(
                    subject="software_fixture",
                    run="hybrid-20000101-120000-fixture",
                    pair=key,
                    block=block,
                    hand=j % 2,
                    true_hz=8 + j // 2,
                    mi=rng.normal(size=(3, 750)),
                    scores=rng.random(6),
                    start=float(i * 12),
                    end=float(i * 12 + 10),
                    gap=1.0,
                    good=True,
                    montage="unit-test",
                    ss_sha256="ss_" + key,
                    mi_sha256="mi_" + key,
                )
            )
    return records


class ForwardEvaluationTests(unittest.TestCase):
    def test_held_truth_cannot_change_deployable_predictions(self):
        rs = recording()
        scores = {r["pair"]: r["scores"] for r in rs}
        train, test = rs[:48], rs[48:]
        changed = [
            dict(r, hand=1 - r["hand"], true_hz=8 + (r["true_hz"] - 7) % 6)
            for r in test
        ]
        a, _ = pipeline.evaluate(train, test, scores, "within_1p5", "fixture")
        b, _ = pipeline.evaluate(train, changed, scores, "within_1p5", "fixture")
        for name in (
            "p",
            "ss_hz",
            "mu",
            "beta",
            "broadband",
            "mi_only",
            "mi_plus_p",
            "no_p",
            "with_p",
            "shift_refit",
        ):
            np.testing.assert_array_equal([r[name] for r in a], [r[name] for r in b])

    def test_prospective_run_outputs_all_enrolled_people(self):
        rs = recording()
        scores = {r["pair"]: r["scores"] for r in rs}
        acquisition = dict(
            subject="software_fixture",
            run=rs[0]["run"],
            valid_pairs=84,
            seeds=[770],
            planned_settings=[dict(planned=84, idle=8, preset_seed=770)],
            plan_mismatch_pairs=[],
            seed_missing_pairs=[],
            signal_sources=["unit-test fixture"],
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / rs[0]["run"]
            folder.mkdir()
            (folder / "hybrid-fixture-ssvep.npz").touch()
            with patch.object(
                pipeline, "load", return_value=(rs, {}, [])
            ), patch.object(
                pipeline, "audit_pairs", return_value=acquisition
            ), patch.object(
                pipeline,
                "long_score",
                side_effect=lambda p: scores[p.name.removesuffix("-ssvep.npz")],
            ):
                report = pipeline.run(
                    root,
                    root / "out",
                    strict_new=True,
                    roster={"software_fixture", "did_not_record"},
                )
            with (root / "out/cohort_flow.csv").open() as f:
                flow = {r["subject"]: r for r in csv.DictReader(f)}
            self.assertEqual(flow["did_not_record"]["primary_held_pairs"], "0")
            self.assertEqual(flow["software_fixture"]["primary_held_pairs"], "36")
            self.assertEqual(report["experiments"]["within_1p5"]["pooled"]["n"], 36)
            self.assertEqual(report["primary_experiment"], "within_4s")
            self.assertFalse(report["acquisition_issues"])
            self.assertTrue((root / "out/结果.md").is_file())
