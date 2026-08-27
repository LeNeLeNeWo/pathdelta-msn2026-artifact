#!/usr/bin/env python3
"""Run a one-scenario live smoke over all RQ2 method implementations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.msn2026_v8_day2_agent.agent_benchmark import EditingMethodRunner, METHODS
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord
from experiments.msn2026_v8_day2_agent.llm_client_v2 import InstrumentedDeepSeekClient


def run(benchmark_root: Path, output_root: Path, scenario_id: str, methods: list[str]) -> dict:
    scenario = benchmark_root / "scenarios" / scenario_id
    intent = json.loads((scenario / "intent.json").read_text(encoding="utf-8"))
    baseline = {path.stem: path.read_text(encoding="utf-8") for path in (scenario / "baseline").glob("*.conf")}
    pre = [BehaviorRecord(**row) for row in json.loads((scenario / "pre_observations.json").read_text(encoding="utf-8"))]
    results = {}
    for method_id in methods:
        client = InstrumentedDeepSeekClient(timeout_s=120, max_retries=2)
        result = EditingMethodRunner(
            METHODS[method_id], client, max_attempts=2, total_token_budget=14000, max_completion_tokens=6000
        ).run_case(intent, baseline, pre, output_root / method_id)
        method_root = output_root / method_id
        method_root.mkdir(parents=True, exist_ok=True)
        (method_root / "trace.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        results[method_id] = {
            "stop_reason": result["stop_reason"],
            "attempts": result["attempts"],
            "logical_llm_calls": result["llm_metrics"]["logical_llm_calls"],
            "backend_attempts": result["llm_metrics"]["backend_attempts"],
            "retry_count": result["llm_metrics"]["retry_count"],
            "tokens": result["llm_metrics"]["token_usage"],
            "full_envelope_compliance": bool(result["final_evaluation"] and result["final_evaluation"]["contract_pass"]["full_envelope"]),
        }
    summary = {"scope": "one-scenario-live-smoke-not-paper-result", "scenario_id": scenario_id, "methods": results}
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-root", type=Path, default=Path("data/msn2026_v8_envelope_benchmark"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v8_agent_methods_smoke_v2"))
    parser.add_argument("--scenario", default="bf_shared_rm")
    parser.add_argument("--methods", nargs="+", default=list(METHODS))
    args = parser.parse_args()
    print(json.dumps(run(args.benchmark_root, args.output_root, args.scenario, args.methods), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
