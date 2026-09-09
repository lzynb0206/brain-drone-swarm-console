"""Shared settings for CSV replay, LSL acquisition and FBCCA decoding."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 传承蜂群六频配置。刺激界面、FBCCA 参考信号和标签映射只读取这一份配置。
SSVEP_PROFILE_NAME = "传承蜂群六频"
COMMANDS = ("起飞", "前进", "后退", "左转", "右转", "切换队形")
TARGET_FREQUENCIES_HZ = (11.0, 13.75, 15.0, 16.5, 18.33, 20.625)
SWARM_EXTRA_FREQUENCY_HZ = TARGET_FREQUENCIES_HZ[-1]
TRUE_LABELS = ("空闲",) + COMMANDS
LABEL_TO_FREQUENCY = dict(zip(COMMANDS, TARGET_FREQUENCIES_HZ))

# 没有新的带标签训练集、权重和 metadata 前必须保持关闭。
FB_EEGNET_ENABLED = os.getenv("BDS_FB_EEGNET_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
FB_EEGNET_MODEL_PATH = Path(
    os.getenv("BDS_FB_EEGNET_MODEL", str(PROJECT_ROOT / "models" / "fb_eegnet_fusion.pt"))
).expanduser()
FB_EEGNET_METADATA_PATH = Path(
    os.getenv("BDS_FB_EEGNET_METADATA", str(PROJECT_ROOT / "models" / "fb_eegnet_metadata.json"))
).expanduser()

SAMPLE_RATE_HZ = float(os.getenv("BDS_EEG_SAMPLE_RATE", "250"))
FRAME_SIZE = int(os.getenv("BDS_EEG_FRAME_SIZE", "32"))
WINDOW_SECONDS = float(os.getenv("BDS_EEG_WINDOW_SECONDS", "4"))
DECISION_STEP_SECONDS = float(os.getenv("BDS_EEG_DECISION_STEP_SECONDS", "1"))
SSVEP_CHANNELS_1BASED = (2, 7)

FBCCA_MIN_SCORE = float(os.getenv("BDS_FBCCA_MIN_SCORE", "0.06"))
FBCCA_MIN_MARGIN = float(os.getenv("BDS_FBCCA_MIN_MARGIN", "0.01"))

EEG_BACKEND = os.getenv("BDS_EEG_BACKEND", "csv").strip().lower()
EEG_CSV_PATH = Path(
    os.getenv("BDS_EEG_CSV_PATH", str(PROJECT_ROOT / "data" / "samples" / "start-1.csv"))
).expanduser()
LSL_STREAM_NAME = os.getenv("BDS_LSL_STREAM_NAME", "obci_eeg1")
LSL_STREAM_TYPE = os.getenv("BDS_LSL_STREAM_TYPE", "EEG")
LSL_RESOLVE_TIMEOUT_SECONDS = float(os.getenv("BDS_LSL_TIMEOUT", "3"))

DATA_ROOT = Path(
    os.getenv("BDS_EEG_DATA_ROOT", str(PROJECT_ROOT / "data"))
).expanduser()
RECORDING_ROOT = DATA_ROOT
STIMULUS_RECORDING_ROOT = DATA_ROOT / "stimulus_logs"
