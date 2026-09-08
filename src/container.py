"""Application composition root.

Only this module chooses mock versus real integrations. Views depend on services
or ports and never instantiate hardware clients directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.adapters.http import HttpAuthGateway
from src.adapters.real import (
    CrazyflieSwarmConfig,
    CrazyflieSwarmGateway,
    CsvEEGStream,
    DisabledSwarmGateway,
    LSLEEGStream,
    RealSystemMonitor,
    RuntimeEngineerDataProvider,
    UnavailableEEGStream,
)
from src.adapters.mock import (
    MockAuthGateway,
    MockEEGStream,
    MockEngineerDataProvider,
    MockSwarmGateway,
    MockSystemMonitor,
)
from src.bci import FBCCADecoder
from src.bci.config import (
    EEG_BACKEND,
    EEG_CSV_PATH,
    FRAME_SIZE,
    LSL_RESOLVE_TIMEOUT_SECONDS,
    LSL_STREAM_NAME,
    LSL_STREAM_TYPE,
    RECORDING_ROOT,
    SAMPLE_RATE_HZ,
)
from src.ports import EEGStream, EngineerDataProvider, IntentDecoder, SwarmGateway, SystemMonitor
from src.services.auth_service import AuthService
from src.services.experiment_recorder import ExperimentRecorder
from src.settings import AUTH_API_URL, AUTH_BACKEND


@dataclass(frozen=True)
class ApplicationContainer:
    auth_service: AuthService
    eeg_stream: EEGStream
    swarm_gateway: SwarmGateway
    system_monitor: SystemMonitor
    engineer_data_provider: EngineerDataProvider
    intent_decoder: IntentDecoder | None = None
    experiment_recorder: ExperimentRecorder | None = None


def create_demo_container() -> ApplicationContainer:
    """组装纯演示环境，不会连接任何真实设备、数据库或网络服务。

    【真实接口接入点】将来不要到 View 里直接 ``import pylsl/cflib``。
    应新增 ``create_production_container()``，把下面四个 Mock 分别替换成
    PostgreSQL/Spring HTTP 认证、LSLEEGStream、CrazyflieSwarmGateway 和
    RealSystemMonitor。这样前端绘制代码不需要随硬件实现一起重写。
    """

    return ApplicationContainer(
        auth_service=AuthService(MockAuthGateway()),
        eeg_stream=MockEEGStream(),
        swarm_gateway=MockSwarmGateway(),
        system_monitor=MockSystemMonitor(),
        engineer_data_provider=MockEngineerDataProvider(),
    )


def create_default_container() -> ApplicationContainer:
    """Create the normal app container, using the Spring REST API by default.

    Authentication may be explicitly switched to mock for UI-only testing.
    EEG and swarm data never silently fall back to generated mock values.
    """

    auth_gateway = (
        MockAuthGateway()
        if AUTH_BACKEND == "mock"
        else HttpAuthGateway(AUTH_API_URL)
    )
    if EEG_BACKEND == "lsl":
        eeg_stream: EEGStream = LSLEEGStream(
            stream_name=LSL_STREAM_NAME,
            stream_type=LSL_STREAM_TYPE,
            timeout_seconds=LSL_RESOLVE_TIMEOUT_SECONDS,
            frame_size=FRAME_SIZE,
        )
    elif EEG_BACKEND == "csv" and EEG_CSV_PATH.is_file():
        eeg_stream = CsvEEGStream(
            EEG_CSV_PATH,
            sample_rate_hz=SAMPLE_RATE_HZ,
            frame_size=FRAME_SIZE,
            loop=True,
            realtime=True,
        )
    else:
        eeg_stream = UnavailableEEGStream(
            f"EEG 数据源不可用：backend={EEG_BACKEND!r}，CSV={EEG_CSV_PATH}"
        )

    swarm_backend = os.getenv("BDS_SWARM_BACKEND", "disabled").strip().lower()
    if swarm_backend == "crazyflie":
        swarm_gateway: SwarmGateway = CrazyflieSwarmGateway(CrazyflieSwarmConfig.from_environment())
    else:
        swarm_gateway = DisabledSwarmGateway(
            "蜂群接口未启用；设置 BDS_SWARM_BACKEND=crazyflie 并配置实机参数"
        )
    system_monitor = RealSystemMonitor()
    engineer_data_provider = RuntimeEngineerDataProvider(eeg_stream, swarm_gateway)

    return ApplicationContainer(
        auth_service=AuthService(auth_gateway),
        eeg_stream=eeg_stream,
        swarm_gateway=swarm_gateway,
        system_monitor=system_monitor,
        engineer_data_provider=engineer_data_provider,
        intent_decoder=FBCCADecoder(),
        experiment_recorder=ExperimentRecorder(RECORDING_ROOT),
    )
