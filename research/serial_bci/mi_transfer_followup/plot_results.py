"""Plot archived chronological tests from the saved trial predictions and summary."""
import argparse
import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import roc_auc_score


HERE = Path(__file__).resolve().parent
INK = "#20292E"
GREY = "#50616A"
GRID = "#D5DDE0"
HIT = "#007565"
MISS = "#9A4C26"
EXPERIMENTS = ("within_1p5", "across_days", "cross_person_archived_challenge")


def subject_id(value):
    match = re.fullmatch(r"subject-0*(\d+)", value)
    return match.group(1) if match else value


def strip_positions(p, rng, center):
    # Stagger neighboring marks deterministically while preserving their P coordinates.
    order = np.argsort(p, kind="stable")
    shifts = np.zeros(len(p))
    for rank, i in enumerate(order):
        near = [shifts[j] for j in order[:rank]
                if abs(p[j] - p[i]) < .0053]
        candidates = np.arange(-.34, .35, .057)
        if near:
            spacing = np.min(np.abs(candidates[:, None] - np.asarray(near)), axis=1)
            choices = candidates[spacing >= .055]
        else:
            choices = candidates
        shifts[i] = (choices[np.argmin(np.abs(choices))] if len(choices)
                     else rng.uniform(-.34, .34))
    return center + shifts


def draw(results):
    with (results / "predictions.csv").open(encoding="utf-8") as f:
        trials = list(csv.DictReader(f))
    summary = json.loads((results / "summary.json").read_text())["experiments"]
    groups = {e: [r for r in trials if r["experiment"] == e] for e in EXPERIMENTS}
    if any(not groups[e] or e not in summary for e in EXPERIMENTS):
        raise ValueError("This archival figure needs within-session, across-day and held-person tests.")
    by_session = defaultdict(list)
    primary = groups[EXPERIMENTS[0]]
    for row in primary:
        by_session[row["run"]].append(row)
    if len(by_session) != 3:
        raise ValueError("This figure is laid out for the original three primary test sessions.")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "text.color": INK,
                     "axes.labelcolor": INK, "xtick.color": GREY, "ytick.color": INK,
                     "pdf.fonttype": 42, "ps.fonttype": 42})
    fig = plt.figure(figsize=(9, 5.8), facecolor="white")
    fig.text(.045, .962, "A", fontsize=15, fontweight="bold", va="top")
    fig.text(.082, .962, "Does P rank correct SSVEP decisions higher?", fontsize=13,
             fontweight="bold", va="top")
    pooled_auc = roc_auc_score([int(r["ss_hit"]) for r in primary], [float(r["p"]) for r in primary])
    fig.text(.082, .900, f"Each point is one held-out trial. The AUC pooled across all {len(primary)} trials is "
             f"{pooled_auc:.2f}.", color=GREY, fontsize=9)
    grid = fig.add_gridspec(1, 3, left=.105, right=.98, top=.769, bottom=.558, wspace=.48)
    rng = np.random.default_rng(261119)
    pp = [float(r["p"]) for r in primary]
    left = max(0, math.floor((min(pp) - .008)*20)/20)
    right = min(1, math.ceil((max(pp) + .008)*20)/20)
    for col, (run, rows) in enumerate(sorted(by_session.items())):
        ax = fig.add_subplot(grid[0, col])
        p = np.array([float(r["p"]) for r in rows])
        hit = np.array([int(r["ss_hit"]) for r in rows]).astype(bool)
        person = subject_id(rows[0]["subject"])
        date = datetime.strptime(run.split("-")[1], "%Y%m%d").strftime("%d %b")
        auc = roc_auc_score(hit, p) if np.unique(hit).size == 2 else np.nan
        ax.set_title(f"ID {person}  ·  {date}\nAUC {auc:.2f}    |    {hit.sum()}/{len(p)} correct",
                     loc="left", fontsize=9, fontweight="bold", linespacing=1.5, pad=6)
        for target, center, color in ((True, 1, HIT), (False, 0, MISS)):
            vals = p[hit == target]
            ax.axhline(center, color=GRID, linewidth=.6, zorder=0)
            ax.scatter(vals, strip_positions(vals, rng, center), s=14, marker="o",
                       facecolors=color if target else "white", edgecolors=color,
                       linewidths=.8, alpha=.9, zorder=2)
        ax.set_xlim(left, right)
        ax.set_ylim(-.49, 1.47)
        ax.set_xticks(np.arange(math.ceil(left*10)/10, right+.001, .1))
        ax.set_yticks([0, 1], ["Wrong", "Correct"] if col == 0 else ["", ""])
        ax.tick_params(axis="y", length=0, labelsize=8, pad=6)
        ax.tick_params(axis="x", labelsize=8, length=3, width=.6)
        for side in ("left", "right", "top"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
    fig.text(.545, .493, "Calibrated probability P of a correct SSVEP decision",
             fontsize=9, ha="center")
    fig.text(.045, .418, "B", fontsize=15, fontweight="bold", va="top")
    fig.text(.082, .418, "Does adding P correct MI hand predictions?", fontsize=13,
             fontweight="bold", va="top")
    ax = fig.add_axes([.081, .131, .89, .222])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    positions = [0, .447, .585, .757, .935]
    titles = ["Held-out evaluation", "MI only", "MI + P", "Fixed / lost", "Δ BA (pp)"]
    for j, (x, lab) in enumerate(zip(positions, titles)):
        ax.text(x, .93, lab, ha="left" if j == 0 else "center", va="center",
                color=GREY, fontsize=8, fontweight="bold")
    yvals = [.68, .403, .127]
    names = ["Within session", "Across days", "Held-out person"]
    for y, name, e in zip(yvals, names, EXPERIMENTS):
        batch = groups[e]
        ids = ", ".join(sorted({subject_id(r["subject"]) for r in batch}))
        tag = "IDs" if "," in ids else "ID"
        o = summary[e]
        base, plus = o["pooled"]["arms"]["mi_only"], o["pooled"]["arms"]["mi_plus_p"]
        if base["n"] != len(batch) or plus["n"] != len(batch):
            raise ValueError(f"Summary and trial CSV disagree for {e}")
        entries = [f"{name}   ·   {tag} {ids}", f"{base['correct']}/{len(batch)}",
                   f"{plus['correct']}/{len(batch)}", f"{plus['rescued']} / {plus['harmed']}",
                   f"{o['mean_person_delta_ba']*100:+.1f}"]
        for j, (x, label) in enumerate(zip(positions, entries)):
            ax.text(x, y, label, ha="left" if j == 0 else "center", va="center",
                    fontsize=9.3, color=HIT if j == 2 else INK)
    for y in (.803, .54, .267, .006):
        ax.axhline(y, color=GRID, linewidth=.65)
    primary_pairs = {r["pair"] for r in primary}
    across = {r["pair"] for r in groups["across_days"]}
    overlap = len(primary_pairs & across)
    fig.text(.082, .087, f"Across-day testing reuses {overlap} primary test trials. "
             "The cross-person model was trained on ID 1 and tested on ID 3.", fontsize=8, color=GREY)
    fig.text(.082, .052, "Fixed / lost counts changes from wrong to correct / correct to wrong. "
             "Δ BA is the participant-mean change in balanced accuracy.", fontsize=8, color=GREY)
    fig.savefig(results / "paired_ssvep_mi.png", dpi=300)
    fig.savefig(results / "paired_ssvep_mi.pdf")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=HERE / "results")
    draw(parser.parse_args().results)
