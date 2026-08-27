"""Method-faithful adapters for the v8.5 same-task comparison."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Callable, Mapping, Sequence

from experiments.msn2026_v8_day2_agent.agent_benchmark import parse_edit_response
from experiments.msn2026_v8_day2_agent.change_envelope import SearchReplaceEdit
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient
from experiments.msn2026_v83_external import run_agent128 as v83
from experiments.msn2026_v84_agent_repair import run_agent128_repair_v2 as v84

from . import method_prompts as prompts


METHODS = (
    "llm_netcfg_adapted",
    "inta_adapted",
    "cosynth_vpp_adapted",
    "cornetto_agentic_adapted",
    "pathdelta_fullr",
)


class BudgetExhausted(RuntimeError):
    pass


class BudgetedModel:
    def __init__(
        self,
        *,
        max_calls: int,
        max_completion_tokens_per_call: int,
        max_completion_tokens_per_case: int,
        temperature: float,
        thinking_mode: str,
    ) -> None:
        self.client = InstrumentedDeepSeekClient(
            timeout_s=240,
            max_retries=2,
            thinking_mode=thinking_mode,
        )
        self.max_calls = max_calls
        self.max_per_call = max_completion_tokens_per_call
        self.max_completion = max_completion_tokens_per_case
        self.temperature = temperature

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        used = self.client.metrics.token_usage.completion
        remaining = self.max_completion - used
        if self.client.metrics.logical_llm_calls >= self.max_calls or remaining <= 0:
            raise BudgetExhausted("frozen logical-call/completion-token budget exhausted")
        return self.client.complete(
            messages,
            temperature=self.temperature,
            max_completion_tokens=min(self.max_per_call, remaining),
        )

    def metrics(self) -> dict[str, Any]:
        return self.client.metrics.to_dict()


def parse_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"{.*}", raw, re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def parse_edit_response_checked(
    raw: str, expected_devices: Sequence[str]
) -> tuple[list[SearchReplaceEdit], dict[str, Any]]:
    """Validate the common transaction shape with actionable, patch-free errors."""
    value = parse_json(raw)
    rows = value.get("edits")
    if not isinstance(rows, list):
        raise ValueError("response must contain an edits array")
    allowed = set(expected_devices)
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"edit {index} must be a JSON object")
        missing = [
            key for key in ("device", "old_text", "new_text") if key not in row
        ]
        if missing:
            raise ValueError(
                f"edit {index} missing required keys {missing}; every edit must "
                "repeat device exactly as structured_target.device and include "
                "old_text and new_text"
            )
        if row["device"] not in allowed:
            raise ValueError(
                f"edit {index} has unknown device {row['device']!r}; use one of "
                f"the task device keys {sorted(allowed)!r}"
            )
    return parse_edit_response(raw)




def compact_policy_execution(config: str, subject: str, fec: str) -> dict[str, Any]:
    """Candidate-derived semantic evidence without commands or repair strategy."""
    trace = v84.trace_subject(config, subject, fec)
    if not trace.get("applicable"):
        return trace
    terms = []
    for step in trace.get("steps", []):
        prefix_lists = [
            {"name": row.get("name"), "permits_fec": row.get("permits_fec")}
            for row in step.get("prefix_list_results", [])
        ]
        route_filters = [
            {
                "prefix": row.get("prefix"),
                "mode": row.get("mode"),
                "matches_fec": row.get("matches_fec"),
            }
            for row in step.get("route_filter_results", [])
        ]
        terms.append({
            "ordinal": step.get("ordinal"),
            "term": step.get("term"),
            "matched": step.get("matched"),
            "prefix_lists": prefix_lists,
            "route_filters": route_filters,
            "applied": step.get("applied"),
        })
    return {
        "policy": trace.get("policy"),
        "fec": fec,
        "evaluated_terms": terms,
        "result": trace.get("result"),
        "evidence_only": True,
    }


def impact_feedback(
    evaluation: Mapping[str, Any],
    full_pre: Sequence[Any],
    baseline: Mapping[str, str],
    device: str,
    subject: str,
) -> dict[str, Any]:
    """Augment Envelope counterexamples with compact candidate execution evidence."""
    payload = v84.detailed_feedback(
        "full_envelope", evaluation, full_pre, baseline, subject
    )
    candidate_configs = evaluation.get("candidate_configs") or {}
    candidate = candidate_configs.get(device)
    if not isinstance(candidate, str):
        return payload
    for counterexample in payload.get("counterexamples", []):
        rows = (
            counterexample.get("observed_relations")
            or counterexample.get("examples")
            or []
        )
        for row in rows:
            fec = row.get("fec")
            if fec:
                row["candidate_policy_execution"] = compact_policy_execution(
                    candidate, subject, fec
                )
    payload["execution_evidence_added"] = True
    payload["patch_disclosed"] = False
    payload["strategy_disclosed"] = False
    return payload


def failed_evaluation(error: Exception | str) -> dict[str, Any]:
    return {
        "candidate_applied": False,
        "transaction_error": str(error),
        "syntax": {"status": "N/A"},
        "reports": {},
        "contract_pass": {
            key: False for key in (
                "submission", "goal_only", "write_scope", "verifier_loop",
                "oracle_contract", "full_envelope",
            )
        },
    }


def evaluate_raw_patch(
    raw: str,
    baseline: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
) -> dict[str, Any]:
    try:
        edits, parsed = parse_edit_response_checked(raw, tuple(baseline))
        evaluation = v83.evaluate(
            baseline, edits, visible_pre, visible_pre, visible_env, visible_env
        )
        return {"raw": raw, "parsed": parsed, "evaluation": evaluation}
    except Exception as exc:
        return {
            "raw": raw,
            "error": f"{type(exc).__name__}: {exc}",
            "evaluation": failed_evaluation(exc),
        }


def candidate_from_current(
    baseline: Mapping[str, str],
    current: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
) -> dict[str, Any]:
    edits = [
        SearchReplaceEdit(device=device, old_text=baseline[device], new_text=text)
        for device, text in current.items()
        if text != baseline[device]
    ]
    evaluation = v83.evaluate(
        baseline, edits, visible_pre, visible_pre, visible_env, visible_env
    )
    return {
        "raw": json.dumps({"edits": [asdict(edit) for edit in edits]}, sort_keys=True),
        "parsed": {"edits": [asdict(edit) for edit in edits]},
        "evaluation": evaluation,
    }


def common_payload(
    metadata: Mapping[str, Any],
    intent: Mapping[str, Any],
    baseline: Mapping[str, str],
) -> dict[str, Any]:
    device = metadata["device"]
    subject = metadata["subject"]
    target_fec = intent["selector"]["fecs"][0]
    return {
        "intent": intent["raw_text"],
        "structured_target": {
            "device": device,
            "subject": intent["selector"]["subjects"][0],
            "prefix": target_fec,
            "dimension": intent["changes"][0]["dimension"],
            "required_value": intent["changes"][0]["desired"],
        },
        "vendor": metadata["vendor"],
        "baseline_config": baseline[device],
        "read_only_network_status": {
            "bound_policy_context": v84.policy_context(baseline, device, subject),
            "pre_change_target_trace": v84.trace_subject(
                baseline[device], subject, target_fec
            ),
            "provenance": (
                "candidate-independent parser output; no expected patch, object, "
                "command, or insertion point"
            ),
        },
    }


def vendor_evidence(metadata: Mapping[str, Any]) -> dict[str, Any]:
    vendor = metadata["vendor"]
    if vendor == "cisco_ios":
        facts = [
            "route-map clauses execute in ascending numeric sequence",
            "match ip address prefix-list uses prefixes permitted by the named list",
            "a prefix-list deny does not create a positive route-map match",
            "set local-preference changes local preference of a matched route",
            "preserve existing sequence semantics unless the request requires change",
        ]
    elif vendor == "juniper_junos":
        facts = [
            "policy-statement terms execute in configuration order",
            "set-format term order follows the first occurrence of each term",
            "then next term continues; accept/reject terminates evaluation",
            "route-filter matching modes affect covered prefix lengths",
        ]
    else:
        facts = [
            "ordered policy clauses preserve match scope and control flow",
            "referenced prefix and community objects may be shared",
        ]
    return {
        "source": "frozen vendor card used as target-manual retrieval result",
        "vendor": vendor,
        "facts": facts,
        "retrieval_is_candidate_independent": True,
    }


def finalize_arm(
    model: BudgetedModel,
    trace: list[dict[str, Any]],
    *,
    accepted: bool,
    acceptance_basis: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "accepted": accepted,
        "acceptance_basis": acceptance_basis,
        "attempt_exhaustion": not accepted,
        "trace": trace,
        "submissions": sum("evaluation" in row for row in trace),
        "llm_metrics": model.metrics(),
    }
    if extra:
        result.update(extra)
    return result


def run_llm_netcfg(
    model: BudgetedModel,
    metadata: Mapping[str, Any],
    intent: Mapping[str, Any],
    baseline: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
) -> dict[str, Any]:
    payload = common_payload(metadata, intent, baseline)
    trace: list[dict[str, Any]] = []
    try:
        translation_raw = model.complete([
            {"role": "system", "content": prompts.LLM_NETCFG_CLASSIFY},
            {"role": "user", "content": json.dumps({
                "requirement": intent["raw_text"],
                "network_status": {
                    "device": metadata["device"],
                    "vendor": metadata["vendor"],
                    "subject": metadata["subject"],
                },
            }, sort_keys=True)},
        ])
        translation = parse_json(translation_raw)
        trace.append({"stage": "classification_translation", "raw": translation_raw, "parsed": translation})
        repair_feedback = None
        for attempt in range(1, 9):
            request = {
                **payload,
                "translated_requirement": translation,
                "attempt": attempt,
            }
            if repair_feedback is not None:
                request["verifier_report"] = repair_feedback
            system = prompts.LLM_NETCFG_GENERATE if attempt == 1 else prompts.LLM_NETCFG_REPAIR
            raw = model.complete([
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(request, sort_keys=True)},
            ])
            candidate = evaluate_raw_patch(raw, baseline, visible_pre, visible_env)
            candidate.update({"stage": "configuration_generation", "attempt": attempt})
            trace.append(candidate)
            if candidate["evaluation"]["contract_pass"].get("goal_only", False):
                return finalize_arm(
                    model, trace, accepted=True,
                    acceptance_basis="LLM-NetCFG syntax and primary-goal verifier",
                )
            repair_feedback = v83.compact_feedback("goal_only", candidate["evaluation"])
    except Exception as exc:
        trace.append({"stage": "pipeline_error", "error": f"{type(exc).__name__}: {exc}"})
    return finalize_arm(
        model, trace, accepted=False,
        acceptance_basis="LLM-NetCFG syntax and primary-goal verifier",
    )


def run_inta(
    model: BudgetedModel,
    metadata: Mapping[str, Any],
    intent: Mapping[str, Any],
    baseline: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
) -> dict[str, Any]:
    payload = common_payload(metadata, intent, baseline)
    trace: list[dict[str, Any]] = []
    last_audit: dict[str, Any] | None = None
    evidence = vendor_evidence(metadata)
    try:
        extract_raw = model.complete([
            {"role": "system", "content": prompts.INTA_EXTRACT},
            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
        ])
        extraction = parse_json(extract_raw)
        trace.append({"stage": "intent_extraction", "raw": extract_raw, "parsed": extraction})
        candidate: dict[str, Any] | None = None
        for attempt in range(1, 4):
            generation_payload = {
                **payload,
                "intent_fragments": extraction,
                "retrieved_vendor_evidence": evidence,
                "attempt": attempt,
            }
            if candidate is not None:
                generation_payload["previous_candidate"] = candidate.get("parsed")
                generation_payload["semantic_report"] = last_audit
                generation_payload["syntax_report"] = candidate["evaluation"].get("syntax")
            raw = model.complete([
                {
                    "role": "system",
                    "content": prompts.INTA_GENERATE if attempt == 1 else prompts.INTA_REFINE,
                },
                {"role": "user", "content": json.dumps(generation_payload, sort_keys=True)},
            ])
            candidate = evaluate_raw_patch(raw, baseline, visible_pre, visible_env)
            candidate.update({
                "stage": "incremental_generation" if attempt == 1 else "semantic_refinement",
                "attempt": attempt,
            })
            trace.append(candidate)
            audit_raw = model.complete([
                {"role": "system", "content": prompts.INTA_SEMANTIC_AUDIT},
                {"role": "user", "content": json.dumps({
                    "intent": intent["raw_text"],
                    "baseline_fragments": extraction,
                    "candidate_patch": candidate.get("parsed"),
                    "visible_semantic_verifier_report": v83.compact_feedback(
                        "goal_only", candidate["evaluation"]
                    ),
                }, sort_keys=True)},
            ])
            last_audit = parse_json(audit_raw)
            trace.append({"stage": "semantic_audit", "attempt": attempt, "raw": audit_raw, "parsed": last_audit})
            syntax_pass = candidate["evaluation"].get("syntax", {}).get("status") == "PASS"
            target_pass = candidate["evaluation"].get("contract_pass", {}).get(
                "goal_only", False
            )
            if syntax_pass and target_pass and bool(last_audit.get("consistent")):
                return finalize_arm(
                    model, trace, accepted=True,
                    acceptance_basis=(
                        "INTA syntax check, visible target semantics, and LLM semantic report"
                    ),
                    extra={"retrieved_evidence": evidence},
                )
    except Exception as exc:
        trace.append({"stage": "pipeline_error", "error": f"{type(exc).__name__}: {exc}"})
    return finalize_arm(
        model, trace, accepted=False,
        acceptance_basis=(
            "INTA syntax check, visible target semantics, and LLM semantic report"
        ),
        extra={"retrieved_evidence": evidence, "last_semantic_audit": last_audit},
    )


def run_cosynth(
    model: BudgetedModel,
    metadata: Mapping[str, Any],
    intent: Mapping[str, Any],
    baseline: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
) -> dict[str, Any]:
    payload = common_payload(metadata, intent, baseline)
    trace: list[dict[str, Any]] = []
    feedback = None
    try:
        for attempt in range(1, 9):
            request = {**payload, "attempt": attempt}
            if feedback is not None:
                request["localized_verifier_feedback"] = feedback
            raw = model.complete([
                {
                    "role": "system",
                    "content": prompts.COSYNTH_GENERATE if attempt == 1 else prompts.COSYNTH_REPAIR,
                },
                {"role": "user", "content": json.dumps(request, sort_keys=True)},
            ])
            candidate = evaluate_raw_patch(raw, baseline, visible_pre, visible_env)
            candidate.update({"stage": "verified_prompt_programming", "attempt": attempt})
            trace.append(candidate)
            if candidate["evaluation"]["contract_pass"].get("verifier_loop", False):
                return finalize_arm(
                    model, trace, accepted=True,
                    acceptance_basis="CoSynth syntax plus visible semantic verifiers",
                )
            feedback = v83.compact_feedback("verifier_loop", candidate["evaluation"])
    except Exception as exc:
        trace.append({"stage": "pipeline_error", "error": f"{type(exc).__name__}: {exc}"})
    return finalize_arm(
        model, trace, accepted=False,
        acceptance_basis="CoSynth syntax plus visible semantic verifiers",
    )


def cornetto_observation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    report = (evaluation.get("reports") or {}).get("passive", {})
    compliance = report.get("compliance", {})
    semantic = report.get("semantic", {})
    regressions = list(semantic.get("non_target_attribute_changes", []))
    return {
        "syntax": evaluation.get("syntax"),
        "target_fixed": bool(compliance.get("goal_success")),
        "remaining_target_obligations": list(compliance.get("target_failures", []))[:8],
        "regressions": regressions[:8],
        "regression_count": len(regressions),
        "all_visible_specs_satisfied": bool(
            evaluation.get("contract_pass", {}).get("verifier_loop", False)
        ),
    }


def run_cornetto(
    model: BudgetedModel,
    metadata: Mapping[str, Any],
    intent: Mapping[str, Any],
    baseline: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
) -> dict[str, Any]:
    current = dict(baseline)
    checkpoint = dict(baseline)
    payload = common_payload(metadata, intent, baseline)
    trace: list[dict[str, Any]] = []
    conversation = [
        {"role": "system", "content": prompts.CORNETTO_SYSTEM},
        {"role": "user", "content": json.dumps({
            "task": intent["raw_text"],
            "available_device": metadata["device"],
            "vendor": metadata["vendor"],
            "visible_violated_specification": payload["structured_target"],
            "instructions": "Use tools and submit a final current configuration.",
        }, sort_keys=True)},
    ]
    accepted = submitted = False
    try:
        for step in range(1, model.max_calls + 1):
            raw = model.complete(conversation)
            try:
                action = parse_json(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                observation = {
                    "error": "malformed tool action JSON",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "instruction": "Return exactly one complete JSON tool action.",
                }
                trace.append({
                    "stage": "cornetto_react_step",
                    "step": step,
                    "raw": raw,
                    "action": None,
                    "observation": observation,
                })
                conversation.extend([
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": json.dumps(
                        {"tool_observation": observation}, sort_keys=True
                    )},
                ])
                continue
            name = str(action.get("action", ""))
            params = action.get("action_input") or {}
            candidate: dict[str, Any] | None = None
            if name == "inspect_config":
                device = params.get("device") or params.get("router_name") or metadata["device"]
                observation: Any = {"device": device, "config": current.get(device, "ERROR: unknown device")}
            elif name == "get_violated_specs":
                observation = {
                    "required_target": payload["structured_target"],
                    "note": "verify reports non-target regressions if introduced",
                }
            elif name == "get_topology":
                observation = {
                    "device": metadata["device"],
                    "subject": metadata["subject"],
                    "bound_policy_context": v84.policy_context(
                        baseline, metadata["device"], metadata["subject"]
                    ),
                }
            elif name == "apply_patch":
                device = params.get("device") or params.get("router_name")
                old = params.get("old_text") if "old_text" in params else params.get("search")
                new = params.get("new_text") if "new_text" in params else params.get("replace")
                if device not in current:
                    observation = {"error": "unknown device"}
                elif not isinstance(old, str) or not old:
                    observation = {"error": "empty search block"}
                elif current[device].count(old) != 1:
                    observation = {
                        "error": "search block must occur exactly once in current config",
                        "occurrences": current[device].count(old),
                    }
                else:
                    current[device] = current[device].replace(old, str(new or ""), 1)
                    observation = {"patch_applied": True, "device": device}
            elif name == "verify":
                candidate = candidate_from_current(baseline, current, visible_pre, visible_env)
                observation = cornetto_observation(candidate["evaluation"])
                report = (candidate["evaluation"].get("reports") or {}).get("passive", {})
                if report.get("compliance", {}).get("semantic_frame_preserved", False):
                    checkpoint = dict(current)
            elif name == "rollback":
                current = dict(checkpoint)
                observation = {"rolled_back_to_checkpoint": True}
            elif name == "submit":
                candidate = candidate_from_current(baseline, current, visible_pre, visible_env)
                observation = {"submitted": True}
                submitted = accepted = True
            else:
                observation = {
                    "error": "unknown action",
                    "available": [
                        "inspect_config", "get_violated_specs", "get_topology",
                        "apply_patch", "verify", "rollback", "submit",
                    ],
                }
            row = {
                "stage": "cornetto_react_step",
                "step": step,
                "raw": raw,
                "action": action,
                "observation": observation,
            }
            if candidate is not None:
                row.update({"evaluation": candidate["evaluation"], "parsed": candidate["parsed"]})
            trace.append(row)
            conversation.extend([
                {"role": "assistant", "content": raw},
                {"role": "user", "content": json.dumps({"tool_observation": observation}, sort_keys=True)},
            ])
            if submitted:
                break
    except Exception as exc:
        trace.append({"stage": "pipeline_error", "error": f"{type(exc).__name__}: {exc}"})
    final_candidate = candidate_from_current(baseline, current, visible_pre, visible_env)
    trace.append({"stage": "cornetto_final_state", **final_candidate})
    return finalize_arm(
        model, trace, accepted=accepted,
        acceptance_basis="Cornetto submit action after self-directed tool loop",
        extra={
            "submitted": submitted,
            "tool_steps": sum(row.get("stage") == "cornetto_react_step" for row in trace),
            "final_current_configs": current,
        },
    )


def run_pathdelta(
    model: BudgetedModel,
    metadata: Mapping[str, Any],
    intent: Mapping[str, Any],
    baseline: Mapping[str, str],
    visible_pre: Sequence[Any],
    visible_env: Any,
    active_pre: Sequence[Any],
    full_env: Any,
) -> dict[str, Any]:
    full_pre = list(visible_pre) + list(active_pre)
    payload = common_payload(metadata, intent, baseline)
    subject = metadata["subject"]
    device = metadata["device"]
    target_fec = intent["selector"]["fecs"][0]
    pre_execution = compact_policy_execution(
        baseline[device], subject, target_fec
    )
    preserved_target_attributes: dict[str, Any] = {}
    for row in visible_pre:
        if row.subject == subject and row.fec == target_fec:
            preserved_target_attributes = dict(row.attributes)
            preserved_target_attributes.pop(
                intent["changes"][0]["dimension"], None
            )
            break
    payload.update({
        "pre_change_target_execution_summary": pre_execution,
        "target_attributes_to_preserve": preserved_target_attributes,
        "coverage_provenance": {
            "candidate_patch_used": False,
            "backend": "coverage_directed_active_policy_classes",
            "active_witness_count": len(active_pre),
        },
    })
    trace: list[dict[str, Any]] = []
    try:
        for attempt_index in range(1, 9):
            request = dict(payload)
            request["repair_attempt"] = attempt_index
            if trace:
                request["verification_counterexample"] = impact_feedback(
                    trace[-1]["evaluation"], full_pre, baseline, device, subject
                )
                signatures = [
                    v84.semantic_signature(row["evaluation"]) for row in trace
                ]
                request["prior_failed_semantic_signatures"] = signatures
                request["repeated_latest_outcome_count"] = signatures.count(signatures[-1])
                request["controller_instruction"] = (
                    "Start from the immutable baseline, not the rejected text. "
                    "Produce a semantically different complete candidate. Use the "
                    "ordered policy context to simulate both the target FEC and every "
                    "listed collateral FEC before output. In a first-match policy, a "
                    "new target term placed after the target's existing matched terminal "
                    "term is unreachable; it must execute before that terminal match. "
                    "If an earlier target-specific term intercepts the route, preserve "
                    "every pre-change non-target attribute of the target (for example "
                    "communities, metric, or AS-path) in addition to the requested delta. "
                    "Every match object referenced by a new term must also be "
                    "defined by the same complete candidate and must positively match "
                    "the target in the candidate execution evidence. A collateral FEC "
                    "must not reach a target-only set action: if an "
                    "existing match scope covers both, choose or construct a target-"
                    "exclusive scope while preserving the original path for the "
                    "collateral FEC. The edit interface can "
                    "insert new configuration by replacing any exact unique baseline "
                    "anchor; a new match object alone is incomplete unless the bound "
                    "policy actually references it. Choose all object names, sequence "
                    "positions, and commands yourself."
                )
            raw = model.complete([
                {"role": "system", "content": prompts.PATHDELTA_SYSTEM},
                {"role": "user", "content": json.dumps(request, sort_keys=True)},
            ])
            try:
                edits, parsed = parse_edit_response_checked(
                    raw, tuple(baseline)
                )
                evaluation = v83.evaluate(
                    baseline, edits, visible_pre, full_pre, visible_env, full_env
                )
                candidate = {"raw": raw, "parsed": parsed, "evaluation": evaluation}
            except Exception as exc:
                candidate = {
                    "raw": raw,
                    "error": f"{type(exc).__name__}: {exc}",
                    "evaluation": failed_evaluation(exc),
                }
            candidate["attempt"] = attempt_index
            trace.append(candidate)
            if candidate["evaluation"]["contract_pass"].get("full_envelope", False):
                return finalize_arm(
                    model, trace, accepted=True,
                    acceptance_basis="registered coverage-directed Full Envelope",
                    extra={
                        "active_witness_count": len(active_pre),
                        "candidate_patch_used_to_construct_witnesses": False,
                    },
                )
    except Exception as exc:
        trace.append({"stage": "pipeline_error", "error": f"{type(exc).__name__}: {exc}"})
    return finalize_arm(
        model, trace, accepted=False,
        acceptance_basis="registered coverage-directed Full Envelope",
        extra={
            "active_witness_count": len(active_pre),
            "candidate_patch_used_to_construct_witnesses": False,
        },
    )


RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "llm_netcfg_adapted": run_llm_netcfg,
    "inta_adapted": run_inta,
    "cosynth_vpp_adapted": run_cosynth,
    "cornetto_agentic_adapted": run_cornetto,
    "pathdelta_fullr": run_pathdelta,
}
