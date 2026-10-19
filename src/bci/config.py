"""Shared settings for acquisition and SSVEP decoding."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 六频单机控制配置。刺激界面、FBCCA 参考信号和标签映射只读取这一份配置。
SSVEP_PROFILE_NAME = "学长原版参数复现 · 8–13 Hz"
SSVEP_STIMULUS_LAYOUT = "legacy_large_six_target_v1"
COMMANDS = ("起飞", "前进", "后退", "左移", "右移", "降落")
TARGET_FREQUENCIES_HZ = (8.0, 9.0, 10.0, 11.0, 12.0, 13.0)
TRUE_LABELS = ("空闲",) + COMMANDS
MI_TRUE_LABELS = ("静息", "想象左手", "想象右手", "想象双脚")
LABEL_TO_FREQUENCY = dict(zip(COMMANDS, TARGET_FREQUENCIES_HZ))

# 2026-10-19 起使用两路额极眼电代理＋六路后脑 SSVEP。CH1/CH8 只用于
# 去眼电，不进入 FBCCA；GND/REF 仍是设备地和参考，绝不能当成 Fp1/Fp2。
# 通用 CH1..CH8 只能按这个固定接线顺序映射，原标签会保留在 metadata。
SSVEP_CHANNEL_NAMES = ("Fp1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "Fp2")
OCULAR_REFERENCE_CHANNELS_1BASED = (1, 8)
SSVEP_CHANNELS_1BASED = (2, 3, 4, 5, 6, 7)
SSVEP_DECODING_CHANNEL_NAMES = tuple(
    SSVEP_CHANNEL_NAMES[index - 1] for index in SSVEP_CHANNELS_1BASED
)

# 没有新的带标签训练集、权重和 metadata 前必须保持关闭。
FB_EEGNET_ENABLED = os.getenv("BDS_FB_EEGNET_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
FB_EEGNET_MODEL_PATH = Path(
    os.getenv("BDS_FB_EEGNET_MODEL", str(PROJECT_ROOT / "models" / "fb_eegnet_fusion.pt"))
).expanduser()
FB_EEGNET_METADATA_PATH = Path(
    os.getenv("BDS_FB_EEGNET_METADATA", str(PROJECT_ROOT / "models" / "fb_eegnet_metadata.json"))
).expanduser()

PERSONAL_TEMPLATE_MODEL_PATH = Path(
    os.getenv(
        "BDS_PERSONAL_TEMPLATE_MODEL",
        str(PROJECT_ROOT / "models" / "personal_template_ssvep_experimental.npz"),
    )
).expanduser()
PERSONAL_TEMPLATE_METADATA_PATH = Path(
    os.getenv(
        "BDS_PERSONAL_TEMPLATE_METADATA",
        str(PROJECT_ROOT / "models" / "personal_template_ssvep_experimental_metadata.json"),
    )
).expanduser()
# A local model file must never silently become the flight-control default.
# Personal/neural models remain selectable for research, but require explicit
# operator selection and independent production approval before hardware use.
INTENT_DECODER = os.getenv("BDS_INTENT_DECODER", "fbcca").strip().lower()
TRANSFORMER_MODEL_PATH = Path(
    os.getenv(
        "BDS_TRANSFORMER_MODEL",
        str(PROJECT_ROOT / "models" / "transformer_ssvep.pt"),
    )
).expanduser()
TRANSFORMER_METADATA_PATH = Path(
    os.getenv(
        "BDS_TRANSFORMER_METADATA",
        str(PROJECT_ROOT / "models" / "transformer_ssvep_metadata.json"),
    )
).expanduser()
TRANSFORMER_MIN_CONFIDENCE = float(os.getenv("BDS_TRANSFORMER_MIN_CONFIDENCE", "0.60"))
TRANSFORMER_REQUIRED_WINDOWS = int(os.getenv("BDS_TRANSFORMER_REQUIRED_WINDOWS", "2"))
TRANSFORMER_COOLDOWN_SECONDS = float(os.getenv("BDS_TRANSFORMER_COOLDOWN_SECONDS", "0.50"))

SAMPLE_RATE_HZ = float(os.getenv("BDS_EEG_SAMPLE_RATE", "250"))
FRAME_SIZE = int(os.getenv("BDS_EEG_FRAME_SIZE", "8"))
WINDOW_SECONDS = float(os.getenv("BDS_EEG_WINDOW_SECONDS", "4"))
DECISION_STEP_SECONDS = float(os.getenv("BDS_EEG_DECISION_STEP_SECONDS", "0.25"))

FBCCA_MIN_SCORE = float(os.getenv("BDS_FBCCA_MIN_SCORE", "0.35"))
FBCCA_MIN_MARGIN = float(os.getenv("BDS_FBCCA_MIN_MARGIN", "0.06"))
# Calibrate while all targets flicker and the operator looks at the centre.
# A noisy baseline must raise rejection thresholds, never be capped down.
FBCCA_IDLE_CALIBRATION_SECONDS = float(
    os.getenv("BDS_FBCCA_IDLE_CALIBRATION_SECONDS", "12.0")
)

# Normal startup waits for the live LSL stream.  CSV is an explicit experiment
# mode and must never start looping merely because a bundled sample exists.
EEG_BACKEND = os.getenv("BDS_EEG_BACKEND", "lsl").strip().lower()
EEG_CSV_PATH = Path(
    os.getenv("BDS_EEG_CSV_PATH", str(PROJECT_ROOT / "data" / "samples" / "start-1.csv"))
).expanduser()
LSL_STREAM_NAME = os.getenv("BDS_LSL_STREAM_NAME", "obci_eeg1")
LSL_STREAM_TYPE = os.getenv("BDS_LSL_STREAM_TYPE", "EEG")
LSL_RESOLVE_TIMEOUT_SECONDS = float(os.getenv("BDS_LSL_TIMEOUT", "3"))
# Optional exact port for the soldier one-click SSVEP workflow.  When empty,
# the UI auto-selects a unique ExpandableEEG Bluetooth/serial descriptor.
SOLDIER_EEG_PORT = os.getenv("BDS_SOLDIER_EEG_PORT", "").strip()

DATA_ROOT = Path(
    os.getenv("BDS_EEG_DATA_ROOT", str(PROJECT_ROOT / "data"))
).expanduser()
RECORDING_ROOT = DATA_ROOT
STIMULUS_RECORDING_ROOT = DATA_ROOT / "stimulus_logs"
