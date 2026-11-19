#!/usr/bin/env python3
"""Train the runtime personal score calibrator from one automatic SSVEP run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bci.config import (  # noqa: E402
    PERSONAL_SCORE_METADATA_PATH,
    PERSONAL_SCORE_MODEL_PATH,
    RESEARCH_DATA_ROOT,
)
from src.ml.personal_score_training import train_personal_score_calibration  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--raw-directory", type=Path,
        default=RESEARCH_DATA_ROOT / "raw_trials",
    )
    parser.add_argument("--model-path", type=Path, default=PERSONAL_SCORE_MODEL_PATH)
    parser.add_argument("--metadata-path", type=Path, default=PERSONAL_SCORE_METADATA_PATH)
    parser.add_argument("--algorithm-name", default="个人校准融合")
    parser.add_argument("--minimum-trial-index", type=int)
    parser.add_argument("--maximum-trial-index", type=int)
    parser.add_argument("--trial-indices", type=int, nargs="+")
    args = parser.parse_args()
    metadata = train_personal_score_calibration(
        args.raw_directory,
        args.model_path,
        args.metadata_path,
        run_id=args.run_id,
        algorithm_name=args.algorithm_name,
        minimum_trial_index=args.minimum_trial_index,
        maximum_trial_index=args.maximum_trial_index,
        trial_indices=args.trial_indices,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
