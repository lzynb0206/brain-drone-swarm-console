"""Bounded camera sources shared by the gesture subprocess and offline tests."""
from __future__ import annotations

from urllib.parse import urlsplit
from urllib.request import urlopen

import cv2
import numpy as np


class JpegBuffer:
    """Extract the newest complete JPEG without depending on a boundary name."""

    def __init__(self):
        self.data = b""

    def feed(self, chunk: bytes) -> bytes | None:
        self.data += chunk
        latest = None
        while True:
            start = self.data.find(b"\xff\xd8")
            if start < 0:
                self.data = self.data[-1:]
                break
            end = self.data.find(b"\xff\xd9", start + 2)
            if end < 0:
                self.data = self.data[start:]
                break
            latest = self.data[start:end + 2]
            self.data = self.data[end + 2:]
        if len(self.data) > 4 * 1024 * 1024:
            self.data = b""
        return latest


class CameraSource:
    def __init__(self, kind: str, source: str):
        self.response = self.capture = None
        self.parser = JpegBuffer()
        if kind == "mjpeg":
            url = urlsplit(source)
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("请输入完整的 http(s)://摄像头地址/stream")
            self.response = urlopen(source, timeout=5)
        elif kind in {"camera", "video"}:
            self.capture = cv2.VideoCapture(int(source) if kind == "camera" else source)
            if not self.capture.isOpened():
                self.close()
                raise RuntimeError("无法打开输入源，请检查摄像头权限、编号或视频文件")
        else:
            raise ValueError("不支持的摄像头输入类型")

    def read(self):
        if self.capture is not None:
            ok, frame = self.capture.read()
            return frame if ok else None
        while True:
            chunk = self.response.read1(16384)
            if not chunk:
                raise ConnectionError("MJPEG 视频流已断开，请检查设备后重新连接")
            jpeg = self.parser.feed(chunk)
            if jpeg:
                frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return frame

    def close(self):
        if self.capture is not None:
            self.capture.release()
        if self.response is not None:
            self.response.close()


def encode_preview(frame, max_width=640) -> str:
    import base64

    if frame.shape[1] > max_width:
        frame = cv2.resize(frame, (max_width, round(frame.shape[0] * max_width / frame.shape[1])))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        raise RuntimeError("摄像头画面编码失败")
    return base64.b64encode(jpeg).decode("ascii")
