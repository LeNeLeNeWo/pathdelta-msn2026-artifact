#!/usr/bin/env python3
"""Freeze a label-independent 128-event paired Agent subset."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path


SEED = 83127
COUNT = 128


def main() -> None:
    data_root = Path("data/msn2026_v83_external")
    paths = sorted((data_root / "public/candidates").glob("*/*.json"))
    rng = random.Random(SEED)
    rng.shuffle(paths)
    selected = paths[:COUNT]
    records = []
    aggregate = hashlib.sha256()
    for path in selected:
        payload = json.loads(path.read_text(encoding="utf-8"))
        relative = path.relative_to(data_root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row = {
            "case_id": f"{payload['scenario_id']}::{payload['mode']}",
            "scenario_id": payload["scenario_id"],
            "mode": payload["mode"],
            "candidate_path": relative,
            "candidate_file_sha256": digest,
        }
        records.append(row)
        aggregate.update(json.dumps(row, sort_keys=True).encode())
        aggregate.update(b"\n")
    output = {
        "schema_version": "msn2026-v83-agent-subset-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "case_count": len(records),
        "selection_used_labels": False,
        "aggregate_sha256": aggregate.hexdigest(),
        "cases": records,
    }
    target = data_root / "agent128_subset.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()

