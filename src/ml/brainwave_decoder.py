"""Independent BrainWaveDecoder reproduction model.

The implementation is kept separate from ``BrainWaveDecoderAdaptation``.  The
published graphical abstract and ablation figure establish five convolutional
stages, feature widths 48 -> 48 -> 85 -> 192 -> 192, a bidirectional GRU with
128 output features, sequence flattening, and a dense classifier.  The paper
does not provide an official code repository and its closed full text was not
available while this reproduction was prepared.  Kernel sizes, normalization,
and activations below are therefore explicit reproduction assumptions rather
than silently claimed paper facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn


@dataclass(frozen=True)
class BrainWaveDecoderProfile:
    """Auditable architecture profile for the independent reproduction."""

    convolution_widths: tuple[int, ...] = (48, 48, 85, 192, 192)
    bigru_output_features: int = 128
    temporal_kernel_size: int = 3
    activation: str = "ReLU"
    normalization: str = "unverified; configurable in the reproduction"
    sequence_reduction: str = "flatten all Bi-GRU time steps"
    verified_from_public_figures: tuple[str, ...] = (
        "five convolutional stages",
        "feature widths 48, 48, 85, 192, 192",
        "bidirectional GRU",
        "128-dimensional Bi-GRU output",
        "flattened Bi-GRU output shape 1 x (192 * samples) x 128",
        "flatten followed by a dense classifier",
    )
    reproduction_assumptions: tuple[str, ...] = (
        "same-padded temporal kernels with a configured odd length",
        "first convolution uses a temporal-by-3 time-channel kernel",
        "second convolution spans all EEG channels",
        "normalization choice and ReLU after convolutional stages",
        "64 hidden units per GRU direction",
        "one shared Bi-GRU is reused for all 192 feature maps with recurrent state reset at each map",
    )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalization_2d(name: str, channels: int) -> nn.Module:
    if name == "batch":
        return nn.BatchNorm2d(channels)
    if name == "group":
        return nn.GroupNorm(1, channels)
    if name == "none":
        return nn.Identity()
    raise ValueError(f"unknown normalization {name!r}")


def _normalization_1d(name: str, channels: int) -> nn.Module:
    if name == "batch":
        return nn.BatchNorm1d(channels)
    if name == "group":
        return nn.GroupNorm(1, channels)
    if name == "none":
        return nn.Identity()
    raise ValueError(f"unknown normalization {name!r}")


class _ConvNormAct2d(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int, kernel_size, padding=0, *, normalization: str):
        super().__init__(
            nn.Conv2d(
                in_channels,
                out_channels,
                kernel_size=kernel_size,
                padding=padding,
                bias=False,
            ),
            _normalization_2d(normalization, out_channels),
            nn.ReLU(inplace=True),
        )


class _ConvNormAct1d(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int, *, normalization: str):
        super().__init__(
            nn.Conv1d(
                in_channels,
                out_channels,
                kernel_size=kernel_size,
                padding=kernel_size // 2,
                bias=False,
            ),
            _normalization_1d(normalization, out_channels),
            nn.ReLU(inplace=True),
        )


class BrainWaveDecoder(nn.Module):
    """Five-convolution CNN + Bi-GRU reproduction of BrainWaveDecoder.

    Input is raw EEG with shape ``(batch, channels, samples)``.  No filter
    bank, FFT, template, or learned attention pooling is applied.  A fixed
    input scale may be used purely as a unit conversion; it is not estimated
    from train or test data.
    """

    def __init__(
        self,
        *,
        channels: int,
        samples: int,
        classes: int,
        input_scale: float = 1.0e-4,
        normalization: str = "batch",
        temporal_kernel_size: int = 3,
        gru_layout: str = "per_feature_map",
    ) -> None:
        super().__init__()
        if channels < 1 or samples < 1 or classes < 2:
            raise ValueError("channels/samples/classes must be positive")
        self.channels = int(channels)
        self.samples = int(samples)
        self.classes = int(classes)
        self.input_scale = float(input_scale)
        self.normalization = normalization
        kernel = int(temporal_kernel_size)
        if kernel < 1 or kernel % 2 == 0:
            raise ValueError("temporal_kernel_size must be a positive odd integer")
        self.temporal_kernel_size = kernel
        if gru_layout not in {"time_features", "feature_maps", "per_feature_map"}:
            raise ValueError(
                "gru_layout must be 'time_features', 'feature_maps', or 'per_feature_map'"
            )
        self.gru_layout = gru_layout

        # The image uses time x channel.  Conv1 preserves both axes and Conv2
        # collapses the physical-channel axis, matching the published shapes.
        self.conv1 = _ConvNormAct2d(
            1, 48, (kernel, 3), (kernel // 2, 1), normalization=normalization
        )
        self.conv2 = _ConvNormAct2d(
            48, 48, (1, self.channels), normalization=normalization
        )
        self.conv3 = _ConvNormAct1d(48, 85, kernel, normalization=normalization)
        self.conv4 = _ConvNormAct1d(85, 192, kernel, normalization=normalization)
        self.conv5 = _ConvNormAct1d(192, 192, kernel, normalization=normalization)
        gru_input_size = {
            "time_features": 192,
            "feature_maps": self.samples,
            "per_feature_map": 1,
        }[gru_layout]
        self.bigru = nn.GRU(
            input_size=gru_input_size,
            hidden_size=64,
            num_layers=1,
            batch_first=True,
            bidirectional=True,
        )
        sequence_length = {
            "time_features": self.samples,
            "feature_maps": 192,
            "per_feature_map": 192 * self.samples,
        }[gru_layout]
        self.classifier = nn.Linear(sequence_length * 128, self.classes)

    @property
    def profile(self) -> dict[str, object]:
        profile = BrainWaveDecoderProfile().to_dict()
        profile["normalization"] = self.normalization
        profile["temporal_kernel_size"] = self.temporal_kernel_size
        profile["gru_layout"] = self.gru_layout
        return profile

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        if values.ndim != 3:
            raise ValueError("BrainWaveDecoder expects (batch, channels, samples)")
        if values.shape[1:] != (self.channels, self.samples):
            raise ValueError(
                f"expected (*,{self.channels},{self.samples}), got {tuple(values.shape)}"
            )
        # Conv2 must span channels, so arrange the image as time x channels.
        x = values.float().mul(self.input_scale).transpose(1, 2).unsqueeze(1)
        x = self.conv1(x)
        x = self.conv2(x).squeeze(-1)
        x = self.conv3(x)
        x = self.conv4(x)
        x = self.conv5(x)
        if self.gru_layout == "time_features":
            gru_input = x.transpose(1, 2)
            sequence, _ = self.bigru(gru_input)
        elif self.gru_layout == "feature_maps":
            sequence, _ = self.bigru(x)
        else:
            # The public figure shows one Bi-GRU path per one of the 192
            # feature maps and a final 1 x (192 * samples) x 128 tensor.  A
            # shared GRU over a batch*feature reshape reproduces that geometry
            # while resetting recurrent state at each feature-map boundary.
            batch = x.shape[0]
            gru_input = x.reshape(batch * 192, self.samples, 1)
            sequence, _ = self.bigru(gru_input)
            sequence = sequence.reshape(batch, 192 * self.samples, 128)
        return self.classifier(sequence.flatten(start_dim=1))


__all__ = ("BrainWaveDecoder", "BrainWaveDecoderProfile")
