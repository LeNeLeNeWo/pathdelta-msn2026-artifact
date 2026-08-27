#!/usr/bin/env python3
"""Run the frozen v8 development pilot without starting a formal experiment."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.msn2026_v8_day2_agent.agent_benchmark import (
    SYSTEM_PROMPT,
    _feedback,
    evaluate_submission,
    parse_edit_response,
)
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    build_frr_dependency_graph,
    derive_change_envelope_v2,
    handwritten_special_case_audit,
)
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient


SYNTHETIC_SUBSET = ["bf_shared_rm", "bf_route_map_call", "bf_community_nested", "bf_multi_device"]
PUBLIC_SUBSET = ["public_frr_continue", "public_kathara_call", "public_containerlab_reuse"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configs(root: Path) -> Dict[str, str]:
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.conf"))}


def _records(path: Path) -> List[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def _rq1_subset(project_root: Path) -> Dict[str, Any]:
    rows = json.loads((project_root / "results/msn2026_v8_rq1_dev/rq1_candidate_results.json").read_text(encoding="utf-8"))
    rows = [row for row in rows if row["scenario_id"] in SYNTHETIC_SUBSET]
    safe = [row for row in rows if row["ground_truth"]["semantically_acceptable"]]
    collateral = [row for row in rows if row["ground_truth"]["target_satisfied"] and row["ground_truth"]["collateral_semantic_change"]]
    return {
        "candidate_count": len(rows),
        "safe_count": len(safe),
        "goal_satisfying_collateral_count": len(collateral),
        "goal_only_collateral_accepted": sum(row["accepted"]["V1_goal"] for row in collateral),
        "full_envelope_collateral_accepted": sum(row["accepted"]["V5_full_envelope"] for row in collateral),
        "full_safe_accepted": sum(row["accepted"]["V5_full_envelope"] for row in safe),
        "accepted_safe_candidate_classes": sorted({row["candidate_id"] for row in safe if row["accepted"]["V5_full_envelope"]}),
    }


def _rq4_public_inference(project_root: Path) -> Dict[str, Any]:
    root = project_root / "data/msn2026_v8_public_brownfield/cases"
    rows = []
    for case_id in PUBLIC_SUBSET:
        case = root / case_id
        configs = _configs(case / "baseline")
        intent = json.loads((case / "intent.json").read_text(encoding="utf-8"))
        pre = _records(case / "pre_observations.json")
        graph = build_frr_dependency_graph(configs)
        envelope = derive_change_envelope_v2(
            intent,
            configs,
            pre,
            graph,
            behavior_universe_provenance={"backend": "public-source-conditioned-pilot-observations", "complete": False, "uncovered_reason": "finite pilot FEC set"},
        )
        meta = json.loads((case / "case.json").read_text(encoding="utf-8"))
        rows.append(
            {
                "case_id": case_id,
                "source_id": meta["source_id"],
                "topology_family": meta["topology_family"],
                "features": meta["features"],
                "target_obligations": len(envelope.target_delta),
                "frame_obligations": len(envelope.semantic_frame),
                "dependency_closure_size": len(envelope.dependency_closure),
                "protected_dependency_count": len(envelope.protected_dependencies),
                "protected_dependencies": envelope.protected_dependencies,
                "universe_complete": envelope.coverage["universe_complete"],
            }
        )
    return {"cases": rows, "special_case_audit": handwritten_special_case_audit()}


def _paired_feedback_pilot(project_root: Path, output_root: Path) -> Dict[str, Any]:
    retained = output_root / "paired_feedback" / "trace.json"
    if retained.exists():
        # The pilot freezes one stochastic first candidate. Mechanism-only
        # reruns reuse that exact trace instead of resampling until favorable.
        return json.loads(retained.read_text(encoding="utf-8"))
    case = project_root / "data/msn2026_v8_envelope_benchmark/scenarios/bf_shared_rm"
    intent = json.loads((case / "intent.json").read_text(encoding="utf-8"))
    baseline = _configs(case / "baseline")
    pre = _records(case / "pre_observations.json")
    envelope = derive_change_envelope_v2(
        intent,
        baseline,
        pre,
        build_frr_dependency_graph(baseline),
        behavior_universe_provenance={"backend": "paired-pilot-observations", "complete": False, "uncovered_reason": "finite pilot FEC set"},
    )
    initial_payload = {"intent": intent["raw_text"], "baseline_configs": baseline, "eligible_information": "immutable pre-state only"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(initial_payload, sort_keys=True)}]
    client = InstrumentedDeepSeekClient(timeout_s=120, max_retries=2)
    raw_first = client.complete(messages, temperature=0.0, max_completion_tokens=6000)
    first_edits, first_parsed = parse_edit_response(raw_first)
    first_eval = evaluate_submission(
        baseline,
        first_edits,
        pre,
        envelope,
        output_root / "paired_feedback" / "attempt_1",
        "pathdelta-msn2026-frr-syntax",
    )
    goal_only_accepts = first_eval["contract_pass"]["syntax_goal"]
    full_accepts_first = first_eval["contract_pass"]["full_envelope"]
    trace = [{"attempt": 1, "raw_llm_response": raw_first, "parsed_submission": first_parsed, "evaluation": first_eval}]
    final_full = first_eval
    if not full_accepts_first:
        feedback = _feedback("full_envelope", first_eval)
        messages += [
            {"role": "assistant", "content": raw_first},
            {"role": "user", "content": json.dumps({"verification_counterexample": feedback, "instruction": "Revise your own complete baseline-relative edits. Return JSON only."}, sort_keys=True)},
        ]
        raw_second = client.complete(messages, temperature=0.0, max_completion_tokens=6000)
        second_edits, second_parsed = parse_edit_response(raw_second)
        second_eval = evaluate_submission(
            baseline,
            second_edits,
            pre,
            envelope,
            output_root / "paired_feedback" / "attempt_2",
            "pathdelta-msn2026-frr-syntax",
        )
        trace[0]["full_feedback"] = feedback
        trace.append({"attempt": 2, "raw_llm_response": raw_second, "parsed_submission": second_parsed, "evaluation": second_eval})
        final_full = second_eval
    result = {
        "design": "same fresh LLM first candidate replayed under Goal-only and Full acceptance; only Full may request one model revision",
        "goal_only": {
            "accepted_first_candidate": goal_only_accepts,
            "target_success": bool(first_eval.get("report") and first_eval["report"]["compliance"]["goal_success"]),
            "collateral_regression": bool(first_eval.get("report") and first_eval["report"]["compliance"]["collateral_change"]),
            "attempts": 1,
        },
        "full_envelope": {
            "accepted_first_candidate": full_accepts_first,
            "final_accepted": final_full["contract_pass"]["full_envelope"],
            "final_target_success": bool(final_full.get("report") and final_full["report"]["compliance"]["goal_success"]),
            "final_collateral_regression": bool(final_full.get("report") and final_full["report"]["compliance"]["collateral_change"]),
            "attempts": len(trace),
        },
        "llm_metrics": client.metrics.to_dict(),
        "trace": trace,
    }
    paired_root = output_root / "paired_feedback"
    paired_root.mkdir(parents=True, exist_ok=True)
    (paired_root / "trace.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def run(project_root: Path, output_root: Path) -> Dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    freeze = {
        "pilot_id": "msn2026_v8_evidence_pilot",
        "frozen_before_live_paired_run": True,
        "synthetic_scenarios": SYNTHETIC_SUBSET,
        "public_cases": PUBLIC_SUBSET,
        "verification_scenario": "scenario_path_replace",
        "manifests": {
            "envelope_benchmark": _sha(project_root / "data/msn2026_v8_envelope_benchmark/benchmark_manifest.json"),
            "public_brownfield": _sha(project_root / "data/msn2026_v8_public_brownfield/brownfield_manifest.json"),
            "batfish_rela": _sha(project_root / "data/msn2026_v8_batfish_rela_dev/dataset_manifest.json"),
        },
        "selection_rule": "predeclared cross-source/topology/pattern development sample; no case removed after outcomes",
        "not_formal_freeze": True,
    }
    (output_root / "pilot_subset_manifest.json").write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rq1 = _rq1_subset(project_root)
    rq4 = _rq4_public_inference(project_root)
    (output_root / "rq4_inference_audit.json").write_text(json.dumps(rq4, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rq2 = _paired_feedback_pilot(project_root, output_root)
    verification = json.loads((project_root / "results/msn2026_v8_batfish_rela_dev/batfish_rela_summary.json").read_text(encoding="utf-8"))
    summary = {
        "scope": "frozen development pilot, not formal experiment",
        "rq1": rq1,
        "rq2": {
            "goal_only": rq2["goal_only"],
            "full_envelope": rq2["full_envelope"],
            "logical_llm_calls": rq2["llm_metrics"]["logical_llm_calls"],
            "tokens": rq2["llm_metrics"]["token_usage"],
        },
        "rq3": {
            "safe_alternative_false_rejects": rq1["safe_count"] - rq1["full_safe_accepted"],
            "safe_alternative_count": rq1["safe_count"],
        },
        "rq4": rq4,
        "rq5": {
            "batfish_diff_reachability_positive_rows": verification["batfish"]["differential_reachability_rows"],
            "rela_positive_pass": verification["rela"]["result"]["passed"],
            "batfish_diff_reachability_collateral_rows": verification["rela"]["collateral_negative"]["batfish_differential_reachability_rows"],
            "rela_collateral_rejected": not verification["rela"]["collateral_negative"]["result"]["passed"],
        },
    }
    (output_root / "pilot_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(PROJECT_ROOT, PROJECT_ROOT / "results/msn2026_v8_evidence_pilot"), indent=2, sort_keys=True))
