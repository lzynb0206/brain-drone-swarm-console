"""Offline frequency-conditioned DDPM with a log-spectrum background loss.

DDPM equations follow Ho et al., https://arxiv.org/abs/2006.11239.
The spectral constraint is a research implementation, not a claim to reproduce
a published EEG model or to guarantee physiological realism.
"""
from __future__ import annotations

import math

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


def standardize_trials(eeg, fs=250.0):
    from src.bci.preprocessing import remove_linear_drift, notch_mains, fft_bandpass
    values = np.asarray(eeg, dtype=np.float64)
    if values.ndim != 3 or not np.isfinite(values).all():
        raise ValueError("Expected finite trial x channel x time EEG")
    output = []
    for trial in values:
        filtered = fft_bandpass(notch_mains(remove_linear_drift(trial), fs), fs, 6., 50.)
        rms = float(np.sqrt(np.mean(filtered ** 2)))
        if rms < 1e-8:
            raise ValueError("Flat trial cannot be used for diffusion training")
        output.append(filtered / rms)
    return np.asarray(output, dtype=np.float32)


class SpectralFidelity(nn.Module):
    """Target PSD above a fitted A/f**exponent background, measured in dB.

    Fit log(power) = intercept + slope*log(f) over 6.5--45 Hz, excluding
    every candidate fundamental/harmonic by at least one DFT bin. The same
    excluded bins are used for all labels; the requested target is never fit.
    A dB margin is an engineering criterion, not a statistical p-value.
    """

    def __init__(self, samples=500, fs=250.0, frequencies=(8., 9., 10., 11., 12., 13.),
                 margin_db=3.0):
        super().__init__()
        if samples < 100 or fs <= 100 or not frequencies:
            raise ValueError("Unsupported spectral window")
        f = torch.fft.rfftfreq(samples, 1. / fs)
        targets = torch.tensor(frequencies, dtype=torch.float32)
        # Inputs have a 6--50 Hz bandpass. Fitting the removed 4--6 Hz bins
        # would pull down the background and artificially inflate target SNR.
        mask = (f >= 6.5) & (f <= 45)
        for frequency in frequencies:
            for harmonic in range(1, 5):
                mask &= torch.abs(f - frequency * harmonic) > max(fs / samples, 0.5)
        if mask.sum() < 8:
            raise ValueError("Too few unmasked bins for the 1/f background fit")
        fit_x = torch.stack((torch.ones(int(mask.sum())), torch.log(f[mask])), dim=1)
        target_x = torch.stack((torch.ones(len(targets)), torch.log(targets)), dim=1)
        self.register_buffer("fit_indices", torch.where(mask)[0])
        self.register_buffer("fit_operator", torch.linalg.pinv(fit_x))
        self.register_buffer("target_design", target_x)
        self.register_buffer("target_indices", torch.argmin(torch.abs(f[None] - targets[:, None]), dim=1))
        self.register_buffer("window", torch.hann_window(samples))
        self.margin_db = float(margin_db)

    def excess_db(self, eeg):
        power = torch.fft.rfft((eeg - eeg.mean(-1, keepdim=True)) * self.window, dim=-1).abs().square().mean(1)
        floor = power.mean(-1, keepdim=True).clamp_min(1e-12) * 1e-8
        log_power = torch.log(power + floor)
        coefficients = log_power[:, self.fit_indices] @ self.fit_operator.T
        fitted = coefficients @ self.target_design.T
        return (log_power[:, self.target_indices] - fitted) * (10. / math.log(10.))

    def forward(self, eeg, labels):
        excess = self.excess_db(eeg)
        selected = excess.gather(1, labels[:, None]).squeeze(1)
        return F.relu(self.margin_db - selected) / 10.


class ConditionalBlock(nn.Module):
    def __init__(self, in_channels, out_channels, embedding_size):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, out_channels, 5, padding=2)
        self.norm = nn.GroupNorm(4, out_channels)
        self.film = nn.Linear(embedding_size, 2 * out_channels)
        self.conv2 = nn.Conv1d(out_channels, out_channels, 5, padding=2)
        self.skip = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else nn.Identity()

    def forward(self, x, embedding):
        hidden = self.norm(self.conv1(x))
        scale, bias = self.film(embedding).chunk(2, dim=1)
        hidden = hidden * (1 + scale[..., None]) + bias[..., None]
        return self.skip(x) + self.conv2(F.silu(hidden))


class FrequencyUNet(nn.Module):
    def __init__(self, channels=6, width=24, frequencies=(8., 9., 10., 11., 12., 13.), fs=250.):
        super().__init__()
        self.channels, self.width, self.fs = channels, width, fs
        self.register_buffer("frequencies", torch.tensor(frequencies))
        embedding = width * 4
        self.label_embedding = nn.Embedding(len(frequencies), embedding)
        self.time_embedding = nn.Sequential(nn.Linear(width, embedding), nn.SiLU(), nn.Linear(embedding, embedding))
        self.first = ConditionalBlock(channels + 6, width, embedding)
        self.down = ConditionalBlock(width, width * 2, embedding)
        self.middle = ConditionalBlock(width * 2, width * 2, embedding)
        self.up = ConditionalBlock(width * 3, width, embedding)
        self.last = nn.Conv1d(width, channels, 1)

    def forward(self, x, timesteps, labels):
        scales = torch.exp(-math.log(10000) * torch.arange(self.width // 2, device=x.device) / max(self.width // 2 - 1, 1))
        angles = timesteps[:, None].float() * scales
        embedding = self.time_embedding(torch.cat((angles.sin(), angles.cos()), 1)) + self.label_embedding(labels)
        time = torch.arange(x.shape[-1], device=x.device) / self.fs
        phases = 2 * math.pi * self.frequencies[labels, None, None] * torch.arange(1, 4, device=x.device)[None, :, None] * time
        references = torch.cat((phases.sin(), phases.cos()), dim=1)
        skip = self.first(torch.cat((x, references), dim=1), embedding)
        down = self.down(F.avg_pool1d(skip, 2), embedding)
        middle = self.middle(down, embedding)
        up = F.interpolate(middle, size=x.shape[-1], mode="linear", align_corners=False)
        return self.last(self.up(torch.cat((skip, up), dim=1), embedding))


class FrequencyDDPM(nn.Module):
    def __init__(self, channels=6, samples=500, fs=250., frequencies=(8., 9., 10., 11., 12., 13.),
                 steps=100, width=24, physics_weight=0.1, margin_db=3.):
        super().__init__()
        if steps < 2 or width < 4 or width % 4:
            raise ValueError("Invalid diffusion schedule/network width")
        self.config = dict(channels=channels, samples=samples, fs=fs, frequencies=list(frequencies),
                           steps=steps, width=width, physics_weight=physics_weight, margin_db=margin_db)
        self.network = FrequencyUNet(channels, width, frequencies, fs)
        self.spectral = SpectralFidelity(samples, fs, frequencies, margin_db)
        t = torch.linspace(0, steps, steps + 1) / steps
        cumulative = torch.cos((t + .008) / 1.008 * math.pi / 2).square()
        cumulative /= cumulative[0].clone()
        beta = (1 - cumulative[1:] / cumulative[:-1]).clamp(.0001, .999)
        self.register_buffer("beta", beta)
        self.register_buffer("alpha", 1 - beta)
        self.register_buffer("alpha_bar", torch.cumprod(1 - beta, 0))

    def loss(self, clean, labels, generator=None):
        steps = torch.randint(len(self.beta), (len(clean),), device=clean.device, generator=generator)
        noise = torch.randn(clean.shape, device=clean.device, generator=generator)
        alpha = self.alpha_bar[steps, None, None]
        noisy = alpha.sqrt() * clean + (1 - alpha).sqrt() * noise
        estimate = self.network(noisy, steps, labels)
        mse = F.mse_loss(estimate, noise)
        reconstructed = (noisy - (1 - alpha).sqrt() * estimate) / alpha.sqrt()
        # Suppress the ill-conditioned x0 estimate near pure noise; do not
        # force a strong invented peak into every high-noise training sample.
        physics = (self.spectral(reconstructed, labels) * alpha.flatten()).mean()
        return mse + self.config["physics_weight"] * physics, mse.detach(), physics.detach()

    @torch.no_grad()
    def sample(self, labels, generator=None):
        self.eval()
        values = torch.randn((len(labels), self.config["channels"], self.config["samples"]),
                             device=self.beta.device, generator=generator)
        for step in reversed(range(len(self.beta))):
            t = torch.full((len(labels),), step, device=values.device, dtype=torch.long)
            noise_estimate = self.network(values, t, labels)
            abar = self.alpha_bar[step]
            previous = self.alpha_bar[step - 1] if step else torch.ones_like(abar)
            clean = ((values - (1 - abar).sqrt() * noise_estimate) / abar.sqrt()).clamp(-5., 5.)
            mean = (previous.sqrt() * self.beta[step] / (1 - abar) * clean
                    + self.alpha[step].sqrt() * (1 - previous) / (1 - abar) * values)
            if step:
                variance = self.beta[step] * (1 - previous) / (1 - abar)
                values = mean + variance.sqrt() * torch.randn(values.shape, device=values.device, generator=generator)
            else:
                values = mean
        return values
