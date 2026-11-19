#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.ml.hybrid_pairs import export_split

if __name__ == "__main__":
    p=argparse.ArgumentParser(description="Export real SSVEP→MI pairs with disjoint whole-run splits")
    p.add_argument("--directory",type=Path,default=ROOT/"data/research_experiments/raw_trials")
    p.add_argument("--train-runs",nargs="+",required=True);p.add_argument("--test-runs",nargs="+",required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--task-set",choices=("left_right","wave_grasp"),default="left_right")
    p.add_argument("--seconds",type=float,default=3.);a=p.parse_args()
    result=export_split(a.directory,a.train_runs,a.test_runs,a.output,a.task_set,a.seconds)
    print({k:len(v) for k,v in result.items()})
