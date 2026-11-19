"""Rebuild results from the existing public caches and historical serial NPZ files."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    base=Path(__file__).resolve().parent
    steps=[
        ('ssvep/validate.py',), ('ssvep/diagnostics.py',), ('ssvep/figure.py',),
        ('ssvep/wearable.py',), ('ssvep/literature_comparator.py',), ('mi/run_mi.py',), ('mi/run_physionet.py','--last-subject','12'),
        ('paired/independent_replay.py',), ('ssvep/comparator_replay.py',), ('serial/run.py','--calibration-only'),
        ('serial/run.py',), ('serial/verify.py',),
    ]
    with tempfile.TemporaryDirectory() as cache:
        env=dict(os.environ, MPLCONFIGDIR=cache)
        for script,*args in steps:
            print('Running',script,*args,flush=True)
            subprocess.run([sys.executable,str(base/script),*args],check=True,cwd=base.parents[1],env=env)


if __name__=='__main__':
    main()
