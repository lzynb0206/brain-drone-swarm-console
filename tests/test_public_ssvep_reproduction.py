from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.ml.public_ssvep_data import (
    TARGET_CHANNEL_NAMES,
    TARGET_FREQUENCIES_HZ,
    TSINGHUA_64_CHANNEL_NAMES,
    PreparedPublicData,
    benchmark_frequency_order,
    beta_frequency_order,
    discover_subject_files,
    load_public_subject,
    prepare_public_data,
    select_channel_indices,
    select_frequency_indices,
)
from src.ml.reproduce_public_ssvep import (
    _fbcca_predictions,
    aggregate,
    build_parser,
    loso_split,
    resolve_held_out_subject,
)


SCIPY_AVAILABLE = importlib.util.find_spec("scipy") is not None


def _synthetic_eeg(
    frequencies, blocks: int = 1, sample_count: int = 1000,
) -> np.ndarray:
    sample_rate = 250.0
    timeline = np.arange(sample_count, dtype=float) / sample_rate
    data = np.zeros((64, sample_count, 40, blocks), dtype=np.float32)
    selected_channels = select_channel_indices(TSINGHUA_64_CHANNEL_NAMES)
    for frequency in TARGET_FREQUENCIES_HZ:
        target = select_frequency_indices(frequencies, (frequency,))[0]
        for channel_offset, channel in enumerate(selected_channels):
            wave = 25.0 * np.sin(
                2.0 * np.pi * frequency * timeline + channel_offset * 0.07
            )
            wave += 5.0 * np.sin(4.0 * np.pi * frequency * timeline)
            data[channel, :, target, :] = wave[:, None]
    return data


@unittest.skipUnless(SCIPY_AVAILABLE, "scipy is required for MAT fixtures")
class PublicDatasetLoaderTest(unittest.TestCase):
    def test_benchmark_metadata_selection_and_preparation(self) -> None:
        from scipy import io

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Benchmark"
            root.mkdir()
            frequencies = benchmark_frequency_order()
            io.savemat(root / "Freq_Phase.mat", {"freqs": frequencies})
            io.savemat(
                root / "S1.mat",
                {"data": _synthetic_eeg(frequencies)},
                do_compression=True,
            )

            self.assertEqual(["S1.mat"], [path.name for path in discover_subject_files(root)])
            subject = load_public_subject(root / "S1.mat", "benchmark", root)
            self.assertEqual((64, 1000, 40, 1), subject.eeg.shape)
            self.assertTrue(subject.frequency_source.endswith("Freq_Phase.mat"))
            self.assertEqual(
                tuple(range(8)), select_frequency_indices(subject.frequencies_hz)
            )

            output = Path(directory) / "prepared"
            prepared = prepare_public_data((("benchmark", root),), output)
            self.assertEqual((8, 8, 500), prepared.values.shape)
            self.assertEqual(list(range(8)), prepared.targets.tolist())
            self.assertFalse(prepared.manifest["has_idle_class"])
            self.assertFalse(prepared.manifest["runtime_compatible"])
            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "trials_manifest.csv").is_file())
            prediction, _ = _fbcca_predictions(prepared.values)
            self.assertEqual(prepared.targets.tolist(), prediction.tolist())

    def test_official_frequency_fallback_orders_match_target_indices(self) -> None:
        benchmark = benchmark_frequency_order()
        beta = beta_frequency_order()

        self.assertEqual(
            [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
            benchmark[:8].tolist(),
        )
        self.assertEqual(tuple(range(8)), select_frequency_indices(benchmark))
        self.assertEqual(
            [8.6, 8.8, 9.0, 9.2],
            [round(float(value), 1) for value in beta[:4]],
        )
        self.assertEqual(
            (37, 2, 7, 12, 17, 22, 27, 32),
            select_frequency_indices(beta),
        )

    def test_beta_struct_metadata_and_axis_order(self) -> None:
        from scipy import io

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "BETA"
            root.mkdir()
            frequencies = beta_frequency_order()
            channel_info = np.empty((64, 4), dtype=object)
            channel_info[:, :3] = 0.0
            channel_info[:, 3] = np.asarray(TSINGHUA_64_CHANNEL_NAMES, dtype=object)
            # BETA stores channel x time x block x condition.
            eeg = np.transpose(
                _synthetic_eeg(frequencies, sample_count=750), (0, 1, 3, 2)
            )
            io.savemat(
                root / "S01.mat",
                {"data": {
                    "EEG": eeg,
                    "suppl_info": {
                        "freqs": frequencies,
                        "chan": channel_info,
                        "srate": 250.0,
                    },
                }},
                do_compression=True,
            )
            subject = load_public_subject(root / "S01.mat", "beta", root)
            self.assertEqual((64, 750, 40, 1), subject.eeg.shape)
            self.assertEqual("data.suppl_info", subject.frequency_source)
            self.assertEqual("data.suppl_info", subject.channel_source)
            self.assertEqual(TARGET_CHANNEL_NAMES, tuple(
                subject.channel_names[index]
                for index in select_channel_indices(subject.channel_names)
            ))
            matched = select_frequency_indices(subject.frequencies_hz)
            self.assertEqual(
                list(TARGET_FREQUENCIES_HZ),
                [round(float(subject.frequencies_hz[index]), 1) for index in matched],
            )
            with self.assertRaisesRegex(ValueError, "刺激段过短"):
                prepare_public_data((("beta", root),), Path(directory) / "short-beta")


class LOSOSplitTest(unittest.TestCase):
    def _data(self) -> PreparedPublicData:
        subjects = np.repeat(["benchmark:S01", "benchmark:S02", "beta:S01", "beta:S02"], 8)
        targets = np.tile(np.arange(8, dtype=np.int64), 4)
        rows = tuple({
            "dataset": subject.split(":")[0],
            "subject_id": subject,
            "block_index": 0,
        } for subject in subjects)
        return PreparedPublicData(
            values=np.zeros((32, 8, 500), dtype=np.float32),
            targets=targets,
            subjects=subjects,
            datasets=np.asarray([value.split(":")[0] for value in subjects]),
            blocks=np.zeros(32, dtype=np.int64),
            source_target_indices=targets.copy(),
            trial_rows=rows,
            manifest={},
        )

    def test_loso_never_leaks_test_or_validation_subjects(self) -> None:
        data = self._data()
        held_out, fold_index = resolve_held_out_subject(data, "beta:S01", None)
        self.assertEqual("beta:S01", held_out)
        self.assertEqual(2, fold_index)
        split = loso_split(data, held_out, validation_fraction=0.25, seed=7)
        train = set(split["training_subjects"])
        validation = set(split["validation_subjects"])
        self.assertNotIn(held_out, train | validation)
        self.assertFalse(train & validation)
        self.assertEqual({held_out}, set(data.subjects[split["test_indices"]]))

    def test_ambiguous_short_subject_name_is_rejected_for_combined_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须唯一匹配"):
            resolve_held_out_subject(self._data(), "S01", None)

    def test_cli_exposes_resumable_stages(self) -> None:
        parser = build_parser()
        prepare = parser.parse_args([
            "prepare", "--benchmark-root", "/tmp/benchmark", "--output-dir", "/tmp/out"
        ])
        self.assertEqual("prepare", prepare.command)
        fold = parser.parse_args([
            "run-fold", "--prepared-dir", "/tmp/out", "--fold-index", "3",
            "--methods", "fbcca",
        ])
        self.assertEqual(3, fold.fold_index)
        self.assertEqual(["fbcca"], fold.methods)

    def test_aggregate_tracks_missing_folds_per_method(self) -> None:
        data = self._data()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared"
            prepared.mkdir()
            cache = prepared / "public_ssvep_8class.npz"
            np.savez_compressed(
                cache,
                eeg=data.values,
                targets=data.targets,
                subjects=data.subjects,
                datasets=data.datasets,
                blocks=data.blocks,
                source_target_indices=data.source_target_indices,
            )
            (root / "manifest.json").write_text(
                json.dumps({"cache_file": str(cache)}), encoding="utf-8"
            )
            with (root / "trials_manifest.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=("dataset", "subject_id", "block_index")
                )
                writer.writeheader()
                writer.writerows(data.trial_rows)

            subjects = sorted(set(data.subjects.tolist()))
            for index, subject in enumerate(subjects):
                methods = {
                    "fbcca": {
                        "trial_count": 8,
                        "accuracy": 1.0,
                        "macro_f1": 1.0,
                        "confusion_matrix": np.eye(8, dtype=int).tolist(),
                    }
                }
                if index < len(subjects) - 1:
                    methods["transformer"] = dict(methods["fbcca"])
                fold = root / "folds" / subject.replace(":", "_")
                fold.mkdir(parents=True)
                (fold / "metrics.json").write_text(json.dumps({
                    "status": "complete",
                    "fold_index": index,
                    "held_out_subject": subject,
                    "methods": methods,
                }), encoding="utf-8")
            result = aggregate(argparse.Namespace(prepared_dir=root))
            self.assertTrue(result["methods"]["fbcca"]["complete"])
            self.assertFalse(result["methods"]["transformer"]["complete"])
            self.assertEqual([subjects[-1]], result["methods"]["transformer"]["missing_subjects"])
            self.assertFalse(result["complete"])

    def test_aggregate_requires_transformer_even_when_no_fold_ran_it(self) -> None:
        data = self._data()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared"
            prepared.mkdir()
            np.savez_compressed(
                prepared / "public_ssvep_8class.npz",
                eeg=data.values,
                targets=data.targets,
                subjects=data.subjects,
                datasets=data.datasets,
                blocks=data.blocks,
                source_target_indices=data.source_target_indices,
            )
            (root / "manifest.json").write_text(
                json.dumps({"cache_file": "prepared/public_ssvep_8class.npz"}),
                encoding="utf-8",
            )
            with (root / "trials_manifest.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=("dataset", "subject_id", "block_index")
                )
                writer.writeheader()
                writer.writerows(data.trial_rows)
            for index, subject in enumerate(sorted(set(data.subjects.tolist()))):
                fold = root / "folds" / subject.replace(":", "_")
                fold.mkdir(parents=True)
                (fold / "metrics.json").write_text(json.dumps({
                    "status": "complete",
                    "fold_index": index,
                    "held_out_subject": subject,
                    "methods": {"fbcca": {
                        "trial_count": 8,
                        "accuracy": 1.0,
                        "macro_f1": 1.0,
                        "confusion_matrix": np.eye(8, dtype=int).tolist(),
                    }},
                }), encoding="utf-8")

            result = aggregate(argparse.Namespace(prepared_dir=root))

        self.assertTrue(result["methods"]["fbcca"]["complete"])
        self.assertFalse(result["methods"]["transformer"]["complete"])
        self.assertEqual(0, result["methods"]["transformer"]["completed_fold_count"])
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
