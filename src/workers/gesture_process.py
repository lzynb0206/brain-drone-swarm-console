"""Camera + SNN subprocess. JSON-lines stdout is consumed by the Qt controller.

Capture and inference are independent so CPU inference does not freeze preview.
The bounded window always contains the latest frames, never a growing work queue.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import signal
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("camera", "mjpeg", "video"), required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--threshold", type=float, default=12)
    parser.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0)
    args = parser.parse_args()
    write_lock, buffer_lock = threading.Lock(), threading.Lock()
    frames = deque(maxlen=17)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    source = inference_worker = None

    def emit(kind, **values):
        with write_lock:
            print(json.dumps({"kind": kind, **values}, ensure_ascii=False), flush=True)

    def infer():
        try:
            from src.integrations.gesture_snn import CLASSES, GestureRecognizer

            recognizer = GestureRecognizer(args.weights)
            emit("status", message=f"模型已加载 · {recognizer.device.upper()} · 等待 17 帧窗口")
            last_id = -1
            while not stop.wait(0.03):
                with buffer_lock:
                    batch = list(frames)
                if len(batch) != 17 or batch[-1][0] == last_id:
                    continue
                last_id, captured_at, _ = batch[-1]
                # Do not keep classifying a frozen camera after capture stops.
                if time.monotonic() - captured_at > 2:
                    continue
                started = time.monotonic()
                scores, events = recognizer.predict([item[2] for item in batch], args.threshold)
                order = scores.argsort()[::-1][:3]
                if stop.is_set():
                    return
                emit("result", frame_id=last_id, captured_at=captured_at,
                     top=int(order[0]), top_name=CLASSES[int(order[0])],
                     score=float(scores[order[0]]),
                     top3=[{"name": CLASSES[int(i)], "score": float(scores[i])} for i in order],
                     infer_ms=(time.monotonic() - started) * 1000,
                     events=encode_preview(events, 512))
        except Exception as exc:
            emit("error", message=f"手势模型加载或推理失败：{exc}")
            stop.set()

    try:
        import cv2
        from src.integrations.gesture_camera import CameraSource, encode_preview

        emit("status", message="正在打开摄像头并加载手势模型…")
        source = CameraSource(args.kind, args.source)
        inference_worker = threading.Thread(target=infer)
        inference_worker.start()
        frame_id, last_preview = 0, 0.0
        fps = source.capture.get(cv2.CAP_PROP_FPS) if args.kind == "video" else 0
        video_interval = 1 / min(60, max(1, fps or 25))
        while not stop.is_set():
            started = time.monotonic()
            frame = source.read()
            if frame is None:
                stop.set()
                if args.kind == "video":
                    emit("ended", message="视频回放结束")
                else:
                    emit("error", message="摄像头未返回画面，请检查权限或连接")
                break
            if args.rotate:
                frame = cv2.rotate(frame, {90: cv2.ROTATE_90_CLOCKWISE,
                                          180: cv2.ROTATE_180,
                                          270: cv2.ROTATE_90_COUNTERCLOCKWISE}[args.rotate])
            # Bound memory/compute for high-resolution webcams while preserving aspect ratio.
            if frame.shape[1] > 640:
                frame = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
            captured_at = time.monotonic()
            frame_id += 1
            with buffer_lock:
                frames.append((frame_id, captured_at, frame))
            if captured_at - last_preview >= 0.1:
                emit("frame", image=encode_preview(frame), captured_at=captured_at)
                last_preview = captured_at
            if args.kind == "video":
                stop.wait(max(0, video_interval - (time.monotonic() - started)))
    except Exception as exc:
        emit("error", message=f"摄像头启动或读取失败：{exc}")
        return 1
    finally:
        stop.set()
        if source is not None:
            source.close()
        if inference_worker is not None:
            inference_worker.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
