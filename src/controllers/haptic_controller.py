"""Qt state and process lifecycle for manual dual-hand haptic control."""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from src.integrations.haptics.protocol import normalize


@dataclass
class HandState:
    state: str = "offline"
    mode: str = "USB"
    endpoint: str = ""
    message: str = "未连接"
    fields: dict = field(default_factory=dict)
    devices: list = field(default_factory=list)
    devices_mode: str = "USB"
    pending: str = ""


class HapticController(QObject):
    changed = Signal()
    log_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hands = {hand: HandState() for hand in ("L", "R")}
        self.logs = deque(maxlen=200)
        self.message = "选择设备后连接"
        self.process = QProcess(self)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self._heartbeat)
        self._buffer = bytearray()
        self._stderr = ""
        self._counter = 0
        self._pending = {}
        self._queue = []
        self._stopping = False

    def _log(self, hand, message):
        label = {"L": "左手", "R": "右手"}.get(hand, "系统")
        line = f"{datetime.now():%H:%M:%S}  {label}  {message}"
        self.logs.append(line)
        self.log_received.emit(line)

    def _notice(self, hand, message):
        if hand in self.hands:
            self.hands[hand].message = message
        self.message = message
        self._log(hand, message)
        self.changed.emit()

    def scan(self, hand, mode):
        self._request("scan", hand, mode=mode)

    def connect_device(self, hand, mode, endpoint):
        if not endpoint:
            self._notice(hand, "请先刷新并选择设备")
            return
        self._request("connect", hand, mode=mode, endpoint=endpoint)

    def disconnect_device(self, hand):
        self._request("disconnect", hand)

    def send(self, hand, command):
        try:
            command = normalize(command)
        except ValueError as exc:
            self._notice(hand, str(exc))
            return
        self._request("send", hand, command=command)

    def start_both(self, left, right):
        self._request("dual_start", strengths={"L": left, "R": right})

    def stop_both(self):
        self._request("dual_stop")

    def _request(self, action, hand=None, **params):
        if self._stopping:
            return
        targets = [hand] if hand in self.hands else list(self.hands)
        if hand is not None and hand not in self.hands:
            return
        stop = action == "dual_stop" or (action == "send" and params.get("command") == "off")
        if not stop and any(self.hands[h].pending for h in targets):
            return
        if action in {"send", "dual_start"} and any(self.hands[h].state != "ready" for h in targets):
            self._notice(hand, "请先连接并完成设备校验")
            return
        if params.get("mode") == "BLE" and importlib.util.find_spec("bleak") is None:
            self._notice(hand, "BLE 需要安装 requirements-haptics.txt 中的依赖")
            return
        self._counter += 1
        request = dict(id=self._counter, action=action, **params)
        if hand is not None:
            request["hand"] = hand
        self._pending[self._counter] = (request, targets, time.monotonic())
        for h in targets:
            self.hands[h].pending = action
        if self.process.state() == QProcess.NotRunning:
            self._buffer.clear()
            self._stderr = ""
            self._queue.append(request)
            self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
            self.process.start(sys.executable, ["-u", "-m", "src.workers.haptic_process"])
        elif self.process.state() == QProcess.Starting:
            self._queue.append(request)
        else:
            self._write(request)
        self.changed.emit()

    def _write(self, request):
        payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        if self.process.write(payload) == -1:
            self._notice(None, "无法向触觉进程发送请求")
            self.process.kill()

    def _started(self):
        if self._stopping:
            return
        for request in self._queue:
            self._write(request)
        self._queue.clear()
        self.timer.start()

    def _heartbeat(self):
        self._write({"id": 0, "action": "heartbeat"})
        if any(time.monotonic() - started > 35 for _, _, started in self._pending.values()):
            self._notice(None, "触觉请求超时，正在关闭连接")
            self.shutdown()

    def _read_error(self):
        self._stderr = (self._stderr + bytes(self.process.readAllStandardError()).decode("utf-8", "replace"))[-2000:]

    def _read_output(self):
        self._buffer.extend(bytes(self.process.readAllStandardOutput()))
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("Invalid event")
                self._handle(data)
            except (ValueError, TypeError, KeyError, AttributeError):
                self._notice(None, "触觉进程数据无效，正在关闭连接")
                self.process.kill()
                return
        if len(self._buffer) > 65536:
            self._notice(None, "触觉进程输出超限")
            self.process.kill()

    def _handle(self, data):
        if "id" in data:
            request, targets, _ = self._pending.pop(data["id"], ({}, [], 0))
            for h in targets:
                self.hands[h].pending = next((r["action"] for r, hs, _ in self._pending.values() if h in hs), "")
            if data.get("error"):
                self._notice(request.get("hand"), data["error"])
            for result in data.get("results", []):
                self._notice(result["hand"], ("" if result["ok"] else "失败：") + result["message"])
        else:
            event, hand = data.get("event"), data.get("hand")
            state = self.hands.get(hand)
            if event == "error":
                self._notice(None, data["message"])
            elif event == "state" and state:
                state.state = data["state"]
                state.mode = data.get("mode", state.mode)
                state.endpoint = data.get("endpoint", "")
                state.message = data.get("detail", {"connecting": "连接中…", "verifying": "校验设备…", "ready": "设备已连接", "closing": "停止并断开中…", "offline": "未连接"}[state.state])
                if state.state != "ready":
                    state.fields.clear()
            elif event == "devices" and state:
                state.devices, state.devices_mode = data["rows"], data["mode"]
            elif event == "status" and state:
                state.fields = data["fields"]
            elif event == "stopped" and state:
                state.fields = {**state.fields, "motor": "0", "mode": "STOP", "rtp": "0"}
            elif event == "log":
                channel = data.get("tx") or data.get("rx") or ""
                direction = "TX" if data.get("tx") else "RX"
                self._log(hand, f"{direction} {channel}  {data['text']}")
            elif event == "i2c" and state:
                fields = data["fields"]
                state.message = f"I²C：0x5A {fields['0x5A']} / 0x5B {fields['0x5B']}"
        self.changed.emit()

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self._clear()
            self._notice(None, "触觉进程无法启动：" + self.process.errorString())

    def _clear(self):
        self.timer.stop()
        self._queue.clear()
        self._pending.clear()
        for state in self.hands.values():
            state.state, state.pending, state.endpoint = "offline", "", ""
            state.fields.clear()
            state.devices.clear()
            state.message = "已断开"

    def _finished(self, code, _status):
        expected = self._stopping
        self._clear()
        if not expected:
            self._notice(None, "触觉进程已退出，设备状态未知；请重新连接" + (f"：{self._stderr[-200:]}" if code else ""))
        self.changed.emit()

    def shutdown(self):
        if self.process.state() == QProcess.NotRunning:
            return
        self._stopping = True
        self.timer.stop()
        # Do not send queued connects after logout races with process startup.
        self._queue.clear()
        if self.process.state() == QProcess.Starting:
            self.process.waitForStarted(1500)
        if self.process.state() == QProcess.Running:
            self._counter += 1
            self._write({"id": self._counter, "action": "shutdown"})
            if not self.process.waitForFinished(7000):
                self.process.kill()
                self.process.waitForFinished(1000)
                self._notice(None, "触觉进程强制退出，停止未确认")
        self._clear()
        self._stopping = False
        self.changed.emit()
