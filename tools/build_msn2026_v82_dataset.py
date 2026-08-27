#!/usr/bin/env python3
"""Build the independently generated, blinded v8.2 benchmark.

This file intentionally does not import any PathDelta/Envelope module.
Candidate construction and oracle observations are driven only by frozen
scenario specifications and mutation classes.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import random
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from experiments.msn2026_v82_large_scale.v82_policy_evaluator import evaluate_record

DEFAULT_ROOT = PROJECT / "data/msn2026_v82_large_scale_r3"
SEED = 20260813
FAMILIES = (
    "shared_route_map",
    "shared_prefix_list",
    "overlap_precedence",
    "call_continue",
    "community_composition",
    "reuse_legacy_style",
    "multi_device_dependency",
    "target_exclusive_hidden_fec",
)
SOURCES = (
    {
        "source_id": "frr",
        "repository": "https://github.com/FRRouting/frr",
        "commit": "644ddec30de401fd275d09f30c69ed3f1bd8e350",
        "license": "GPL-2.0-or-later/per-file SPDX",
        "files": ("COPYING", "tests/topotests/bgp_route_map_comm_list/r1/frr.conf"),
        "style": {"step": 5, "rm_prefix": "RM_PUBLIC_", "pl_prefix": "PL_PUBLIC_"},
    },
    {
        "source_id": "kathara_labs",
        "repository": "https://github.com/KatharaFramework/Kathara-Labs",
        "commit": "87fbf87e009938a4a62a696a87a730966fd35bff",
        "license": "GPL-3.0",
        "files": ("LICENSE", "exam-labs/2022-01-14-alien/lab/as1/etc/frr/frr.conf"),
        "style": {"step": 20, "rm_prefix": "RM_K_", "pl_prefix": "PL_K_"},
    },
    {
        "source_id": "containerlab",
        "repository": "https://github.com/srl-labs/containerlab",
        "commit": "021d88c20307a888c81afe63e31c96dd10cf820d",
        "license": "BSD-3-Clause",
        "files": ("LICENSE", "lab-examples/frr01/router1/frr.conf"),
        "style": {"step": 10, "rm_prefix": "RM_CLAB_", "pl_prefix": "PL_CLAB_"},
    },
    {
        "source_id": "synthetic",
        "repository": None,
        "commit": None,
        "license": "project-generated",
        "files": (),
        "style": {"step": 7, "rm_prefix": "legacyRm_", "pl_prefix": "legacyPl_"},
    },
)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def download_sources(root: Path) -> list[Dict[str, Any]]:
    records = []
    for source in SOURCES:
        files = []
        for relative in source["files"]:
            repo = source["repository"].removeprefix("https://github.com/").rstrip("/")
            url = f"https://raw.githubusercontent.com/{repo}/{source['commit']}/{relative}"
            request = urllib.request.Request(url, headers={"User-Agent": "PathDelta-v82-freeze"})
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
            destination = root / "sources" / source["source_id"] / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            files.append({"path": relative, "url": url, "bytes": len(data), "sha256": sha_bytes(data)})
        records.append({**source, "downloaded_files": files})
    return records


def net(base: int, variant: int, offset: int) -> str:
    return f"10.{(base * 17 + variant * 3 + offset) % 240}.0.0/24"


def rec(device: str, subject: str, fec: str, decision: str, lp: int | None, source: str) -> Dict[str, Any]:
    return {
        "behavior_id": f"{device}|{subject}|{fec}",
        "device": device,
        "subject": subject,
        "fec": fec,
        "attributes": {"decision": decision, "local_pref": lp, "session": "established"},
        "source": source,
    }


def changed(rows: Iterable[Mapping[str, Any]], updates: Mapping[str, Mapping[str, Any]], source: str) -> list[Dict[str, Any]]:
    output = json.loads(json.dumps(list(rows)))
    for row in output:
        row["attributes"].update(updates.get(row["behavior_id"], {}))
        row["source"] = source
    return output


def scenario_spec(family_index: int, variant: int, source: Mapping[str, Any], rng: random.Random) -> Dict[str, Any]:
    family = FAMILIES[family_index]
    style = source["style"]
    step = int(style["step"])
    if source["source_id"] == "synthetic":
        step = rng.choice((7, 11, 13))
    suffix = f"{family_index:02d}{variant:02d}"
    device = f"edge-{source['source_id']}-{suffix}"
    aux = f"rr-{source['source_id']}-{suffix}"
    target = f"203.0.{family_index * 12 + variant}.0/24"
    control = net(family_index, variant, 1)
    hidden = net(family_index, variant, 2)
    opaque = f"100.64.{family_index * 12 + variant}.0/24"
    desired = 220 + 10 * (variant % 6)
    rm = f"{style['rm_prefix']}{suffix}_IN"
    rm_a = f"{style['rm_prefix']}{suffix}_A"
    rm_b = f"{style['rm_prefix']}{suffix}_B"
    child = f"{style['rm_prefix']}{suffix}_CHILD"
    reuse = f"{style['rm_prefix']}{suffix}_REUSE"
    pl_all = f"{style['pl_prefix']}{suffix}_ALL"
    pl_target = f"{style['pl_prefix']}{suffix}_TARGET"
    pl_shared = f"{style['pl_prefix']}{suffix}_SHARED"
    pl_control = f"{style['pl_prefix']}{suffix}_CONTROL"
    cl_blue = f"CL_{suffix}_BLUE"
    n1, n2 = "198.51.100.1", "198.51.100.2"

    shared_families = {
        "shared_route_map", "overlap_precedence", "community_composition",
        "reuse_legacy_style", "multi_device_dependency",
    }
    binding_a = rm if family in shared_families else rm_a
    binding_b = rm if family in shared_families else rm_b
    lines = [
        "frr version 8.4", "frr defaults traditional", f"hostname {device}",
        f"! fresh source-conditioned source={source['source_id']} variant={variant}",
        "service integrated-vtysh-config", "!", "router bgp 65000",
        f" neighbor {n1} remote-as 65101", f" neighbor {n2} remote-as 65102",
        f" neighbor {n1} route-map {binding_a} in", f" neighbor {n2} route-map {binding_b} in", "!",
        f"ip prefix-list {pl_all} seq {step} permit 0.0.0.0/0 le 32",
        f"ip prefix-list {pl_target} seq {step} permit {target}",
        f"ip prefix-list {pl_control} seq {step} permit {control}",
    ]
    base_target_decision, base_target_lp = "permit", 100
    base_control_lp = 100
    protected_object = rm
    if family == "shared_prefix_list":
        base_target_decision, base_target_lp = "implicit-deny", None
        base_control_lp = 150
        desired = 150
        lines += [
            f"ip prefix-list {pl_shared} seq {step} permit {control}",
            f"route-map {rm_a} permit {step}", f" match ip address prefix-list {pl_shared}", " set local-preference 150",
            f"route-map {rm_b} permit {step}", f" match ip address prefix-list {pl_shared}", " set local-preference 150",
        ]
        protected_object = pl_shared
    elif family == "call_continue":
        lines += [
            f"route-map {child} permit {step}", f" match ip address prefix-list {pl_all}", " set local-preference 100",
            f"route-map {rm_a} permit {step}", f" call {child}",
            f"route-map {rm_b} permit {step}", f" call {child}",
        ]
        protected_object = child
    elif family == "target_exclusive_hidden_fec":
        base_control_lp = 120
        lines += [
            f"route-map {rm_a} permit {step}", f" match ip address prefix-list {pl_target}", " set local-preference 100",
            f"route-map {rm_a} permit {step * 2}", f" match ip address prefix-list {pl_all}", " set local-preference 120",
            f"route-map {rm_b} permit {step}", f" match ip address prefix-list {pl_all}", " set local-preference 100",
        ]
        protected_object = ""
    elif family == "overlap_precedence":
        base_control_lp = 120
        lines += [
            f"route-map {rm} permit {step}", f" match ip address prefix-list {pl_control}", " set local-preference 120",
            f"route-map {rm} permit {step * 2}", f" match ip address prefix-list {pl_all}", " set local-preference 100",
        ]
    elif family == "community_composition":
        lines += [
            f"bgp community-list standard {cl_blue} permit 65000:10",
            f"route-map {rm} permit {step}", f" match community {cl_blue}", " set local-preference 150",
            f"route-map {rm} permit {step * 2}", f" match ip address prefix-list {pl_all}", " set local-preference 100",
        ]
    else:
        lines += [f"route-map {rm} permit {step}", f" match ip address prefix-list {pl_all}", " set local-preference 100"]

    # Every baseline includes a correct dormant policy so safe reuse is a real,
    # non-generated implementation alternative.
    lines += [
        f"route-map {reuse} permit {step}", f" match ip address prefix-list {pl_target}", f" set local-preference {desired}",
    ]
    if family == "overlap_precedence":
        lines += [
            f"route-map {reuse} permit {step * 2}", f" match ip address prefix-list {pl_control}",
            " set local-preference 120", f"route-map {reuse} permit {step * 3}",
            f" match ip address prefix-list {pl_all}", " set local-preference 100",
        ]
    else:
        lines += [
            f"route-map {reuse} permit {step * 2}",
            f" match ip address prefix-list {pl_shared if family == 'shared_prefix_list' else pl_all}",
            f" set local-preference {base_control_lp}",
        ]
    lines += ["!", "line vty", "!"]
    edge_config = "\n".join(lines) + "\n"
    configs = {device: edge_config}
    if family in {"multi_device_dependency", "target_exclusive_hidden_fec"}:
        configs[aux] = (
            f"frr version 8.4\nfrr defaults traditional\nhostname {aux}\nservice integrated-vtysh-config\n!\n"
            "router bgp 65000\n neighbor 192.0.2.10 remote-as 65000\n!\nline vty\n!\n"
        )
    return {
        "scenario_id": f"v82-{family}-{source['source_id']}-{variant:02d}",
        "family": family, "source_group": source["source_id"], "variant": variant,
        "device": device, "aux_device": aux, "configs": configs, "step": step,
        "target": target, "control": control, "hidden": hidden, "opaque": opaque,
        "desired": desired, "base_target_decision": base_target_decision,
        "base_target_lp": base_target_lp, "base_control_lp": base_control_lp,
        "n1": n1, "n2": n2, "rm": rm, "rm_a": rm_a, "rm_b": rm_b,
        "child": child, "reuse": reuse, "pl_all": pl_all, "pl_target": pl_target,
        "pl_control": pl_control, "pl_shared": pl_shared, "protected_object": protected_object,
    }


def insert_before_line_vty(text: str, fragment: str) -> str:
    marker = "!\nline vty\n"
    if marker not in text:
        raise ValueError("line vty marker missing")
    return text.replace(marker, f"!\n{fragment.rstrip()}\n!\nline vty\n", 1)


def rebind(text: str, neighbor: str, old: str, new: str) -> str:
    return text.replace(f"neighbor {neighbor} route-map {old} in", f"neighbor {neighbor} route-map {new} in", 1)


def local_policy(spec: Mapping[str, Any], name: str, sequence_shift: int = 0) -> str:
    first = max(1, spec["step"] + sequence_shift)
    second = first + spec["step"]
    fallback_pl = spec["pl_shared"] if spec["family"] == "shared_prefix_list" else spec["pl_all"]
    target = (
        f"route-map {name} permit {first}\n match ip address prefix-list {spec['pl_target']}\n set local-preference {spec['desired']}\n"
    )
    if spec["family"] == "overlap_precedence":
        return (
            target
            + f"route-map {name} permit {second}\n match ip address prefix-list {spec['pl_control']}\n set local-preference 120\n"
            + f"route-map {name} permit {second + spec['step']}\n match ip address prefix-list {spec['pl_all']}\n set local-preference 100"
        )
    return target + f"route-map {name} permit {second}\n match ip address prefix-list {fallback_pl}\n set local-preference {spec['base_control_lp']}"


def candidate_configs(spec: Mapping[str, Any], kind: str) -> Dict[str, str]:
    configs = dict(spec["configs"])
    edge = spec["device"]
    text = configs[edge]
    target_root = spec["rm_a"] if spec["family"] not in {
        "shared_route_map", "overlap_precedence", "community_composition",
        "reuse_legacy_style", "multi_device_dependency",
    } else spec["rm"]
    local_a = f"{spec['rm']}_LOCAL_A"
    local_b = f"{spec['rm']}_LOCAL_B"
    if kind == "safe_local_fork":
        text = insert_before_line_vty(text, local_policy(spec, local_a))
        text = rebind(text, spec["n1"], target_root, local_a)
    elif kind == "safe_reuse":
        text = rebind(text, spec["n1"], target_root, spec["reuse"])
    elif kind == "safe_alternative":
        if spec["family"] == "target_exclusive_hidden_fec":
            old = f"route-map {spec['rm_a']} permit {spec['step']}\n match ip address prefix-list {spec['pl_target']}\n set local-preference 100"
            new = old.replace("set local-preference 100", f"set local-preference {spec['desired']}")
            text = text.replace(old, new, 1)
        else:
            text = insert_before_line_vty(text, local_policy(spec, local_b, sequence_shift=2))
            text = rebind(text, spec["n1"], target_root, local_b)
    elif kind == "unsafe_visible":
        if spec["family"] == "shared_prefix_list":
            text = text.replace(
                f"ip prefix-list {spec['pl_shared']} seq {spec['step']} permit {spec['control']}",
                f"ip prefix-list {spec['pl_shared']} seq {spec['step']} permit 0.0.0.0/0 le 32",
                1,
            )
        elif spec["family"] == "target_exclusive_hidden_fec":
            text = text.replace("set local-preference 100", f"set local-preference {spec['desired']}", 1)
            text = text.replace("set local-preference 120", f"set local-preference {spec['desired']}", 1)
        else:
            # Add a match-all high-priority clause to the shared/transitive object.
            obj = spec["protected_object"] or target_root
            fragment = f"route-map {obj} permit 1\n match ip address prefix-list {spec['pl_all']}\n set local-preference {spec['desired']}"
            text = insert_before_line_vty(text, fragment)
    elif kind == "unsafe_active_hidden":
        target_only = f"{spec['rm']}_TARGET_ONLY"
        fragment = (
            f"route-map {target_only} permit {spec['step']}\n"
            f" match ip address prefix-list {spec['pl_target']}\n"
            f" set local-preference {spec['desired']}\n"
            f"route-map {target_only} permit {spec['step'] * 2}\n"
            f" match ip address prefix-list {spec['pl_control']}\n"
            f" set local-preference {spec['base_control_lp']}"
        )
        if spec["family"] == "shared_prefix_list":
            hidden_pl = f"{spec['pl_target']}_HIDDEN"
            fragment = (
                f"ip prefix-list {hidden_pl} seq 1 permit {spec['hidden']}\n"
                + fragment
                + f"\nroute-map {target_only} permit {spec['step'] * 3}\n"
                f" match ip address prefix-list {hidden_pl}\n"
                f" set local-preference {spec['desired']}"
            )
        text = insert_before_line_vty(text, fragment)
        text = rebind(text, spec["n1"], target_root, target_only)
    elif kind == "unsafe_opaque_or_scope":
        text = insert_before_line_vty(text, local_policy(spec, local_a))
        text = rebind(text, spec["n1"], target_root, local_a)
        if spec["family"] == "target_exclusive_hidden_fec":
            aux = spec["aux_device"]
            configs[aux] = configs[aux].replace(f"hostname {aux}", f"hostname {aux}-changed", 1)
        elif spec["family"] == "shared_prefix_list":
            text = text.replace(
                f"ip prefix-list {spec['pl_shared']} seq {spec['step']} permit {spec['control']}",
                f"ip prefix-list {spec['pl_shared']} seq 1 permit {spec['opaque']}\n"
                f"ip prefix-list {spec['pl_shared']} seq {spec['step']} permit {spec['control']}",
                1,
            )
        else:
            obj = spec["protected_object"] or target_root
            opaque_pl = f"{spec['pl_target']}_OPAQUE"
            fragment = (
                f"ip prefix-list {opaque_pl} seq 1 permit {spec['opaque']}\n"
                f"route-map {obj} permit 1\n match ip address prefix-list {opaque_pl}\n"
                f" set local-preference {spec['desired'] + 50}"
            )
            text = insert_before_line_vty(text, fragment)
    else:
        raise ValueError(kind)
    configs[edge] = text
    return configs


def observations(spec: Mapping[str, Any]) -> Dict[str, Any]:
    d, n1, n2 = spec["device"], spec["n1"], spec["n2"]
    visible = [
        rec(d, n1, spec["target"], spec["base_target_decision"], spec["base_target_lp"], "visible-pre"),
        rec(d, n2, "192.0.2.0/24", "implicit-deny" if spec["family"] == "shared_prefix_list" else "permit", None if spec["family"] == "shared_prefix_list" else 100, "visible-pre"),
        rec(d, n1, spec["control"], "permit", spec["base_control_lp"], "visible-pre"),
    ]
    active = [
        rec(d, n2, spec["target"], spec["base_target_decision"], spec["base_target_lp"], "active-pre"),
        rec(d, n1, spec["hidden"], "permit" if spec["family"] != "shared_prefix_list" else "implicit-deny", spec["base_control_lp"] if spec["family"] != "shared_prefix_list" else None, "active-pre"),
        rec(d, n2, spec["control"], "permit", spec["base_control_lp"], "active-pre"),
    ]
    heldout = [
        rec(d, n2, spec["opaque"], "permit" if spec["family"] != "shared_prefix_list" else "implicit-deny", 100 if spec["family"] != "shared_prefix_list" else None, "oracle-pre")
    ]
    if spec["family"] == "target_exclusive_hidden_fec":
        heldout.append(rec(spec["aux_device"], "192.0.2.10", spec["opaque"], "permit", 100, "oracle-pre"))
    return {"visible": visible, "active": active, "heldout": heldout}


def evaluate_rows(configs: Mapping[str, str], rows: Iterable[Mapping[str, Any]], source: str) -> list[Dict[str, Any]]:
    output = json.loads(json.dumps(list(rows)))
    for row in output:
        actual = evaluate_record(configs, row)
        if actual is not None:
            row["attributes"] = actual
        row["source"] = source
    return output


def evidence_for(
    spec: Mapping[str, Any], obs: Mapping[str, Any], kind: str, configs: Mapping[str, str]
) -> Dict[str, Any]:
    visible_post = evaluate_rows(configs, obs["visible"], "independent-visible-post")
    active_post = evaluate_rows(configs, obs["active"], "independent-active-post")
    heldout_post = evaluate_rows(configs, obs["heldout"], "independent-oracle-post")
    if kind == "unsafe_opaque_or_scope" and spec["family"] == "target_exclusive_hidden_fec":
        # The out-of-scope auxiliary hostname edit resets its control-plane
        # session; this operational obligation is not a route-map evaluation.
        for row in heldout_post:
            if row["device"] == spec["aux_device"]:
                row["attributes"]["session"] = "reset"
    return {
        "visible_post": visible_post,
        "active_post": active_post,
        "heldout_post": heldout_post,
    }


def build(root: Path, seed: int) -> Dict[str, Any]:
    if root.name not in {"msn2026_v82_large_scale", "msn2026_v82_large_scale_r1", "msn2026_v82_large_scale_r2", "msn2026_v82_large_scale_r3"} or len(root.resolve().parts) < 4:
        raise ValueError(f"unsafe output root: {root}")
    if root.exists():
        raise FileExistsError(f"frozen dataset root already exists: {root}")
    root.mkdir(parents=True)
    rng = random.Random(seed)
    source_records = download_sources(root)
    write_json(root / "source_manifest.json", source_records)
    salt = hashlib.sha256(f"v82-oracle-salt-{seed}-{rng.random()}".encode()).hexdigest()
    candidate_kinds = (
        "safe_local_fork", "safe_reuse", "safe_alternative",
        "unsafe_visible", "unsafe_active_hidden", "unsafe_opaque_or_scope",
    )
    scenario_rows = []
    oracle_index = {}
    agent_pool = []
    for family_index, family in enumerate(FAMILIES):
        for variant in range(12):
            source = source_records[(variant + family_index) % 4]
            spec = scenario_spec(family_index, variant, source, rng)
            scenario_id = spec["scenario_id"]
            public_root = root / "benchmark/scenarios" / scenario_id
            for device, text in spec["configs"].items():
                path = public_root / "baseline" / f"{device}.conf"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            obs = observations(spec)
            obs = {
                "visible": evaluate_rows(spec["configs"], obs["visible"], "visible-pre"),
                "active": evaluate_rows(spec["configs"], obs["active"], "active-pre"),
                "heldout": evaluate_rows(spec["configs"], obs["heldout"], "oracle-pre"),
            }
            intent = {
                "intent_id": f"intent-{scenario_id}",
                "raw_text": (
                    f"On {spec['device']}, set local preference {spec['desired']} only for {spec['target']} from {spec['n1']}."
                    if family != "shared_prefix_list"
                    else f"On {spec['device']}, permit {spec['target']} from {spec['n1']} with local preference {spec['desired']} only."
                ),
                "selector": {"devices": [spec["device"]], "subjects": [spec["n1"]], "fecs": [spec["target"]]},
                "changes": (
                    [{"dimension": "local_pref", "relation": "replace", "desired": spec["desired"]}]
                    if family != "shared_prefix_list"
                    else [
                        {"dimension": "decision", "relation": "replace", "desired": "permit"},
                        {"dimension": "local_pref", "relation": "replace", "desired": spec["desired"]},
                    ]
                ),
            }
            write_json(public_root / "intent.json", intent)
            write_json(public_root / "visible_pre_observations.json", obs["visible"])
            write_json(public_root / "active_pre_observations.json", obs["active"])
            public_meta = {
                "scenario_id": scenario_id, "family": family, "source_group": spec["source_group"],
                "variant": variant, "seed": seed, "candidate_count": 6,
                "source_conditioning_only": spec["source_group"] != "synthetic",
            }
            write_json(public_root / "scenario.json", public_meta)
            ids = []
            for kind_index, kind in enumerate(candidate_kinds):
                candidate_id = hashlib.sha256(f"{salt}|{scenario_id}|{kind_index}".encode()).hexdigest()[:24]
                ids.append(candidate_id)
                candidate_root = root / "benchmark/candidates" / candidate_id
                configs = candidate_configs(spec, kind)
                for device, text in configs.items():
                    path = candidate_root / "configs" / f"{device}.conf"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                write_json(candidate_root / "candidate.json", {"candidate_id": candidate_id, "scenario_id": scenario_id})
                evidence = evidence_for(spec, obs, kind, configs)
                write_json(root / "evidence" / candidate_id / "visible_post_observations.json", evidence["visible_post"])
                write_json(root / "evidence" / candidate_id / "active_post_observations.json", evidence["active_post"])
                truth = {
                    "candidate_id": candidate_id,
                    "scenario_id": scenario_id,
                    "semantically_acceptable": kind.startswith("safe_"),
                    "target_satisfied": True,
                    "collateral_semantic_change": kind.startswith("unsafe_"),
                    "safe_implementation_class": kind if kind.startswith("safe_") else None,
                    "unsafe_mutation_class": kind if kind.startswith("unsafe_") else None,
                    "ground_truth_basis": "independent frozen mutation semantics plus held-out evidence",
                }
                write_json(root / "oracle/candidates" / f"{candidate_id}.json", truth)
                write_json(root / "oracle/heldout" / f"{candidate_id}_pre.json", obs["heldout"])
                write_json(root / "oracle/heldout" / f"{candidate_id}_post.json", evidence["heldout_post"])
                oracle_index[candidate_id] = truth
            write_json(public_root / "candidate_index.json", {"candidate_ids": ids})
            scenario_rows.append(public_meta)
            agent_pool.append(public_meta)

    subset_rng = random.Random(81703)
    agent_ids = []
    rela_ids = []
    kathara_ids = []
    for family in FAMILIES:
        family_rows = [row for row in agent_pool if row["family"] == family]
        subset_rng.shuffle(family_rows)
        agent_ids.extend(row["scenario_id"] for row in family_rows[:4])
        rela_ids.extend(row["scenario_id"] for row in family_rows[4:8])
        kathara_ids.extend(row["scenario_id"] for row in family_rows[8:10])
    write_json(root / "subsets.json", {"seed": 81703, "agent": sorted(agent_ids), "rela": sorted(rela_ids), "kathara": sorted(kathara_ids)})
    write_json(root / "oracle/oracle_manifest.json", {"salt": salt, "candidate_truth": oracle_index})
    write_json(root / "scenario_catalog.json", scenario_rows)

    public_files = {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.relative_to(root).as_posix().startswith("oracle/")
        and path.name != "dataset_manifest.json"
    }
    oracle_files = {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted((root / "oracle").rglob("*")) if path.is_file()
    }
    manifest = {
        "schema_version": "msn2026.v82.dataset.1.1", "dataset_id": root.name,
        "seed": seed, "scenario_count": len(scenario_rows), "candidate_count": len(oracle_index),
        "source_groups": {source["source_id"]: 24 for source in source_records},
        "families": {family: 12 for family in FAMILIES},
        "uses_legacy_experiment_inputs": False,
        "protocol_freeze_sha256": sha_file(PROJECT / "experiments/msn2026_v82_large_scale/protocol_freeze.json"),
        "public_files_sha256": public_files,
        "sealed_oracle_files_sha256": oracle_files,
    }
    write_json(root / "dataset_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.seed), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
