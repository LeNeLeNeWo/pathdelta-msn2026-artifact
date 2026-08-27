#!/usr/bin/env python3
"""Create the immutable v8.3 run manifest from completed artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def item(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    return {"path": relative, "sha256": sha(path), "bytes": path.stat().st_size}


def main() -> None:
    result = ROOT / "results/msn2026_v83_external"
    protocol = load(ROOT / "experiments/msn2026_v83_external/protocol_freeze.json")
    agent = load(result / "agent128/analysis.json")
    redteam = load(result / "redteam_generation/summary.json")
    batfish_parse = load(result / "batfish_applicability/summary.json")
    batfish_symbolic = load(result / "batfish_symbolic_external_v2/summary.json")
    cross = load(result / "cross_backend_audit.json")
    paths = [
        "experiments/msn2026_v83_external/preregistered_protocol.md",
        "experiments/msn2026_v83_external/protocol_freeze.json",
        "experiments/msn2026_v83_external/run_manifest.schema.json",
        "data/msn2026_v83_external/source_manifest.json",
        "data/msn2026_v83_external/dataset_manifest.json",
        "data/msn2026_v83_external/agent128_subset.json",
        "results/msn2026_v83_external/redteam_corpus_receipt.json",
        "results/msn2026_v83_external/external_boundaries/analysis.json",
        "results/msn2026_v83_external/confirmatory_statistics.json",
        "results/msn2026_v83_external/backend_coverage_matrix.json",
        "results/msn2026_v83_external/coverage_sensitivity.json",
        "results/msn2026_v83_external/scalability.json",
        "results/msn2026_v83_external/scalability_batfish.json",
        "results/msn2026_v83_external/agent128/analysis.json",
        "results/msn2026_v83_external/agent128/integrity_receipt.json",
        "results/msn2026_v83_external/batfish_symbolic_external_v2/summary.json",
        "results/msn2026_v83_external/cross_backend_audit.json",
        "results/msn2026_v83_external/go_no_go.json",
    ]
    manifest = {
        "schema_version": "msn2026.v83.run.1",
        "run_id": "pathdelta-msn2026-v83-external-agent128",
        "status": "completed",
        "protocol": {"version": "msn2026-v83-1.0.0", "aggregate_sha256": protocol["aggregate_sha256"],
                     "data_seed": 20260814, "agent_seed": 83127, "coverage_seed": 83191},
        "data": {"fresh_sources": True, "legacy_inputs_used": False,
                 "dataset_manifest_sha256": sha(ROOT / "data/msn2026_v83_external/dataset_manifest.json"),
                 "source_manifest_sha256": sha(ROOT / "data/msn2026_v83_external/source_manifest.json")},
        "model": {"provider": redteam["llm_metrics"]["provider"], "configured_model": redteam["llm_metrics"]["configured_model"],
                  "backend": redteam["llm_metrics"]["backend"], "base_url": redteam["llm_metrics"]["base_url"],
                  "thinking_mode": redteam["llm_metrics"]["thinking_mode"], "secret_values_recorded": False},
        "backends": {"batfish_parse": {"candidate_count": batfish_parse["candidate_count"], "status_counts": batfish_parse["status_counts"]},
                     "batfish_symbolic": {"candidate_count": batfish_symbolic["candidate_count"],
                                          "applicable_count": batfish_symbolic["applicable_count"], "na": batfish_symbolic["verifier_na"]},
                     "registered_adapter": {"vendor_count": 3, "candidate_count": 253}},
        "metrics": {"redteam_generation": {"logical_llm_calls": redteam["llm_metrics"]["logical_llm_calls"],
                                             "token_usage": redteam["llm_metrics"]["token_usage"]},
                    "agent128": agent["all_backend_calls"], "cross_backend_methods": cross["methods"]},
        "artifacts": [item(path) for path in paths],
        "claim_status": "NARROWED",
        "limitations": [
            "The preregistered 15 percentage-point improvement gate over VerifierLoop was not met (observed 10 points).",
            "The registered finite attribute oracle omitted Batfish-visible metric and origin-type collateral in 12 applicable candidates.",
            "The cross-backend union is exploratory and post-unblinding; it is not pooled with confirmatory statistics.",
            "Agent128 uses one configured model endpoint and has substantial fail-closed attempt exhaustion.",
        ],
    }
    path = result / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"run_manifest": str(path), "sha256": sha(path), "artifact_count": len(paths)}, indent=2))


if __name__ == "__main__":
    main()
