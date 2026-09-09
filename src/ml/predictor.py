"""Lazy and metadata-validated FB-EEGNet runtime predictor."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class FBEEGNetPredictor:
    def __init__(self, model_path: Path | str, metadata_path: Path | str, device: str = "cpu") -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.metadata_path = Path(metadata_path).expanduser().resolve()
        self.device_name = device
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
        return tuple(str(value) for value in self.metadata.get("labels", []))

    def load(self) -> bool:
        if not self.model_path.is_file() or not self.metadata_path.is_file():
            self.error = "缺少 FB-EEGNet 权重或 metadata；请先采集真实试次并训练"
            return False
        try:
            import torch
            from src.ml.fb_eegnet import FBEEGNetFusion

            self.metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            model_args = dict(self.metadata["model_args"])
            model = FBEEGNetFusion(**model_args)
            checkpoint = torch.load(self.model_path, map_location=self.device_name, weights_only=True)
            model.load_state_dict(checkpoint.get("state_dict", checkpoint))
            model.to(self.device_name).eval()
            self.torch, self.model, self.error = torch, model, ""
            return True
        except Exception as exc:
            self.error = str(exc)
            self.torch, self.model = None, None
            return False

    def predict(self, eeg, fs: float) -> dict[str, object]:
        if not self.enabled:
            raise RuntimeError(self.error or "FB-EEGNet 未启用")
        from src.ml.preprocessing import preprocess_trial

        preprocess_kwargs = {
            "target_fs": float(self.metadata.get("sample_rate", 250.0)),
            "target_samples": int(self.metadata.get("sample_count", 1000)),
            "expected_channels": int(dict(self.metadata["model_args"])["channels"]),
        }
        if self.metadata.get("filter_banks"):
            preprocess_kwargs["filter_banks"] = self.metadata["filter_banks"]
        processed = preprocess_trial(
            eeg, fs,
            **preprocess_kwargs,
        )
        torch = self.torch
        clean = torch.from_numpy(processed["clean"]).unsqueeze(0).to(self.device_name)
        banks = torch.from_numpy(processed["filter_bank"]).unsqueeze(0).to(self.device_name)
        with torch.no_grad():
            probabilities = torch.softmax(self.model(clean, banks), dim=1).cpu().numpy()[0]
        order = np.argsort(probabilities)[::-1]
        best, second = int(order[0]), int(order[1])
        thresholds = dict(self.metadata.get("gate_thresholds", {}))
        confidence = float(probabilities[best])
        margin = confidence - float(probabilities[second])
        entropy = float(-np.sum(np.clip(probabilities, 1e-12, 1.0) * np.log(np.clip(probabilities, 1e-12, 1.0))))
        accepted = (
            confidence >= float(thresholds.get("min_confidence", 0.65))
            and margin >= float(thresholds.get("min_margin", 0.10))
            and entropy <= float(thresholds.get("max_entropy", 1.4))
        )
        return {
            "label": self.labels[best], "confidence": confidence, "margin": margin,
            "entropy": entropy, "accepted": accepted,
            "scores": {label: float(value) for label, value in zip(self.labels, probabilities)},
            "quality_mask": np.asarray(processed["quality_mask"], dtype=int).tolist(),
        }
