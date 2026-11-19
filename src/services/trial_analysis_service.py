"""Import labeled EEG trials and generate a reproducible single-trial report."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from src.bci.config import (
    COMMANDS,
    FBCCA_MIN_SCORE,
    LABEL_TO_FREQUENCY,
    SSVEP_CHANNEL_NAMES,
)
from src.bci.decoder_settings import DecoderSettings
from src.bci.montage import HYBRID_IDS, MONTAGES
from src.bci.preprocessing import (
    FILTER_BANK_HIGH_EDGE_HZ,
    FILTER_BANK_LOW_EDGES_HZ,
    assess_signal_quality,
    filter_bank,
    notch_mains,
    remove_linear_drift,
)


@dataclass(frozen=True)
class TrialAnalysisRecord:
    """Lightweight metadata for one labeled raw EEG trial."""

    path: Path
    trial_id: str
    true_label: str
    sample_rate_hz: float
    channel_names: tuple[str, ...]
    channel_count: int
    sample_count: int
    duration_seconds: float
    start_time: str = ""
    session_id: str = ""
    trial_index: str = ""
    analysis_offset_seconds: float | None = None
    experiment_id: str = ""
    experiment_start_time: str = ""
    subject_id: str = ""
    subject_name: str = ""
    acquisition_date: str = ""
    paradigm_type: str = "SSVEP"
    stage: str = ""
    pair_id: str = ""
    montage_id: str = ""
    record_kind: str = ""
    preceding_ssvep_trial_id: str = ""
    ssvep_true_label: str = ""
    mi_true_label: str = ""
    collection_type: str = ""
    collection_scheme: str = ""
    acquisition_subject_id: str = ""
    ssvep_channel_names: tuple[str, ...] = ()
    mi_channel_names: tuple[str, ...] = ()
    alignment_status: str = ""
    decoder_settings: DecoderSettings | None = None

    @property
    def collection_label(self) -> str:
        label = self.collection_type or ("SSVEP+MI混合" if self.is_serial_pair else
                 "静息" if self.analysis_kind == "rest" else self.paradigm_type)
        scheme = {"pilot": "流程试跑", "rest": "专门静息",
                  "rest_open": "睁眼静息", "rest_closed": "闭眼静息"}.get(self.collection_scheme, "")
        return f"{label} · {scheme}" if scheme else label

    @property
    def analysis_kind(self) -> str:
        if self.stage in {"baseline", "rest_open", "rest_closed"}:
            return "rest"
        return "mi" if self.paradigm_type.upper() == "MI" or self.stage == "mi" else "ssvep"

    @property
    def is_serial_pair(self) -> bool:
        return bool(self.pair_id and self.ssvep_true_label and self.mi_true_label
                    and self.stage in {"ssvep", "mi"})


@dataclass(frozen=True)
class TrialAnalysisReport:
    """In-memory figure and key decision values for a selected trial window."""

    figure_png: bytes
    metrics_path: Path
    channel_quality_path: Path
    trial_id: str
    true_label: str
    start_seconds: float
    end_seconds: float
    candidate_label: str
    confidence: float
    margin: float
    accepted: bool
    usable_channel_count: int
    total_channel_count: int
    analysis_kind: str = "ssvep"


class TrialAnalysisService:
    """Read labeled NPZ trials and create the seven-panel research report."""

    def __init__(self, output_root: Path | str) -> None:
        self.output_root = Path(output_root).expanduser().resolve()

    @staticmethod
    def prepare_renderer():
        """Initialize Matplotlib's non-GUI backend on the Qt main thread."""

        cache_dir = Path(tempfile.gettempdir()) / "brain-drone-matplotlib"
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        matplotlib.rcParams["font.sans-serif"] = [
            "PingFang SC",
            "Heiti SC",
            "Arial Unicode MS",
            "Noto Sans CJK SC",
            "DejaVu Sans",
        ]
        matplotlib.rcParams["axes.unicode_minus"] = False
        return Figure, FigureCanvasAgg

    def discover(self, source: Path | str) -> tuple[list[TrialAnalysisRecord], list[str]]:
        """Return valid labeled trials and non-fatal import warnings."""

        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            raise ValueError(f"数据路径不存在：{source_path}")
        if source_path.is_file():
            candidates = [source_path]
        else:
            raw_trial_dir = source_path / "raw_trials"
            search_root = raw_trial_dir if raw_trial_dir.is_dir() else source_path
            candidates = sorted(search_root.rglob("*.npz"))
        if not candidates:
            raise ValueError("没有找到 NPZ 试次文件")

        records: list[TrialAnalysisRecord] = []
        warnings: list[str] = []
        for path in candidates:
            try:
                record = self.read_metadata(path)
            except (OSError, ValueError, KeyError) as exc:
                warnings.append(f"{path.name}: {exc}")
                continue
            if not record.true_label:
                warnings.append(f"{path.name}: 缺少真实标签")
                continue
            records.append(record)
        if not records:
            detail = f"；首个问题：{warnings[0]}" if warnings else ""
            raise ValueError(f"没有找到可分析的带标签 EEG 试次{detail}")
        records.sort(key=lambda row: (row.true_label, row.start_time, row.trial_id))
        return records, warnings

    @staticmethod
    def read_metadata(path: Path | str) -> TrialAnalysisRecord:
        trial_path = Path(path).expanduser().resolve()
        with np.load(trial_path, allow_pickle=False) as payload:
            if not {"eeg", "fs", "true_label"}.issubset(payload.files):
                raise ValueError("不是带 eeg/fs/true_label 的原始试次")
            eeg = np.asarray(payload["eeg"])
            if eeg.ndim != 2 or eeg.shape[0] == 0 or eeg.shape[1] < 32:
                raise ValueError("EEG 必须是 channels × samples 的二维数组")
            sample_rate = float(np.asarray(payload["fs"]).reshape(-1)[0])
            if not np.isfinite(sample_rate) or sample_rate <= 0:
                raise ValueError("采样率无效")
            label = TrialAnalysisService._text_scalar(payload, "true_label")
            names = TrialAnalysisService._string_tuple(payload, "channel_names")
            if len(names) != eeg.shape[0]:
                names = tuple(
                    SSVEP_CHANNEL_NAMES[index]
                    if index < len(SSVEP_CHANNEL_NAMES) else f"CH{index + 1}"
                    for index in range(eeg.shape[0])
                )
            context = TrialAnalysisService._json_scalar(payload, "trial_context")
            metadata = TrialAnalysisService._json_scalar(payload, "metadata")
            trial_id = str(
                context.get("trial_id") or metadata.get("trial_id") or trial_path.stem
            )
            start_time = TrialAnalysisService._text_scalar(payload, "start_time")
            session_id = TrialAnalysisService._text_scalar(payload, "session_id")
            experiment_id = TrialAnalysisService._experiment_id(
                context,
                metadata,
                trial_id,
                session_id,
                start_time,
                trial_path,
            )
            experiment_start_time = TrialAnalysisService._experiment_start_time(
                experiment_id,
                start_time,
            )
            acquisition_date = TrialAnalysisService._acquisition_date(
                experiment_start_time,
                experiment_id,
                trial_path,
            )
            window_start = TrialAnalysisService._optional_number(
                payload, "window_start_lsl_time"
            )
            analysis_time = TrialAnalysisService._optional_number(
                payload, "analysis_lsl_time"
            )
            analysis_offset = None
            if window_start is not None and analysis_time is not None:
                candidate = analysis_time - window_start
                if 0 <= candidate < eeg.shape[1] / sample_rate:
                    analysis_offset = float(candidate)
            if analysis_offset is None and context.get('recording_mode') == 'external_bdf':
                analysis_offset = float(context.get('discard_seconds', 0.0))
            return TrialAnalysisRecord(
                path=trial_path,
                trial_id=trial_id,
                true_label=label,
                sample_rate_hz=sample_rate,
                channel_names=names,
                channel_count=int(eeg.shape[0]),
                sample_count=int(eeg.shape[1]),
                duration_seconds=float(eeg.shape[1] / sample_rate),
                start_time=start_time,
                session_id=session_id,
                trial_index=str(context.get("trial_index", "")),
                analysis_offset_seconds=analysis_offset,
                experiment_id=experiment_id,
                experiment_start_time=experiment_start_time,
                acquisition_date=acquisition_date,
                subject_id=str(context.get("acquisition_subject_id") or ""),
                subject_name=str(context.get("subject_name") or TrialAnalysisService._text_scalar(payload, "subject_name") or ""),
                acquisition_subject_id=str(context.get("acquisition_subject_id") or ""),
                collection_type=str(context.get("collection_type") or TrialAnalysisService._text_scalar(payload, "collection_type") or ""),
                collection_scheme=str(context.get("collection_scheme") or TrialAnalysisService._text_scalar(payload, "collection_scheme") or ""),
                paradigm_type=str(context.get("paradigm_type") or TrialAnalysisService._text_scalar(payload, "paradigm_type") or "SSVEP"),
                stage=str(context.get("stage") or ""),
                pair_id=str(context.get("pair_id") or ""),
                montage_id=str(context.get("montage_id") or ""),
                ssvep_channel_names=tuple(context.get('ssvep_channel_names') or ()),
                mi_channel_names=tuple(context.get('mi_channel_names') or ()),
                alignment_status=str(context.get('alignment_status') or ''),
                decoder_settings=(DecoderSettings.from_dict(context["decoder_settings"])
                                  if context.get("decoder_settings") else None),
                record_kind=str(context.get("record_kind") or ""),
                preceding_ssvep_trial_id=str(context.get("preceding_ssvep_trial_id") or ""),
                ssvep_true_label=str(context.get("ssvep_true_label") or ""),
                mi_true_label=str(context.get("mi_true_label") or ""),
            )

    @staticmethod
    def _experiment_id(
        context: dict,
        metadata: dict,
        trial_id: str,
        session_id: str,
        start_time: str,
        trial_path: Path,
    ) -> str:
        """Return the acquisition-run identity used by the analysis browser."""

        run_id = str(context.get("run_id") or metadata.get("run_id") or "").strip()
        if run_id:
            return run_id
        trial_match = re.match(r"^(.+)-trial-\d+$", trial_id)
        if trial_match:
            return trial_match.group(1)
        if session_id:
            return session_id
        try:
            minute = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            minute = None
        if minute is not None:
            return f"experiment-{minute:%Y%m%d-%H%M}"
        return f"directory-{trial_path.parent}"

    @staticmethod
    def _experiment_start_time(experiment_id: str, fallback: str) -> str:
        """Prefer the run/session timestamp so every trial shares one group time."""

        match = re.search(r"(?:^|-)(\d{8})-(\d{6})(?:-|$)", experiment_id)
        if match:
            try:
                parsed = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
            except ValueError:
                pass
            else:
                return parsed.isoformat(timespec="seconds")
        return fallback

    @staticmethod
    def _acquisition_date(
        experiment_start_time: str,
        experiment_id: str,
        trial_path: Path,
    ) -> str:
        """Return a stable local calendar date for the subject hierarchy."""

        try:
            parsed = datetime.fromisoformat(
                str(experiment_start_time).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return f"{parsed:%Y-%m-%d}"
        match = re.search(r"(?:^|-)(\d{4})(\d{2})(\d{2})(?:-|$)", experiment_id)
        if match:
            return "-".join(match.groups())
        return datetime.fromtimestamp(trial_path.stat().st_mtime).strftime("%Y-%m-%d")

    def generate_report(
        self,
        record: TrialAnalysisRecord,
        start_seconds: float,
        end_seconds: float,
    ) -> TrialAnalysisReport:
        """Analyze one window, render its figure in memory, and save numeric results."""

        with np.load(record.path, allow_pickle=False) as payload:
            eeg = np.asarray(payload["eeg"], dtype=float)
        if eeg.ndim != 2 or not np.isfinite(eeg).all():
            raise ValueError("试次 EEG 包含无效值或维度错误")
        start, end = self._validate_window(record, start_seconds, end_seconds)
        start_index = int(round(start * record.sample_rate_hz))
        end_index = int(round(end * record.sample_rate_hz))
        window = eeg[:, start_index:end_index]
        if window.shape[1] < 100:
            raise ValueError("所选分析片段太短，至少需要 100 个采样点")

        quality = assess_signal_quality(window, record.sample_rate_hz)
        if record.alignment_status:
            quality['alignment_status'] = record.alignment_status
            quality['hardware_synchronized'] = False
        if record.analysis_kind in {"mi", "rest"}:
            from src.services.mi_trial_analysis import generate_mi_report

            return generate_mi_report(self, record, eeg, window, start, end, quality)
        decoder_error = ""
        try:
            settings = record.decoder_settings or DecoderSettings()
            decoder = settings.create_decoder()
            if record.ssvep_channel_names:
                if any(n not in record.channel_names for n in record.ssvep_channel_names):
                    raise ValueError('所选 SSVEP 电极不在文件中')
                indices = tuple(record.channel_names.index(n) for n in record.ssvep_channel_names)
                decoder = settings.create_decoder(channels_1based=tuple(i+1 for i in indices))
            elif record.montage_id in HYBRID_IDS:
                montage = MONTAGES[record.montage_id]
                indices = montage.indices(record.channel_names, "SSVEP")
                decoder = settings.create_decoder(channels_1based=tuple(i + 1 for i in indices))
            result = decoder.decode(window, record.sample_rate_hz)
            candidate_label = result.label
            confidence = float(result.confidence)
            margin = float(result.margin)
            accepted = bool(result.accepted)
            scores = {name: float(result.scores.get(name, 0.0)) for name in COMMANDS}
        except ValueError as exc:
            decoder_error = str(exc)
            candidate_label = "无法解码"
            confidence = 0.0
            margin = 0.0
            accepted = False
            scores = {name: 0.0 for name in COMMANDS}

        output_dir = self.output_root / self._safe_component(record.true_label)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = (
            f"{self._safe_component(record.trial_id)}_"
            f"{start:.2f}-{end:.2f}s"
        )
        metrics_path = output_dir / f"{stem}.json"
        channel_quality_path = output_dir / f"{stem}_channels.csv"

        target_frequency = LABEL_TO_FREQUENCY.get(record.true_label)
        metrics = {
            "schema_version": 1,
            "source_path": str(record.path),
            "trial": {
                **asdict(record),
                "path": str(record.path),
            },
            "analysis_window_seconds": {"start": start, "end": end},
            "target_frequency_hz": target_frequency,
            "quality": quality,
            "fbcca": {
                "channel_names": [record.channel_names[i - 1] for i in decoder.channels_1based if i <= record.channel_count],
                "candidate_label": candidate_label,
                "confidence": confidence,
                "margin": margin,
                "accepted": accepted,
                "score_threshold": settings.min_score,
                "decoder_settings": settings.to_dict(),
                "scores": scores,
                "error": decoder_error,
            },
        }
        metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_channel_quality(channel_quality_path, record, quality)
        figure_png = self._render_report(
            record,
            eeg,
            window,
            start,
            end,
            quality,
            scores,
            candidate_label,
            confidence,
            margin,
            accepted,
            decoder_error,
        )
        return TrialAnalysisReport(
            figure_png=figure_png,
            metrics_path=metrics_path,
            channel_quality_path=channel_quality_path,
            trial_id=record.trial_id,
            true_label=record.true_label,
            start_seconds=start,
            end_seconds=end,
            candidate_label=candidate_label,
            confidence=confidence,
            margin=margin,
            accepted=accepted,
            usable_channel_count=int(quality["usable_channel_count"]),
            total_channel_count=int(quality["total_channel_count"]),
        )

    @staticmethod
    def validate_pair(ssvep: TrialAnalysisRecord, mi: TrialAnalysisRecord) -> None:
        """A neighboring file is not sufficient evidence of a serial pair."""
        if (ssvep.stage != "ssvep" or mi.stage != "mi" or not ssvep.is_serial_pair
                or not mi.is_serial_pair or not ssvep.experiment_id
                or ssvep.experiment_id != mi.experiment_id or ssvep.pair_id != mi.pair_id
                or ssvep.subject_id != mi.subject_id or ssvep.montage_id != mi.montage_id
                or ssvep.channel_names != mi.channel_names or ssvep.sample_rate_hz != mi.sample_rate_hz
                or ssvep.record_kind != mi.record_kind
                or ssvep.trial_id != mi.preceding_ssvep_trial_id
                or ssvep.true_label != mi.ssvep_true_label or mi.true_label != ssvep.mi_true_label
                or ssvep.true_label != ssvep.ssvep_true_label or mi.true_label != mi.mi_true_label):
            raise ValueError("SSVEP / MI 的批次、受试者或配对标签不一致")
        timestamps = []
        for record in (ssvep, mi):
            with np.load(record.path, allow_pickle=False) as payload:
                if "sample_timestamps" not in payload:
                    raise ValueError("配对记录缺少逐样本时间戳，无法核对先后顺序")
                times = np.asarray(payload["sample_timestamps"], dtype=float)
            if (times.ndim != 1 or len(times) != record.sample_count or not np.isfinite(times).all()
                    or not len(times) or not np.all(np.diff(times) > 0)):
                raise ValueError("配对时间戳不完整或不递增")
            timestamps.append(times)
        if timestamps[0][-1] >= timestamps[1][0]:
            raise ValueError("MI 起点必须晚于同组 SSVEP 结束时间")

    @staticmethod
    def _validate_window(
        record: TrialAnalysisRecord,
        start_seconds: float,
        end_seconds: float,
    ) -> tuple[float, float]:
        start = max(0.0, float(start_seconds))
        end = min(record.duration_seconds, float(end_seconds))
        if end <= start:
            raise ValueError("分析结束时间必须晚于开始时间")
        return start, end

    @staticmethod
    def _write_channel_quality(
        path: Path,
        record: TrialAnalysisRecord,
        quality: dict[str, object],
    ) -> None:
        fields = (
            "channel",
            "usable",
            "std_uv",
            "max_centered_amplitude_uv",
            "raw_mains_50hz_ratio",
            "residual_mains_50hz_ratio",
            "mains_50hz_ratio",
            "issues",
        )
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for index, channel in enumerate(quality["channels"]):
                row = dict(channel)
                row["channel"] = record.channel_names[index]
                row["issues"] = "；".join(row.get("issues", ())) or "合格"
                writer.writerow({field: row.get(field, "") for field in fields})

    @staticmethod
    def _render_report(
        record: TrialAnalysisRecord,
        eeg: np.ndarray,
        window: np.ndarray,
        start: float,
        end: float,
        quality: dict[str, object],
        scores: dict[str, float],
        candidate_label: str,
        confidence: float,
        margin: float,
        accepted: bool,
        decoder_error: str,
    ) -> bytes:
        Figure, FigureCanvasAgg = TrialAnalysisService.prepare_renderer()

        target_frequency = LABEL_TO_FREQUENCY.get(record.true_label)
        quality_pass = int(quality["usable_channel_count"]) >= 2
        evidence = "通过" if quality_pass else "未通过"
        decision = "通过" if accepted else "拒绝"
        title = (
            f"真实“{record.true_label}"
            + (f"/{target_frequency:g} Hz" if target_frequency is not None else "")
            + f"”试次 · 通道质量{evidence} · FBCCA {decision}（候选 {candidate_label}）"
        )
        figure = Figure(figsize=(18, 20), constrained_layout=True)
        FigureCanvasAgg(figure)
        grid = figure.add_gridspec(4, 2, height_ratios=(1.0, 1.0, 1.0, 1.0))
        ax_wave = figure.add_subplot(grid[0, 0])
        ax_table = figure.add_subplot(grid[0, 1])
        ax_raw_fft = figure.add_subplot(grid[1, 0])
        ax_clean_fft = figure.add_subplot(
            grid[1, 1], sharex=ax_raw_fft, sharey=ax_raw_fft,
        )
        ax_fft_comparison = figure.add_subplot(
            grid[2, :], sharex=ax_raw_fft, sharey=ax_raw_fft,
        )
        ax_bands = figure.add_subplot(grid[3, 0])
        ax_scores = figure.add_subplot(grid[3, 1])
        figure.suptitle(title, fontsize=18, fontweight="bold")

        TrialAnalysisService._plot_waveforms(
            ax_wave, record, eeg, start, end
        )
        TrialAnalysisService._plot_quality_table(ax_table, record, quality)
        detrended = remove_linear_drift(window)
        cleaned = notch_mains(detrended, record.sample_rate_hz)
        raw_spectrum = TrialAnalysisService._plot_spectrum(
            ax_raw_fft,
            detrended,
            record.sample_rate_hz,
            target_frequency,
            "C  所选片段去漂移后 FFT（陷波前）",
        )
        clean_spectrum = TrialAnalysisService._plot_spectrum(
            ax_clean_fft,
            cleaned,
            record.sample_rate_hz,
            target_frequency,
            "D  去漂移＋50 Hz 陷波后 FFT",
        )
        shared_y_limits = TrialAnalysisService._shared_log_limits(
            raw_spectrum[1], clean_spectrum[1],
        )
        ax_raw_fft.set_ylim(*shared_y_limits)
        ax_clean_fft.set_ylim(*shared_y_limits)
        TrialAnalysisService._plot_spectrum_comparison(
            ax_fft_comparison,
            raw_spectrum,
            clean_spectrum,
            record.sample_rate_hz,
            target_frequency,
            shared_y_limits,
        )
        TrialAnalysisService._plot_filter_bands(
            ax_bands, cleaned, record.sample_rate_hz, target_frequency
        )
        TrialAnalysisService._plot_scores(
            ax_scores,
            scores,
            record.true_label,
            candidate_label,
            confidence,
            margin,
            accepted,
            decoder_error,
        )
        output = io.BytesIO()
        try:
            figure.savefig(output, format="png", dpi=150, facecolor="white")
            return output.getvalue()
        finally:
            figure.clear()

    @staticmethod
    def _plot_waveforms(ax, record, eeg, start: float, end: float) -> None:
        timeline = np.arange(eeg.shape[1]) / record.sample_rate_hz
        centered = eeg - np.mean(eeg, axis=1, keepdims=True)
        scale = np.percentile(np.abs(centered), 95, axis=1, keepdims=True)
        scale[scale < 1e-9] = 1.0
        normalized = centered / scale
        offsets = np.arange(eeg.shape[0] - 1, -1, -1, dtype=float) * 2.25
        for index, (name, offset) in enumerate(zip(record.channel_names, offsets)):
            ax.plot(timeline, normalized[index] + offset, lw=0.8, color="#3B82F6")
            ax.text(-0.015, offset, name, transform=ax.get_yaxis_transform(), ha="right", va="center")
        ax.axvspan(start, end, color="#16A34A", alpha=0.08, label=f"分析窗口 {start:.2f}–{end:.2f}s")
        ax.axvline(start, color="#16A34A", ls="--", lw=1.2)
        ax.axvline(end, color="#16A34A", ls="--", lw=1.2)
        ax.set_xlim(0, record.duration_seconds)
        ax.set_yticks([])
        ax.set_xlabel("试次时间（s）")
        ax.set_title("A  保存的多通道原始波形（逐通道标准化显示）")
        ax.grid(axis="x", alpha=0.18)
        ax.legend(loc="upper right", fontsize=8)

    @staticmethod
    def _plot_quality_table(ax, record, quality: dict[str, object]) -> None:
        ax.axis("off")
        rows = []
        colors = []
        for index, channel in enumerate(quality["channels"]):
            issues = "；".join(channel["issues"]) or "合格"
            rows.append([
                record.channel_names[index],
                "是" if channel["usable"] else "否",
                f"{channel['std_uv']:.1f}",
                f"{channel['max_centered_amplitude_uv']:.0f}",
                f"{100 * channel.get('raw_mains_50hz_ratio', channel['mains_50hz_ratio']):.1f}%",
                f"{100 * channel.get('residual_mains_50hz_ratio', channel['mains_50hz_ratio']):.1f}%",
                issues,
            ])
            colors.append(["#E8F5E9" if channel["usable"] else "#FDECEC"] * 7)
        table = ax.table(
            cellText=rows,
            colLabels=(
                "通道", "可用", "标准差 μV", "最大中心振幅 μV",
                "陷波前50 Hz", "陷波后50 Hz", "结论",
            ),
            cellColours=colors,
            colWidths=(0.08, 0.07, 0.13, 0.17, 0.13, 0.13, 0.29),
            cellLoc="center",
            loc="upper center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1.0, 1.45)
        usable = int(quality["usable_channel_count"])
        total = int(quality["total_channel_count"])
        ax.set_title(f"B  分析窗口通道质量检查 · {usable}/{total} 通道可用", pad=12)
        ax.text(
            0.02,
            0.02,
            "质量检查只判断该片段能否进入解码，不会修改原始数据。",
            transform=ax.transAxes,
            fontsize=9,
        )

    @staticmethod
    def _mean_spectrum(data: np.ndarray, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
        scale = np.std(data, axis=1, keepdims=True)
        scale[scale < 1e-9] = 1.0
        standardized = data / scale
        frequencies = np.fft.rfftfreq(data.shape[1], d=1.0 / sample_rate_hz)
        amplitude = np.mean(np.abs(np.fft.rfft(standardized, axis=1)), axis=0)
        amplitude = np.maximum(amplitude / max(1, data.shape[1]), 1e-8)
        return frequencies, amplitude

    @staticmethod
    def _plot_spectrum(
        ax, data, sample_rate_hz, target_frequency, title: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        frequencies, amplitude = TrialAnalysisService._mean_spectrum(data, sample_rate_hz)
        mask = (frequencies >= 5.0) & (frequencies <= min(60.0, sample_rate_hz * 0.49))
        visible_frequencies = frequencies[mask]
        visible_amplitude = amplitude[mask]
        ax.semilogy(visible_frequencies, visible_amplitude, color="#2F80ED", lw=1.3)
        if target_frequency is not None:
            for harmonic in range(1, 5):
                value = target_frequency * harmonic
                if value > 60:
                    break
                ax.axvline(value, color="#16A34A", alpha=0.55, lw=0.9)
            ax.text(target_frequency + 0.3, max(visible_amplitude) * 0.7, f"{target_frequency:g} Hz 目标", color="#16A34A", fontsize=8)
        if sample_rate_hz > 104:
            ax.axvline(50, color="#F97316", ls=":", lw=1.4)
            ax.text(49.5, max(visible_amplitude) * 0.5, "50 Hz", color="#F97316", fontsize=8, ha="right")
        ax.set_xlim(5, min(60, sample_rate_hz * 0.49))
        ax.set_xlabel("频率（Hz）")
        ax.set_ylabel("平均标准化幅度（对数轴）")
        ax.set_title(title)
        ax.grid(alpha=0.2, which="both")
        return visible_frequencies, visible_amplitude

    @staticmethod
    def _shared_log_limits(*amplitudes: np.ndarray) -> tuple[float, float]:
        """Return one padded positive log range covering every FFT curve."""

        values = np.concatenate([
            np.asarray(amplitude, dtype=float).reshape(-1)
            for amplitude in amplitudes
        ])
        values = values[np.isfinite(values) & (values > 0.0)]
        if not values.size:
            return 1e-8, 1.0
        low = float(np.min(values))
        high = float(np.max(values))
        if high <= low:
            return low / 1.2, high * 1.2
        log_span = np.log10(high) - np.log10(low)
        padding_factor = 10.0 ** max(0.03, log_span * 0.05)
        return low / padding_factor, high * padding_factor

    @staticmethod
    def _plot_spectrum_comparison(
        ax,
        raw_spectrum: tuple[np.ndarray, np.ndarray],
        clean_spectrum: tuple[np.ndarray, np.ndarray],
        sample_rate_hz: float,
        target_frequency: float | None,
        y_limits: tuple[float, float],
    ) -> None:
        raw_frequencies, raw_amplitude = raw_spectrum
        clean_frequencies, clean_amplitude = clean_spectrum
        ax.semilogy(
            raw_frequencies,
            raw_amplitude,
            color="#F97316",
            lw=1.2,
            alpha=0.78,
            label="陷波前（已去漂移）",
        )
        ax.semilogy(
            clean_frequencies,
            clean_amplitude,
            color="#2F80ED",
            lw=1.4,
            alpha=0.92,
            label="50 Hz 陷波后",
        )
        if target_frequency is not None:
            for harmonic in range(1, 5):
                value = target_frequency * harmonic
                if value <= 60:
                    ax.axvline(value, color="#16A34A", ls="--", alpha=0.45, lw=0.8)
        if sample_rate_hz > 104:
            ax.axvline(50, color="#4B5563", ls=":", lw=1.2)
        ax.set_xlim(5, min(60, sample_rate_hz * 0.49))
        ax.set_ylim(*y_limits)
        ax.set_xlabel("频率（Hz）")
        ax.set_ylabel("平均标准化幅度（对数轴）")
        ax.set_title("E  50 Hz 陷波前后 FFT 叠加对比（与 C/D 同一坐标）")
        ax.grid(alpha=0.2, which="both")
        ax.legend(fontsize=9, loc="upper right")

    @staticmethod
    def _plot_filter_bands(ax, data, sample_rate_hz, target_frequency) -> None:
        colors = ("#2F80ED", "#9B51E0", "#0F9D8A")
        for (filtered, weight), low, color in zip(
            filter_bank(data, sample_rate_hz),
            FILTER_BANK_LOW_EDGES_HZ,
            colors,
        ):
            frequencies, amplitude = TrialAnalysisService._mean_spectrum(filtered, sample_rate_hz)
            mask = (frequencies >= 5.0) & (frequencies <= min(60.0, sample_rate_hz * 0.49))
            ax.semilogy(
                frequencies[mask],
                amplitude[mask],
                color=color,
                lw=1.0,
                label=f"{low:g}–{FILTER_BANK_HIGH_EDGE_HZ:g} Hz，权重 {weight:.3f}",
            )
        if target_frequency is not None:
            for harmonic in range(1, 5):
                value = target_frequency * harmonic
                if value <= 60:
                    ax.axvline(value, color="#16A34A", ls="--", alpha=0.45, lw=0.8)
        ax.set_xlim(5, min(60, sample_rate_hz * 0.49))
        ax.set_xlabel("频率（Hz）")
        ax.set_ylabel("各滤波子带平均幅度")
        ax.set_title("F  FBCCA 三个滤波子带")
        ax.grid(alpha=0.2, which="both")
        ax.legend(fontsize=8, loc="upper right")

    @staticmethod
    def _plot_scores(
        ax,
        scores,
        true_label,
        candidate_label,
        confidence,
        margin,
        accepted,
        decoder_error,
    ) -> None:
        values = [scores[name] for name in COMMANDS]
        colors = ["#E98585" if name != candidate_label else "#73BE73" for name in COMMANDS]
        bars = ax.bar(COMMANDS, values, color=colors, width=0.65)
        ax.axhline(FBCCA_MIN_SCORE, color="#4B5563", ls="--", lw=1.0, label=f"默认分数门限 {FBCCA_MIN_SCORE:.2f}")
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.3f}", ha="center", va="bottom", fontsize=8)
        verdict = (
            f"真实：{true_label}｜候选：{candidate_label}｜分数 {confidence:.3f}｜差值 {margin:.3f}｜"
            f"{'通过' if accepted else '拒绝'}"
        )
        if decoder_error:
            verdict += f"｜{decoder_error}"
        ax.set_ylabel("FBCCA 分数")
        ax.set_title(f"G  六类 FBCCA 分数\n{verdict}")
        ax.grid(axis="y", alpha=0.2)
        ax.legend(fontsize=8)

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.-]+", "_", str(value)).strip("._")
        return cleaned or "trial"

    @staticmethod
    def _text_scalar(payload, key: str) -> str:
        if key not in payload.files:
            return ""
        value = np.asarray(payload[key])
        if value.size != 1:
            return ""
        item = value.reshape(-1)[0]
        if isinstance(item, bytes):
            return item.decode("utf-8", errors="replace")
        return str(item)

    @staticmethod
    def _string_tuple(payload, key: str) -> tuple[str, ...]:
        if key not in payload.files:
            return ()
        return tuple(str(value) for value in np.asarray(payload[key]).reshape(-1))

    @staticmethod
    def _json_scalar(payload, key: str) -> dict[str, object]:
        text = TrialAnalysisService._text_scalar(payload, key)
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _optional_number(payload, key: str) -> float | None:
        if key not in payload.files:
            return None
        values = np.asarray(payload[key], dtype=float).reshape(-1)
        if values.size != 1 or not np.isfinite(values[0]):
            return None
        return float(values[0])


__all__ = [
    "TrialAnalysisRecord",
    "TrialAnalysisReport",
    "TrialAnalysisService",
]
