"""Participant-level inference; repeated seeds are not extra participants."""
import numpy as np
from scipy import stats


def paired_effect(baseline,method,seed=20261101,repeats=10000):
    a,b=np.asarray(baseline,float),np.asarray(method,float)
    if a.shape!=b.shape or a.ndim!=1 or not len(a) or not np.isfinite(a+b).all():raise ValueError("Matched participant arrays required")
    d=b-a;rng=np.random.default_rng(seed)
    means=d[rng.integers(0,len(d),(repeats,len(d)))].mean(1)
    ci=np.quantile(means,[.025,.975]).tolist() if len(d)>=2 else None
    sd=float(d.std(ddof=1)) if len(d)>1 else 0.
    return dict(participants=len(d),mean_gain=float(d.mean()),bootstrap95=ci,
        paired_dz=float(d.mean()/sd) if sd>0 else None,
        interpretation="Exploratory; participants are the resampling unit")


def mcnemar_exact(y,before,after):
    y,a,b=map(np.asarray,(y,before,after))
    if y.ndim!=1 or y.shape!=a.shape or a.shape!=b.shape:raise ValueError("Matched trial predictions required")
    fixed=int(((a!=y)&(b==y)).sum());harmed=int(((a==y)&(b!=y)).sum());n=fixed+harmed
    return dict(fixed=fixed,harmed=harmed,p=float(stats.binomtest(fixed,n,.5).pvalue) if n else 1.)


def ability_interaction(training_ability,baseline,method):
    ability,a,b=map(lambda x:np.asarray(x,float),(training_ability,baseline,method))
    if any(x.ndim!=1 or not np.isfinite(x).all() or ((x<0)|(x>1)).any() for x in (ability,a,b)):
        raise ValueError("Finite participant accuracy arrays in [0,1] required")
    if ability.shape!=a.shape or a.shape!=b.shape or len(a)<4:
        return dict(status="insufficient_participants",participants=len(a))
    x=np.column_stack((np.ones(len(a)),1-ability));gain=b-a
    if np.linalg.matrix_rank(x)<2:return dict(status="constant_training_ability")
    inverse=np.linalg.inv(x.T@x);coef=inverse@x.T@gain;resid=gain-x@coef
    leverage=np.einsum("ij,jk,ik->i",x,inverse,x)
    scaled=resid/np.maximum(1-leverage,1e-8);cov=inverse@(x.T@(scaled[:,None]**2*x))@inverse
    se=np.sqrt(np.diag(cov));critical=stats.t.ppf(.975,len(a)-2)
    low=ability<.6
    return dict(status="exploratory",participants=len(a),ability_source="training-only OOF, separate from test improvement",
        intercept=float(coef[0]),weak_ability_interaction=float(coef[1]),
        interaction95=[float(coef[1]-critical*se[1]),float(coef[1]+critical*se[1])],
        low_ability_count=int(low.sum()),low_ability_subset_sufficient=bool(low.sum()>=8),
        low_ability_effect=paired_effect(a[low],b[low]) if low.any() else None,
        causal_or_selective_compensation_proven=False)
