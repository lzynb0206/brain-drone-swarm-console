#!/usr/bin/env python3
"""Run the preprocessing/data-split code from the public GCD repository."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ml.gcd_preprocessing import load_data  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use GCD utils.py logic to process processed_data.h5",
    )
    parser.add_argument(
        "filepath",
        type=Path,
        help="path to the GCD processed_data.h5 file",
    )
    parser.add_argument(
        "--graph-type",
        choices=("G_PCC", "G_MSC", "G_PLV"),
        default="G_PCC",
    )
    parser.add_argument("--normalize-adjacency", action="store_true")
    parser.add_argument("--plot-adjacency", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.filepath.is_file():
        print(f"ERROR: file not found: {args.filepath}", file=sys.stderr)
        return 2

    folds = load_data(
        args.filepath,
        graph_type=args.graph_type,
        if_normalized_adj=args.normalize_adjacency,
        if_plot_adj=args.plot_adjacency,
    )
    summary = {
        "source": "Quentin2022/Graph-Contrastive-Denoising-Module utils.py",
        "filepath": str(args.filepath.resolve()),
        "graph_type": args.graph_type,
        "normalized_adjacency": bool(args.normalize_adjacency),
        "folds": len(folds),
        "first_fold": {
            split: {
                key: list(value.shape)
                for key, value in folds[0][split].items()
            }
            for split in ("train", "test")
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
