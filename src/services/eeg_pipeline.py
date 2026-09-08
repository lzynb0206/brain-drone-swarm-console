"""Compatibility import; the Qt worker now lives in ``src.workers``."""

from src.workers.eeg_worker import EEGPipelineWorker, EEGWorker

__all__ = ["EEGPipelineWorker", "EEGWorker"]
