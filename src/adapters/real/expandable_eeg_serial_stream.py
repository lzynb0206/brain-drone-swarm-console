"""Native transport for the vendor ExpandableEEG Bluetooth firmware.

The device exposes OpenBCI/Cyton-compatible 33-byte packets.  Windows can use
its outgoing ESP32SPP COM port directly.  Some recent macOS releases create a
``/dev/cu.*`` node without establishing its underlying RFCOMM connection, so
this adapter automatically uses a small native IOBluetooth bridge for that
specific device and keeps the packet/EEG path identical on both platforms.
"""

from __future__ import annotations

import glob
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from src.adapters.real.brainflow_cyton_eeg_stream import SerialDeviceDescriptor
from src.bci.config import SSVEP_CHANNEL_NAMES
from src.domain import EEGFrame


@dataclass(frozen=True)
class ExpandableEEGPacket:
    """One validated 33-byte OpenBCI binary packet."""

    sample_index: int
    eeg_counts: tuple[int, ...]
    eeg_uv: tuple[float, ...]
    aux_counts: tuple[int, ...]
    packet_type: int


class ExpandableEEGPacketParser:
    """Incrementally synchronize and decode Cyton-compatible packets."""

    PACKET_SIZE = 33
    START_BYTE = 0xA0
    STOP_NIBBLE = 0xC0
    ADS1299_VREF_VOLTS = 4.5
    ADS1299_GAIN = 24.0
    EEG_UV_PER_COUNT = (
        ADS1299_VREF_VOLTS / ((1 << 23) - 1) / ADS1299_GAIN * 1_000_000.0
    )

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.valid_packets = 0
        self.invalid_packets = 0
        self.discarded_bytes = 0

    def reset(self) -> None:
        self._buffer.clear()
        self.valid_packets = 0
        self.invalid_packets = 0
        self.discarded_bytes = 0

    def feed(self, payload: bytes | bytearray | memoryview) -> list[ExpandableEEGPacket]:
        if payload:
            self._buffer.extend(payload)
        packets: list[ExpandableEEGPacket] = []
        while self._buffer:
            try:
                start = self._buffer.index(self.START_BYTE)
            except ValueError:
                self.discarded_bytes += len(self._buffer)
                self._buffer.clear()
                break
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < self.PACKET_SIZE:
                break
            if self._buffer[self.PACKET_SIZE - 1] & 0xF0 != self.STOP_NIBBLE:
                self.invalid_packets += 1
                self.discarded_bytes += 1
                del self._buffer[0]
                continue
            raw = bytes(self._buffer[: self.PACKET_SIZE])
            del self._buffer[: self.PACKET_SIZE]
            packets.append(self._decode(raw))
            self.valid_packets += 1
        return packets

    @classmethod
    def _decode(cls, raw: bytes) -> ExpandableEEGPacket:
        eeg_counts = tuple(
            cls._signed_int24(raw[offset : offset + 3])
            for offset in range(2, 26, 3)
        )
        aux_counts = tuple(
            int.from_bytes(raw[offset : offset + 2], byteorder="big", signed=True)
            for offset in range(26, 32, 2)
        )
        return ExpandableEEGPacket(
            sample_index=int(raw[1]),
            eeg_counts=eeg_counts,
            eeg_uv=tuple(value * cls.EEG_UV_PER_COUNT for value in eeg_counts),
            aux_counts=aux_counts,
            packet_type=int(raw[32] & 0x0F),
        )

    @staticmethod
    def _signed_int24(payload: bytes) -> int:
        value = int.from_bytes(payload, byteorder="big", signed=False)
        return value - (1 << 24) if value & 0x800000 else value


class ExpandableEEGSerialStream:
    """Read 8-channel ExpandableEEG data without BrainFlow's Cyton handshake."""

    BAUD_RATE = 115_200
    SAMPLE_RATE_HZ = 250.0
    # The packet contains only an 8-bit sample counter, not a hardware clock.
    # Keep counter-based timing stable while it agrees with the host, but
    # re-anchor when transport buffering or packet loss makes it drift behind.
    TIMESTAMP_RESYNC_THRESHOLD_SECONDS = 0.25

    def __init__(
        self,
        serial_port: str = "",
        frame_size: int = 32,
        *,
        serial_factory: Callable[..., Any] | None = None,
        port_lister: Callable[[], list[Any]] | None = None,
        channel_names: tuple[str, ...] = SSVEP_CHANNEL_NAMES,
        settle_seconds: float = 2.0,
        startup_timeout_seconds: float = 5.0,
        reset_delay_seconds: float = 1.5,
        handshake_delay_seconds: float = 1.0,
        configure_delay_seconds: float = 2.0,
        stream_start_delay_seconds: float = 2.0,
        frame_timeout_seconds: float = 1.0,
        reset_fallback: bool = True,
        rfcomm_factory: Callable[..., Any] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self.serial_port = str(serial_port)
        self.frame_size = max(1, int(frame_size))
        self._serial_factory = serial_factory
        self._port_lister = port_lister
        self.channel_names = tuple(str(value) for value in channel_names)
        if len(self.channel_names) != 8 or any(not value.strip() for value in self.channel_names):
            raise ValueError("ExpandableEEG acquisition requires exactly 8 channel names")
        self.settle_seconds = max(0.0, float(settle_seconds))
        self.startup_timeout_seconds = max(0.05, float(startup_timeout_seconds))
        self.reset_delay_seconds = max(0.0, float(reset_delay_seconds))
        self.handshake_delay_seconds = max(0.0, float(handshake_delay_seconds))
        self.configure_delay_seconds = max(0.0, float(configure_delay_seconds))
        self.stream_start_delay_seconds = max(0.0, float(stream_start_delay_seconds))
        self.frame_timeout_seconds = max(0.05, float(frame_timeout_seconds))
        self.reset_fallback = bool(reset_fallback)
        self._rfcomm_factory = rfcomm_factory
        self._wall_clock = wall_clock or time.time
        self.sample_rate_hz = self.SAMPLE_RATE_HZ
        self.source_name = "ExpandableEEG Native · 未选择串口"
        self._serial: Any | None = None
        self._parser = ExpandableEEGPacketParser()
        self._packets: deque[ExpandableEEGPacket] = deque()
        # Timestamp estimates are attached at receipt, not when downstream
        # decoding eventually consumes a frame from the queue.
        self._packet_timestamps: deque[float] = deque()
        self._bytes_received = 0
        self._total_samples = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        self._duplicate_packets = 0
        self._out_of_order_packets = 0
        self._last_sample_index: int | None = None
        self._last_timestamp: float | None = None
        self._timestamp_resync_count = 0
        self._max_timestamp_drift_seconds = 0.0
        self._last_timestamp_drift_seconds = 0.0
        self._last_startup_sequence = ""
        self._transport = "serial"

    @property
    def connected(self) -> bool:
        return self._serial is not None

    def configure_serial_port(self, serial_port: str) -> None:
        if self.connected:
            raise RuntimeError("请先停止采集，再切换串口设备")
        serial_port = str(serial_port).strip()
        if not serial_port:
            raise ValueError("串口不能为空")
        self.serial_port = serial_port
        self.source_name = f"ExpandableEEG Native · {serial_port}"

    def discover_devices(self) -> list[SerialDeviceDescriptor]:
        if self._port_lister is not None:
            ports = list(self._port_lister())
        else:
            try:
                from serial.tools import list_ports

                ports = list(list_ports.comports())
            except ImportError:
                candidates = sorted(set(glob.glob("/dev/cu.*") + glob.glob("/dev/tty.*")))
                if not candidates:
                    raise RuntimeError("缺少 pyserial，无法搜索蓝牙 COM/串口设备")
                return [SerialDeviceDescriptor(port=value) for value in candidates]
        descriptors = [
            item
            if isinstance(item, SerialDeviceDescriptor)
            else SerialDeviceDescriptor(
                port=str(getattr(item, "device", item)),
                description=str(getattr(item, "description", "") or ""),
                hardware_id=str(getattr(item, "hwid", "") or ""),
            )
            for item in ports
        ]
        ignored_fragments = ("bluetooth-incoming-port", "debug-console")
        descriptors = [
            item
            for item in descriptors
            if not any(fragment in item.port.lower() for fragment in ignored_fragments)
        ]
        # macOS exposes both /dev/cu.* and /dev/tty.* for one RFCOMM service.
        # The callout endpoint is the correct one for an application initiating
        # the connection; suppress its dial-in twin to avoid a misleading choice.
        callout_names = {
            item.port.removeprefix("/dev/cu.")
            for item in descriptors
            if item.port.startswith("/dev/cu.")
        }
        descriptors = [
            item
            for item in descriptors
            if not (
                item.port.startswith("/dev/tty.")
                and item.port.removeprefix("/dev/tty.") in callout_names
            )
        ]
        return sorted(descriptors, key=lambda item: item.port.lower())

    def connect(self) -> None:
        if self.connected:
            return
        if not self.serial_port:
            raise RuntimeError("请先刷新设备并选择 ExpandableEEG 蓝牙 COM/串口")
        serial_factory = self._serial_factory
        use_native_rfcomm = (
            serial_factory is None
            and sys.platform == "darwin"
            and "expandableeegbt" in self.serial_port.lower()
        )
        if use_native_rfcomm:
            rfcomm_factory = self._rfcomm_factory
            if rfcomm_factory is None:
                from src.adapters.real.macos_rfcomm_serial import MacOSRFCOMMSerial

                rfcomm_factory = MacOSRFCOMMSerial
            device_name = self.serial_port.rsplit(".", 1)[-1]
            serial_link = rfcomm_factory(
                device_name=device_name,
                channel=1,
                timeout=0.05,
                write_timeout=1.0,
            )
            self._transport = "macos-native-rfcomm"
        else:
            if serial_factory is None:
                try:
                    from serial import Serial
                except ImportError as exc:
                    raise RuntimeError("未安装 pyserial；请执行 pip install pyserial") from exc
                serial_factory = Serial
            serial_link = serial_factory(
                port=self.serial_port,
                baudrate=self.BAUD_RATE,
                timeout=0.05,
                write_timeout=1.0,
                # macOS allows multiple processes to open the same RFCOMM node.
                # Without an exclusive lock OpenBCI GUI and this application can
                # silently split one EEG byte stream between them.
                exclusive=True if self.serial_port.startswith("/dev/") else None,
            )
            self._transport = "serial"
        self._serial = serial_link
        self._reset_health()
        self.source_name = f"ExpandableEEG Native · {self.serial_port}"
        try:
            if self.settle_seconds:
                time.sleep(self.settle_seconds)
            self._reset_input_buffer()
            self._start_standard_stream()
            self._last_startup_sequence = "v,d,c,b"
            if self._wait_for_first_packet(self.startup_timeout_seconds):
                return
            if self.reset_fallback:
                self._write_command(b"s")
                time.sleep(0.15)
                self._write_command(b"~")
                if self.reset_delay_seconds:
                    time.sleep(self.reset_delay_seconds)
                self._reset_input_buffer()
                self._start_standard_stream()
                self._last_startup_sequence = "v,d,c,b -> s,~,v,d,c,b"
                if self._wait_for_first_packet(self.startup_timeout_seconds):
                    return
            if self._bytes_received == 0:
                raise TimeoutError(
                    "蓝牙SPP/RFCOMM链路已打开，但设备未返回任何字节。"
                    "这通常表示采集主板没有向蓝牙模块转发数据，或设备并非BT采集模式。"
                    "请用Type-C数据口运行 scripts/expandable_eeg_config.py --probe；"
                    "如果系统看不到新的USB串口，请更换确认支持数据传输的Type-C线"
                )
            raise TimeoutError(
                f"串口收到 {self._bytes_received} 字节，但没有有效的0xA0/0xCx数据包；"
                "请核对设备固件和通道数"
            )
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        serial_link, self._serial = self._serial, None
        if serial_link is None:
            return
        try:
            serial_link.write(b"s")
            flush = getattr(serial_link, "flush", None)
            if callable(flush):
                flush()
        except Exception:
            pass
        try:
            serial_link.close()
        except Exception:
            pass

    def flush(self) -> int:
        discarded = len(self._packets)
        self._packets.clear()
        self._packet_timestamps.clear()
        self._parser = ExpandableEEGPacketParser()
        serial_link = self._serial
        if serial_link is not None:
            try:
                waiting = int(getattr(serial_link, "in_waiting", 0) or 0)
                if waiting > 0:
                    serial_link.read(waiting)
                    discarded += waiting // ExpandableEEGPacketParser.PACKET_SIZE
            except Exception:
                pass
            try:
                reset = getattr(serial_link, "reset_input_buffer", None)
                if callable(reset):
                    reset()
            except Exception:
                pass
        return discarded

    def read_frame(self) -> EEGFrame:
        if not self.connected:
            raise RuntimeError("ExpandableEEG设备尚未连接")
        deadline = time.monotonic() + self.frame_timeout_seconds
        while len(self._packets) < self.frame_size and time.monotonic() < deadline:
            if not self._read_serial_once():
                time.sleep(0.005)
        if not self._packets:
            raise TimeoutError("等待 ExpandableEEG 数据")
        packets = [
            self._packets.popleft()
            for _ in range(min(self.frame_size, len(self._packets)))
        ]
        arrival_estimates = [self._packet_timestamps.popleft() for _ in packets]
        samples = [[] for _ in range(8)]
        timestamps: list[float] = []
        # Receiving 96 packets and consuming them as three 32-sample frames
        # must NOT assign three different "now" anchors to that same batch.
        # Host arrival is still only an estimate: Bluetooth latency is unknown
        # without a hardware clock/trigger. Never claim hardware synchrony.
        for packet, host_timestamp in zip(packets, arrival_estimates):
            timestamp = self._accept_sample_index(
                packet.sample_index,
                host_timestamp,
            )
            if timestamp is None:
                continue
            for channel, value in enumerate(packet.eeg_uv):
                samples[channel].append(float(value))
            timestamps.append(timestamp)
        if not timestamps:
            raise TimeoutError("ExpandableEEG仅返回重复数据包")
        self._total_samples += len(timestamps)
        return EEGFrame(
            timestamp=timestamps[-1],
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
            samples=tuple(tuple(channel) for channel in samples),
            sample_timestamps=tuple(timestamps),
            channel_units=("uV",) * 8,
            source_id=f"expandable-eeg-serial:{self.serial_port}",
        )

    def playback_snapshot(self) -> dict[str, object]:
        return {
            "backend": "hardware",
            "driver": "expandable-native",
            "connected": self.connected,
            "source": self.source_name,
            "serial_port": self.serial_port,
            "transport": self._transport,
            "sample_rate_hz": self.sample_rate_hz,
        }

    def health_snapshot(self) -> dict[str, object]:
        return {
            "backend": "hardware",
            "driver": "expandable-native",
            "connected": self.connected,
            "stream_name": self.source_name,
            "serial_port": self.serial_port,
            "transport": self._transport,
            "channel_count": 8,
            "nominal_sample_rate_hz": self.sample_rate_hz,
            "actual_sample_rate_hz": self.sample_rate_hz,
            "total_samples": self._total_samples,
            "bytes_received": self._bytes_received,
            "valid_packets": self._parser.valid_packets,
            "invalid_packets": self._parser.invalid_packets,
            "resync_discarded_bytes": self._parser.discarded_bytes,
            "gap_count": self._gap_count,
            "max_gap_seconds": self._max_gap_seconds,
            "duplicate_packets": self._duplicate_packets,
            "non_monotonic_timestamps": self._out_of_order_packets,
            "last_sample_index": self._last_sample_index,
            "timestamp_resync_count": self._timestamp_resync_count,
            "last_timestamp_drift_seconds": self._last_timestamp_drift_seconds,
            "max_timestamp_drift_seconds": self._max_timestamp_drift_seconds,
            "sample_timestamp_source": "host_receipt_estimate_with_sample_counter",
            "hardware_synchronized": False,
            "queued_sample_count": len(self._packets),
            "startup_sequence": self._last_startup_sequence,
            "channel_names": self.channel_names,
            "channel_units": ("uV",) * 8,
            "reference": "SRB2/A1",
            "bias": "BIAS/A2",
        }

    def _write_command(self, command: bytes) -> None:
        serial_link = self._serial
        if serial_link is None:
            raise RuntimeError("串口未连接")
        written = int(serial_link.write(command))
        if written != len(command):
            raise IOError(f"串口命令写入不完整：{written}/{len(command)}")
        flush = getattr(serial_link, "flush", None)
        if callable(flush):
            flush()

    def _start_standard_stream(self) -> None:
        """Replay the command sequence captured from the proven Windows run."""
        self._write_command(b"v")
        if self.handshake_delay_seconds:
            time.sleep(self.handshake_delay_seconds)
        self._reset_input_buffer()
        self._write_command(b"d")
        if self.configure_delay_seconds:
            time.sleep(self.configure_delay_seconds)
        self._write_command(b"c")
        if self.stream_start_delay_seconds:
            time.sleep(self.stream_start_delay_seconds)
        self._reset_input_buffer()
        self._write_command(b"b")

    def _reset_input_buffer(self) -> None:
        serial_link = self._serial
        if serial_link is None:
            return
        reset = getattr(serial_link, "reset_input_buffer", None)
        if callable(reset):
            reset()
        self._parser = ExpandableEEGPacketParser()
        self._packets.clear()
        self._packet_timestamps.clear()

    def _wait_for_first_packet(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._read_serial_once()
            if self._packets:
                return True
            time.sleep(0.005)
        return False

    def _read_serial_once(self) -> bool:
        serial_link = self._serial
        if serial_link is None:
            return False
        try:
            waiting = int(getattr(serial_link, "in_waiting", 0) or 0)
            payload = serial_link.read(min(4096, max(1, waiting)))
        except Exception as exc:
            raise ConnectionError(f"ExpandableEEG蓝牙串口读取失败：{exc}") from exc
        if not payload:
            return False
        received_at = float(self._wall_clock())
        self._bytes_received += len(payload)
        packets = self._parser.feed(payload)
        if packets:
            # A capped serial read may leave newer samples in the transport
            # buffer. Account for their nominal duration in the host estimate.
            unread_bytes = max(0, waiting - len(payload))
            unread_packets = (unread_bytes + self._parser.PACKET_SIZE - 1) // self._parser.PACKET_SIZE
            latest = received_at - unread_packets / self.sample_rate_hz
            estimates = [latest] * len(packets)
            for index in range(len(packets) - 2, -1, -1):
                delta = (packets[index + 1].sample_index - packets[index].sample_index) & 0xFF
                # Match the existing forward counter policy. Duplicates share
                # a timestamp and are discarded by _accept_sample_index.
                if delta > 127:
                    delta = 1
                estimates[index] = estimates[index + 1] - delta / self.sample_rate_hz
            self._packets.extend(packets)
            self._packet_timestamps.extend(estimates)
        return True

    def _accept_sample_index(self, sample_index: int, suggested_timestamp: float) -> float | None:
        if self._last_sample_index is None or self._last_timestamp is None:
            self._last_sample_index = sample_index
            self._last_timestamp = suggested_timestamp
            self._last_timestamp_drift_seconds = 0.0
            return suggested_timestamp
        delta = (sample_index - self._last_sample_index) & 0xFF
        if delta == 0:
            self._duplicate_packets += 1
            return None
        if delta > 127:
            self._out_of_order_packets += 1
            delta = 1
        elif delta > 1:
            missing = delta - 1
            self._gap_count += missing
            self._max_gap_seconds = max(
                self._max_gap_seconds, missing / self.sample_rate_hz,
            )
        predicted_timestamp = self._last_timestamp + delta / self.sample_rate_hz
        drift = predicted_timestamp - suggested_timestamp
        self._last_timestamp_drift_seconds = float(drift)
        self._max_timestamp_drift_seconds = max(
            self._max_timestamp_drift_seconds,
            abs(float(drift)),
        )

        # A received serial sample cannot genuinely be acquired after the host
        # time at which it was read.  Clamp any future estimate immediately.
        # When the counter estimate is substantially behind (for example after
        # a blocked transport), re-anchor as well instead of carrying a stale
        # absolute time into the experiment marker domain.
        should_reanchor = (
            predicted_timestamp > suggested_timestamp
            or suggested_timestamp - predicted_timestamp
            > self.TIMESTAMP_RESYNC_THRESHOLD_SECONDS
        )
        timestamp = suggested_timestamp if should_reanchor else predicted_timestamp
        if (
            should_reanchor
            and abs(drift) > 1.0 / self.sample_rate_hz
        ):
            self._timestamp_resync_count += 1
        self._last_sample_index = sample_index
        self._last_timestamp = timestamp
        return timestamp

    def _reset_health(self) -> None:
        self._parser = ExpandableEEGPacketParser()
        self._packets.clear()
        self._packet_timestamps.clear()
        self._bytes_received = 0
        self._total_samples = 0
        self._gap_count = 0
        self._max_gap_seconds = 0.0
        self._duplicate_packets = 0
        self._out_of_order_packets = 0
        self._last_sample_index = None
        self._last_timestamp = None
        self._timestamp_resync_count = 0
        self._max_timestamp_drift_seconds = 0.0
        self._last_timestamp_drift_seconds = 0.0
        self._last_startup_sequence = ""


__all__ = [
    "ExpandableEEGPacket",
    "ExpandableEEGPacketParser",
    "ExpandableEEGSerialStream",
]
