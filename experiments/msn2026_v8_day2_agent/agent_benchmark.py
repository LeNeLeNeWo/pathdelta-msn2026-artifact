"""Unified LLM configuration-editing methods for PathDelta-Agent RQ2.

Every method asks the same configured LLM to submit exact baseline-relative
edits. Methods differ only in supplied context and acceptance/counterexample
contract; no method has an expected patch or deterministic renderer.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from experiments.msn2026_v8_day2_agent.change_envelope import (
    SearchReplaceEdit,
    apply_search_replace_edits,
    evaluate_matrix,
    parse_config,
)
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    ChangeEnvelopeV2,
    build_frr_dependency_graph,
    derive_change_envelope_v2,
)
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient
from experiments.msn2026_v8_day2_agent.semantic_metrics import build_blast_radius_report
from experiments.msn2026_v8_day2_agent.counterexample_feedback import build_counterexample_feedback


SYSTEM_PROMPT = """You are editing an existing brownfield FRR network configuration.
You own the implementation: choose any exact configuration delta that satisfies the intent and preserves unrelated behavior.
No hidden component will construct or repair the patch for you.
Return JSON only with this shape:
{"edits":[{"device":"device key","old_text":"exact unique baseline text","new_text":"replacement"}],"summary":"brief"}
Every submission is applied to the immutable original baseline. Do not ask questions and do not return markdown."""


@dataclass(frozen=True)
class MethodSpec:
    method_id: str
    name: str
    context_mode: str
    acceptance_contract: str
    one_shot: bool
    envelope_source: str


METHODS = {
    "A": MethodSpec("A", "Direct LLM Editing", "baseline", "submission", True, "none"),
    "B": MethodSpec("B", "Context/RAG LLM", "retrieved_dependencies", "submission", True, "none"),
    "C": MethodSpec("C", "Iterative Repair Agent", "retrieved_dependencies", "syntax_goal", False, "none"),
    "D": MethodSpec("D", "Write-Scope Agent", "baseline", "write_scope", False, "none"),
    "E": MethodSpec("E", "Goal-Verified Agent", "baseline", "syntax_goal", False, "none"),
    "F": MethodSpec("F", "PathDelta-Agent", "retrieved_dependencies", "full_envelope", False, "inferred"),
    "G": MethodSpec("G", "Oracle-Envelope Agent", "retrieved_dependencies", "full_envelope", False, "oracle"),
}


def parse_edit_response(raw: str) -> Tuple[List[SearchReplaceEdit], Dict[str, Any]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    if not isinstance(payload.get("edits"), list) or not payload["edits"]:
        raise ValueError("response requires a non-empty edits array")
    edits = [
        SearchReplaceEdit(str(item["device"]), str(item["old_text"]), str(item["new_text"]))
        for item in payload["edits"]
    ]
    return edits, payload


def _dependency_context(configs: Mapping[str, str]) -> Dict[str, Any]:
    graph = build_frr_dependency_graph(configs)
    return {
        "nodes": [asdict(node) for _, node in sorted(graph.nodes.items())],
        "edges": [
            {"source": source, "target": target}
            for source, targets in sorted(graph.edges.items())
            for target in sorted(targets)
        ],
        "note": "Read-only pre-state facts; no object is recommended for editing.",
    }


def _post_records(configs: Mapping[str, str], pre: Sequence[BehaviorRecord]) -> List[BehaviorRecord]:
    output: List[BehaviorRecord] = []
    by_device: Dict[str, List[BehaviorRecord]] = {}
    for record in pre:
        by_device.setdefault(record.device, []).append(record)
    for device, records in by_device.items():
        model = parse_config(configs[device])
        matrix = evaluate_matrix(model, {row.subject for row in records}, {row.fec for row in records})
        for record in records:
            route = matrix[f"{record.subject}|{record.fec}"]
            output.append(
                BehaviorRecord(
                    record.behavior_id,
                    record.device,
                    record.subject,
                    record.fec,
                    {**record.attributes, **route},
                    "rq2_lightweight_poststate_evaluator",
                )
            )
    return output


def _syntax_check(configs: Mapping[str, str], attempt_root: Path, container: str) -> Dict[str, Any]:
    attempt_root.mkdir(parents=True, exist_ok=True)
    evidence = []
    for device, text in configs.items():
        path = attempt_root / f"{device}.conf"
        path.write_text(text, encoding="utf-8")
        # Docker is hosted in WSL. The project is mounted read-only at /workspace.
        project_root = Path(__file__).resolve().parents[2]
        relative = path.resolve().relative_to(project_root).as_posix()
        prefix = ["wsl.exe"] if __import__("os").name == "nt" else []
        command = prefix + ["docker", "exec", container, "vtysh", "-C", "-f", f"/workspace/{relative}"]
        try:
            run = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            evidence.append({"device": device, "returncode": run.returncode, "stdout": run.stdout[-1000:], "stderr": run.stderr[-1000:]})
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"status": "N/A", "error": str(exc), "files": evidence}
    return {"status": "PASS" if all(row["returncode"] == 0 for row in evidence) else "FAIL", "files": evidence}


def _oracle_envelope(
    intent: Mapping[str, Any],
    configs: Mapping[str, str],
    pre_records: Sequence[BehaviorRecord],
) -> ChangeEnvelopeV2:
    # In the development corpus the human/oracle selector and desired relation
    # are stored in the ground-truth intent. This path intentionally bypasses
    # literal grounding but still returns no patch or strategy.
    oracle_intent = dict(intent)
    oracle_intent["raw_text"] = "oracle-normalized selector and relation"
    return derive_change_envelope_v2(
        oracle_intent,
        configs,
        pre_records,
        build_frr_dependency_graph(configs),
        behavior_universe_provenance={"backend": "oracle-development-observations", "complete": False, "uncovered_reason": "finite benchmark FEC set"},
    )


def evaluate_submission(
    baseline: Mapping[str, str],
    edits: Sequence[SearchReplaceEdit],
    pre_records: Sequence[BehaviorRecord],
    envelope: ChangeEnvelopeV2,
    attempt_root: Path,
    frr_container: str,
) -> Dict[str, Any]:
    try:
        candidate = apply_search_replace_edits(baseline, edits)
    except ValueError as exc:
        return {
            "candidate_applied": False,
            "syntax": {"status": "N/A"},
            "report": None,
            "transaction_error": str(exc),
            "contract_pass": {"submission": False, "syntax_goal": False, "write_scope": False, "full_envelope": False},
        }
    syntax = _syntax_check(candidate, attempt_root, frr_container)
    report = build_blast_radius_report(
        baseline,
        candidate,
        pre_records,
        _post_records(candidate, pre_records),
        envelope,
        build_frr_dependency_graph(baseline),
        build_frr_dependency_graph(candidate),
    ).to_dict()
    compliance = report["compliance"]
    budget = envelope.footprint_budget
    structural, textual = report["structural"], report["textual"]
    write_scope = bool(
        set(structural["devices_touched"]) <= set(budget.allowed_devices)
        and len(structural["bindings_changed"]) <= budget.max_bindings_changed
        and len(structural["new_objects_created"]) <= budget.max_new_objects
        and textual["lines_touched"] <= budget.max_changed_lines
    )
    syntax_pass = syntax["status"] == "PASS"
    goal_pass = syntax_pass and compliance["goal_success"]
    return {
        "candidate_applied": True,
        "candidate_configs": candidate,
        "syntax": syntax,
        "report": report,
        "transaction_error": None,
        "contract_pass": {
            "submission": True,
            "syntax_goal": goal_pass,
            "write_scope": goal_pass and write_scope,
            "full_envelope": goal_pass and compliance["envelope_compliance"],
        },
    }


def _feedback(contract: str, evaluation: Mapping[str, Any]) -> Dict[str, Any]:
    return build_counterexample_feedback(contract, evaluation)


class EditingMethodRunner:
    def __init__(
        self,
        spec: MethodSpec,
        client: InstrumentedDeepSeekClient,
        *,
        max_attempts: int = 3,
        total_token_budget: int = 12000,
        temperature: float = 0.0,
        max_completion_tokens: int = 6000,
        frr_container: str = "pathdelta-msn2026-frr-syntax",
    ) -> None:
        self.spec = spec
        self.client = client
        self.max_attempts = max_attempts
        self.total_token_budget = total_token_budget
        self.temperature = temperature
        self.max_completion_tokens = max_completion_tokens
        self.frr_container = frr_container

    def run_case(
        self,
        intent: Mapping[str, Any],
        baseline: Mapping[str, str],
        pre_records: Sequence[BehaviorRecord],
        output_root: Path,
    ) -> Dict[str, Any]:
        inferred = derive_change_envelope_v2(
            intent,
            baseline,
            pre_records,
            build_frr_dependency_graph(baseline),
            behavior_universe_provenance={"backend": "rq2-development-observations", "complete": False, "uncovered_reason": "finite pilot FEC set"},
        )
        envelope = _oracle_envelope(intent, baseline, pre_records) if self.spec.envelope_source == "oracle" else inferred
        initial = {
            "intent": intent["raw_text"],
            "baseline_configs": baseline,
            "eligible_information": "all supplied data are immutable pre-state facts",
        }
        if self.spec.context_mode == "retrieved_dependencies":
            initial["retrieved_context"] = _dependency_context(baseline)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(initial, sort_keys=True)}]
        trace: List[Dict[str, Any]] = []
        final_evaluation: Optional[Dict[str, Any]] = None
        stop_reason = "attempt_budget_exhausted"
        attempts_allowed = 1 if self.spec.one_shot else self.max_attempts
        for attempt in range(1, attempts_allowed + 1):
            if self.client.metrics.token_usage.total >= self.total_token_budget:
                stop_reason = "token_budget_exhausted"
                break
            raw: Optional[str] = None
            try:
                raw = self.client.complete(messages, temperature=self.temperature, max_completion_tokens=self.max_completion_tokens)
                edits, parsed = parse_edit_response(raw)
                evaluation = evaluate_submission(
                    baseline,
                    edits,
                    pre_records,
                    envelope,
                    output_root / f"attempt_{attempt}" / "candidate_configs",
                    self.frr_container,
                )
                feedback = _feedback(self.spec.acceptance_contract, evaluation)
                trace.append(
                    {
                        "attempt": attempt,
                        "raw_llm_response": raw,
                        "parsed_submission": parsed,
                        "evaluation": evaluation,
                        "feedback": feedback,
                    }
                )
                final_evaluation = evaluation
                if self.spec.acceptance_contract == "submission" or evaluation["contract_pass"][self.spec.acceptance_contract]:
                    stop_reason = "candidate_submitted" if self.spec.acceptance_contract == "submission" else "contract_pass"
                    break
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": json.dumps({"verification_counterexample": feedback, "instruction": "Revise your own baseline-relative edits."}, sort_keys=True)},
                ]
            except Exception as exc:
                trace.append(
                    {
                        "attempt": attempt,
                        "model_or_parse_error": f"{type(exc).__name__}: {exc}",
                        "raw_llm_response": raw,
                    }
                )
                messages.append({"role": "user", "content": json.dumps({"error": str(exc), "instruction": "Return valid edit JSON."})})
        successful_attempts = [
            row
            for row in trace
            if row.get("evaluation")
            and row["evaluation"]["contract_pass"].get(self.spec.acceptance_contract, False)
        ]
        counterexample_types = [
            item["type"]
            for row in trace
            for item in (row.get("feedback") or {}).get("counterexamples", [])
        ]
        collateral_sets = [
            set(row["evaluation"]["report"]["semantic"]["non_target_attribute_changes"])
            for row in trace
            if row.get("evaluation") and row["evaluation"].get("report")
        ]
        new_collateral = any(
            bool(current - previous)
            for previous, current in zip(collateral_sets, collateral_sets[1:])
        )
        return {
            "method": asdict(self.spec),
            "stop_reason": stop_reason,
            "attempts": len(trace),
            "budgets": {
                "max_attempts": self.max_attempts,
                "one_shot_design": self.spec.one_shot,
                "total_token_budget": self.total_token_budget,
                "temperature": self.temperature,
                "max_completion_tokens_per_call": self.max_completion_tokens,
            },
            "llm_metrics": self.client.metrics.to_dict(),
            "first_attempt_contract_success": bool(
                trace
                and trace[0].get("evaluation")
                and trace[0]["evaluation"]["contract_pass"].get(self.spec.acceptance_contract, False)
            ),
            "counterexample_types": counterexample_types,
            "revision_success": bool(successful_attempts and successful_attempts[0]["attempt"] > 1),
            "revision_introduced_new_collateral": new_collateral,
            "final_evaluation": final_evaluation,
            "trace": trace,
            "trusted_patch_generator_used": False,
        }
