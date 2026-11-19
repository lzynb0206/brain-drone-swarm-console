"""External replication of the fixed 3-channel CSP on later within-person imagery runs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import numpy as np

from download_physionet import DATA
from run_mi import HERE, CSPClassifier, csp_right, metrics, write_table

# Previously documented recording issues, selected without viewing accuracy.
EXCLUDE = {88, 92, 100, 104}
EVENT = re.compile(rb'([+-][\d.]+)\x15([\d.]+)\x14T([012])\x14')


def read_edf(path):
    """Read the continuous PhysioNet EDF+C format, preserving annotation onsets."""
    raw = path.read_bytes()
    nchan = int(raw[252:256])
    n_header = int(raw[184:192])
    sec = float(raw[244:252])
    nrecords = int(raw[236:244])
    if raw[192:197] != b'EDF+C' or sec != 1 or n_header != 256*(nchan+1):
        raise ValueError(f'Unsupported EDF layout in {path.name}')

    def field(offset, size, convert):
        start = 256 + offset*nchan
        return [convert(raw[start+i*size:start+(i+1)*size].decode('ascii').strip()) for i in range(nchan)]

    names = field(0, 16, lambda s:s.rstrip('.').upper())
    channels = [names.index(c) for c in ('C3','CZ','C4')]
    annot = names.index('EDF ANNOTATIONS')
    units = field(96,8,str)
    phys_min = field(104,8,float)
    phys_max = field(112,8,float)
    dig_min = field(120,8,float)
    dig_max = field(128,8,float)
    lengths = field(216,8,int)
    bounds = np.cumsum([0,*lengths])
    samples = np.frombuffer(raw,dtype='<i2',offset=n_header).reshape(nrecords,bounds[-1])
    eeg = []
    fs = lengths[channels[0]]
    for c in channels:
        if units[c] != 'uV' or lengths[c] != fs:
            raise ValueError(f'Inconsistent channel timing/units in {path.name}')
        counts = samples[:,bounds[c]:bounds[c+1]].ravel().astype(float)
        gain = (phys_max[c]-phys_min[c])/(dig_max[c]-dig_min[c])
        eeg.append((counts-dig_min[c])*gain+phys_min[c])
    eeg = np.array(eeg)
    anns = samples[:,bounds[annot]:bounds[annot+1]].tobytes()
    epochs, truth, trial_ids, omitted = [], [], [], []
    tags = [(float(t),float(d),int(c)) for t,d,c in EVENT.findall(anns) if c!=b'0']
    for i,(t,d,c) in enumerate(tags,1):
        a = round(t*fs)
        b = a + 3*fs
        if d < 3 or a < 0 or b > eeg.shape[-1]:
            omitted.append(i)
            continue
        epochs.append(eeg[:,a:b])
        truth.append(c-1)
        trial_ids.append(i)
    if not epochs or len(set(truth)) != 2:
        raise ValueError(f'Missing one or both hand classes in {path.name}')
    return np.stack(epochs), np.array(truth), trial_ids, fs, omitted, hashlib.sha256(raw).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=DATA)
    p.add_argument('--out',type=Path,default=HERE/'physionet_results')
    p.add_argument('--last-subject',type=int,choices=range(1,110),default=12,metavar='1..109')
    args = p.parse_args()
    rows, summaries, files = [], [], []
    for s in range(1,args.last_subject+1):
        if s in EXCLUDE:
            continue
        subject=f'S{s:03d}'
        runs = {}
        for r in (4,8,12):
            name=f'{subject}R{r:02d}.edf'
            runs[r]=read_edf(args.data/subject/name)
            files.append(dict(subject=subject,run=r,name=name,sha256=runs[r][5],trials=len(runs[r][1]),
                              omitted_short=runs[r][4],sample_rate=runs[r][3]))
        fs = runs[4][3]
        if any(v[3] != fs for v in runs.values()):
            raise ValueError(f'Sampling rate changes within participant {subject}')
        train = np.concatenate([runs[r][0] for r in (4,8)])
        labels = np.concatenate([runs[r][1] for r in (4,8)])
        model = CSPClassifier(fs=fs).fit(train,labels)
        pr = csp_right(model,runs[12][0])
        part = [dict(subject=subject,run=12,trial=j,y_true=int(y),p_right=float(p_right),
                     y_pred=int(p_right>.5),p_correct=max(p_right,1-p_right),
                     correct=int((p_right>.5)==y)) for j,y,p_right in zip(runs[12][2],runs[12][1],pr)]
        rows.extend(part)
        summaries.append(dict(subject=subject,**metrics(part,'p_right')))
    args.out.mkdir(parents=True,exist_ok=True)
    write_table(args.out/'predictions.csv',rows)
    write_table(args.out/'per_subject.csv',summaries)
    rng=np.random.default_rng(261119)
    draws=rng.integers(len(summaries),size=(10000,len(summaries)))
    def subject_metric(key):
        v=np.array([s[key] for s in summaries])
        return dict(mean=v.mean(), ci95=np.quantile(v[draws].mean(1),[.025,.975]).tolist())
    result=dict(dataset='EEGMMIDB 1.0.0',source='https://doi.org/10.13026/C28G6P',
                cohort=f'S001..S{args.last_subject:03d}',excluded=sorted(s for s in EXCLUDE if s <= args.last_subject),
                exclusion_source='https://pmc.ncbi.nlm.nih.gov/articles/PMC5066028/',
                run_train=[4,8],run_test=12,subjects=len(summaries),method='Three-channel 8–30 Hz CSP + shrinkage LDA',
                micro=metrics(rows,'p_right'),cluster={k:subject_metric(k) for k in ('accuracy','brier','nll','balanced_accuracy')},
                trial_rule='Imagery annotations T1/T2 lasting >=3 s with all samples available; every training and test run must contain both classes.',
                omitted_trial_count=sum(len(f['omitted_short']) for f in files))
    (args.out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    (args.out/'manifest.json').write_text(json.dumps(files,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
