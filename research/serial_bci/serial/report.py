"""Create manuscript-ready tables from one cohort's frozen predictions."""
from pathlib import Path

import numpy as np


def write_report(rows, summary, out: Path):
    overall=summary['pooled']
    delta=summary['primary_brier_gain']
    ci=delta.get('participant_bootstrap_ci95')
    n=len(rows)
    text=['# 串行留出队列结果','',
          f"本次共有 {delta['n']} 人、{len(summary['by_run'])} 次可分析会话，校准块之后留出 {n} 条有效配对记录。"
          f"其中频率正确 {overall['ss_correct']} 条，手别正确 {overall['mi_correct']} 条，两个判断均正确 {overall['joint_correct']} 条。",
          '',f"受试者等权的 Brier 改善（无逐试次 P 减去 p×q）为 {delta['mean']:+.5f}。"
          f"共有 {delta['people_better']}/{delta['n']} 人改善。"
          + (f"按人重抽样的 95% 区间为 [{ci[0]:.5f}, {ci[1]:.5f}]。" if ci else '此处仅作描述。'),
          '', '| 预测完整指令正确的模型 | Brier | 对数损失 | 固定训练阈值接受数 | 接受中的错误 | 约80%同覆盖时正确/接受 |',
          '|---|---:|---:|---:|---:|---:|']
    names={'prior':'训练联合正确率','no_p':'SS 训练正确率 × q','ss_only':'p × MI 训练正确率',
           'margin_product':'原分差 × q','product':'p × q','joint_logistic':'联合 logistic','weighted_posthoc':'收缩传递（补充）'}
    for k,v in overall['command'].items():
        a,b=v['frozen_policy'],v['matched80']
        text.append(f"| {names[k]} | {v['brier']:.5f} | {v['logloss']:.5f} | {a['accepted']}/{n} | {a['errors']} | {b['accepted']-b['errors']}/{b['accepted']} |")
    text+=['','| SSVEP 方案 | 频率正确/总数 | 非8判8/非8总数 | 真8漏判/真8总数 |','|---|---:|---:|---:|']
    for k,v in overall['ss'].items():
        label={'raw':'原始 FBCCA','other_mean':'同试次减其他频率均值','idle':'减8段闪烁空闲','rest':'减同run睁眼短静息','train_nontarget':'减训练非目标','frozen':'Benchmark 参数直迁'}[k]
        text.append(f"| {label} | {v['correct']}/{v['n']} | {v['fp8']}/{v['non8']} | {v['fn8']}/{v['true8']} |")
    subset=[r for r in rows if r['idle_hz']!='']
    if subset:
        text+=['','闪烁空闲可用的同一批试次，按相同分母比较。','',
               '| 方法 | 频率正确 | 非8判8 | 真8漏判 |','|---|---:|---:|---:|']
        f=np.array([r['true_hz'] for r in subset])
        for k in ('raw','rest','idle','train_nontarget'):
            matched=[r for r in subset if r[k+'_hz']!='']
            if len(matched)!=len(subset):
                continue
            h=np.array([r[k+'_hz'] for r in matched])
            text.append(f"| {k} | {sum(f==h)}/{len(f)} | {sum((h==8)&(f!=8))}/{sum(f!=8)} | {sum((h!=8)&(f==8))}/{sum(f==8)} |")
    text+=['','| 受试者 | 留出数 | 双正确 | 无逐次 P 的 Brier | p×q 的 Brier | 改善 |','|---|---:|---:|---:|---:|---:|']
    for p,v in summary['by_person'].items():
        a,b=(v['command'][k]['brier'] for k in ('no_p','product'))
        text.append(f"| {p} | {v['n']} | {v['joint_correct']} | {a:.5f} | {b:.5f} | {a-b:+.5f} |")
    m=overall['mi']
    text+=['',f"相同 MI 分类头加入 P 前后分别判对 {m['hand_mi_only']['correct']}/{n} 和 {m['hand_with_p']['correct']}/{n}，"
           f"右手概率 Brier 分别为 {m['hand_mi_only']['brier']:.5f} 和 {m['hand_with_p']['brier']:.5f}。",
           '', '阈值、排除记录、各会话详情和哈希见本目录 CSV 与 JSON。80% 排序使用该批留出分数，只作离线描述。']
    (out/'结果表.md').write_text('\n'.join(text)+'\n')
