"""Application composition root.

Only this module chooses mock versus real integrations. Views depend on services
or ports and never instantiate hardware clients directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.firebase import FirebaseAuthGateway
from src.adapters.mock import (
    MockAuthGateway,
    MockEEGStream,
    MockEngineerDataProvider,
    MockSwarmGateway,
    MockSystemMonitor,
)
from src.ports import EEGStream, EngineerDataProvider, SwarmGateway, SystemMonitor
from src.services.auth_service import AuthService
from src.services.role_profile_store import LocalRoleProfileStore
from src.settings import AUTH_BACKEND, FIREBASE_API_KEY, ROLE_PROFILE_PATH


@dataclass(frozen=True)
class ApplicationContainer:
    auth_service: AuthService
    eeg_stream: EEGStream
    swarm_gateway: SwarmGateway
    system_monitor: SystemMonitor
    engineer_data_provider: EngineerDataProvider


def create_demo_container() -> ApplicationContainer:
    """组装纯演示环境，不会连接任何真实设备、数据库或网络服务。

    【真实接口接入点】将来不要到 View 里直接 ``import pylsl/cflib``。
    应新增 ``create_production_container()``，把下面四个 Mock 分别替换成
    SQLite/HTTPAuthGateway、LSLEEGStream、CrazyflieSwarmGateway 和
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
    """Create the normal app container, using Firebase Auth by default.

    Set ``YY_AUTH_BACKEND=mock`` to work offline with the original role-based
    demo accounts. Hardware and telemetry remain mocked in both modes.
    """

    auth_gateway = (
        MockAuthGateway()
        if AUTH_BACKEND == "mock"
        else FirebaseAuthGateway(FIREBASE_API_KEY, role_store=LocalRoleProfileStore(ROLE_PROFILE_PATH))
    )
    return ApplicationContainer(
        auth_service=AuthService(auth_gateway),
        eeg_stream=MockEEGStream(),
        swarm_gateway=MockSwarmGateway(),
        system_monitor=MockSystemMonitor(),
        engineer_data_provider=MockEngineerDataProvider(),
    )
