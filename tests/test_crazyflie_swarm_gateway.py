from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace

from src.adapters.real.crazyflie_swarm_gateway import CflibBackend, CrazyflieSwarmConfig, CrazyflieSwarmGateway
from src.domain import SwarmCommand


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.connected = threading.Event()
        self.telemetry = None
        self.uri = ""
        self.uris: tuple[str, ...] = ()

    def connect(self, uris, telemetry, event) -> None:
        self.calls.append(("connect", uris))
        self.telemetry = telemetry
        self.uris = tuple(uris)
        self.uri = uris[0]
        for uri in self.uris:
            self.emit_telemetry(0.4, uri=uri)
        self.connected.set()

    def emit_telemetry(
        self, height_m: float, voltage: float = 4.0, *, uri: str | None = None,
    ) -> None:
        assert self.telemetry is not None
        self.telemetry(uri or self.uri, time.time(), {
            "stateEstimate.x": 0.0, "stateEstimate.y": 0.0, "stateEstimate.z": height_m,
            "stateEstimate.vx": 0.0, "stateEstimate.vy": 0.0, "stateEstimate.vz": 0.0,
            "pm.vbat": voltage, "range.zrange": max(1.0, height_m * 1000.0),
            "motion.squal": 80.0,
        })

    def disconnect(self) -> None: self.calls.append(("disconnect",))
    def arm_all(self) -> bool: self.calls.append(("arm",)); return True
    def arm_one(self, uri) -> bool: self.calls.append(("arm_one", uri)); return True
    def disarm_all(self) -> None: self.calls.append(("disarm",))
    def disarm_one(self, uri) -> None: self.calls.append(("disarm_one", uri))
    def takeoff_all(self, height_m, duration_s) -> None:
        self.calls.append(("takeoff", height_m, duration_s))
        for uri in self.uris:
            self.emit_telemetry(height_m, uri=uri)
    def takeoff_one(self, uri, height_m, duration_s) -> None:
        self.calls.append(("takeoff_one", uri, height_m, duration_s))
        self.emit_telemetry(height_m, uri=uri)
    def land_all(self, duration_s) -> None: self.calls.append(("land", duration_s))
    def land_one(self, uri, duration_s) -> None: self.calls.append(("land_one", uri, duration_s))
    def move_all_relative(self, dx, dy, dz, duration_s) -> None: self.calls.append(("move", dx, dy, dz, duration_s))
    def move_one_relative(self, uri, dx, dy, dz, duration_s) -> None: self.calls.append(("move_one", uri, dx, dy, dz, duration_s))
    def hover_all(self, duration_s) -> None: self.calls.append(("hover", duration_s))
    def hover_one(self, uri, duration_s) -> None: self.calls.append(("hover_one", uri, duration_s))
    def stop_motion_all(self) -> None: self.calls.append(("stop_motion",))
    def stop_motion_one(self, uri) -> None: self.calls.append(("stop_motion_one", uri))
    def stop_motors_all(self) -> None: self.calls.append(("motor_stop",))
    def stop_motors_one(self, uri) -> None: self.calls.append(("motor_stop_one", uri))
    def start_manual_control(self, target_height_m) -> None: self.calls.append(("manual_start", target_height_m))
    def start_manual_control_one(self, uri, target_height_m) -> None:
        self.calls.append(("manual_start_one", uri, target_height_m))
    def set_manual_input(self, vertical, forward, lateral) -> None: self.calls.append(("manual_input", vertical, forward, lateral))
    def set_manual_input_one(self, uri, vertical, forward, lateral) -> None:
        self.calls.append(("manual_input_one", uri, vertical, forward, lateral))
    def set_led_all(self, red, green, blue, duration_s) -> None: self.calls.append(("led", red, green, blue, duration_s))
    def emergency_stop_all(self) -> None: self.calls.append(("emergency",))


class CrazyflieGatewayTest(unittest.TestCase):
    def make_gateway(self, *, control=True):
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01",), control_enabled=control,
        ), backend)
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

    def test_connects_multiple_aircraft_and_keeps_separate_telemetry(self) -> None:
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"),
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)

        self.assertEqual(
            ("radio://test/01", "radio://test/02"),
            backend.calls[0][1],
        )
        self.assertEqual(["CF-01", "CF-02"], [uav.uav_id for uav in gateway.list_uavs()])
        self.assertIn("2 架 Crazyflie 遥测已连接", gateway.status().message)

    def test_multi_aircraft_arm_starts_one_group_control_stream(self) -> None:
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"), control_enabled=True,
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)

        gateway.arm()

        self.assertIn(("arm",), backend.calls)
        self.assertIn(("manual_start", 0.4), backend.calls)
        self.assertTrue(gateway.status().armed)
        self.assertTrue(gateway.status().airborne)

    def test_group_horizontal_stick_waits_until_every_aircraft_has_height(self) -> None:
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"), control_enabled=True,
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        gateway.arm()
        backend.emit_telemetry(0.05, uri="radio://test/02")

        with self.assertRaisesRegex(RuntimeError, "等待起飞高度"):
            gateway.set_manual_input(0.0, 0.4, 0.0)

        gateway.set_manual_input(0.4, 0.0, 0.0)
        self.assertIn(("manual_input", 0.4, 0.0, 0.0), backend.calls)

        # Clearing the minimum Flow height is not enough: every aircraft must
        # first enter the configured 0.4 m take-off target tolerance.
        backend.emit_telemetry(0.25, uri="radio://test/01")
        backend.emit_telemetry(0.25, uri="radio://test/02")
        with self.assertRaisesRegex(RuntimeError, "等待起飞高度"):
            gateway.set_manual_input(0.0, 0.4, 0.0)

        backend.emit_telemetry(0.38, uri="radio://test/01")
        backend.emit_telemetry(0.38, uri="radio://test/02")
        gateway.set_manual_input(0.0, 0.4, 0.0)
        self.assertIn(("manual_input", 0.0, 0.4, 0.0), backend.calls)

    def test_selected_aircraft_can_take_off_move_and_land_independently(self) -> None:
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"), control_enabled=True,
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)

        gateway.send_command(SwarmCommand(
            "takeoff", uav_id="CF-02", parameters={"height_m": 0.4},
        ))
        self.wait_for_call(backend, "takeoff_one")
        deadline = time.time() + 1.0
        while not gateway.list_uavs()[1].airborne and time.time() < deadline:
            time.sleep(0.005)

        first, second = gateway.list_uavs()
        self.assertFalse(first.airborne)
        self.assertTrue(second.airborne)
        self.assertIn(("arm_one", "radio://test/02"), backend.calls)

        gateway.send_command(SwarmCommand(
            "move_relative",
            uav_id="CF-02",
            parameters={"dx_m": 0.2, "dy_m": 0.0, "dz_m": 0.0},
        ))
        move = self.wait_for_call(backend, "move_one")
        self.assertEqual("radio://test/02", move[1])

        gateway.send_command(SwarmCommand("land", uav_id="CF-02"))
        self.wait_for_call(backend, "land_one")
        deadline = time.time() + 1.0
        while gateway.list_uavs()[1].airborne and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(gateway.list_uavs()[1].airborne)

    def test_disconnect_lands_only_airborne_subset_before_closing_links(self) -> None:
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"), control_enabled=True,
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        gateway.send_command(SwarmCommand(
            "takeoff", uav_id="CF-02", parameters={"height_m": 0.4},
        ))
        self.wait_for_call(backend, "takeoff_one")
        deadline = time.time() + 1.0
        while not gateway.list_uavs()[1].airborne and time.time() < deadline:
            time.sleep(0.005)

        gateway.disconnect()

        self.assertIn(("land_one", "radio://test/02", 3.0), backend.calls)
        self.assertNotIn(("land", 3.0), backend.calls)

    def test_group_takeoff_move_and_land_address_all_aircraft(self) -> None:
        backend = FakeBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"), control_enabled=True,
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        self.addCleanup(gateway.disconnect)
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)

        gateway.send_command(SwarmCommand("takeoff", parameters={"height_m": 0.4}))
        self.wait_for_call(backend, "takeoff")
        deadline = time.time() + 1.0
        while not all(item.airborne for item in gateway.list_uavs()) and time.time() < deadline:
            time.sleep(0.005)
        self.assertTrue(all(item.airborne for item in gateway.list_uavs()))

        gateway.send_command(SwarmCommand("forward", parameters={"distance_m": 0.2}))
        self.wait_for_call(backend, "move")
        gateway.send_command(SwarmCommand("land"))
        self.wait_for_call(backend, "land")

    def test_requires_explicit_control_flag(self) -> None:
        gateway, _ = self.make_gateway(control=False)
        with self.assertRaisesRegex(RuntimeError, "只读遥测"):
            gateway.arm()

    def test_arm_starts_flow_hover(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        self.assertIn(("manual_start", 0.4), backend.calls)
        self.assertTrue(gateway.status().airborne)
        self.assertEqual("flowdeck", gateway.status().backend)

    def test_selected_takeoff_height_is_passed_to_flow_hover(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.set_takeoff_height(0.5)
        self.assertEqual(0.5, gateway.takeoff_height_m())

        gateway.arm()

        self.assertIn(("manual_start", 0.5), backend.calls)
        with self.assertRaisesRegex(RuntimeError, "不能修改"):
            gateway.set_takeoff_height(0.6)

    def test_virtual_sticks_are_clamped(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.set_manual_input(2.0, -0.4, 0.25)
        self.assertIn(("manual_input", 1.0, -0.4, 0.25), backend.calls)

    def test_relative_movement_is_queued(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("forward", parameters={"distance_m": 0.2}))
        move = self.wait_for_call(backend, "move")
        self.assertEqual((0.2, 0.0, 0.0), move[1:4])

    def test_mission_takeoff_can_arm_from_connected_ground_state(self) -> None:
        gateway, backend = self.make_gateway()

        gateway.send_command(SwarmCommand("takeoff", parameters={"height_m": 0.5}))

        self.wait_for_call(backend, "takeoff")
        self.assertIn(("arm",), backend.calls)
        self.assertTrue(gateway.status().armed)
        self.assertTrue(gateway.status().airborne)

    def test_takeoff_is_not_completed_before_target_height_telemetry(self) -> None:
        class DelayedTakeoffBackend(FakeBackend):
            def takeoff_all(self, height_m, duration_s) -> None:
                self.calls.append(("takeoff", height_m, duration_s))

        backend = DelayedTakeoffBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01",), control_enabled=True,
        ), backend)
        completions: list[str] = []
        gateway.add_command_listener(
            lambda status, _message, command: completions.append(
                f"{status}:{command.action}"
            )
        )
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        self.addCleanup(gateway.disconnect)

        gateway.send_command(SwarmCommand("takeoff", parameters={"height_m": 0.5}))
        self.wait_for_call(backend, "takeoff")
        time.sleep(0.1)

        self.assertNotIn("COMPLETED:takeoff", completions)
        self.assertFalse(any(call[0] == "hover" for call in backend.calls))

        backend.emit_telemetry(0.5)
        self.wait_for_call(backend, "hover")
        deadline = time.time() + 1.0
        while "COMPLETED:takeoff" not in completions and time.time() < deadline:
            time.sleep(0.005)
        self.assertIn("COMPLETED:takeoff", completions)

    def test_arbitrary_relative_movement_is_queued_for_selected_aircraft(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()

        gateway.send_command(SwarmCommand(
            "move_relative",
            uav_id="CF-01",
            parameters={"dx_m": 0.4, "dy_m": -0.2, "dz_m": 0.1},
        ))

        move = self.wait_for_call(backend, "move_one")
        self.assertEqual("radio://test/01", move[1])
        self.assertEqual((0.4, -0.2, 0.1), move[2:5])

    def test_controlled_landing_can_be_armed_again(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.send_command(SwarmCommand("land"))
        self.wait_for_call(backend, "land")
        deadline = time.time() + 1
        while gateway.status().armed and time.time() < deadline:
            time.sleep(0.005)

        landed = gateway.status()
        self.assertFalse(landed.armed)
        self.assertFalse(landed.airborne)
        self.assertFalse(landed.emergency)
        self.assertIn("可再次解锁", landed.message)

        gateway.arm()
        self.assertEqual(2, sum(1 for call in backend.calls if call[0] == "arm"))
        self.assertEqual(2, sum(1 for call in backend.calls if call[0] == "manual_start"))

    def test_controlled_landing_is_rejected_before_flow_hover_starts(self) -> None:
        gateway, backend = self.make_gateway()

        with self.assertRaisesRegex(RuntimeError, "Flow Hover 尚未启动"):
            gateway.send_command(SwarmCommand("land"))

        self.assertFalse(any(call[0] == "land" for call in backend.calls))

    def test_duplicate_controlled_landing_is_not_queued(self) -> None:
        class SlowLandingBackend(FakeBackend):
            def __init__(self) -> None:
                super().__init__()
                self.landing_started = threading.Event()
                self.release_landing = threading.Event()

            def land_all(self, duration_s) -> None:
                self.calls.append(("land", duration_s))
                self.landing_started.set()
                self.release_landing.wait(1.0)

        backend = SlowLandingBackend()
        gateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig(
            uris=("radio://test/01",), control_enabled=True,
        ), backend)
        gateway.connect()
        self.assertTrue(backend.connected.wait(1.0))
        deadline = time.time() + 1.0
        while not gateway.status().connected and time.time() < deadline:
            time.sleep(0.005)
        self.addCleanup(gateway.disconnect)
        self.addCleanup(backend.release_landing.set)

        gateway.arm()
        gateway.send_command(SwarmCommand("land"))
        self.assertTrue(backend.landing_started.wait(1.0))
        with self.assertRaisesRegex(RuntimeError, "降落已在进行"):
            gateway.send_command(SwarmCommand("land"))
        with self.assertRaisesRegex(RuntimeError, "降落已在进行"):
            gateway.send_command(SwarmCommand("forward"))
        with self.assertRaisesRegex(RuntimeError, "正在受控降落"):
            gateway.set_manual_input(0.0, 0.2, 0.0)
        with self.assertRaisesRegex(RuntimeError, "正在受控降落"):
            gateway.arm()
        backend.release_landing.set()

        deadline = time.time() + 1.0
        while gateway.status().airborne and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(1, sum(1 for call in backend.calls if call[0] == "land"))

    def test_landed_confirmation_does_not_require_can_be_armed(self) -> None:
        class Swarm:
            _cfs = {"radio://test/01": object()}

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        backend._swarm = Swarm()
        backend._telemetry_cache = {"radio://test/01": {"supervisor.info": 0.0}}

        self.assertTrue(backend._wait_for_landed_confirmation())

    def test_low_voltage_only_warns_and_does_not_block_rearm(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.config = replace(gateway.config, battery_low_confirm_s=0.0)
        gateway.arm()

        gateway._on_telemetry("radio://test/01", time.time(), {
            "stateEstimate.x": 0.0, "stateEstimate.y": 0.0, "stateEstimate.z": 0.35,
            "stateEstimate.vx": 0.0, "stateEstimate.vy": 0.0, "stateEstimate.vz": 0.0,
            "pm.vbat": 3.1, "range.zrange": 350.0, "motion.squal": 80.0,
        })

        warning = gateway.status()
        self.assertEqual("battery_low", warning.state)
        self.assertTrue(warning.armed)
        self.assertTrue(warning.airborne)
        self.assertIn("3.10V", warning.message)

        # Manual control remains available and must not erase the warning.
        gateway.set_manual_input(0.0, 0.0, 0.0)
        self.assertEqual("battery_low", gateway.status().state)

        # Most importantly, low voltage never rejects the landing command.
        gateway.send_command(SwarmCommand("land"))
        self.wait_for_call(backend, "land")
        deadline = time.time() + 1.0
        while gateway.status().airborne and time.time() < deadline:
            time.sleep(0.005)

        landed = gateway.status()
        self.assertEqual("connected", landed.state)
        self.assertFalse(landed.armed)
        self.assertFalse(landed.airborne)
        gateway.arm()
        self.assertTrue(gateway.status().armed)

    def test_low_preflight_voltage_does_not_block_arm(self) -> None:
        gateway, backend = self.make_gateway()
        self.assertEqual(3.2, gateway.config.minimum_battery_voltage)
        gateway._on_telemetry("radio://test/01", time.time(), {
            "stateEstimate.x": 0.0, "stateEstimate.y": 0.0, "stateEstimate.z": 0.1,
            "stateEstimate.vx": 0.0, "stateEstimate.vy": 0.0, "stateEstimate.vz": 0.0,
            "pm.vbat": 3.19, "range.zrange": 100.0, "motion.squal": 80.0,
        })
        gateway.arm()
        self.assertIn(("arm",), backend.calls)

    def test_pre_arm_reports_crash_state_separately_from_emergency_lock(self) -> None:
        class Swarm:
            _cfs = {"radio://test/01": object()}

            def parallel_safe(self, callback, args_dict=None) -> None:
                raise AssertionError("crashed aircraft must not receive an arm request")

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        backend._swarm = Swarm()
        backend._telemetry_cache = {"radio://test/01": {"supervisor.info": float(1 << 7)}}

        with self.assertRaisesRegex(RuntimeError, "坠机状态"):
            backend.arm_all()

    def test_arm_request_is_sent_without_can_be_armed_precheck(self) -> None:
        class Commander:
            def send_notify_setpoint_stop(self) -> None: return None

        class Platform:
            def __init__(self) -> None: self.requests = []
            def send_arming_request(self, armed) -> None: self.requests.append(bool(armed))

        class Scf:
            def __init__(self) -> None:
                self.cf = type("Cf", (), {})()
                self.cf.commander = Commander()
                self.cf.platform = Platform()
                self.cf.supervisor = None

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        scf = Scf()

        class Swarm:
            _cfs = {"radio://test/01": scf}

            def parallel_safe(self, callback, args_dict=None) -> None:
                callback(scf)
                # Simulate the firmware confirming the official request. The
                # pre-request value intentionally has can_be_armed == 0.
                backend._telemetry_cache["radio://test/01"]["supervisor.info"] = 1 << 1

        backend._swarm = Swarm()
        backend._supervisor_info_reliable = True
        backend._telemetry_cache = {"radio://test/01": {"supervisor.info": 0.0}}

        self.assertTrue(backend.arm_all())
        self.assertEqual([True], scf.cf.platform.requests)

    def test_legacy_protocol_sends_arm_request_without_waiting_for_unreliable_bit(self) -> None:
        class Commander:
            def send_notify_setpoint_stop(self) -> None: return None

        class Platform:
            def __init__(self) -> None: self.requests = []
            def get_protocol_version(self) -> int: return 6
            def send_arming_request(self, armed) -> None: self.requests.append(bool(armed))

        class Scf:
            def __init__(self) -> None:
                self.cf = type("Cf", (), {})()
                self.cf.commander = Commander()
                self.cf.platform = Platform()

        class Swarm:
            def __init__(self, scf) -> None: self._cfs = {"radio://test/01": scf}
            def parallel_safe(self, callback, args_dict=None) -> None:
                for uri, scf in self._cfs.items():
                    callback(scf, *(args_dict[uri] if args_dict else []))

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        scf = Scf()
        backend._swarm = Swarm(scf)
        backend._telemetry_cache = {"radio://test/01": {"supervisor.info": 0.0}}
        backend._supervisor_info_reliable = backend._detect_supervisor_info_support()

        self.assertIsNone(backend.arm_all())
        self.assertEqual([True], scf.cf.platform.requests)

    def test_modern_protocol_enables_armed_confirmation(self) -> None:
        class Platform:
            def get_protocol_version(self) -> int: return 7

        scf = type("Scf", (), {"cf": type("Cf", (), {"platform": Platform()})()})()
        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        backend._swarm = type("Swarm", (), {"_cfs": {"radio://test/01": scf}})()

        self.assertTrue(backend._detect_supervisor_info_support())

    def test_removed_multi_aircraft_action_is_rejected(self) -> None:
        gateway, _ = self.make_gateway()
        gateway.arm()
        with self.assertRaisesRegex(RuntimeError, "不支持"):
            gateway.send_command(SwarmCommand("legacy_multi_action"))

    def test_flow_quality_loss_triggers_emergency(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        assert backend.telemetry is not None
        bad = {
            "stateEstimate.x": 0.0, "stateEstimate.y": 0.0, "stateEstimate.z": 0.35,
            "pm.vbat": 4.0, "range.zrange": 0.0, "motion.squal": 0.0,
        }
        for _ in range(3):
            backend.telemetry("radio://test/01", time.time(), bad)
        self.wait_for_call(backend, "emergency")
        self.assertEqual("emergency", gateway.status().state)

    def test_motor_stop_is_recoverable(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.stop_motors()
        self.wait_for_call(backend, "motor_stop")
        deadline = time.time() + 1
        while "电机已停止" not in gateway.status().message and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(gateway.status().armed)
        self.assertFalse(gateway.status().emergency)

    def test_emergency_requires_disconnect_and_reset(self) -> None:
        gateway, backend = self.make_gateway()
        gateway.arm()
        gateway.emergency_stop()
        self.wait_for_call(backend, "emergency")
        with self.assertRaisesRegex(RuntimeError, "断开"):
            gateway.reset_after_emergency_reboot()
        gateway.disconnect()
        gateway.reset_after_emergency_reboot()
        self.assertFalse(gateway.status().emergency)

    def test_cflib_emergency_uses_localization_fallback(self) -> None:
        calls: list[str] = []

        class Recorder:
            def send_emergency_stop(self) -> None: calls.append("loc-emergency")

        class Commander:
            def send_stop_setpoint(self) -> None: calls.append("stop")

        class Scf:
            cf = type("Cf", (), {"supervisor": None, "loc": Recorder(), "commander": Commander()})()

        CflibBackend._emergency_single(Scf())
        self.assertEqual(["loc-emergency"], calls)

    def test_hardware_self_check_detects_flow_v2_and_ai_deck(self) -> None:
        events: list[tuple[str, str]] = []

        class Param:
            values = {"deck": {"bcFlow": "0", "bcFlow2": "1", "bcAI": "1"}}
            def __init__(self) -> None: self.writes = []
            def set_value(self, name, value) -> None: self.writes.append((name, value))

        class Log:
            toc = type("Toc", (), {"toc": {"range": {"zrange": object()}, "motion": {"squal": object()}}})()

        class Scf:
            cf = type("Cf", (), {"link_uri": "radio://test/01", "param": Param(), "log": Log()})()

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        backend._event = lambda level, message: events.append((level, message))
        backend._configure_single(Scf())
        self.assertEqual(2.0, backend._flow_detected_max_height)
        self.assertTrue(any("Flow Deck V2" in message and "AI Deck" in message for _, message in events))

    def test_hardware_self_check_waits_for_async_parameter_values(self) -> None:
        class Param:
            def __init__(self) -> None:
                self.values = {}
                self.is_updated = False
                self.writes = []

            def set_value(self, name, value) -> None:
                self.writes.append((name, value))

        class Log:
            toc = type("Toc", (), {"toc": {
                "range": {"zrange": object()},
                "motion": {"squal": object()},
            }})()

        param = Param()

        class Scf:
            cf = type("Cf", (), {
                "link_uri": "radio://test/01",
                "param": param,
                "log": Log(),
            })()

        def finish_download() -> None:
            time.sleep(0.05)
            param.values = {"deck": {"bcFlow": "0", "bcFlow2": "1", "bcAI": "0"}}
            param.is_updated = True

        downloader = threading.Thread(target=finish_download)
        downloader.start()
        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        backend._configure_single(Scf())
        downloader.join(timeout=1.0)

        self.assertTrue(backend._detected_decks["radio://test/01"]["flow_v2"])
        self.assertIn(("stabilizer.estimator", "2"), param.writes)

    def test_hardware_self_check_ignores_transient_zero_flow_values(self) -> None:
        class Param:
            def __init__(self) -> None:
                # This is the real startup race: the keys and is_updated flag
                # arrive before the deck driver changes bcFlow2 from 0 to 1.
                self.values = {"deck": {"bcFlow": "0", "bcFlow2": "0", "bcAI": "0"}}
                self.is_updated = True
                self.writes = []
                self.refreshes = []

            def set_value(self, name, value) -> None:
                self.writes.append((name, value))

            def request_param_update(self, name) -> None:
                self.refreshes.append(name)
                if name == "deck.bcFlow2":
                    self.values = {"deck": {"bcFlow": "0", "bcFlow2": "1", "bcAI": "0"}}

        class Log:
            toc = type("Toc", (), {"toc": {
                "range": {"zrange": object()},
                "motion": {"squal": object()},
            }})()

        param = Param()

        class Scf:
            cf = type("Cf", (), {
                "link_uri": "radio://test/01",
                "param": param,
                "log": Log(),
            })()

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        backend._configure_single(Scf())

        self.assertTrue(backend._detected_decks["radio://test/01"]["flow_v2"])
        self.assertIn("deck.bcFlow2", param.refreshes)
        self.assertIn(("stabilizer.estimator", "2"), param.writes)

    def test_hardware_self_check_rejects_missing_flow_logs(self) -> None:
        class Param:
            values = {"deck": {"bcFlow": "1"}}
            def set_value(self, name, value) -> None: return None

        class Log:
            toc = type("Toc", (), {"toc": {}})()

        class Scf:
            cf = type("Cf", (), {"link_uri": "radio://test/01", "param": Param(), "log": Log()})()

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",)))
        with self.assertRaisesRegex(RuntimeError, "range.zrange.*motion.squal"):
            backend._configure_single(Scf())

    def test_backend_streams_official_hover_setpoints(self) -> None:
        class Commander:
            def __init__(self) -> None: self.hover_setpoints = []; self.stopped = False
            def send_hover_setpoint(self, vx, vy, yawrate, height) -> None: self.hover_setpoints.append((vx, vy, yawrate, height))
            def send_notify_setpoint_stop(self) -> None: return None
            def send_stop_setpoint(self) -> None: self.stopped = True

        class Platform:
            def __init__(self) -> None: self.requests = []
            def send_arming_request(self, armed) -> None: self.requests.append(bool(armed))

        class HighLevelCommander:
            def stop(self) -> None: return None

        class Scf:
            def __init__(self) -> None:
                self.cf = type("Cf", (), {})()
                self.cf.commander = Commander(); self.cf.platform = Platform(); self.cf.high_level_commander = HighLevelCommander()

        class Swarm:
            def __init__(self, scf) -> None: self._cfs = {"radio://test/01": scf}
            def parallel_safe(self, callback, args_dict=None) -> None:
                for uri, scf in self._cfs.items(): callback(scf, *(args_dict[uri] if args_dict else []))

        backend = CflibBackend(CrazyflieSwarmConfig(uris=("radio://test/01",), flow_setpoint_rate_hz=100.0))
        scf = Scf(); backend._swarm = Swarm(scf)
        backend._start_flow_control(0.4)
        time.sleep(0.04)
        self.assertTrue(scf.cf.commander.hover_setpoints)
        backend.set_manual_input(0.5, 1.0, -0.5)
        self.assertEqual((0.5, 0.25, 0.0), backend._flow_velocity)
        self.assertEqual(0.25, backend._flow_vertical_speed)
        backend.stop_motors_all()
        self.assertTrue(scf.cf.commander.stopped)
        self.assertEqual(False, scf.cf.platform.requests[-1])

    def test_backend_scheduler_refreshes_two_independent_flow_states(self) -> None:
        class Commander:
            def __init__(self) -> None:
                self.hover_setpoints = []
            def send_hover_setpoint(self, vx, vy, yawrate, height) -> None:
                self.hover_setpoints.append((vx, vy, yawrate, height))
            def send_notify_setpoint_stop(self) -> None: return None
            def send_stop_setpoint(self) -> None: return None

        class Platform:
            def send_arming_request(self, armed) -> None: return None

        class HighLevelCommander:
            def stop(self) -> None: return None

        class Scf:
            def __init__(self) -> None:
                self.cf = type("Cf", (), {})()
                self.cf.commander = Commander()
                self.cf.platform = Platform()
                self.cf.high_level_commander = HighLevelCommander()

        first, second = Scf(), Scf()

        class Swarm:
            _cfs = {"radio://test/01": first, "radio://test/02": second}
            def parallel_safe(self, callback, args_dict=None) -> None:
                for uri, scf in self._cfs.items():
                    callback(scf, *(args_dict[uri] if args_dict else []))

        backend = CflibBackend(CrazyflieSwarmConfig(
            uris=("radio://test/01", "radio://test/02"), flow_setpoint_rate_hz=100.0,
        ))
        backend._swarm = Swarm()
        backend._start_flow_control(0.4)
        time.sleep(0.04)

        self.assertTrue(first.cf.commander.hover_setpoints)
        self.assertTrue(second.cf.commander.hover_setpoints)
        backend.set_manual_input_one("radio://test/02", 0.0, 1.0, 0.0)
        with backend._flow_control_lock:
            first_state = backend._flow_states["radio://test/01"]
            second_state = backend._flow_states["radio://test/02"]
            self.assertEqual((0.0, 0.0, 0.0), first_state.velocity)
            self.assertEqual((0.5, 0.0, 0.0), second_state.velocity)

        backend.stop_motors_all()


if __name__ == "__main__":
    unittest.main()
