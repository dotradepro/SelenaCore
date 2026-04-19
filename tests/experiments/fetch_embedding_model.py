#!/usr/bin/env python3
"""
tests/experiments/fetch_embedding_model.py — fetch an ONNX sentence-transformers
model into a target directory in the exact layout the core expects
(`model.onnx` + `tokenizer.json`).

Replaces the old sweep workflow that kept candidate models under
`_private/`. Models are now streamed directly from
HuggingFace on demand, same way `_provision_download_embedding` in
`core/api/routes/setup.py` handles the production download.

Usage
-----
    # Download the production default into the production path:
    python3 tests/experiments/fetch_embedding_model.py \\
        sentence-transformers/all-MiniLM-L6-v2 \\
        /var/lib/selena/models/embedding/all-MiniLM-L6-v2

    # Or into a temp dir for sweep comparison (see sweep_embedding_models.sh):
    python3 tests/experiments/fetch_embedding_model.py \\
        sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \\
        /tmp/sweep/paraphrase-multilingual-MiniLM-L12-v2

Catalog
-------
The following `repo_id` values are known to work (all sentence-transformers
families that ship ONNX exports on the hub):

    sentence-transformers/all-MiniLM-L6-v2            (~22 MB)   — prod default
    sentence-transformers/all-MiniLM-L12-v2           (~50 MB)
    sentence-transformers/all-mpnet-base-v2           (~150 MB)
    sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (~200 MB)
    intfloat/multilingual-e5-small                    (~200 MB)
    intfloat/multilingual-e5-base                     (~450 MB)

Each call downloads exactly two files: `onnx/model.onnx` and
`tokenizer.json`. For e5 / non-sentence-transformers repos that publish ONNX
under a different path, pass `--onnx-path` to override.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path


def fetch(repo_id: str, dest: Path, onnx_path: str, revision: str | None) -> None:
    rev = revision or "main"
    base = f"https://huggingface.co/{repo_id}/resolve/{rev}"
    files = {
        "model.onnx": f"{base}/{onnx_path}",
        "tokenizer.json": f"{base}/tokenizer.json",
    }
    dest.mkdir(parents=True, exist_ok=True)
    for fname, url in files.items():
        out = dest / fname
        if out.is_file() and out.stat().st_size > 0:
            print(f"  skip {fname} (already {out.stat().st_size:,} bytes)")
            continue
        print(f"  GET {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "selena-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(out, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        print(f"    -> {out} ({out.stat().st_size:,} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("repo_id", help="HuggingFace repo ID, e.g. sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("dest", type=Path, help="destination directory")
    ap.add_argument("--onnx-path", default="onnx/model.onnx",
                    help="path inside repo to the ONNX file (default: onnx/model.onnx)")
    ap.add_argument("--revision", default=None,
                    help="commit SHA or branch to pin (default: main)")
    args = ap.parse_args()
    print(f"fetching {args.repo_id} @ {args.revision or 'main'} -> {args.dest}")
    fetch(args.repo_id, args.dest, args.onnx_path, args.revision)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
