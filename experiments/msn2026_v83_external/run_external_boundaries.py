#!/usr/bin/env python3
"""Blindly evaluate fixed boundaries on the frozen external candidate corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (  # noqa: E402
    BehaviorRecord,
    derive_change_envelope_v2,
)
from experiments.msn2026_v8_day2_agent.semantic_metrics import build_blast_radius_report  # noqa: E402
from experiments.msn2026_v83_external.vendor_policy_adapter import (  # noqa: E402
    behavior_rows,
    build_dependency_graph,
)


METHODS = (
    "goal_only",
    "write_scope",
    "visible_plus_scope",
    "verifier_loop",
    "oracle_contract",
    "full_envelope",
    "full_minus_active",
    "full_minus_dependency",
    "full_minus_frame",
    "full_minus_footprint",
    "full_minus_coverage_provenance",
)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(rows: Sequence[Mapping[str, Any]]) -> list[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in rows]


def post_records(candidate: Mapping[str, str], pre: Sequence[BehaviorRecord]) -> list[BehaviorRecord]:
    by_key: dict[tuple[str, str], list[BehaviorRecord]] = {}
    for row in pre:
        by_key.setdefault((row.device, row.subject), []).append(row)
    output: list[BehaviorRecord] = []
    for (device, subject), group in by_key.items():
        rows = behavior_rows(device, candidate[device], subject, [row.fec for row in group], "v83_external_post_adapter")
        output.extend(records(rows))
    return output


def envelope(intent: Mapping[str, Any], baseline: Mapping[str, str], pre: Sequence[BehaviorRecord], backend: str) -> Any:
    return derive_change_envelope_v2(
        intent,
        baseline,
        pre,
        build_dependency_graph(baseline),
        behavior_universe_provenance={
            "backend": backend,
            "complete": False,
            "uncovered_reason": "finite public-policy equivalence representatives",
            "candidate_patch_used": False,
        },
    )


def report(baseline: Mapping[str, str], candidate: Mapping[str, str], pre: Sequence[BehaviorRecord], env: Any) -> dict[str, Any]:
    return build_blast_radius_report(
        baseline,
        candidate,
        pre,
        post_records(candidate, pre),
        env,
        build_dependency_graph(baseline),
        build_dependency_graph(candidate),
    ).to_dict()


def method_verdicts(full: Mapping[str, Any], passive: Mapping[str, Any]) -> dict[str, bool]:
    fc = full["compliance"]
    pc = passive["compliance"]
    goal = bool(fc["goal_success"])
    visible_frame = bool(pc["semantic_frame_preserved"])
    visible_scope = bool(pc["hard_footprint_preserved"])
    return {
        "goal_only": goal,
        "write_scope": goal and bool(fc["hard_footprint_preserved"]),
        "visible_plus_scope": bool(pc["goal_success"] and visible_frame and visible_scope),
        # This is the fixed contract used by the Batfish verifier-loop arm:
        # target predicates plus differential checks over operator-visible tests.
        "verifier_loop": bool(pc["goal_success"] and visible_frame),
        # The oracle contract is a manual complete target/non-target behavior
        # specification. It is an upper bound and emits no patch strategy.
        "oracle_contract": goal and bool(fc["semantic_frame_preserved"]),
        "full_envelope": bool(fc["envelope_compliance"]),
        "full_minus_active": bool(pc["envelope_compliance"]),
        "full_minus_dependency": goal and bool(fc["semantic_frame_preserved"]) and bool(fc["hard_footprint_preserved"]),
        "full_minus_frame": goal and bool(fc["dependency_frame_preserved"]) and bool(fc["hard_footprint_preserved"]),
        "full_minus_footprint": goal and bool(fc["semantic_frame_preserved"]) and bool(fc["dependency_frame_preserved"]),
        "full_minus_coverage_provenance": bool(fc["envelope_compliance"]),
    }


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float | None]:
    if total == 0:
        return [None, None]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def summarize(rows: Sequence[Mapping[str, Any]], method: str) -> dict[str, Any]:
    safe = [row for row in rows if row["oracle"]["goal_success"] and row["oracle"]["safe"]]
    unsafe = [row for row in rows if row["oracle"]["goal_success"] and not row["oracle"]["safe"]]
    unsafe_accept = sum(row["verdicts"][method] for row in unsafe)
    safe_reject = sum(not row["verdicts"][method] for row in safe)
    return {
        "method": method,
        "safe_goal_count": len(safe),
        "unsafe_goal_count": len(unsafe),
        "unsafe_accepted": unsafe_accept,
        "unsafe_false_accept_rate": unsafe_accept / len(unsafe) if unsafe else None,
        "unsafe_false_accept_wilson95": wilson(unsafe_accept, len(unsafe)),
        "safe_rejected": safe_reject,
        "safe_false_reject_rate": safe_reject / len(safe) if safe else None,
        "safe_false_reject_wilson95": wilson(safe_reject, len(safe)),
    }


def percentile(values: Sequence[int], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v83_external/external_boundaries"))
    args = parser.parse_args()
    blinded = []
    frame_sizes = []
    for candidate_path in sorted((args.data_root / "public/candidates").glob("*/*.json")):
        candidate_row = load(candidate_path)
        sid = candidate_row["scenario_id"]
        scenario = args.data_root / "public/scenarios" / sid
        metadata = load(scenario / "metadata.json")
        intent = load(scenario / "intent.json")
        device = metadata["device"]
        baseline = {device: (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")}
        candidate = candidate_row["candidate_configs"]
        passive = records(load(scenario / "passive_observations.json"))
        active = records(load(args.data_root / "sealed/oracles" / sid / "active_observations.json"))
        full_pre = passive + active
        full_env = envelope(intent, baseline, full_pre, "external_active_policy_classes")
        passive_env = envelope(intent, baseline, passive, "operator_visible_passive_tests")
        full_report = report(baseline, candidate, full_pre, full_env)
        passive_report = report(baseline, candidate, passive, passive_env)
        frame_sizes.append(len(full_env.semantic_frame))
        blinded.append({
            "scenario_id": sid,
            "mode": candidate_row["mode"],
            "candidate_sha256": candidate_row["candidate_sha256"],
            "source_group": metadata["source_group"],
            "vendor": metadata["vendor"],
            "family": metadata["family"],
            "verdicts": method_verdicts(full_report, passive_report),
            "full_report": full_report,
            "passive_report": passive_report,
            "envelope_summary": {
                "target_obligations": len(full_env.target_delta),
                "frame_obligations": len(full_env.semantic_frame),
                "protected_dependencies": len(full_env.protected_dependencies),
                "footprint_budget": asdict(full_env.footprint_budget),
            },
        })
    args.output_root.mkdir(parents=True, exist_ok=True)
    blinded_path = args.output_root / "blinded_verdicts.json"
    write(blinded_path, blinded)
    receipt = {
        "schema_version": "msn2026-v83-boundary-receipt-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(blinded),
        "blinded_verdicts_sha256": sha256(blinded_path),
        "redteam_corpus_receipt_sha256": sha256(Path("results/msn2026_v83_external/redteam_corpus_receipt.json")),
        "oracle_labels_read_before_receipt": False,
    }
    write(args.output_root / "blinding_receipt.json", receipt)

    unblinded = []
    for row in blinded:
        label_path = args.data_root / "sealed/candidate_oracles" / row["scenario_id"] / f"{row['mode']}.json"
        label = load(label_path)
        unblinded.append({**row, "oracle": {key: value for key, value in label.items() if key != "post_observations"}})
    overall = [summarize(unblinded, method) for method in METHODS]
    strata: dict[str, Any] = {}
    for field in ("source_group", "vendor", "family", "mode"):
        strata[field] = {}
        for value in sorted({row[field] for row in unblinded}):
            subset = [row for row in unblinded if row[field] == value]
            strata[field][value] = [summarize(subset, method) for method in METHODS]
    analysis = {
        "schema_version": "msn2026-v83-external-boundary-analysis-1.0",
        "candidate_count": len(unblinded),
        "goal_achieving_count": sum(row["oracle"]["goal_success"] for row in unblinded),
        "safe_goal_count": sum(row["oracle"]["goal_success"] and row["oracle"]["safe"] for row in unblinded),
        "unsafe_goal_count": sum(row["oracle"]["goal_success"] and not row["oracle"]["safe"] for row in unblinded),
        "non_goal_count": sum(not row["oracle"]["goal_success"] for row in unblinded),
        "overall": overall,
        "strata": strata,
        "frame_obligations": {
            "count": len(frame_sizes),
            "median": statistics.median(frame_sizes),
            "p95": percentile(frame_sizes, 0.95),
            "min": min(frame_sizes),
            "max": max(frame_sizes),
        },
        "blinding_receipt": receipt,
    }
    write(args.output_root / "unblinded_results.json", unblinded)
    write(args.output_root / "analysis.json", analysis)
    print(json.dumps({key: value for key, value in analysis.items() if key not in {"strata"}}, indent=2))


if __name__ == "__main__":
    main()

