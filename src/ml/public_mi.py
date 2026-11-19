"""Official BNCI2014-001 reader; MAT trial indices mark trial onset, cue is +2s."""
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from src.ml.mi_baseline import BCI2A_CHANNELS


def load_bnci_session(path, seconds=3.):
    data=loadmat(Path(path),squeeze_me=True,struct_as_record=False)["data"]
    epochs=[];labels=[];groups=[];artifacts=[];baselines=[]
    for run_index,run in enumerate(np.atleast_1d(data)):
        onsets=np.atleast_1d(run.trial).astype(int)
        y=np.atleast_1d(run.y).astype(int)
        if not len(onsets):
            continue
        fs=float(run.fs)
        if fs!=250 or run.X.shape[1]!=25 or len(onsets)!=len(y):
            raise ValueError("Unexpected official BCI IV-2a format")
        bad=np.atleast_1d(getattr(run,"artifacts",np.zeros(len(y)))).astype(int)
        for j,(onset,label) in enumerate(zip(onsets,y)):
            start=int(onset)-1
            cue=start+round(2*fs)
            stop=cue+round(seconds*fs)
            if label not in (1,2,3,4) or start<0 or stop>len(run.X):
                raise ValueError("MI label or epoch bounds invalid")
            epochs.append(np.asarray(run.X[cue:stop,:22],float).T)
            baselines.append(np.asarray(run.X[start:cue,:22],float).T)
            labels.append(label-1);groups.append(run_index);artifacts.append(bool(bad[j]))
    if not epochs:
        raise ValueError("No labelled MI trials")
    return {"eeg":np.stack(epochs),"y":np.array(labels),"run":np.array(groups),
            "artifact_flag":np.array(artifacts),"baseline":np.stack(baselines),
            "fs":250.,"channel_names":BCI2A_CHANNELS}
