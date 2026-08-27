#!/usr/bin/env python3
"""Freeze adaptive Full-U repair before opening the complete Batfish audit."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


FILES = (
    "experiments/msn2026_v84_agent_repair/run_union_repair.py",
    "experiments/msn2026_v84_agent_repair/run_agent128_repair_v2.py",
    "experiments/msn2026_v84_agent_repair/run_batfish_agent_final.py",
)


def main() -> None:
    payload = {
        "schema_version": "msn2026-v84-union-repair-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_DURING_BLINDED_FULL_BATFISH_AUDIT_AFTER_ONE_SMOKE_CASE",
        "prior_knowledge": "v8.3 cross-model audit already established target metric/origin omissions",
        "selection": "all Full-R completed cases receiving Batfish FAIL; N/A remains fail closed",
        "fixed_parameters": {"thinking_mode": "enabled", "temperature": 0.1, "max_submissions": 3, "max_completion_tokens": 8000},
        "inputs": [
            {"path": name, "sha256": hashlib.sha256(Path(name).read_bytes()).hexdigest()}
            for name in FILES
        ],
    }
    target = Path("experiments/msn2026_v84_agent_repair/union_repair_freeze.json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
