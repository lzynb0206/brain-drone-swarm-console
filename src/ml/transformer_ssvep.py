"""Frequency-patch Transformer used by the SSVEP reproduction baseline."""

from __future__ import annotations

import torch
from torch import nn


class TransformerSSVEP(nn.Module):
    """Classify an ``(N, channels, samples)`` SSVEP window.

    The two-second window is split into non-overlapping temporal patches.  Each
    patch is converted to log FFT magnitudes, projected to a token, and fused by
    a Transformer encoder plus a learnable classification token.
    """

    def __init__(
        self,
        channels: int = 8,
        sample_count: int = 500,
        patch_count: int = 4,
        classes: int = 7,
        embedding_dim: int = 128,
        encoder_layers: int = 2,
        attention_heads: int = 8,
        feedforward_dim: int = 256,
        classifier_hidden_dim: int = 64,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.channels = int(channels)
        self.sample_count = int(sample_count)
        self.patch_count = int(patch_count)
        self.classes = int(classes)
        if self.channels < 1 or self.classes < 2:
            raise ValueError("Transformer requires at least one channel and two classes")
        if self.patch_count < 1 or self.sample_count % self.patch_count:
            raise ValueError("sample_count must be divisible by patch_count")
        if embedding_dim % attention_heads:
            raise ValueError("embedding_dim must be divisible by attention_heads")

        self.patch_size = self.sample_count // self.patch_count
        fft_bins = self.patch_size // 2 + 1
        self.input_projection = nn.Sequential(
            nn.Linear(self.channels * fft_bins, embedding_dim),
            nn.LayerNorm(embedding_dim),
        )
        self.class_token = nn.Parameter(torch.zeros(1, 1, embedding_dim))
        self.position_embedding = nn.Parameter(
            torch.zeros(1, self.patch_count + 1, embedding_dim)
        )
        self.embedding_dropout = nn.Dropout(dropout)
        layer = nn.TransformerEncoderLayer(
            d_model=embedding_dim,
            nhead=attention_heads,
            dim_feedforward=feedforward_dim,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer, num_layers=encoder_layers, enable_nested_tensor=False
        )
        self.final_norm = nn.LayerNorm(embedding_dim)
        self.classifier = nn.Sequential(
            nn.Linear(embedding_dim, classifier_hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(classifier_hidden_dim, self.classes),
        )
        nn.init.trunc_normal_(self.class_token, std=0.02)
        nn.init.trunc_normal_(self.position_embedding, std=0.02)

    def forward(self, values):
        if values.ndim != 3:
            raise ValueError("Expected EEG with shape (batch, channels, samples)")
        if values.shape[1] != self.channels or values.shape[2] != self.sample_count:
            raise ValueError(
                f"Expected (*, {self.channels}, {self.sample_count}), got {tuple(values.shape)}"
            )
        # unfold: (N,C,P,L); permute so every temporal patch becomes one token.
        patches = values.unfold(-1, self.patch_size, self.patch_size).permute(0, 2, 1, 3)
        spectrum = torch.log1p(torch.abs(torch.fft.rfft(patches, dim=-1)))
        mean = spectrum.mean(dim=-1, keepdim=True)
        scale = spectrum.std(dim=-1, keepdim=True, unbiased=False).clamp_min(1e-6)
        spectrum = (spectrum - mean) / scale
        tokens = self.input_projection(spectrum.flatten(start_dim=2))
        class_token = self.class_token.expand(values.shape[0], -1, -1)
        tokens = torch.cat((class_token, tokens), dim=1)
        tokens = self.embedding_dropout(tokens + self.position_embedding)
        encoded = self.encoder(tokens)
        return self.classifier(self.final_norm(encoded[:, 0]))
