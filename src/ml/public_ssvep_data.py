"""Load and prepare the public Tsinghua Benchmark and BETA SSVEP datasets.

The loader deliberately has no download code.  It consumes the original
``S*.mat`` files already present on disk and converts both public layouts to a
single representation: ``trial x 8 channels x 500 samples`` at 250 Hz.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


TARGET_FREQUENCIES_HZ = tuple(float(value) for value in range(8, 16))
TARGET_CHANNEL_NAMES = ("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4")
SOURCE_SAMPLE_RATE_HZ = 250.0
TARGET_SAMPLE_RATE_HZ = 250.0
PRE_STIMULUS_SECONDS = 0.5
POST_STIMULUS_SECONDS = 0.5
DISCARD_AFTER_ONSET_SECONDS = 0.5
WINDOW_SECONDS = 2.0

# Both Tsinghua releases use this 64-channel order.  The metadata/file supplied
# with a dataset is preferred; this list is only a documented official-layout
# fallback and its use is recorded in the generated manifest.
TSINGHUA_64_CHANNEL_NAMES = (
    "Fp1", "Fpz", "Fp2", "AF3", "AF4", "F7", "F5", "F3", "F1", "Fz",
    "F2", "F4", "F6", "F8", "FT7", "FC5", "FC3", "FC1", "FCz", "FC2",
    "FC4", "FC6", "FT8", "T7", "C5", "C3", "C1", "Cz", "C2", "C4",
    "C6", "T8", "M1", "TP7", "CP5", "CP3", "CP1", "CPz", "CP2", "CP4",
    "CP6", "TP8", "M2", "P7", "P5", "P3", "P1", "Pz", "P2", "P4",
    "P6", "P8", "PO7", "PO5", "PO3", "POz", "PO4", "PO6", "PO8", "CB1",
    "O1", "Oz", "O2", "CB2",
)


def benchmark_frequency_order() -> np.ndarray:
    """Return target-index order used by the Wang2016 Benchmark release."""

    # Targets are stored row-major in the 5 x 8 speller, not in globally
    # ascending frequency order: 8..15, then 8.2..15.2, ..., 8.8..15.8.
    # Getting this fallback wrong yields plausible-looking results with
    # systematically incorrect labels whenever Freq_Phase.mat is absent.
    return np.asarray(
        [8.0 + integer + 0.2 * row for row in range(5) for integer in range(8)],
        dtype=float,
    )


def beta_frequency_order() -> np.ndarray:
    """Return target-index order used by the Liu2020 BETA keyboard."""

    return np.asarray(
        [8.6 + 0.2 * index for index in range(37)] + [8.0, 8.2, 8.4],
        dtype=float,
    )


@dataclass(frozen=True)
class PublicSubject:
    dataset: str
    subject_id: str
    source_file: Path
    eeg: np.ndarray  # channels x time x targets x blocks
    frequencies_hz: np.ndarray
    channel_names: tuple[str, ...]
    sample_rate_hz: float
    frequency_source: str
    channel_source: str


@dataclass(frozen=True)
class PreparedPublicData:
    values: np.ndarray
    targets: np.ndarray
    subjects: np.ndarray
    datasets: np.ndarray
    blocks: np.ndarray
    source_target_indices: np.ndarray
    trial_rows: tuple[dict[str, object], ...]
    manifest: dict[str, object]


def _scipy_io_signal():
    try:
        from scipy import io, signal
    except ImportError as exc:
        raise RuntimeError("公开数据集复现需要 scipy；请安装 requirements-ml.txt") from exc
    return io, signal


def discover_subject_files(root: Path) -> list[Path]:
    """Find original subject MAT files without treating metadata as subjects."""

    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"数据集目录不存在：{root}")
    pattern = re.compile(r"^S0*(\d+)\.mat$", re.IGNORECASE)
    matched = [path for path in root.rglob("*.mat") if pattern.match(path.name)]
    if not matched:
        raise FileNotFoundError(f"目录中未发现 S*.mat 被试文件：{root}")

    def key(path: Path):
        match = pattern.match(path.name)
        return int(match.group(1)), str(path)

    return sorted(matched, key=key)


def subject_number(path: Path) -> int:
    match = re.match(r"^S0*(\d+)\.mat$", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"无法从文件名解析被试编号：{path.name}")
    return int(match.group(1))


def _field(value, names: Sequence[str]):
    candidates = {name.casefold() for name in names}
    current = value
    while isinstance(current, np.ndarray) and current.size == 1:
        current = current.reshape(-1)[0]
    if isinstance(current, np.void) and current.dtype.names:
        for name in current.dtype.names:
            if name.casefold() in candidates:
                return current[name]
    for name in dir(current):
        if name.casefold() in candidates:
            return getattr(current, name)
    return None


def _numeric_vector(value) -> np.ndarray | None:
    if value is None:
        return None
    try:
        array = np.asarray(value, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return None
    array = array[np.isfinite(array)]
    return array if array.size else None


def _string_values(value) -> list[str]:
    strings: list[str] = []
    if value is None:
        return strings
    for item in np.asarray(value, dtype=object).reshape(-1):
        while isinstance(item, np.ndarray) and item.size == 1:
            item = item.reshape(-1)[0]
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="ignore")
        if isinstance(item, str):
            text = item.strip()
            if text:
                strings.append(text)
    return strings


def _read_loc_channels(subject_path: Path, dataset_root: Path) -> tuple[str, ...] | None:
    search_roots = [subject_path.parent]
    for parent in subject_path.parents:
        if parent == dataset_root or dataset_root not in parent.parents:
            search_roots.append(parent)
            break
        search_roots.append(parent)
    seen: set[Path] = set()
    for directory in search_roots:
        if directory in seen or not directory.is_dir():
            continue
        seen.add(directory)
        for candidate in directory.glob("*.loc"):
            names = []
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                fields = line.strip().replace("\t", " ").split()
                if fields:
                    names.append(fields[-1])
            if all(name in names for name in TARGET_CHANNEL_NAMES) and len(names) >= 64:
                return tuple(names[:64])
    return None


def _find_freq_phase(subject_path: Path, dataset_root: Path) -> Path | None:
    current = subject_path.parent
    while True:
        for candidate in current.glob("*.mat"):
            if candidate.name.casefold() == "freq_phase.mat":
                return candidate
        if current == dataset_root or current.parent == current:
            break
        current = current.parent
    matches = list(dataset_root.rglob("Freq_Phase.mat"))
    return matches[0] if len(matches) == 1 else None


def _channel_names_from_metadata(suppl) -> tuple[str, ...] | None:
    value = _field(suppl, ("chan", "channel", "channels", "chan_info", "channel_info"))
    strings = _string_values(value)
    # A BETA location matrix has numeric columns plus a final name column, so
    # extracting only strings produces the 64 labels in order.
    if len(strings) >= 64 and all(name in strings for name in TARGET_CHANNEL_NAMES):
        return tuple(strings[:64])
    return None


def _canonicalize_eeg(eeg, target_count: int, channel_count: int) -> np.ndarray:
    values = np.asarray(eeg)
    values = np.squeeze(values)
    if values.ndim not in (3, 4):
        raise ValueError(f"EEG应为3/4维数组，实际形状为 {values.shape}")

    channel_axes = [i for i, size in enumerate(values.shape) if size == channel_count]
    target_axes = [i for i, size in enumerate(values.shape) if size == target_count]
    if len(channel_axes) != 1 or len(target_axes) != 1:
        raise ValueError(
            f"无法唯一识别通道轴({channel_count})或目标轴({target_count})：{values.shape}"
        )
    channel_axis, target_axis = channel_axes[0], target_axes[0]
    remaining = [i for i in range(values.ndim) if i not in (channel_axis, target_axis)]
    sample_candidates = [i for i in remaining if values.shape[i] >= 500]
    if len(sample_candidates) != 1:
        raise ValueError(f"无法唯一识别时间轴：{values.shape}")
    sample_axis = sample_candidates[0]
    block_axes = [i for i in remaining if i != sample_axis]
    if len(block_axes) > 1:
        raise ValueError(f"无法唯一识别试次块轴：{values.shape}")
    if block_axes:
        canonical = np.transpose(values, (channel_axis, sample_axis, target_axis, block_axes[0]))
    else:
        canonical = np.transpose(values, (channel_axis, sample_axis, target_axis))[..., None]
    if not np.issubdtype(canonical.dtype, np.number):
        raise ValueError("EEG矩阵不是数值类型")
    return np.asarray(canonical, dtype=np.float32)


def _validate_frequency_vector(values, target_count: int) -> np.ndarray | None:
    vector = _numeric_vector(values)
    if vector is None or vector.size != target_count:
        return None
    if float(vector.min()) < 7.0 or float(vector.max()) > 17.0:
        return None
    return vector.astype(float)


def load_public_subject(path: Path, dataset: str, dataset_root: Path) -> PublicSubject:
    """Load one original MAT file and preserve metadata provenance."""

    dataset = dataset.strip().lower()
    if dataset not in {"benchmark", "beta"}:
        raise ValueError("dataset 必须是 benchmark 或 beta")
    io, _ = _scipy_io_signal()
    path = Path(path).expanduser().resolve()
    dataset_root = Path(dataset_root).expanduser().resolve()
    mat = io.loadmat(path, squeeze_me=True, struct_as_record=False)
    raw = mat.get("data")
    if raw is None:
        raise ValueError(f"MAT文件缺少 data 变量：{path}")

    suppl = None
    if dataset == "beta":
        eeg = _field(raw, ("EEG", "eeg"))
        suppl = _field(raw, ("suppl_info", "Suppl_info", "supplementary_info"))
        if eeg is None:
            raise ValueError(f"BETA文件缺少 data.EEG：{path}")
        fallback_frequencies = beta_frequency_order()
    else:
        eeg = raw
        fallback_frequencies = benchmark_frequency_order()

    target_count = len(fallback_frequencies)
    frequency_source = "official-layout-fallback"
    frequencies = None
    if dataset == "beta":
        frequencies = _validate_frequency_vector(
            _field(suppl, ("freqs", "frequencies", "frequency", "freq")), target_count
        )
        if frequencies is not None:
            frequency_source = "data.suppl_info"
    else:
        companion = _find_freq_phase(path, dataset_root)
        if companion is not None:
            companion_mat = io.loadmat(companion, squeeze_me=True, struct_as_record=False)
            frequencies = _validate_frequency_vector(
                companion_mat.get("freqs", companion_mat.get("frequencies")), target_count
            )
            if frequencies is not None:
                frequency_source = str(companion)
    if frequencies is None:
        frequencies = fallback_frequencies

    channel_source = "official-64-channel-order-fallback"
    channel_names = _channel_names_from_metadata(suppl) if suppl is not None else None
    if channel_names is not None:
        channel_source = "data.suppl_info"
    else:
        channel_names = _read_loc_channels(path, dataset_root)
        if channel_names is not None:
            channel_source = "64-channels.loc"
    if channel_names is None:
        channel_names = TSINGHUA_64_CHANNEL_NAMES

    sample_rate = SOURCE_SAMPLE_RATE_HZ
    if suppl is not None:
        candidate = _numeric_vector(_field(suppl, ("srate", "fs", "sample_rate")))
        if candidate is not None and candidate.size:
            sample_rate = float(candidate[0])
    if sample_rate <= 0:
        raise ValueError(f"采样率无效：{sample_rate}")

    canonical = _canonicalize_eeg(eeg, target_count, len(channel_names))
    number = subject_number(path)
    return PublicSubject(
        dataset=dataset,
        subject_id=f"{dataset}:S{number:02d}",
        source_file=path,
        eeg=canonical,
        frequencies_hz=frequencies,
        channel_names=tuple(channel_names),
        sample_rate_hz=sample_rate,
        frequency_source=frequency_source,
        channel_source=channel_source,
    )


def select_frequency_indices(
    available: Sequence[float], requested: Sequence[float] = TARGET_FREQUENCIES_HZ,
    tolerance_hz: float = 0.051,
) -> tuple[int, ...]:
    available_array = np.asarray(available, dtype=float).reshape(-1)
    selected = []
    for frequency in requested:
        distances = np.abs(available_array - float(frequency))
        index = int(np.argmin(distances))
        if float(distances[index]) > tolerance_hz:
            raise ValueError(f"数据集中找不到 {frequency:g} Hz（最近差值 {distances[index]:.3f} Hz）")
        if index in selected:
            raise ValueError("多个目标频率匹配到同一条件索引")
        selected.append(index)
    return tuple(selected)


def select_channel_indices(
    available: Sequence[str], requested: Sequence[str] = TARGET_CHANNEL_NAMES,
) -> tuple[int, ...]:
    normalized = {str(name).strip().casefold(): index for index, name in enumerate(available)}
    missing = [name for name in requested if name.casefold() not in normalized]
    if missing:
        raise ValueError(f"缺少目标通道：{', '.join(missing)}")
    return tuple(normalized[name.casefold()] for name in requested)


def preprocess_public_window(
    values,
    source_fs: float,
    target_fs: float = TARGET_SAMPLE_RATE_HZ,
    window_seconds: float = WINDOW_SECONDS,
) -> np.ndarray:
    """Apply the paper profile without changing the physical time span."""

    _, signal = _scipy_io_signal()
    data = np.asarray(values, dtype=np.float64)
    if data.ndim != 2 or data.shape[0] != len(TARGET_CHANNEL_NAMES):
        raise ValueError(f"窗口必须为8通道二维数组，实际为 {data.shape}")
    if not np.isfinite(data).all():
        raise ValueError("窗口包含 NaN/Inf")
    if abs(float(source_fs) - float(target_fs)) > 1e-8:
        ratio = Fraction(float(target_fs) / float(source_fs)).limit_denominator(1000)
        data = signal.resample_poly(data, ratio.numerator, ratio.denominator, axis=-1)
    expected = int(round(float(target_fs) * float(window_seconds)))
    if abs(data.shape[1] - expected) > 1:
        raise ValueError(f"重采样后窗口点数错误：期望 {expected}，实际 {data.shape[1]}")
    if data.shape[1] != expected:
        data = signal.resample(data, expected, axis=-1)

    data -= data.mean(axis=-1, keepdims=True)
    nyquist = float(target_fs) / 2.0
    high = min(45.0, nyquist * 0.96)
    sos = signal.butter(4, [5.0 / nyquist, high / nyquist], btype="bandpass", output="sos")
    data = signal.sosfiltfilt(sos, data, axis=-1)
    if 50.0 < nyquist:
        b, a = signal.iirnotch(50.0, 30.0, float(target_fs))
        data = signal.filtfilt(b, a, data, axis=-1)
    scale = data.std(axis=-1, keepdims=True)
    if np.any(scale < 1e-12):
        raise ValueError("存在平坦通道")
    return ((data - data.mean(axis=-1, keepdims=True)) / scale).astype(np.float32)


def _subject_trials(subject: PublicSubject) -> Iterable[tuple[np.ndarray, int, int, int]]:
    channel_indices = select_channel_indices(subject.channel_names)
    frequency_indices = select_frequency_indices(subject.frequencies_hz)
    start = int(round((PRE_STIMULUS_SECONDS + DISCARD_AFTER_ONSET_SECONDS) * subject.sample_rate_hz))
    length = int(round(WINDOW_SECONDS * subject.sample_rate_hz))
    stop = start + length
    stimulus_stop = subject.eeg.shape[1] - int(
        round(POST_STIMULUS_SECONDS * subject.sample_rate_hz)
    )
    if stop > stimulus_stop:
        raise ValueError(
            f"{subject.subject_id} 刺激段过短：丢弃刺激开始后0.5秒后无法取得完整2秒；"
            f"所需终点 {stop}，刺激实际终点 {stimulus_stop}。"
            "该短试次不能混入刺激结束后的0.5秒，请用 --skip-invalid-subjects 明确排除"
        )
    for label, source_target in enumerate(frequency_indices):
        for block in range(subject.eeg.shape[3]):
            raw = subject.eeg[np.asarray(channel_indices), start:stop, source_target, block]
            yield preprocess_public_window(raw, subject.sample_rate_hz), label, source_target, block


def prepare_public_data(
    roots: Sequence[tuple[str, Path]],
    output_dir: Path,
    skip_invalid_subjects: bool = False,
) -> PreparedPublicData:
    """Prepare all subject files and write an auditable trial manifest/cache."""

    output_dir = Path(output_dir).expanduser().resolve()
    prepared_dir = output_dir / "prepared"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    values: list[np.ndarray] = []
    targets: list[int] = []
    subjects: list[str] = []
    datasets: list[str] = []
    blocks: list[int] = []
    source_targets: list[int] = []
    rows: list[dict[str, object]] = []
    subject_summaries: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    seen_subjects: dict[str, Path] = {}

    for dataset, root_value in roots:
        root = Path(root_value).expanduser().resolve()
        for path in discover_subject_files(root):
            try:
                subject = load_public_subject(path, dataset, root)
                if subject.subject_id in seen_subjects:
                    raise ValueError(
                        f"被试编号重复：{subject.subject_id} 同时出现在 "
                        f"{seen_subjects[subject.subject_id]} 和 {path}；"
                        "请把root指向唯一的数据版本（不要同时包含原版与wof版）"
                    )
                seen_subjects[subject.subject_id] = path
                first_row = len(values)
                for trial, label, source_target, block in _subject_trials(subject):
                    row = {
                        "row": len(values),
                        "dataset": subject.dataset,
                        "subject_id": subject.subject_id,
                        "source_file": str(subject.source_file),
                        "block_index": int(block),
                        "source_target_index": int(source_target),
                        "label_index": int(label),
                        "frequency_hz": float(TARGET_FREQUENCIES_HZ[label]),
                    }
                    values.append(trial)
                    targets.append(label)
                    subjects.append(subject.subject_id)
                    datasets.append(subject.dataset)
                    blocks.append(block)
                    source_targets.append(source_target)
                    rows.append(row)
                subject_summaries.append({
                    "dataset": subject.dataset,
                    "subject_id": subject.subject_id,
                    "source_file": str(subject.source_file),
                    "trial_count": len(values) - first_row,
                    "block_count": int(subject.eeg.shape[3]),
                    "source_shape": list(subject.eeg.shape),
                    "source_sample_rate_hz": float(subject.sample_rate_hz),
                    "frequency_source": subject.frequency_source,
                    "channel_source": subject.channel_source,
                })
            except Exception as exc:
                error = {"dataset": dataset, "source_file": str(path), "error": str(exc)}
                errors.append(error)
                if not skip_invalid_subjects:
                    raise ValueError(f"解析公开数据失败：{path}：{exc}") from exc

    if not values:
        raise ValueError("没有成功准备任何公开SSVEP试次")
    value_array = np.stack(values).astype(np.float32)
    target_array = np.asarray(targets, dtype=np.int64)
    subject_array = np.asarray(subjects)
    dataset_array = np.asarray(datasets)
    block_array = np.asarray(blocks, dtype=np.int64)
    source_target_array = np.asarray(source_targets, dtype=np.int64)
    cache_path = prepared_dir / "public_ssvep_8class.npz"
    np.savez_compressed(
        cache_path,
        eeg=value_array,
        targets=target_array,
        subjects=subject_array,
        datasets=dataset_array,
        blocks=block_array,
        source_target_indices=source_target_array,
    )

    trial_manifest_path = output_dir / "trials_manifest.csv"
    with trial_manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    counts = {f"{frequency:g}Hz": int(np.sum(target_array == index))
              for index, frequency in enumerate(TARGET_FREQUENCIES_HZ)}
    manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "public-offline-eight-frequency-classification",
        "runtime_compatible": False,
        "runtime_incompatibility": "公开复现无空闲类，且为8频分类；不得覆盖项目7类实机模型",
        "datasets": sorted(set(datasets)),
        "subject_count": len(set(subjects)),
        "trial_count": len(values),
        "labels_hz": list(TARGET_FREQUENCIES_HZ),
        "has_idle_class": False,
        "channel_names": list(TARGET_CHANNEL_NAMES),
        "preprocessing": {
            "source_epoch_pre_stimulus_seconds": PRE_STIMULUS_SECONDS,
            "source_epoch_post_stimulus_seconds": POST_STIMULUS_SECONDS,
            "discard_after_stimulus_onset_seconds": DISCARD_AFTER_ONSET_SECONDS,
            "window_seconds": WINDOW_SECONDS,
            "target_sample_rate_hz": TARGET_SAMPLE_RATE_HZ,
            "bandpass_hz": [5.0, 45.0],
            "notch_hz": 50.0,
            "per_channel_zscore": True,
        },
        "class_counts": counts,
        "cache_file": str(cache_path.relative_to(output_dir)),
        "trial_manifest_file": str(trial_manifest_path.relative_to(output_dir)),
        "subjects": subject_summaries,
        "skipped_subjects": errors,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return PreparedPublicData(
        values=value_array,
        targets=target_array,
        subjects=subject_array,
        datasets=dataset_array,
        blocks=block_array,
        source_target_indices=source_target_array,
        trial_rows=tuple(rows),
        manifest=manifest,
    )


def load_prepared_public_data(output_dir: Path) -> PreparedPublicData:
    """Reload a previously prepared cache without re-reading all MAT files."""

    output_dir = Path(output_dir).expanduser().resolve()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    cache_path = Path(manifest["cache_file"])
    if not cache_path.is_absolute():
        cache_path = output_dir / cache_path
    with np.load(cache_path, allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    rows = []
    with (output_dir / "trials_manifest.csv").open("r", newline="", encoding="utf-8") as handle:
        rows.extend(csv.DictReader(handle))
    return PreparedPublicData(
        values=np.asarray(arrays["eeg"], dtype=np.float32),
        targets=np.asarray(arrays["targets"], dtype=np.int64),
        subjects=np.asarray(arrays["subjects"]).astype(str),
        datasets=np.asarray(arrays["datasets"]).astype(str),
        blocks=np.asarray(arrays["blocks"], dtype=np.int64),
        source_target_indices=np.asarray(arrays["source_target_indices"], dtype=np.int64),
        trial_rows=tuple(rows),
        manifest=manifest,
    )
