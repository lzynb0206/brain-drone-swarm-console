"""Fixed-rule Bayesian and discriminative confidence baselines for six-way FBCCA."""
import argparse
import json
from pathlib import Path

import h5py
import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

if __package__:
    from .validate import ROOT, HERE, FREQ, fbcca, baseline, fit, apply, metrics, write_csv
else:
    from validate import ROOT, HERE, FREQ, fbcca, baseline, fit, apply, metrics, write_csv


def load(root,name):
    with h5py.File(root/f'{name}_common6_2s.h5') as h:
        score=np.stack([fbcca(epoch[:,:375]) for epoch in h['eeg'][:]])
        label,subjects,blocks=(h[col][:] for col in ['label','subject','block'])
    return score,label,subjects,blocks


def features(scores):
    ordered=np.sort(np.atleast_2d(scores),axis=1)[:,::-1]
    return ordered[:,0,None]-ordered[:,1:]


def fit_gap_ridge(adjusted_train_scores, correct):
    """Learn from train-only, out-of-group background-adjusted score vectors."""
    return make_pipeline(StandardScaler(),LogisticRegression(
        C=1.,solver='lbfgs',max_iter=300,tol=1e-10)).fit(features(adjusted_train_scores),correct)


def frozen_gap_logit(adjusted_scores,parameters):
    """Compute source ridge logits from *already adjusted* six-column scores."""
    x=(features(adjusted_scores)-np.array(parameters['center']))/np.array(parameters['scale'])
    return x@np.array(parameters['ridge_slope'])+parameters['ridge_intercept']


def run(root,out):
    out.mkdir(parents=True,exist_ok=True)
    scores,label,groups,_=load(root,'benchmark')
    held,truth,people,blocks=load(root,'beta')
    corrected=np.empty_like(scores)
    for person in np.unique(groups):
        select=groups==person
        corrected[select]=scores[select]-baseline(scores[~select],label[~select],'train_nontarget')
    source_correct=corrected.argmax(1)==label
    global_offset=baseline(scores,label,'train_nontarget')
    pipeline=fit_gap_ridge(corrected,source_correct)
    standardized,ridge = pipeline.steps[0][1],pipeline.steps[1][1]
    x=standardized.transform(features(corrected))
    v=standardized.transform(features(held-global_offset))
    platt=fit(scores,label,groups,'train_nontarget')
    pred,old=apply(held,'train_nontarget',platt)
    assert np.array_equal(pred,(held-global_offset).argmax(1))
    parameters=dict(frequencies=FREQ.tolist(),offset=global_offset.tolist(),
                    center=standardized.mean_.tolist(),scale=standardized.scale_.tolist(),
                    ridge_slope=ridge.coef_[0].tolist(),ridge_intercept=ridge.intercept_[0],gmm={})
    log_density=[]
    for result in [False,True]:
        fit_rows=source_correct==result
        gmm=GaussianMixture(n_components=2,covariance_type='full',reg_covar=.01,n_init=3,
                            random_state=20261119,max_iter=300).fit(x[fit_rows])
        if not gmm.converged_:
            raise RuntimeError('The fixed mixture model did not converge')
        parameters['gmm'][str(int(result))] = dict(prior=fit_rows.mean(),weights=gmm.weights_.tolist(),
            means=gmm.means_.tolist(),covariances=gmm.covariances_.tolist())
        log_density.append(gmm.score_samples(v)+np.log(fit_rows.mean()))
    post=expit(log_density[1]-log_density[0])
    probabilities=dict(platt=old['platt'],isotonic=old['isotonic'],
                       ridge_five_differences=ridge.predict_proba(v)[:,1],gmm_bayes=post)
    correct=pred==truth
    summary=[]
    rows=[]
    losses={}
    for name,p in probabilities.items():
        stats=metrics(correct,p)
        summary.append(dict(method=name,correct=int(correct.sum()),n=len(truth),
                            accuracy=np.mean(correct),mean_p=p.mean(),**stats))
        q=np.clip(p,1e-6,1-1e-6)
        losses[name]=np.column_stack([(p-correct)**2,-np.where(correct,np.log(q),np.log1p(-q))])
    for idx,subject in enumerate(people):
        rows.append(dict(subject=int(subject),block=int(blocks[idx]),true_hz=FREQ[truth[idx]],
                         pred_hz=FREQ[pred[idx]],correct=int(correct[idx]),
                         **{name:prob[idx] for name,prob in probabilities.items()}))
    rng=np.random.default_rng(20261119)
    subjects=np.unique(people)
    selections=rng.integers(len(subjects),size=(10000,len(subjects)))
    for row in summary:
        delta=losses[row['method']]-losses['platt']
        means=np.array([delta[people==s].mean(0) for s in subjects])
        quantiles=np.quantile(means[selections].mean(axis=1),[.025,.975],axis=0)
        for i,metric in enumerate(['brier','logloss']):
            row[f'delta_{metric}']=means[:,i].mean()
            row[f'delta_{metric}_lo95']=quantiles[0,i]
            row[f'delta_{metric}_hi95']=quantiles[1,i]
        row['delta_ece10']=row['ece10']-summary[0]['ece10']
    out.joinpath('benchmark_models.json').write_text(json.dumps(parameters,indent=2)+'\n')
    write_csv(out/'metrics.csv',summary)
    write_csv(out/'trials.csv',rows)
    out.joinpath('protocol.json').write_text(json.dumps(dict(
        source='Benchmark',test='BETA',seconds=1.5,source_crossfit='leave one subject out for background',
        source_correct=int(source_correct.sum()),source_wrong=int(np.sum(~source_correct)),
        standardization='StandardScaler fit on OOF training margins only',features='top minus rank 2 through 6',
        ridge_C=1,gmm_components_per_outcome=2,gmm_covariance='full',gmm_reg_covar=.01,
        gmm_n_init=3,seed=20261119,bootstrap_participants=10000,
        logloss_clip=1e-6,prior='empirical OOF correctness rate'),indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--processed',type=Path,default=ROOT/'data/public_ssvep/processed')
    parser.add_argument('--out',type=Path,default=HERE/'comparator_results')
    args=parser.parse_args()
    run(args.processed,args.out)
