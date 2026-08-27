#!/usr/bin/env python3
"""Impact-aware, patch-free repair controller for the v8.3 Agent128 corpus.

This is a post-freeze mechanism iteration.  It never overwrites v8.3 results.
The old Agent128 traces are a development diagnostic; outputs are written to a
separate v8.4 directory.  The verifier still returns observations rather than
an expected object, command, insertion point, or reference patch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.agent_benchmark import parse_edit_response  # noqa: E402
from experiments.msn2026_v8_day2_agent.counterexample_feedback import assert_feedback_is_patch_free  # noqa: E402
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient  # noqa: E402
from experiments.msn2026_v83_external import run_agent128 as v83  # noqa: E402
from experiments.msn2026_v83_external import vendor_policy_adapter as adapter  # noqa: E402


SYSTEM_V2 = """You are an impact-aware network operations agent editing an existing brownfield configuration.
You own the implementation and must return an actual baseline-relative patch.  Before emitting it, explicitly
check in your private reasoning that (1) the target relation is achieved, (2) every reported non-target relation
keeps its pre-change value, (3) shared match sets do not broaden the change, and (4) ordered policies are evaluated
in their actual current order.  In Junos set-format policy, a term's order is determined by its first occurrence;
placing a new term beside a later line of an already-seen term does not make it execute earlier.  In route maps,
sequence order and match scope determine which routes receive a set action.
Changing prefix-list membership alone never changes an attribute value: the
route must reach a policy term whose set action equals the required value.  A
prefix-list deny entry does not positively match a route-map match clause.

Verifier feedback contains observations only.  It never supplies a correct object, command, insertion point, or
patch strategy.  If a previous semantic outcome failed, derive a materially different candidate from the original
baseline and the read-only policy context instead of repeating the same edit.

Return JSON only:
{"edits":[{"device":"device key","old_text":"exact unique baseline text","new_text":"replacement"}],
 "impact_check":{"target":"brief","non_target":"brief","ordering":"brief"},"summary":"brief"}
Each old_text must occur exactly once in the immutable original baseline. Do not ask questions or return markdown."""


def trace_subject(config: str, subject: str, fec: str) -> dict[str, Any]:
    """Return read-only policy evaluation evidence, never an edit suggestion."""
    model = adapter.parse(config)
    binding = model.bindings.get(subject)
    if binding is None:
        return {"applicable": False, "reason": "subject binding not parsed"}
    policy, _direction = binding
    steps = []
    local_pref = 100
    communities: list[str] = []
    for ordinal, term in enumerate(model.policies.get(policy, []), 1):
        prefix_results = [
            {"name": name, "mode": mode, "permits_fec": adapter.prefix_list_permits(model, name, fec, mode)}
            for name, mode in term.prefix_lists
        ]
        route_filter_results = [
            {"prefix": prefix, "mode": mode, "matches_fec": adapter._network_matches(fec, prefix, mode)}
            for prefix, mode in term.route_filters
        ]
        matched = True
        if prefix_results:
            matched = any(item["permits_fec"] for item in prefix_results)
        if matched and route_filter_results:
            matched = any(item["matches_fec"] for item in route_filter_results)
        step = {"ordinal": ordinal, "term": term.name, "matched": matched,
                "prefix_list_results": prefix_results, "route_filter_results": route_filter_results}
        steps.append(step)
        if not matched:
            continue
        if term.set_local_pref is not None:
            local_pref = term.set_local_pref
        communities.extend(value for value in term.set_communities if value not in communities)
        terminal = term.terminal or ("deny" if term.action == "deny" else "permit")
        step["applied"] = {"set_local_pref": term.set_local_pref,
                           "set_communities": list(term.set_communities), "terminal": terminal}
        if terminal == "next-term":
            continue
        return {"applicable": True, "policy": policy, "fec": fec, "steps": steps,
                "result": {"decision": "deny" if terminal == "deny" else "permit",
                           "local_pref": None if terminal == "deny" else local_pref,
                           "communities": sorted(communities)}}
    return {"applicable": True, "policy": policy, "fec": fec, "steps": steps,
            "result": {"decision": "implicit-deny", "local_pref": None, "communities": sorted(communities)}}


def policy_context(baseline: Mapping[str, str], device: str, subject: str) -> dict[str, Any]:
    model = adapter.parse(baseline[device])
    binding = model.bindings.get(subject)
    if binding is None:
        return {"applicable": False, "reason": "subject binding not parsed"}
    policy, direction = binding
    terms = []
    for ordinal, term in enumerate(model.policies.get(policy, []), 1):
        terms.append({
            "ordinal": ordinal,
            "term": term.name,
            "action": term.action,
            "prefix_lists": [{"name": name, "mode": mode} for name, mode in term.prefix_lists],
            "route_filters": [{"prefix": prefix, "mode": mode} for prefix, mode in term.route_filters],
            "set_local_pref": term.set_local_pref,
            "set_communities": list(term.set_communities),
            "terminal": term.terminal,
        })
    referenced = sorted({item["name"] for term in terms for item in term["prefix_lists"]})
    resolved = {}
    for name in referenced:
        resolved[name] = [
            {"seq": rule.seq, "action": rule.action, "prefix": rule.network,
             "ge": rule.ge, "le": rule.le, "mode": rule.mode}
            for rule in model.prefix_lists.get(name, [])
        ]
    return {
        "applicable": True,
        "vendor": model.vendor,
        "subject": subject,
        "bound_policy": policy,
        "direction": direction,
        "ordered_terms": terms,
        "resolved_prefix_lists": resolved,
        "order_source": "first textual occurrence for Junos; numeric route-map sequence for IOS/EOS",
        "provenance": "read-only pre-change parser output; no candidate patch used",
    }


def _post_map(evaluation: Mapping[str, Any], pre: Sequence[Any]) -> dict[str, Any]:
    if not evaluation.get("candidate_configs"):
        return {}
    return {row.behavior_id: row for row in v83.post_records(evaluation["candidate_configs"], pre)}


def detailed_feedback(contract: str, evaluation: Mapping[str, Any], full_pre: Sequence[Any],
                      baseline: Mapping[str, str], subject: str) -> dict[str, Any]:
    before = {row.behavior_id: row for row in full_pre}
    after = _post_map(evaluation, full_pre)
    counterexamples: list[dict[str, Any]] = []
    if evaluation.get("transaction_error"):
        counterexamples.append({"type": "transaction", "observed": str(evaluation["transaction_error"])})
    elif evaluation.get("evaluation_error"):
        counterexamples.append({"type": "unsupported_evaluation", "observed": str(evaluation["evaluation_error"])})
    else:
        if (evaluation.get("syntax") or {}).get("status") != "PASS":
            counterexamples.append({"type": "syntax", "observed": evaluation.get("syntax")})
        report_key = "passive" if contract == "verifier_loop" else "full"
        report = (evaluation.get("reports") or {}).get(report_key, {})
        compliance, semantic = report.get("compliance", {}), report.get("semantic", {})
        if not compliance.get("goal_success", False):
            observations = []
            for item in evaluation.get("target_observations", [])[:8]:
                old = before.get(item["behavior_id"])
                observations.append({
                    "behavior_id": item["behavior_id"],
                    "fec": old.fec if old else None,
                    "dimension": item["dimension"],
                    "pre_change": old.attributes.get(item["dimension"]) if old else None,
                    "candidate_observed": item.get("observed"),
                    "required": item.get("required"),
                    "pre_change_policy_trace": trace_subject(baseline[old.device], subject, old.fec) if old else None,
                    "candidate_policy_trace": trace_subject(evaluation["candidate_configs"][old.device], subject, old.fec)
                    if old and evaluation.get("candidate_configs") else None,
                })
            counterexamples.append({
                "type": "target_unmet",
                "obligation_ids": list(compliance.get("target_failures", []))[:8],
                "observed_relations": observations,
            })
        if contract in {"verifier_loop", "oracle_contract", "full_envelope"} and not compliance.get("semantic_frame_preserved", False):
            atoms = list(semantic.get("non_target_attribute_changes", []))
            examples = []
            for atom in atoms[:8]:
                behavior_id, dimension = atom.rsplit("::", 1)
                old, new = before.get(behavior_id), after.get(behavior_id)
                examples.append({
                    "behavior_id": behavior_id,
                    "fec": old.fec if old else None,
                    "dimension": dimension,
                    "pre_change": old.attributes.get(dimension) if old else None,
                    "candidate_observed": new.attributes.get(dimension) if new else None,
                    "required": "preserve pre_change",
                    "pre_change_matched_term": (trace_subject(baseline[old.device], subject, old.fec).get("steps") or [{}])[-1].get("term") if old else None,
                    "candidate_matched_term": (trace_subject(evaluation["candidate_configs"][old.device], subject, old.fec).get("steps") or [{}])[-1].get("term")
                    if old and evaluation.get("candidate_configs") else None,
                })
            counterexamples.append({
                "type": "semantic_collateral",
                "changed_count": len(atoms),
                "examples": examples,
                "obligation_ids": list(compliance.get("frame_failures", []))[:8],
            })
        if contract == "full_envelope":
            if not compliance.get("dependency_frame_preserved", False):
                counterexamples.append({"type": "protected_dependency", "changed": list(semantic.get("protected_dependency_violations", []))[:8]})
            if not compliance.get("hard_footprint_preserved", False):
                counterexamples.append({"type": "footprint", "failures": list(compliance.get("footprint_failures", []))[:8]})
    payload = {
        "schema_version": "msn2026-v84-impact-feedback-1.0",
        "contract": contract,
        "verdict": "FAIL",
        "counterexamples": counterexamples,
        "patch_disclosed": False,
        "strategy_disclosed": False,
        "instruction": "Satisfy the target and restore every listed non-target relation to its pre-change value. Re-evaluate policy ordering and shared match scope before submitting a different baseline-relative patch.",
    }
    assert_feedback_is_patch_free(payload)
    return payload


def semantic_signature(evaluation: Mapping[str, Any]) -> str:
    report = (evaluation.get("reports") or {}).get("full", {})
    compliance, semantic = report.get("compliance", {}), report.get("semantic", {})
    material = {
        "goal": compliance.get("goal_success"),
        "target_failures": compliance.get("target_failures", []),
        "frame_changes": semantic.get("non_target_attribute_changes", []),
        "dependency": semantic.get("protected_dependency_violations", []),
        "footprint": compliance.get("footprint_failures", []),
        "transaction": evaluation.get("transaction_error"),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


def call_v2(client: InstrumentedDeepSeekClient, messages: list[dict[str, str]], baseline: Mapping[str, str], passive: Sequence[Any],
            full_pre: Sequence[Any], passive_env: Any, full_env: Any, max_tokens: int, temperature: float) -> dict[str, Any]:
    raw = ""
    try:
        raw = client.complete(messages, temperature=temperature, max_completion_tokens=max_tokens)
        edits, parsed = parse_edit_response(raw)
        return {"raw": raw, "parsed": parsed,
                "evaluation": v83.evaluate(baseline, edits, passive, full_pre, passive_env, full_env)}
    except Exception as exc:
        return {"raw": raw, "error": f"{type(exc).__name__}: {exc}",
                "evaluation": {"candidate_applied": False, "transaction_error": f"{type(exc).__name__}: {exc}",
                               "syntax": {"status": "N/A"}, "reports": {},
                               "contract_pass": {m: False for m in ("submission", *v83.ITERATIVE.values())}}}


def guided_branch(client: InstrumentedDeepSeekClient, initial: Mapping[str, Any], base_prompt: Mapping[str, Any], contract: str,
                  baseline: Mapping[str, str], passive: Sequence[Any], full_pre: Sequence[Any], passive_env: Any,
                  full_env: Any, max_tokens: int, max_submissions: int, temperature: float) -> dict[str, Any]:
    trace = [initial]
    if initial["evaluation"]["contract_pass"].get(contract):
        return {"accepted": True, "submissions": 1, "trace": trace, "controller": "impact_aware_v2"}
    signatures = [semantic_signature(initial["evaluation"])]
    for attempt_index in range(2, max_submissions + 1):
        prompt = dict(base_prompt)
        prompt["repair_attempt"] = attempt_index
        subject = base_prompt["structured_target"]["subject"]
        prompt["verification_counterexample"] = detailed_feedback(contract, trace[-1]["evaluation"], full_pre, baseline, subject)
        prompt["prior_failed_semantic_signatures"] = list(signatures)
        prompt["controller_instruction"] = (
            "Start from the immutable baseline, not the rejected text. Produce a semantically different candidate. "
            "Use the read-only ordered policy context to simulate target and listed collateral FECs before output. "
            "The edit interface may introduce new configuration lines by replacing any exact unique baseline anchor; "
            "it is not limited to modifying an already-existing object."
        )
        # A clean context prevents repeated invalid patches from anchoring later attempts.
        messages = [{"role": "system", "content": SYSTEM_V2},
                    {"role": "user", "content": json.dumps(prompt, sort_keys=True)}]
        attempt = call_v2(client, messages, baseline, passive, full_pre, passive_env, full_env, max_tokens, temperature)
        trace.append(attempt)
        signature = semantic_signature(attempt["evaluation"])
        signatures.append(signature)
        if attempt["evaluation"]["contract_pass"].get(contract):
            break
    return {"accepted": bool(trace[-1]["evaluation"]["contract_pass"].get(contract)),
            "submissions": len(trace), "trace": trace, "controller": "impact_aware_v2",
            "semantic_signatures": signatures}


def run_case(case: Mapping[str, Any], data_root: Path, max_tokens: int, max_submissions: int,
             temperature: float, thinking_mode: str) -> dict[str, Any]:
    sid = case["scenario_id"]
    scenario = data_root / "public/scenarios" / sid
    metadata, intent = v83.load(scenario / "metadata.json"), v83.load(scenario / "intent.json")
    device = metadata["device"]
    baseline = {device: (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")}
    candidate_row = v83.load(data_root / case["candidate_path"])
    passive = v83.records(v83.load(scenario / "passive_observations.json"))
    complete_pre_rows = v83.load(data_root / "sealed/oracles" / sid / "complete_pre_observations.json")
    active_rows = v83.load(data_root / "sealed/oracles" / sid / "active_observations.json")
    full_pre = passive + v83.records(active_rows)
    passive_env = v83.make_envelope(intent, baseline, passive, "operator_visible_passive_tests")
    full_env = v83.make_envelope(intent, baseline, full_pre, "coverage_directed_active_policy_classes")
    initial_eval = v83.evaluate(baseline, [v83.SearchReplaceEdit(**row) for row in candidate_row["edits"]],
                                passive, full_pre, passive_env, full_env)
    initial_raw = json.dumps({"edits": candidate_row["edits"], "summary": "frozen v8.3 first candidate"}, sort_keys=True)
    initial = {"raw": initial_raw, "parsed": json.loads(initial_raw), "evaluation": initial_eval, "replayed": True}
    subject = intent["selector"]["subjects"][0]
    prompt = {
        "intent": intent["raw_text"],
        "structured_target": {"device": device, "subject": subject, "prefix": intent["selector"]["fecs"][0],
                              "dimension": intent["changes"][0]["dimension"], "required_value": intent["changes"][0]["desired"]},
        "vendor": metadata["vendor"],
        "baseline_config": baseline[device],
        "read_only_policy_context": policy_context(baseline, device, subject),
        "pre_change_target_trace": trace_subject(baseline[device], subject, intent["selector"]["fecs"][0]),
    }
    client = InstrumentedDeepSeekClient(timeout_s=240, max_retries=2, thinking_mode=thinking_mode)
    start = len(client.metrics.calls)
    arm = guided_branch(client, initial, prompt, "full_envelope", baseline, passive, full_pre, passive_env, full_env,
                        max_tokens, max_submissions, temperature)
    arm["llm_usage"] = v83.usage_since(client, start)
    final_eval = arm["trace"][-1]["evaluation"]
    arm["oracle"] = v83.oracle(final_eval, complete_pre_rows, intent)
    arm["verified_completion"] = bool(arm["accepted"] and arm["oracle"]["safe"])
    arm["unsafe_release"] = bool(arm["accepted"] and not arm["oracle"]["safe"])
    arm["reject_to_repair_success"] = bool(arm["submissions"] > 1 and arm["verified_completion"])
    return {
        "schema_version": "msn2026-v84-agent-repair-case-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case["case_id"], "scenario_id": sid, "mode": case["mode"], "source_group": metadata["source_group"],
        "vendor": metadata["vendor"], "family": metadata["family"], "first_candidate_sha256": candidate_row["candidate_sha256"],
        "development_replay_of_v83": True, "arm": arm, "llm_metrics": client.metrics.to_dict(),
        "max_submissions": max_submissions, "temperature": temperature, "thinking_mode": thinking_mode,
    }


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(p * len(values)) - 1))]


def summarize(paths: Sequence[Path], output_root: Path) -> dict[str, Any]:
    cases = [v83.load(path) for path in paths]
    arms = [row["arm"] for row in cases]
    calls = [call for row in cases for call in row["llm_metrics"]["calls"]]
    latencies = [float(call["latency_ms"]) for call in calls]
    failure_taxonomy: dict[str, int] = {}
    for arm in arms:
        if arm["verified_completion"]:
            key = "verified_completion"
        elif arm["unsafe_release"]:
            key = "unsafe_release"
        elif arm["oracle"]["goal"]:
            key = "semantic_collateral_exhaustion"
        else:
            key = "target_unmet_exhaustion"
        failure_taxonomy[key] = failure_taxonomy.get(key, 0) + 1
    summary = {
        "schema_version": "msn2026-v84-agent-repair-summary-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "verified_completion": sum(a["verified_completion"] for a in arms),
        "unsafe_release": sum(a["unsafe_release"] for a in arms),
        "reject_to_repair_success": sum(a["reject_to_repair_success"] for a in arms),
        "attempt_exhaustion": sum(a["submissions"] >= cases[i]["max_submissions"] and not a["accepted"] for i, a in enumerate(arms)),
        "failure_taxonomy": failure_taxonomy,
        "total_submissions": sum(a["submissions"] for a in arms),
        "logical_llm_calls": len(calls),
        "backend_attempts": sum(a["llm_usage"]["backend_attempts"] for a in arms),
        "retry_count": sum(a["llm_usage"]["retry_count"] for a in arms),
        "token_usage": {key: sum(a["llm_usage"]["token_usage"][key] for a in arms) for key in ("prompt", "completion", "total")},
        "latency_ms_total": sum(latencies),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": percentile(latencies, .95),
        "development_replay_of_v83": True,
        "claim_status": "post-freeze mechanism development; not confirmatory",
    }
    v83.write(output_root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v84_agent_repair/dev_replay"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--only-v83-full-failures", action="store_true")
    parser.add_argument("--max-submissions", type=int, default=8)
    parser.add_argument("--max-completion-tokens", type=int, default=5000)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--thinking-mode", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()
    subset = v83.load(args.data_root / "agent128_subset.json")["cases"]
    if args.case_id:
        wanted = set(args.case_id)
        subset = [case for case in subset if case["case_id"] in wanted]
    if args.only_v83_full_failures:
        old_root = Path("results/msn2026_v83_external/agent128/cases")
        failed = set()
        for path in old_root.glob("*.json"):
            row = v83.load(path)
            if not row["arms"]["full_envelope"]["verified_completion"]:
                failed.add(row["case_id"])
        subset = [case for case in subset if case["case_id"] in failed]
    selected = subset[args.offset:(args.offset + args.limit) if args.limit else None]
    for ordinal, case in enumerate(selected, 1):
        output = args.output_root / "cases" / f"{case['scenario_id']}__{case['mode']}.json"
        if output.exists() and not args.rerun:
            print(f"repair-v2 {ordinal}/{len(selected)} {case['case_id']} SKIP", flush=True)
            continue
        started = time.perf_counter()
        row = run_case(case, args.data_root, args.max_completion_tokens, args.max_submissions,
                       args.temperature, args.thinking_mode)
        v83.write(output, row)
        print(f"repair-v2 {ordinal}/{len(selected)} {case['case_id']} vc={row['arm']['verified_completion']} "
              f"calls={row['llm_metrics']['logical_llm_calls']} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    summary = summarize(sorted((args.output_root / "cases").glob("*.json")), args.output_root)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
