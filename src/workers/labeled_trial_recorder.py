"""Onset-aligned labelled EEG trial capture for :mod:`eeg_worker`."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Mapping

import numpy as np

from src.bci.preprocessing import assess_signal_quality
from src.bci.decoder_settings import DecoderSettings
from src.bci.montage import HYBRID_IDS, MONTAGES, research_decoder
from src.domain import IntentResult
from src.ports import EEGStream, IntentDecoder
from src.services.experiment_recorder import ExperimentRecorder
from src.services.acquisition_identity import capture_identity
from src.services.experiment_timing import (
    ExperimentClock,
    ExperimentMarkerOutlet,
    ExperimentTimestamp,
)


@dataclass(frozen=True)
class TrialCaptureRequest:
    label: str
    frequency_hz: float
    marker: ExperimentTimestamp
    duration_seconds: float | None
    analysis_offset_seconds: float
    metadata: dict[str, object]


class LabeledTrialRecorder:
    """Own pending-trial state, sample alignment, decoding and persistence."""

    # LSL frames normally contain about 0.13 s of samples (32 samples at
    # 250 Hz).  Half a second leaves ample scheduling margin while still
    # rejecting a request that was accidentally applied during the following
    # rest/cue phase.
    MAX_ONSET_DELAY_SECONDS = 0.5

    def __init__(
        self,
        recorder: ExperimentRecorder,
        decoder: IntentDecoder,
        experiment_clock: ExperimentClock,
        marker_outlet: ExperimentMarkerOutlet,
        *,
        source_name: Callable[[], str],
        experiment_context: Callable[[], tuple[str, str]],
        channel_metadata: Callable[
            [], tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
        ],
        status_changed: Callable[[str], None],
        error_occurred: Callable[[str], None],
        recognition_received: Callable[[IntentResult], None],
        trial_saved: Callable[[str, str], None],
        record_phase: Callable[..., object],
    ) -> None:
        self.recorder = recorder
        self.decoder = decoder
        self.experiment_clock = experiment_clock
        self.marker_outlet = marker_outlet
        self._source_name = source_name
        self._experiment_context = experiment_context
        self._channel_metadata = channel_metadata
        self._status_changed = status_changed
        self._error_occurred = error_occurred
        self._recognition_received = recognition_received
        self._trial_saved = trial_saved
        self._record_phase = record_phase
        self._state_lock = threading.Lock()
        self._pending_true_label = ""
        self._pending_request: TrialCaptureRequest | None = None
        self._capture_generation = 0
        self._active_capture_generation = -1
        self._active_metadata: dict[str, object] = {}
        self._active_capture_seconds: float | None = None
        self._active_analysis_offset_seconds = 0.0
        self._active_stimulus_lsl_time = 0.0
        self._analysis_marker_generation = -1
        self._buffer: np.ndarray | None = None
        self._timestamps = np.asarray([], dtype=np.float64)
        self._start_time = ""
        self._paradigm_type = "SSVEP"
        self._hybrid_decoder = None
        self._hybrid_contract = None

    @property
    def capture_seconds(self) -> float | None:
        with self._state_lock:
            return self._active_capture_seconds

    @property
    def paradigm_type(self) -> str:
        with self._state_lock:
            return self._paradigm_type

    def set_paradigm_type(self, paradigm_type: str) -> None:
        with self._state_lock:
            self._paradigm_type = str(paradigm_type or "SSVEP")

    def capture_next(self, true_label: str) -> None:
        with self._state_lock:
            self._pending_true_label = str(true_label)
            self._active_metadata = {}
            self._active_capture_seconds = None
            self._active_analysis_offset_seconds = 0.0
            self._active_stimulus_lsl_time = 0.0
            self._capture_generation += 1

    def queue_capture(
        self,
        true_label: str,
        frequency_hz: float,
        marker_timestamp: float | None,
        duration_seconds: float | None,
        discard_seconds: float,
        metadata: Mapping[str, object] | None,
    ) -> str:
        label = str(true_label)
        duration = None if duration_seconds is None else float(duration_seconds)
        offset = max(0.0, float(discard_seconds))
        if duration is not None and duration <= 0.0:
            raise ValueError("duration_seconds must be positive")
        if duration is not None and offset >= duration:
            raise ValueError("discard_seconds must be shorter than duration_seconds")
        context = dict(metadata or {})
        trial_id = str(context.get("trial_id", "")).strip()
        if label and not trial_id:
            trial_id = self.recorder.new_trial_id()
            context["trial_id"] = trial_id
        stamp = self.experiment_clock.stamp(marker_timestamp, "auto")
        context.setdefault("stimulus_lsl_time", stamp.lsl_seconds)
        context.setdefault("analysis_start_offset_seconds", offset)
        context.setdefault("capture_duration_seconds", duration)
        context.setdefault("clock_source", stamp.clock_source)
        with self._state_lock:
            self._pending_request = TrialCaptureRequest(
                label=label,
                frequency_hz=float(frequency_hz),
                marker=stamp,
                duration_seconds=duration,
                analysis_offset_seconds=offset,
                metadata=context,
            )
        return trial_id

    def cancel(self) -> dict[str, object]:
        with self._state_lock:
            request = self._pending_request
            context = dict(
                request.metadata if request is not None else self._active_metadata
            )
            self._pending_request = None
            self._pending_true_label = ""
            self._capture_generation += 1
            self._active_metadata = {}
            self._active_capture_seconds = None
            self._active_analysis_offset_seconds = 0.0
            self._active_stimulus_lsl_time = 0.0
        return context

    def pending_true_label(self) -> str:
        with self._state_lock:
            return self._pending_true_label

    def apply_pending(self, stream: EEGStream) -> bool:
        with self._state_lock:
            request = self._pending_request
            self._pending_request = None
        if request is None:
            return False
        discarded = 0
        flush = getattr(stream, "flush", None)
        if callable(flush) and not request.label:
            try:
                discarded = int(flush() or 0)
            except Exception as exc:
                self._error_occurred(f"清空 LSL 历史缓存失败：{exc}")
        with self._state_lock:
            self._pending_true_label = request.label
            self._active_metadata = dict(request.metadata)
            self._active_capture_seconds = request.duration_seconds
            self._active_analysis_offset_seconds = request.analysis_offset_seconds
            self._active_stimulus_lsl_time = request.marker.lsl_seconds
            self._capture_generation += 1
        reset_decoder = getattr(self.decoder, "reset", None)
        if callable(reset_decoder):
            reset_decoder()
        subject_id, session_id = capture_identity(request.metadata, self._experiment_context())
        marker_metadata = {
            **request.metadata,
            "subject_id": subject_id,
            "session_id": session_id,
            "phase": "stimulus",
            "true_label": request.label,
            "frequency_hz": request.frequency_hz,
            "discarded_samples": discarded,
            "analysis_start_offset_seconds": request.analysis_offset_seconds,
            "capture_duration_seconds": request.duration_seconds,
            "mode": "experiment" if request.label else "control",
        }
        self.recorder.record_marker(
            "stimulus_capture_started",
            request.marker.lsl_seconds,
            subject_id,
            session_id,
            marker_metadata,
            unix_timestamp=request.marker.unix_seconds,
            clock_source=request.marker.clock_source,
        )
        self.marker_outlet.publish(
            "stimulus_capture_started",
            request.marker.lsl_seconds,
            marker_metadata,
        )
        if request.label:
            message = (
                f"刺激同步采集：{request.label}，已按 LSL 时间戳对齐"
                "（保留起始原始段）"
            )
        else:
            message = f"在线脑控已同步，已清空 {discarded} 个历史样本并重置解码器"
        self._status_changed(message)
        return True

    def collect(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        sample_rate_hz: float,
        window_samples: int,
    ) -> None:
        with self._state_lock:
            label = self._pending_true_label
            generation = self._capture_generation
            trial_metadata = dict(self._active_metadata)
            analysis_offset = self._active_analysis_offset_seconds
            stimulus_lsl_time = self._active_stimulus_lsl_time
        if not label:
            self._reset_buffer()
            return
        values, timestamps = self._align_to_stimulus(
            values,
            timestamps,
            stimulus_lsl_time,
            generation,
            trial_metadata,
        )
        if values.shape[1] == 0:
            return
        self._validate_capture_onset(
            timestamps,
            stimulus_lsl_time,
            sample_rate_hz,
            generation,
            trial_metadata,
        )
        self._append(values, timestamps, generation)
        self._mark_analysis_start(
            generation,
            stimulus_lsl_time,
            analysis_offset,
            trial_metadata,
        )
        if self._buffer is None or self._buffer.shape[1] < window_samples:
            return
        self._save(label, generation, sample_rate_hz, window_samples, trial_metadata)

    def _align_to_stimulus(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        stimulus_lsl_time: float,
        generation: int,
        trial_metadata: dict[str, object],
    ) -> tuple[np.ndarray, np.ndarray]:
        if not timestamps.size or stimulus_lsl_time <= 0.0:
            return values, timestamps
        distance = abs(float(timestamps[-1]) - stimulus_lsl_time)
        if distance <= 3600.0:
            if float(timestamps[-1]) < stimulus_lsl_time:
                return values[:, :0], timestamps[:0]
            keep = timestamps >= stimulus_lsl_time
            return values[:, keep], timestamps[keep]
        trial_metadata.setdefault("sample_clock_alignment", "unverified_fallback")
        with self._state_lock:
            if self._capture_generation == generation:
                self._active_metadata.setdefault(
                    "sample_clock_alignment",
                    "unverified_fallback",
                )
        return values, timestamps

    def _validate_capture_onset(
        self,
        timestamps: np.ndarray,
        stimulus_lsl_time: float,
        sample_rate_hz: float,
        generation: int,
        trial_metadata: dict[str, object],
    ) -> None:
        """Reject trials whose first available sample missed stimulus onset."""

        if generation == self._active_capture_generation:
            return
        if (
            not timestamps.size
            or stimulus_lsl_time <= 0.0
            or trial_metadata.get("sample_clock_alignment") == "unverified_fallback"
        ):
            return
        onset_delay = float(timestamps[0]) - float(stimulus_lsl_time)
        sample_tolerance = 2.0 / max(float(sample_rate_hz), 1.0)
        allowed_delay = max(self.MAX_ONSET_DELAY_SECONDS, sample_tolerance)
        trial_metadata["capture_onset_delay_seconds"] = onset_delay
        with self._state_lock:
            if self._capture_generation == generation:
                self._active_metadata["capture_onset_delay_seconds"] = onset_delay
        if onset_delay > allowed_delay:
            raise ValueError(
                "EEG试次起点与视觉刺激错位："
                f"首个样本晚了 {onset_delay:.3f} 秒（允许≤{allowed_delay:.3f}秒）。"
                "本试次未保存，请检查 LSL 时间戳和数据流。"
            )

    def _append(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        generation: int,
    ) -> None:
        new_capture = generation != self._active_capture_generation
        channel_changed = self._buffer is None or self._buffer.shape[0] != values.shape[0]
        if new_capture or channel_changed:
            self._active_capture_generation = generation
            self._buffer = values.copy()
            self._timestamps = timestamps.copy()
            first_lsl = (
                float(timestamps[0])
                if timestamps.size
                else self.experiment_clock.now().lsl_seconds
            )
            first_unix = self.experiment_clock.to_unix(first_lsl, "lsl")
            self._start_time = datetime.fromtimestamp(first_unix).astimezone().isoformat(
                timespec="milliseconds"
            )
            return
        self._buffer = np.concatenate((self._buffer, values), axis=1)
        self._timestamps = np.concatenate((self._timestamps, timestamps))

    def _mark_analysis_start(
        self,
        generation: int,
        stimulus_lsl_time: float,
        analysis_offset: float,
        trial_metadata: dict[str, object],
    ) -> None:
        if not trial_metadata.get("trial_id"):
            return
        if generation == self._analysis_marker_generation:
            return
        analysis_target = stimulus_lsl_time + analysis_offset
        eligible = self._timestamps[self._timestamps >= analysis_target]
        if not eligible.size:
            return
        analysis_time = float(eligible[0])
        trial_metadata["analysis_lsl_time"] = analysis_time
        with self._state_lock:
            if self._capture_generation == generation:
                self._active_metadata["analysis_lsl_time"] = analysis_time
        self._record_phase(
            "analysis",
            analysis_time,
            trial_metadata,
            timestamp_domain="lsl",
        )
        self._analysis_marker_generation = generation

    def _save(
        self,
        label: str,
        generation: int,
        sample_rate_hz: float,
        window_samples: int,
        trial_metadata: dict[str, object],
    ) -> None:
        if self._buffer is None:
            return
        labeled_window = self._buffer[:, :window_samples].copy()
        labeled_timestamps = self._timestamps[:window_samples].copy()
        hybrid_capture = trial_metadata.get("montage_id") in HYBRID_IDS
        paradigm_type = str(trial_metadata.get("paradigm_type", self.paradigm_type)) if hybrid_capture else self.paradigm_type
        if hybrid_capture:
            result = self._decode_hybrid_stage(labeled_window, labeled_timestamps, sample_rate_hz, trial_metadata)
        elif paradigm_type == "SSVEP":
            try:
                result = self.decoder.decode(labeled_window, sample_rate_hz)
            except ValueError as exc:
                # A labelled acquisition trial is valuable even when every
                # channel fails the online quality gate.  Keep the raw window
                # and its ground-truth label so it can be inspected or decoded
                # offline; only the online prediction is rejected.  Other
                # programming/configuration errors must still surface.
                if "没有可用 EEG 通道" not in str(exc):
                    raise
                quality = assess_signal_quality(labeled_window, sample_rate_hz)
                quality.update({
                    "decode_error": str(exc),
                    "collection_continued": True,
                    "quality_warning": "全部通道未通过在线质量门限；原始试次已保留",
                })
                result = IntentResult(
                    timestamp=datetime.now().timestamp(),
                    label="",
                    confidence=0.0,
                    margin=0.0,
                    accepted=False,
                    reason=f"低质量试次仍已保存：{exc}",
                    frequency_hz=0.0,
                    scores={},
                    sample_rate_hz=float(sample_rate_hz),
                    sample_count=int(labeled_window.shape[1]),
                    used_channels_1based=(),
                    signal_quality=quality,
                )
        else:
            try:
                quality = assess_signal_quality(labeled_window, sample_rate_hz)
            except Exception as exc:
                quality = {"status": "unknown", "reason": str(exc)}
            result = IntentResult(
                timestamp=datetime.now().timestamp(),
                label="",
                confidence=0.0,
                margin=0.0,
                accepted=False,
                reason="运动想象采集模式：仅保存原始 EEG，尚未启用 MI 分类器",
                frequency_hz=0.0,
                scores={},
                sample_rate_hz=float(sample_rate_hz),
                sample_count=int(labeled_window.shape[1]),
                used_channels_1based=tuple(range(1, labeled_window.shape[0] + 1)),
                signal_quality=quality,
            )
        subject_id, session_id = capture_identity(trial_metadata, self._experiment_context())
        channel_names, original_channel_names, channel_units = self._channel_metadata()
        saved = self.recorder.record(
            result,
            labeled_window,
            label,
            self._source_name(),
            subject_id,
            session_id,
            self._start_time,
            sample_timestamps=labeled_timestamps,
            channel_names=channel_names,
            original_channel_names=original_channel_names,
            channel_units=channel_units,
            paradigm_type=paradigm_type,
            log_recognition=paradigm_type == "SSVEP",
            trial_metadata=trial_metadata,
            clock_source=self.experiment_clock.source,
        )
        if paradigm_type == "SSVEP" and not hybrid_capture:
            self._recognition_received(result)
        self._trial_saved(label, saved["raw_trial_path"])
        with self._state_lock:
            if self._capture_generation == generation:
                self._pending_true_label = ""
                self._active_metadata = {}
                self._active_capture_seconds = None
                self._active_analysis_offset_seconds = 0.0
                self._active_stimulus_lsl_time = 0.0
        self._reset_buffer()

    def _decode_hybrid_stage(self, eeg, timestamps, fs, metadata):
        montage = MONTAGES[metadata["montage_id"]]
        names, _, _ = self._channel_metadata()
        if tuple(names) != montage.names or eeg.shape[0] != len(montage.names):
            raise ValueError("采集通道必须与版本化接线一致：" + ", ".join(montage.names))
        delta = np.diff(timestamps)
        timing_ok = bool(len(delta) and np.isfinite(delta).all() and np.all(delta > 0) and delta.max() <= .02)
        stage = metadata.get("stage")
        if stage == "ssvep":
            settings = (DecoderSettings.from_dict(metadata["decoder_settings"])
                        if metadata.get("decoder_settings") else None)
            contract = (metadata.get("run_id"), montage.id, float(fs), eeg.shape[1], settings)
            if not contract[0]:
                raise ValueError("串行采集缺少 run_id")
            if self._hybrid_decoder is None or self._hybrid_contract != contract:
                self._hybrid_decoder = research_decoder(montage.id, settings)
                self._hybrid_contract = contract
            try:
                result = self._hybrid_decoder.decode(eeg, fs)
            except ValueError as exc:
                if "没有可用 EEG 通道" not in str(exc):
                    raise
                result = IntentResult(timestamp=datetime.now().timestamp(), label="", confidence=0., margin=0.,
                    accepted=False, reason=str(exc), frequency_hz=0., scores={}, sample_rate_hz=fs,
                    sample_count=eeg.shape[1], used_channels_1based=(), signal_quality={"status": "bad"})
            valid = timing_ok and len(result.used_channels_1based) >= 2
            quality = {**result.signal_quality, "montage_id": montage.id, "timing_ok": timing_ok,
                       "p_ssvep_valid": valid and result.signal_quality.get("bn_idle_windows", 0) >= self._hybrid_decoder.min_idle_windows,
                       "selected_channel_names": list(montage.ssvep),
                       "confidence_is_probability": False, "research_only": True}
            if not valid:
                quality["status"] = "bad"
            if metadata.get("true_label") == "空闲" and valid:
                # Predict first, then update. Commands and MI never update the background.
                self._hybrid_decoder.observe_idle_result(result)
            return replace(result, accepted=False, signal_quality=quality, reason="5 通道 BN-FBCCA-B 科研记录")
        if stage not in ("mi", "baseline", "rest_open", "rest_closed"):
            raise ValueError("未知串行采集阶段")
        indices = montage.indices(names, "MI")
        quality = assess_signal_quality(eeg[list(indices)], fs)
        quality.update(montage_id=montage.id, timing_ok=timing_ok, selected_channel_names=list(montage.mi),
                       mi_model_status="offline_validation", research_only=True)
        if not timing_ok:
            quality["status"] = "bad"
        return IntentResult(timestamp=datetime.now().timestamp(), label="", confidence=0., margin=0.,
            accepted=False, reason="MI 原始数据及前序 SSVEP 条件已记录；分类模型离线验证",
            frequency_hz=0., scores={}, sample_rate_hz=fs, sample_count=eeg.shape[1],
            used_channels_1based=tuple(i+1 for i, ok in zip(indices, quality["usable_mask"]) if ok), signal_quality=quality)

    def _reset_buffer(self) -> None:
        self._buffer = None
        self._timestamps = np.asarray([], dtype=np.float64)
        self._active_capture_generation = -1
        self._start_time = ""


__all__ = ["LabeledTrialRecorder", "TrialCaptureRequest"]
