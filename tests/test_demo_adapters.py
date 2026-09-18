from __future__ import annotations

import unittest

from src.adapters.mock import MockEEGStream, MockSwarmGateway, MockSystemMonitor
from src.domain import SwarmCommand


class DemoAdaptersTest(unittest.TestCase):
    def test_eeg_stream_contract(self) -> None:
        stream = MockEEGStream(frame_size=16)
        stream.connect()
        frame = stream.read_frame()
        self.assertEqual(8, len(frame.channel_names))
        self.assertEqual(8, len(frame.samples))
        self.assertTrue(all(len(channel) == 16 for channel in frame.samples))
        stream.disconnect()

    def test_swarm_command_and_emergency_stop(self) -> None:
        gateway = MockSwarmGateway()
        self.assertEqual(4, len(gateway.list_uavs()))
        gateway.send_command(SwarmCommand("takeoff"))
        gateway.send_command(SwarmCommand("forward", parameters={"speed_mps": 2.0}))
        gateway.emergency_stop()
        self.assertEqual("emergency_stop", gateway.command_log[-1].action)

    def test_system_monitor_values_are_in_range(self) -> None:
        metrics = MockSystemMonitor().snapshot()
        self.assertGreaterEqual(metrics.cpu_percent, 0)
        self.assertLessEqual(metrics.cpu_percent, 100)


if __name__ == "__main__":
    unittest.main()
