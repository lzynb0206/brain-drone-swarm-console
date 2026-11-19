"""Draw the cross-corpus external evaluation from saved test predictions."""
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

RESULT = Path(__file__).parent/'results'
colors = {'raw':'#657485','train_nontarget':'#006f73','margin':'#777b87','platt':'#006f73','isotonic':'#b85c21'}


def read(name):
    with (RESULT/name).open() as f:
        return [r for r in csv.DictReader(f) if r['split']=='benchmark_to_beta']


def draw():
    fig, axes = plt.subplots(1,3,figsize=(11.4,3.7),layout='constrained')
    plt.rcParams.update({'svg.fonttype':'none'})
    metrics = [r for r in read('metrics.csv') if r['seconds']=='1.5' and r['method']=='platt']
    ax=axes[0]
    for j,(v,label) in enumerate([('raw','Original'),('train_nontarget','Background-corrected')]):
        m=next(r for r in metrics if r['variant']==v)
        y=[100*int(m['fp8'])/int(m['non8']),100*int(m['fn8'])/int(m['true8'])]
        bars=ax.bar(np.arange(2)+(j-.5)*.34,y,width=.33,color=colors[v],label=label)
        ax.bar_label(bars,fmt='%.1f',fontsize=8,padding=3)
    ax.set_xticks([0,1],['False positive\n(non-8 Hz, n=1,400)','False negative\n(true 8 Hz, n=280)'])
    ax.set_ylabel('Error rate (%)')
    ax.set_ylim(0,20)
    ax.set_title('A    8 Hz decision errors',loc='left',fontsize=12,fontweight='bold')
    ax.legend(loc='upper right',frameon=False,fontsize=8)
    ax=axes[1]
    reliability=[r for r in read('reliability.csv') if r['variant']=='train_nontarget']
    ax.plot([0,1],[0,1],linestyle='--',linewidth=1,color='#aaa')
    for method,name in [('margin','Relative margin'),('platt','Platt'),('isotonic','Isotonic')]:
        bins=[r for r in reliability if r['method']==method and int(r['n'])>=10]
        ax.plot([float(r['mean_p']) for r in bins],[float(r['acc']) for r in bins],
                marker='o',markersize=4,color=colors[method],label=name,linewidth=1.5)
    ax.set(xlim=(-.02,1.02),ylim=(-.02,1.02),xlabel='Mean reported confidence',ylabel='Observed fraction correct')
    ax.set_title('B    Confidence reliability',loc='left',fontsize=12,fontweight='bold')
    ax.legend(loc='lower right',frameon=False,fontsize=8)
    ax=axes[2]
    for v,name in [('raw','Original'),('train_nontarget','Background-corrected')]:
        rows=[r for r in read('selective.csv') if r['variant']==v]
        ax.plot([100*float(r['coverage']) for r in rows],[100*(1-float(r['acc_accepted'])) for r in rows],
                marker='o',color=colors[v],label=name,linewidth=1.6)
    ax.set(xlim=(30,104),ylim=(0,22),xlabel='Accepted trials (%)',ylabel='Error among accepted trials (%)')
    ax.set_title('C    Risk and coverage',loc='left',fontsize=12,fontweight='bold')
    ax.legend(loc='upper left',frameon=False,fontsize=8)
    for ax in axes:
        ax.spines[['right','top']].set_visible(False)
        ax.tick_params(labelsize=8)
        ax.grid(axis='y',color='#e4e6e8',linewidth=.5)
        ax.set_axisbelow(True)
    fig.savefig(RESULT/'external_validation.svg')
    fig.savefig(RESULT/'external_validation.png',dpi=220)


if __name__=='__main__':
    draw()
