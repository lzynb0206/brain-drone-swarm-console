"""Explicit resting descriptors and training-calibrated MI physics objectives."""
import numpy as np
from scipy import signal
import torch
from torch.nn import functional as F
from src.ml.mi_diffusion import band_power


class RestDescriptor:
    """mu/beta log power and aperiodic slope; no invented EOG or impedance."""
    def __init__(self,fs=250.):self.fs=float(fs)

    def features(self,eeg):
        x=np.asarray(eeg,float)
        if x.ndim!=3 or x.shape[1]!=3 or x.shape[-1]<self.fs or not np.isfinite(x).all():
            raise ValueError("Rest descriptor needs finite C3/Cz/C4 baseline epochs")
        freq,power=signal.welch(signal.detrend(x,axis=-1),fs=self.fs,nperseg=min(round(2*self.fs),x.shape[-1]),axis=-1)
        floor=np.maximum(power.mean(-1,keepdims=True)*1e-8,1e-12);logp=np.log(power+floor)
        use=(freq>=4)&(freq<=40)&~((freq>=8)&(freq<=30))
        design=np.stack((np.ones(use.sum()),np.log(freq[use])),1)
        slope=(logp[...,use]@np.linalg.pinv(design).T)[...,1]
        df=freq[1]-freq[0]
        mu=np.log(np.maximum(power[...,(freq>=8)&(freq<13)].sum(-1)*df,1e-12))
        beta=np.log(np.maximum(power[...,(freq>=13)&(freq<30)].sum(-1)*df,1e-12))
        return np.stack((mu,beta,slope),-1).reshape(len(x),9)

    def fit(self,training_baselines):
        f=self.features(training_baselines);self.center=f.mean(0);self.scale=f.std(0).clip(.1);return self

    def transform(self,eeg):return ((self.features(eeg)-self.center)/self.scale).astype(np.float32)

    def metadata(self):
        return dict(fs=self.fs,center=self.center.tolist(),scale=self.scale.tolist(),
            features=[f"{channel}_{name}" for channel in ("C3","Cz","C4") for name in ("log_mu","log_beta","aperiodic_slope")],
            EOG_template_available=False,impedance_available=False,ERD_latency_inferred_from_rest=False)


def calibrate_erd(training_mi,training_baseline,labels,oof_accuracy,fs=250.):
    """Use labelled training calibration for contralateral ERD, never test MI."""
    y=np.asarray(labels,int)
    if not set(np.unique(y))=={0,1}:raise ValueError("ERD calibration here is for left/right imagery only")
    power=band_power(torch.tensor(training_mi,dtype=torch.float32),fs).numpy()
    baseline=band_power(torch.tensor(training_baseline,dtype=torch.float32),fs).numpy()
    erd=np.clip(1-power/baseline,-2,1)
    target=np.stack([np.median(erd[y==label,contra],axis=0) for label,contra in ((0,2),(1,0))])
    reliability=float(np.clip((float(oof_accuracy)-.5)/.5,0,1))
    return dict(target_contralateral_erd=target.tolist(),strength=reliability,
        ability_source="training-only grouped OOF accuracy",oof_accuracy=float(oof_accuracy),
        class_to_contralateral_channel=[2,0],classes=["left_hand","right_hand"],
        ability_is_clinical_diagnosis=False)


def calibrated_diffusion_loss(model,x,p,p_mask,subject,baseline,y,calibration,physics=True):
    t=torch.randint(len(model.alpha_bar),(len(x),),device=x.device)
    noise=torch.randn_like(x);a=model.alpha_bar[t,None,None]
    noisy=a.sqrt()*x+(1-a).sqrt()*noise
    eps=model.network(noisy,t,p,p_mask,subject);clean=(noisy-(1-a).sqrt()*eps)/a.sqrt()
    noise_loss=F.mse_loss(eps,noise)
    if not physics:return noise_loss,{"noise":float(noise_loss.detach()),"erd":0.,"protect":0.}
    power=band_power(clean,model.config["fs"]);base=band_power(baseline,model.config["fs"])
    erd=(1-power/base).clamp(-2,1)
    idx=torch.arange(len(x),device=x.device);contra=torch.tensor([2,0],device=x.device)[y]
    target=torch.tensor(calibration["target_contralateral_erd"],dtype=x.dtype,device=x.device)[y]
    erd_loss=F.smooth_l1_loss(erd[idx,contra],target)*calibration["strength"]
    # Protect the document's exact 8–15 Hz band against excessive attenuation.
    def protected_power(v):
        win=torch.hann_window(v.shape[-1],device=v.device)
        psd=torch.fft.rfft((v-v.mean(-1,keepdim=True))*win,dim=-1).abs().square()
        freq=torch.fft.rfftfreq(v.shape[-1],1/model.config["fs"],device=v.device)
        return psd[...,(freq>=8)&(freq<=15)].sum(-1).clamp_min(1e-8)
    ratio=protected_power(clean)/protected_power(x).detach()
    protect=F.relu(np.log(.8)-ratio.log()).square().mean()
    loss=noise_loss+model.config["erd_weight"]*erd_loss+model.config["protect_weight"]*protect
    return loss,dict(noise=float(noise_loss.detach()),erd=float(erd_loss.detach()),protect=float(protect.detach()))
