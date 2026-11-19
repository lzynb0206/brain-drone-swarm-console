"""Window-local CSP + shrinkage LDA with an explicit channel/task contract."""
from pathlib import Path
import json
import numpy as np
from scipy import linalg, signal
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis

BCI2A_CHANNELS = ("Fz", "FC3", "FC1", "FCz", "FC2", "FC4", "C5", "C3", "C1", "Cz", "C2",
                  "C4", "C6", "CP3", "CP1", "CPz", "CP2", "CP4", "P1", "Pz", "P2", "POz")
MI_CHANNELS = ("C3", "Cz", "C4")


def preprocess_mi(eeg, fs):
    x = np.asarray(eeg, dtype=float)
    if x.ndim != 3 or x.shape[-1] < int(fs) or not np.isfinite(x).all() or fs <= 60:
        raise ValueError("MI data must be finite trials × channels × samples, at least one second")
    x = signal.detrend(x, axis=-1, type="linear")
    sos = signal.butter(4, (8, 30), fs=fs, btype="bandpass", output="sos")
    return np.ascontiguousarray(signal.sosfiltfilt(sos, x, axis=-1, padlen=min(x.shape[-1]-1, round(.5*fs))))


class CSPClassifier:
    def __init__(self, fs=250., channel_names=MI_CHANNELS, task_labels=("想象左手", "想象右手")):
        self.fs = float(fs)
        self.channel_names = tuple(channel_names)
        self.task_labels = tuple(task_labels)

    def _validate(self, x):
        x = np.asarray(x, float)
        if x.ndim != 3 or x.shape[1] != len(self.channel_names) or not np.isfinite(x).all():
            raise ValueError("MI model channel/data contract mismatch")
        return x

    def fit(self, eeg, y, *, prepared=False):
        x = self._validate(eeg)
        self.sample_count = x.shape[-1]
        x = x if prepared else preprocess_mi(x, self.fs)
        y = np.asarray(y, int)
        self.classes = np.unique(y)
        if len(y) != len(x) or len(self.classes) < 2 or any(np.sum(y==c)<2 for c in self.classes):
            raise ValueError("Need at least two examples in each MI class")
        cov = np.einsum("nct,ndt->ncd", x, x)
        cov /= np.maximum(np.trace(cov, axis1=1, axis2=2)[:,None,None], 1e-12)
        identity = np.eye(x.shape[1]) / x.shape[1]
        cov = .9 * cov + .1 * identity
        filters = []
        for cls in (self.classes[:1] if len(self.classes)==2 else self.classes):
            a,b = cov[y==cls].mean(0),cov[y!=cls].mean(0)
            _, vectors = linalg.eigh(a, a+b)
            filters.extend((vectors[:,0], vectors[:,-1]))
        self.filters = np.stack(filters)
        features = self.features(x)
        lda = LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto").fit(features,y)
        self.coef = lda.coef_; self.intercept = lda.intercept_
        return self

    def features(self, prepared):
        projected = np.einsum("fc,nct->nft", self.filters, prepared)
        variance = np.var(projected, axis=-1)
        return np.log(np.maximum(variance / np.maximum(variance.sum(1,keepdims=True), 1e-12),1e-12))

    def predict(self, eeg, *, prepared=False):
        if getattr(self,"metadata",{}).get("provenance",{}).get("requires_diffusion") and not prepared:
            raise ValueError("This classifier requires its matching diffusion preprocessing")
        x = self._validate(eeg)
        if x.shape[-1] != self.sample_count:
            raise ValueError("MI model window length mismatch")
        x = x if prepared else preprocess_mi(x,self.fs)
        decision = self.features(x) @ self.coef.T + self.intercept
        return self.classes[(decision[:,0] > 0).astype(int)] if len(self.classes)==2 else self.classes[decision.argmax(1)]

    def save(self, path, provenance):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        metadata={"schema":1,"method":"CSP_shrinkage_LDA","sample_rate":self.fs,
                  "sample_count":self.sample_count,"channel_names":self.channel_names,
                  "task_labels":self.task_labels,"preprocessing":"window_local_detrend_butter4_8_30_pad0.5s",
                  "research_only":True,"provenance":provenance}
        np.savez_compressed(path,filters=self.filters,coef=self.coef,intercept=self.intercept,
                            classes=self.classes,metadata=json.dumps(metadata,ensure_ascii=False))

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as p:
            meta=json.loads(str(p["metadata"].item()))
            if meta.get("schema")!=1 or meta.get("method")!="CSP_shrinkage_LDA":
                raise ValueError("Unsupported MI artifact")
            model=cls(meta["sample_rate"],meta["channel_names"],meta["task_labels"])
            model.sample_count=int(meta["sample_count"])
            for attr in ("filters","coef","intercept","classes"):
                setattr(model,attr,p[attr].copy())
            model.metadata=meta
        return model
