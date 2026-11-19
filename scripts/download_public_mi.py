#!/usr/bin/env python3
"""Download unchanged official BNCI IV-2a MAT files with resumable range requests."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://lampx.tugraz.at/~bci/database/001-2014/"


def run(args):
    result = subprocess.run(["curl", "-L", "--fail", "-sS", "--connect-timeout", "20", *args], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-1000:])
    return result.stdout


def fetch_chunk(job):
    url, start, end, path = job
    size = end-start+1
    if path.exists() and path.stat().st_size == size:
        return path
    temp = path.with_suffix(".partial")
    header = path.with_suffix(".headers")
    run(["--retry", "3", "--max-time", "240", "--range", f"{start}-{end}",
         "-D", str(header), "-o", str(temp), url])
    expected = f"bytes {start}-{end}/"
    if expected not in header.read_text() or temp.stat().st_size != size:
        raise ValueError(f"Server did not return the exact requested byte range: {path.name}")
    temp.replace(path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subjects", nargs="+", type=int, default=[1])
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or any(s not in range(1,10) for s in args.subjects):
        parser.error("subjects 1..9; workers 1..8")
    directory = ROOT / "data/public_mi/bnci2014_001"
    directory.mkdir(parents=True, exist_ok=True)
    jobs = []; files = []
    for subject in args.subjects:
        for session in ("T", "E"):
            name = f"A{subject:02d}{session}.mat"; url = BASE+name
            header = run(["--head", "--max-time", "30", url]).decode()
            match = re.search(r"(?im)^Content-Length:\s*(\d+)", header)
            if not match:
                raise ValueError(f"No content length: {url}")
            length = int(match.group(1)); path = directory/name
            files.append((path, url, length))
            if path.exists() and path.stat().st_size == length:
                continue
            chunks = directory/(name+".chunks"); chunks.mkdir(exist_ok=True)
            for start in range(0, length, 1024*1024):
                jobs.append((url, start, min(length-1,start+1024*1024-1), chunks/f"{start:09d}.bin"))
    print(f"Download {len(files)} official MAT files; {len(jobs)} resumable chunks", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_chunk, job) for job in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            future.result()
            if i % 8 == 0 or i == len(futures):
                print(f"Downloaded chunks {i}/{len(futures)}", flush=True)
    manifest_path = directory/"download_manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest_by_name = {r["file"]:r for r in previous.get("files",[])}
    for path, url, length in files:
        if not path.exists() or path.stat().st_size != length:
            temp = path.with_suffix(".assembling")
            with temp.open("wb") as output:
                for chunk in sorted((directory/(path.name+".chunks")).glob("*.bin")):
                    output.write(chunk.read_bytes())
            if temp.stat().st_size != length:
                raise ValueError("Assembled download length mismatch")
            temp.replace(path)
        from scipy.io import loadmat
        payload = loadmat(path, variable_names=["data"])
        if "data" not in payload:
            raise ValueError(f"Invalid BNCI MAT file: {path}")
        manifest_by_name[path.name] = {"file": path.name, "source_url": url, "bytes": length,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = [manifest_by_name[k] for k in sorted(manifest_by_name)]
    manifest_path.write_text(json.dumps({"dataset": "BCI IV-2a / BNCI2014-001",
        "source": "https://bnci-horizon-2020.eu/database/data-sets", "license": "CC BY-ND 4.0",
        "raw_files_unchanged": True, "files": manifest}, indent=2)+"\n")
    print("Official MI downloads verified", flush=True)


if __name__ == "__main__":
    main()
