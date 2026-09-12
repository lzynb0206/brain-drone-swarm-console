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

    def move_one_relative(self, uri, dx, dy, dz, duration_s) -> None:
        self.calls.append(("move_one", uri, dx, dy, dz, duration_s))

    def go_to_positions(self, stages, duration_s) -> None:
        self.calls.append(("positions", stages, duration_s))
        if self.telemetry is not None and stages:
            for uri, position in stages[-1].items():
                self.telemetry(uri, time.time(), {
                    "stateEstimate.x": position.x,
                    "stateEstimate.y": position.y,
                    "stateEstimate.z": position.z,
                    "stateEstimate.vx": 0.0,
                    "stateEstimate.vy": 0.0,
                    "stateEstimate.vz": 0.0,
                    "pm.vbat": 4.0,
                    "lighthouse.status": 2.0,
                    "lighthouse.bsActive": 1.0,
                })

    def set_led_all(self, red, green, blue, duration_s) -> None:
        self.calls.append(("led", red, green, blue, duration_s))

    def hover_one_by_one(self, height_m, height_offsets_m, hover_s, duration_s) -> None:
        self.calls.append(("hover_one_by_one", height_m, height_offsets_m, hover_s, duration_s))

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

    def test_requires_explicit_control_flag(self) -> None:
        gateway, _ = self.make_gateway(control=False)
        with self.assertRaisesRegex(RuntimeError, "只读遥测"):
            gateway.arm()

    def test_requires_geofence_before_arming(self) -> None:
        gateway, _ = self.make_gateway(bounds=False)
        with self.assertRaisesRegex(RuntimeError, "GEOFENCE"):
            gateway.arm()

    def test_bare_single_mode_skips_lighthouse_and_geofence(self) -> None:
        backend = FakeBackend()
        config = CrazyflieSwarmConfig(
            uris=("radio://test/01",),
            control_enabled=True,
            bounds=None,
            require_lighthouse=False,
        )
        gateway = CrazyflieSwarmGateway(config, backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1))
        self.addCleanup(gateway.disconnect)

        deadline = time.time() + 1
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)

        self.assertEqual("bare", gateway.status().backend)
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        with self.assertRaisesRegex(RuntimeError, "无定位单机模式"):
            gateway.send_command(SwarmCommand("formation", parameters={"name": "line"}))

    def test_takeoff_and_relative_movement_are_queued(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        gateway.send_command(SwarmCommand("forward", parameters={"distance_m": 0.2}))
        move = self.wait_for_call(backend, "move")
        self.assertEqual((0.2, 0.0, 0.0), move[1:4])

    def test_emergency_stop_bypasses_normal_queue_and_locks(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.emergency_stop()
        self.wait_for_call(backend, "emergency")
        with self.assertRaisesRegex(RuntimeError, "急停"):
            gateway.send_command(SwarmCommand("forward"))

    def test_square_checks_every_waypoint_before_submission(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        with self.assertRaisesRegex(RuntimeError, "围栏"):
            gateway.send_command(SwarmCommand("relative_square", parameters={"distance_m": 0.5}))
        self.assertFalse(any(call[0] == "move" for call in backend.calls))

    def test_one_by_one_hover_is_background_and_disarms_after_completion(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("hover_one_by_one", parameters={"hover_s": 0.5}))
        self.wait_for_call(backend, "hover_one_by_one")
        deadline = time.time() + 1
        while gateway.status().armed and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(gateway.status().armed)

    def test_four_aircraft_absolute_formation_is_staged_and_verified(self) -> None:
        backend = FakeBackend()
        config = CrazyflieSwarmConfig(
            uris=tuple(f"radio://test/0{index}" for index in range(1, 5)),
            control_enabled=True,
            bounds=FlightBounds(-5, 5, -5, 5, 0, 2),
            minimum_spacing_m=0.5,
        )
        gateway = CrazyflieSwarmGateway(config, backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        gateway.send_command(SwarmCommand("formation", parameters={"name": "diamond"}))
        call = self.wait_for_call(backend, "positions")
        self.assertEqual(2, len(call[1]))
        deadline = time.time() + 1
        while not any("飞行指令完成：formation" in item for item in gateway.events()) and time.time() < deadline:
            time.sleep(0.005)
        self.assertTrue(any("飞行指令完成：formation" in item for item in gateway.events()))

    def test_synchronized_hover_test_lands_and_disarms(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("hover_sync_test", parameters={"hover_s": 0.5}))
        self.wait_for_call(backend, "takeoff")
        self.wait_for_call(backend, "hover")
        self.wait_for_call(backend, "land")
        deadline = time.time() + 1
        while gateway.status().armed and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(gateway.status().armed)

    def test_repeated_lighthouse_loss_triggers_safety_stop(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
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
