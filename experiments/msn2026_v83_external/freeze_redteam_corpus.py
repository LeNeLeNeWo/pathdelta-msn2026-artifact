#!/usr/bin/env python3
"""Strip labels from red-team candidates and write a pre-evaluation receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> tuple[int, str]:
    aggregate = hashlib.sha256()
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        aggregate.update(relative.encode())
        aggregate.update(b"\0")
        aggregate.update(sha256(path).encode())
        aggregate.update(b"\n")
        count += 1
    return count, aggregate.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--generation-root", type=Path, default=Path("results/msn2026_v83_external/redteam_generation"))
    parser.add_argument("--receipt", type=Path, default=Path("results/msn2026_v83_external/redteam_corpus_receipt.json"))
    args = parser.parse_args()
    public_root = args.data_root / "public/candidates"
    if public_root.exists():
        shutil.rmtree(public_root)
    records = []
    for path in sorted((args.generation_root / "cases").glob("*.json")):
        case = load(path)
        for candidate in case["candidates"]:
            if not candidate.get("applied"):
                continue
            public = {
                "scenario_id": case["scenario_id"],
                "mode": candidate["mode"],
                "edits": candidate["edits"],
                "candidate_configs": candidate["candidate_configs"],
                "candidate_sha256": candidate["candidate_sha256"],
                "source_group": case["source_group"],
                "vendor": case["vendor"],
                "family": case["family"],
                "oracle_label_exposed": False,
            }
            target = public_root / case["scenario_id"] / f"{candidate['mode']}.json"
            write(target, public)
            records.append({"scenario_id": case["scenario_id"], "mode": candidate["mode"], "path": str(target), "sha256": sha256(target)})
    public_count, public_hash = tree_hash(public_root)
    label_count, label_hash = tree_hash(args.data_root / "sealed/candidate_oracles")
    receipt = {
        "schema_version": "msn2026-v83-redteam-receipt-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(records),
        "public_file_count": public_count,
        "public_tree_sha256": public_hash,
        "sealed_label_file_count": label_count,
        "sealed_label_tree_sha256": label_hash,
        "generation_summary_sha256": sha256(args.generation_root / "summary.json"),
        "labels_present_in_public_corpus": False,
        "envelope_verdicts_computed_before_receipt": False,
        "candidates": records,
    }
    write(args.receipt, receipt)
    print(json.dumps({key: value for key, value in receipt.items() if key != "candidates"}, indent=2))


if __name__ == "__main__":
    main()

