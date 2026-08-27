#!/usr/bin/env python3
"""Build fresh, development-disjoint Day-2 scenarios for v8.5."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import random
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from experiments.msn2026_v83_external.vendor_policy_adapter import (
    behavior_rows,
    boundary_witnesses,
    configured_prefixes,
    evaluate_subject,
    find_candidate_subjects,
    parse,
    policy_for_subject,
)

DEFAULT_ROOT = PROJECT / "data/msn2026_v85_external_baselines"
DEFAULT_DEVELOPMENT_INDEX = PROJECT / "data/msn2026_v83_external/scenario_index.json"
SEED = 20260815
CASE_COUNT = 48
FAMILY_PRIORITY = {
    "shared_policy": 0,
    "shared_prefix_list": 1,
    "community_composition": 2,
    "ordered_policy": 3,
    "legacy_policy": 4,
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> tuple[int, str]:
    aggregate = hashlib.sha256()
    count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        aggregate.update(relative.encode())
        aggregate.update(b"\0")
        aggregate.update(sha256(path).encode())
        aggregate.update(b"\n")
        count += 1
    return count, aggregate.hexdigest()


def scenario_id(origin: str, subject: str, prefix: str) -> str:
    material = f"v85|{SEED}|cornetto|{origin}|{subject}|{prefix}"
    return "cmp-cornetto-" + hashlib.sha256(material.encode()).hexdigest()[:16]


def classify(model: Any, subject: str) -> str:
    policy = policy_for_subject(model, subject)
    terms = model.policies.get(policy, [])
    refs = [name for term in terms for name, _mode in term.prefix_lists]
    all_refs = [
        name
        for policy_terms in model.policies.values()
        for term in policy_terms
        for name, _mode in term.prefix_lists
    ]
    bound_policies = [name for name, _direction in model.bindings.values()]
    if bound_policies.count(policy) > 1:
        return "shared_policy"
    if any(all_refs.count(name) > 1 for name in refs):
        return "shared_prefix_list"
    if any(term.set_communities for term in terms):
        return "community_composition"
    if len(terms) >= 3:
        return "ordered_policy"
    return "legacy_policy"


def development_exclusion(index_path: Path) -> dict[str, Any]:
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    source_scenarios = sorted({
        row["source_scenario"]
        for row in rows
        if row.get("source_group") == "cornetto"
    })
    source_paths = sorted({
        row["source_path"]
        for row in rows
        if row.get("source_group") == "cornetto"
    })
    return {
        "schema_version": "msn2026-v85-development-exclusion-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "negative selection only; no old configuration, candidate, label, or result imported",
        "source_index": index_path.as_posix(),
        "source_index_sha256": sha256(index_path),
        "excluded_source_scenarios": source_scenarios,
        "excluded_source_paths": source_paths,
    }


def mine_pool(
    root: Path,
    rng: random.Random,
    excluded_paths: set[str],
) -> list[dict[str, Any]]:
    dataset = root / "sources/raw/cornetto_dataset"
    paths = sorted(dataset.glob("scenario-*/final_configs/configs/*.cfg"))
    rng.shuffle(paths)
    best_by_scenario: dict[str, dict[str, Any]] = {}
    for path in paths:
        source_scenario = path.parents[2].name
        origin = str(path.relative_to(root / "sources/raw"))
        if origin in excluded_paths or path.stat().st_size > 120_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        model = parse(text)
        subjects = find_candidate_subjects(model, "in")
        prefixes = configured_prefixes(model)
        if not subjects or len(prefixes) < 2:
            continue
        for subject in subjects:
            permitted = [
                (prefix, evaluate_subject(model, subject, prefix))
                for prefix in prefixes
            ]
            permitted = [
                (prefix, outcome)
                for prefix, outcome in permitted
                if outcome and outcome["decision"] == "permit"
                and outcome["local_pref"] is not None
            ]
            if len(permitted) < 2:
                continue
            permitted.sort(key=lambda pair: pair[0])
            prefix, before = rng.choice(permitted)
            row = {
                "source_group": "cornetto",
                "vendor": model.vendor,
                "origin": origin,
                "source_scenario": source_scenario,
                "text": text,
                "device": model.hostname,
                "subject": subject,
                "policy": policy_for_subject(model, subject),
                "target_prefix": prefix,
                "dimension": "local_pref",
                "desired": int(before["local_pref"]) + 50,
                "before": before,
                "family": classify(model, subject),
            }
            rank = (
                FAMILY_PRIORITY[row["family"]],
                -len(model.policies.get(row["policy"], [])),
                row["origin"],
                row["subject"],
            )
            current = best_by_scenario.get(source_scenario)
            if current is None or rank < current["_rank"]:
                row["_rank"] = rank
                best_by_scenario[source_scenario] = row
    output = []
    for row in best_by_scenario.values():
        clean = dict(row)
        clean.pop("_rank", None)
        output.append(clean)
    return output


def balanced_select(
    pool: list[dict[str, Any]], rng: random.Random, count: int
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        groups.setdefault(row["family"], []).append(row)
    for rows in groups.values():
        rng.shuffle(rows)
    selected: list[dict[str, Any]] = []
    family_order = sorted(groups, key=lambda value: FAMILY_PRIORITY[value])
    while len(selected) < count and any(groups.values()):
        for family in family_order:
            if groups.get(family) and len(selected) < count:
                selected.append(groups[family].pop())
    if len(selected) != count:
        raise RuntimeError(f"mined only {len(selected)} of {count} cases")
    return selected


def select_cases(
    pool: list[dict[str, Any]],
    rng: random.Random,
    count: int,
    excluded_scenarios: set[str],
) -> list[dict[str, Any]]:
    rare_families = {"shared_prefix_list", "community_composition", "shared_policy"}
    rare = [row for row in pool if row["family"] in rare_families]
    rare.sort(key=lambda row: (FAMILY_PRIORITY[row["family"]], row["source_scenario"]))
    if len(rare) < 10:
        raise RuntimeError(f"insufficient natural dependency-rich cases: {len(rare)}")
    used_scenarios = {row["source_scenario"] for row in rare}
    ordinary_pool = [
        row for row in pool
        if row["source_scenario"] not in excluded_scenarios
        and row["source_scenario"] not in used_scenarios
        and row["family"] not in rare_families
    ]
    selected = rare + balanced_select(ordinary_pool, rng, count - len(rare))
    rng.shuffle(selected)
    return selected


def build_case(root: Path, case: dict[str, Any], ordinal: int) -> dict[str, Any]:
    sid = scenario_id(case["origin"], case["subject"], case["target_prefix"])
    public = root / "public/scenarios" / sid
    sealed = root / "sealed/oracles" / sid
    config_name = f"{case['device']}.conf"
    config_path = public / "baseline/configs" / config_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(case["text"], encoding="utf-8")

    model = parse(case["text"])
    witnesses = boundary_witnesses(model, limit=64)
    if case["target_prefix"] not in witnesses:
        witnesses.append(case["target_prefix"])
    full_rows = behavior_rows(
        case["device"],
        case["text"],
        case["subject"],
        witnesses,
        "v85_candidate_independent_policy_classes",
    )
    target_id = (
        f"{case['device']}|{case['subject']}|"
        f"{ipaddress.ip_network(case['target_prefix'], strict=False)}"
    )
    target_rows = [row for row in full_rows if row["behavior_id"] == target_id]
    if len(target_rows) != 1:
        raise RuntimeError(f"target observation missing in {sid}")
    non_target = [row for row in full_rows if row["behavior_id"] != target_id]
    passive_rows = target_rows + non_target[:: max(1, len(non_target) // 8)][:8]
    active_rows = [row for row in full_rows if row not in passive_rows]

    subject_text = (
        case["subject"]
        .replace("@in", " inbound")
        .replace("@out", " outbound")
        .replace("@import", " import")
        .replace("@export", " export")
    )
    raw = (
        f"On {case['device']}, set local preference {case['desired']} for "
        f"{case['target_prefix']} received through {subject_text}. Preserve "
        "unrelated routing behavior and existing operational conventions."
    )
    intent = {
        "intent_id": sid,
        "raw_text": raw,
        "selector": {
            "devices": [case["device"]],
            "subjects": [case["subject"]],
            "fecs": [case["target_prefix"]],
            "dimensions": [case["dimension"]],
        },
        "changes": [{
            "dimension": case["dimension"],
            "relation": "replace",
            "desired": case["desired"],
        }],
    }
    metadata = {
        "scenario_id": sid,
        "ordinal": ordinal,
        "source_group": "cornetto",
        "source_scenario": case["source_scenario"],
        "source_path": case["origin"],
        "source_sha256": hashlib.sha256(case["text"].encode()).hexdigest(),
        "vendor": case["vendor"],
        "family": case["family"],
        "device": case["device"],
        "subject": case["subject"],
        "policy": case["policy"],
        "target_prefix": case["target_prefix"],
        "target_dimension": case["dimension"],
        "seed": SEED,
        "generator": "build_msn2026_v85_external_scenarios.py",
        "candidate_patch_used_for_witnesses": False,
    }
    write_json(public / "intent.json", intent)
    write_json(public / "metadata.json", metadata)
    write_json(public / "passive_observations.json", passive_rows)
    write_json(sealed / "active_observations.json", active_rows)
    write_json(sealed / "complete_pre_observations.json", full_rows)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--development-index", type=Path, default=DEFAULT_DEVELOPMENT_INDEX)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--count", type=int, default=CASE_COUNT)
    args = parser.parse_args()
    if args.seed != SEED:
        raise ValueError(f"frozen seed is {SEED}")
    if args.count != CASE_COUNT:
        raise ValueError(f"frozen case count is {CASE_COUNT}")
    if not (args.root / "source_manifest.json").exists():
        raise FileNotFoundError("run prepare_msn2026_v85_sources.py first")

    exclusion = development_exclusion(args.development_index)
    write_json(args.root / "development_exclusion.json", exclusion)
    rng = random.Random(args.seed)
    pool = mine_pool(args.root, rng, set(exclusion["excluded_source_paths"]))
    cases = select_cases(
        pool,
        rng,
        args.count,
        set(exclusion["excluded_source_scenarios"]),
    )
    if {row["origin"] for row in cases} & set(exclusion["excluded_source_paths"]):
        raise AssertionError("development source configuration leaked")

    for relative in ("public", "sealed"):
        target = args.root / relative
        if target.exists():
            shutil.rmtree(target)
    metadata = [build_case(args.root, case, index + 1) for index, case in enumerate(cases)]
    write_json(args.root / "scenario_index.json", metadata)
    public_count, public_digest = tree_hash(args.root / "public")
    sealed_count, sealed_digest = tree_hash(args.root / "sealed")
    manifest = {
        "schema_version": "msn2026-v85-external-scenarios-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "scenario_count": len(metadata),
        "source_groups": dict(Counter(row["source_group"] for row in metadata)),
        "vendors": dict(Counter(row["vendor"] for row in metadata)),
        "families": dict(Counter(row["family"] for row in metadata)),
        "source_configurations_disjoint_from_v83": True,
        "source_scenarios_disjoint_from_v83": False,
        "topology_disjoint_count": sum(
            row["source_scenario"]
            not in set(exclusion["excluded_source_scenarios"])
            for row in metadata
        ),
        "development_exclusion_sha256": sha256(args.root / "development_exclusion.json"),
        "source_manifest_sha256": sha256(args.root / "source_manifest.json"),
        "public_file_count": public_count,
        "public_tree_sha256": public_digest,
        "sealed_file_count": sealed_count,
        "sealed_tree_sha256": sealed_digest,
        "legacy_experiment_inputs": False,
    }
    write_json(args.root / "dataset_manifest.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
