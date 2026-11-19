"""Explicit small-data adaptations; never replace online or paper models.

The compact TFF keeps both parallel paths, bidirectional time convolutions,
complex-spectrum attention, and the 124-position fusion grid. Only feature
widths and the dense head shrink. These are project adaptations, not official
published architectures. Original definitions and saved checkpoints are intact.
"""
from __future__ import annotations

import torch
from torch import nn

from src.ml.fb_eegnet import FBEEGNetFusion
from src.ml.recent_ssvep_models import (
    SSVEPTFFNet, BrainWaveDecoderAdaptation, _Conv2dNormAct,
    _ChannelAttention, _sn_conv1d, _sn_linear,
)


class CompactTemporal(nn.Module):
    def __init__(self, channels=8, dropout=.5):
        super().__init__()
        self.spatial_temporal = nn.Sequential(
            _Conv2dNormAct(1, 4, (channels, 1)),
            _Conv2dNormAct(4, 8, (1, 10), stride=(1, 2)), nn.Dropout(dropout))
        self.temporal_spatial = nn.Sequential(
            _Conv2dNormAct(1, 4, (1, 10), stride=(1, 2)),
            _Conv2dNormAct(4, 8, (channels, 1)), nn.Dropout(dropout))
        self.forward_conv = _sn_conv1d(8, 8, 3, padding=1, bias=False)
        self.reverse_conv = _sn_conv1d(8, 8, 3, padding=1, bias=False)
        self.fusion = nn.Sequential(_sn_conv1d(8, 8, 1, bias=False),
            nn.BatchNorm1d(8), nn.PReLU(8), nn.Dropout(dropout))
        self.pool = nn.AdaptiveAvgPool1d(124)

    def forward(self, x):
        image = x.unsqueeze(1)
        x = (self.spatial_temporal(image) + self.temporal_spatial(image)).squeeze(2)
        x = self.forward_conv(x) + self.reverse_conv(x.flip(-1)).flip(-1)
        return self.pool(self.fusion(x))


class CompactSpectral(nn.Module):
    def __init__(self, channels=8, dropout=.5):
        super().__init__()
        self.spatial_spectral = nn.Sequential(
            _Conv2dNormAct(1, 4, (channels, 1)),
            _Conv2dNormAct(4, 8, (1, 10), stride=(1, 2)),
            _Conv2dNormAct(8, 8, (1, 35), stride=(1, 2)), nn.Dropout(dropout))
        self.spectral_spatial = nn.Sequential(
            _Conv2dNormAct(1, 4, (1, 10), stride=(1, 2)),
            _Conv2dNormAct(4, 8, (channels, 1)),
            _Conv2dNormAct(8, 8, (1, 35), stride=(1, 2)), nn.Dropout(dropout))
        self.attention = _ChannelAttention(8)

    def forward(self, x):
        image = x.unsqueeze(1)
        return self.attention((self.spatial_spectral(image) +
                               self.spectral_spatial(image)).squeeze(2))


class CompactTFFNet(nn.Module):
    def __init__(self, channels=8, classes=6, dropout=.5):
        super().__init__()
        self.temporal = CompactTemporal(channels, dropout)
        self.spectral = CompactSpectral(channels, dropout)
        self.gate = nn.Sequential(_sn_linear(2, 8), nn.GELU(), _sn_linear(8, 2))
        self.classifier = nn.Sequential(nn.Flatten(), _sn_linear(8 * 124, 16),
            nn.LayerNorm(16), nn.PReLU(16), nn.Dropout(dropout), _sn_linear(16, classes))

    def forward(self, temporal, spectrum):
        a, b = self.temporal(temporal), self.spectral(spectrum)
        if a.shape != b.shape:
            raise ValueError("TFF requires the fixed 570-wide complex spectrum")
        pair = torch.stack((a, b), dim=-1)
        return self.classifier((pair * self.gate(pair).softmax(-1)).sum(-1))


class CompactCNNBiGRU(nn.Module):
    def __init__(self, channels=8, classes=6, dropout=.5):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(channels, 8, 15, padding=7, bias=False),
            nn.BatchNorm1d(8), nn.GELU(), nn.MaxPool1d(4), nn.Dropout(dropout),
            nn.Conv1d(8, 16, 9, padding=4, bias=False),
            nn.BatchNorm1d(16), nn.GELU(), nn.MaxPool1d(4), nn.Dropout(dropout),
            nn.Conv1d(16, 16, 5, padding=2, bias=False),
            nn.BatchNorm1d(16), nn.GELU(), nn.MaxPool1d(2))
        self.recurrent = nn.GRU(16, 8, batch_first=True, bidirectional=True)
        self.attention = nn.Linear(16, 1)
        self.classifier = nn.Sequential(nn.LayerNorm(16), nn.Dropout(dropout),
                                        nn.Linear(16, classes))

    def forward(self, x):
        seq, _ = self.recurrent(self.encoder(x).transpose(1, 2))
        w = self.attention(seq).squeeze(-1).softmax(-1)
        return self.classifier((seq * w.unsqueeze(-1)).sum(1))


def build_model(method, variant):
    if variant not in ("regularized_original", "compact_regularized"):
        raise ValueError(variant)
    compact = variant == "compact_regularized"
    if method == "SSVEP-TFFNet":
        return CompactTFFNet() if compact else SSVEPTFFNet(dropout=.6)
    if method == "CNN-BiGRU":
        return CompactCNNBiGRU() if compact else BrainWaveDecoderAdaptation(dropout=.5)
    if method == "FB-EEGNet":
        model = FBEEGNetFusion(channels=8, bands=4, classes=6, dropout=.5,
            temporal_filters=4 if compact else 8, output_filters=8 if compact else 16)
        if compact:
            model.classifier = nn.Sequential(nn.Linear(16, 16), nn.LayerNorm(16),
                nn.ELU(), nn.Dropout(.5), nn.Linear(16, 6))
        return model
    raise ValueError(method)


class ValidationStopper:
    """Loss-based stopping: no access to test labels or training accuracy."""
    def __init__(self, patience=15, minimum_epochs=20, min_delta=1e-4):
        self.patience = patience
        self.minimum_epochs = minimum_epochs
        self.min_delta = min_delta
        self.best_loss = float("inf")
        self.best_epoch = 0

    def update(self, epoch, loss):
        if not torch.isfinite(torch.tensor(loss)):
            raise ValueError("Validation loss is not finite")
        improved = float(loss) < self.best_loss - self.min_delta
        if improved:
            self.best_loss, self.best_epoch = float(loss), int(epoch)
        stop = epoch >= self.minimum_epochs and epoch - self.best_epoch >= self.patience
        return improved, stop


def validation_choice(candidates):
    """Predeclared selection: lowest validation NLL, then compactness."""
    return min(candidates, key=lambda x: (x["validation_nll"], x["parameters"],
                                          x["selected_epoch"], x["variant"]))
