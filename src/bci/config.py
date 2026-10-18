"""Shared settings for acquisition and SSVEP decoding."""

from __future__ import annotations

import json
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

# 第三篇论文所用的枕区/顶枕区八通道顺序。硬件、LSL metadata、训练 metadata
# 必须保持相同顺序。通用 CH1..CH8 只能按固定接线位置映射，原标签会保留，
# 正式自动实验开始前还必须由操作者确认真实电极位置。
SSVEP_CHANNEL_NAMES = ("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4")
# 少通道个人化方案：硬件仍输出 8 路，FBCCA/个人模板只使用
# 枕区和顶枕区的 Oz、PO3、PO4（CH2/CH4/CH6）。2026-10-18 的 60 轮现场校准中，
# 这三路跨试次可用率分别为 100%/85%/98%；O1 仅 7%，不再把它默认送入解码。
SSVEP_CHANNELS_1BASED = (2, 4, 6)

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


def _personal_template_matches_layout() -> bool:
    if not PERSONAL_TEMPLATE_MODEL_PATH.is_file() or not PERSONAL_TEMPLATE_METADATA_PATH.is_file():
        return False
    try:
        metadata = json.loads(PERSONAL_TEMPLATE_METADATA_PATH.read_text(encoding="utf-8"))
        return (
            metadata.get("model_version") == "personal_template_ssvep_v5"
            and metadata.get("stimulus_layout") == SSVEP_STIMULUS_LAYOUT
            and metadata.get("calibration_condition") == "isolated_single_target_v1"
        )
    except Exception:
        return False


# Only a template collected with the exact current target geometry becomes the
# local default. Older full-screen calibration remains on disk but is never
# silently applied to the new six-target layout.
_DEFAULT_INTENT_DECODER = "personal-template" if _personal_template_matches_layout() else "fbcca"
INTENT_DECODER = os.getenv("BDS_INTENT_DECODER", _DEFAULT_INTENT_DECODER).strip().lower()
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
