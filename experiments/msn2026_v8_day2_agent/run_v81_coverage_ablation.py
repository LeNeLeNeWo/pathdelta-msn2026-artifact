#!/usr/bin/env python3
"""Replay the same stored LLM candidates with passive vs active FEC coverage."""

from __future__ import annotations

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

DATA = ROOT / "data/msn2026_v81_public_nontriviality"
SOURCE = ROOT / "results/msn2026_v81_public_paired_agent_pilot/paired_results.json"
OUTPUT = ROOT / "results/msn2026_v81_coverage_ablation"


def records(path: Path) -> list[BehaviorRecord]:
    return [BehaviorRecord(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def configs(path: Path) -> Dict[str, str]:
    return {item.stem: item.read_text(encoding="utf-8") for item in path.glob("*.conf")}


def submission_edits(payload: Dict[str, Any]) -> list[SearchReplaceEdit]:
    return [SearchReplaceEdit(row["device"], row["old_text"], row["new_text"]) for row in payload["edits"]]


def main() -> None:
    stored = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = []
    for item in stored:
        case = DATA / "scenarios" / item["scenario_id"]
        baseline = configs(case / "baseline")
        passive = records(case / "visible_pre_observations.json")
        active, provenance = augment_behavior_universe_with_frr_probes(baseline, passive)
        heldout = records(case / "heldout_pre_observations.json")
        intent = json.loads((case / "intent.json").read_text(encoding="utf-8"))
        submission = item["common_trace"][-1]["parsed_submission"]
        outcomes = {}
        for mode, universe in (("passive_visible", passive), ("active_witness", active)):
            envelope = derive_change_envelope_v2(
                intent,
                baseline,
                universe,
                build_frr_dependency_graph(baseline),
                behavior_universe_provenance={
                    "backend": mode,
                    "complete": False,
                    "uncovered_reason": "coverage ablation",
                },
            )
            evaluation = evaluate_submission(
                baseline,
                submission_edits(submission),
                universe,
                envelope,
                OUTPUT / item["scenario_id"] / mode,
                "pathdelta-msn2026-frr-syntax",
            )
            outcomes[mode] = {
                "accepted": bool(evaluation["contract_pass"]["full_envelope"]),
                "oracle": oracle_safety(baseline, evaluation, heldout),
                "frame_failures": evaluation["report"]["compliance"]["frame_failures"],
            }
        rows.append(
            {
                "scenario_id": item["scenario_id"],
                "outcomes": outcomes,
                "active_probe_count": len(provenance["added_behavior_records"]),
                "new_llm_calls": 0,
            }
        )
    summary = {
        "case_count": len(rows),
        "new_llm_calls": 0,
        "passive_unsafe_accepts": sum(
            row["outcomes"]["passive_visible"]["accepted"]
            and not row["outcomes"]["passive_visible"]["oracle"]["safe"]
            for row in rows
        ),
        "active_unsafe_accepts": sum(
            row["outcomes"]["active_witness"]["accepted"]
            and not row["outcomes"]["active_witness"]["oracle"]["safe"]
            for row in rows
        ),
        "passive_safe_accepts": sum(
            row["outcomes"]["passive_visible"]["accepted"]
            and row["outcomes"]["passive_visible"]["oracle"]["safe"]
            for row in rows
        ),
        "active_safe_accepts": sum(
            row["outcomes"]["active_witness"]["accepted"]
            and row["outcomes"]["active_witness"]["oracle"]["safe"]
            for row in rows
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "candidate_results.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
