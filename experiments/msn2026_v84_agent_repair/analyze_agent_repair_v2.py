#!/usr/bin/env python3
"""Finalize auditable v8.4 replay/holdout summaries without changing outcomes."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_agent128_repair_v2 as v84  # noqa: E402


ROOT = Path(".")
RESULT_ROOT = ROOT / "results/msn2026_v84_agent_repair"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    import math
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]


def group(rows: list[dict], key: str) -> dict[str, dict[str, int]]:
    output: dict[str, dict[str, int]] = {}
    for value in sorted({row[key] for row in rows}):
        selected = [row for row in rows if row[key] == value]
        output[value] = {
            "cases": len(selected),
            "verified_completion": sum(row["arm"]["verified_completion"] for row in selected),
            "unsafe_release": sum(row["arm"]["unsafe_release"] for row in selected),
            "logical_llm_calls": sum(row["arm"]["llm_usage"]["logical_llm_calls"] for row in selected),
        }
    return output


def feedback_example(replay_rows: list[dict]) -> dict:
    case = next(row for row in replay_rows if row["case_id"] == "ext-batfish_official-6aca0187b502d999::minimal")
    sid = case["scenario_id"]
    scenario = ROOT / "data/msn2026_v83_external/public/scenarios" / sid
    metadata = load(scenario / "metadata.json")
    device = metadata["device"]
    baseline = {device: (scenario / "baseline/configs" / f"{device}.conf").read_text(encoding="utf-8")}
    passive = v84.v83.records(load(scenario / "passive_observations.json"))
    active = v84.v83.records(load(ROOT / "data/msn2026_v83_external/sealed/oracles" / sid / "active_observations.json"))
    intent = load(scenario / "intent.json")
    feedback = v84.detailed_feedback(
        "full_envelope", case["arm"]["trace"][0]["evaluation"], passive + active,
        baseline, intent["selector"]["subjects"][0]
    )
    return {
        "case_id": case["case_id"],
        "feedback": feedback,
        "audit": {
            "patch_disclosed": feedback["patch_disclosed"],
            "strategy_disclosed": feedback["strategy_disclosed"],
            "contains_edits_field": "edits" in json.dumps(feedback).lower(),
            "contains_replacement_text": "old_text" in json.dumps(feedback) or "new_text" in json.dumps(feedback),
        },
    }


def main() -> None:
    old = load(ROOT / "results/msn2026_v83_external/agent128/summary.json")
    original = next(row for row in old["methods"] if row["method"] == "full_envelope")
    replay = load(RESULT_ROOT / "frozen_replay79/summary.json")
    holdout = load(RESULT_ROOT / "holdout32/summary.json")
    batfish_agent128 = load(RESULT_ROOT / "batfish_agent128/summary.json")
    union_agent128 = load(RESULT_ROOT / "batfish_union_repairs/combined_summary.json")
    union_repair = load(RESULT_ROOT / "union_repair_agent128/summary.json")
    batfish_holdout = load(RESULT_ROOT / "batfish_holdout32/summary.json")
    union_holdout = load(RESULT_ROOT / "batfish_union_holdout_repairs/combined_summary.json")
    replay_rows = [load(path) for path in sorted((RESULT_ROOT / "frozen_replay79/cases").glob("*.json"))]
    holdout_rows = [load(path) for path in sorted((RESULT_ROOT / "holdout32/cases").glob("*.json"))]
    final_lines = [
        row["arm"]["trace"][-1]["evaluation"]["reports"]["full"]["textual"]["lines_touched"]
        for row in replay_rows
    ]
    analysis = {
        "schema_version": "msn2026-v84-agent-repair-analysis-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_partition": {
            "original_agent128": "v8.3 frozen confirmatory agent experiment",
            "failure_replay79": "post-freeze engineering replay on a development corpus",
            "holdout32": "post-freeze agent holdout disjoint from Agent128; shared public candidate pool",
            "full_u": "independent Batfish audit plus adaptive repair and independent re-audit",
        },
        "original_agent128_full": original,
        "engineering_replay_combined": {
            "case_count": 128,
            "verified_completion": original["verified_completion"] + replay["verified_completion"],
            "unsafe_release": original["unsafe_release"] + replay["unsafe_release"],
            "reject_to_repair_success": original["reject_to_repair_success"] + replay["reject_to_repair_success"],
            "attempt_exhaustion": replay["attempt_exhaustion"],
        },
        "failure_replay79": replay,
        "holdout32": holdout,
        "independent_batfish_agent128": {key: batfish_agent128[key] for key in ("candidate_count", "pass", "fail", "verifier_na")},
        "union_repair_agent128": union_repair,
        "full_u_agent128": union_agent128,
        "independent_batfish_holdout32": {key: batfish_holdout[key] for key in ("candidate_count", "pass", "fail", "verifier_na")},
        "full_u_holdout32": union_holdout,
        "replay_submission_distribution": dict(sorted(Counter(row["arm"]["submissions"] for row in replay_rows).items())),
        "replay_final_patch_lines": {
            "min": min(final_lines),
            "median": statistics.median(final_lines),
            "p95": percentile(final_lines, .95),
            "max": max(final_lines),
        },
        "replay_by_vendor": group(replay_rows, "vendor"),
        "replay_by_family": group(replay_rows, "family"),
        "holdout_by_vendor": group(holdout_rows, "vendor"),
        "holdout_by_family": group(holdout_rows, "family"),
    }
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    (RESULT_ROOT / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    example = feedback_example(replay_rows)
    (RESULT_ROOT / "feedback_example.json").write_text(json.dumps(example, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest_inputs = [
        ROOT / "experiments/msn2026_v84_agent_repair/agent_repair_v2_freeze.json",
        ROOT / "experiments/msn2026_v84_agent_repair/agent_holdout32_freeze.json",
        ROOT / "experiments/msn2026_v84_agent_repair/union_repair_freeze.json",
        ROOT / "experiments/msn2026_v84_agent_repair/union_reaudit_freeze_amendment.json",
        ROOT / "experiments/msn2026_v84_agent_repair/transport_recovery_freeze.json",
        ROOT / "experiments/msn2026_v84_agent_repair/union_holdout_freeze.json",
        RESULT_ROOT / "frozen_replay79/summary.json",
        RESULT_ROOT / "holdout32/summary.json",
        RESULT_ROOT / "batfish_agent128/summary.json",
        RESULT_ROOT / "union_repair_agent128/summary.json",
        RESULT_ROOT / "batfish_union_repairs/combined_summary.json",
        RESULT_ROOT / "batfish_holdout32/summary.json",
        RESULT_ROOT / "batfish_union_holdout_repairs/combined_summary.json",
        RESULT_ROOT / "analysis.json",
        RESULT_ROOT / "feedback_example.json",
    ]
    manifest = {
        "schema_version": "msn2026-v84-agent-repair-manifest-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "v8.3 fresh public-source corpus; v8.3 failure outcomes explicitly select the development replay only; holdout selection is disjoint and label-independent",
        "seeds": {"agent128": 83127, "holdout32": 84032},
        "backend": "openai_compatible_chat_completions",
        "model": old["llm_metrics"]["configured_model"],
        "credentials_recorded": False,
        "inputs_and_outputs": [{"path": path.as_posix(), "sha256": digest(path)} for path in manifest_inputs],
    }
    (RESULT_ROOT / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(analysis, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
