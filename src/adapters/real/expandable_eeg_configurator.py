"""Vendor configuration-port protocol for ExpandableEEG.

The supplied ``ExpandableEEG_Config.exe`` is a PyInstaller application.  Its
wire protocol is small and platform independent, so keeping it here lets
macOS/Linux users inspect and configure a board once its Type-C data port is
visible as a serial device.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable


_DEVICE_REPLY = re.compile(
    r"(?:exp)?eeg-(?P<mode>\d)-(?P<channels>\d+)"
    r"(?:-'(?P<network>[^']+)'-(?P<ip>\d+\.\d+\.\d+\.\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExpandableEEGDeviceInfo:
    """Configuration reported by the board's Type-C serial endpoint."""

    mode: int
    channel_count: int
    network_name: str = ""
    ip_address: str = ""

    @property
    def mode_name(self) -> str:
        return {1: "WiFi AP", 2: "WiFi Client", 3: "Bluetooth"}.get(
            self.mode, f"Unknown({self.mode})",
        )

    @classmethod
    def parse(cls, payload: str | bytes) -> "ExpandableEEGDeviceInfo":
        text = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else payload
        match = _DEVICE_REPLY.search(str(text).strip())
        if match is None:
            raise ValueError(f"无法识别ExpandableEEG配置回复：{text!r}")
        return cls(
            mode=int(match.group("mode")),
            channel_count=int(match.group("channels")),
            network_name=str(match.group("network") or ""),
            ip_address=str(match.group("ip") or ""),
        )


class ExpandableEEGConfigurator:
    """Probe or change the vendor communication mode through Type-C serial."""

    BAUD_RATE = 115_200

    def __init__(
        self,
        *,
        serial_factory: Callable[..., Any] | None = None,
        settle_seconds: float = 0.2,
        response_timeout_seconds: float = 2.0,
    ) -> None:
        self._serial_factory = serial_factory
        self.settle_seconds = max(0.0, float(settle_seconds))
        self.response_timeout_seconds = max(0.1, float(response_timeout_seconds))

    def probe(self, port: str) -> ExpandableEEGDeviceInfo:
        serial_link = self._open(port)
        try:
            return self._probe_open_link(serial_link)
        finally:
            serial_link.close()

    def configure_bluetooth(
        self, port: str, name: str = "ExpandableEEGBT",
    ) -> ExpandableEEGDeviceInfo:
        name = self._validate_field(name, "蓝牙名称")
        return self._configure(port, f"3|{name}\n".encode("utf-8"))

    def configure_wifi_ap(
        self, port: str, ssid: str, password: str,
    ) -> ExpandableEEGDeviceInfo:
        return self._configure_wifi(port, 1, ssid, password)

    def configure_wifi_client(
        self, port: str, ssid: str, password: str,
    ) -> ExpandableEEGDeviceInfo:
        return self._configure_wifi(port, 2, ssid, password)

    def _configure_wifi(
        self, port: str, mode: int, ssid: str, password: str,
    ) -> ExpandableEEGDeviceInfo:
        ssid = self._validate_field(ssid, "Wi-Fi名称")
        password = self._validate_field(password, "Wi-Fi密码")
        return self._configure(
            port, f"{mode}|{ssid}|{password}\n".encode("utf-8"),
        )

    def _configure(self, port: str, command: bytes) -> ExpandableEEGDeviceInfo:
        serial_link = self._open(port)
        try:
            current = self._probe_open_link(serial_link)
            written = int(serial_link.write(command))
            if written != len(command):
                raise IOError(f"配置命令写入不完整：{written}/{len(command)}")
            flush = getattr(serial_link, "flush", None)
            if callable(flush):
                flush()
            return current
        finally:
            serial_link.close()

    def _open(self, port: str) -> Any:
        port = str(port).strip()
        if not port:
            raise ValueError("配置串口不能为空")
        serial_factory = self._serial_factory
        if serial_factory is None:
            try:
                from serial import Serial
            except ImportError as exc:
                raise RuntimeError("未安装pyserial；请执行 pip install pyserial") from exc
            serial_factory = Serial
        try:
            serial_link = serial_factory(
                port=port,
                baudrate=self.BAUD_RATE,
                timeout=0.1,
                write_timeout=1.0,
                exclusive=True if port.startswith("/dev/") else None,
            )
        except Exception as exc:
            raise ConnectionError(f"无法打开配置串口 {port}：{exc}") from exc
        if self.settle_seconds:
            time.sleep(self.settle_seconds)
        reset = getattr(serial_link, "reset_input_buffer", None)
        if callable(reset):
            reset()
        return serial_link

    def _probe_open_link(self, serial_link: Any) -> ExpandableEEGDeviceInfo:
        command = b"connect\n"
        written = int(serial_link.write(command))
        if written != len(command):
            raise IOError(f"探测命令写入不完整：{written}/{len(command)}")
        flush = getattr(serial_link, "flush", None)
        if callable(flush):
            flush()
        deadline = time.monotonic() + self.response_timeout_seconds
        response = bytearray()
        while time.monotonic() < deadline:
            waiting = int(getattr(serial_link, "in_waiting", 0) or 0)
            chunk = serial_link.read(max(1, min(4096, waiting)))
            if chunk:
                response.extend(chunk)
                try:
                    return ExpandableEEGDeviceInfo.parse(bytes(response))
                except ValueError:
                    pass
            else:
                time.sleep(0.01)
        if not response:
            raise TimeoutError(
                "配置串口没有回复connect命令；请确认Type-C线支持数据传输，"
                "并且所选的是USB配置端口而非蓝牙SPP端口"
            )
        raise ValueError(f"配置串口回复格式不匹配：{bytes(response)!r}")

    @staticmethod
    def _validate_field(value: str, label: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError(f"{label}不能为空")
        if "|" in value or "\n" in value or "\r" in value:
            raise ValueError(f"{label}不能包含竖线或换行符")
        return value


__all__ = ["ExpandableEEGConfigurator", "ExpandableEEGDeviceInfo"]
