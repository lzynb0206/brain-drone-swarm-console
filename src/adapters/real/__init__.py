"""Real and replayable EEG infrastructure adapters."""

from src.adapters.real.csv_eeg_stream import CsvEEGStream
from src.adapters.real.lsl_eeg_stream import LSLEEGStream

__all__ = ["CsvEEGStream", "LSLEEGStream"]
