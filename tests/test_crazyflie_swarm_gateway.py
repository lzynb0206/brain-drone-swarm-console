from __future__ import annotations

import threading
import time
import unittest

from src.adapters.real.crazyflie_swarm_gateway import (
    CflibBackend,
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

    def arm_all(self) -> bool:
        self.calls.append(("arm",))
        return True

    def disarm_all(self) -> None:
        self.calls.append(("disarm",))

    def takeoff_all(self, height_m, duration_s) -> None:
        self.calls.append(("takeoff", height_m, duration_s))

    def land_all(self, duration_s) -> None:
        self.calls.append(("land", duration_s))

    def move_all_relative(self, dx, dy, dz, duration_s) -> None:
        self.calls.append(("move", dx, dy, dz, duration_s))

    def hover_all(self, duration_s) -> None:
        self.calls.append(("hover", duration_s))

    def stop_motion_all(self) -> None:
        self.calls.append(("stop_motion",))

    def stop_motors_all(self) -> None:
        self.calls.append(("motor_stop",))

    def start_manual_control(self) -> None:
        self.calls.append(("manual_start",))

    def set_manual_input(self, vertical, forward, lateral) -> None:
        self.calls.append(("manual_input", vertical, forward, lateral))

    def move_one_relative(self, uri, dx, dy, dz, duration_s) -> None:
        self.calls.append(("move_one", uri, dx, dy, dz, duration_s))

    def go_to_positions(self, stages, duration_s, cancel_check=None) -> None:
        self.calls.append(("positions", stages, duration_s))
        if cancel_check is not None:
            cancel_check()
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

    def hover_one_by_one(
        self, height_m, height_offsets_m, hover_s, duration_s, cancel_check=None,
    ) -> None:
        self.calls.append(("hover_one_by_one", height_m, height_offsets_m, hover_s, duration_s))
        if cancel_check is not None:
            cancel_check()

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
        self.assertIn(("manual_start",), backend.calls)
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        with self.assertRaisesRegex(RuntimeError, "无定位单机模式"):
            gateway.send_command(SwarmCommand("formation", parameters={"name": "line"}))

    def test_bare_virtual_sticks_are_clamped_and_low_latency(self) -> None:
        backend = FakeBackend()
        config = CrazyflieSwarmConfig(
            uris=("radio://test/01",), control_enabled=True,
            bounds=None, require_lighthouse=False,
        )
        gateway = CrazyflieSwarmGateway(config, backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        gateway.arm()
        gateway.set_manual_input(2.0, -0.4, 0.25)
        self.assertIn(("manual_input", 1.0, -0.4, 0.25), backend.calls)
        self.assertTrue(gateway.status().airborne)

    def test_takeoff_and_relative_movement_are_queued(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "takeoff")
        gateway.send_command(SwarmCommand("forward", parameters={"distance_m": 0.2}))
        move = self.wait_for_call(backend, "move")
        self.assertEqual((0.2, 0.0, 0.0), move[1:4])

    def test_takeoff_enters_hover_and_reports_airborne(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "hover")
        deadline = time.time() + 1
        while not gateway.status().airborne and time.time() < deadline:
            time.sleep(0.005)
        self.assertTrue(gateway.status().airborne)
        self.assertIn("保持目标位置", gateway.status().message)

    def test_motor_stop_is_recoverable_and_requires_rearming(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "hover")
        gateway.stop_motors()
        self.wait_for_call(backend, "motor_stop")
        deadline = time.time() + 1
        while "电机已停止" not in gateway.status().message and time.time() < deadline:
            time.sleep(0.005)
        status = gateway.status()
        self.assertFalse(status.airborne)
        self.assertFalse(status.armed)
        self.assertFalse(status.emergency)
        gateway.arm()
        self.assertGreaterEqual(sum(call[0] == "arm" for call in backend.calls), 2)

    def test_motor_stop_cancels_inflight_takeoff_before_rearm(self) -> None:
        class BlockingTakeoffBackend(FakeBackend):
            def __init__(self) -> None:
                super().__init__()
                self.takeoff_started = threading.Event()
                self.release_takeoff = threading.Event()

            def takeoff_all(self, height_m, duration_s) -> None:
                self.calls.append(("takeoff", height_m, duration_s))
                self.takeoff_started.set()
                self.release_takeoff.wait(1.0)

        backend = BlockingTakeoffBackend()
        config = CrazyflieSwarmConfig(
            uris=("radio://test/01",), control_enabled=True,
            bounds=None, require_lighthouse=False,
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
        self.assertTrue(backend.takeoff_started.wait(1))
        self.assertTrue(gateway.status().airborne)

        gateway.stop_motors()
        self.wait_for_call(backend, "motor_stop")
        with self.assertRaisesRegex(RuntimeError, "尚未完成"):
            gateway.arm()

        backend.release_takeoff.set()
        deadline = time.time() + 1
        while "电机已停止" not in gateway.status().message and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(any(call[0] == "hover" for call in backend.calls))
        gateway.arm()

    def test_stop_motion_uses_high_priority_bare_control(self) -> None:
        backend = FakeBackend()
        config = CrazyflieSwarmConfig(
            uris=("radio://test/01",), control_enabled=True,
            bounds=None, require_lighthouse=False,
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
        self.wait_for_call(backend, "hover")
        deadline = time.time() + 1
        while not gateway.status().airborne and time.time() < deadline:
            time.sleep(0.005)
        gateway.stop_motion()
        self.assertIn(("stop_motion",), backend.calls)

    def test_positioned_stop_motion_bypasses_command_queue(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("takeoff"))
        self.wait_for_call(backend, "hover")
        gateway.stop_motion()
        self.assertIn(("stop_motion",), backend.calls)

    def test_connect_cannot_clear_emergency_lock(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.emergency_stop()
        self.wait_for_call(backend, "emergency")
        gateway.disconnect()
        with self.assertRaisesRegex(RuntimeError, "急停锁"):
            gateway.connect()
        gateway.reset_after_emergency_reboot()
        gateway.connect()
        self.assertTrue(backend.connected.wait(1))

    def test_emergency_stop_bypasses_normal_queue_and_locks(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.emergency_stop()
        self.wait_for_call(backend, "emergency")
        with self.assertRaisesRegex(RuntimeError, "急停"):
            gateway.send_command(SwarmCommand("forward"))
        with self.assertRaisesRegex(RuntimeError, "断开"):
            gateway.reset_after_emergency_reboot()
        gateway.disconnect()
        gateway.reset_after_emergency_reboot()
        self.assertFalse(gateway.status().emergency)

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

    def test_cflib_028_emergency_uses_localization_fallback(self) -> None:
        calls: list[str] = []

        class Recorder:
            def send_emergency_stop(self) -> None:
                calls.append("loc-emergency")

        class Commander:
            def send_stop_setpoint(self) -> None:
                calls.append("stop")

        class Cf:
            supervisor = None
            loc = Recorder()
            commander = Commander()

        class Scf:
            cf = Cf()

        CflibBackend._emergency_single(Scf())
        self.assertEqual(["loc-emergency"], calls)

    def test_bare_backend_streams_althold_and_returns_direction_to_neutral(self) -> None:
        class Commander:
            def __init__(self) -> None:
                self.setpoints: list[tuple[float, float, float, int]] = []
                self.stopped = False

            def send_setpoint(self, roll, pitch, yawrate, thrust) -> None:
                self.setpoints.append((roll, pitch, yawrate, thrust))

            def send_stop_setpoint(self) -> None:
                self.stopped = True

        class Param:
            def __init__(self) -> None:
                self.toc = type("Toc", (), {"toc": {"flightmode": {"althold": object()}}})()
                self.values: list[tuple[str, str]] = []

            def set_value(self, name, value) -> None:
                self.values.append((name, value))

        class Platform:
            def __init__(self) -> None:
                self.requests: list[bool] = []

            def send_arming_request(self, armed) -> None:
                self.requests.append(bool(armed))

        class HighLevelCommander:
            def stop(self) -> None:
                return None

        class Cf:
            def __init__(self) -> None:
                self.commander = Commander()
                self.param = Param()
                self.platform = Platform()
                self.high_level_commander = HighLevelCommander()

        class Scf:
            def __init__(self) -> None:
                self.cf = Cf()

        class Swarm:
            def __init__(self, scf) -> None:
                self._cfs = {"radio://test/01": scf}

            def parallel_safe(self, callback, args_dict=None) -> None:
                if args_dict is None:
                    for scf in self._cfs.values():
                        callback(scf)
                else:
                    for uri, args in args_dict.items():
                        callback(self._cfs[uri], *args)

        config = CrazyflieSwarmConfig(
            uris=("radio://test/01",), require_lighthouse=False,
            bare_move_pulse_s=0.08, bare_setpoint_rate_hz=100.0,
        )
        backend = CflibBackend(config)
        scf = Scf()
        backend._swarm = Swarm(scf)

        self.assertIsNone(backend.arm_all())
        backend._start_bare_control()
        backend._set_bare_motion(0.0, 0.0, 0.15)
        time.sleep(0.04)
        self.assertIn(("flightmode.althold", "1"), scf.cf.param.values)
        self.assertTrue(any(item[3] > 32767 for item in scf.cf.commander.setpoints))

        backend.set_manual_input(0.5, 1.0, -0.5)
        roll, pitch, yawrate, thrust = backend._bare_setpoint
        self.assertEqual(-2.0, roll)
        self.assertEqual(-4.0, pitch)
        self.assertEqual(0.0, yawrate)
        self.assertGreater(thrust, 32767)

        backend.move_all_relative(0.08, 0.0, 0.0, 0.08)
        self.assertTrue(any(item[1] < 0 for item in scf.cf.commander.setpoints))
        self.assertEqual((0.0, 0.0, 0.0, 32767), backend._bare_setpoint)

        backend.stop_motors_all()
        self.assertTrue(scf.cf.commander.stopped)
        self.assertEqual(False, scf.cf.platform.requests[-1])
        self.assertIn(("flightmode.althold", "0"), scf.cf.param.values)


if __name__ == "__main__":
    unittest.main()
