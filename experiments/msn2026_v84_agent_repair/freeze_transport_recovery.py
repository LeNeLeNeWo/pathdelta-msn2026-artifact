#!/usr/bin/env python3
"""Freeze transport-only recovery after incident detection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    files = (
        "experiments/msn2026_v84_agent_repair/resume_after_transport_incident.py",
        "experiments/msn2026_v84_agent_repair/transport_incident_001.md",
        "experiments/msn2026_v84_agent_repair/agent_holdout32_freeze.json",
        "experiments/msn2026_v84_agent_repair/union_repair_freeze.json",
    )
    payload = {
        "schema_version": "msn2026-v84-transport-recovery-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_BEFORE_SEQUENTIAL_RECOVERY",
        "eligibility": "missing cases or failed traces containing a listed transport marker only",
        "semantic_parameters_changed": False,
        "max_whole_case_transport_rounds": 3,
        "inputs": [{"path": name, "sha256": hashlib.sha256(Path(name).read_bytes()).hexdigest()} for name in files],
    }
    target = Path("experiments/msn2026_v84_agent_repair/transport_recovery_freeze.json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
