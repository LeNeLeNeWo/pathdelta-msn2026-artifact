#!/usr/bin/env python3
"""Freeze union re-audit composition before repair outputs are available."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    files = (
        "experiments/msn2026_v84_agent_repair/run_batfish_union_reaudit.py",
        "experiments/msn2026_v84_agent_repair/run_batfish_agent_final.py",
        "experiments/msn2026_v84_agent_repair/union_repair_freeze.json",
        "results/msn2026_v84_agent_repair/batfish_agent128/summary.json",
    )
    payload = {
        "schema_version": "msn2026-v84-union-reaudit-freeze-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "FROZEN_BEFORE_UNION_REPAIR_COMPLETION",
        "composition": "88 unchanged original PASS + independently re-audited repair PASS; 7 original N/A remains fail closed",
        "inputs": [{"path": name, "sha256": hashlib.sha256(Path(name).read_bytes()).hexdigest()} for name in files],
    }
    target = Path("experiments/msn2026_v84_agent_repair/union_reaudit_freeze.json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
