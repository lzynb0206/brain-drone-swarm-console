"""Descriptive motor-channel figures; never turn MI into SSVEP predictions."""

from dataclasses import asdict, replace
import io
import json

import numpy as np
from scipy import signal

from src.bci.preprocessing import notch_mains, remove_linear_drift


MOTOR_CHANNELS = ("C3", "Cz", "C4")
MI_BANDS = (("μ 8–13 Hz", 8., 13.), ("β 13–30 Hz", 13., 30.))


def motor_features(window, names, fs, channels=MOTOR_CHANNELS):
    """Select physical electrodes by name and retain absolute PSD units."""
    if not channels or len(set(channels)) != len(channels) or len(set(names)) != len(names) or any(name not in names for name in channels):
        raise ValueError("MI 分析所选电极名称缺失或重复，不能按位置猜测")
    if fs <= 80:
        raise ValueError("MI 时频分析需要大于 80 Hz 的采样率")
    indices = tuple(names.index(name) for name in channels)
    motor = window[list(indices)]
    clean = notch_mains(remove_linear_drift(motor), fs)
    nperseg = min(round(fs), clean.shape[1])
    frequencies, psd = signal.welch(clean, fs=fs, nperseg=nperseg, axis=-1)
    spacing = frequencies[1] - frequencies[0]
    power = np.stack([
        psd[:, (frequencies >= low) & (frequencies < high)].sum(axis=-1) * spacing
        for _, low, high in MI_BANDS
    ], axis=-1)
    tf_frequencies, times, spectrogram = signal.spectrogram(
        clean, fs=fs, nperseg=nperseg, noverlap=min(nperseg - 1, round(nperseg * .75)), axis=-1,
    )
    return dict(channels=tuple(channels), indices=indices, clean=clean, frequencies=frequencies, psd=psd,
                power=power, tf_frequencies=tf_frequencies, times=times, spectrogram=spectrogram)


def generate_mi_report(service, record, eeg, window, start, end, quality):
    from src.services.trial_analysis_service import TrialAnalysisReport

    channels = record.mi_channel_names or MOTOR_CHANNELS
    features = motor_features(window, record.channel_names, record.sample_rate_hz, channels)
    output_dir = service.output_root / service._safe_component(record.true_label)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{service._safe_component(record.trial_id)}_{start:.2f}-{end:.2f}s"
    metrics_path = output_dir / f"{stem}.json"
    channel_quality_path = output_dir / f"{stem}_channels.csv"
    metrics = {
        "schema_version": 1, "analysis_kind": record.analysis_kind,
        "source_path": str(record.path), "trial": {**asdict(record), "path": str(record.path)},
        "analysis_window_seconds": {"start": start, "end": end}, "quality": quality,
        "mi": {
            "channel_names": channels,
            "band_power_uv2": {name: {band[0]: float(features["power"][i, j]) for j, band in enumerate(MI_BANDS)}
                               for i, name in enumerate(channels)},
            "classifier_applied": False, "erd_computed": False,
            "note": "波形、功率及时频描述；未进行 MI 分类，也未用短基线替代专门静息。",
            "psd_method": "Welch; detrend + 50 Hz notch; up to 1 s segments",
            "band_edges": "lower inclusive, upper exclusive",
        },
    }
    figure_png = render_mi_report(service, record, eeg, start, end, quality, features)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    service._write_channel_quality(channel_quality_path, record, quality)
    return TrialAnalysisReport(
        figure_png=figure_png, metrics_path=metrics_path, channel_quality_path=channel_quality_path,
        trial_id=record.trial_id, true_label=record.true_label, start_seconds=start, end_seconds=end,
        candidate_label="未运行 MI 分类", confidence=0., margin=0., accepted=False,
        usable_channel_count=int(quality["usable_channel_count"]), total_channel_count=int(quality["total_channel_count"]),
        analysis_kind=record.analysis_kind,
    )


def render_mi_report(service, record, eeg, start, end, quality, features):
    channels = features["channels"]
    Figure, FigureCanvasAgg = service.prepare_renderer()
    figure = Figure(figsize=(18, 12+3*int(np.ceil(len(channels)/4))), constrained_layout=True)
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(3, 2, height_ratios=(1., 1., 1.))
    title = "静息" if record.analysis_kind == "rest" else "MI"
    figure.suptitle(
        f"{title} 阶段 · {record.true_label} · {start:g}–{end:g} s\n"
        f"所选 {len(channels)} 路波形与频带功率 · 未运行 MI 分类或 ERD 分析",
        fontsize=17, fontweight="bold",
    )
    motor_record = replace(record, channel_names=channels, channel_count=len(channels))
    ax_wave = figure.add_subplot(grid[0, 0])
    service._plot_waveforms(ax_wave, motor_record, eeg[list(features["indices"])], start, end)
    ax_wave.set_title("A  运动区原始波形（逐通道标准化显示）")
    ax_quality = figure.add_subplot(grid[0, 1])
    service._plot_quality_table(ax_quality, record, quality)
    ax_quality.texts[-1].set_text("保存全部通道；下方 MI 图按指定电极名称分析。")
    ax_psd = figure.add_subplot(grid[1, 0])
    colors = ("#2563eb", "#059669", "#d97706")
    visible = (features["frequencies"] >= 4) & (features["frequencies"] <= 40)
    for i, name in enumerate(channels):
        ax_psd.semilogy(features["frequencies"][visible], np.maximum(features["psd"][i, visible], 1e-12),
                        color=colors[i % len(colors)], label=name)
    for (label, low, high), color in zip(MI_BANDS, ("#dbeafe", "#fef3c7")):
        ax_psd.axvspan(low, high, color=color, alpha=.5, label=label)
    ax_psd.set(title="C  运动区功率谱（Welch）", xlabel="频率（Hz）", ylabel="功率谱密度（μV²/Hz）", xlim=(4, 40))
    ax_psd.legend(fontsize=9, ncol=2)
    ax_psd.grid(alpha=.2)
    ax_power = figure.add_subplot(grid[1, 1])
    positions = np.arange(len(channels))
    for i, (label, _, _) in enumerate(MI_BANDS):
        ax_power.bar(positions + (i - .5) * .32, features["power"][:, i], width=.32,
                     color=("#2563eb", "#d97706")[i], label=label)
    ax_power.set(xticks=positions, xticklabels=channels, ylabel="频带绝对功率（μV²）",
                 title="D  μ / β 频带功率（非分类分数）")
    ax_power.legend()
    ax_power.grid(axis="y", alpha=.2)
    tf_columns = min(4, len(channels))
    tf_grid = grid[2, :].subgridspec(int(np.ceil(len(channels)/tf_columns)), tf_columns)
    tf_mask = (features["tf_frequencies"] >= 4) & (features["tf_frequencies"] <= 40)
    db = 10 * np.log10(np.maximum(features["spectrogram"][:, tf_mask], 1e-12))
    low, high = np.percentile(db, (5, 95))
    if high <= low:
        high = low + 1.
    time_centers = features["times"] + start
    if len(time_centers) == 1:
        time_edges = np.array([start, end])
    else:
        step = time_centers[1] - time_centers[0]
        time_edges = np.r_[time_centers[0] - step / 2, (time_centers[:-1] + time_centers[1:]) / 2,
                           time_centers[-1] + step / 2]
    frequencies = features["tf_frequencies"][tf_mask]
    df = features["tf_frequencies"][1] - features["tf_frequencies"][0]
    frequency_edges = np.r_[frequencies - df / 2, frequencies[-1] + df / 2]
    for i, name in enumerate(channels):
        ax = figure.add_subplot(tf_grid[i//tf_columns, i%tf_columns])
        mesh = ax.pcolormesh(time_edges, frequency_edges, db[i],
                             shading="flat", cmap="viridis", vmin=low, vmax=high)
        ax.set(title=f"{chr(69 + i)}  {name} 时频功率", xlabel="本阶段时间（s）", ylabel="频率（Hz）",
               xlim=(start, end), ylim=(4, 40))
        figure.colorbar(mesh, ax=ax, label="dB（μV²/Hz）", shrink=.82)
    output = io.BytesIO()
    try:
        figure.savefig(output, format="png", dpi=150, facecolor="white")
        return output.getvalue()
    finally:
        figure.clear()
