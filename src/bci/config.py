"""Shared settings for acquisition and SSVEP decoding."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 六频单机控制配置。刺激界面、FBCCA 参考信号和标签映射只读取这一份配置。
SSVEP_PROFILE_NAME = "单机 Flow 六频"
COMMANDS = ("起飞", "前进", "后退", "左移", "右移", "降落")
TARGET_FREQUENCIES_HZ = (11.0, 13.75, 15.0, 16.5, 18.33, 20.625)
TRUE_LABELS = ("空闲",) + COMMANDS
MI_TRUE_LABELS = ("静息", "想象左手", "想象右手", "想象双脚")
LABEL_TO_FREQUENCY = dict(zip(COMMANDS, TARGET_FREQUENCIES_HZ))

# 第三篇论文所用的枕区/顶枕区八通道顺序。硬件、LSL metadata、训练 metadata
# 必须保持相同顺序。通用 CH1..CH8 只能按固定接线位置映射，原标签会保留，
# 正式自动实验开始前还必须由操作者确认真实电极位置。
SSVEP_CHANNEL_NAMES = ("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4")
SSVEP_CHANNELS_1BASED = tuple(range(1, len(SSVEP_CHANNEL_NAMES) + 1))

# 没有新的带标签训练集、权重和 metadata 前必须保持关闭。
FB_EEGNET_ENABLED = os.getenv("BDS_FB_EEGNET_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
FB_EEGNET_MODEL_PATH = Path(
    os.getenv("BDS_FB_EEGNET_MODEL", str(PROJECT_ROOT / "models" / "fb_eegnet_fusion.pt"))
).expanduser()
FB_EEGNET_METADATA_PATH = Path(
    os.getenv("BDS_FB_EEGNET_METADATA", str(PROJECT_ROOT / "models" / "fb_eegnet_metadata.json"))
).expanduser()

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
FRAME_SIZE = int(os.getenv("BDS_EEG_FRAME_SIZE", "32"))
WINDOW_SECONDS = float(os.getenv("BDS_EEG_WINDOW_SECONDS", "2"))
DECISION_STEP_SECONDS = float(os.getenv("BDS_EEG_DECISION_STEP_SECONDS", "0.25"))

FBCCA_MIN_SCORE = float(os.getenv("BDS_FBCCA_MIN_SCORE", "0.06"))
FBCCA_MIN_MARGIN = float(os.getenv("BDS_FBCCA_MIN_MARGIN", "0.01"))
# Online control performs one short eyes-open idle calibration for the current
# operator/session.  The learned thresholds are deliberately capped so a noisy
# calibration cannot make the controls impossible to trigger.
FBCCA_IDLE_CALIBRATION_SECONDS = float(
    os.getenv("BDS_FBCCA_IDLE_CALIBRATION_SECONDS", "3.0")
)
FBCCA_AUTO_MAX_SCORE = float(os.getenv("BDS_FBCCA_AUTO_MAX_SCORE", "0.12"))
FBCCA_AUTO_MAX_MARGIN = float(os.getenv("BDS_FBCCA_AUTO_MAX_MARGIN", "0.03"))

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
