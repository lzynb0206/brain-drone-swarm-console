"""Lazy, metadata-validated runtime predictor for Transformer-SSVEP."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.bci.config import SSVEP_CHANNEL_NAMES, TRUE_LABELS


class TransformerSSVEPPredictor:
    def __init__(self, model_path: Path | str, metadata_path: Path | str, device: str = "cpu") -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.metadata_path = Path(metadata_path).expanduser().resolve()
        self.device_name = str(device)
        self.metadata: dict[str, object] = {}
        self.model = None
        self.torch = None
        self.error = ""
        self.load()

    @property
    def enabled(self) -> bool:
        return self.model is not None and self.torch is not None

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.metadata.get("labels", ()))

    @property
    def channel_names(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.metadata.get("channel_names", ()))

    def load(self) -> bool:
        self.model = None
        self.torch = None
        if not self.model_path.is_file() or not self.metadata_path.is_file():
            self.error = (
                "缺少 Transformer-SSVEP 权重或 metadata；"
                "请先采集带标签试次并运行训练命令"
            )
            return False
        try:
            import torch

            from src.ml.transformer_ssvep import TransformerSSVEP

            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            model_args = dict(metadata["model_args"])
            labels = tuple(str(value) for value in metadata["labels"])
            if len(labels) != int(model_args["classes"]):
                raise ValueError("metadata labels count does not match model classes")
            if labels != tuple(TRUE_LABELS):
                raise ValueError(f"metadata labels must be {TRUE_LABELS}, got {labels}")
            channel_names = tuple(str(value) for value in metadata.get("channel_names", ()))
            if channel_names != tuple(SSVEP_CHANNEL_NAMES):
                raise ValueError(
                    f"metadata channel order must be {SSVEP_CHANNEL_NAMES}, got {channel_names}"
                )
            model = TransformerSSVEP(**model_args)
            checkpoint = torch.load(
                self.model_path, map_location=self.device_name, weights_only=True
            )
            model.load_state_dict(checkpoint.get("state_dict", checkpoint))
            model.to(self.device_name).eval()
            self.metadata = metadata
            self.torch, self.model, self.error = torch, model, ""
            return True
        except Exception as exc:
            self.error = f"Transformer-SSVEP 加载失败：{exc}"
            self.model = None
            self.torch = None
            return False

    def predict(self, eeg, fs: float) -> dict[str, object]:
        if not self.enabled:
            raise RuntimeError(self.error or "Transformer-SSVEP 未启用")
        from src.ml.preprocessing import preprocess_transformer_trial

        model_args = dict(self.metadata["model_args"])
        processed = preprocess_transformer_trial(
            eeg,
            fs,
            target_fs=float(self.metadata.get("sample_rate", 250.0)),
            target_samples=int(model_args["sample_count"]),
            expected_channels=int(model_args["channels"]),
        )
        torch = self.torch
        values = torch.from_numpy(processed["clean"]).unsqueeze(0).to(self.device_name)
        with torch.no_grad():
            probabilities = torch.softmax(self.model(values), dim=1).cpu().numpy()[0]
        order = np.argsort(probabilities)[::-1]
        best, second = int(order[0]), int(order[1])
        confidence = float(probabilities[best])
        margin = confidence - float(probabilities[second])
        clipped = np.clip(probabilities, 1e-12, 1.0)
        entropy = float(-np.sum(clipped * np.log(clipped)))
        thresholds = dict(self.metadata.get("gate_thresholds", {}))
        min_confidence = float(thresholds.get("min_confidence", 0.60))
        label = self.labels[best]
        return {
            "label": label,
            "confidence": confidence,
            "margin": margin,
            "entropy": entropy,
            "accepted": confidence >= min_confidence and label != "空闲",
            "scores": {
                label_name: float(value)
                for label_name, value in zip(self.labels, probabilities)
            },
            "quality_mask": np.asarray(processed["quality_mask"], dtype=int).tolist(),
            "sample_count": int(processed["target_samples"]),
        }
