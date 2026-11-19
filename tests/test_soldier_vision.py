from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from src.controllers.gesture_controller import DEFAULT_WEIGHTS, GestureController

HAS_VISION = all(importlib.util.find_spec(n) is not None for n in ("cv2", "torch"))


@unittest.skipUnless(importlib.util.find_spec("cv2"), "Optional OpenCV is not installed")
class JpegStreamTests(unittest.TestCase):
    def test_mjpeg_source_decodes_real_jpeg_and_reports_disconnect(self):
        import cv2
        import numpy as np
        from src.integrations.gesture_camera import CameraSource

        ok, encoded = cv2.imencode(".jpg", np.full((80, 120, 3), 128, dtype=np.uint8))
        self.assertTrue(ok)
        stream = io.BytesIO(b"--cameraBoundary\r\nContent-Type: image/jpeg\r\n\r\n"
                            + encoded.tobytes() + b"\r\n--cameraBoundary\r\n")
        with patch("src.integrations.gesture_camera.urlopen", return_value=stream):
            source = CameraSource("mjpeg", "http://camera.local/stream")
            self.assertEqual(source.read().shape, (80, 120, 3))
            with self.assertRaises(ConnectionError):
                source.read()
            source.close()
            self.assertTrue(stream.closed)

    def test_split_markers_and_latest_frame_without_fixed_boundary(self):
        from src.integrations.gesture_camera import JpegBuffer

        parser = JpegBuffer()
        self.assertIsNone(parser.feed(b"--different-boundary\r\n\xff"))
        self.assertIsNone(parser.feed(b"\xd8frame-1\xff"))
        self.assertEqual(parser.feed(b"\xd9\r\n--abc\r\n\xff\xd8frame-2\xff\xd9"),
                         b"\xff\xd8frame-2\xff\xd9")
        self.assertEqual(parser.data, b"")

    def test_broken_stream_cannot_grow_memory_indefinitely(self):
        from src.integrations.gesture_camera import JpegBuffer

        parser = JpegBuffer()
        parser.feed(b"\xff\xd8" + b"x" * (4 * 1024 * 1024))
        self.assertEqual(parser.data, b"")


class GestureControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = GestureController()

    def tearDown(self):
        self.controller.shutdown()

    def test_invalid_source_or_missing_weights_does_not_open_camera(self):
        self.controller.start("mjpeg", "file:///tmp/image")
        self.assertFalse(self.controller.running)
        self.assertIn("http", self.controller.message)
        self.controller.start("camera", "0", "/not/a/model.pth")
        self.assertFalse(self.controller.running)
        self.assertIn("权重", self.controller.message)
        self.assertEqual(self.controller.process.state(), QProcess.NotRunning)

    def test_missing_optional_dependencies_leave_application_usable(self):
        with tempfile.NamedTemporaryFile() as weights, patch(
                "src.controllers.gesture_controller.importlib.util.find_spec", return_value=None):
            self.controller.start("camera", "0", weights.name)
        self.assertFalse(self.controller.running)
        self.assertIn("requirements-vision.txt", self.controller.message)

    def test_stale_recognition_and_camera_loss_clear_suggestions(self):
        controller = self.controller
        controller.running = True
        controller._last_frame = time.monotonic()
        controller.result = {"captured_at": time.monotonic() - 5, "top": 1}
        self.assertIsNone(controller.fresh_result())
        controller.result["captured_at"] = time.monotonic()
        self.assertIsNotNone(controller.fresh_result())
        controller._last_frame -= 5
        with patch.object(controller.process, "terminate"):
            controller._check_freshness()
        self.assertIsNone(controller.result)
        self.assertIn("没有新画面", controller.message)
        controller.running = False
        controller.kill_timer.stop()

    def test_late_process_messages_cannot_revive_stopped_result(self):
        self.controller._stopping = True
        self.controller._handle_message({"kind": "status", "message": "running"})
        self.assertNotEqual(self.controller.message, "running")

    @unittest.skipUnless(HAS_VISION and DEFAULT_WEIGHTS.is_file(), "Optional vision dependencies/weights missing")
    def test_real_video_subprocess_emits_preview_and_snn_results_then_stops(self):
        import cv2
        import numpy as np

        previews, results, events = [], [], []
        self.controller.frame_received.connect(lambda image: previews.append(image.size()))
        self.controller.events_received.connect(lambda image: events.append(image.size()))
        self.controller.changed.connect(lambda: results.append(self.controller.result)
                                        if self.controller.result else None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (160, 120))
            self.assertTrue(writer.isOpened())
            for index in range(160):
                frame = np.zeros((120, 160, 3), dtype=np.uint8)
                x = index * 7 % 120
                cv2.rectangle(frame, (x, 25), (x + 35, 100), (255, 255, 255), -1)
                writer.write(frame)
            writer.release()
            self.controller.start("video", str(path))
            deadline = time.monotonic() + 18
            while not results and time.monotonic() < deadline and self.controller.running:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertTrue(previews, self.controller.message)
            self.assertTrue(results, self.controller.message + self.controller._stderr)
            self.assertTrue(events)
            self.assertEqual(len(results[-1]["top3"]), 3)
            self.assertIsNotNone(self.controller.fresh_result())
            self.controller.shutdown()
            self.assertEqual(self.controller.process.state(), QProcess.NotRunning)
            self.assertFalse(self.controller.running)
            self.assertIsNone(self.controller.result)


class SoldierVisionNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_soldier_navigation_and_logout_cleanup_do_not_send_flight_commands(self):
        from src.adapters.mock import MockSwarmGateway
        from src.domain import Role, User
        from src.views.main_view import MainView

        gateway = MockSwarmGateway()
        view = MainView(swarm_gateway=gateway)
        try:
            view.resize(1280, 720)
            view.set_user(User("soldier", "测试", Role.SOLDIER))
            view.show()
            before = len(gateway.command_log)
            view._activate("soldier-page:视觉手势")
            self.app.processEvents()
            self.assertTrue(view.soldier_widget.vision_panel.isVisible())
            self.assertEqual(view.soldier_root, "单兵面板")
            view._activate("soldier-page:脑控")
            self.app.processEvents()
            self.assertFalse(view.soldier_widget.vision_panel.isVisible())
            self.assertEqual(len(gateway.command_log), before)
            with patch.object(view.gesture, "shutdown") as shutdown:
                view.deactivate()
                shutdown.assert_called_once()
        finally:
            view.close()


if __name__ == "__main__":
    unittest.main()
