#!/usr/bin/env python3
"""Run the frozen v8.5 same-task external-method comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.change_envelope import SearchReplaceEdit
from experiments.msn2026_v83_external import run_agent128 as v83
from experiments.msn2026_v85_external_baselines.method_adapters import (
    METHODS,
    RUNNERS,
    BudgetedModel,
)

HERE = Path(__file__).resolve().parent
DEFAULT_DATA = Path("data/msn2026_v85_external_baselines")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def final_candidate_evaluation(arm: Mapping[str, Any]) -> Mapping[str, Any]:
    for row in reversed(arm.get("trace", [])):
        evaluation = row.get("evaluation")
        if evaluation is not None:
            return evaluation
    return {
        "candidate_applied": False,
        "transaction_error": "method produced no candidate",
        "syntax": {"status": "N/A"},
        "reports": {},
        "contract_pass": {},
    }


def full_posthoc_evaluation(
    initial: Mapping[str, Any],
    baseline: Mapping[str, str],
    passive: Sequence[Any],
    full_pre: Sequence[Any],
    passive_env: Any,
    full_env: Any,
) -> dict[str, Any]:
    candidate = initial.get("candidate_configs")
    if not candidate:
        return dict(initial)
    edits = [
        SearchReplaceEdit(
            device=device,
            old_text=baseline[device],
            new_text=text,
        )
        for device, text in candidate.items()
        if text != baseline[device]
    ]
    return v83.evaluate(
        baseline, edits, passive, full_pre, passive_env, full_env
    )


def footprint(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    report = (evaluation.get("reports") or {}).get("full", {})
    structural = report.get("structural", {})
    textual = report.get("textual", {})
    return {
        "lines_touched": textual.get("lines_touched"),
        "devices_touched": len(structural.get("devices_touched", [])),
        "policy_objects_touched": len(structural.get("policy_objects_touched", [])),
        "new_objects_created": len(structural.get("new_objects_created", [])),
        "bindings_changed": len(structural.get("bindings_changed", [])),
    }


def score_arms(
    arms: dict[str, dict[str, Any]],
    baseline: Mapping[str, str],
    passive: Sequence[Any],
    full_pre: Sequence[Any],
    passive_env: Any,
    full_env: Any,
    complete_rows: Sequence[Mapping[str, Any]],
    intent: Mapping[str, Any],
) -> None:
    """Open sealed scoring observations only after every method terminates."""
    for arm in arms.values():
        final_visible = final_candidate_evaluation(arm)
        posthoc = full_posthoc_evaluation(
            final_visible, baseline, passive, full_pre, passive_env, full_env
        )
        oracle = v83.oracle(posthoc, complete_rows, intent)
        accepted = bool(arm["accepted"])
        arm["posthoc_evaluation"] = posthoc
        arm["oracle"] = oracle
        arm["verified_completion"] = bool(accepted and oracle["safe"])
        arm["unsafe_release"] = bool(accepted and not oracle["safe"])
        arm["collateral_release"] = bool(
            accepted and oracle["goal"] and oracle["collateral_atoms"]
        )
        arm["failed_intent_release"] = bool(accepted and not oracle["goal"])
        arm["final_goal_success"] = bool(oracle["goal"])
        arm["collateral_regression"] = bool(oracle["collateral_atoms"])
        arm["footprint"] = footprint(posthoc)


def run_case(
    case: Mapping[str, Any],
    data_root: Path,
    methods: Sequence[str],
    *,
    max_calls: int,
    max_tokens_per_call: int,
    max_tokens_per_case: int,
    temperature: float,
    thinking_mode: str,
) -> dict[str, Any]:
    sid = case["scenario_id"]
    scenario = data_root / "public/scenarios" / sid
    metadata = load(scenario / "metadata.json")
    intent = load(scenario / "intent.json")
    device = metadata["device"]
    baseline = {
        device: (
            scenario / "baseline/configs" / f"{device}.conf"
        ).read_text(encoding="utf-8")
    }
    passive = v83.records(load(scenario / "passive_observations.json"))
    passive_env = v83.make_envelope(
        intent, baseline, passive, "operator_visible_passive_tests"
    )

    # Candidate-independent active witnesses are available only to PathDelta.
    active = v83.records(
        load(data_root / "sealed/oracles" / sid / "active_observations.json")
    )
    full_pre = list(passive) + list(active)
    full_env = v83.make_envelope(
        intent, baseline, full_pre, "coverage_directed_active_policy_classes"
    )

    arms: dict[str, dict[str, Any]] = {}
    for method in methods:
        model = BudgetedModel(
            max_calls=max_calls,
            max_completion_tokens_per_call=max_tokens_per_call,
            max_completion_tokens_per_case=max_tokens_per_case,
            temperature=temperature,
            thinking_mode=thinking_mode,
        )
        started = time.perf_counter()
        try:
            if method == "pathdelta_fullr":
                arm = RUNNERS[method](
                    model, metadata, intent, baseline, passive, passive_env,
                    active, full_env,
                )
            else:
                arm = RUNNERS[method](
                    model, metadata, intent, baseline, passive, passive_env
                )
        except Exception as exc:
            arm = {
                "accepted": False,
                "acceptance_basis": "pipeline exception",
                "attempt_exhaustion": True,
                "trace": [{
                    "stage": "unhandled_pipeline_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }],
                "submissions": 0,
                "llm_metrics": model.metrics(),
            }
        arm["wall_time_ms"] = (time.perf_counter() - started) * 1000
        arms[method] = arm

    # No method is running beyond this line. It is now legal to open the oracle.
    complete_rows = load(
        data_root / "sealed/oracles" / sid / "complete_pre_observations.json"
    )
    score_arms(
        arms, baseline, passive, full_pre, passive_env, full_env,
        complete_rows, intent,
    )
    return {
        "schema_version": "msn2026-v85-external-baseline-case-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case["case_id"],
        "scenario_id": sid,
        "source_scenario": metadata["source_scenario"],
        "source_group": metadata["source_group"],
        "vendor": metadata["vendor"],
        "family": metadata["family"],
        "baseline_lines": len(baseline[device].splitlines()),
        "methods": list(methods),
        "information_boundary": {
            "same_visible_inputs": True,
            "sealed_oracle_opened_after_all_methods_terminated": True,
            "active_witnesses_visible_only_to_pathdelta": True,
            "candidate_patch_used_for_active_witnesses": False,
        },
        "arms": arms,
    }


def summarize(case_paths: Sequence[Path], output_root: Path) -> dict[str, Any]:
    rows = [load(path) for path in case_paths]
    methods = rows[0]["methods"] if rows else []
    summaries = []
    for method in methods:
        arms = [row["arms"][method] for row in rows]
        calls = [
            call
            for arm in arms
            for call in arm["llm_metrics"].get("calls", [])
        ]
        summaries.append({
            "method": method,
            "case_count": len(arms),
            "verified_completion": sum(arm["verified_completion"] for arm in arms),
            "unsafe_release": sum(arm["unsafe_release"] for arm in arms),
            "collateral_release": sum(arm["collateral_release"] for arm in arms),
            "failed_intent_release": sum(arm["failed_intent_release"] for arm in arms),
            "attempt_exhaustion": sum(arm["attempt_exhaustion"] for arm in arms),
            "final_goal_success": sum(arm["final_goal_success"] for arm in arms),
            "collateral_regression": sum(arm["collateral_regression"] for arm in arms),
            "logical_llm_calls": sum(
                arm["llm_metrics"]["logical_llm_calls"] for arm in arms
            ),
            "backend_attempts": sum(
                arm["llm_metrics"]["backend_attempts"] for arm in arms
            ),
            "retry_count": sum(
                arm["llm_metrics"]["retry_count"] for arm in arms
            ),
            "prompt_tokens": sum(
                arm["llm_metrics"]["token_usage"]["prompt"] for arm in arms
            ),
            "completion_tokens": sum(
                arm["llm_metrics"]["token_usage"]["completion"] for arm in arms
            ),
            "latency_ms": sum(
                arm["llm_metrics"]["latency_ms"] for arm in arms
            ),
            "wall_time_ms": sum(arm["wall_time_ms"] for arm in arms),
            "source_groups": dict(Counter(row["source_group"] for row in rows)),
            "families": dict(Counter(row["family"] for row in rows)),
        })
    summary = {
        "schema_version": "msn2026-v85-external-baseline-summary-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_case_count": len(rows),
        "methods": summaries,
        "adaptation_claim": (
            "method-faithful same-task adaptations; not original-task reproductions"
        ),
        "pilot_and_confirmatory_pooled": False,
    }
    write(output_root / "summary.json", summary)
    return summary


def manifest(
    *,
    stage: str,
    split_path: Path,
    output_root: Path,
    methods: Sequence[str],
    data_root: Path,
    max_calls: int,
    max_tokens_per_call: int,
    max_tokens_per_case: int,
    temperature: float,
    thinking_mode: str,
) -> dict[str, Any]:
    split = load(split_path)
    value = {
        "schema_version": "msn2026-v85-run-manifest-1.0",
        "run_id": f"v85-{stage}-{sha256(split_path)[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "frozen",
        "stage": stage,
        "methods": list(methods),
        "dataset": {
            "root": data_root.as_posix(),
            "split_path": split_path.as_posix(),
            "split_sha256": sha256(split_path),
            "case_count": len(split["cases"]),
            "source_manifest_sha256": sha256(data_root / "source_manifest.json"),
            "dataset_manifest_sha256": sha256(data_root / "dataset_manifest.json"),
        },
        "protocol": {
            "path": (HERE / "preregistered_protocol.md").as_posix(),
            "sha256": sha256(HERE / "preregistered_protocol.md"),
            "adaptation_claim": (
                "method-faithful same-task adaptations; not original-task reproductions"
            ),
        },
        "model": {
            "backend_env": "DEEPSEEK_BASE_URL",
            "model_env": "DEEPSEEK_MODEL",
            "configured_model": os.getenv("DEEPSEEK_MODEL", ""),
            "temperature": temperature,
            "thinking_mode": thinking_mode,
            "seed_support": "backend_not_exposed",
        },
        "budgets": {
            "max_logical_llm_calls": max_calls,
            "max_completion_tokens_per_call": max_tokens_per_call,
            "max_completion_tokens_per_case": max_tokens_per_case,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "api_key_recorded": False,
            "required_environment_present": {
                key: bool(os.getenv(key))
                for key in (
                    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"
                )
            },
        },
        "integrity": {
            "runner_sha256": sha256(Path(__file__)),
            "adapters_sha256": sha256(HERE / "method_adapters.py"),
            "prompts_sha256": sha256(HERE / "method_prompts.py"),
            "third_party_cornetto_commit": "21641495fb6485c1d6d61d44597a58d87ed29de2",
            "third_party_llm_netcfg_commit": "2673a80efef576ba996f07d3b915f29ed88c880a",
        },
    }
    write(output_root / "run_manifest.json", value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("pilot", "confirmatory", "sensitivity"), default="pilot")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--method", action="append", choices=METHODS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--max-completion-tokens-per-call", type=int, default=5000)
    parser.add_argument("--max-completion-tokens-per-case", type=int, default=40000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--thinking-mode", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()

    if args.split is None:
        name = "pilot8" if args.stage == "pilot" else "confirmatory40"
        args.split = args.data_root / "splits" / f"{name}.json"
    if args.output_root is None:
        args.output_root = Path("results/msn2026_v85_external_baselines") / args.stage
    methods = tuple(args.method or METHODS)

    if args.stage == "confirmatory":
        frozen = load(HERE / "confirmatory_freeze.json")
        checks = {
            "split": (sha256(args.split), frozen["split_sha256"]),
            "runner": (sha256(Path(__file__)), frozen["runner_sha256"]),
            "adapters": (
                sha256(HERE / "method_adapters.py"), frozen["adapters_sha256"]
            ),
            "prompts": (
                sha256(HERE / "method_prompts.py"), frozen["prompts_sha256"]
            ),
            "protocol": (
                sha256(HERE / "preregistered_protocol.md"),
                frozen["protocol_sha256"],
            ),
            "source_manifest": (
                sha256(args.data_root / "source_manifest.json"),
                frozen["source_manifest_sha256"],
            ),
            "dataset_manifest": (
                sha256(args.data_root / "dataset_manifest.json"),
                frozen["dataset_manifest_sha256"],
            ),
        }
        mismatches = {
            name: values for name, values in checks.items() if values[0] != values[1]
        }
        if mismatches:
            raise RuntimeError(f"confirmatory freeze mismatch: {mismatches}")

    missing_env = [
        key for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
        if not os.getenv(key)
    ]
    if missing_env:
        raise RuntimeError(
            "missing required environment variables: " + ", ".join(missing_env)
        )

    run_manifest = manifest(
        stage=args.stage,
        split_path=args.split,
        output_root=args.output_root,
        methods=methods,
        data_root=args.data_root,
        max_calls=args.max_calls,
        max_tokens_per_call=args.max_completion_tokens_per_call,
        max_tokens_per_case=args.max_completion_tokens_per_case,
        temperature=args.temperature,
        thinking_mode=args.thinking_mode,
    )
    run_manifest["status"] = "running"
    write(args.output_root / "run_manifest.json", run_manifest)

    cases = load(args.split)["cases"]
    selected = cases[
        args.offset:(args.offset + args.limit) if args.limit else None
    ]
    for ordinal, case in enumerate(selected, 1):
        target = args.output_root / "cases" / f"{case['scenario_id']}.json"
        if target.exists() and not args.rerun:
            print(f"{ordinal}/{len(selected)} {case['case_id']} SKIP", flush=True)
            continue
        started = time.perf_counter()
        row = run_case(
            case, args.data_root, methods,
            max_calls=args.max_calls,
            max_tokens_per_call=args.max_completion_tokens_per_call,
            max_tokens_per_case=args.max_completion_tokens_per_case,
            temperature=args.temperature,
            thinking_mode=args.thinking_mode,
        )
        write(target, row)
        compact = {
            method: {
                "vc": arm["verified_completion"],
                "unsafe": arm["unsafe_release"],
                "calls": arm["llm_metrics"]["logical_llm_calls"],
            }
            for method, arm in row["arms"].items()
        }
        print(
            f"{ordinal}/{len(selected)} {case['case_id']} "
            f"{json.dumps(compact, sort_keys=True)} "
            f"elapsed={time.perf_counter()-started:.1f}s",
            flush=True,
        )

    summary = summarize(
        sorted((args.output_root / "cases").glob("*.json")),
        args.output_root,
    )
    run_manifest["status"] = "complete"
    run_manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    run_manifest["summary_sha256"] = sha256(args.output_root / "summary.json")
    write(args.output_root / "run_manifest.json", run_manifest)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
