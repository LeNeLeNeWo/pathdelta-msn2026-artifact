#!/usr/bin/env python3
"""Validate v8.1 dependency claims with Batfish symbolic policy diffs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return str(value)


def records(frame: Any, limit: int = 20) -> list[Dict[str, Any]]:
    return [{str(key): jsonable(value) for key, value in row.items()} for _, row in frame.head(limit).iterrows()]


def snapshot(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "configs").mkdir(parents=True)
    for path in source.glob("*.conf"):
        text = path.read_text(encoding="utf-8")
        # Batfish vendor detection needs an FRR version/defaults marker. This
        # adapter adds parser metadata only; policy commands remain byte-for-
        # byte identical to the candidate artifact.
        if not text.lstrip().lower().startswith("frr version"):
            text = "frr version 8.4\nfrr defaults traditional\n" + text
        if "service integrated-vtysh-config" not in text:
            lines = text.splitlines()
            hostname_index = next(
                (index for index, line in enumerate(lines) if line.startswith("hostname ")), 1
            )
            lines.insert(hostname_index + 1, "service integrated-vtysh-config")
            text = "\n".join(lines) + "\n"
        # The source-conditioned policy snippets intentionally omit interface
        # addressing. Add an inert loopback and router-id in the Batfish-only
        # wrapper so vendor/model detection has a complete device skeleton.
        if "interface lo" not in text:
            text = text.replace(
                "service integrated-vtysh-config\n",
                "service integrated-vtysh-config\n!\ninterface lo\n ip address 10.255.255.1/32\n!\n",
                1,
            )
        if " bgp router-id " not in text:
            text = text.replace("router bgp 65000\n", "router bgp 65000\n bgp router-id 10.255.255.1\n", 1)
        if "\nline vty\n" not in text:
            text = text.rstrip() + "\nline vty\n!\n"
        # Batfish's snapshot classifier ignores some nonstandard extensions;
        # use its conventional .cfg extension inside the derived snapshot.
        (destination / "configs" / f"{path.stem}.cfg").write_text(text, encoding="utf-8")


def compare(bf: Any, post: str, pre: str, node: str, policy: str, reference: str | None = None) -> Dict[str, Any]:
    frame = bf.q.compareRoutePolicies(
        nodes=node, policy=policy, referencePolicy=reference or policy
    ).answer(snapshot=post, reference_snapshot=pre).frame()
    return {"difference_rows": len(frame), "preview": records(frame)}


def run(data_root: Path, output_root: Path) -> Dict[str, Any]:
    try:
        from pybatfish.client.session import Session
    except ImportError as exc:
        raise RuntimeError("use .venv-batfish/bin/python") from exc

    output_root.mkdir(parents=True, exist_ok=True)
    snapshots = output_root / "snapshots"
    bf = Session(host="localhost")
    network = "pathdelta-msn2026-v81-policy"
    bf.set_network(network)
    scenario_specs = {
        "latent_shared_route_map": {
            "node": "edge-latent-rm",
            "protected": ["RM_SHARED"],
            "target_policies": {"safe_local_fork": "RM_TARGET_LOCAL"},
        },
        "latent_shared_prefix_list": {
            "node": "edge-latent-pl",
            "protected": ["RM_RIGHT"],
            "target_policies": {"safe_target_clause": "RM_LEFT"},
        },
        "target_exclusive_in_place": {
            "node": "edge-exclusive",
            "protected": [],
            "target_policies": {"safe_exclusive_in_place": "RM_EXCLUSIVE"},
        },
    }
    rows = []
    for scenario_id, spec in scenario_specs.items():
        scenario = data_root / "scenarios" / scenario_id
        pre_name = f"{scenario_id}-pre"
        pre_path = snapshots / pre_name
        snapshot(scenario / "baseline", pre_path)
        bf.init_snapshot(str(pre_path), name=pre_name, overwrite=True)
        for candidate_root in sorted((scenario / "candidates").iterdir()):
            candidate_id = candidate_root.name
            post_name = f"{scenario_id}-{candidate_id}"
            post_path = snapshots / post_name
            snapshot(candidate_root / "configs", post_path)
            bf.init_snapshot(str(post_path), name=post_name, overwrite=True)
            parse_status = bf.q.fileParseStatus().answer(snapshot=post_name).frame()
            warnings = bf.q.parseWarning().answer(snapshot=post_name).frame()
            status_records = records(parse_status)
            warning_records = records(warnings)
            allowlisted_warning = lambda row: str(row.get("Text", "")).lower().startswith(
                ("frr version", "frr defaults")
            )
            protected_diffs = {
                policy: compare(bf, post_name, pre_name, spec["node"], policy)
                for policy in spec["protected"]
            }
            target_policy = spec["target_policies"].get(candidate_id)
            target_diff = (
                compare(bf, post_name, pre_name, spec["node"], target_policy)
                if target_policy and target_policy != "RM_TARGET_LOCAL"
                else None
            )
            # The local fork has no same-named policy in pre. Compare its new
            # target policy with the old shared target policy instead.
            if target_policy == "RM_TARGET_LOCAL":
                target_diff = compare(
                    bf, post_name, pre_name, spec["node"], "RM_TARGET_LOCAL", "RM_SHARED"
                )
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "candidate_id": candidate_id,
                    "parse_status": status_records,
                    "parse_warnings": warning_records,
                    "parse_audit_pass": bool(status_records)
                    and all(row.get("Nodes") for row in status_records)
                    and all(allowlisted_warning(row) for row in warning_records),
                    "protected_policy_diffs": protected_diffs,
                    "target_policy_diff": target_diff,
                }
            )

    checks = {
        "all_snapshots_parsed_with_only_allowlisted_frr_metadata_warnings": all(
            row["parse_audit_pass"] for row in rows
        ),
        "unsafe_shared_route_map_detected": next(
            row for row in rows if row["candidate_id"] == "unsafe_value_only_shared_edit"
        )["protected_policy_diffs"]["RM_SHARED"]["difference_rows"] > 0,
        "safe_local_fork_preserves_shared_route_map": next(
            row for row in rows if row["candidate_id"] == "safe_local_fork"
        )["protected_policy_diffs"]["RM_SHARED"]["difference_rows"] == 0,
        "unsafe_shared_prefix_list_changes_other_consumer": next(
            row for row in rows if row["candidate_id"] == "unsafe_shared_match_expansion"
        )["protected_policy_diffs"]["RM_RIGHT"]["difference_rows"] > 0,
        "safe_target_clause_preserves_other_consumer": next(
            row for row in rows if row["candidate_id"] == "safe_target_clause"
        )["protected_policy_diffs"]["RM_RIGHT"]["difference_rows"] == 0,
        "safe_exclusive_in_place_has_intended_policy_delta": (
            next(row for row in rows if row["candidate_id"] == "safe_exclusive_in_place")["target_policy_diff"]["difference_rows"] > 0
        ),
    }
    summary = {
        "backend": "batfish_compareRoutePolicies",
        "network": network,
        "scope": "v8.1 targeted backend validation, not a prevalence estimate",
        "parser_claim_boundary": "Batfish normalizes these FRR policy snippets through its CISCO_IOS grammar; only frr version/defaults metadata warnings are allowlisted.",
        "checks": checks,
        "passed": all(checks.values()),
        "rows": rows,
    }
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/msn2026_v81_nontriviality"))
    parser.add_argument("--output-root", type=Path, default=Path("results/msn2026_v81_batfish_policy_dev"))
    args = parser.parse_args()
    result = run(args.data_root, args.output_root)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
