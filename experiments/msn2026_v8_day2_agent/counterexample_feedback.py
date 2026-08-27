"""Patch-free structured verifier counterexamples for LLM revision."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


FORBIDDEN_FEEDBACK_KEYS = {
    "correct_patch",
    "expected_patch",
    "new_text",
    "recommended_object",
    "required_strategy",
    "renderer_output",
}
FORBIDDEN_STRATEGY_WORDS = {"APPEND", "PREPEND", "REBIND", "LOCAL_FORK"}


def build_counterexample_feedback(contract: str, evaluation: Mapping[str, Any]) -> Dict[str, Any]:
    counterexamples: List[Dict[str, Any]] = []
    if evaluation.get("transaction_error"):
        counterexamples.append(
            {
                "type": "transaction",
                "backend": "edit_transaction",
                "obligation_ids": [],
                "observed": {"error": str(evaluation["transaction_error"])},
            }
        )
    else:
        syntax = evaluation.get("syntax") or {}
        if syntax.get("status") != "PASS":
            counterexamples.append(
                {
                    "type": "syntax",
                    "backend": "frr_vtysh",
                    "obligation_ids": [],
                    "observed": {"status": syntax.get("status"), "diagnostics": syntax.get("files", [])},
                }
            )
        report = evaluation.get("report") or {}
        compliance = report.get("compliance") or {}
        semantic = report.get("semantic") or {}
        if not compliance.get("goal_success", False):
            counterexamples.append(
                {
                    "type": "target_unmet",
                    "backend": "target_relation",
                    "obligation_ids": list(compliance.get("target_failures", [])),
                    "observed": {"status": "relation_not_satisfied"},
                }
            )
        if contract == "write_scope" and not (evaluation.get("contract_pass") or {}).get("write_scope", False):
            counterexamples.append(
                {
                    "type": "footprint",
                    "backend": "static_write_scope",
                    "obligation_ids": [],
                    "observed": {
                        "structural": report.get("structural", {}),
                        "textual": report.get("textual", {}),
                    },
                }
            )
        if contract == "full_envelope":
            if not compliance.get("semantic_frame_preserved", False):
                changed = list(semantic.get("non_target_attribute_changes", []))
                relation_kind = "session" if any(item.endswith("::session") for item in changed) else "path_relation" if any(item.endswith("::path") for item in changed) else "semantic_frame"
                counterexamples.append(
                    {
                        "type": relation_kind,
                        "backend": "behavior_relation",
                        "obligation_ids": list(compliance.get("frame_failures", [])),
                        "observed": {"changed_behavior_atoms": changed, "missing_behaviors": semantic.get("missing_post_behaviors", [])},
                    }
                )
            if not compliance.get("dependency_frame_preserved", False):
                counterexamples.append(
                    {
                        "type": "protected_dependency",
                        "backend": "dependency_graph_diff",
                        "obligation_ids": [],
                        "observed": {"changed_protected_nodes": list(semantic.get("protected_dependency_violations", []))},
                    }
                )
            if not compliance.get("hard_footprint_preserved", False):
                counterexamples.append(
                    {
                        "type": "footprint",
                        "backend": "envelope_footprint",
                        "obligation_ids": list(compliance.get("footprint_failures", [])),
                        "observed": {"structural": report.get("structural", {}), "textual": report.get("textual", {})},
                    }
                )
    payload = {
        "schema_version": "1.0.0-dev",
        "contract": contract,
        "verdict": "PASS" if not counterexamples else "FAIL",
        "counterexamples": counterexamples,
        "patch_disclosed": False,
        "strategy_disclosed": False,
    }
    assert_feedback_is_patch_free(payload)
    return payload


def assert_feedback_is_patch_free(payload: Mapping[str, Any]) -> None:
    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            forbidden = FORBIDDEN_FEEDBACK_KEYS & {str(key).lower() for key in value}
            if forbidden:
                raise ValueError(f"feedback contains forbidden patch keys: {sorted(forbidden)}")
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            upper = value.upper().replace("-", "_")
            if any(word in upper for word in FORBIDDEN_STRATEGY_WORDS):
                raise ValueError("feedback contains a forbidden edit-strategy token")

    visit(payload)

