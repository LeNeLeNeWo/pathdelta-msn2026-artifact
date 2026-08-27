#!/usr/bin/env python3
"""Record the pre-full-run correction found by the four-case re-audit smoke."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    script = Path("experiments/msn2026_v84_agent_repair/run_batfish_union_reaudit.py")
    payload = {
        "schema_version": "msn2026-v84-union-reaudit-amendment-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "AMENDED_AFTER_4_CASE_SMOKE_BEFORE_33_CASE_REPAIR_COMPLETION",
        "reason": "Bookkeeping only: count original FAIL rows without an accepted registered repair as remaining fail-closed.",
        "smoke_result": "4/4 independent Batfish PASS; no verifier rule or agent prompt changed",
        "amended_script": {"path": script.as_posix(), "sha256": hashlib.sha256(script.read_bytes()).hexdigest()},
    }
    target = Path("experiments/msn2026_v84_agent_repair/union_reaudit_freeze_amendment.json")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
