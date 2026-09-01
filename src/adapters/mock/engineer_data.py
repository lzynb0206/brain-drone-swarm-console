"""Deterministic engineering data used until real services are connected."""

from __future__ import annotations

from datetime import datetime

from src.ports import ModuleRecord, StreamStatus


class MockEngineerDataProvider:
    """In-memory data source implementing the same contract as real APIs."""

    def streams(self) -> list[StreamStatus]:
        return [
            StreamStatus("EEG", "DeepBCI 采集板", 250, 0.1, True),
            StreamStatus("无人机遥测", "Crazyflie 蜂群", 100, 0.3, True),
            StreamStatus("fNIRS", "多模态头盔", 50, 0.0, True),
        ]

    def module_records(self, module_key: str) -> list[ModuleRecord]:
        now = datetime.now().strftime("%H:%M:%S")
        data = {
            "swarm": [("UAV-01", "在线", "电量 82%"), ("UAV-02", "在线", "电量 75%"), ("UAV-03", "告警", "丢包 1.2%")],
            "target": [("目标识别服务", "运行中", "置信度 91%"), ("目标跟踪服务", "运行中", "延迟 42ms")],
            "decoder": [("GraSPNet", "已加载", "准确率 91.2%"), ("预处理流水线", "运行中", "250Hz")],
            "coordination": [("指令仲裁器", "运行中", "队列 0"), ("编队控制器", "待命", "PID")],
            "device": [("DeepBCI 采集板", "在线", "固件 v2.1"), ("多模态头盔", "离线", "固件 v1.0")],
            "quality": [("EEG 阻抗", "正常", "8/8 通道"), ("工频干扰", "轻微", "50Hz 12.5µV")],
            "data": [("会话索引", "正常", "12 个会话"), ("数据目录", "正常", "占用 68%")],
        }
        return [ModuleRecord(name, status, metric, now) for name, status, metric in data.get(module_key, [])]

    def logs(self, level: str) -> list[str]:
        values = {
            "INFO": ["LSL 数据流已连接", "算法 GraSPNet 已加载", "系统监控刷新完成"],
            "WARN": ["UAV-03 丢包率偏高", "fNIRS 信号暂未参与融合"],
            "ERROR": ["当前没有未处理错误"],
        }
        stamp = datetime.now().strftime("%H:%M:%S")
        return [f"{stamp}  {level}  {message}" for message in values.get(level, [])]
