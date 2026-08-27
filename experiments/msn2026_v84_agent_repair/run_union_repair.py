#!/usr/bin/env python3
"""Route independent Batfish counterexamples back to the frozen LLM editor."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_agent128_repair_v2 as v84  # noqa: E402
from run_batfish_agent_final import collect_combined_agent128  # noqa: E402
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient  # noqa: E402


UNION_SYSTEM = v84.SYSTEM_V2 + """

An independent route-policy verifier may report target output attributes that
changed only because a new rule bypassed legacy actions. The requested target
dimension is the only authorized target change. Preserve every other target
attribute at its reference value, including metric and origin, as well as all
non-target behavior. This is an impact constraint, not a prescribed edit: you
still choose the object, ordering, policy-control flow, and exact commands.
"""


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def route_values(text: str) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, pattern, cast in (
        ("localPreference", r"localPreference=(\d+)", int),
        ("metric", r"metric=(\d+)", int),
        ("originType", r"originType='?([A-Za-z]+)'?", str),
    ):
        match = re.search(pattern, text)
        if match:
            output[key] = cast(match.group(1))
    communities = re.search(r"communities=\[([^\]]*)\]", text)
    if communities:
        output["communities"] = sorted(re.findall(r"([0-9]+:[0-9]+)", communities.group(1)))
    return output


def counterexample(audit: dict) -> dict:
    target = audit["target_prefix"]
    target_rows = [
        row for row in audit["symbolic_difference"].get("rows", [])
        if target in str(row.get("Input_Route", ""))
    ]
    target_examples = []
    for row in target_rows[:3]:
        target_examples.append({
            "fec": target,
            "reference_output": route_values(str(row.get("Reference_Output_Route", ""))),
            "candidate_output": route_values(str(row.get("Snapshot_Output_Route", ""))),
            "unauthorized_changed_fields": audit["unauthorized_target_fields"],
            "required": "preserve reference_output for every field except the requested dimension",
        })
    return {
        "backend": "Batfish compareRoutePolicies",
        "verdict": audit["symbolic_frame_verdict"],
        "target_examples": target_examples,
        "non_target_changed_fecs": [fec for fec in audit["symbolic_difference_networks"] if fec != target],
        "patch_disclosed": False,
        "recommended_object": None,
        "recommended_commands": None,
    }


def run_one(case: dict, audit: dict, data_root: Path, max_submissions: int, max_tokens: int,
            temperature: float, thinking_mode: str) -> dict:
    sid = case["scenario_id"]
    scenario = data_root / "public/scenarios" / sid
    metadata, intent = v84.v83.load(scenario / "metadata.json"), v84.v83.load(scenario / "intent.json")
    device = metadata["device"]
    baseline = {device: (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")}
    passive = v84.v83.records(v84.v83.load(scenario / "passive_observations.json"))
    active = v84.v83.records(v84.v83.load(data_root / "sealed/oracles" / sid / "active_observations.json"))
    full_pre = passive + active
    passive_env = v84.v83.make_envelope(intent, baseline, passive, "operator_visible_passive_tests")
    full_env = v84.v83.make_envelope(intent, baseline, full_pre, "coverage_directed_active_policy_classes")
    subject = intent["selector"]["subjects"][0]
    prompt = {
        "intent": intent["raw_text"],
        "structured_target": {
            "device": device, "subject": subject, "prefix": intent["selector"]["fecs"][0],
            "dimension": intent["changes"][0]["dimension"], "required_value": intent["changes"][0]["desired"],
        },
        "vendor": metadata["vendor"],
        "baseline_config": baseline[device],
        "read_only_policy_context": v84.policy_context(baseline, device, subject),
        "pre_change_target_trace": v84.trace_subject(baseline[device], subject, intent["selector"]["fecs"][0]),
        "independent_verifier_counterexample": counterexample(audit),
        "controller_instruction": (
            "Start from the immutable baseline. Produce a baseline-relative patch that satisfies the target, "
            "the Full Envelope, and the independent attribute-preservation counterexample."
        ),
    }
    client = InstrumentedDeepSeekClient(timeout_s=240, max_retries=2, thinking_mode=thinking_mode)
    trace = []
    for attempt_index in range(1, max_submissions + 1):
        attempt_prompt = dict(prompt)
        attempt_prompt["repair_attempt"] = attempt_index
        if trace:
            attempt_prompt["registered_counterexample"] = v84.detailed_feedback(
                "full_envelope", trace[-1]["evaluation"], full_pre, baseline, subject
            )
        messages = [{"role": "system", "content": UNION_SYSTEM},
                    {"role": "user", "content": json.dumps(attempt_prompt, sort_keys=True)}]
        attempt = v84.call_v2(client, messages, baseline, passive, full_pre, passive_env, full_env, max_tokens, temperature)
        trace.append(attempt)
        if attempt["evaluation"]["contract_pass"].get("full_envelope"):
            break
    accepted_registered = bool(trace[-1]["evaluation"]["contract_pass"].get("full_envelope"))
    return {
        "schema_version": "msn2026-v84-union-repair-case-1.0",
        "case_id": case["case_id"], "scenario_id": sid, "mode": case["mode"],
        "vendor": metadata["vendor"], "family": metadata["family"],
        "batfish_input_verdict": audit["symbolic_frame_verdict"],
        "counterexample": counterexample(audit),
        "arm": {
            "accepted_registered": accepted_registered,
            "submissions": len(trace), "trace": trace,
            "llm_usage": v84.v83.usage_since(client, 0),
        },
        "llm_metrics": client.metrics.to_dict(),
        "claim_status": "adaptive heterogeneous-verifier repair; requires independent Batfish re-audit",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=Path("results/msn2026_v84_agent_repair/batfish_agent128/summary.json"))
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v84_agent_repair/union_repair_agent128"))
    parser.add_argument("--max-submissions", type=int, default=3)
    parser.add_argument("--max-completion-tokens", type=int, default=8000)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--thinking-mode", choices=("enabled", "disabled"), default="enabled")
    args = parser.parse_args()
    audit_rows = {f"{row['scenario_id']}::{row['mode']}": row for row in load(args.audit)["rows"]}
    cases = {row["case_id"]: row for row in collect_combined_agent128()}
    selected = [row for row in audit_rows.values() if row["symbolic_frame_verdict"] == "FAIL"]
    for ordinal, audit in enumerate(selected, 1):
        case_id = f"{audit['scenario_id']}::{audit['mode']}"
        target = args.output_root / "cases" / f"{audit['scenario_id']}__{audit['mode']}.json"
        started = time.perf_counter()
        result = run_one(cases[case_id], audit, args.data_root, args.max_submissions,
                         args.max_completion_tokens, args.temperature, args.thinking_mode)
        v84.v83.write(target, result)
        print(f"union-repair {ordinal}/{len(selected)} {case_id} registered={result['arm']['accepted_registered']} "
              f"calls={result['llm_metrics']['logical_llm_calls']} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    summary = {
        "schema_version": "msn2026-v84-union-repair-summary-1.0",
        "case_count": len(selected),
        "registered_pass": sum(load(path)["arm"]["accepted_registered"] for path in (args.output_root / "cases").glob("*.json")),
        "requires_batfish_reaudit": True,
    }
    v84.v83.write(args.output_root / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
