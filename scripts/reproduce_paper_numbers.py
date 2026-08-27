#!/usr/bin/env python3
"""Reconstruct the manuscript's principal counts from frozen aggregate JSON."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def by_method(rows):
    return {row["method"]: row for row in rows}


def require(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, found {actual!r}")


def main() -> None:
    boundary = load("results/msn2026_v83_external/external_boundaries/analysis.json")
    require(boundary["candidate_count"], 253, "applicable candidates")
    require(boundary["safe_goal_count"], 58, "registered-safe goal candidates")
    require(boundary["unsafe_goal_count"], 120, "registered-unsafe goal candidates")
    b = by_method(boundary["overall"])
    require(b["verifier_loop"]["unsafe_accepted"], 12, "VerifierLoop unsafe acceptance")
    require(b["full_envelope"]["unsafe_accepted"], 0, "FullR unsafe acceptance")
    require(b["full_envelope"]["safe_rejected"], 0, "FullR safe rejection")

    agent = by_method(load("results/msn2026_v83_external/agent128/analysis.json")["methods"])
    require(agent["direct"]["verified_completion"], 33, "Direct VC")
    require(agent["verifier_loop"]["collateral_release"], 5, "VerifierLoop CR")
    require(agent["full_envelope"]["verified_completion"], 49, "FullR VC")
    require(agent["full_envelope"]["collateral_release"], 0, "FullR CR")

    comparison = by_method(
        load("results/msn2026_v85_external_baselines/confirmatory/summary.json")["methods"]
    )
    require(comparison["cornetto_agentic_adapted"]["verified_completion"], 18, "Cornetto-adapted VC")
    require(comparison["cornetto_agentic_adapted"]["unsafe_release"], 10, "Cornetto-adapted unsafe")
    require(comparison["pathdelta_fullr"]["verified_completion"], 35, "PathDelta VC")
    require(comparison["pathdelta_fullr"]["unsafe_release"], 0, "PathDelta unsafe")

    development = load("results/msn2026_v84_agent_repair/analysis.json")
    require(development["engineering_replay_combined"]["verified_completion"], 128, "development replay VC")
    require(development["full_u_agent128"]["full_u_verified_completion"], 120, "FullU repair VC")
    require(development["full_u_holdout32"]["full_u_verified_completion"], 31, "holdout VC")

    print("| Study | Method | Verified completion | Unsafe/collateral release |")
    print("| --- | --- | ---: | ---: |")
    print(f"| Agent128 | Direct | {agent['direct']['verified_completion']}/128 | {agent['direct']['collateral_release']}/128 |")
    print(f"| Agent128 | VerifierLoop | {agent['verifier_loop']['verified_completion']}/128 | {agent['verifier_loop']['collateral_release']}/128 |")
    print(f"| Agent128 | FullR | {agent['full_envelope']['verified_completion']}/128 | {agent['full_envelope']['collateral_release']}/128 |")
    print(f"| Common task | Cornetto/Agentic adapted | {comparison['cornetto_agentic_adapted']['verified_completion']}/40 | {comparison['cornetto_agentic_adapted']['unsafe_release']}/40 |")
    print(f"| Common task | PathDelta FullR | {comparison['pathdelta_fullr']['verified_completion']}/40 | {comparison['pathdelta_fullr']['unsafe_release']}/40 |")
    print("\nAll frozen manuscript-count assertions PASS.")


if __name__ == "__main__":
    main()
