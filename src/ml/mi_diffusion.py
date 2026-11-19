"""Small MI noise-prediction DDPM with scalar confidence and optional rest FiLM.

Observed EEG is the training target, not clean neural ground truth. Spectral
losses preserve its mu/beta structure; they cannot establish artifact removal.
No MI label is supplied to the network at training or inference.
"""
import math
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from src.ml.ssvep_diffusion import ConditionalBlock


def band_power(x, fs=250.):
    window = torch.hann_window(x.shape[-1], device=x.device)
    psd = torch.fft.rfft((x-x.mean(-1,keepdim=True))*window,dim=-1).abs().square()
    psd = psd / (fs*window.square().sum())
    freq = torch.fft.rfftfreq(x.shape[-1],1/fs,device=x.device)
    return torch.stack([psd[..., (freq>=lo)&(freq<hi)].sum(-1)*fs/x.shape[-1]
                        for lo,hi in ((8,13),(13,30))],dim=-1).clamp_min(1e-8)


def baseline_features(eeg, fs=250.):
    return band_power(torch.as_tensor(np.ascontiguousarray(eeg),dtype=torch.float32),fs).log().flatten(1).numpy()


def condition_values(mode, source, p, valid, groups, seed=20261101):
    p = np.asarray(p,dtype=np.float32).copy(); valid=np.asarray(valid,bool)
    if mode == "none":
        return np.zeros_like(p), np.zeros_like(p)
    if mode not in ("true","shuffled","constant") or source != "actual_preceding_ssvep":
        raise ValueError("Confidence conditioning requires actual paired SSVEP evidence")
    if not valid.all() or not np.isfinite(p).all() or ((p<0)|(p>1)).any():
        raise ValueError("Missing or invalid preceding SSVEP confidence")
    if mode == "constant":
        p[:] = .5
    elif mode == "shuffled":
        rng=np.random.default_rng(seed);groups=np.asarray(groups)
        for group in np.unique(groups):
            idx=np.where(groups==group)[0];p[idx]=rng.permutation(p[idx])
    return p, np.ones_like(p)


class ConfidenceUNet(nn.Module):
    def __init__(self, channels=3, width=8, subject_features=6):
        super().__init__();self.width=width
        emb=width*4
        self.time=nn.Sequential(nn.Linear(width,emb),nn.SiLU(),nn.Linear(emb,emb))
        self.confidence=nn.Sequential(nn.Linear(2,emb),nn.SiLU(),nn.Linear(emb,emb))
        self.subject=nn.Sequential(nn.Linear(subject_features,emb),nn.SiLU(),nn.Linear(emb,emb))
        self.first=ConditionalBlock(channels,width,emb)
        self.down=ConditionalBlock(width,width*2,emb)
        self.middle=ConditionalBlock(width*2,width*2,emb)
        self.up=ConditionalBlock(width*3,width,emb)
        self.out=nn.Conv1d(width,channels,1)

    def forward(self,x,t,p,mask,subject):
        scales=torch.exp(-math.log(10000)*torch.arange(self.width//2,device=x.device)/(self.width//2-1))
        angles=t[:,None].float()*scales
        emb=self.time(torch.cat((angles.sin(),angles.cos()),1))
        emb=emb+self.confidence(torch.stack((p*mask,mask),1))+self.subject(subject)
        skip=self.first(x,emb)
        middle=self.middle(self.down(F.avg_pool1d(skip,2),emb),emb)
        up=F.interpolate(middle,size=x.shape[-1],mode="linear",align_corners=False)
        return self.out(self.up(torch.cat((skip,up),1),emb))


class MIDiffusion(nn.Module):
    def __init__(self, channels=3, steps=32, width=8, fs=250., erd_weight=.05, protect_weight=.02,
                 subject_features=None):
        super().__init__()
        if steps<4 or width<4 or width%4:
            raise ValueError("Invalid MI diffusion configuration")
        self.config=dict(channels=channels,steps=steps,width=width,fs=fs,
                         erd_weight=erd_weight,protect_weight=protect_weight)
        if subject_features is not None:self.config["subject_features"]=int(subject_features)
        self.network=ConfidenceUNet(channels,width,channels*2 if subject_features is None else subject_features)
        beta=torch.linspace(.0001,.02,steps)
        self.register_buffer("alpha_bar",torch.cumprod(1-beta,0))

    def loss(self,x,p,mask,subject,baseline):
        t=torch.randint(len(self.alpha_bar),(len(x),),device=x.device)
        noise=torch.randn_like(x);a=self.alpha_bar[t,None,None]
        noisy=a.sqrt()*x+(1-a).sqrt()*noise
        estimated=self.network(noisy,t,p,mask,subject)
        clean=(noisy-(1-a).sqrt()*estimated)/a.sqrt()
        noise_loss=(estimated-noise).square().mean()
        target_power=band_power(x,self.config["fs"]);power=band_power(clean,self.config["fs"])
        rest_power=band_power(baseline,self.config["fs"])
        # Positive ERD = power decrease relative to measured pre-MI baseline.
        target_erd=(1-target_power/rest_power).clamp(-2,1)
        erd=(1-power/rest_power).clamp(-2,1)
        # Weak observed ERD receives a weaker constraint; this is not an ability diagnosis.
        strength=target_erd.detach().clamp(0,1).mean((1,2))
        erd_loss=((erd-target_erd).square().mean((1,2))*strength).mean()
        protect_loss=(power.log()-target_power.log()).square().mean()
        loss=noise_loss+self.config["erd_weight"]*erd_loss+self.config["protect_weight"]*protect_loss
        return loss,dict(noise=float(noise_loss.detach()),erd=float(erd_loss.detach()),protect=float(protect_loss.detach()))

    @torch.no_grad()
    def single_pass(self,x,p,mask,subject,step=3):
        """One deterministic residual correction; no added noise or reverse loop.

        The noisy observation is evaluated once at a fixed noise level. This
        is a single-step approximation, not equivalent to full DDPM sampling.
        """
        if not 0<=step<len(self.alpha_bar):raise ValueError("Invalid single-pass step")
        t=torch.full((len(x),),step,device=x.device,dtype=torch.long)
        a=self.alpha_bar[step];eps=self.network(x,t,p,mask,subject)
        return x+(a.rsqrt()-1)*x-((1-a)/a).sqrt()*eps

    @torch.no_grad()
    def reconstruct(self,x,p,mask,subject,start_step=3,seed=20261101):
        if not 0<=start_step<len(self.alpha_bar):
            raise ValueError("Invalid reverse diffusion starting step")
        gen=torch.Generator(device=x.device).manual_seed(seed)
        a=self.alpha_bar[start_step]
        state=a.sqrt()*x+(1-a).sqrt()*torch.randn(x.shape,generator=gen,device=x.device,dtype=x.dtype)
        for step in range(start_step,-1,-1):
            t=torch.full((len(x),),step,device=x.device,dtype=torch.long)
            eps=self.network(state,t,p,mask,subject);a=self.alpha_bar[step]
            clean=(state-(1-a).sqrt()*eps)/a.sqrt()
            if step:
                prev=self.alpha_bar[step-1];state=prev.sqrt()*clean+(1-prev).sqrt()*eps
            else:
                state=clean
        return state
