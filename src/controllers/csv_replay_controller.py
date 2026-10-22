"""CSV replay workflow extracted from the main dashboard widget."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Protocol

from PySide6.QtWidgets import QFileDialog, QWidget


class CSVReplayHost(Protocol):
    _pipeline_worker: object | None
    _eeg_history: list
    _eeg_channel_names: tuple[str, ...]
    _eeg_channel_units: tuple[str, ...]
    _latest_recognition: object | None
    research_page: str

    def _disconnect_eeg_pipeline(self, stop_csv_recording: bool = True) -> None: ...
    def _build_eeg_worker(self, replace: bool = False) -> None: ...
    def _start_eeg_pipeline(self) -> None: ...
    def _notify(self, message: str) -> None: ...
    def update(self, *args) -> None: ...


class CSVReplayController:
    """Own CSV selection, transport controls and replay presentation state."""

    def __init__(self, stream, host: CSVReplayHost) -> None:
        self.stream = stream
        self.host = host
        self.paused = False

    def snapshot(self) -> dict[str, object]:
        snapshot = getattr(self.stream, "playback_snapshot", None)
        if callable(snapshot):
            try:
                return dict(snapshot())
            except Exception as exc:
                return {"backend": "error", "error": str(exc)}
        return {"backend": "unavailable"}

    def choose_file(self) -> None:
        selector = getattr(self.stream, "select_csv", None)
        if not callable(selector):
            self.host._notify("当前 EEG 容器不支持运行时 CSV 回放")
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self.host if isinstance(self.host, QWidget) else None,
            "导入 OpenBCI / LSL EEG CSV",
            str(Path.home()),
            "EEG CSV (*.csv);;所有文件 (*)",
        )
        if not path:
            self.host._notify("已取消导入 CSV")
            return
        self.host._disconnect_eeg_pipeline(stop_csv_recording=True)
        try:
            selector(path)
        except Exception as exc:
            self.host._notify(f"CSV 导入失败：{exc}")
            return
        self.host._eeg_history = []
        self.host._eeg_channel_names = ()
        self.host._eeg_channel_units = ()
        self.host._latest_recognition = None
        self.paused = False
        self.host._build_eeg_worker(replace=True)
        self.host._start_eeg_pipeline()
        self.host.research_page = "CSV回放"
        self.host._notify(f"已导入并开始回放：{Path(path).name}")

    def toggle(self) -> None:
        if self.snapshot().get("backend") != "csv":
            self.host._notify("请先导入 CSV 文件")
            return
        worker = self.host._pipeline_worker
        running = worker is not None and worker.isRunning()
        if running:
            self.host._disconnect_eeg_pipeline(stop_csv_recording=False)
            self.paused = True
            self.host._notify("CSV 回放已暂停")
        else:
            self.host._build_eeg_worker(replace=True)
            self.host._start_eeg_pipeline()
            self.paused = False
            self.host._notify("CSV 回放已继续")

    def rewind(self) -> None:
        rewind = getattr(self.stream, "rewind_csv", None)
        if not callable(rewind):
            self.host._notify("当前 EEG 容器不支持 CSV 回放")
            return
        worker = self.host._pipeline_worker
        was_running = worker is not None and worker.isRunning()
        if was_running:
            self.host._disconnect_eeg_pipeline(stop_csv_recording=False)
        try:
            rewind()
        except Exception as exc:
            self.host._notify(f"CSV 回到开头失败：{exc}")
            return
        self._reset_history()
        if was_running:
            self.host._build_eeg_worker(replace=True)
            self.host._start_eeg_pipeline()
        self.host._notify("CSV 已回到开头")

    def seek(self, fraction: float) -> None:
        seek = getattr(self.stream, "seek_csv", None)
        if not callable(seek):
            self.host._notify("当前 EEG 容器不支持 CSV 回放")
            return
        try:
            seek(fraction)
        except Exception as exc:
            self.host._notify(f"CSV 定位失败：{exc}")
            return
        self._reset_history()
        self.host.update()

    def set_speed(self, speed: float) -> None:
        setter = getattr(self.stream, "set_csv_playback_speed", None)
        if not callable(setter):
            self.host._notify("当前 EEG 容器不支持 CSV 回放")
            return
        try:
            setter(speed)
        except Exception as exc:
            self.host._notify(f"设置回放速度失败：{exc}")
            return
        self.host._notify(f"CSV 回放速度：{speed:g}x")

    def _reset_history(self) -> None:
        self.host._eeg_history = [
            deque(maxlen=360) for _ in self.host._eeg_channel_names
        ]


__all__ = ["CSVReplayController"]
