from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.bci.config import (
    DECISION_STEP_SECONDS,
    SSVEP_CHANNEL_NAMES,
    WINDOW_SECONDS,
)
from src.bci.runtime_decoder import RuntimeIntentDecoder
from src.bci.temporal_gate import ConsecutiveWindowGate
from src.domain import IntentResult
from src.ml.transformer_predictor import TransformerSSVEPPredictor


def result(label: str = "前进", confidence: float = 0.8, accepted: bool = True) -> IntentResult:
    return IntentResult(
        timestamp=1.0,
        label=label,
        confidence=confidence,
        margin=0.4,
        accepted=accepted,
        reason="single window",
        frequency_hz=13.75,
        scores={label: confidence},
        sample_rate_hz=250.0,
        sample_count=500,
        used_channels_1based=tuple(range(1, 9)),
    )


class SequenceDecoder:
    available = True
    backend_status = "test"

    def __init__(self, values) -> None:
        self.values = iter(values)

    def decode(self, _samples, _sample_rate_hz):
        return next(self.values)


class TemporalGateTest(unittest.TestCase):
    def test_requires_two_confident_matching_windows_then_cools_down(self) -> None:
        clock = [10.0]
        gate = ConsecutiveWindowGate(
            SequenceDecoder([result(), result(), result(), result(), result()]),
            min_confidence=0.6,
            required_windows=2,
            cooldown_seconds=0.5,
            clock=lambda: clock[0],
        )
        first = gate.decode(np.ones((8, 500)), 250.0)
        second = gate.decode(np.ones((8, 500)), 250.0)
        during_cooldown = gate.decode(np.ones((8, 500)), 250.0)
        clock[0] = 10.6
        after_cooldown_first = gate.decode(np.ones((8, 500)), 250.0)
        after_cooldown_second = gate.decode(np.ones((8, 500)), 250.0)

        self.assertFalse(first.accepted)
        self.assertIn("1/2", first.reason)
        self.assertTrue(second.accepted)
        self.assertTrue(second.safety_verified)
        self.assertIn("连续 2", second.reason)
        self.assertFalse(during_cooldown.accepted)
        self.assertIn("冷却", during_cooldown.reason)
        self.assertFalse(after_cooldown_first.accepted)
        self.assertTrue(after_cooldown_second.accepted)

    def test_low_confidence_or_changed_label_resets_evidence(self) -> None:
        gate = ConsecutiveWindowGate(
            SequenceDecoder([
                result("前进", 0.8),
                result("前进", 0.4, False),
                result("前进", 0.8),
                result("左转", 0.8),
                result("左转", 0.8),
            ]),
            cooldown_seconds=0.0,
        )
        outputs = [gate.decode(None, 250.0) for _ in range(5)]
        self.assertEqual([False, False, False, False, True], [item.accepted for item in outputs])


class RuntimeDecoderTest(unittest.TestCase):
    def test_switches_live_decoder_and_refuses_unavailable_model(self) -> None:
        class Unavailable:
            available = False
            backend_status = "missing weights"

            def decode(self, *_args):
                raise AssertionError("must not decode")

        baseline = SequenceDecoder([result("起飞")])
        manager = RuntimeIntentDecoder(
            {"FBCCA": baseline, "Transformer-SSVEP": Unavailable()},
            initial="FBCCA",
        )
        self.assertEqual("起飞", manager.decode(None, 250.0).label)
        with self.assertRaisesRegex(RuntimeError, "missing weights"):
            manager.select("Transformer-SSVEP")
        self.assertEqual("FBCCA", manager.active_name)

    def test_missing_assets_are_reported_without_importing_torch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictor = TransformerSSVEPPredictor(root / "missing.pt", root / "missing.json")
        self.assertFalse(predictor.enabled)
        self.assertIn("缺少", predictor.error)


class TransformerConfigurationTest(unittest.TestCase):
    def test_paper_adapted_runtime_profile(self) -> None:
        self.assertEqual(2.0, WINDOW_SECONDS)
        self.assertEqual(0.25, DECISION_STEP_SECONDS)
        self.assertEqual(("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4"), SSVEP_CHANNEL_NAMES)

    @unittest.skipUnless(importlib.util.find_spec("torch"), "torch not installed in base test environment")
    def test_transformer_forward_shape(self) -> None:
        import torch

        from src.ml.transformer_ssvep import TransformerSSVEP

        model = TransformerSSVEP()
        output = model(torch.randn(3, 8, 500))
        self.assertEqual((3, 7), tuple(output.shape))


if __name__ == "__main__":
    unittest.main()
