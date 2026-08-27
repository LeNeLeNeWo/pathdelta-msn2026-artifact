#!/usr/bin/env python3
"""Hash the controller, fixed parameters, and disjoint holdout before execution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


FILES = (
    "experiments/msn2026_v84_agent_repair/run_agent128_repair_v2.py",
    "experiments/msn2026_v84_agent_repair/run_agent_holdout32.py",
    "experiments/msn2026_v84_agent_repair/freeze_agent_holdout32.py",
    "data/msn2026_v84_agent_repair/agent_holdout32.json",
)


def main() -> None:
    rows = []
    for name in FILES:
        path = Path(name)
        rows.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    payload = {
        "schema_version": "msn2026-v84-agent-holdout-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_BEFORE_HOLDOUT_EXECUTION",
        "selection": "32 candidates selected without labels from the 125 candidates disjoint from Agent128",
        "fixed_parameters": {
            "thinking_mode": "enabled",
            "temperature": 0.1,
            "max_submissions": 8,
            "max_completion_tokens": 8000,
        },
        "inputs": rows,
    }
    target = Path("experiments/msn2026_v84_agent_repair/agent_holdout32_freeze.json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
