"""Research FBCSP + shrinkage LDA; all spatial/feature fitting uses training data.

Inspired by Ang et al. (2008/2012), with ANOVA feature selection instead of
their mutual-information selector. This is a declared variant, not a replica.
"""
import json
from pathlib import Path
import numpy as np
from scipy import linalg,signal,special
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.feature_selection import f_classif

BANDS=((8,12),(12,16),(16,20),(20,24),(24,28),(28,32))


def filter_mi_banks(eeg,fs=250.):
    x=np.asarray(eeg,float)
    if x.ndim!=3 or x.shape[-1]<fs or not np.isfinite(x).all():
        raise ValueError("Expected finite MI epochs at least one second long")
    x=signal.detrend(x,axis=-1)
    return np.stack([signal.sosfiltfilt(signal.butter(4,b,fs=fs,btype="bandpass",output="sos"),x,axis=-1,
        padlen=min(x.shape[-1]-1,round(.5*fs))) for b in BANDS],axis=1)


class FilterBankCSP:
    def __init__(self,k=8,fs=250.,channel_names=("C3","Cz","C4")):
        self.k=int(k);self.fs=float(fs);self.channel_names=tuple(channel_names)

    def fit(self,eeg,y,*,prepared=False):
        x=np.asarray(eeg) if prepared else filter_mi_banks(eeg,self.fs)
        if x.ndim!=4 or x.shape[1]!=len(BANDS) or x.shape[2]!=len(self.channel_names):
            raise ValueError("FBCSP channel/band contract mismatch")
        y=np.asarray(y,int);self.classes=np.unique(y);self.samples=x.shape[-1]
        if len(y)!=len(x) or len(self.classes)<2 or any((y==c).sum()<2 for c in self.classes):
            raise ValueError("Insufficient MI training classes")
        matrices=[]
        for band in x.transpose(1,0,2,3):
            cov=np.einsum("nct,ndt->ncd",band,band)
            cov/=np.maximum(np.trace(cov,axis1=1,axis2=2)[:,None,None],1e-12)
            cov=.9*cov+.1*np.eye(band.shape[1])/band.shape[1]
            filters=[]
            for cls in (self.classes[:1] if len(self.classes)==2 else self.classes):
                ca,cb=cov[y==cls].mean(0),cov[y!=cls].mean(0)
                _,vectors=linalg.eigh(ca,ca+cb)
                n=min(2,band.shape[1]//2)
                filters.extend(vectors[:,j] for j in [*range(n),*range(-n,0)])
            matrices.append(np.stack(filters))
        self.filters=np.stack(matrices)
        features=self.features(x)
        f,_=f_classif(features,y);f=np.nan_to_num(f,nan=0.,posinf=1e12)
        count=features.shape[1] if self.k<=0 else min(self.k,features.shape[1])
        self.selected=np.argsort(-f,kind="stable")[:count]
        lda=LinearDiscriminantAnalysis(solver="lsqr",shrinkage="auto").fit(features[:,self.selected],y)
        self.coef=lda.coef_;self.intercept=lda.intercept_
        return self

    def features(self,bands):
        projected=np.einsum("bfc,nbct->nbft",self.filters,bands)
        variance=projected.var(-1)
        return np.log(np.maximum(variance/np.maximum(variance.sum(-1,keepdims=True),1e-12),1e-12)).reshape(len(bands),-1)

    def predict_proba(self,eeg,*,prepared=False):
        x=np.asarray(eeg) if prepared else filter_mi_banks(eeg,self.fs)
        if x.ndim!=4 or x.shape[1:3]!=(len(BANDS),len(self.channel_names)) or x.shape[-1]!=self.samples:
            raise ValueError("FBCSP channel/window contract mismatch")
        z=self.features(x)[:,self.selected]@self.coef.T+self.intercept
        if len(self.classes)==2:
            p=special.expit(z[:,0]);return np.column_stack((1-p,p))
        return special.softmax(z,axis=1)

    def predict(self,eeg,*,prepared=False):
        return self.classes[self.predict_proba(eeg,prepared=prepared).argmax(1)]

    def save(self,path,provenance):
        meta=dict(schema=1,kind="research_FBCSP_ANOVA_LDA",fs=self.fs,samples=self.samples,
            names=self.channel_names,k=self.k,bands=BANDS,provenance=provenance)
        np.savez_compressed(path,metadata=json.dumps(meta),filters=self.filters,selected=self.selected,
            coef=self.coef,intercept=self.intercept,classes=self.classes)

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as z:
            meta=json.loads(str(z["metadata"].item()))
            if meta.get("kind")!="research_FBCSP_ANOVA_LDA" or tuple(map(tuple,meta["bands"]))!=BANDS:
                raise ValueError("Unsupported filter-bank model")
            model=cls(meta["k"],meta["fs"],meta["names"]);model.samples=meta["samples"]
            for key in ("filters","selected","coef","intercept","classes"):setattr(model,key,z[key].copy())
        return model
