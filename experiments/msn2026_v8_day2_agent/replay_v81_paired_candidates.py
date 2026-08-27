#!/usr/bin/env python3
"""Replay immutable LLM submissions after oracle/coverage fixes, with zero API calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.msn2026_v8_day2_agent.agent_benchmark import evaluate_submission
from experiments.msn2026_v8_day2_agent.change_envelope import SearchReplaceEdit
from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    augment_behavior_universe_with_frr_probes,
    build_frr_dependency_graph,
    derive_change_envelope_v2,
)
from experiments.msn2026_v8_day2_agent.run_v81_paired_agent import oracle_safety


def records(path: Path) -> list[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def configs(path: Path) -> Dict[str, str]:
    return {item.stem: item.read_text(encoding="utf-8") for item in sorted(path.glob("*.conf"))}


def edits(submission: Dict[str, Any]) -> list[SearchReplaceEdit]:
    return [SearchReplaceEdit(str(row["device"]), str(row["old_text"]), str(row["new_text"])) for row in submission["edits"]]


def evaluate_stored(
    submission: Dict[str, Any],
    baseline: Dict[str, str],
    universe: list[BehaviorRecord],
    envelope: Any,
    output: Path,
) -> Dict[str, Any]:
    return evaluate_submission(
        baseline, edits(submission), universe, envelope, output, "pathdelta-msn2026-frr-syntax"
    )


def run(source_results: Path, data_root: Path, output_root: Path) -> Dict[str, Any]:
    source_bytes = source_results.read_bytes()
    source = json.loads(source_bytes)
    rows = []
    for old in source:
        case_root = data_root / "scenarios" / old["scenario_id"]
        baseline = configs(case_root / "baseline")
        observed = records(case_root / "visible_pre_observations.json")
        heldout = records(case_root / "heldout_pre_observations.json")
        universe, provenance = augment_behavior_universe_with_frr_probes(baseline, observed)
        intent = json.loads((case_root / "intent.json").read_text(encoding="utf-8"))
        envelope = derive_change_envelope_v2(
            intent,
            baseline,
            universe,
            build_frr_dependency_graph(baseline),
            behavior_universe_provenance={
                "backend": "corrected-replay-active-config-boundary-probes",
                "complete": False,
                "uncovered_reason": "finite equivalence-class representatives",
                "active_probe_plan": provenance,
            },
        )
        common = old["common_trace"][-1]["parsed_submission"]
        goal_eval = evaluate_stored(
            common, baseline, universe, envelope, output_root / old["scenario_id"] / "goal"
        )
        final_submission = common
        for trace in old.get("full_envelope", {}).get("trace", []):
            if trace.get("parsed_submission"):
                final_submission = trace["parsed_submission"]
        full_eval = evaluate_stored(
            final_submission, baseline, universe, envelope, output_root / old["scenario_id"] / "full"
        )
        rows.append(
            {
                "scenario_id": old["scenario_id"],
                "source_candidate_sha256": hashlib.sha256(json.dumps(common, sort_keys=True).encode()).hexdigest(),
                "goal_only": {
                    "accepted": bool(goal_eval["contract_pass"]["syntax_goal"]),
                    "oracle": oracle_safety(baseline, goal_eval, heldout),
                    "evaluation": goal_eval,
                },
                "full_envelope": {
                    "accepted": bool(full_eval["contract_pass"]["full_envelope"]),
                    "oracle": oracle_safety(baseline, full_eval, heldout),
                    "evaluation": full_eval,
                    "used_stored_revision": final_submission is not common,
                },
                "active_probe_provenance": provenance,
                "new_llm_calls": 0,
            }
        )
    summary = {
        "case_count": len(rows),
        "new_llm_calls": 0,
        "source_results_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "goal_only_unsafe_accepts": sum(row["goal_only"]["accepted"] and not row["goal_only"]["oracle"]["safe"] for row in rows),
        "full_envelope_unsafe_accepts": sum(row["full_envelope"]["accepted"] and not row["full_envelope"]["oracle"]["safe"] for row in rows),
        "full_envelope_safe_accepts": sum(row["full_envelope"]["accepted"] and row["full_envelope"]["oracle"]["safe"] for row in rows),
        "full_envelope_rejections": sum(not row["full_envelope"]["accepted"] for row in rows),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "replayed_results.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"summary": summary, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-results", type=Path, default=Path("results/msn2026_v81_public_paired_agent_pilot/paired_results.json"))
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v81_public_nontriviality"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v81_public_paired_agent_corrected_replay"))
    args = parser.parse_args()
    result = run(args.source_results, args.data_root, args.output_root)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
