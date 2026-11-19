#!/usr/bin/env python3
"""Train the separate research-only ``个人算法-Lzy`` runtime model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bci.config import (  # noqa: E402
    LZY_PERSONAL_METADATA_PATH,
    LZY_PERSONAL_MODEL_PATH,
    RESEARCH_DATA_ROOT,
)
from src.ml.lzy_personal_training import train_lzy_personal_algorithm  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--raw-directory", type=Path, default=RESEARCH_DATA_ROOT / "raw_trials",
    )
    parser.add_argument("--model-path", type=Path, default=LZY_PERSONAL_MODEL_PATH)
    parser.add_argument("--metadata-path", type=Path, default=LZY_PERSONAL_METADATA_PATH)
    parser.add_argument(
        "--output-directory", type=Path,
        default=PROJECT_ROOT / "outputs" / "lzy_personal_algorithm",
    )
    args = parser.parse_args()
    metadata = train_lzy_personal_algorithm(
        args.raw_directory,
        args.model_path,
        args.metadata_path,
        args.output_directory,
        run_id=args.run_id,
    )
    print(json.dumps({
        "model_path": metadata["model_path"],
        "source_run_id": metadata["source_run_id"],
        "evaluation": metadata["evaluation"],
        "requested_feature_comparison": metadata["requested_feature_comparison"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
