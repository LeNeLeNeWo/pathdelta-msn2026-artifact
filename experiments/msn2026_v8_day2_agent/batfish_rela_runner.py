"""Backend-neutral Batfish path extraction and Rela obligation compilation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


STATUS = {"PASS", "FAIL", "N/A"}


@dataclass(frozen=True)
class FECMapping:
    fec_id: str
    prefix: str
    destination_samples: Tuple[str, ...]
    fec_class: str
    obligation_id: str
    relation: str
    allowed: Optional[Mapping[str, Any]]


@dataclass(frozen=True)
class ExtractedPathSet:
    fec_id: str
    snapshot: str
    paths: Tuple[Tuple[str, ...], ...]
    destination_samples: Tuple[str, ...]
    max_traces: int
    ecmp_observed: bool
    trace_limit_reached: bool
    completeness: str
    query_provenance: Mapping[str, Any]


def canonical_ecmp_paths(paths: Iterable[Sequence[str]]) -> Tuple[Tuple[str, ...], ...]:
    """Represent ECMP as a deterministic set of complete node sequences."""
    return tuple(sorted({tuple(str(node) for node in path) for path in paths if path}))


def make_extracted_path_set(
    mapping: FECMapping,
    snapshot: str,
    paths: Iterable[Sequence[str]],
    *,
    max_traces: int,
    trace_rows_returned: int,
    network: str,
    query: str,
) -> ExtractedPathSet:
    canonical = canonical_ecmp_paths(paths)
    limit = trace_rows_returned >= max_traces
    return ExtractedPathSet(
        fec_id=mapping.fec_id,
        snapshot=snapshot,
        paths=canonical,
        destination_samples=mapping.destination_samples,
        max_traces=max_traces,
        ecmp_observed=len(canonical) > 1,
        trace_limit_reached=limit,
        completeness="bounded_trace_limit" if limit else "bounded_destination_samples",
        query_provenance={
            "backend": "batfish",
            "network": network,
            "snapshot": snapshot,
            "query": query,
            "destination_samples": list(mapping.destination_samples),
        },
    )


def compile_rela_payload(
    snapshot_id: str,
    mappings: Sequence[FECMapping],
    pre: Mapping[str, ExtractedPathSet],
    post: Mapping[str, ExtractedPathSet],
) -> Dict[str, Any]:
    fecs = []
    provenance = []
    for mapping in mappings:
        before, after = pre[mapping.fec_id], post[mapping.fec_id]
        obligation = {
            "obligation_id": mapping.obligation_id,
            "relation": "preserve" if mapping.fec_class == "non_target" else mapping.relation,
        }
        if mapping.fec_class == "target" and mapping.allowed is not None:
            obligation["allowed"] = dict(mapping.allowed)
        fecs.append(
            {
                "fec_id": mapping.fec_id,
                "prefix": mapping.prefix,
                "class": mapping.fec_class,
                "before_paths": [list(path) for path in before.paths],
                "after_paths": [list(path) for path in after.paths],
                "obligation": obligation,
            }
        )
        provenance.append(
            {
                "fec_id": mapping.fec_id,
                "obligation_id": mapping.obligation_id,
                "pre_query": before.query_provenance,
                "post_query": after.query_provenance,
                "pre_path_sha256": hashlib.sha256(json.dumps(before.paths).encode()).hexdigest(),
                "post_path_sha256": hashlib.sha256(json.dumps(after.paths).encode()).hexdigest(),
                "coverage": {"pre": before.completeness, "post": after.completeness},
            }
        )
    return {
        "schema_version": "msn2026.rela_snapshot.v2",
        "snapshot_id": snapshot_id,
        "path_source": "batfish_independent_snapshot_queries",
        "fecs": fecs,
        "batfish_to_rela_provenance": provenance,
    }


def audit_batfish_parse(
    file_status_records: Sequence[Mapping[str, Any]],
    warning_records: Sequence[Mapping[str, Any]],
    *,
    warning_allowlist: Sequence[str],
) -> Dict[str, Any]:
    if not file_status_records:
        return {"status": "N/A", "reason": "fileParseStatus returned no records", "warnings": []}
    failed = [row for row in file_status_records if "FAILED" in str(row).upper() or "UNRECOGNIZED" in str(row).upper() and "PARTIALLY" not in str(row).upper()]
    classified = []
    for row in warning_records:
        text = json.dumps(row, sort_keys=True, default=str)
        allowed = any(token.lower() in text.lower() for token in warning_allowlist)
        classified.append({"warning": row, "classification": "ALLOWLISTED" if allowed else "UNEXPECTED"})
    unexpected = [row for row in classified if row["classification"] == "UNEXPECTED"]
    return {
        "status": "FAIL" if failed or unexpected else "PASS",
        "failed_parse_records": failed,
        "warnings": classified,
        "unexpected_warning_count": len(unexpected),
        "claim_boundary": "Parser PASS does not imply control-plane or path correctness.",
    }


def normalize_backend_status(executed: bool, passed: Optional[bool], reason: Optional[str] = None) -> Dict[str, Any]:
    if not executed:
        return {"status": "N/A", "reason": reason or "backend not executed"}
    return {"status": "PASS" if passed else "FAIL", "reason": reason}


def agreement_record(
    *,
    syntax: Mapping[str, Any],
    reachability: Mapping[str, Any],
    rela: Mapping[str, Any],
    dynamic: Mapping[str, Any],
) -> Dict[str, Any]:
    for backend in (syntax, reachability, rela, dynamic):
        if backend.get("status") not in STATUS:
            raise ValueError(f"invalid backend status: {backend}")
    return {
        "frr_syntax": dict(syntax),
        "batfish_reachability": dict(reachability),
        "rela_path_relation": dict(rela),
        "kathara_dynamic": dict(dynamic),
        "all_executed_backends_pass": all(
            backend["status"] == "PASS"
            for backend in (syntax, reachability, rela, dynamic)
            if backend["status"] != "N/A"
        ),
        "has_na": any(backend["status"] == "N/A" for backend in (syntax, reachability, rela, dynamic)),
    }

