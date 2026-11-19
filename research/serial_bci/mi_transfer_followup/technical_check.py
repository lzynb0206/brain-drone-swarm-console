"""Technical rehearsal for the frozen 4 s visual window, using calibration blocks only."""

import json
from pathlib import Path

import numpy as np
from sklearn.metrics import balanced_accuracy_score

from research.serial_bci.mi_transfer_followup.collection_audit import audit_pairs
from research.serial_bci.mi_transfer_followup.experts import expert_evidence
from research.serial_bci.serial.run import load, ss_predict
from research.serial_bci.ssvep.validate import baseline, decisions


def rehearsal(raw, out, long_score):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    lines = [
        "# 独立工程预试 · 4 秒视觉窗",
        "",
        "仅使用校准块 1–4。下表中的数值均为留块交叉预测，信号质量提示保留。正式队列的后三个测试块封存。",
        "",
        "| 记录 | 有效对 | 视觉命中 | 非 8 Hz 误报 / 非 8 Hz | 真 8 Hz 漏报 / 真 8 Hz | 同块视觉 AUC | P / 常数 Brier | μ / β MI 平衡准确率 | C3–C4 近重复 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for folder in sorted({p.parent for p in Path(raw).rglob("hybrid-*-ssvep.npz")}):
        pairs, _, _ = load(folder, max_block=4)
        if not pairs:
            continue
        usable = []
        scores = []
        unavailable = []
        for r in pairs:
            try:
                score = long_score(folder / (r["pair"] + "-ssvep.npz"))
            except (ValueError, KeyError, OSError) as e:
                unavailable.append(dict(pair=r["pair"], reason=str(e)))
                continue
            scores.append(score)
            usable.append(r)
        g = np.array([r["block"] for r in usable])
        f = np.array([r["true_hz"] - 8 for r in usable])
        y = np.array([r["hand"] for r in usable])
        report = dict(
            run=folder.name, valid_pairs=len(usable), long_window_exclusions=unavailable
        )
        reports.append(report)
        if set(g) != {1, 2, 3, 4} or any(
            len(np.unique(f[g != b])) != 6
            or np.min(np.bincount(y[g != b], minlength=2)) < 2
            for b in set(g)
        ):
            report["status"] = "insufficient_calibration_blocks_or_classes"
            lines.append(
                f"| {folder.name} | {len(usable)} | 校准块或类别不足 | — | — | — | — | — | — |"
            )
            continue
        report["acquisition"] = audit_pairs(folder, usable)
        s = np.array(scores)
        x = np.stack([r["mi"] for r in usable])
        pred = np.empty(len(g), int)
        p = np.zeros(len(g))
        prior = np.zeros(len(g))
        for b in np.unique(g):
            a = np.flatnonzero(g != b)
            h = np.flatnonzero(g == b)
            pred[h], _, p[h] = ss_predict(s, f, g, a, h, "train_nontarget")
            hits = []
            for other in np.unique(g[a]):
                src = a[g[a] != other]
                held = a[g[a] == other]
                guess, _ = decisions(
                    s[held],
                    baseline(s[src], f[src], "train_nontarget"),
                    "train_nontarget",
                )
                hits.extend(guess == f[held])
            prior[h] = np.mean(hits)
        oof, _ = expert_evidence(x, y, g, x[:1])
        hit = f == pred
        wins = 0.0
        n = 0
        for b in np.unique(g):
            a = p[(g == b) & hit]
            c = p[(g == b) & ~hit]
            wins += np.sum(a[:, None] > c) + 0.5 * np.sum(a[:, None] == c)
            n += len(a) * len(c)
        auc = wins / n if n else None
        report.update(
            status="calibration_only",
            ss_correct=int(sum(hit)),
            ss_brier=float(np.mean((p - hit) ** 2)),
            prior_brier=float(np.mean((prior - hit) ** 2)),
            within_block_auc=auc,
            non8=int(sum(f != 0)),
            true8=int(sum(f == 0)),
            false8=int(sum((f != 0) & (pred == 0))),
            missed8=int(sum((f == 0) & (pred != 0))),
            mi_ba=[float(balanced_accuracy_score(y, q > 0.5)) for q in oof.T],
            c3_c4_redundant=sum(r["c3_c4_redundant"] for r in usable),
        )
        lines.append(
            f"| {folder.name} | {len(g)} | {sum(hit)} | {report['false8']}/{report['non8']} | {report['missed8']}/{report['true8']} | "
            + (f"{auc:.3f}" if auc is not None else "不可估")
            + f" | {report['ss_brier']:.4f} / {report['prior_brier']:.4f} | {report['mi_ba'][0]:.1%} / {report['mi_ba'][1]:.1%} | {report['c3_c4_redundant']} |"
        )
    (out / "technical_check.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2, allow_nan=False)
    )
    (out / "技术预试.md").write_text("\n".join(lines) + "\n")
    if not reports:
        raise ValueError(
            "No formal calibration records; use the independent engineering session with four scheduled blocks"
        )
    print(out / "技术预试.md")
