#!/usr/bin/env python3
"""Run the frozen impact-aware controller on the disjoint 32-case holdout."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_agent128_repair_v2 as v84  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v83_external"))
    parser.add_argument("--subset", type=Path, default=Path("data/msn2026_v84_agent_repair/agent_holdout32.json"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v84_agent_repair/holdout32"))
    parser.add_argument("--max-submissions", type=int, default=8)
    parser.add_argument("--max-completion-tokens", type=int, default=8000)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--thinking-mode", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()

    selected = v84.v83.load(args.subset)["cases"]
    for ordinal, case in enumerate(selected, 1):
        output = args.output_root / "cases" / f"{case['scenario_id']}__{case['mode']}.json"
        if output.exists() and not args.rerun:
            print(f"holdout {ordinal}/{len(selected)} {case['case_id']} SKIP", flush=True)
            continue
        started = time.perf_counter()
        row = v84.run_case(case, args.data_root, args.max_completion_tokens, args.max_submissions,
                           args.temperature, args.thinking_mode)
        row["development_replay_of_v83"] = False
        row["agent_holdout"] = True
        row["holdout_subset"] = args.subset.as_posix()
        v84.v83.write(output, row)
        print(f"holdout {ordinal}/{len(selected)} {case['case_id']} "
              f"vc={row['arm']['verified_completion']} calls={row['llm_metrics']['logical_llm_calls']} "
              f"elapsed={time.perf_counter()-started:.1f}s", flush=True)

    summary = v84.summarize(sorted((args.output_root / "cases").glob("*.json")), args.output_root)
    summary["development_replay_of_v83"] = False
    summary["agent_holdout"] = True
    summary["disjoint_from_agent128"] = True
    summary["claim_status"] = "post-freeze held-out agent evaluation; candidate pool shared with v8.3 boundary study"
    v84.v83.write(args.output_root / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
