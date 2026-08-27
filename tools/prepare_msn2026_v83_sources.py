#!/usr/bin/env python3
"""Fetch and hash the fresh public sources for the v8.3 external study."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "data/msn2026_v83_external"
SOURCES = (
    {
        "id": "cornetto_code",
        "url": "https://github.com/nsg-ethz/cornetto.git",
        "commit": "21641495fb6485c1d6d61d44597a58d87ed29de2",
        "destination": "cornetto_repo",
        "license": "MIT",
    },
    {
        "id": "test_pyramid",
        "url": "https://github.com/intentionet/test-pyramid.git",
        "commit": "2e407de5a59db2145eaf17fc42501d2f784f866e",
        "destination": "test_pyramid",
        "license": "Apache-2.0",
    },
)
HF_URL = "https://huggingface.co/datasets/iprotogeros/cornetto-benchmark"
HF_COMMIT = "cdf3d68ecc47b4afe63e6b3ca8f5c07821c191bd"


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_record(root: Path) -> dict[str, object]:
    aggregate = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(
        p for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    ):
        relative = path.relative_to(root).as_posix()
        digest = sha256(path)
        size = path.stat().st_size
        aggregate.update(relative.encode())
        aggregate.update(b"\0")
        aggregate.update(digest.encode())
        aggregate.update(b"\n")
        count += 1
        total += size
    return {"file_count": count, "bytes": total, "tree_sha256": aggregate.hexdigest()}


def checkout(source: dict[str, str], raw: Path) -> Path:
    destination = raw / source["destination"]
    if not (destination / ".git").exists():
        subprocess.run(["git", "clone", source["url"], str(destination)], check=True)
    subprocess.run(["git", "fetch", "--depth", "1", "origin", source["commit"]], cwd=destination, check=True)
    subprocess.run(["git", "checkout", "--detach", source["commit"]], cwd=destination, check=True)
    actual = run("git", "rev-parse", "HEAD", cwd=destination)
    if actual != source["commit"]:
        raise RuntimeError(f"commit mismatch for {source['id']}: {actual}")
    return destination


def download_cornetto(raw: Path) -> tuple[Path, str]:
    destination = raw / "cornetto_dataset"
    if destination.exists() and any(destination.iterdir()):
        return destination, HF_COMMIT
    with tempfile.TemporaryDirectory() as temp:
        clone = Path(temp) / "repo"
        subprocess.run(["git", "clone", "--depth", "1", HF_URL, str(clone)], check=True)
        commit = run("git", "rev-parse", "HEAD", cwd=clone)
        if commit != HF_COMMIT:
            raise RuntimeError(f"Cornetto dataset revision mismatch: {commit}")
        shutil.move(str(clone / "main_dataset"), destination)
    return destination, commit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    raw = args.root / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    entries = []
    for source in SOURCES:
        path = checkout(source, raw)
        entries.append({**source, "resolved_commit": run("git", "rev-parse", "HEAD", cwd=path), **tree_record(path)})
    dataset, resolved = download_cornetto(raw)
    entries.append({
        "id": "cornetto_public_dataset",
        "url": HF_URL,
        "requested_revision": HF_COMMIT,
        "resolved_revision": resolved,
        "destination": "cornetto_dataset",
        "license": "dataset repository metadata",
        **tree_record(dataset),
    })
    manifest = {
        "schema_version": "msn2026-v83-source-manifest-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fresh_download": True,
        "legacy_experiment_inputs": False,
        "sources": entries,
    }
    target = args.root / "source_manifest.json"
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
