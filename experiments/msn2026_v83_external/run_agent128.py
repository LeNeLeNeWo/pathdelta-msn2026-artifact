#!/usr/bin/env python3
"""Resumable paired Agent128 experiment over the frozen v8.3 candidate subset.

The first patch is replayed byte-for-byte across all causal arms.  Arms differ
only in their acceptance contract and patch-free counterexample feedback.  RAG
is a prompt-changing matched baseline and is deliberately not called causal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.agent_benchmark import parse_edit_response  # noqa: E402
from experiments.msn2026_v8_day2_agent.change_envelope import SearchReplaceEdit, apply_search_replace_edits  # noqa: E402
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord, derive_change_envelope_v2  # noqa: E402
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient  # noqa: E402
from experiments.msn2026_v8_day2_agent.semantic_metrics import build_blast_radius_report  # noqa: E402
from experiments.msn2026_v83_external.vendor_policy_adapter import behavior_rows, build_dependency_graph  # noqa: E402


SYSTEM = """You are a network operations agent editing an existing brownfield configuration.
You own the implementation: choose exact baseline-relative edits that satisfy the intent while
preserving unrelated behavior. Return JSON only:
{"edits":[{"device":"device key","old_text":"exact unique baseline text","new_text":"replacement"}],"summary":"brief"}
Every submission is applied to the immutable original baseline. Each old_text must occur exactly
once. Do not ask questions, return markdown, or assume that a verifier will repair your patch."""

METHODS = ("direct", "rag", "goal_only", "write_scope", "verifier_loop", "oracle_contract", "full_envelope")
ITERATIVE = {
    "goal_only": "goal_only",
    "write_scope": "write_scope",
    "verifier_loop": "verifier_loop",
    "oracle_contract": "oracle_contract",
    "full_envelope": "full_envelope",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def records(rows: Sequence[Mapping[str, Any]]) -> list[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in rows]


def post_records(candidate: Mapping[str, str], pre: Sequence[BehaviorRecord]) -> list[BehaviorRecord]:
    grouped: dict[tuple[str, str], list[BehaviorRecord]] = {}
    for row in pre:
        grouped.setdefault((row.device, row.subject), []).append(row)
    output: list[BehaviorRecord] = []
    for (device, subject), group in grouped.items():
        if device not in candidate:
            continue
        output.extend(records(behavior_rows(device, candidate[device], subject, [r.fec for r in group], "v83_agent_post")))
    return output


def make_envelope(intent: Mapping[str, Any], baseline: Mapping[str, str], pre: Sequence[BehaviorRecord], backend: str) -> Any:
    return derive_change_envelope_v2(
        intent,
        baseline,
        pre,
        build_dependency_graph(baseline),
        behavior_universe_provenance={
            "backend": backend,
            "complete": False,
            "uncovered_reason": "finite policy-equivalence representatives",
            "candidate_patch_used": False,
        },
    )


def parse_gate(candidate: Mapping[str, str]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        graph = build_dependency_graph(candidate)
        if not graph.nodes:
            raise ValueError("vendor adapter extracted no policy/dependency nodes")
        return {"status": "PASS", "backend": "multi_vendor_adapter", "latency_ms": (time.perf_counter() - started) * 1000}
    except Exception as exc:
        return {"status": "FAIL", "backend": "multi_vendor_adapter", "latency_ms": (time.perf_counter() - started) * 1000,
                "diagnostic": f"{type(exc).__name__}: {exc}"}


def scope_ok(report: Mapping[str, Any], env: Any) -> bool:
    structural, textual, budget = report["structural"], report["textual"], env.footprint_budget
    return bool(
        set(structural["devices_touched"]) <= set(budget.allowed_devices)
        and len(structural["devices_touched"]) <= budget.max_devices_touched
        and len(structural["bindings_changed"]) <= budget.max_bindings_changed
        and len(structural["new_objects_created"]) <= budget.max_new_objects
        and len(structural["policy_objects_touched"]) <= budget.max_existing_objects_modified + budget.max_new_objects
        and textual["lines_touched"] <= budget.max_changed_lines
    )


def evaluate(
    baseline: Mapping[str, str], edits: Sequence[SearchReplaceEdit], passive: Sequence[BehaviorRecord], full_pre: Sequence[BehaviorRecord],
    passive_env: Any, full_env: Any,
) -> dict[str, Any]:
    try:
        candidate = apply_search_replace_edits(baseline, edits)
    except Exception as exc:
        return {"candidate_applied": False, "transaction_error": f"{type(exc).__name__}: {exc}", "syntax": {"status": "N/A"},
                "reports": {}, "contract_pass": {m: False for m in ("submission", *ITERATIVE.values())}}
    syntax = parse_gate(candidate)
    try:
        passive_post = post_records(candidate, passive)
        full_post = post_records(candidate, full_pre)
        passive_report = build_blast_radius_report(
            baseline, candidate, passive, passive_post, passive_env,
            build_dependency_graph(baseline), build_dependency_graph(candidate),
        ).to_dict()
        full_report = build_blast_radius_report(
            baseline, candidate, full_pre, full_post, full_env,
            build_dependency_graph(baseline), build_dependency_graph(candidate),
        ).to_dict()
    except Exception as exc:
        return {"candidate_applied": True, "candidate_configs": candidate, "transaction_error": None, "syntax": syntax,
                "evaluation_error": f"{type(exc).__name__}: {exc}", "reports": {},
                "contract_pass": {m: False for m in ("submission", *ITERATIVE.values())}}
    syn = syntax["status"] == "PASS"
    pc, fc = passive_report["compliance"], full_report["compliance"]
    passes = {
        "submission": syn,
        "goal_only": syn and bool(fc["goal_success"]),
        "write_scope": syn and bool(fc["goal_success"]) and scope_ok(full_report, full_env),
        "verifier_loop": syn and bool(pc["goal_success"]) and bool(pc["semantic_frame_preserved"]),
        "oracle_contract": syn and bool(fc["goal_success"]) and bool(fc["semantic_frame_preserved"]),
        "full_envelope": syn and bool(fc["envelope_compliance"]),
    }
    full_after = {row.behavior_id: row for row in full_post}
    target_observations = []
    for obligation in full_env.target_delta:
        observed = full_after.get(obligation.behavior_id)
        target_observations.append({
            "obligation_id": obligation.obligation_id,
            "behavior_id": obligation.behavior_id,
            "dimension": obligation.dimension,
            "required": obligation.desired,
            "observed": observed.attributes.get(obligation.dimension) if observed else None,
        })
    return {"candidate_applied": True, "candidate_configs": candidate, "transaction_error": None, "syntax": syntax,
            "reports": {"passive": passive_report, "full": full_report}, "target_observations": target_observations,
            "contract_pass": passes}


def compact_feedback(contract: str, evaluation: Mapping[str, Any]) -> dict[str, Any]:
    counterexamples: list[dict[str, Any]] = []
    if evaluation.get("transaction_error"):
        counterexamples.append({"type": "transaction", "observed": evaluation["transaction_error"]})
    elif evaluation.get("evaluation_error"):
        counterexamples.append({"type": "unsupported_evaluation", "observed": evaluation["evaluation_error"]})
    else:
        if (evaluation.get("syntax") or {}).get("status") != "PASS":
            counterexamples.append({"type": "syntax", "observed": evaluation.get("syntax")})
        report_key = "passive" if contract == "verifier_loop" else "full"
        report = (evaluation.get("reports") or {}).get(report_key, {})
        compliance, semantic = report.get("compliance", {}), report.get("semantic", {})
        if not compliance.get("goal_success", False):
            counterexamples.append({"type": "target_unmet", "obligation_ids": compliance.get("target_failures", [])[:8],
                                    "observed_relations": list(evaluation.get("target_observations", []))[:8]})
        if contract in {"verifier_loop", "oracle_contract", "full_envelope"} and not compliance.get("semantic_frame_preserved", False):
            changed = list(semantic.get("non_target_attribute_changes", []))
            counterexamples.append({"type": "semantic_collateral", "changed_count": len(changed), "examples": changed[:8],
                                    "obligation_ids": list(compliance.get("frame_failures", []))[:8]})
        if contract == "write_scope" and not evaluation.get("contract_pass", {}).get("write_scope", False):
            counterexamples.append({"type": "write_scope", "observed": {"structural": report.get("structural", {}), "textual": report.get("textual", {})}})
        if contract == "full_envelope":
            if not compliance.get("dependency_frame_preserved", False):
                counterexamples.append({"type": "protected_dependency", "changed": list(semantic.get("protected_dependency_violations", []))[:8]})
            if not compliance.get("hard_footprint_preserved", False):
                counterexamples.append({"type": "footprint", "failures": list(compliance.get("footprint_failures", []))[:8]})
    return {"schema_version": "msn2026-v83-feedback-1.0", "contract": contract, "verdict": "FAIL",
            "counterexamples": counterexamples, "patch_disclosed": False, "strategy_disclosed": False,
            "instruction": "Revise your own baseline-relative edits. No expected object, position, or patch strategy is provided."}


def oracle(candidate_eval: Mapping[str, Any], label_pre: Sequence[Mapping[str, Any]], intent: Mapping[str, Any]) -> dict[str, Any]:
    if not candidate_eval.get("candidate_applied"):
        return {"goal": False, "safe": False, "collateral_atoms": [], "reason": "candidate_not_applied"}
    candidate = candidate_eval["candidate_configs"]
    device = intent["selector"]["devices"][0]
    subject = intent["selector"]["subjects"][0]
    rows = behavior_rows(device, candidate[device], subject, [r["fec"] for r in label_pre], "v83_agent_oracle_post")
    before, after = {r["behavior_id"]: r for r in label_pre}, {r["behavior_id"]: r for r in rows}
    target_id = f"{device}|{subject}|{intent['selector']['fecs'][0]}"
    dimension, desired = intent["changes"][0]["dimension"], intent["changes"][0]["desired"]
    target = after.get(target_id)
    goal = bool(target and target["attributes"].get(dimension) == desired and candidate_eval.get("syntax", {}).get("status") == "PASS")
    collateral: list[str] = []
    for behavior_id, old in before.items():
        new = after.get(behavior_id)
        if new is None:
            collateral.append(f"{behavior_id}::missing")
            continue
        for dim in sorted(set(old["attributes"]) | set(new["attributes"])):
            if behavior_id == target_id and dim == dimension:
                continue
            if old["attributes"].get(dim) != new["attributes"].get(dim):
                collateral.append(f"{behavior_id}::{dim}")
    return {"goal": goal, "safe": bool(goal and not collateral), "collateral_atoms": collateral}


def call(client: InstrumentedDeepSeekClient, messages: list[dict[str, str]], baseline: Mapping[str, str], passive: Sequence[BehaviorRecord],
         full_pre: Sequence[BehaviorRecord], passive_env: Any, full_env: Any, max_tokens: int) -> dict[str, Any]:
    raw = ""
    try:
        raw = client.complete(messages, temperature=0.0, max_completion_tokens=max_tokens)
        edits, parsed = parse_edit_response(raw)
        return {"raw": raw, "parsed": parsed, "evaluation": evaluate(baseline, edits, passive, full_pre, passive_env, full_env)}
    except Exception as exc:
        return {"raw": raw, "error": f"{type(exc).__name__}: {exc}",
                "evaluation": {"candidate_applied": False, "transaction_error": f"{type(exc).__name__}: {exc}", "syntax": {"status": "N/A"},
                               "reports": {}, "contract_pass": {m: False for m in ("submission", *ITERATIVE.values())}}}


def branch(client: InstrumentedDeepSeekClient, initial: Mapping[str, Any], messages: list[dict[str, str]], contract: str,
           baseline: Mapping[str, str], passive: Sequence[BehaviorRecord], full_pre: Sequence[BehaviorRecord], passive_env: Any,
           full_env: Any, max_tokens: int, max_submissions: int) -> dict[str, Any]:
    trace = [initial]
    if initial["evaluation"]["contract_pass"].get(contract):
        return {"accepted": True, "submissions": 1, "trace": trace}
    conversation = list(messages)
    conversation.append({"role": "assistant", "content": initial["raw"]})
    for _ in range(1, max_submissions):
        conversation.append({"role": "user", "content": json.dumps({"verification_counterexample": compact_feedback(contract, trace[-1]["evaluation"])}, sort_keys=True)})
        attempt = call(client, conversation, baseline, passive, full_pre, passive_env, full_env, max_tokens)
        trace.append(attempt)
        if attempt.get("raw"):
            conversation.append({"role": "assistant", "content": attempt["raw"]})
        if attempt["evaluation"]["contract_pass"].get(contract):
            break
    return {"accepted": bool(trace[-1]["evaluation"]["contract_pass"].get(contract)), "submissions": len(trace), "trace": trace}


def dep_context(baseline: Mapping[str, str], limit: int = 200) -> dict[str, Any]:
    graph = build_dependency_graph(baseline)
    nodes = [asdict(node) for _, node in sorted(graph.nodes.items())]
    edges = [{"source": a, "target": b} for a, targets in sorted(graph.edges.items()) for b in sorted(targets)]
    return {"nodes": nodes[:limit], "edges": edges[:limit], "truncated": len(nodes) > limit or len(edges) > limit,
            "note": "Read-only pre-state facts; no edit strategy or recommended object."}


def usage_since(client: InstrumentedDeepSeekClient, start: int) -> dict[str, Any]:
    calls = client.metrics.calls[start:]
    return {
        "logical_llm_calls": len(calls),
        "backend_attempts": sum(call.backend_attempts for call in calls),
        "retry_count": sum(call.retries for call in calls),
        "token_usage": {
            "prompt": sum(call.usage.prompt for call in calls),
            "completion": sum(call.usage.completion for call in calls),
            "total": sum(call.usage.total for call in calls),
        },
        "latency_ms": sum(call.latency_ms for call in calls),
    }


def run_case(case: Mapping[str, Any], data_root: Path, max_tokens: int, max_submissions: int) -> dict[str, Any]:
    sid, mode = case["scenario_id"], case["mode"]
    scenario = data_root / "public/scenarios" / sid
    metadata, intent = load(scenario / "metadata.json"), load(scenario / "intent.json")
    device = metadata["device"]
    baseline = {device: (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")}
    candidate_row = load(data_root / case["candidate_path"])
    passive = records(load(scenario / "passive_observations.json"))
    complete_pre_rows = load(data_root / "sealed/oracles" / sid / "complete_pre_observations.json")
    active_rows = load(data_root / "sealed/oracles" / sid / "active_observations.json")
    full_pre = passive + records(active_rows)
    passive_env = make_envelope(intent, baseline, passive, "operator_visible_passive_tests")
    full_env = make_envelope(intent, baseline, full_pre, "coverage_directed_active_policy_classes")
    initial_eval = evaluate(baseline, [SearchReplaceEdit(**row) for row in candidate_row["edits"]], passive, full_pre, passive_env, full_env)
    initial_raw = json.dumps({"edits": candidate_row["edits"], "summary": "frozen independent first candidate"}, sort_keys=True)
    initial = {"raw": initial_raw, "parsed": json.loads(initial_raw), "evaluation": initial_eval, "replayed": True}
    prompt = {"intent": intent["raw_text"], "structured_target": {"device": device, "subject": intent["selector"]["subjects"][0],
              "prefix": intent["selector"]["fecs"][0], "dimension": intent["changes"][0]["dimension"],
              "required_value": intent["changes"][0]["desired"]}, "vendor": metadata["vendor"], "baseline_config": baseline[device]}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(prompt, sort_keys=True)}]
    client = InstrumentedDeepSeekClient(timeout_s=240, max_retries=2, thinking_mode="disabled")
    arms: dict[str, Any] = {
        "direct": {"accepted": bool(initial_eval["contract_pass"]["submission"]), "submissions": 1, "trace": [initial],
                   "llm_usage": {"logical_llm_calls": 0, "backend_attempts": 0, "retry_count": 0,
                                 "token_usage": {"prompt": 0, "completion": 0, "total": 0}, "latency_ms": 0.0,
                                 "note": "first-candidate generation is accounted in redteam_generation/summary.json"}},
    }
    for method, contract in ITERATIVE.items():
        call_start = len(client.metrics.calls)
        arms[method] = branch(client, initial, messages, contract, baseline, passive, full_pre, passive_env, full_env, max_tokens, max_submissions)
        arms[method]["llm_usage"] = usage_since(client, call_start)
    rag_prompt = dict(prompt)
    rag_prompt["retrieved_dependency_context"] = dep_context(baseline)
    call_start = len(client.metrics.calls)
    rag_attempt = call(client, [messages[0], {"role": "user", "content": json.dumps(rag_prompt, sort_keys=True)}], baseline, passive, full_pre, passive_env, full_env, max_tokens)
    arms["rag"] = {"accepted": bool(rag_attempt["evaluation"]["contract_pass"]["submission"]), "submissions": 1, "trace": [rag_attempt],
                   "llm_usage": usage_since(client, call_start),
                   "pairing_note": "matched event and temperature; prompt differs, so this is not a same-first-candidate causal arm"}
    for method, arm in arms.items():
        final_eval = arm["trace"][-1]["evaluation"]
        arm["oracle"] = oracle(final_eval, complete_pre_rows, intent)
        arm["verified_completion"] = bool(arm["accepted"] and arm["oracle"]["safe"])
        arm["unsafe_release"] = bool(arm["accepted"] and not arm["oracle"]["safe"])
        arm["reject_to_repair_success"] = bool(arm["submissions"] > 1 and arm["verified_completion"])
    return {"schema_version": "msn2026-v83-agent-case-1.0", "created_at": datetime.now(timezone.utc).isoformat(),
            "case_id": case["case_id"], "scenario_id": sid, "mode": mode, "source_group": metadata["source_group"],
            "vendor": metadata["vendor"], "family": metadata["family"], "first_candidate_sha256": candidate_row["candidate_sha256"],
            "same_first_candidate_arms": ["direct", *ITERATIVE.keys()], "rag_is_causal": False, "arms": arms,
            "llm_metrics": client.metrics.to_dict(), "max_submissions": max_submissions, "temperature": 0.0}


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]


def summarize(case_paths: Sequence[Path], output_root: Path) -> dict[str, Any]:
    cases = [load(path) for path in case_paths]
    methods = []
    for method in METHODS:
        arms = [row["arms"][method] for row in cases]
        methods.append({"method": method, "case_count": len(arms),
                        "first_goal_success": sum(a["trace"][0]["evaluation"]["contract_pass"].get("goal_only", False) for a in arms),
                        "final_goal_success": sum(a["oracle"]["goal"] for a in arms),
                        "verified_completion": sum(a["verified_completion"] for a in arms),
                        "unsafe_release": sum(a["unsafe_release"] for a in arms),
                        "collateral_regression": sum(bool(a["oracle"]["collateral_atoms"]) for a in arms),
                        "total_submissions": sum(a["submissions"] for a in arms),
                        "reject_to_repair_success": sum(a["reject_to_repair_success"] for a in arms),
                        "attempt_exhaustion": sum(a["submissions"] >= cases[i]["max_submissions"] and not a["accepted"] for i, a in enumerate(arms)),
                        "logical_llm_calls": sum(a["llm_usage"]["logical_llm_calls"] for a in arms),
                        "backend_attempts": sum(a["llm_usage"]["backend_attempts"] for a in arms),
                        "retry_count": sum(a["llm_usage"]["retry_count"] for a in arms),
                        "prompt_tokens": sum(a["llm_usage"]["token_usage"]["prompt"] for a in arms),
                        "completion_tokens": sum(a["llm_usage"]["token_usage"]["completion"] for a in arms),
                        "latency_ms": sum(a["llm_usage"]["latency_ms"] for a in arms)})
    metrics = [row["llm_metrics"] for row in cases]
    calls = [call for metric in metrics for call in metric["calls"]]
    latencies = [float(call["latency_ms"]) for call in calls]
    summary = {"schema_version": "msn2026-v83-agent-summary-1.0", "created_at": datetime.now(timezone.utc).isoformat(),
               "completed_case_count": len(cases), "methods": methods,
               "llm_metrics": {"logical_llm_calls": sum(m["logical_llm_calls"] for m in metrics),
                               "backend_attempts": sum(m["backend_attempts"] for m in metrics),
                               "retry_count": sum(m["retry_count"] for m in metrics),
                               "token_usage": {k: sum(m["token_usage"][k] for m in metrics) for k in ("prompt", "completion", "total")},
                               "latency_ms_total": sum(latencies), "latency_ms_p50": statistics.median(latencies) if latencies else None,
                               "latency_ms_p95": percentile(latencies, .95), "backend": metrics[0]["backend"] if metrics else None,
                               "configured_model": metrics[0]["configured_model"] if metrics else None,
                               "thinking_mode": metrics[0].get("thinking_mode") if metrics else None},
               "same_first_candidate": True, "trusted_patch_generator_used": False}
    write(output_root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v83_external/agent128"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-submissions", type=int, default=5)
    parser.add_argument("--max-completion-tokens", type=int, default=4000)
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()
    subset = load(args.data_root / "agent128_subset.json")["cases"]
    selected = subset[args.offset : (args.offset + args.limit) if args.limit else None]
    for ordinal, case in enumerate(selected, 1):
        output = args.output_root / "cases" / f"{case['scenario_id']}__{case['mode']}.json"
        if output.exists() and not args.rerun:
            print(f"agent {ordinal}/{len(selected)} {case['case_id']} SKIP", flush=True)
            continue
        started = time.perf_counter()
        row = run_case(case, args.data_root, args.max_completion_tokens, args.max_submissions)
        write(output, row)
        print(f"agent {ordinal}/{len(selected)} {case['case_id']} calls={row['llm_metrics']['logical_llm_calls']} "
              f"elapsed={time.perf_counter()-started:.1f}s", flush=True)
    case_paths = sorted((args.output_root / "cases").glob("*.json"))
    summary = summarize(case_paths, args.output_root)
    print(json.dumps({"completed_case_count": summary["completed_case_count"], "methods": summary["methods"],
                      "llm_metrics": summary["llm_metrics"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
