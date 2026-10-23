"""Engineering dashboard data composed from live adapter caches."""

from __future__ import annotations

from datetime import datetime

from src.bci.config import SAMPLE_RATE_HZ
from src.ports import EEGStream, ModuleRecord, StreamStatus, SwarmGateway


class RuntimeEngineerDataProvider:
    def __init__(self, eeg_stream: EEGStream, swarm_gateway: SwarmGateway) -> None:
        self.eeg_stream = eeg_stream
        self.swarm_gateway = swarm_gateway

    def streams(self) -> list[StreamStatus]:
        eeg_online = bool(getattr(self.eeg_stream, "connected", False))
        swarm_status = self.swarm_gateway.status()
        return [
            StreamStatus("EEG", str(getattr(self.eeg_stream, "source_name", type(self.eeg_stream).__name__)),
                         float(getattr(self.eeg_stream, "sample_rate_hz", SAMPLE_RATE_HZ) or SAMPLE_RATE_HZ),
                         0.0, eeg_online),
            StreamStatus("无人机遥测", swarm_status.backend, 10.0, 0.0, swarm_status.connected),
        ]

    def module_records(self, module_key: str) -> list[ModuleRecord]:
        now = datetime.now().strftime("%H:%M:%S")
        status = self.swarm_gateway.status()
        if module_key == "swarm":
            uavs = self.swarm_gateway.list_uavs()
            if not uavs:
                return [ModuleRecord("Crazyflie 蜂群", status.state, status.message or "无遥测", now)]
            return [
                ModuleRecord(uav.uav_id, uav.status.value,
                             f"{uav.battery_voltage or 0:.2f}V · ({uav.position.x:.2f}, {uav.position.y:.2f}, {uav.position.z:.2f})m",
                             now)
                for uav in uavs
            ]
        data = {
            "target": [("目标识别服务", "未接入", "等待真实模型/数据接口")],
            "decoder": [("FBCCA / Transformer-SSVEP", "已接入", "2秒窗口；0.25秒更新"),
                        ("EEG 数据源", "在线" if getattr(self.eeg_stream, "connected", False) else "离线",
                         str(getattr(self.eeg_stream, "source_name", "未配置")))],
            "coordination": [("蜂群命令队列", status.state, "已解锁" if status.armed else "已锁定")],
            "device": [("Crazyflie", status.state, status.message),
                       ("EEG", "在线" if getattr(self.eeg_stream, "connected", False) else "离线",
                        str(getattr(self.eeg_stream, "source_name", "未配置")))],
            "quality": [("Flow Deck", "等待遥测" if not status.connected else "见单机状态", "range.zrange / motion.squal"),
                        ("EEG 信号质量", "由解码记录提供", "见 recognition_logs")],
            "data": [("实验记录", "本地文件", "data/research_experiments"),
                     ("PostgreSQL 元数据", "未接入", "等待实验元数据 API")],
        }
        return [ModuleRecord(name, item_status, metric, now) for name, item_status, metric in data.get(module_key, [])]

    def logs(self, level: str) -> list[str]:
        event_reader = getattr(self.swarm_gateway, "events", None)
        if callable(event_reader):
            values = event_reader(level)
            if values:
                return values
        return [f"{datetime.now():%H:%M:%S}  {level}  暂无真实设备日志"]
