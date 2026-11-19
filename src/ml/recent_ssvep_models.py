"""Compact neural models used by the recent-method SSVEP benchmark.

The benchmark deliberately keeps these research-only models separate from the
runtime decoders.  ``SSVEPTFFNet`` follows the layer table in Dai et al. (2025)
while adapting the output width to this project's six classes.  The
``BrainWaveDecoderAdaptation`` preserves the published CNN + bidirectional-GRU
design family; it is explicitly an adaptation because the full layer table and
official implementation were not publicly available when this benchmark was
created.
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn.utils.parametrizations import spectral_norm


def _sn_conv1d(*args, **kwargs) -> nn.Conv1d:
    return spectral_norm(nn.Conv1d(*args, **kwargs))


def _sn_conv2d(*args, **kwargs) -> nn.Conv2d:
    return spectral_norm(nn.Conv2d(*args, **kwargs))


def _sn_linear(*args, **kwargs) -> nn.Linear:
    return spectral_norm(nn.Linear(*args, **kwargs))


class _Conv2dNormAct(nn.Sequential):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size,
        *,
        stride=1,
    ) -> None:
        super().__init__(
            _sn_conv2d(
                in_channels,
                out_channels,
                kernel_size,
                stride=stride,
                bias=False,
            ),
            nn.BatchNorm2d(out_channels),
            nn.PReLU(out_channels),
        )


class _TemporalNet(nn.Module):
    """Time-domain branch from the published SSVEP-TFFNet layer table."""

    def __init__(self, channels: int, feature_length: int, dropout: float) -> None:
        super().__init__()
        self.spatial_temporal = nn.Sequential(
            _Conv2dNormAct(1, 16, (channels, 1)),
            _Conv2dNormAct(16, 32, (1, 10), stride=(1, 2)),
            nn.Dropout(dropout),
        )
        self.temporal_spatial = nn.Sequential(
            _Conv2dNormAct(1, 16, (1, 10), stride=(1, 2)),
            _Conv2dNormAct(16, 32, (channels, 1)),
            nn.Dropout(dropout),
        )
        self.forward_conv = _sn_conv1d(32, 32, 3, padding=1, bias=False)
        self.reverse_conv = _sn_conv1d(32, 32, 3, padding=1, bias=False)
        self.fusion = nn.Sequential(
            _sn_conv1d(32, 32, 1, bias=False),
            nn.BatchNorm1d(32),
            nn.PReLU(32),
            nn.Dropout(dropout),
        )
        self.pool = nn.AdaptiveAvgPool1d(feature_length)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        image = values.unsqueeze(1)
        fused = (
            self.spatial_temporal(image) + self.temporal_spatial(image)
        ).squeeze(2)
        forward = self.forward_conv(fused)
        reverse = torch.flip(
            self.reverse_conv(torch.flip(fused, dims=(-1,))), dims=(-1,)
        )
        return self.pool(self.fusion(forward + reverse))


class _ChannelAttention(nn.Module):
    def __init__(self, channels: int = 32, reduction: int = 4) -> None:
        super().__init__()
        hidden = max(1, channels // reduction)
        self.shared = nn.Sequential(
            _sn_conv1d(channels, hidden, 1),
            nn.PReLU(hidden),
            _sn_conv1d(hidden, channels, 1),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        average = values.mean(dim=-1, keepdim=True)
        maximum = values.amax(dim=-1, keepdim=True)
        weights = torch.sigmoid(self.shared(average) + self.shared(maximum))
        return values * weights


class _SpectralNet(nn.Module):
    """Complex-spectrum branch from the published SSVEP-TFFNet layer table."""

    def __init__(self, channels: int, dropout: float) -> None:
        super().__init__()
        self.spatial_spectral = nn.Sequential(
            _Conv2dNormAct(1, 16, (channels, 1)),
            _Conv2dNormAct(16, 32, (1, 10), stride=(1, 2)),
            _Conv2dNormAct(32, 32, (1, 35), stride=(1, 2)),
            nn.Dropout(dropout),
        )
        self.spectral_spatial = nn.Sequential(
            _Conv2dNormAct(1, 16, (1, 10), stride=(1, 2)),
            _Conv2dNormAct(16, 32, (channels, 1)),
            _Conv2dNormAct(32, 32, (1, 35), stride=(1, 2)),
            nn.Dropout(dropout),
        )
        self.attention = _ChannelAttention(32)

    def forward(self, spectrum: torch.Tensor) -> torch.Tensor:
        image = spectrum.unsqueeze(1)
        fused = self.spatial_spectral(image) + self.spectral_spatial(image)
        return self.attention(fused.squeeze(2))


class SSVEPTFFNet(nn.Module):
    """Time-frequency fusion network adapted to eight channels and six classes.

    The frequency input is the concatenated real and imaginary FFT spectrum in
    the half-open interval [7, 64) Hz.  A five-second, 250-Hz input therefore
    has exactly 285 bins per component and the paper's 570-value input width.
    """

    def __init__(
        self,
        channels: int = 8,
        classes: int = 6,
        feature_length: int = 124,
        dropout: float = 0.5,
    ) -> None:
        super().__init__()
        self.channels = int(channels)
        self.classes = int(classes)
        self.feature_length = int(feature_length)
        self.temporal = _TemporalNet(channels, feature_length, dropout)
        self.spectral = _SpectralNet(channels, dropout)
        self.gate = nn.Sequential(
            _sn_linear(2, 8),
            nn.GELU(),
            _sn_linear(8, 2),
        )
        flattened = 32 * feature_length
        self.classifier = nn.Sequential(
            nn.Flatten(),
            _sn_linear(flattened, 198),
            nn.LayerNorm(198),
            nn.PReLU(198),
            nn.Dropout(dropout),
            _sn_linear(198, classes),
        )

    def forward(
        self, temporal: torch.Tensor, complex_spectrum: torch.Tensor
    ) -> torch.Tensor:
        time_features = self.temporal(temporal)
        frequency_features = self.spectral(complex_spectrum)
        if time_features.shape != frequency_features.shape:
            raise ValueError(
                "SSVEP-TFFNet temporal and spectral features must have the same shape"
            )
        pair = torch.stack((time_features, frequency_features), dim=-1)
        weights = torch.softmax(self.gate(pair), dim=-1)
        fused = torch.sum(pair * weights, dim=-1)
        return self.classifier(fused)


class BrainWaveDecoderAdaptation(nn.Module):
    """Research adaptation of the 2026 raw-EEG CNN + Bi-GRU design family."""

    def __init__(
        self,
        channels: int = 8,
        classes: int = 6,
        dropout: float = 0.35,
    ) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(channels, 32, 15, padding=7, bias=False),
            nn.BatchNorm1d(32),
            nn.GELU(),
            nn.MaxPool1d(4),
            nn.Dropout(dropout),
            nn.Conv1d(32, 64, 9, padding=4, bias=False),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.MaxPool1d(4),
            nn.Dropout(dropout),
            nn.Conv1d(64, 64, 5, padding=2, bias=False),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.MaxPool1d(2),
        )
        self.recurrent = nn.GRU(
            input_size=64,
            hidden_size=32,
            num_layers=1,
            batch_first=True,
            bidirectional=True,
        )
        self.attention = nn.Linear(64, 1)
        self.classifier = nn.Sequential(
            nn.LayerNorm(64),
            nn.Dropout(dropout),
            nn.Linear(64, classes),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        sequence = self.encoder(values).transpose(1, 2)
        sequence, _ = self.recurrent(sequence)
        weights = torch.softmax(self.attention(sequence).squeeze(-1), dim=-1)
        summary = torch.sum(sequence * weights.unsqueeze(-1), dim=1)
        return self.classifier(summary)


__all__ = ("SSVEPTFFNet", "BrainWaveDecoderAdaptation")
