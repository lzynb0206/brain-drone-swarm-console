#!/usr/bin/env python3
"""Download and convert four public SSVEP datasets to one compact HDF5 schema.

Archives are processed one at a time.  Only six common posterior channels and a
two-second post-latency window are retained, then the large source archive is
deleted unless ``--keep-archives`` is requested.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

import h5py
import numpy as np
from scipy import signal
from scipy.io import loadmat


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON_CHANNELS = ("O1", "OZ", "O2", "PO3", "POZ", "PO4")
OUTPUT_SAMPLE_RATE_HZ = 250.0
OUTPUT_SAMPLE_COUNT = 500
EXACT_FREQUENCIES_HZ = (8.0, 9.0, 10.0, 11.0, 12.0, 13.0)


class CompactWriter:
    def __init__(
        self,
        path: Path,
        *,
        dataset_name: str,
        frequencies_hz: Sequence[float],
        source_url: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.partial_path = path.with_suffix(path.suffix + ".partial")
        if self.partial_path.exists():
            self.partial_path.unlink()
        self.file = h5py.File(self.partial_path, "w")
        self.file.attrs["dataset_name"] = dataset_name
        self.file.attrs["source_url"] = source_url
        self.file.attrs["sample_rate_hz"] = OUTPUT_SAMPLE_RATE_HZ
        self.file.attrs["sample_count"] = OUTPUT_SAMPLE_COUNT
        self.file.attrs["common_channels_json"] = json.dumps(COMMON_CHANNELS)
        self.file.attrs["frequencies_hz_json"] = json.dumps(
            [float(value) for value in frequencies_hz]
        )
        self.eeg = self.file.create_dataset(
            "eeg",
            shape=(0, len(COMMON_CHANNELS), OUTPUT_SAMPLE_COUNT),
            maxshape=(None, len(COMMON_CHANNELS), OUTPUT_SAMPLE_COUNT),
            chunks=(32, len(COMMON_CHANNELS), OUTPUT_SAMPLE_COUNT),
            compression="gzip",
            compression_opts=4,
            dtype="f4",
        )
        self.label = self._vector("label", "i2")
        self.frequency_hz = self._vector("frequency_hz", "f4")
        self.subject = self._vector("subject", "i2")
        self.block = self._vector("block", "i2")
        self.electrode_type = self._vector("electrode_type", "i1")

    def _vector(self, name: str, dtype: str):
        return self.file.create_dataset(
            name,
            shape=(0,),
            maxshape=(None,),
            chunks=(1024,),
            compression="gzip",
            compression_opts=4,
            dtype=dtype,
        )

    def append(
        self,
        eeg: np.ndarray,
        labels: np.ndarray,
        frequencies: np.ndarray,
        subjects: np.ndarray,
        blocks: np.ndarray,
        electrode_types: np.ndarray,
    ) -> None:
        count = int(len(labels))
        if eeg.shape != (count, len(COMMON_CHANNELS), OUTPUT_SAMPLE_COUNT):
            raise ValueError(f"Unexpected compact EEG shape {eeg.shape}")
        start = int(self.eeg.shape[0])
        end = start + count
        for dataset in (
            self.eeg,
            self.label,
            self.frequency_hz,
            self.subject,
            self.block,
            self.electrode_type,
        ):
            dataset.resize((end, *dataset.shape[1:]))
        self.eeg[start:end] = np.asarray(eeg, dtype=np.float32)
        self.label[start:end] = labels
        self.frequency_hz[start:end] = frequencies
        self.subject[start:end] = subjects
        self.block[start:end] = blocks
        self.electrode_type[start:end] = electrode_types

    def finish(self) -> None:
        self.file.attrs["trial_count"] = int(self.eeg.shape[0])
        self.file.flush()
        self.file.close()
        self.partial_path.replace(self.path)

    def abort(self) -> None:
        self.file.close()
        if self.partial_path.exists():
            self.partial_path.unlink()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    subprocess.run(
        [
            "curl", "-fL", "--silent", "--show-error", "--retry", "3",
            "--continue-at", "-", url, "-o", str(destination),
        ],
        check=True,
    )


def _normal_channel(value: object) -> str:
    return str(value).strip().upper()


def _channel_indices(names: Sequence[object]) -> list[int]:
    normalized = [_normal_channel(name) for name in names]
    missing = [name for name in COMMON_CHANNELS if name not in normalized]
    if missing:
        raise ValueError(f"Missing common channels: {missing}; got {normalized}")
    return [normalized.index(name) for name in COMMON_CHANNELS]


def _append_trials(
    writer: CompactWriter,
    values: list[np.ndarray],
    labels: list[int],
    frequencies: list[float],
    subjects: list[int],
    blocks: list[int],
    electrode_types: list[int],
) -> None:
    writer.append(
        np.asarray(values, dtype=np.float32),
        np.asarray(labels, dtype=np.int16),
        np.asarray(frequencies, dtype=np.float32),
        np.asarray(subjects, dtype=np.int16),
        np.asarray(blocks, dtype=np.int16),
        np.asarray(electrode_types, dtype=np.int8),
    )


def prepare_benchmark(
    root: Path, output: Path, *, keep_archives: bool, max_subjects: int | None
) -> None:
    source = "https://bci.med.tsinghua.edu.cn/"
    cache = root / "cache" / "benchmark"
    support = cache / "support"
    support.mkdir(parents=True, exist_ok=True)
    for name in ("Readme.txt", "64-channels.loc", "Freq_Phase.mat"):
        _download(
            f"https://bci.med.tsinghua.edu.cn/upload/yijun/{name}",
            support / name,
        )
    channel_names = [
        line.split()[-1] for line in (support / "64-channels.loc").read_text().splitlines()
        if line.strip()
    ]
    channels = _channel_indices(channel_names)
    mapping = loadmat(support / "Freq_Phase.mat", squeeze_me=True)
    source_frequencies = np.asarray(mapping["freqs"], dtype=float).reshape(-1)
    targets = [int(np.flatnonzero(np.isclose(source_frequencies, value))[0]) for value in EXACT_FREQUENCIES_HZ]
    writer = CompactWriter(
        output,
        dataset_name="Tsinghua Benchmark",
        frequencies_hz=EXACT_FREQUENCIES_HZ,
        source_url=source,
    )
    subject_count = min(35, max_subjects or 35)
    try:
        for subject in range(1, subject_count + 1):
            archive = cache / f"S{subject}.mat.7z"
            _download(
                f"https://bci.med.tsinghua.edu.cn/upload/yijun/S{subject}.mat.7z",
                archive,
            )
            with tempfile.TemporaryDirectory(prefix="benchmark-") as temporary:
                subprocess.run(
                    ["tar", "-xf", str(archive), "-C", temporary], check=True
                )
                matrix = np.asarray(
                    loadmat(Path(temporary) / f"S{subject}.mat", squeeze_me=True)["data"]
                )
            start = round((0.5 + 0.14) * OUTPUT_SAMPLE_RATE_HZ)
            values: list[np.ndarray] = []
            labels: list[int] = []
            frequencies: list[float] = []
            subjects: list[int] = []
            blocks: list[int] = []
            for label, target in enumerate(targets):
                for block in range(matrix.shape[3]):
                    values.append(matrix[channels, start:start + OUTPUT_SAMPLE_COUNT, target, block])
                    labels.append(label)
                    frequencies.append(EXACT_FREQUENCIES_HZ[label])
                    subjects.append(subject)
                    blocks.append(block + 1)
            _append_trials(writer, values, labels, frequencies, subjects, blocks, [0] * len(labels))
            print(f"benchmark subject {subject}/{subject_count}", flush=True)
            if not keep_archives:
                archive.unlink(missing_ok=True)
        writer.finish()
    except Exception:
        writer.abort()
        raise


def _beta_groups() -> Iterable[tuple[int, int]]:
    for start in range(1, 71, 10):
        yield start, min(start + 9, 70)


def prepare_beta(
    root: Path, output: Path, *, keep_archives: bool, max_subjects: int | None
) -> None:
    source = "https://bci.med.tsinghua.edu.cn/"
    cache = root / "cache" / "beta"
    writer = CompactWriter(
        output,
        dataset_name="BETA",
        frequencies_hz=EXACT_FREQUENCIES_HZ,
        source_url=source,
    )
    subject_limit = min(70, max_subjects or 70)
    try:
        for group_start, group_end in _beta_groups():
            if group_start > subject_limit:
                break
            name = f"S{group_start}-S{group_end}.tar.gz"
            archive = cache / name
            _download(
                f"https://bci.med.tsinghua.edu.cn/upload/liubingchuan/{name}",
                archive,
            )
            with tempfile.TemporaryDirectory(prefix="beta-") as temporary:
                with tarfile.open(archive, "r:gz") as handle:
                    handle.extractall(temporary, filter="data")
                for subject in range(group_start, min(group_end, subject_limit) + 1):
                    payload = loadmat(
                        Path(temporary) / f"S{subject}.mat",
                        struct_as_record=False,
                        squeeze_me=True,
                    )["data"]
                    matrix = np.asarray(payload.EEG)
                    info = payload.suppl_info
                    source_frequencies = np.asarray(info.freqs, dtype=float).reshape(-1)
                    targets = [
                        int(np.flatnonzero(np.isclose(source_frequencies, value))[0])
                        for value in EXACT_FREQUENCIES_HZ
                    ]
                    channel_names = [row[-1] for row in np.asarray(info.chan)]
                    channels = _channel_indices(channel_names)
                    start = round((0.5 + 0.13) * OUTPUT_SAMPLE_RATE_HZ)
                    values: list[np.ndarray] = []
                    labels: list[int] = []
                    frequencies: list[float] = []
                    subjects: list[int] = []
                    blocks: list[int] = []
                    for label, target in enumerate(targets):
                        for block in range(matrix.shape[2]):
                            values.append(matrix[channels, start:start + OUTPUT_SAMPLE_COUNT, block, target])
                            labels.append(label)
                            frequencies.append(EXACT_FREQUENCIES_HZ[label])
                            subjects.append(subject)
                            blocks.append(block + 1)
                    _append_trials(writer, values, labels, frequencies, subjects, blocks, [0] * len(labels))
                    print(f"beta subject {subject}/{subject_limit}", flush=True)
            if not keep_archives:
                archive.unlink(missing_ok=True)
        writer.finish()
    except Exception:
        writer.abort()
        raise


def _wearable_groups() -> Iterable[tuple[int, int]]:
    for start in range(1, 91, 10):
        yield start, start + 9
    yield 91, 102


def prepare_wearable(
    root: Path, output: Path, *, keep_archives: bool, max_subjects: int | None
) -> None:
    source = "https://bci.med.tsinghua.edu.cn/"
    cache = root / "cache" / "wearable"
    frequencies_hz = (9.25, 11.25, 13.25, 9.75, 11.75, 13.75, 10.25, 12.25, 14.25, 10.75, 12.75, 14.75)
    writer = CompactWriter(
        output,
        dataset_name="Wearable SSVEP",
        frequencies_hz=frequencies_hz,
        source_url=source,
    )
    source_channels = ("POZ", "PO3", "PO4", "PO5", "PO6", "OZ", "O1", "O2")
    channels = _channel_indices(source_channels)
    subject_limit = min(102, max_subjects or 102)
    try:
        for group_start, group_end in _wearable_groups():
            if group_start > subject_limit:
                break
            name = f"S{group_start:03d}-S{group_end:03d}.zip"
            archive = cache / name
            _download(
                f"https://bci.med.tsinghua.edu.cn/upload/zhufangkun/{name}",
                archive,
            )
            with tempfile.TemporaryDirectory(prefix="wearable-") as temporary:
                with zipfile.ZipFile(archive) as handle:
                    handle.extractall(temporary)
                for subject in range(group_start, min(group_end, subject_limit) + 1):
                    matrix = np.asarray(
                        loadmat(Path(temporary) / f"S{subject:03d}.mat", squeeze_me=True)["data"]
                    )
                    start = round((0.5 + 0.14) * OUTPUT_SAMPLE_RATE_HZ)
                    values: list[np.ndarray] = []
                    labels: list[int] = []
                    frequencies: list[float] = []
                    subjects: list[int] = []
                    blocks: list[int] = []
                    electrode_types: list[int] = []
                    for label, frequency in enumerate(frequencies_hz):
                        for electrode_type in range(matrix.shape[2]):
                            for block in range(matrix.shape[3]):
                                values.append(
                                    matrix[channels, start:start + OUTPUT_SAMPLE_COUNT, electrode_type, block, label]
                                )
                                labels.append(label)
                                frequencies.append(frequency)
                                subjects.append(subject)
                                blocks.append(block + 1)
                                electrode_types.append(electrode_type + 1)
                    _append_trials(
                        writer, values, labels, frequencies, subjects, blocks, electrode_types
                    )
                    print(f"wearable subject {subject}/{subject_limit}", flush=True)
            if not keep_archives:
                archive.unlink(missing_ok=True)
        writer.finish()
    except Exception:
        writer.abort()
        raise


def prepare_nakanishi(
    root: Path, output: Path, *, keep_archives: bool, max_subjects: int | None
) -> None:
    source = "https://github.com/mnakanishi/12JFPM_SSVEP"
    cache = root / "cache" / "nakanishi"
    archive = cache / "master.zip"
    _download(source + "/archive/refs/heads/master.zip", archive)
    frequencies_hz = (9.25, 11.25, 13.25, 9.75, 11.75, 13.75, 10.25, 12.25, 14.25, 10.75, 12.75, 14.75)
    source_channels = ("PO7", "PO3", "POZ", "PO4", "PO8", "O1", "OZ", "O2")
    channels = _channel_indices(source_channels)
    writer = CompactWriter(
        output,
        dataset_name="Nakanishi 2015",
        frequencies_hz=frequencies_hz,
        source_url=source,
    )
    subject_count = min(10, max_subjects or 10)
    try:
        with tempfile.TemporaryDirectory(prefix="nakanishi-") as temporary:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temporary)
            source_root = Path(temporary) / "12JFPM_SSVEP-master" / "data"
            for subject in range(1, subject_count + 1):
                matrix = np.asarray(loadmat(source_root / f"s{subject}.mat", squeeze_me=True)["eeg"])
                start = 39 + round(0.135 * 256.0)
                values: list[np.ndarray] = []
                labels: list[int] = []
                frequencies: list[float] = []
                subjects: list[int] = []
                blocks: list[int] = []
                for label, frequency in enumerate(frequencies_hz):
                    for block in range(matrix.shape[3]):
                        segment = matrix[label, channels, start:start + 512, block]
                        segment = signal.resample_poly(segment, 125, 128, axis=1)
                        values.append(segment[:, :OUTPUT_SAMPLE_COUNT])
                        labels.append(label)
                        frequencies.append(frequency)
                        subjects.append(subject)
                        blocks.append(block + 1)
                _append_trials(writer, values, labels, frequencies, subjects, blocks, [0] * len(labels))
                print(f"nakanishi subject {subject}/{subject_count}", flush=True)
        writer.finish()
        if not keep_archives:
            archive.unlink(missing_ok=True)
    except Exception:
        writer.abort()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT / "data" / "public_ssvep",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=("benchmark", "beta", "wearable", "nakanishi"),
        default=("benchmark", "beta", "wearable", "nakanishi"),
    )
    parser.add_argument("--keep-archives", action="store_true")
    parser.add_argument("--max-subjects", type=int)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    builders = {
        "benchmark": prepare_benchmark,
        "beta": prepare_beta,
        "wearable": prepare_wearable,
        "nakanishi": prepare_nakanishi,
    }
    for name in args.datasets:
        output = processed / f"{name}_common6_2s.h5"
        if output.exists():
            print(f"skip existing {output}", flush=True)
            continue
        builders[name](
            root,
            output,
            keep_archives=args.keep_archives,
            max_subjects=args.max_subjects,
        )
        with h5py.File(output, "r") as handle:
            print(json.dumps({
                "dataset": name,
                "path": str(output),
                "trials": int(handle["eeg"].shape[0]),
                "shape": list(handle["eeg"].shape),
                "size_bytes": output.stat().st_size,
            }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
