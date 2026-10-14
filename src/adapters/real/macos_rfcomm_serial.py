"""Serial-like transport backed by macOS IOBluetooth RFCOMM.

Recent macOS releases can leave a paired classic-SPP device represented by an
``/dev/cu.*`` pseudo serial node that opens successfully but never creates an
ACL/RFCOMM connection.  The small Objective-C helper bundled beside this
module opens the discovered RFCOMM service channel directly and exposes raw
bytes through pipes, keeping the rest of the EEG packet parser platform
independent.
"""

from __future__ import annotations

import array
import fcntl
import hashlib
import os
import platform
import select
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path


class MacOSRFCOMMSerial:
    """Minimal pyserial-compatible facade over a native RFCOMM subprocess."""

    def __init__(
        self,
        *,
        device_name: str = "ExpandableEEGBT",
        channel: int = 1,
        timeout: float = 0.05,
        write_timeout: float = 1.0,
        ready_timeout: float = 12.0,
        bridge_binary: Path | str | None = None,
    ) -> None:
        if platform.system() != "Darwin":
            raise RuntimeError("MacOSRFCOMMSerial 只能在 macOS 上使用")
        self.device_name = str(device_name).strip() or "ExpandableEEGBT"
        self.channel = max(1, min(30, int(channel)))
        self.timeout = max(0.0, float(timeout))
        self.write_timeout = max(0.05, float(write_timeout))
        self.closed = False
        self._write_lock = threading.Lock()
        self._stderr_log = bytearray()
        binary = Path(bridge_binary) if bridge_binary else self._build_bridge()
        self._process = subprocess.Popen(
            [str(binary), self.device_name, str(self.channel)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        try:
            self._await_ready(max(0.1, float(ready_timeout)))
        except Exception:
            self.close()
            raise

    @property
    def in_waiting(self) -> int:
        if self.closed or self._process.stdout is None:
            return 0
        available = array.array("i", [0])
        try:
            fcntl.ioctl(self._process.stdout.fileno(), termios.FIONREAD, available, True)
        except (OSError, ValueError):
            return 0
        return max(0, int(available[0]))

    @property
    def diagnostics(self) -> str:
        return self._stderr_log.decode("utf-8", errors="replace").strip()

    def write(self, payload: bytes | bytearray | memoryview) -> int:
        if self.closed or self._process.stdin is None:
            raise OSError("RFCOMM bridge 已关闭")
        data = bytes(payload)
        if not data:
            return 0
        deadline = time.monotonic() + self.write_timeout
        written = 0
        with self._write_lock:
            fd = self._process.stdin.fileno()
            while written < len(data):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("RFCOMM bridge 写入超时")
                _, writable, _ = select.select([], [fd], [], remaining)
                if not writable:
                    raise TimeoutError("RFCOMM bridge 写入超时")
                count = os.write(fd, data[written:])
                if count <= 0:
                    raise OSError("RFCOMM bridge 写入失败")
                written += count
        return written

    def read(self, count: int = 1) -> bytes:
        if self.closed or self._process.stdout is None or count <= 0:
            return b""
        fd = self._process.stdout.fileno()
        readable, _, _ = select.select([fd], [], [], self.timeout)
        if not readable:
            if self._process.poll() is not None:
                self._collect_stderr()
                raise ConnectionError(
                    "macOS RFCOMM bridge 已退出" +
                    (f"：{self.diagnostics}" if self.diagnostics else "")
                )
            return b""
        return os.read(fd, max(1, int(count)))

    def reset_input_buffer(self) -> None:
        if self.closed or self._process.stdout is None:
            return
        fd = self._process.stdout.fileno()
        while select.select([fd], [], [], 0)[0]:
            if not os.read(fd, 65_536):
                break

    def flush(self) -> None:
        return None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        process = getattr(self, "_process", None)
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        self._collect_stderr()
        for handle in (process.stdout, process.stderr):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass

    def _await_ready(self, timeout: float) -> None:
        if self._process.stderr is None:
            raise RuntimeError("RFCOMM bridge 没有诊断通道")
        fd = self._process.stderr.fileno()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._collect_stderr()
                raise ConnectionError(
                    "无法建立 macOS RFCOMM 连接" +
                    (f"：{self.diagnostics}" if self.diagnostics else "")
                )
            readable, _, _ = select.select([fd], [], [], min(0.1, deadline - time.monotonic()))
            if not readable:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                continue
            self._stderr_log.extend(chunk)
            if any(line.startswith(b"READY ") for line in self._stderr_log.splitlines()):
                return
            if any(line.startswith(b"ERROR ") for line in self._stderr_log.splitlines()):
                raise ConnectionError(f"无法建立 macOS RFCOMM 连接：{self.diagnostics}")
        self._collect_stderr()
        raise TimeoutError(
            "等待 macOS RFCOMM 连接超时" +
            (f"：{self.diagnostics}" if self.diagnostics else "")
        )

    def _collect_stderr(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.stderr is None:
            return
        fd = process.stderr.fileno()
        try:
            while select.select([fd], [], [], 0)[0]:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                self._stderr_log.extend(chunk)
        except (OSError, ValueError):
            pass

    @classmethod
    def _build_bridge(cls) -> Path:
        source = Path(__file__).with_name("native") / "macos_rfcomm_bridge.m"
        if not source.is_file():
            raise RuntimeError(f"缺少 macOS RFCOMM bridge 源码：{source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        cache_dir = Path(tempfile.gettempdir()) / "brain-drone-swarm-console-rfcomm"
        cache_dir.mkdir(parents=True, exist_ok=True)
        binary = cache_dir / f"macos_rfcomm_bridge-{platform.machine()}-{digest}"
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary
        temporary = binary.with_suffix(f".tmp-{os.getpid()}")
        command = [
            "xcrun", "clang", "-fobjc-arc",
            "-framework", "Foundation",
            "-framework", "IOBluetooth",
            str(source), "-o", str(temporary),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"无法编译 macOS RFCOMM bridge：{exc}") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                "无法编译 macOS RFCOMM bridge：" +
                (completed.stderr.strip() or completed.stdout.strip())
            )
        temporary.chmod(0o700)
        os.replace(temporary, binary)
        return binary


__all__ = ["MacOSRFCOMMSerial"]
