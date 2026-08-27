"""Verify independently extracted path-set obligations with unmodified Rela."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from rela.language.regularir import I, P, PIntersect, PNegSymbols, PStar, pDot, postState, preState
from rela.networkmodel import SimpleNC
from rela.verification.specverifier import SpecVerifier


def _path_expr(path: List[str]):
    if not path:
        raise ValueError("Rela path cannot be empty")
    expression = P(path[0])
    for symbol in path[1:]:
        expression = expression + P(symbol)
    return expression


def _path_set_expr(paths: List[List[str]]):
    if not paths:
        raise ValueError("path set cannot be empty for this obligation")
    expression = _path_expr(paths[0])
    for path in paths[1:]:
        expression = expression | _path_expr(path)
    return expression


def _replace_symbol(old: str, new: str):
    changed = I(PStar(pDot)) + (P(old) * P(new)) + I(PStar(pDot))
    unchanged = I(PStar(PNegSymbols(old)))
    return changed | unchanged


def compile_spec(obligation: Dict[str, Any]):
    relation = obligation["relation"]
    if relation == "preserve":
        return preState == postState
    if relation == "replace":
        allowed = obligation["allowed"]
        if allowed.get("kind") == "replace_symbol":
            return preState >> _replace_symbol(allowed["from"], allowed["to"]) == postState
        if allowed.get("kind") == "exact_path_pairs":
            pairs = allowed["pairs"]
            transforms = _path_expr(pairs[0]["before"]) * _path_expr(pairs[0]["after"])
            for pair in pairs[1:]:
                transforms = transforms | (_path_expr(pair["before"]) * _path_expr(pair["after"]))
            return preState >> transforms == postState
        raise ValueError("unsupported replace relation")
    if relation == "add":
        return preState | _path_set_expr(obligation["allowed"]["paths"]) == postState
    if relation == "remove":
        return PIntersect(preState, ~_path_set_expr(obligation["allowed"]["paths"])) == postState
    raise ValueError(f"unsupported relation {relation!r}")


def verify_fec(fec: Dict[str, Any]) -> Dict[str, Any]:
    spec = compile_spec(fec["obligation"])
    result = SpecVerifier.verify(spec, SimpleNC.from_single_fec(fec["before_paths"], fec["after_paths"]))
    return {
        "fec_id": fec["fec_id"],
        "prefix": fec["prefix"],
        "obligation_id": fec["obligation"]["obligation_id"],
        "relation": fec["obligation"]["relation"],
        "spec": str(spec),
        "status": "PASS" if result.is_passed() and result.n_skipped == 0 else "FAIL",
        "n_total": result.n_total,
        "n_passed": result.n_passed,
        "n_failed": result.n_failed,
        "n_skipped": result.n_skipped,
    }


def verify_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    for fec in payload["fecs"]:
        try:
            rows.append(verify_fec(fec))
        except (KeyError, ValueError, TypeError) as exc:
            rows.append(
                {
                    "fec_id": fec.get("fec_id"),
                    "prefix": fec.get("prefix"),
                    "obligation_id": (fec.get("obligation") or {}).get("obligation_id"),
                    "relation": (fec.get("obligation") or {}).get("relation"),
                    "status": "N/A",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
    statuses = {row["status"] for row in rows}
    overall = "FAIL" if "FAIL" in statuses else "N/A" if "N/A" in statuses else "PASS"
    return {
        "schema_version": "msn2026.rela_result.v2",
        "snapshot_id": payload["snapshot_id"],
        "path_source": payload["path_source"],
        "status": overall,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = verify_payload(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

