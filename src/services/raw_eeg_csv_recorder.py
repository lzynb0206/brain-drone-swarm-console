"""Write live EEG frames to the CSV schema used by the LSL recorder."""

from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from src.domain import EEGFrame


class RawEEGCsvRecorder:
    """Record unmodified channel samples and their LSL timestamps.

    ``start`` arms the recorder.  The file is created on the first frame so
    the header always uses the channel metadata delivered by the selected
    stream instead of guessed defaults.
    """

    def __init__(self, output_dir: Path | str, flush_interval_seconds: float = 1.0) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.flush_interval_seconds = max(0.0, float(flush_interval_seconds))
        self._lock = threading.Lock()
        self._armed = False
        self._handle: TextIO | None = None
        self._writer: csv.writer | None = None
        self._path: Path | None = None
        self._channel_names: tuple[str, ...] = ()
        self._original_channel_names: tuple[str, ...] = ()
        self._channel_units: tuple[str, ...] = ()
        self._sample_rate_hz = 0.0
        self._sample_count = 0
        self._first_timestamp: float | None = None
        self._last_timestamp: float | None = None
        self._last_flush = 0.0
        self._session_id = ""
        self._subject_id = ""
        self._source = ""
        self._started_at = ""

    @property
    def is_recording(self) -> bool:
        return self._armed

    @property
    def path(self) -> Path | None:
        return self._path

    def start(self, session_id: str = "", subject_id: str = "", source: str = "") -> None:
        with self._lock:
            if self._armed:
                raise RuntimeError("CSV recording is already active")
            self._armed = True
            self._session_id = str(session_id)
            self._subject_id = str(subject_id)
            self._source = str(source)
            self._started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
            self._sample_count = 0
            self._first_timestamp = None
            self._last_timestamp = None
            self._channel_names = ()
            self._original_channel_names = ()
            self._channel_units = ()
            self._sample_rate_hz = 0.0
            self._path = None
            self._handle = None
            self._writer = None

    def append(self, frame: EEGFrame) -> int:
        with self._lock:
            if not self._armed:
                return 0
            channel_count = len(frame.samples)
            if channel_count == 0:
                return 0
            sample_count = len(frame.samples[0])
            if any(len(channel) != sample_count for channel in frame.samples):
                raise ValueError("EEG frame channels have different sample counts")
            if sample_count == 0:
                return 0
            if self._writer is None:
                self._open_for_frame(frame)
            if channel_count != len(self._channel_names):
                raise ValueError(
                    f"CSV channel count changed from {len(self._channel_names)} to {channel_count}"
                )
            timestamps = self._timestamps_for_frame(frame, sample_count)
            assert self._writer is not None
            for offset, timestamp in enumerate(timestamps):
                if self._first_timestamp is None:
                    self._first_timestamp = timestamp
                self._last_timestamp = timestamp
                elapsed = timestamp - self._first_timestamp
                row = [frame.samples[channel][offset] for channel in range(channel_count)]
                self._writer.writerow(
                    [self._sample_count, repr(timestamp), repr(elapsed), *row]
                )
                self._sample_count += 1
            if time.monotonic() - self._last_flush >= self.flush_interval_seconds:
                self._flush_locked()
            return sample_count

    def stop(self) -> Path | None:
        with self._lock:
            self._armed = False
            path = self._path
            self._close_locked()
            if path is not None:
                self._write_metadata(path)
            return path

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "recording": self._armed,
                "path": str(self._path) if self._path else "",
                "sample_count": self._sample_count,
                "duration_seconds": max(
                    0.0,
                    (self._last_timestamp or 0.0) - (self._first_timestamp or 0.0),
                ),
                "channel_count": len(self._channel_names),
                "sample_rate_hz": self._sample_rate_hz,
                "started_at": self._started_at,
            }

    def _open_for_frame(self, frame: EEGFrame) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._unique_path()
        self._handle = self._path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.writer(self._handle, lineterminator="\n")
        count = len(frame.samples)
        supplied = tuple(str(name).strip() for name in frame.channel_names)
        self._channel_names = (
            supplied if len(supplied) == count
            else tuple(f"CH{index + 1}" for index in range(count))
        )
        original = tuple(str(name).strip() for name in frame.original_channel_names)
        self._original_channel_names = (
            original if len(original) == count else self._channel_names
        )
        self._sample_rate_hz = float(frame.sample_rate_hz)
        supplied_units = tuple(str(unit).strip() for unit in frame.channel_units)
        self._channel_units = (
            supplied_units if len(supplied_units) == count else ("",) * count
        )
        self._writer.writerow(
            ["sample_index", "lsl_timestamp", "elapsed_seconds", *self._channel_names]
        )
        self._handle.flush()
        self._last_flush = time.monotonic()

    def _timestamps_for_frame(self, frame: EEGFrame, sample_count: int) -> tuple[float, ...]:
        if len(frame.sample_timestamps) == sample_count:
            return tuple(float(value) for value in frame.sample_timestamps)
        sample_rate = max(float(frame.sample_rate_hz), 1.0)
        end = float(frame.timestamp)
        return tuple(end - (sample_count - 1 - index) / sample_rate for index in range(sample_count))

    def _unique_path(self) -> Path:
        stem = f"lsl_raw_{datetime.now():%Y%m%d_%H%M%S}"
        candidate = self.output_dir / f"{stem}.csv"
        suffix = 1
        while candidate.exists():
            candidate = self.output_dir / f"{stem}_{suffix}.csv"
            suffix += 1
        return candidate

    def _flush_locked(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            self._last_flush = time.monotonic()

    def _close_locked(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
        self._handle = None
        self._writer = None

    def _write_metadata(self, path: Path) -> None:
        metadata = {
            "format": "openbci-lsl-raw-csv-v1",
            "csv_file": path.name,
            "started_at": self._started_at,
            "stopped_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "session_id": self._session_id,
            "subject_id": self._subject_id,
            "source": self._source,
            "channel_names": list(self._channel_names),
            "original_channel_names": list(self._original_channel_names),
            "channel_units": list(self._channel_units),
            "sample_rate_hz": self._sample_rate_hz,
            "sample_count": self._sample_count,
        }
        path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
