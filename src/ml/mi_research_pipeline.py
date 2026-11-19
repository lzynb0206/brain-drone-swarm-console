"""Offline research inference only: fixed preprocessing, one pass, explicit P provenance."""
from pathlib import Path
import numpy as np
import torch
from scipy.special import expit, softmax
from src.ml.mi_baseline import CSPClassifier, preprocess_mi, MI_CHANNELS
from src.ml.mi_diffusion import MIDiffusion
from src.ml.mi_research import RestDescriptor


class MIResearchPipeline:
    def __init__(self, directory):
        directory=Path(directory)
        self.checkpoint=torch.load(directory/"diffusion.pt",map_location="cpu",weights_only=True)
        c=self.checkpoint
        if tuple(c["channels"])!=MI_CHANNELS or c["fs"]!=250.:
            raise ValueError("Expected 250 Hz C3/Cz/C4 research model")
        self.model=MIDiffusion(**c["config"]);self.model.load_state_dict(c["state_dict"]);self.model.eval()
        self.classifier=CSPClassifier.load(directory/"classifier.npz")
        self.mean=np.asarray(c["mean"]);self.scale=np.asarray(c["scale"])
        self.rest=RestDescriptor(c["fs"]);self.rest.center=np.asarray(c["rest_encoder"]["center"])
        self.rest.scale=np.asarray(c["rest_encoder"]["scale"])
        self.condition=c.get("condition","none")

    def prepare_rest(self, rest):
        return self.rest.transform(rest)

    def transform(self, raw, *, rest_features=None, preceding_p=None, p_source=None, force_strength=None):
        c=self.checkpoint;x=preprocess_mi(raw,c["fs"]);n=len(x)
        if x.shape[1:]!=(3,c["samples"]):raise ValueError("MI model channel or window length mismatch")
        strength=c["strength"] if force_strength is None else float(force_strength)
        if not 0<=strength<=1:raise ValueError("Residual strength must be in [0,1]")
        p=np.zeros(n,np.float32);mask=p.copy()
        if self.condition!="none":
            if p_source!="actual_preceding_ssvep" or preceding_p is None:
                raise ValueError("Actual earlier SSVEP evidence is required")
            p=np.asarray(preceding_p,np.float32)
            if p.shape!=(n,) or not np.isfinite(p).all() or ((p<0)|(p>1)).any():
                raise ValueError("Invalid preceding SSVEP confidence")
            if self.condition=="constant":p=np.full(n,.5,np.float32)
            # Shuffled control shuffles training P only. Inference uses actual P.
            mask=np.ones(n,np.float32)
        if c.get("uses_rest",c["variant"]=="physics_rest"):
            features=np.asarray(rest_features,np.float32)
            if features.shape!=(n,9) or not np.isfinite(features).all():raise ValueError("Earlier measured rest features required")
        else:features=np.zeros((n,9),np.float32)
        if strength==0:return x
        z=torch.tensor((x-self.mean)/self.scale,dtype=torch.float32)
        result=self.model.single_pass(z,torch.tensor(p),torch.tensor(mask),torch.tensor(features),step=c["step"]).numpy()
        return x+strength*(result*self.scale+self.mean-x)

    def predict(self, raw, **kwargs):
        return self.classifier.predict(self.transform(raw,**kwargs),prepared=True)

    def scores(self, raw, **kwargs):
        """LDA soft scores; these are not calibrated probabilities of correctness."""
        x=self.transform(raw,**kwargs);clf=self.classifier
        d=clf.features(x)@clf.coef.T+clf.intercept
        if len(clf.classes)==2:
            p=expit(d[:,0]);return np.column_stack((1-p,p))
        return softmax(d,axis=1)
