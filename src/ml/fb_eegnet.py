"""Compact filter-bank EEGNet fusion network."""

from __future__ import annotations

import torch
from torch import nn


class EEGNetEncoder(nn.Module):
    def __init__(
        self, channels: int, temporal_filters: int = 8, depth_multiplier: int = 2,
        output_filters: int = 16, time_kernel: int = 63, dropout: float = 0.4,
    ) -> None:
        super().__init__()
        depth_filters = temporal_filters * depth_multiplier
        self.features = nn.Sequential(
            nn.Conv2d(1, temporal_filters, (1, time_kernel), padding=(0, time_kernel // 2), bias=False),
            nn.BatchNorm2d(temporal_filters),
            nn.Conv2d(temporal_filters, depth_filters, (channels, 1), groups=temporal_filters, bias=False),
            nn.BatchNorm2d(depth_filters), nn.ELU(), nn.AvgPool2d((1, 4)), nn.Dropout(dropout),
            nn.Conv2d(depth_filters, depth_filters, (1, 15), padding=(0, 7), groups=depth_filters, bias=False),
            nn.Conv2d(depth_filters, output_filters, (1, 1), bias=False),
            nn.BatchNorm2d(output_filters), nn.ELU(), nn.AvgPool2d((1, 8)), nn.Dropout(dropout),
            nn.AdaptiveAvgPool2d((1, 1)),
        )

    def forward(self, values):
        return self.features(values).flatten(start_dim=1)


class FBEEGNetFusion(nn.Module):
    def __init__(
        self, channels: int = 8, bands: int = 4, classes: int = 7,
        temporal_filters: int = 8, depth_multiplier: int = 2,
        output_filters: int = 16, dropout: float = 0.4,
    ) -> None:
        super().__init__()
        self.channels, self.bands, self.classes = int(channels), int(bands), int(classes)
        encoder_args = (channels, temporal_filters, depth_multiplier, output_filters)
        self.frequency_encoder = EEGNetEncoder(*encoder_args, dropout=dropout)
        self.spatial_temporal_encoder = EEGNetEncoder(*encoder_args, dropout=dropout)
        self.band_attention = nn.Sequential(
            nn.Linear(output_filters, max(1, output_filters // 2)), nn.Tanh(),
            nn.Linear(max(1, output_filters // 2), 1),
        )
        self.classifier = nn.Sequential(
            nn.Linear(output_filters * 2, 64), nn.LayerNorm(64), nn.ELU(),
            nn.Dropout(dropout), nn.Linear(64, classes),
        )

    def forward(self, clean, filter_bank):
        if clean.ndim != 3 or filter_bank.ndim != 4:
            raise ValueError("Expected clean (N,C,T) and filter_bank (N,B,C,T)")
        batch, bands, channels, samples = filter_bank.shape
        if bands != self.bands or channels != self.channels:
            raise ValueError("Input dimensions do not match model metadata")
        features = self.frequency_encoder(filter_bank.reshape(batch * bands, 1, channels, samples))
        features = features.reshape(batch, bands, -1)
        weights = torch.softmax(self.band_attention(features).squeeze(-1), dim=1)
        frequency_features = torch.sum(features * weights.unsqueeze(-1), dim=1)
        temporal_features = self.spatial_temporal_encoder(clean.unsqueeze(1))
        return self.classifier(torch.cat((frequency_features, temporal_features), dim=1))
