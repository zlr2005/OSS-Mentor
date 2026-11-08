"""导出开发验证学习曲线；误差线仅表示五seed标准差。"""
import argparse
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def run(root):
    rows=json.loads((root/'results.json').read_text())
    baseline=json.loads((root/'untrained_cosine.json').read_text())
    fractions=[.25,.5,.75,1.]
    fig,axes=plt.subplots(1,2,figsize=(10,4),constrained_layout=True)
    for ax,key,label in zip(axes,['developer_macro_mrr','recall5'],['Developer-macro MRR','Recall@5']):
        x=[next(r['train_users'] for r in rows if r['fraction']==f) for f in fractions]
        y=[statistics.mean(r['metrics'][key] for r in rows if r['fraction']==f) for f in fractions]
        error=[statistics.stdev(r['metrics'][key] for r in rows if r['fraction']==f) for f in fractions]
        ax.errorbar(x,y,yerr=error,marker='o',capsize=4,label='Fresh text model (mean +/- seed SD)',color='#315aaa')
        ax.axhline(baseline[key],ls='--',color='#b15c25',label='Untrained text cosine')
        ax.set_xlabel('Training developers');ax.set_ylabel(label);ax.grid(alpha=.2)
        ax.set_xticks(x)
    axes[0].legend(fontsize=8,loc='lower right')
    fig.suptitle('Fixed development validation: 209 outcomes / 135 developers\nNew audited-input dataset; not comparable to previous CODER metrics',fontsize=10)
    fig.savefig(root/'learning_curve.png',dpi=180)
    plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    run(p.parse_args().root)
