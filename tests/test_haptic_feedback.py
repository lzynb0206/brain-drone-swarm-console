"""Protocol/device safety regressions, adapted from the upstream haptic console."""
import asyncio
import unittest
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from src.integrations.haptics.protocol import Lines, normalize, parse
from src.integrations.haptics.service import Console


def status(hand, source, token, version="4", motor="0"):
    return (f"STATUS motor={motor} protocol={version} ble=1 drv=OK addr=0x5a sda=3 scl=4 "
            f"en=1 mode=READY effect=1 rtp=0 drvstatus=0x0 hand={hand} id={token} src={source}\r\n")


class Fake:
    def __init__(self, hand, receive=None, mode="BLE", version="4"):
        self.hand, self.receive, self.mode, self.version = hand, receive, mode, version
        self.connected = False
        self.fail = self.silent = False
        self.writes = []
        self.data = bytearray()
        self.motor = "0"

    async def open(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def write(self, data):
        token, command = data.decode().strip().split(" ", 1)
        token = token[1:]
        self.writes.append(command)
        if self.silent:
            return
        if command == "status":
            line = status(self.hand, self.mode, token, self.version, self.motor)
        else:
            if command == "ping":
                body = "PONG"
            elif command == "i2cscan":
                body = "I2CSCAN 0x5A=ACK 0x5B=NO"
            elif command == "help":
                body = "HELP on off status i2cscan effect 0-123 rtp 0-127 ping help"
            elif self.fail and command.startswith("rtp "):
                body = "ERR BUSY"
            elif command in {"off", "rtp 0"}:
                self.motor, body = "0", "OK OFF"
            else:
                self.motor, body = "1", "OK " + command.upper()
            line = f"{body} hand={self.hand} id={token} src={self.mode}\n"
        if self.receive:
            for i in range(0, len(line), 20):
                self.receive(line[i:i + 20].encode())
        else:
            self.data.extend(line.encode())

    def read(self):
        data = bytes(self.data)
        self.data.clear()
        return data


class ParsingTests(unittest.TestCase):
    def test_partial_and_multiple_lines(self):
        lines = Lines()
        self.assertEqual(list(lines.feed(b"PON")), [])
        self.assertEqual(list(lines.feed(b"G\r\nOK OFF\n")), ["PONG", "OK OFF"])

    def test_overflow_discards_through_newline(self):
        lines = Lines(4)
        self.assertEqual(list(lines.feed(b"12345")), [None])
        self.assertEqual(list(lines.feed(b"junk\nPONG\n")), ["PONG"])

    def test_protocol_and_validation(self):
        reply = parse(status("L", "BLE", "00A3").strip())
        self.assertEqual(reply.fields["motor"], "0")
        with self.assertRaises(ValueError):
            parse("STATUS motor=0 hand=L src=BLE")
        with self.assertRaises(ValueError):
            parse("ERR hand=L src=BLE id=0002")
        for bad in ("effect -1", "rtp 128", "effect ab", "on\noff", "effect 124"):
            with self.assertRaises(ValueError):
                normalize(bad)
        self.assertEqual(normalize("vibe 14"), "effect 14")


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.fakes = {}
        self.console = Console(lambda **e: self.events.append(e), self.ble, self.usb)
        self.console.devices = {"LEFT": "L", "RIGHT": "R", "LEGACY": "legacy"}
        self.console.usb_ports = {"COM13", "/dev/cu.usbmodem-test", "/dev/ttyACM0"}

    def ble(self, device, receive):
        hand = device if device != "legacy" else "L"
        self.fakes[device] = Fake(hand, receive, version="3" if device == "legacy" else "4")
        return self.fakes[device]

    def usb(self, port):
        self.fakes[port] = Fake("R", mode="USB")
        return self.fakes[port]

    async def asyncTearDown(self):
        await self.console.dispatch({"action": "shutdown"})

    async def connect(self, hand, mode, endpoint):
        return (await self.console.dispatch(dict(action="connect", hand=hand, mode=mode, endpoint=endpoint)))[0]

    async def test_two_ble_sessions_independent(self):
        left, right = await asyncio.gather(self.connect("L", "BLE", "LEFT"),
                                           self.connect("R", "BLE", "RIGHT"))
        self.assertTrue(left["ok"] and right["ok"])
        self.assertTrue((await self.console.dispatch(dict(action="send", hand="R", command="rtp 70")))[0]["ok"])
        self.assertIn("rtp 70", self.fakes["R"].writes)
        self.assertNotIn("rtp 70", self.fakes["L"].writes)
        await self.console.dispatch(dict(action="disconnect", hand="R"))
        self.assertEqual(self.fakes["R"].writes[-1], "off")
        self.assertTrue(self.console.sessions["L"].ready)

    async def test_effect_scan_help_and_zero_rtp(self):
        self.assertTrue((await self.connect("L", "BLE", "LEFT"))["ok"])
        session = self.console.sessions["L"]
        self.assertEqual((await session.send("vibe 14"))[0], "OK EFFECT 14")
        self.assertEqual((await session.send("scan"))[1]["0x5A"], "ACK")
        self.assertTrue((await session.send("h"))[0].startswith("HELP"))
        self.assertEqual((await session.send("rtp 0"))[0], "OK OFF")
        self.assertTrue(any(e.get("event") == "i2c" for e in self.events))

    async def test_wrong_hand_and_old_version_rejected_without_stop(self):
        for endpoint, key in (("RIGHT", "R"), ("LEGACY", "legacy")):
            result = await self.connect("L", "BLE", endpoint)
            self.assertFalse(result["ok"])
            self.assertIn("预期", result["message"])
            self.assertNotIn("off", self.fakes[key].writes)
            self.assertFalse(self.fakes[key].connected)

    async def test_duplicate_and_mixed_transport(self):
        self.assertTrue((await self.connect("L", "BLE", "LEFT"))["ok"])
        self.assertFalse((await self.connect("R", "BLE", "LEFT"))["ok"])
        self.assertTrue((await self.connect("R", "USB", "COM13"))["ok"])
        self.assertTrue(any(e.get("rx") == "USB" and e.get("src") == "USB" for e in self.events))

    async def test_usb_uses_enumerated_ports_on_all_platforms(self):
        for port in ("COM13", "/dev/cu.usbmodem-test", "/dev/ttyACM0"):
            self.assertTrue((await self.connect("R", "USB", port))["ok"])
            await self.console.dispatch(dict(action="disconnect", hand="R"))
        self.assertFalse((await self.connect("R", "USB", "/tmp/not-a-device"))["ok"])

    async def test_group_partial_failure_preserves_other_hand(self):
        await self.connect("L", "BLE", "LEFT")
        await self.connect("R", "BLE", "RIGHT")
        self.fakes["R"].fail = True
        results = await self.console.dispatch(dict(action="dual_start", strengths={"L": 60, "R": 90}))
        self.assertEqual([r["ok"] for r in results], [True, False])
        self.assertEqual(self.fakes["L"].motor, "1")
        results = await self.console.dispatch(dict(action="dual_stop"))
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(self.fakes["L"].motor, "0")

    async def test_drop_affects_only_one_hand(self):
        await self.connect("L", "BLE", "LEFT")
        await self.connect("R", "BLE", "RIGHT")
        self.fakes["L"].connected = False
        await asyncio.sleep(0.15)
        self.assertFalse(self.console.sessions["L"].ready)
        self.assertTrue(self.console.sessions["R"].ready)
        results = await self.console.dispatch(dict(action="dual_stop"))
        self.assertEqual([r["ok"] for r in results], [False, True])

    async def test_wrong_source_id_and_malformed_frame_cannot_confirm(self):
        await self.connect("L", "BLE", "LEFT")
        session = self.console.sessions["L"]
        self.fakes["L"].silent = True
        request = asyncio.create_task(session.exchange("rtp 42", timeout=0.4))
        await asyncio.sleep(0)
        token = next(key for key, value in session.pending.items() if value.command == "rtp 42")
        for line in (f"OK RTP 42 hand=L id={token} src=USB",
                     f"OK RTP 42 hand=L id=FFFF src=BLE",
                     f"OK RTP 42 hand=L hand=L id={token} src=BLE",
                     f"OK RTP 43 hand=L id={token} src=BLE"):
            session.receive((line + "\n").encode(), session.lines)
        self.assertFalse(request.done())
        session.receive(f"OK RTP 42 hand=L id={token} src=BLE\n".encode(), session.lines)
        self.assertEqual((await request)[0], "OK RTP 42")
        self.fakes["L"].silent = False

    async def test_missing_control_ack_disconnects_only_affected_hand(self):
        await self.connect("L", "BLE", "LEFT")
        await self.connect("R", "BLE", "RIGHT")
        self.fakes["L"].silent = True
        result = await self.console.dispatch(dict(action="send", hand="L", command="rtp 60"))
        self.assertFalse(result[0]["ok"])
        self.assertIn("off", self.fakes["L"].writes)
        self.assertFalse(self.console.sessions["L"].ready)
        self.assertTrue(self.console.sessions["R"].ready)

    async def test_silent_connection_times_out(self):
        await self.connect("L", "BLE", "LEFT")
        self.fakes["L"].silent = True
        self.console.sessions["L"].last_rx -= 5
        await asyncio.sleep(0.15)
        self.assertFalse(self.console.sessions["L"].ready)


class HapticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sidebar_is_separate_and_target_changes_do_not_send(self):
        from src.views.main_view import MainView
        view = MainView()
        try:
            view._role_stack.setCurrentWidget(view.soldier_widget)
            view.resize(1280, 800)
            view.show()
            view.soldier_widget.sidebar.buttons["haptic"].click()
            self.app.processEvents()
            shell = view.soldier_widget
            self.assertEqual(shell.active_section, "haptic")
            self.assertTrue(shell.haptic_panel.isVisible())
            self.assertFalse(shell.vision_panel.isVisible())
            self.assertEqual(shell._regions, [])
            self.assertEqual(view.haptic.process.state(), QProcess.NotRunning)
            card = shell.haptic_panel.cards["L"]
            with patch.object(view.haptic, "send") as send:
                card.slider.setValue(72)
                send.assert_not_called()
                view.haptic._handle({"event": "state", "hand": "L", "state": "ready", "mode": "USB"})
                view.haptic._handle({"event": "status", "hand": "L", "fields": {"motor": "0", "drv": "OK", "rtp": "0"}})
                card.start.click()
                send.assert_called_once_with("L", "rtp 72")
                self.assertEqual(card.motor.text(), "已停止")  # An outgoing command is not a device acknowledgement.
            view.soldier_widget.sidebar.buttons["vision"].click()
            self.app.processEvents()
            self.assertTrue(shell.vision_panel.isVisible())
            self.assertFalse(shell.haptic_panel.isVisible())
            with patch.object(view.haptic, "shutdown") as shutdown:
                view.deactivate()
                shutdown.assert_called_once()
        finally:
            view.close()

    def test_stop_remains_available_while_start_ack_is_pending(self):
        from src.controllers.haptic_controller import HapticController
        controller = HapticController()
        controller.hands["L"].state = "ready"
        with patch.object(controller.process, "state", return_value=QProcess.Running), patch.object(controller, "_write") as write:
            controller.send("L", "rtp 70")
            controller.send("L", "off")
            self.assertEqual([call.args[0]["command"] for call in write.call_args_list], ["rtp 70", "off"])
            controller._handle({"id": 1, "results": [{"hand": "L", "ok": True, "message": "OK RTP 70"}]})
            self.assertTrue(controller.hands["L"].pending)
            controller._handle({"id": 2, "results": [{"hand": "L", "ok": True, "message": "OK OFF"}]})
            self.assertFalse(controller.hands["L"].pending)

    def test_backend_json_bridge_closes_on_eof(self):
        result = subprocess.run([sys.executable, "-u", "-m", "src.workers.haptic_process"],
                                input='{"id":1,"action":"send","hand":"L","command":"off"}\n',
                                cwd=Path(__file__).resolve().parents[1], text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(messages[0], {"event": "ready"})
        self.assertTrue(any(m.get("state") == "offline" for m in messages))

    def test_backend_stops_when_gui_heartbeat_disappears(self):
        with subprocess.Popen([sys.executable, "-u", "-m", "src.workers.haptic_process"],
                              cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                self.fail("Backend survived loss of GUI heartbeat")
            messages = [json.loads(line) for line in process.stdout.read().splitlines()]
            self.assertTrue(any(m.get("event") == "error" and "心跳" in m["message"] for m in messages))
            self.assertEqual(process.returncode, 0, process.stderr.read())
