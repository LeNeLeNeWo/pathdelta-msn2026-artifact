#!/usr/bin/env python3
"""Mine fresh external Day-2 scenarios without importing Envelope code."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from experiments.msn2026_v83_external.vendor_policy_adapter import (
    boundary_witnesses,
    behavior_rows,
    configured_prefixes,
    evaluate_subject,
    find_candidate_subjects,
    parse,
    policy_for_subject,
)


DEFAULT_ROOT = PROJECT / "data/msn2026_v83_external"
SEED = 20260814


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scenario_id(source: str, origin: str, subject: str, prefix: str) -> str:
    digest = hashlib.sha256(f"v83|{SEED}|{source}|{origin}|{subject}|{prefix}".encode()).hexdigest()[:16]
    return f"ext-{source}-{digest}"


def public_prefix(index: int) -> str:
    return f"198.18.{index}.0/24"


def classify(model: Any, subject: str) -> str:
    policy = policy_for_subject(model, subject)
    terms = model.policies.get(policy, [])
    refs = [name for term in terms for name, _mode in term.prefix_lists]
    all_refs = [name for policy_terms in model.policies.values() for term in policy_terms for name, _mode in term.prefix_lists]
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


def choose_cisco_cases(root: Path, rng: random.Random, count: int) -> list[dict[str, Any]]:
    dataset = root / "sources/raw/cornetto_dataset"
    candidates = sorted(dataset.glob("scenario-*/final_configs/configs/*.cfg"))
    rng.shuffle(candidates)
    selected: list[dict[str, Any]] = []
    used_cornetto_scenarios: set[str] = set()
    for path in candidates:
        source_scenario = path.parents[2].name
        if source_scenario in used_cornetto_scenarios or path.stat().st_size > 100_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        model = parse(text)
        inbound = find_candidate_subjects(model, "in")
        if not inbound:
            continue
        prefixes = configured_prefixes(model)
        rng.shuffle(inbound)
        chosen = None
        for subject in inbound:
            permitted = []
            for prefix in prefixes:
                result = evaluate_subject(model, subject, prefix)
                if result and result["decision"] == "permit" and result["local_pref"] is not None:
                    permitted.append((prefix, result))
            if len(permitted) < 2:
                continue
            prefix, result = rng.choice(permitted)
            chosen = (subject, prefix, result)
            break
        if chosen is None:
            continue
        subject, prefix, before = chosen
        desired = int(before["local_pref"]) + 50
        selected.append({
            "source_group": "cornetto",
            "vendor": model.vendor,
            "origin": str(path.relative_to(root / "sources/raw")),
            "source_scenario": source_scenario,
            "text": text,
            "device": model.hostname,
            "subject": subject,
            "policy": policy_for_subject(model, subject),
            "target_prefix": prefix,
            "dimension": "local_pref",
            "desired": desired,
            "before": before,
            "family": classify(model, subject),
        })
        used_cornetto_scenarios.add(source_scenario)
        if len(selected) == count:
            break
    if len(selected) != count:
        raise RuntimeError(f"mined only {len(selected)} of {count} Cornetto cases")
    return selected


def choose_batfish_cases(root: Path) -> list[dict[str, Any]]:
    source = root / "sources/raw/test_pyramid/snapshot/configs"
    cases: list[dict[str, Any]] = []
    # Four Arista export-policy changes, one per existing aggregate and device.
    for filename in ("bl01.cfg", "bl02.cfg"):
        path = source / filename
        text = path.read_text(encoding="utf-8")
        model = parse(text)
        subject = find_candidate_subjects(model, "out")[0]
        for prefix in ("10.100.0.0/16", "10.200.0.0/16"):
            before = evaluate_subject(model, subject, prefix)
            desired = sorted(set((before or {}).get("communities", [])) | {"65000:8314"})
            cases.append({
                "source_group": "batfish_official",
                "vendor": model.vendor,
                "origin": str(path.relative_to(root / "sources/raw")),
                "source_scenario": "test-pyramid",
                "text": text,
                "device": model.hostname,
                "subject": subject,
                "policy": policy_for_subject(model, subject),
                "target_prefix": prefix,
                "dimension": "communities",
                "desired": desired,
                "before": before,
                "family": "community_composition",
            })
    # Twenty-eight Junos import-policy changes on distinct public prefixes.
    index = 1
    for filename in ("bor01.cfg", "bor02.cfg"):
        path = source / filename
        text = path.read_text(encoding="utf-8")
        model = parse(text)
        subject = "ISP@import"
        if subject not in model.bindings:
            raise RuntimeError(f"missing Junos import policy in {path}")
        for _ in range(14):
            prefix = public_prefix(index)
            index += 1
            before = evaluate_subject(model, subject, prefix)
            cases.append({
                "source_group": "batfish_official",
                "vendor": model.vendor,
                "origin": str(path.relative_to(root / "sources/raw")),
                "source_scenario": "test-pyramid",
                "text": text,
                "device": model.hostname,
                "subject": subject,
                "policy": policy_for_subject(model, subject),
                "target_prefix": prefix,
                "dimension": "local_pref",
                "desired": int((before or {}).get("local_pref") or 100) + 50,
                "before": before,
                "family": "ordered_policy",
            })
    if len(cases) != 32:
        raise AssertionError(len(cases))
    return cases


def build_case(root: Path, case: dict[str, Any], index: int) -> dict[str, Any]:
    sid = scenario_id(case["source_group"], case["origin"], case["subject"], case["target_prefix"])
    public = root / "public/scenarios" / sid
    sealed = root / "sealed/oracles" / sid
    config_name = f"{case['device']}.conf"
    config_path = public / "baseline/configs" / config_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(case["text"], encoding="utf-8")

    model = parse(case["text"])
    witnesses = boundary_witnesses(model, limit=48)
    if case["target_prefix"] not in witnesses:
        witnesses.append(case["target_prefix"])
    full_rows = behavior_rows(
        case["device"], case["text"], case["subject"], witnesses,
        "external_active_policy_observation_v1",
    )
    target_id = f"{case['device']}|{case['subject']}|{ipaddress.ip_network(case['target_prefix'], strict=False)}"
    target_rows = [row for row in full_rows if row["behavior_id"] == target_id]
    if len(target_rows) != 1:
        raise RuntimeError(f"target observation missing in {sid}")
    non_target = [row for row in full_rows if row["behavior_id"] != target_id]
    passive_rows = target_rows + non_target[::4][:8]
    active_rows = [row for row in full_rows if row not in passive_rows]

    raw_subject = case["subject"].replace("@in", " inbound").replace("@out", " outbound").replace("@import", " import").replace("@export", " export")
    if case["dimension"] == "local_pref":
        raw = (
            f"On {case['device']}, set local preference {case['desired']} for {case['target_prefix']} "
            f"received through {raw_subject}. Preserve unrelated routing behavior and existing operational conventions."
        )
    else:
        raw = (
            f"On {case['device']}, add BGP community 65000:8314 only to {case['target_prefix']} "
            f"advertised through {raw_subject}. Preserve unrelated routing behavior and existing operational conventions."
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
        "changes": [{"dimension": case["dimension"], "relation": "replace", "desired": case["desired"]}],
    }
    metadata = {
        "scenario_id": sid,
        "ordinal": index,
        "source_group": case["source_group"],
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
    }
    write_json(public / "intent.json", intent)
    write_json(public / "metadata.json", metadata)
    write_json(public / "passive_observations.json", passive_rows)
    write_json(sealed / "active_observations.json", active_rows)
    write_json(sealed / "complete_pre_observations.json", full_rows)
    write_json(sealed / "target_before.json", target_rows[0])
    return metadata


def tree_hash(root: Path) -> tuple[int, str]:
    aggregate = hashlib.sha256()
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        aggregate.update(relative.encode())
        aggregate.update(b"\0")
        aggregate.update(sha256(path).encode())
        aggregate.update(b"\n")
        count += 1
    return count, aggregate.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    if args.seed != SEED:
        raise ValueError(f"frozen seed is {SEED}")
    rng = random.Random(args.seed)
    for relative in ("public", "sealed"):
        target = args.root / relative
        if target.exists():
            shutil.rmtree(target)
    cases = choose_cisco_cases(args.root, rng, 64) + choose_batfish_cases(args.root)
    metadata = [build_case(args.root, case, index + 1) for index, case in enumerate(cases)]
    write_json(args.root / "scenario_index.json", metadata)
    public_count, public_hash = tree_hash(args.root / "public")
    sealed_count, sealed_hash = tree_hash(args.root / "sealed")
    manifest = {
        "schema_version": "msn2026-v83-external-scenarios-1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "scenario_count": len(metadata),
        "source_groups": {name: sum(row["source_group"] == name for row in metadata) for name in sorted({row["source_group"] for row in metadata})},
        "vendors": {name: sum(row["vendor"] == name for row in metadata) for name in sorted({row["vendor"] for row in metadata})},
        "families": {name: sum(row["family"] == name for row in metadata) for name in sorted({row["family"] for row in metadata})},
        "public_file_count": public_count,
        "public_tree_sha256": public_hash,
        "sealed_file_count": sealed_count,
        "sealed_tree_sha256": sealed_hash,
        "protocol_freeze_sha256": sha256(PROJECT / "experiments/msn2026_v83_external/protocol_freeze.json"),
        "source_manifest_sha256": sha256(args.root / "source_manifest.json"),
        "legacy_experiment_inputs": False,
    }
    write_json(args.root / "dataset_manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
