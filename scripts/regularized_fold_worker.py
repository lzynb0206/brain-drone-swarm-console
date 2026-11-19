#!/usr/bin/env python3
"""Optional isolated fold worker; never share partially written checkpoints."""
import argparse
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.regularize_subject24_neural as study


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fold", required=True, choices=[f"grouped_fold_{k}" for k in range(1, 6)])
    parser.add_argument("--method", required=True, choices=study.METHODS)
    args = parser.parse_args()
    canonical = study.OUT
    destination = canonical / args.fold / args.method
    if destination.exists():
        print("Canonical folder already started; nothing changed", destination, flush=True)
        return
    study.OUT = ROOT / "outputs" / ("subject24_regularized_worker_" + args.fold + "_" + args.method)
    study.freeze()
    assert study.read(study.OUT / "configuration.json") == study.read(canonical / "configuration.json")
    data = study.load_local(study.OUT)
    assert study.read(study.OUT / "quality.json") == study.read(canonical / "quality.json")
    name, split = next((name, split) for name, split in study.get_splits(data) if name == args.fold)
    study.run(data, name, split, args.method)
    source = study.OUT / name / args.method
    result = study.read(source / "result.json")
    assert result["checkpoint_reload_verified"] and len(result["refits"]) == 3
    assert destination.exists() is False, "Canonical fold already started; leave isolated output untouched"
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".incoming-regularized-", dir=destination.parent))
    shutil.copytree(source, stage, dirs_exist_ok=True)
    hashes = {str(p.relative_to(source)): study.sha(p) for p in source.rglob("*") if p.is_file()}
    assert hashes == {str(p.relative_to(stage)): study.sha(p) for p in stage.rglob("*") if p.is_file()}
    assert not destination.exists(), "Canonical worker advanced; keep staging for inspection"
    stage.rename(destination)
    study.save(canonical / f"import_{name}_{args.method}.json", dict(
        source=str(source), destination=str(destination), file_hashes=hashes,
        configuration_sha256=study.sha(canonical / "configuration.json"),
        worker_source_sha256=study.sha(Path(__file__)),
        note="Isolated complete fold only; no existing canonical artifacts overwritten"))
    print("Imported complete fold", name, args.method, flush=True)


if __name__ == "__main__":
    main()
