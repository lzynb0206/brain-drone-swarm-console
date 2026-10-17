#!/usr/bin/env python3
"""Build the runtime personal-template model from one calibration run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bci.config import (  # noqa: E402
    PERSONAL_TEMPLATE_METADATA_PATH,
    PERSONAL_TEMPLATE_MODEL_PATH,
)
from src.ml.personal_template_model import train_personal_template_model  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--model", type=Path, default=PERSONAL_TEMPLATE_MODEL_PATH)
    parser.add_argument("--metadata", type=Path, default=PERSONAL_TEMPLATE_METADATA_PATH)
    args = parser.parse_args()
    metadata = train_personal_template_model(
        args.run_directory,
        args.model,
        args.metadata,
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
