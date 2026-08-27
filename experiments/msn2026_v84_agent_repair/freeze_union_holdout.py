#!/usr/bin/env python3
"""Freeze reuse of the union controller on the disjoint holdout."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    files = (
        "experiments/msn2026_v84_agent_repair/run_union_repair.py",
        "experiments/msn2026_v84_agent_repair/run_union_repair_holdout32.py",
        "experiments/msn2026_v84_agent_repair/run_batfish_union_holdout_reaudit.py",
        "experiments/msn2026_v84_agent_repair/union_repair_freeze.json",
        "data/msn2026_v84_agent_repair/agent_holdout32.json",
        "results/msn2026_v84_agent_repair/batfish_holdout32/summary.json",
    )
    payload = {
        "schema_version": "msn2026-v84-union-holdout-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_BEFORE_HOLDOUT_UNION_REPAIR",
        "mechanism_or_parameter_changes": False,
        "selection": "the 9 independent Batfish FAIL rows; the 1 N/A remains fail closed",
        "inputs": [{"path": name, "sha256": hashlib.sha256(Path(name).read_bytes()).hexdigest()} for name in files],
    }
    target = Path("experiments/msn2026_v84_agent_repair/union_holdout_freeze.json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
