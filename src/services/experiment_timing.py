"""Shared experiment clock and optional Lab Streaming Layer marker outlet.

EEG samples received from LSL are stamped in ``pylsl.local_clock()`` time.  UI
events historically used Unix time, which makes sample/marker alignment
ambiguous.  This module gives the acquisition path one clock boundary and can
translate legacy Unix timestamps without making pylsl a hard dependency.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ExperimentTimestamp:
    """One event represented in both audit (Unix) and EEG (LSL) domains."""

    lsl_seconds: float
    unix_seconds: float
    clock_source: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "lsl_timestamp": self.lsl_seconds,
            "unix_timestamp": self.unix_seconds,
            "clock_source": self.clock_source,
        }


class ExperimentClock:
    """Translate event timestamps into the same domain as LSL EEG samples.

    When pylsl is unavailable, ``time.monotonic`` provides the same monotonic
    semantics for tests and offline collection.  The selected source is saved
    with every marker/trial so a fallback session cannot be mistaken for a
    hardware-synchronised LSL session.
    """

    UNIX_THRESHOLD_SECONDS = 100_000_000.0

    def __init__(
        self,
        local_clock: Callable[[], float] | None = None,
        unix_clock: Callable[[], float] = time.time,
    ) -> None:
        self._unix_clock = unix_clock
        self._local_clock, self.source = self._resolve_local_clock(local_clock)

    @staticmethod
    def _resolve_local_clock(
        callback: Callable[[], float] | None,
    ) -> tuple[Callable[[], float], str]:
        if callback is not None:
            return callback, "injected_lsl_local_clock"
        try:
            from pylsl import local_clock

            # Some machines can import pylsl but cannot load liblsl until the
            # first function call.  Probe once and fall back cleanly.
            float(local_clock())
            return local_clock, "pylsl.local_clock"
        except (ImportError, OSError, RuntimeError):
            return time.monotonic, "time.monotonic_fallback"

    def now(self) -> ExperimentTimestamp:
        return self.stamp()

    def stamp(
        self,
        timestamp: float | None = None,
        timestamp_domain: str = "auto",
    ) -> ExperimentTimestamp:
        """Return a dual-domain timestamp.

        ``auto`` treats epoch-like values as Unix timestamps and smaller
        monotonic values as LSL-local values.  UI code can therefore keep
        sending its legacy ``time.time()`` values while new protocol code may
        omit the value and receive a native LSL clock reading.
        """

        local_now = float(self._local_clock())
        unix_now = float(self._unix_clock())
        if timestamp is None:
            return ExperimentTimestamp(local_now, unix_now, self.source)

        value = float(timestamp)
        domain = str(timestamp_domain or "auto").strip().lower()
        if domain == "auto":
            domain = "unix" if value >= self.UNIX_THRESHOLD_SECONDS else "lsl"
        if domain in {"unix", "epoch", "wall"}:
            return ExperimentTimestamp(
                local_now + (value - unix_now),
                value,
                self.source,
            )
        if domain in {"lsl", "local", "monotonic"}:
            return ExperimentTimestamp(
                value,
                unix_now + (value - local_now),
                self.source,
            )
        raise ValueError(f"unsupported timestamp domain: {timestamp_domain!r}")

    def to_lsl(self, timestamp: float, timestamp_domain: str = "auto") -> float:
        return self.stamp(timestamp, timestamp_domain).lsl_seconds

    def to_unix(self, timestamp: float, timestamp_domain: str = "auto") -> float:
        return self.stamp(timestamp, timestamp_domain).unix_seconds


class ExperimentMarkerOutlet:
    """Best-effort irregular LSL marker stream.

    Marker publication is deliberately optional.  Local JSONL recording and
    EEG collection continue when pylsl/liblsl is absent; ``last_error`` and
    the returned boolean make that downgrade visible to the caller.
    """

    def __init__(
        self,
        stream_name: str = "brain_drone_experiment_markers",
        source_id: str = "brain-drone-swarm-console-markers",
        outlet_factory: Callable[[Any], Any] | None = None,
        info_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.stream_name = str(stream_name)
        self.source_id = str(source_id)
        self._outlet_factory = outlet_factory
        self._info_factory = info_factory
        self._outlet: Any | None = None
        self._lock = threading.Lock()
        self.last_error = ""

    @property
    def active(self) -> bool:
        with self._lock:
            return self._outlet is not None

    def start(self) -> bool:
        with self._lock:
            if self._outlet is not None:
                return True
            try:
                if self._info_factory is None or self._outlet_factory is None:
                    from pylsl import StreamInfo, StreamOutlet, cf_string

                    info_factory = StreamInfo
                    outlet_factory = StreamOutlet
                    channel_format = cf_string
                else:
                    info_factory = self._info_factory
                    outlet_factory = self._outlet_factory
                    channel_format = "string"
                info = info_factory(
                    self.stream_name,
                    "Markers",
                    1,
                    0.0,
                    channel_format,
                    self.source_id,
                )
                self._outlet = outlet_factory(info)
                self.last_error = ""
                return True
            except (ImportError, OSError, RuntimeError) as exc:
                self.last_error = str(exc)
                self._outlet = None
                return False

    def publish(
        self,
        name: str,
        timestamp: float,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        if not self.start():
            return False
        payload = json.dumps(
            {"name": str(name), "metadata": dict(metadata or {})},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        with self._lock:
            outlet = self._outlet
            if outlet is None:
                return False
            try:
                outlet.push_sample([payload], timestamp=float(timestamp))
                return True
            except (AttributeError, OSError, RuntimeError) as exc:
                self.last_error = str(exc)
                return False

    def stop(self) -> None:
        with self._lock:
            self._outlet = None


__all__ = ["ExperimentClock", "ExperimentMarkerOutlet", "ExperimentTimestamp"]
