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
    ExpandableEEGSerialStream,
    LSLEEGStream,
    RealSystemMonitor,
    RuntimeEngineerDataProvider,
    SelectableEEGStream,
    UnavailableEEGStream,
)
from src.adapters.mock import (
    MockAuthGateway,
    MockEEGStream,
    MockEngineerDataProvider,
    MockSwarmGateway,
    MockSystemMonitor,
)
from src.bci import (
    ConsecutiveWindowGate,
    FBCCADecoder,
    LzyPersonalDecoder,
    PersonalScoreCalibrationDecoder,
    RuntimeIntentDecoder,
)
from src.bci.personal_template_decoder import PersonalTemplateSSVEPDecoder
from src.bci.config import (
    EEG_BACKEND,
    EEG_CSV_PATH,
    FRAME_SIZE,
    LSL_RESOLVE_TIMEOUT_SECONDS,
    LSL_STREAM_NAME,
    LSL_STREAM_TYPE,
    RECORDING_ROOT,
    SAMPLE_RATE_HZ,
    FB_EEGNET_ENABLED,
    FB_EEGNET_METADATA_PATH,
    FB_EEGNET_MODEL_PATH,
    INTENT_DECODER,
    LZY_PERSONAL_METADATA_PATH,
    LZY_PERSONAL_MODEL_PATH,
    PERSONAL_TEMPLATE_METADATA_PATH,
    PERSONAL_TEMPLATE_MODEL_PATH,
    PERSONAL_SCORE_METADATA_PATH,
    PERSONAL_SCORE_MODEL_PATH,
    TRANSFORMER_COOLDOWN_SECONDS,
    TRANSFORMER_METADATA_PATH,
    TRANSFORMER_MIN_CONFIDENCE,
    TRANSFORMER_MODEL_PATH,
    TRANSFORMER_REQUIRED_WINDOWS,
    XZ_PERSONAL_METADATA_PATH,
    XZ_PERSONAL_MODEL_PATH,
    SOLDIER_SSVEP_DATA_ROOT,
    SSVEP_TEST_DATA_ROOT,
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
    ssvep_test_recorder: ExperimentRecorder | None = None
    soldier_ssvep_recorder: ExperimentRecorder | None = None


def create_demo_container() -> ApplicationContainer:
    """组装隔离的测试环境，不连接真实设备、数据库或网络服务。

    正常启动使用 ``create_default_container()``；此容器仅供自动化测试和
    明确选择的离线界面测试使用。
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
    lsl_stream = LSLEEGStream(
        stream_name=LSL_STREAM_NAME,
        stream_type=LSL_STREAM_TYPE,
        timeout_seconds=LSL_RESOLVE_TIMEOUT_SECONDS,
        frame_size=FRAME_SIZE,
    )
    # The vendor Bluetooth firmware is packet-compatible with Cyton but does
    # not implement BrainFlow's full Cyton welcome/configuration handshake.
    # Use the native adapter so a valid SPP link is not rejected before EEG
    # packets have a chance to arrive.
    hardware_stream = ExpandableEEGSerialStream(frame_size=FRAME_SIZE)
    if EEG_BACKEND == "lsl":
        eeg_stream: EEGStream = SelectableEEGStream(
            lsl_stream,
            lsl_stream,
            hardware_stream,
        )
    elif EEG_BACKEND == "csv" and EEG_CSV_PATH.is_file():
        eeg_stream = SelectableEEGStream(
            CsvEEGStream(
                EEG_CSV_PATH,
                sample_rate_hz=SAMPLE_RATE_HZ,
                frame_size=FRAME_SIZE,
                loop=True,
                realtime=True,
            ),
            lsl_stream,
            hardware_stream,
        )
    else:
        eeg_stream = SelectableEEGStream(
            UnavailableEEGStream(
                f"EEG 数据源不可用：backend={EEG_BACKEND!r}，CSV={EEG_CSV_PATH}"
            ),
            lsl_stream,
            hardware_stream,
        )

    swarm_backend = os.getenv("BDS_SWARM_BACKEND", "disabled").strip().lower()
    if swarm_backend == "flowdeck":
        swarm_gateway: SwarmGateway = CrazyflieSwarmGateway(
            CrazyflieSwarmConfig.from_environment()
        )
    else:
        swarm_gateway = DisabledSwarmGateway(
            "无人机接口未启用；设置 BDS_SWARM_BACKEND=flowdeck 并配置实机参数"
        )
    system_monitor = RealSystemMonitor()
    engineer_data_provider = RuntimeEngineerDataProvider(eeg_stream, swarm_gateway)
    baseline_decoder: IntentDecoder = FBCCADecoder()
    if FB_EEGNET_ENABLED:
        from src.bci.hybrid_decoder import HybridIntentDecoder
        from src.ml.predictor import FBEEGNetPredictor

        baseline_decoder = HybridIntentDecoder(
            FBEEGNetPredictor(FB_EEGNET_MODEL_PATH, FB_EEGNET_METADATA_PATH),
            baseline_decoder,
        )
    from src.bci.transformer_decoder import TransformerIntentDecoder
    from src.ml.transformer_predictor import TransformerSSVEPPredictor

    transformer_decoder = ConsecutiveWindowGate(
        TransformerIntentDecoder(
            TransformerSSVEPPredictor(
                TRANSFORMER_MODEL_PATH,
                TRANSFORMER_METADATA_PATH,
            )
        ),
        min_confidence=TRANSFORMER_MIN_CONFIDENCE,
        required_windows=TRANSFORMER_REQUIRED_WINDOWS,
        cooldown_seconds=TRANSFORMER_COOLDOWN_SECONDS,
    )
    from src.ml.personal_template_model import PersonalTemplateModel

    personal_template_decoder = PersonalTemplateSSVEPDecoder(
        PersonalTemplateModel(
            PERSONAL_TEMPLATE_MODEL_PATH,
            PERSONAL_TEMPLATE_METADATA_PATH,
        ),
        FBCCADecoder(),
    )
    from src.ml.personal_score_calibration import PersonalScoreCalibrationModel

    personal_score_decoder = PersonalScoreCalibrationDecoder(
        PersonalScoreCalibrationModel(
            PERSONAL_SCORE_MODEL_PATH,
            PERSONAL_SCORE_METADATA_PATH,
        )
    )
    xz_personal_decoder = PersonalScoreCalibrationDecoder(
        PersonalScoreCalibrationModel(
            XZ_PERSONAL_MODEL_PATH,
            XZ_PERSONAL_METADATA_PATH,
            algorithm_name="个人算法-Xz",
        ),
        algorithm_name="个人算法-Xz",
    )
    from src.ml.lzy_personal_model import LzyPersonalModel

    lzy_personal_decoder = LzyPersonalDecoder(
        LzyPersonalModel(
            LZY_PERSONAL_MODEL_PATH,
            LZY_PERSONAL_METADATA_PATH,
        )
    )
    decoder_aliases = {
        "fbcca": "FBCCA",
        "lzy": "个人算法-Lzy",
        "personal-lzy": "个人算法-Lzy",
        "lzy-personal": "个人算法-Lzy",
        "personal-score": "个人校准融合",
        "score-calibration": "个人校准融合",
        "dual-fbcca-logreg": "个人校准融合",
        "xz": "个人算法-Xz",
        "personal-xz": "个人算法-Xz",
        "xz-personal": "个人算法-Xz",
        "personal": "个人模板-SSVEP",
        "personal-template": "个人模板-SSVEP",
        "template": "个人模板-SSVEP",
        "transformer": "Transformer-SSVEP",
        "transformer-ssvep": "Transformer-SSVEP",
    }
    if INTENT_DECODER not in decoder_aliases:
        raise ValueError(
            "BDS_INTENT_DECODER must be one of: fbcca, lzy, xz, personal-score, "
            "personal-template, transformer"
        )
    intent_decoder: IntentDecoder = RuntimeIntentDecoder(
        {
            "FBCCA": baseline_decoder,
            "个人算法-Lzy": lzy_personal_decoder,
            "个人算法-Xz": xz_personal_decoder,
            "个人校准融合": personal_score_decoder,
            "个人模板-SSVEP": personal_template_decoder,
            "Transformer-SSVEP": transformer_decoder,
        },
        initial=decoder_aliases[INTENT_DECODER],
    )

    return ApplicationContainer(
        auth_service=AuthService(auth_gateway),
        eeg_stream=eeg_stream,
        swarm_gateway=swarm_gateway,
        system_monitor=system_monitor,
        engineer_data_provider=engineer_data_provider,
        intent_decoder=intent_decoder,
        experiment_recorder=ExperimentRecorder(RECORDING_ROOT),
        ssvep_test_recorder=ExperimentRecorder(SSVEP_TEST_DATA_ROOT),
        soldier_ssvep_recorder=ExperimentRecorder(SOLDIER_SSVEP_DATA_ROOT),
    )
