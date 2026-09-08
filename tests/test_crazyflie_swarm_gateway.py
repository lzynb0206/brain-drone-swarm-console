from __future__ import annotations

import threading
import time
import unittest

from src.adapters.real.crazyflie_swarm_gateway import (
    CrazyflieSwarmConfig,
    CrazyflieSwarmGateway,
    FlightBounds,
)
from src.domain import SwarmCommand


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.connected = threading.Event()
        self.telemetry = None

    def connect(self, uris, telemetry, event) -> None:
        self.calls.append(("connect", uris))
        self.telemetry = telemetry
        for index, uri in enumerate(uris):
            telemetry(uri, time.time(), {
                "stateEstimate.x": index * 0.6,
                "stateEstimate.y": 0.0,
                "stateEstimate.z": 0.0,
                "stateEstimate.vx": 0.0,
                "stateEstimate.vy": 0.0,
                "stateEstimate.vz": 0.0,
                "pm.vbat": 4.0,
                "lighthouse.status": 2.0,
                "lighthouse.bsActive": 1.0,
            })
        self.connected.set()

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def takeoff_all(self, height_m, duration_s) -> None:
        self.calls.append(("takeoff", height_m, duration_s))

    def land_all(self, duration_s) -> None:
        self.calls.append(("land", duration_s))

    def move_all_relative(self, dx, dy, dz, duration_s) -> None:
        self.calls.append(("move", dx, dy, dz, duration_s))

    def hover_all(self, duration_s) -> None:
        self.calls.append(("hover", duration_s))

    def emergency_stop_all(self) -> None:
        self.calls.append(("emergency",))


class CrazyflieGatewayTest(unittest.TestCase):
    def make_gateway(self, *, bounds=True, control=True):
        backend = FakeBackend()
        config = CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"),
            control_enabled=control,
            bounds=FlightBounds(-1, 1, -1, 1, 0, 1) if bounds else None,
        )
        gateway = CrazyflieSwarmGateway(config, backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1))
        deadline = time.time() + 1
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        self.addCleanup(gateway.disconnect)
        return gateway, backend

    def wait_for_call(self, backend: FakeBackend, name: str) -> tuple:
        deadline = time.time() + 1
        while time.time() < deadline:
            for call in backend.calls:
                if call[0] == name:
                    return call
            time.sleep(0.005)
        self.fail(f"backend did not receive {name}")

    def test_requires_explicit_control_flag_and_phrase(self) -> None:
        gateway, _ = self.make_gateway(control=False)
        with self.assertRaisesRegex(RuntimeError, "只读遥测"):
            gateway.arm("启用实机控制")

        gateway, _ = self.make_gateway()
        with self.assertRaisesRegex(RuntimeError, "确认文字"):
            gateway.arm("yes")

    def test_requires_geofence_before_arming(self) -> None:
        gateway, _ = self.make_gateway(bounds=False)
        with self.assertRaisesRegex(RuntimeError, "GEOFENCE"):
            gateway.arm("启用实机控制")

    def test_takeoff_and_relative_movement_are_queued(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm("启用实机控制")
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        gateway.send_command(SwarmCommand("forward", parameters={"distance_m": 0.2}))
        move = self.wait_for_call(backend, "move")
        self.assertEqual((0.2, 0.0, 0.0), move[1:4])

    def test_emergency_stop_bypasses_normal_queue_and_locks(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm("启用实机控制")
        gateway.emergency_stop()
        self.wait_for_call(backend, "emergency")
        with self.assertRaisesRegex(RuntimeError, "急停"):
            gateway.send_command(SwarmCommand("forward"))

    def test_repeated_lighthouse_loss_triggers_safety_stop(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm("启用实机控制")
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        deadline = time.time() + 1
        while not any("飞行指令完成" in item for item in gateway.events()) and time.time() < deadline:
            time.sleep(0.005)
        assert backend.telemetry is not None
        bad = {
            "stateEstimate.x": 0.0, "stateEstimate.y": 0.0, "stateEstimate.z": 0.5,
            "pm.vbat": 4.0, "lighthouse.status": 1.0, "lighthouse.bsActive": 0.0,
        }
        for _ in range(3):
            backend.telemetry("radio://test/01", time.time(), bad)
        self.wait_for_call(backend, "emergency")
        self.assertEqual("emergency", gateway.status().state)


if __name__ == "__main__":
    unittest.main()
