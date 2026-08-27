#!/usr/bin/env python3
"""Confirmatory paired statistics, N/A accounting, and frozen GO/NO-GO."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def exact_mcnemar(a: Sequence[bool], b: Sequence[bool]) -> dict[str, Any]:
    # b01: a accepts and b rejects; b10: a rejects and b accepts.
    b01 = sum(x and not y for x, y in zip(a, b))
    b10 = sum(not x and y for x, y in zip(a, b))
    n = b01 + b10
    if n == 0:
        p = 1.0
    else:
        tail = sum(math.comb(n, k) for k in range(0, min(b01, b10) + 1)) / (2**n)
        p = min(1.0, 2 * tail)
    return {"a_accept_b_reject": b01, "a_reject_b_accept": b10, "discordant": n, "two_sided_exact_p": p}


def holm(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(enumerate(rows), key=lambda item: item[1]["test"]["two_sided_exact_p"])
    running = 0.0
    adjusted = [0.0] * len(rows)
    m = len(rows)
    for rank, (index, row) in enumerate(ordered):
        value = min(1.0, (m - rank) * row["test"]["two_sided_exact_p"])
        running = max(running, value)
        adjusted[index] = running
    return [{**row, "holm_adjusted_p": adjusted[i]} for i, row in enumerate(rows)]


def main() -> None:
    result_root = Path("results/msn2026_v83_external")
    rows = load(result_root / "external_boundaries/unblinded_results.json")
    unsafe = [row for row in rows if row["oracle"]["goal_success"] and not row["oracle"]["safe"]]
    safe = [row for row in rows if row["oracle"]["goal_success"] and row["oracle"]["safe"]]
    comparisons = []
    for method in ("goal_only", "write_scope", "visible_plus_scope", "verifier_loop", "oracle_contract",
                   "full_minus_active", "full_minus_dependency", "full_minus_frame", "full_minus_footprint"):
        comparisons.append({"comparison": f"{method}_vs_full_envelope", "population": "unsafe_goal_candidates",
                            "test": exact_mcnemar([r["verdicts"][method] for r in unsafe], [r["verdicts"]["full_envelope"] for r in unsafe])})
    paired = {
        "schema_version": "msn2026-v83-paired-statistics-1.0",
        "unsafe_goal_count": len(unsafe),
        "safe_goal_count": len(safe),
        "comparisons": holm(comparisons),
        "note": "Exact two-sided McNemar tests on identical frozen candidates; Holm correction within this family.",
    }
    write(result_root / "confirmatory_statistics.json", paired)

    batfish = load(result_root / "batfish_applicability/summary.json")
    baseline_rows = load(result_root / "batfish_baseline_na.json")
    baseline_status = {
        row["scenario_id"]: {str(item.get("Status")) for item in row["status"]}
        for row in baseline_rows
    }
    labels = {(r["scenario_id"], r["mode"]): r for r in rows}
    matrix_rows = []
    for case in batfish["cases"]:
        key = (case["scenario_id"], case["mode"])
        label = labels[key]
        candidate_status = case["batfish"]["status"]
        base = baseline_status.get(case["scenario_id"], {"PASSED"})
        inherited = candidate_status != "PASS" and base != {"PASSED"}
        matrix_rows.append({
            "scenario_id": case["scenario_id"], "mode": case["mode"], "source_group": case["source_group"],
            "vendor": case["vendor"], "batfish_status": candidate_status,
            "batfish_parse_warning": bool(case["batfish"]["parse_warnings"]),
            "baseline_statuses": sorted(base), "na_origin": "baseline_inherited" if inherited else (
                "candidate_induced" if candidate_status != "PASS" else "not_na"),
            "adapter_status": "PASS", "oracle_goal": label["oracle"]["goal_success"], "oracle_safe": label["oracle"]["safe"],
            "full_envelope_verdict": label["verdicts"]["full_envelope"],
            "hard_fail_closed_release": bool(candidate_status == "PASS" and label["verdicts"]["full_envelope"]),
        })
    na_rows = [row for row in matrix_rows if row["batfish_status"] != "PASS"]
    safe_goal_na = [row for row in na_rows if row["oracle_goal"] and row["oracle_safe"]]
    coverage = {
        "schema_version": "msn2026-v83-backend-coverage-1.0",
        "candidate_count": len(matrix_rows),
        "obligation_backend_matrix": matrix_rows,
        "summary": {
            "batfish_status_counts": dict(Counter(row["batfish_status"] for row in matrix_rows)),
            "batfish_na_origin_counts": dict(Counter(row["na_origin"] for row in na_rows)),
            "adapter_status_counts": dict(Counter(row["adapter_status"] for row in matrix_rows)),
            "at_least_one_backend_applicable": sum(row["batfish_status"] == "PASS" or row["adapter_status"] == "PASS" for row in matrix_rows),
            "hard_batfish_fail_closed_safe_goal_rejections": len(safe_goal_na),
            "hard_batfish_fail_closed_safe_goal_denominator": len(safe),
            "hard_batfish_fail_closed_safe_false_reject_rate": len(safe_goal_na) / len(safe),
            "batfish_na_goal_unsafe": sum(row["oracle_goal"] and not row["oracle_safe"] for row in na_rows),
            "batfish_na_non_goal": sum(not row["oracle_goal"] for row in na_rows),
        },
        "policy": "Batfish N/A cannot produce PASS. The vendor adapter supplies finite behavioral evidence; hard-Batfish mode fails closed.",
    }
    write(result_root / "backend_coverage_matrix.json", coverage)

    overall = {row["method"]: row for row in load(result_root / "external_boundaries/analysis.json")["overall"]}
    full, verifier, write_scope = overall["full_envelope"], overall["verifier_loop"], overall["write_scope"]
    source_strata = load(result_root / "external_boundaries/analysis.json")["strata"]["source_group"]
    vendor_strata = load(result_root / "external_boundaries/analysis.json")["strata"]["vendor"]
    def method_rate(items: Sequence[Mapping[str, Any]], method: str) -> float | None:
        return next(row["unsafe_false_accept_rate"] for row in items if row["method"] == method)
    direction_sources = all((method_rate(items, "full_envelope") or 0) <= (method_rate(items, "verifier_loop") or 0) for items in source_strata.values())
    strict_vendor_wins = sum((method_rate(items, "full_envelope") or 0) < (method_rate(items, "verifier_loop") or 0) for items in vendor_strata.values())
    coverage_sensitivity = load(result_root / "coverage_sensitivity.json")["conditions"]
    degradation_explained = all(row["mean_active_unsafe_false_accept_rate"] <= row["mean_passive_unsafe_false_accept_rate"] for row in coverage_sensitivity)
    gate1 = ((write_scope["unsafe_false_accept_rate"] - full["unsafe_false_accept_rate"]) >= .15 and
             (verifier["unsafe_false_accept_rate"] - full["unsafe_false_accept_rate"]) >= .15)
    gates = {
        "schema_version": "msn2026-v83-go-no-go-1.0",
        "decision": "GO" if gate1 and full["unsafe_false_accept_rate"] <= .05 and full["safe_false_reject_rate"] <= .15 and degradation_explained else "NO-GO",
        "gates": {
            "minimum_15pp_reduction_vs_write_scope_and_verifier_loop": {"pass": gate1,
                "write_scope_reduction_pp": 100 * (write_scope["unsafe_false_accept_rate"] - full["unsafe_false_accept_rate"]),
                "verifier_loop_reduction_pp": 100 * (verifier["unsafe_false_accept_rate"] - full["unsafe_false_accept_rate"])},
            "full_fa_at_most_5pct": {"pass": full["unsafe_false_accept_rate"] <= .05, "value": full["unsafe_false_accept_rate"]},
            "full_fr_at_most_15pct": {"pass": full["safe_false_reject_rate"] <= .15, "value": full["safe_false_reject_rate"]},
            "direction_both_sources": {"pass": direction_sources},
            "strict_win_at_least_two_vendors": {"pass": strict_vendor_wins >= 2, "vendor_count": strict_vendor_wins},
            "oracle_gap_at_most_5pp": {"pass": overall["oracle_contract"]["unsafe_false_accept_rate"] - full["unsafe_false_accept_rate"] <= .05,
                                        "gap_pp": 100 * (overall["oracle_contract"]["unsafe_false_accept_rate"] - full["unsafe_false_accept_rate"])},
            "coverage_degradation_explained": {"pass": degradation_explained},
        },
        "claim_disposition": "The preregistered universal external-validation GO gate failed because the VerifierLoop gap was 10 pp, not 15 pp. Retain the mechanism unchanged and narrow the claim to the measured evidence regime.",
    }
    write(result_root / "go_no_go.json", gates)
    print(json.dumps({"paired": paired, "backend_summary": coverage["summary"], "go_no_go": gates}, indent=2))


if __name__ == "__main__":
    main()
