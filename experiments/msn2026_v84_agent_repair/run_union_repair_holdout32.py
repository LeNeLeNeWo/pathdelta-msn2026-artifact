#!/usr/bin/env python3
"""Apply the already-frozen union repair controller to held-out Batfish FAILs."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_agent128_repair_v2 as v84  # noqa: E402
import run_union_repair as union  # noqa: E402


AUDIT = Path("results/msn2026_v84_agent_repair/batfish_holdout32/summary.json")
OUTPUT = Path("results/msn2026_v84_agent_repair/union_repair_holdout32")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    audit_rows = [row for row in load(AUDIT)["rows"] if row["symbolic_frame_verdict"] == "FAIL"]
    data_root = Path("data/msn2026_v83_external")
    for ordinal, audit in enumerate(audit_rows, 1):
        case = {
            "case_id": f"{audit['scenario_id']}::{audit['mode']}",
            "scenario_id": audit["scenario_id"], "mode": audit["mode"],
        }
        started = time.perf_counter()
        row = union.run_one(case, audit, data_root, 3, 8000, 0.1, "enabled")
        row["agent_holdout"] = True
        row["controller_frozen_before_holdout_audit"] = True
        target = OUTPUT / "cases" / f"{case['scenario_id']}__{case['mode']}.json"
        v84.v83.write(target, row)
        print(f"union-holdout {ordinal}/{len(audit_rows)} {case['case_id']} "
              f"registered={row['arm']['accepted_registered']} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    rows = [load(path) for path in (OUTPUT / "cases").glob("*.json")]
    summary = {
        "schema_version": "msn2026-v84-union-holdout-repair-1.0",
        "case_count": len(audit_rows),
        "registered_pass": sum(row["arm"]["accepted_registered"] for row in rows),
        "registered_exhaustion": sum(not row["arm"]["accepted_registered"] for row in rows),
        "requires_batfish_reaudit": True,
    }
    v84.v83.write(OUTPUT / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
