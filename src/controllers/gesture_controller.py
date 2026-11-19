"""Own the camera subprocess; no camera, torch or network I/O on the GUI thread."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtGui import QImage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS = PROJECT_ROOT / "models" / "dvs128_snn_best_v4.pth"


class GestureController(QObject):
    changed = Signal()
    frame_received = Signal(QImage)
    events_received = Signal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._check_freshness)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.process.kill)
        self.message = "摄像头未连接"
        self.result = None
        self.running = False
        self.kind = "camera"
        self._buffer = bytearray()
        self._stderr = ""
        self._last_frame = self._started_at = 0.0
        self._stopping = self._failed = False

    def start(self, kind, source, weights=str(DEFAULT_WEIGHTS), threshold=12, rotation=0):
        if self.running:
            return
        try:
            if kind not in {"camera", "mjpeg", "video"}:
                raise ValueError("请选择有效输入类型")
            if kind == "camera" and (not str(source).isdigit() or int(source) > 99):
                raise ValueError("摄像头编号应为 0–99")
            if kind == "mjpeg":
                url = urlsplit(source)
                if url.scheme not in {"http", "https"} or not url.hostname:
                    raise ValueError("请输入完整的 http(s)://摄像头地址/stream")
            if kind == "video" and not Path(source).is_file():
                raise ValueError("请选择存在的视频文件")
            if not Path(weights).is_file():
                raise ValueError("找不到 SNN 权重，请选择 dvs128_snn_best_v4.pth")
            if not 2 <= float(threshold) <= 60 or rotation not in {0, 90, 180, 270}:
                raise ValueError("帧差阈值应为 2–60，旋转角度应为 0/90/180/270")
            missing = [name for name in ("torch", "cv2") if importlib.util.find_spec(name) is None]
            if missing:
                raise ValueError("缺少摄像头依赖，请安装 requirements-vision.txt：" + ", ".join(missing))
        except (ValueError, TypeError) as exc:
            self.message = str(exc)
            self.changed.emit()
            return
        self._buffer.clear()
        self._stderr = ""
        self.result = None
        self._stopping = self._failed = False
        self._last_frame = 0
        self._started_at = time.monotonic()
        self.running = True
        self.kind = kind
        self.message = "正在启动摄像头与手势模型…"
        self.process.setWorkingDirectory(str(PROJECT_ROOT))
        self.process.start(sys.executable, ["-u", "-m", "src.workers.gesture_process",
                           "--kind", kind, "--source", str(source), "--weights", str(weights),
                           "--threshold", str(threshold), "--rotate", str(rotation)])
        self.timer.start()
        self.changed.emit()

    def stop(self):
        if not self.running:
            return
        self._stopping = True
        self.result = None
        self.message = "正在关闭摄像头…"
        self.process.terminate()
        self.kill_timer.start(1500)
        self.changed.emit()

    def shutdown(self):
        self.stop()
        if self.process.state() != QProcess.NotRunning and not self.process.waitForFinished(1800):
            self.process.kill()
            self.process.waitForFinished(1000)
        self.kill_timer.stop()

    def _read_error(self):
        self._stderr = (self._stderr + bytes(self.process.readAllStandardError()).decode("utf-8", "replace"))[-2000:]

    def _read_output(self):
        self._buffer.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            try:
                self._handle_message(json.loads(line))
            except (ValueError, TypeError, KeyError):
                self._fail("手势进程返回了无效数据")
                return
        if len(self._buffer) > 4 * 1024 * 1024:
            self._fail("手势进程输出超过限制")

    @staticmethod
    def _image(encoded):
        image = QImage.fromData(base64.b64decode(encoded, validate=True), "JPEG")
        if image.isNull():
            raise ValueError("无效图像")
        return image

    def _handle_message(self, data):
        if self._stopping or self._failed:
            return
        kind = data["kind"]
        if kind == "frame":
            self.frame_received.emit(self._image(data["image"]))
            self._last_frame = time.monotonic()
        elif kind == "result":
            self.result = data
            self.events_received.emit(self._image(data["events"]))
            self.message = "视频回放 · 仅识别展示" if self.kind == "video" else "摄像头与手势识别运行中"
        elif kind in {"status", "ended"}:
            self.message = data["message"]
            if kind == "ended":
                self.result = None
        elif kind == "error":
            self._fail(data["message"])
        self.changed.emit()

    def _fail(self, message):
        self._failed = True
        self.result = None
        self.message = message
        if self.running:
            self.process.terminate()
            self.kill_timer.start(1500)
        self.changed.emit()

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self.running = False
            self.timer.stop()
            self._fail("无法启动摄像头进程：" + self.process.errorString())

    def _finished(self, code, _status):
        self.timer.stop()
        self.kill_timer.stop()
        self.running = False
        self.result = None
        if self._stopping:
            self.message = "摄像头已关闭"
        elif not self._failed and code:
            self.message = "摄像头进程异常退出：" + (self._stderr[-400:] or str(code))
        self.changed.emit()

    def _check_freshness(self):
        now = time.monotonic()
        if self._stopping or self._failed:
            return
        if self._last_frame and now - self._last_frame > 4:
            self._fail("超过 4 秒没有新画面，摄像头已停止，请重新连接")
        elif not self._last_frame and now - self._started_at > 20:
            self._fail("摄像头启动超时，请检查权限、地址与连接")
        self.changed.emit()

    def fresh_result(self):
        if (not self.running or self.result is None or not self._last_frame
                or time.monotonic() - self._last_frame > 2
                or time.monotonic() - self.result["captured_at"] > 4):
            return None
        return self.result
