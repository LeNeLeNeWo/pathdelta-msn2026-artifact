#!/usr/bin/env python3
"""Analyze completed Agent128 cases without changing or re-running the agent."""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

METHODS = ("direct", "rag", "goal_only", "write_scope", "verifier_loop", "oracle_contract", "full_envelope")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def wilson(x: int, n: int, z: float = 1.959963984540054) -> list[float | None]:
    if not n:
        return [None, None]
    p = x / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return [max(0.0, center - radius), min(1.0, center + radius)]


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    return rows[min(len(rows) - 1, max(0, math.ceil(p * len(rows)) - 1))]


def failure_category(arm: Mapping[str, Any], max_submissions: int) -> str:
    if arm["verified_completion"]:
        return "verified_completion"
    final = arm["trace"][-1]["evaluation"]
    if arm["accepted"]:
        if not arm["oracle"]["goal"]:
            return "failed_intent_released"
        if arm["oracle"]["collateral_atoms"]:
            return "collateral_released"
        return "accepted_oracle_disagreement"
    if final.get("transaction_error"):
        base = "transaction_failure"
    elif final.get("evaluation_error") or final.get("syntax", {}).get("status") != "PASS":
        base = "unsupported_or_syntax"
    else:
        report = final.get("reports", {}).get("full", {})
        compliance = report.get("compliance", {})
        if not compliance.get("goal_success", False):
            base = "target_unmet"
        elif not compliance.get("semantic_frame_preserved", False):
            base = "semantic_collateral_fail_closed"
        elif not compliance.get("dependency_frame_preserved", False):
            base = "dependency_fail_closed"
        elif not compliance.get("hard_footprint_preserved", False):
            base = "footprint_fail_closed"
        else:
            base = "contract_rejected_other"
    if arm["submissions"] >= max_submissions:
        base += "_attempt_exhaustion"
    return base


def summarize(rows: Sequence[Mapping[str, Any]], method: str) -> dict[str, Any]:
    arms = [row["arms"][method] for row in rows]
    usages = [arm["llm_usage"] for arm in arms]
    calls = sum(u["logical_llm_calls"] for u in usages)
    collateral = sum(arm["accepted"] and arm["oracle"]["goal"] and not arm["oracle"]["safe"] for arm in arms)
    failed_intent = sum(arm["accepted"] and not arm["oracle"]["goal"] for arm in arms)
    verified = sum(arm["verified_completion"] for arm in arms)
    released = sum(arm["accepted"] for arm in arms)
    return {
        "method": method,
        "case_count": len(arms),
        "released": released,
        "final_goal_success": sum(arm["oracle"]["goal"] for arm in arms),
        "verified_completion": verified,
        "verified_completion_rate": verified / len(arms) if arms else None,
        "verified_completion_wilson95": wilson(verified, len(arms)),
        "collateral_release": collateral,
        "collateral_release_rate": collateral / len(arms) if arms else None,
        "failed_intent_release": failed_intent,
        "reject_to_repair_success": sum(arm["reject_to_repair_success"] for arm in arms),
        "attempt_exhaustion": sum(arm["submissions"] >= rows[i]["max_submissions"] and not arm["accepted"] for i, arm in enumerate(arms)),
        "total_submissions": sum(arm["submissions"] for arm in arms),
        "logical_llm_calls": calls,
        "backend_attempts": sum(u["backend_attempts"] for u in usages),
        "retry_count": sum(u["retry_count"] for u in usages),
        "prompt_tokens": sum(u["token_usage"]["prompt"] for u in usages),
        "completion_tokens": sum(u["token_usage"]["completion"] for u in usages),
        "total_tokens": sum(u["token_usage"]["total"] for u in usages),
        "latency_ms_total": sum(u["latency_ms"] for u in usages),
        "calls_per_case": calls / len(arms) if arms else None,
        "tokens_per_case": sum(u["token_usage"]["total"] for u in usages) / len(arms) if arms else None,
        "failure_categories": dict(Counter(failure_category(arm, rows[i]["max_submissions"]) for i, arm in enumerate(arms))),
    }


def main() -> None:
    data_root = Path("data/msn2026_v83_external")
    result_root = Path("results/msn2026_v83_external/agent128")
    rows = [load(path) for path in sorted((result_root / "cases").glob("*.json"))]
    subset = load(data_root / "agent128_subset.json")
    initial_labels = []
    for case in subset["cases"]:
        if not any(row["case_id"] == case["case_id"] for row in rows):
            continue
        label = load(data_root / "sealed/candidate_oracles" / case["scenario_id"] / f"{case['mode']}.json")
        initial_labels.append({"case_id": case["case_id"], "goal": label["goal_success"], "safe": label["safe"]})
    methods = [summarize(rows, method) for method in METHODS]
    strata: dict[str, Any] = {}
    for field in ("source_group", "vendor", "family", "mode"):
        strata[field] = {}
        for value in sorted({row[field] for row in rows}):
            selected = [row for row in rows if row[field] == value]
            strata[field][value] = [summarize(selected, method) for method in METHODS]
    all_calls = [call for row in rows for call in row["llm_metrics"]["calls"]]
    latencies = [float(call["latency_ms"]) for call in all_calls]
    output = {
        "schema_version": "msn2026-v83-agent-analysis-1.0",
        "completed_case_count": len(rows),
        "frozen_case_count": subset["case_count"],
        "complete": len(rows) == subset["case_count"],
        "initial_candidate_distribution": dict(Counter(
            "safe_goal" if x["goal"] and x["safe"] else "unsafe_goal" if x["goal"] else "non_goal"
            for x in initial_labels
        )),
        "methods": methods,
        "strata": strata,
        "all_backend_calls": {
            "logical_llm_calls": len(all_calls),
            "backend_attempts": sum(call["backend_attempts"] for call in all_calls),
            "retry_count": sum(call["retries"] for call in all_calls),
            "prompt_tokens": sum(call["usage"]["prompt"] for call in all_calls),
            "completion_tokens": sum(call["usage"]["completion"] for call in all_calls),
            "latency_ms_total": sum(latencies),
            "latency_ms_p50": statistics.median(latencies) if latencies else None,
            "latency_ms_p95": percentile(latencies, .95),
        },
        "accounting_note": "First candidates were generated by the independent red-team run and replayed here; its costs remain in redteam_generation/summary.json rather than being duplicated per arm.",
    }
    write(result_root / "analysis.json", output)
    print(json.dumps({k: v for k, v in output.items() if k != "strata"}, indent=2))


if __name__ == "__main__":
    main()
