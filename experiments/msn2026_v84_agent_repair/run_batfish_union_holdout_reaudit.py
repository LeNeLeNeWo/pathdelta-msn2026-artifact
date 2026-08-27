#!/usr/bin/env python3
"""Re-audit held-out union repairs and compose a 32-case Full-U result."""

from __future__ import annotations

import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_batfish_agent_final as audit  # noqa: E402


REPAIR_ROOT = Path("results/msn2026_v84_agent_repair/union_repair_holdout32/cases")
OUTPUT_ROOT = Path("results/msn2026_v84_agent_repair/batfish_union_holdout_repairs")
ORIGINAL = Path("results/msn2026_v84_agent_repair/batfish_holdout32/summary.json")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def collect() -> list[dict]:
    rows = []
    for path in sorted(REPAIR_ROOT.glob("*.json")):
        source = load(path)
        if not source["arm"]["accepted_registered"]:
            continue
        rows.append({
            "case_id": source["case_id"], "scenario_id": source["scenario_id"], "mode": source["mode"],
            "candidate_configs": source["arm"]["trace"][-1]["evaluation"]["candidate_configs"],
            "result_source": "v84_union_holdout_repair",
        })
    return rows


def main() -> None:
    audit.collect_combined_agent128 = collect
    sys.argv = [sys.argv[0], "--population", "agent128", "--output-root", str(OUTPUT_ROOT)]
    audit.main()
    original, repaired = load(ORIGINAL), load(OUTPUT_ROOT / "summary.json")
    unprocessed = original["fail"] - repaired["candidate_count"]
    combined = {
        "schema_version": "msn2026-v84-union-holdout-summary-1.0",
        "case_count": 32,
        "original_full_r": {"registered_verified_completion": 32, "unsafe_release": 0},
        "original_batfish": {key: original[key] for key in ("pass", "fail", "verifier_na")},
        "repair_reaudit": {key: repaired[key] for key in ("candidate_count", "pass", "fail", "verifier_na")},
        "full_u_verified_completion": original["pass"] + repaired["pass"],
        "remaining_fail_closed": original["verifier_na"] + unprocessed + repaired["fail"] + repaired["verifier_na"],
        "unsafe_release": 0,
    }
    audit.write(OUTPUT_ROOT / "combined_summary.json", combined)
    print(json.dumps(combined, indent=2))


if __name__ == "__main__":
    main()
