#!/usr/bin/env python3
"""Causal pilot: replay one common LLM candidate under two contracts.

Both arms share the complete generation/repair trajectory until a syntactically
valid, goal-satisfying candidate exists. Goal-only then stops. The Full
Envelope arm evaluates that exact candidate and may issue one patch-free
counterexample. No first candidate is resampled per arm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.agent_benchmark import (
    SYSTEM_PROMPT,
    _post_records,
    evaluate_submission,
    parse_edit_response,
)
from experiments.msn2026_v8_day2_agent.change_envelope import apply_search_replace_edits
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    augment_behavior_universe_with_frr_probes,
    build_frr_dependency_graph,
    derive_change_envelope_v2,
)
from experiments.msn2026_v8_day2_agent.counterexample_feedback import build_counterexample_feedback
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient


PAIR_SYSTEM = SYSTEM_PROMPT + "\nYou are not told an expected object name, patch shape, or repair strategy."


def configs(path: Path) -> Dict[str, str]:
    return {item.stem: item.read_text(encoding="utf-8") for item in sorted(path.glob("*.conf"))}


def records(path: Path) -> list[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def changed_atoms(before: Sequence[BehaviorRecord], after: Sequence[BehaviorRecord]) -> list[str]:
    post = {row.behavior_id: row for row in after}
    output = []
    for old in before:
        new = post.get(old.behavior_id)
        if not new:
            output.append(f"{old.behavior_id}::missing")
            continue
        for dimension in sorted(set(old.attributes) | set(new.attributes)):
            if old.attributes.get(dimension) != new.attributes.get(dimension):
                output.append(f"{old.behavior_id}::{dimension}")
    return output


def oracle_safety(
    baseline: Mapping[str, str],
    evaluation: Mapping[str, Any],
    heldout_pre: Sequence[BehaviorRecord],
) -> Dict[str, Any]:
    if not evaluation.get("candidate_applied"):
        return {"safe": False, "heldout_changes": [], "reason": "candidate_not_applied"}
    candidate = evaluation["candidate_configs"]
    heldout_post = _post_records(candidate, heldout_pre)
    changes = changed_atoms(heldout_pre, heldout_post)
    goal = bool((evaluation.get("contract_pass") or {}).get("syntax_goal"))
    visible_collateral = list(
        ((evaluation.get("report") or {}).get("semantic") or {}).get("non_target_attribute_changes", [])
    )
    return {
        "safe": bool(goal and not visible_collateral and not changes),
        "goal": goal,
        "visible_collateral": visible_collateral,
        "heldout_changes": changes,
        "heldout_records_disclosed_to_model": False,
        "heldout_records_disclosed_to_verifier": False,
    }


def call_and_evaluate(
    *,
    client: InstrumentedDeepSeekClient,
    messages: list[Dict[str, str]],
    baseline: Mapping[str, str],
    visible_pre: Sequence[BehaviorRecord],
    envelope: Any,
    attempt_root: Path,
    temperature: float,
    max_completion_tokens: int,
    frr_container: str,
) -> Dict[str, Any]:
    raw = client.complete(messages, temperature=temperature, max_completion_tokens=max_completion_tokens)
    edits, parsed = parse_edit_response(raw)
    evaluation = evaluate_submission(
        baseline, edits, visible_pre, envelope, attempt_root, frr_container
    )
    return {"raw_llm_response": raw, "parsed_submission": parsed, "evaluation": evaluation}


def run_case(
    scenario_root: Path,
    output_root: Path,
    client: InstrumentedDeepSeekClient,
    *,
    common_attempts: int,
    envelope_revisions: int,
    temperature: float,
    max_completion_tokens: int,
    frr_container: str,
) -> Dict[str, Any]:
    baseline = configs(scenario_root / "baseline")
    visible_observed = records(scenario_root / "visible_pre_observations.json")
    heldout_pre = records(scenario_root / "heldout_pre_observations.json")
    intent = json.loads((scenario_root / "intent.json").read_text(encoding="utf-8"))
    visible_pre, probe_provenance = augment_behavior_universe_with_frr_probes(
        baseline, visible_observed
    )
    envelope = derive_change_envelope_v2(
        intent,
        baseline,
        visible_pre,
        build_frr_dependency_graph(baseline),
        behavior_universe_provenance={
            "backend": "paired-agent-visible-plus-active-config-boundary-probes",
            "complete": False,
            "uncovered_reason": "finite equivalence-class representatives; held-out routes reserved for outcome oracle",
            "active_probe_plan": probe_provenance,
        },
    )
    initial = {
        "intent": intent["raw_text"],
        "baseline_configs": baseline,
        "eligible_information": "Only immutable pre-state configuration is supplied; choose your own implementation.",
    }
    messages = [
        {"role": "system", "content": PAIR_SYSTEM},
        {"role": "user", "content": json.dumps(initial, sort_keys=True)},
    ]
    common_trace = []
    common_candidate = None
    for attempt in range(1, common_attempts + 1):
        try:
            item = call_and_evaluate(
                client=client,
                messages=messages,
                baseline=baseline,
                visible_pre=visible_pre,
                envelope=envelope,
                attempt_root=output_root / "common" / f"attempt_{attempt}",
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                frr_container=frr_container,
            )
            common_trace.append(item)
            if item["evaluation"]["contract_pass"]["syntax_goal"]:
                common_candidate = item
                break
            feedback = build_counterexample_feedback("syntax_goal", item["evaluation"])
            messages.extend(
                [
                    {"role": "assistant", "content": item["raw_llm_response"]},
                    {"role": "user", "content": json.dumps({"verification_counterexample": feedback, "instruction": "Revise your own baseline-relative edits."}, sort_keys=True)},
                ]
            )
        except Exception as exc:
            common_trace.append({"error": f"{type(exc).__name__}: {exc}"})
            messages.append({"role": "user", "content": json.dumps({"error": str(exc), "instruction": "Return valid edit JSON."})})

    if common_candidate is None:
        return {
            "scenario_id": scenario_root.name,
            "paired_eligible": False,
            "reason": "common syntax/goal trajectory exhausted",
            "common_trace": common_trace,
        }

    initial_eval = common_candidate["evaluation"]
    initial_oracle = oracle_safety(baseline, initial_eval, heldout_pre)
    # The goal-only arm is a replay verdict over the exact shared candidate.
    goal_arm = {
        "accepted": True,
        "candidate_identity": hashlib.sha256(
            json.dumps(common_candidate["parsed_submission"], sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "oracle": initial_oracle,
        "extra_llm_calls_after_common": 0,
    }

    full_trace = []
    final_eval = initial_eval
    final_oracle = initial_oracle
    if not initial_eval["contract_pass"]["full_envelope"]:
        branch_messages = list(messages)
        # If the common candidate was produced before messages were extended,
        # explicitly append it once before sending envelope-only evidence.
        branch_messages.extend(
            [
                {"role": "assistant", "content": common_candidate["raw_llm_response"]},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "verification_counterexample": build_counterexample_feedback("full_envelope", initial_eval),
                            "instruction": "Revise your own baseline-relative edits; do not assume a prescribed strategy.",
                        },
                        sort_keys=True,
                    ),
                },
            ]
        )
        for revision in range(1, envelope_revisions + 1):
            try:
                item = call_and_evaluate(
                    client=client,
                    messages=branch_messages,
                    baseline=baseline,
                    visible_pre=visible_pre,
                    envelope=envelope,
                    attempt_root=output_root / "full_envelope" / f"revision_{revision}",
                    temperature=temperature,
                    max_completion_tokens=max_completion_tokens,
                    frr_container=frr_container,
                )
                full_trace.append(item)
                final_eval = item["evaluation"]
                final_oracle = oracle_safety(baseline, final_eval, heldout_pre)
                if final_eval["contract_pass"]["full_envelope"]:
                    break
                branch_messages.extend(
                    [
                        {"role": "assistant", "content": item["raw_llm_response"]},
                        {"role": "user", "content": json.dumps({"verification_counterexample": build_counterexample_feedback("full_envelope", final_eval), "instruction": "Revise again."}, sort_keys=True)},
                    ]
                )
            except Exception as exc:
                full_trace.append({"error": f"{type(exc).__name__}: {exc}"})

    full_arm = {
        "accepted": bool(final_eval["contract_pass"]["full_envelope"]),
        "initial_candidate_accepted": bool(initial_eval["contract_pass"]["full_envelope"]),
        "oracle": final_oracle,
        "extra_llm_calls_after_common": len(full_trace),
        "trace": full_trace,
    }
    return {
        "scenario_id": scenario_root.name,
        "paired_eligible": True,
        "same_first_candidate": True,
        "heldout_not_in_prompt_or_contract": True,
        "active_probe_provenance": probe_provenance,
        "common_attempts": len(common_trace),
        "common_trace": common_trace,
        "goal_only": goal_arm,
        "full_envelope": full_arm,
        "initial_full_counterexample": build_counterexample_feedback("full_envelope", initial_eval),
    }


def run(
    data_root: Path,
    output_root: Path,
    *,
    selected_case_ids: set[str] | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    client = InstrumentedDeepSeekClient(timeout_s=180, max_retries=2)
    cases = []
    for scenario_root in sorted((data_root / "scenarios").iterdir()):
        if selected_case_ids and scenario_root.name not in selected_case_ids:
            continue
        cases.append(run_case(scenario_root, output_root / scenario_root.name, client, **kwargs))
    eligible = [case for case in cases if case["paired_eligible"]]
    summary = {
        "case_count": len(cases),
        "paired_eligible": len(eligible),
        "same_first_candidate_cases": sum(case.get("same_first_candidate", False) for case in eligible),
        "goal_only_unsafe_accepts": sum(case["goal_only"]["accepted"] and not case["goal_only"]["oracle"]["safe"] for case in eligible),
        "full_envelope_unsafe_accepts": sum(case["full_envelope"]["accepted"] and not case["full_envelope"]["oracle"]["safe"] for case in eligible),
        "full_envelope_safe_accepts": sum(case["full_envelope"]["accepted"] and case["full_envelope"]["oracle"]["safe"] for case in eligible),
        "cases_requiring_envelope_revision": sum(not case["full_envelope"]["initial_candidate_accepted"] for case in eligible),
        "logical_llm_calls": client.metrics.logical_llm_calls,
        "backend_attempts": client.metrics.backend_attempts,
        "retry_count": client.metrics.retry_count,
        "token_usage": asdict(client.metrics.token_usage),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "paired_results.json").write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "llm_metrics.json").write_text(json.dumps(client.metrics.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"summary": summary, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v81_nontriviality"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v81_paired_agent_pilot"))
    parser.add_argument("--common-attempts", type=int, default=2)
    parser.add_argument("--envelope-revisions", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-completion-tokens", type=int, default=5000)
    parser.add_argument("--frr-container", default="pathdelta-msn2026-frr-syntax")
    parser.add_argument(
        "--case-ids",
        default="",
        help="comma-separated frozen case ids; empty means every scenario",
    )
    args = parser.parse_args()
    result = run(
        args.data_root,
        args.output_root,
        selected_case_ids={item for item in args.case_ids.split(",") if item} or None,
        common_attempts=args.common_attempts,
        envelope_revisions=args.envelope_revisions,
        temperature=args.temperature,
        max_completion_tokens=args.max_completion_tokens,
        frr_container=args.frr_container,
    )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
