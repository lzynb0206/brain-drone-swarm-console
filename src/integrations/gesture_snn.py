"""DVS128 SNN adapted from demo_drone_cam.py; see README.md for provenance.

Imported only by the camera subprocess. No device access or model loading on import.
"""
import cv2
import numpy as np
import torch
import torch.nn as nn

T = 16
H = W = 128
CLASSES = ("拍手", "右手挥手", "其他任意手势", "左手挥手", "右手顺时针",
           "右手逆时针", "左手顺时针", "左手逆时针", "前臂翻滚", "打鼓", "弹吉他")

class SpikeFn(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, alpha):
        ctx.save_for_backward(x)
        ctx.alpha = alpha
        return (x >= 0).float()

    @staticmethod
    def backward(ctx, grad_output):
        (x,) = ctx.saved_tensors
        alpha = ctx.alpha
        surrogate = alpha / 2.0 / (1.0 + (3.141592653589793 / 2.0 * alpha * x) ** 2)
        return grad_output * surrogate, None


def spike_fn(x, alpha=2.0):
    return SpikeFn.apply(x, alpha)


class LIF(nn.Module):
    def __init__(self, tau=2.0, th=1.0):
        super().__init__()
        self.tau = tau
        self.th = th

    def forward(self, x, v):
        v = v + (x - v) / self.tau
        s = spike_fn(v - self.th)
        v = v * (1.0 - s.detach())
        return s, v


class VotingLayer(nn.Module):
    def __init__(self, voter_num=10):
        super().__init__()
        self.voting = nn.AvgPool1d(voter_num, voter_num)

    def forward(self, x):
        return self.voting(x.unsqueeze(1)).squeeze(1)


class SNN(nn.Module):
    def __init__(self, channels=128):
        super().__init__()
        self.conv1 = nn.Conv2d(2, channels, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(channels)
        self.lif1 = LIF()
        self.pool1 = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(channels)
        self.lif2 = LIF()
        self.pool2 = nn.MaxPool2d(2, 2)
        self.conv3 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn3 = nn.BatchNorm2d(channels)
        self.lif3 = LIF()
        self.pool3 = nn.MaxPool2d(2, 2)
        self.conv4 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn4 = nn.BatchNorm2d(channels)
        self.lif4 = LIF()
        self.pool4 = nn.MaxPool2d(2, 2)
        self.conv5 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn5 = nn.BatchNorm2d(channels)
        self.lif5 = LIF()
        self.pool5 = nn.MaxPool2d(2, 2)
        self.drop1 = nn.Dropout(0.5)
        self.fc1 = nn.Linear(channels * 4 * 4, 512)
        self.lif6 = LIF()
        self.drop2 = nn.Dropout(0.5)
        self.fc2 = nn.Linear(512, 110)
        self.lif7 = LIF()
        self.vote = VotingLayer(10)

    def init_states(self, B, device):
        return {"v1": torch.zeros(B, 128, 128, 128, device=device),
                "v2": torch.zeros(B, 128, 64, 64, device=device),
                "v3": torch.zeros(B, 128, 32, 32, device=device),
                "v4": torch.zeros(B, 128, 16, 16, device=device),
                "v5": torch.zeros(B, 128, 8, 8, device=device),
                "v6": torch.zeros(B, 512, device=device),
                "v7": torch.zeros(B, 110, device=device)}


def frames_to_events(frames, th=12.0):
    """BGR 帧列表 -> [T, 2, 128, 128] 事件帧"""
    gs = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames]
    diffs = [gs[i + 1] - gs[i] for i in range(len(gs) - 1)]
    ev = np.zeros((T, 2, H, W), dtype=np.float32)
    idx = np.linspace(0, len(diffs), T + 1).astype(int)
    for t in range(T):
        for d in diffs[idx[t]:idx[t + 1]]:
            ev[t, 0] += cv2.resize(np.maximum(d - th, 0), (W, H), interpolation=cv2.INTER_AREA)
            ev[t, 1] += cv2.resize(np.maximum(-d - th, 0), (W, H), interpolation=cv2.INTER_AREA)
    return ev


class GestureRecognizer:
    """Same weights, frame differences and voting rates as the supplied demo."""

    def __init__(self, weights):
        import os

        torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.net = SNN().to(self.device)
        self.net.load_state_dict(torch.load(weights, map_location=self.device, weights_only=True))
        self.net.eval()

    def predict(self, frames, threshold=12.0):
        raw = frames_to_events(frames, threshold)
        x = torch.from_numpy(raw).unsqueeze(0).to(self.device)
        net = self.net
        with torch.inference_mode():
            states = net.init_states(1, self.device)
            total = 0.0
            for t in range(T):
                h = x[:, t]
                for layer in range(1, 6):
                    key = f"v{layer}"
                    h = getattr(net, f"bn{layer}")(getattr(net, f"conv{layer}")(h))
                    h, states[key] = getattr(net, f"lif{layer}")(h, states[key])
                    h = getattr(net, f"pool{layer}")(h)
                h, states["v6"] = net.lif6(net.fc1(net.drop1(h.flatten(1))), states["v6"])
                h, states["v7"] = net.lif7(net.fc2(net.drop2(h)), states["v7"])
                total = total + h
            scores = (net.vote(total) / T)[0].cpu().numpy()
        grid = np.zeros((4 * H, 4 * W, 3), dtype=np.uint8)
        normalized = raw / max(float(raw.max()), 1.0)
        for t in range(T):
            row, col = divmod(t, 4)
            cell = grid[row * H:(row + 1) * H, col * W:(col + 1) * W]
            cell[:, :, 2] = (255 * normalized[t, 0]).astype(np.uint8)
            cell[:, :, 0] = (255 * normalized[t, 1]).astype(np.uint8)
        return scores, grid
