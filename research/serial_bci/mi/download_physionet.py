"""Download the three unilateral motor-imagery runs from each PhysioNet participant."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

DATA = Path(__file__).resolve().parents[3] / 'data/public_mi/eegmmidb'
BASE = 'https://physionet-open.s3.amazonaws.com/eegmmidb/1.0.0'


def fetch(path):
    url = f'{BASE}/{path.parent.name}/{path.name}'
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.part')
    subprocess.run(['curl', '-fsSL', '--retry', '2', '--continue-at', '-', '--max-time', '300',
                    '-o', str(tmp), url], check=True)
    with tmp.open('rb') as f:
        header = f.read(256)
    expected_header = int(header[184:192])
    records = int(header[236:244])
    nchan = int(header[252:256])
    with tmp.open('rb') as f:
        f.seek(256+216*nchan)
        per_record = sum(int(f.read(8)) for _ in range(nchan))
    if tmp.stat().st_size != expected_header + records*2*per_record:
        raise ValueError(f'Invalid EDF length: {path.name}')
    tmp.replace(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--last-subject', type=int, choices=range(1,110), default=12, metavar='1..109')
    args = parser.parse_args()
    paths = [args.data/f'S{s:03d}'/f'S{s:03d}R{r:02d}.edf' for s in range(1,args.last_subject+1) for r in (4,8,12)]
    with ThreadPoolExecutor(8) as workers:
        for i, _ in enumerate(workers.map(fetch, paths), 1):
            if i % 30 == 0:
                print(f'{i}/{len(paths)}', flush=True)
