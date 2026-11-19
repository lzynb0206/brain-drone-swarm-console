"""Report full-coverage, participant-level results from the frozen offline workflow."""

from pathlib import Path

TITLES = {
    "within_1p5": "会话内主要比较 · 视觉 1.5 秒",
    "within_4s": "敏感性 · 视觉 4 秒",
    "external_gap_p": "敏感性 · 迁移五分差 P",
    "across_days": "同人跨日 · 上次会话训练，下次整场测试",
}
ARMS = {
    "mi_only": "MI 自身调权",
    "mi_plus_p": "MI 自身调权 + 配对 P",
    "no_p": "固定混合",
    "with_p": "仅由 P 调权",
    "broadband": "宽带 CSP",
    "mu": "仅 μ",
    "beta": "仅 β",
    "shift_refit": "训练与测试均块内错一位",
    "oracle_visual": "事后用视觉正误代替 P",
}


def pct(x):
    return "不可估" if x is None else f"{100*x:.2f}%"


def write_report(summary, rows, out):
    lines = [
        "# 真实时序配对 · 后段左右手解码",
        "",
        "每种方法均给出手别。主效应为同一人、同一批测试试次上，加 P 相对于 MI 自身调权的平衡准确率变化，再对受试者等权。改对/改错均相对于同一 MI 自身调权对照。Brier 越小越好。",
        "",
    ]
    primary = summary["primary_experiment"]
    titles = dict(TITLES)
    titles.update(
        across_days_4s="同人跨日 · 4 秒窗补算",
        cross_person_archived_4s="第三个 ID 整段留出 · 4 秒窗补算",
    )
    if summary["strict_new"]:
        titles.update(
            within_1p5="敏感性 · 视觉 1.5 秒", within_4s="独立新队列主比较 · 视觉 4 秒"
        )
    if primary not in summary["experiments"]:
        lines += ["预定主比较没有可用折，以下只能报告敏感性结果。", ""]
    for e in sorted(summary["experiments"], key=lambda k: (k != primary, k)):
        exp = summary["experiments"][e]
        pool = exp["pooled"]
        arms = pool["arms"]
        n = pool["n"]
        lines += [
            "## " + titles.get(e, "独立采集 ID 整人留出 · " + e),
            "",
            f"{exp['subject_count']} 个受试者 ID，{n} 个测试试次；前段 FBCCA 判对 {pool['ss_correct']}/{n}。两个 MI 频带的手别有 {pool['expert_disagreement']} 次分歧。",
            "",
            "| 方法 | 判对 | 平衡准确率（合并试次） | 右手 Brier | 改对 | 改错 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name, label in ARMS.items():
            a = arms[name]
            lines.append(
                f"| {label} | {a['correct']}/{n} | {pct(a['ba'])} | {a['brier']:.5f} | {a['rescued']} | {a['harmed']} |"
            )
        qs = exp["shift_delta_ba_quantiles"]
        ci = exp["subject_bootstrap_95ci"]
        lines += [
            "",
            f"人均主要差值 {pct(exp['mean_person_delta_ba'])}（百分点尺度）。"
            + (
                f" 受试者层面 95% 区间 {pct(ci[0])} 至 {pct(ci[1])}（{exp['interval_method']}）。"
                if ci
                else " 当前数据未计算人群区间。"
            ),
            "",
            (
                f"499 次冻结模型的测试块内循环错配，人均差值的 2.5%、50%、97.5% 分位数为 {', '.join(pct(x) for x in qs)}。真实配对减去错配平均值为 {pct(exp['paired_minus_shift_mean_ba'])}。"
                if qs
                else "无可估错配结果。"
            ),
            "",
            "| 受试者 ID | 分母 | MI 自身判对 | 加 P 判对 | MI 自身平衡准确率 | 加 P 平衡准确率 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for s, v in exp["by_person"].items():
            b = v["arms"]["mi_only"]
            p = v["arms"]["mi_plus_p"]
            lines.append(
                f"| {s} | {v['n']} | {b['correct']} | {p['correct']} | {pct(b['ba'])} | {pct(p['ba'])} |"
            )
        auc = pool["ss_auc_within_block"]
        lines += [
            "",
            f"视觉 P 的 Brier 为 {pool['ss_brier']:.5f}，各会话训练命中率常数为 {pool['ss_prior_brier']:.5f}。同块内视觉正误排序 AUC 为 "
            + (f"{auc:.3f}" if auc is not None else "不可估")
            + f"。非 8 Hz 误报为 8 Hz 有 {pool['false8']}/{pool['non8']} 次，真实 8 Hz 漏报 {pool['missed8']}/{pool['true8']} 次。",
            "",
            f"块及真实频率均相同的错配对照中，有 {exp['conditional_movable']}/{n} 个试次存在其他同层配对选择。真 P 减去这种错配的平均平衡准确率差为 {pct(exp['paired_minus_conditional_mean_ba'])}。错配分布仅作敏感性检验。",
            "",
            f"原始采集端标记 C3 与 C4 近重复的 MI 段有 {pool['redundant_counts']['c3_c4_redundant']}/{n}。这些段保留在主比较中。",
            "",
        ]
    lines += [
        "## 审计与重叠",
        "",
        f"4 秒窗口额外排除 {len(summary['long_window_exclusions'])} 对。每种分析的原始配对标识、来源哈希和预测见 predictions.csv。折、训练清单、短会话、判别系数和区块质量见 summary.json、epoch_audit.csv。跨日、时间窗与五分差分析可能复用主评估试次，不加总。事后视觉正误使用测试视觉标签，仅供机制分析。",
        "",
    ]
    if summary["acquisition_issues"]:
        lines += [
            "## 采集计划核查",
            "",
            *["- " + msg for msg in summary["acquisition_issues"]],
            "",
        ]
    (Path(out) / "结果.md").write_text("\n".join(lines), encoding="utf-8")
