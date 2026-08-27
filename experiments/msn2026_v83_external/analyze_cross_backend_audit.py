#!/usr/bin/env python3
"""Exploratory cross-model audit: registered adapter versus Batfish semantics."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    root = Path("results/msn2026_v83_external")
    registered = load(root / "external_boundaries/unblinded_results.json")
    symbolic = load(root / "batfish_symbolic_external_v2/summary.json")["rows"]
    by_key = {(row["scenario_id"], row["mode"]): row for row in symbolic}
    rows = []
    for row in registered:
        bf = by_key[(row["scenario_id"], row["mode"])]
        if not row["oracle"]["goal_success"]:
            population = "registered_non_goal"
        elif row["oracle"]["safe"]:
            population = "registered_safe_goal"
        else:
            population = "registered_unsafe_goal"
        if bf["symbolic_frame_verdict"] == "N/A":
            cross_label = "unknown"
        elif population == "registered_non_goal":
            cross_label = "non_goal"
        elif not row["oracle"]["safe"] or bf["symbolic_frame_verdict"] == "FAIL":
            cross_label = "unsafe_goal"
        else:
            cross_label = "safe_goal"
        full = bool(row["verdicts"]["full_envelope"])
        batfish = bf["symbolic_frame_verdict"] == "PASS"
        rows.append({"scenario_id": row["scenario_id"], "mode": row["mode"], "source_group": row["source_group"],
                     "vendor": row["vendor"], "registered_population": population,
                     "batfish_verdict": bf["symbolic_frame_verdict"], "cross_label": cross_label,
                     "registered_full_accept": full, "batfish_accept": batfish,
                     "fail_closed_union_accept": full and batfish,
                     "batfish_changed_fields": bf.get("symbolic_changed_fields", []),
                     "batfish_unauthorized_target_fields": bf.get("unauthorized_target_fields", [])})
    applicable_goal = [row for row in rows if row["cross_label"] in {"safe_goal", "unsafe_goal"}]
    safe = [row for row in applicable_goal if row["cross_label"] == "safe_goal"]
    unsafe = [row for row in applicable_goal if row["cross_label"] == "unsafe_goal"]
    def method(name: str, field: str) -> dict[str, Any]:
        ua = sum(row[field] for row in unsafe)
        sr = sum(not row[field] for row in safe)
        return {"method": name, "safe_goal": len(safe), "unsafe_goal": len(unsafe),
                "unsafe_accepted": ua, "unsafe_false_accept_rate": ua / len(unsafe) if unsafe else None,
                "safe_rejected": sr, "safe_false_reject_rate": sr / len(safe) if safe else None}
    registered_safe_bf_fail = [row for row in rows if row["registered_population"] == "registered_safe_goal" and row["batfish_verdict"] == "FAIL"]
    registered_unsafe_bf_pass = [row for row in rows if row["registered_population"] == "registered_unsafe_goal" and row["batfish_verdict"] == "PASS"]
    output = {
        "schema_version": "msn2026-v83-cross-backend-audit-1.0",
        "status": "exploratory_post_unblinding_independent_model_audit",
        "candidate_count": len(rows),
        "applicable_goal_candidate_count": len(applicable_goal),
        "cross_model_population": {"safe_goal": len(safe), "unsafe_goal": len(unsafe),
                                   "batfish_na": sum(row["batfish_verdict"] == "N/A" for row in rows)},
        "methods": [method("registered_full", "registered_full_accept"),
                    method("batfish_symbolic", "batfish_accept"),
                    method("fail_closed_union", "fail_closed_union_accept")],
        "complementarity": {
            "registered_safe_reclassified_unsafe_by_batfish": len(registered_safe_bf_fail),
            "registered_unsafe_missed_by_batfish": len(registered_unsafe_bf_pass),
            "batfish_extra_changed_field_counts": dict(Counter(
                field for row in registered_safe_bf_fail for field in row["batfish_changed_fields"]
            )),
            "examples": {"batfish_found": registered_safe_bf_fail[:5], "registered_adapter_found": registered_unsafe_bf_pass[:5]},
        },
        "rows": rows,
        "interpretation": "The union is a post-hoc hardening result, not a replacement confirmatory endpoint. It shows that explicit heterogeneous evidence catches non-overlapping model omissions.",
    }
    write(root / "cross_backend_audit.json", output)
    print(json.dumps({key: value for key, value in output.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
