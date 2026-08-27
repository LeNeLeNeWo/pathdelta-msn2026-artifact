#!/usr/bin/env python3
"""Freshly download and hash public inputs for the v8.5 comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "data/msn2026_v85_external_baselines"
GIT_SOURCES = (
    {
        "id": "cornetto_code",
        "url": "https://github.com/nsg-ethz/cornetto.git",
        "commit": "21641495fb6485c1d6d61d44597a58d87ed29de2",
        "destination": "cornetto_repo",
        "license": "MIT",
        "use": "author implementation provenance and adapted agent tool semantics",
    },
    {
        "id": "llm_netcfg_code",
        "url": "https://github.com/oscarGLira/LLM-based-Intelligent-Configuration-Validation-Framework.git",
        "commit": "2673a80efef576ba996f07d3b915f29ed88c880a",
        "destination": "llm_netcfg_repo",
        "license": "NO-LICENSE-FILE; inspect only, no source copied",
        "use": "prompt and orchestrator behavior provenance",
    },
)
PAPERS = (
    ("llm_netcfg_paper", "https://arxiv.org/pdf/2408.13298", "2408.13298.pdf"),
    ("inta_paper", "https://arxiv.org/pdf/2501.08760", "2501.08760.pdf"),
    ("cosynth_vpp_paper", "https://arxiv.org/pdf/2307.04945", "2307.04945.pdf"),
    ("agentic_repair_paper", "https://arxiv.org/pdf/2606.06212", "2606.06212.pdf"),
    ("cornetto_paper", "https://arxiv.org/pdf/2604.22513", "2604.22513.pdf"),
)
HF_URL = "https://huggingface.co/datasets/iprotogeros/cornetto-benchmark"
HF_COMMIT = "cdf3d68ecc47b4afe63e6b3ca8f5c07821c191bd"


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_record(root: Path) -> dict[str, object]:
    aggregate = hashlib.sha256()
    count = total = 0
    for path in sorted(
        item for item in root.rglob("*")
        if item.is_file() and ".git" not in item.relative_to(root).parts
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
    if destination.exists():
        shutil.rmtree(destination)
    subprocess.run(["git", "clone", "--no-checkout", source["url"], str(destination)], check=True)
    subprocess.run(["git", "fetch", "--depth", "1", "origin", source["commit"]], cwd=destination, check=True)
    subprocess.run(["git", "checkout", "--detach", source["commit"]], cwd=destination, check=True)
    actual = run("git", "rev-parse", "HEAD", cwd=destination)
    if actual != source["commit"]:
        raise RuntimeError(f"commit mismatch for {source['id']}: {actual}")
    return destination


def download_cornetto(raw: Path) -> tuple[Path, str]:
    destination = raw / "cornetto_dataset"
    if destination.exists():
        shutil.rmtree(destination)
    with tempfile.TemporaryDirectory() as temp:
        clone = Path(temp) / "repo"
        subprocess.run(["git", "clone", "--depth", "1", HF_URL, str(clone)], check=True)
        commit = run("git", "rev-parse", "HEAD", cwd=clone)
        if commit != HF_COMMIT:
            raise RuntimeError(f"Cornetto dataset revision mismatch: {commit}")
        shutil.move(str(clone / "main_dataset"), destination)
    return destination, commit


def download_papers(root: Path) -> list[dict[str, object]]:
    paper_root = root / "sources/papers"
    if paper_root.exists():
        shutil.rmtree(paper_root)
    paper_root.mkdir(parents=True)
    records = []
    for source_id, url, filename in PAPERS:
        target = paper_root / filename
        request = urllib.request.Request(url, headers={"User-Agent": "PathDelta-MSN2026-repro/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            target.write_bytes(response.read())
        records.append({
            "id": source_id,
            "url": url,
            "destination": f"papers/{filename}",
            "bytes": target.stat().st_size,
            "sha256": sha256(target),
            "license": "paper distribution terms; used as method specification",
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    raw = args.root / "sources/raw"
    raw.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for source in GIT_SOURCES:
        path = checkout(source, raw)
        entries.append({
            **source,
            "resolved_commit": run("git", "rev-parse", "HEAD", cwd=path),
            **tree_record(path),
        })
    dataset, resolved = download_cornetto(raw)
    entries.append({
        "id": "cornetto_public_dataset",
        "url": HF_URL,
        "requested_revision": HF_COMMIT,
        "resolved_revision": resolved,
        "destination": "cornetto_dataset",
        "license": "dataset repository metadata",
        "use": "fresh source configurations only; no published benchmark results",
        **tree_record(dataset),
    })
    entries.extend(download_papers(args.root))
    manifest = {
        "schema_version": "msn2026-v85-source-manifest-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fresh_download": True,
        "legacy_experiment_inputs": False,
        "source_code_reuse": {
            "cornetto": "MIT author code inspected; adapter reuses tool semantics, not dataset outputs",
            "llm_netcfg": "no repository license found; no code copied",
            "inta": "no public implementation located; paper-faithful reimplementation",
            "cosynth": "paper explicitly states the system was not built; paper-faithful reimplementation",
        },
        "sources": entries,
    }
    target = args.root / "source_manifest.json"
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
