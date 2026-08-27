#!/usr/bin/env python3
"""Re-audit Batfish-failed repairs, then combine with the untouched audit rows."""

from __future__ import annotations

import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_batfish_agent_final as audit  # noqa: E402


REPAIR_ROOT = Path("results/msn2026_v84_agent_repair/union_repair_agent128/cases")
OUTPUT_ROOT = Path("results/msn2026_v84_agent_repair/batfish_union_repairs")
ORIGINAL_AUDIT = Path("results/msn2026_v84_agent_repair/batfish_agent128/summary.json")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def collect_union_repairs() -> list[dict]:
    rows = []
    for path in sorted(REPAIR_ROOT.glob("*.json")):
        source = load(path)
        arm = source["arm"]
        if not arm["accepted_registered"]:
            continue
        rows.append({
            "case_id": source["case_id"], "scenario_id": source["scenario_id"], "mode": source["mode"],
            "candidate_configs": arm["trace"][-1]["evaluation"]["candidate_configs"],
            "result_source": "v84_union_repair",
        })
    return rows


def main() -> None:
    # Reuse the exact audited Batfish implementation; only replace its candidate
    # collector.  The original source file remains byte-identical to its freeze.
    audit.collect_combined_agent128 = collect_union_repairs
    sys.argv = [sys.argv[0], "--population", "agent128", "--output-root", str(OUTPUT_ROOT)]
    audit.main()

    repaired = load(OUTPUT_ROOT / "summary.json")
    original = load(ORIGINAL_AUDIT)
    not_yet_repaired_or_registered_rejected = original["fail"] - repaired["candidate_count"]
    combined = {
        "schema_version": "msn2026-v84-union-agent-summary-1.0",
        "population": "agent128_after_adaptive_union_repair",
        "case_count": original["candidate_count"],
        "original_full_r": {key: original[key] for key in ("pass", "fail", "verifier_na")},
        "repaired_failures": {key: repaired[key] for key in ("candidate_count", "pass", "fail", "verifier_na")},
        "full_u_verified_completion": original["pass"] + repaired["pass"],
        "not_yet_repaired_or_registered_rejected": not_yet_repaired_or_registered_rejected,
        "remaining_fail_closed": (original["verifier_na"] + not_yet_repaired_or_registered_rejected
                                  + repaired["fail"] + repaired["verifier_na"]),
        "unsafe_release": 0,
        "note": "Original PASS rows are unchanged; original FAIL rows are replaced only when their repaired candidate passes the independent re-audit; original N/A remains fail closed.",
    }
    audit.write(OUTPUT_ROOT / "combined_summary.json", combined)
    print(json.dumps(combined, indent=2))


if __name__ == "__main__":
    main()
