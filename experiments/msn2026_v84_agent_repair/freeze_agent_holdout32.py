#!/usr/bin/env python3
"""Freeze a label-independent agent holdout disjoint from Agent128."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path


SEED = 84032
COUNT = 32


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    old_root = Path("data/msn2026_v83_external")
    new_root = Path("data/msn2026_v84_agent_repair")
    prior = json.loads((old_root / "agent128_subset.json").read_text(encoding="utf-8"))
    used = {row["candidate_path"] for row in prior["cases"]}
    available = [
        path for path in sorted((old_root / "public/candidates").glob("*/*.json"))
        if path.relative_to(old_root).as_posix() not in used
    ]
    rng = random.Random(SEED)
    rng.shuffle(available)
    selected = available[:COUNT]

    rows: list[dict[str, str]] = []
    aggregate = hashlib.sha256()
    for path in selected:
        # Candidate files contain edits but no sealed oracle records.  Selection
        # uses only identity fields and never opens the sealed label directory.
        payload = json.loads(path.read_text(encoding="utf-8"))
        relative = path.relative_to(old_root).as_posix()
        row = {
            "case_id": f"{payload['scenario_id']}::{payload['mode']}",
            "scenario_id": payload["scenario_id"],
            "mode": payload["mode"],
            "candidate_path": relative,
            "candidate_file_sha256": sha256(path),
        }
        rows.append(row)
        aggregate.update(json.dumps(row, sort_keys=True).encode())
        aggregate.update(b"\n")

    output = {
        "schema_version": "msn2026-v84-agent-holdout-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "case_count": len(rows),
        "source_candidate_count": len(available),
        "excluded_agent128_count": len(used),
        "selection_used_labels": False,
        "selection_opened_sealed_oracles": False,
        "disjoint_from_agent128": not any(row["candidate_path"] in used for row in rows),
        "aggregate_sha256": aggregate.hexdigest(),
        "cases": rows,
    }
    new_root.mkdir(parents=True, exist_ok=True)
    target = new_root / "agent_holdout32.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
