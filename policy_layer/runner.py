from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Any
from itertools import combinations

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

from intent_layer.schema import IntentCard
from .aggregator import aggregate_intents
from .emitters import write_policy_outputs
from .mechanism_selector import decide_proto
from .models import PolicyEntry, PolicySketch, PolicySketchDiff, RolePolicy
from .topology_view import load_topology, Topology
from .registry import get_policy_builder
from .ospf_steering_resolver import resolve_ospf_steering_scope


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> Dict:
    if yaml:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_intents(path: Path) -> List[IntentCard]:
    """Load IntentCard(s) from JSON file. Uses Pydantic validation."""
    data = _read_json(path)
    intents: List[IntentCard] = []
    if isinstance(data, list):
        items = data
    else:
        items = [data]
    for item in items:
        # Directly construct Pydantic model; it handles validation and defaults
        intents.append(IntentCard(**item))
    return intents


def _load_intents_from_obj(obj: object) -> List[IntentCard]:
    """加载内存中的 IntentCard dict/list，便于测试调用。"""
    intents: List[IntentCard] = []
    if isinstance(obj, list):
        items = obj
    else:
        items = [obj]
    for item in items:
        intents.append(IntentCard(**item))
    return intents


def _topology_from_dict(data: Dict[str, object], roles: Dict[str, List[str]] | None = None) -> Topology:
    """复用 topology_view.load_topology 的核心逻辑，将 dict 构造成 Topology。"""
    nodes: Dict[str, object] = data.get("nodes", {}) or {}
    edges: List[tuple[str, str]] = []
    for lan in (data.get("lans") or {}).values():
        members = [m.get("device") for m in lan.get("members", []) if m.get("device")]
        for a, b in combinations(members, 2):
            edges.append((a, b))
    for link in data.get("links", []) or []:
        if isinstance(link, dict):
            endpoints = link.get("endpoints") or link.get("nodes") or []
            if len(endpoints) == 2:
                edges.append((endpoints[0], endpoints[1]))
        elif isinstance(link, (list, tuple)) and len(link) == 2:
            edges.append((str(link[0]), str(link[1])))
    return Topology(nodes=nodes, edges=edges, roles=roles or {})


def _build_diff(sketch: PolicySketch, policies: List[PolicyEntry]) -> PolicySketchDiff:
    before = {
        "bgp_style": sketch.bgp_style,
        "ospf_style": sketch.ospf_style,
        "risk_profile": sketch.risk_profile,
    }
    after = {
        "policies": [
            {
                "intent_id": p.intent_id,
                "type": p.type,
                "proto": p.proto,
                "mechanism": p.mechanism,
                "prefix": p.prefix,
                "affected_devices": p.affected_devices,
            }
            for p in policies
        ]
    }
    return PolicySketchDiff(before=before, after=after)


def _parse_enabled_flag(style: Dict[str, Any] | None) -> bool | None:
    """
    解析 bgp_style/ospf_style 的 enabled 字段，用于 force_proto 的显式冲突检测。

    约定：
    - 返回 True/False：表示 enabled 字段显式存在且可解析
    - 返回 None：表示字段缺失或无法解析（允许回退推断）
    """
    if not isinstance(style, dict) or "enabled" not in style:
        return None
    val = style.get("enabled")
    if isinstance(val, bool):
        return val
    if isinstance(val, int) and val in (0, 1):
        return bool(val)
    if isinstance(val, str):
        lowered = val.strip().lower()
        if lowered in {"true", "yes", "y", "1"}:
            return True
        if lowered in {"false", "no", "n", "0"}:
            return False
    return None


def _clone_sketch(sketch: PolicySketch) -> PolicySketch:
    """浅拷贝 PolicySketch，避免在 force_proto 时污染全局 sketch。"""
    return PolicySketch(
        global_=dict(sketch.global_ or {}) if isinstance(sketch.global_, dict) else {},
        bgp_style=dict(sketch.bgp_style or {}) if isinstance(sketch.bgp_style, dict) else {},
        ospf_style=dict(sketch.ospf_style or {}) if isinstance(sketch.ospf_style, dict) else {},
        risk_profile=dict(sketch.risk_profile or {}) if isinstance(sketch.risk_profile, dict) else {},
        roles=dict(sketch.roles or {}) if isinstance(sketch.roles, dict) else {},
        capabilities=dict(sketch.capabilities or {}) if isinstance(sketch.capabilities, dict) else {},
        existing_objects=dict(sketch.existing_objects or {}) if isinstance(sketch.existing_objects, dict) else {},
    )


def _decide_proto_and_reason(
    intent: IntentCard,
    sketch: PolicySketch,
    *,
    force_proto: str,
    scenario: str | None = None,
) -> tuple[str, str, PolicySketch]:
    """
    协议决策：返回 (selected_proto, reason, sketch_for_selection)。

    - selected_proto: "bgp"/"ospf"（auto 模式下按当前逻辑：BGP 优先）
    - reason: 简短可解释文本，用于 stdout 打印与 change_footprint.notes
    - sketch_for_selection: 可能被 force_proto 修改过 enabled 硬开关的副本
    """
    try:
        selected_proto, reason = decide_proto(intent, sketch, force_proto=force_proto)
    except ValueError as exc:
        raise ValueError(f"{exc} (scenario={scenario})") from exc

    # force_proto 模式下，为了让 builder 内部的 select_mechanism 也一致地选到目标协议，
    # 这里通过修改“硬开关”生成一个 sketch 副本（不污染原 sketch）。
    if force_proto == "bgp":
        s2 = _clone_sketch(sketch)
        s2.bgp_style["enabled"] = True
        s2.ospf_style["enabled"] = False
        return selected_proto, reason, s2
    if force_proto == "ospf":
        s2 = _clone_sketch(sketch)
        s2.bgp_style["enabled"] = False
        s2.ospf_style["enabled"] = True
        return selected_proto, reason, s2

    return selected_proto, reason, sketch


def _fill_change_footprint(entry: PolicyEntry) -> None:
    """
    为 PolicyEntry 填充 change_footprint，用于量化影响范围（无合成层的行数/对象统计）。
    字段示例：
      change_footprint = {
          "affected_devices_count": len(affected_devices),
          "affected_devices": [...],
          "mechanisms": [entry.mechanism],
          "exits": [...],  # 结合 preference_tiers 或 affected_devices
          "touched_edges_count": 0,
          "touched_edges": [],
          "notes": "",
      }
    """
    exits: List[str] = []
    if entry.preference_tiers:
        exits = list(entry.preference_tiers.keys())
    elif entry.affected_devices:
        exits = list(entry.affected_devices)

    # 若已生成 OSPF steering 建议，则将 touched_edges 计入 footprint（以 params 中的数据为准）。
    touched_edges: List[List[str]] = []
    steering = entry.params.get("ospf_steering") if isinstance(entry.params, dict) else None
    if isinstance(steering, dict):
        te = steering.get("touched_edges")
        if isinstance(te, list):
            touched_edges = te

    footprint: Dict[str, Any] = {
        "affected_devices_count": len(entry.affected_devices or []),
        "affected_devices": entry.affected_devices or [],
        "mechanisms": [entry.mechanism] if entry.mechanism else [],
        "exits": exits,
        "touched_edges_count": len(touched_edges),
        "touched_edges": touched_edges,
        "notes": "",
    }
    entry.change_footprint = footprint


def _fill_ospf_steering(entry: PolicyEntry, topology: Dict[str, Any]) -> None:
    """
    构造 OSPF steering 建议，输出到 entry.params/ospf_steering 与 change_footprint。
    当前为最小实现：仅在 proto=="ospf" 且存在 preference_tiers，且 scope 提供了明确 sources 时触发。
    """
    if entry.proto != "ospf":
        return
    if not entry.preference_tiers:
        return
    exits = list(entry.preference_tiers.keys())
    if not exits:
        # 兜底：若 preference_tiers 异常为空，则尽量从 affected_devices 里恢复 exits
        exits = list(entry.affected_devices or [])

    # sources 由 resolve_affect_scope(proto="ospf") 提供，并在 normalizer 写入 params["ospf_sources"]。
    # runner 不再重复推断 sources（避免任意兜底导致结果难解释/规模膨胀）。
    sources: List[str] = []
    sources_source = ""
    if isinstance(entry.params, dict):
        raw_sources = entry.params.get("ospf_sources")
        if isinstance(raw_sources, list):
            sources = [str(x) for x in raw_sources if str(x)]
        ss = entry.params.get("ospf_sources_source")
        if isinstance(ss, str):
            sources_source = ss

    if not sources:
        # sources 为空时不执行 steering（避免“硬算 nodes-exits 前 N 个”的任意兜底）
        if isinstance(entry.params, dict):
            entry.params["ospf_steering_skip_reason"] = "skip ospf_steering: no explicit sources"
        return
    steering = resolve_ospf_steering_scope(
        topology=topology,
        sources=sources,
        exits=exits,
        tiers=entry.preference_tiers,
        base_cost=10,
        penalty=20,
        budget=5,
    )

    # 补充 sources 推断来源，便于调试/论文解释（不改变顶层 key 结构）
    meta = steering.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        steering["meta"] = meta
    if sources_source:
        meta.setdefault("sources_source", sources_source)

    # sources 明确时输出 steering（即使 touched_edges 为空）
    entry.params["ospf_steering"] = steering

    # 同步更新“影响范围”：下游应可依赖 affected_devices/change_footprint 作为可信 scope。
    touched_devices: set[str] = set()
    te = steering.get("touched_edges")
    if isinstance(te, list):
        for pair in te:
            if isinstance(pair, list) and len(pair) == 2:
                touched_devices.add(str(pair[0]))
                touched_devices.add(str(pair[1]))

    # OSPF blast radius should track the concrete cost-edited devices, not all
    # candidate sources/exits. Otherwise the baseline path rewrites dozens of
    # routers even when the steering result only touches one or two edges.
    if touched_devices:
        entry.affected_devices = sorted(touched_devices)
    else:
        fallback_scope = {str(x) for x in exits if str(x)}
        if not fallback_scope:
            fallback_scope = {str(x) for x in (entry.affected_devices or []) if str(x)}
        entry.affected_devices = sorted(fallback_scope)


def run_policy_layer(scenario_root: Path, output_dir: Path | None = None, force_proto: str = "auto") -> None:
    intent_path = scenario_root / "IntentCard.json"
    topology_path = scenario_root / "topology.yaml"
    sketch_path = scenario_root / "CurrentPolicySketch.yaml"

    if not intent_path.exists():
        alt_intent = scenario_root / "intent" / "IntentCard.json"
        if alt_intent.exists():
            intent_path = alt_intent
        else:
            raise FileNotFoundError(
                f"IntentCard.json not found at {intent_path} (also checked {alt_intent}). "
                "Please run intent_layer first or place IntentCard.json under scenario_root."
            )
    if not topology_path.exists():
        raise FileNotFoundError(f"topology.yaml not found at {topology_path}")
    if not sketch_path.exists():
        raise FileNotFoundError(f"CurrentPolicySketch.yaml not found at {sketch_path}")

    intents = _load_intents(intent_path)
    sketch_data = _read_yaml(sketch_path)
    sketch = PolicySketch.from_dict(sketch_data)
    topo = load_topology(str(topology_path), roles=sketch.roles)

    groups = aggregate_intents(intents)

    # intent_id -> 协议选择说明（写入 change_footprint.notes）
    proto_notes: Dict[str, str] = {}

    policies: List[PolicyEntry] = []
    for _, intent_group in groups.items():
        for intent in intent_group:
            builder = get_policy_builder(intent.type)
            if not builder:
                raise RuntimeError(f"No policy builder registered for intent type: {intent.type}")
            # 协议决策：force_proto 的显式冲突应抛 ValueError（不包装成 RuntimeError）
            selected_proto, reason, sketch_for_select = _decide_proto_and_reason(
                intent,
                sketch,
                force_proto=force_proto,
                scenario=str(scenario_root),
            )
            try:
                entries = builder.build_policy_entries(intent, topo, sketch_for_select, proto_hint=selected_proto)
            except (ValueError, NotImplementedError) as exc:
                raise RuntimeError(f"Policy layer failed for intent {intent.intent_id}: {exc}") from exc
            for e in entries:
                print(f"[policy_layer] force_proto={force_proto} selected_proto={e.proto} reason={reason}")
                proto_notes[e.intent_id] = f"force_proto={force_proto} selected_proto={e.proto} reason={reason}"
            policies.extend(entries)

    # 读取一次拓扑并合并 roles，填充 change_footprint 和可选 OSPF steering 建议
    topology_data = _read_yaml(topology_path)
    topology_with_roles = dict(topology_data)
    topology_with_roles.setdefault("roles", sketch.roles)
    for p in policies:
        _fill_ospf_steering(p, topology=topology_with_roles)
        _fill_change_footprint(p)
        # notes：协议选择说明 + sources 推断方式，便于调试与论文展示
        notes = proto_notes.get(p.intent_id, "")
        if p.proto == "ospf":
            sources_source = None
            if isinstance(p.params, dict):
                ss = p.params.get("ospf_sources_source")
                if isinstance(ss, str) and ss:
                    sources_source = ss
            steering = p.params.get("ospf_steering") if isinstance(p.params, dict) else None
            meta = steering.get("meta") if isinstance(steering, dict) else None
            if sources_source is None:
                sources_source = meta.get("sources_source") if isinstance(meta, dict) else None
            if sources_source:
                notes = (notes + "; " if notes else "") + f"ospf_sources={sources_source}"
            if isinstance(steering, dict):
                notes = (notes + "; " if notes else "") + "ospf_steering=enabled"
            else:
                skip_reason = p.params.get("ospf_steering_skip_reason") if isinstance(p.params, dict) else None
                if isinstance(skip_reason, str) and skip_reason:
                    notes = (notes + "; " if notes else "") + skip_reason
        if notes:
            p.change_footprint["notes"] = notes

    role_policy = RolePolicy(policies=policies)
    diff = _build_diff(sketch, policies)

    policy_dir = output_dir or scenario_root
    if len(intents) != 1:
        raise ValueError(
            f"write_policy_outputs requires a single intent_id, but got {len(intents)} intents in {intent_path}."
        )
    write_policy_outputs(role_policy, diff, policy_dir, intents[0].intent_id)


def run_policy(
    intent_card: object,
    topology: Dict[str, object],
    sketch: Dict[str, object],
    force_proto: str = "auto",
) -> Dict[str, object]:
    """
    测试/脚本用入口：直接用内存对象运行策略层，返回单条 PolicyEntry 的 dict 视图。
    仅用于测试，不写磁盘。
    """
    intents = _load_intents_from_obj(intent_card)
    sketch_obj = PolicySketch.from_dict(sketch)
    topo = _topology_from_dict(topology, roles=sketch_obj.roles)

    groups = aggregate_intents(intents)
    policies: List[PolicyEntry] = []
    proto_notes: Dict[str, str] = {}
    for _, intent_group in groups.items():
        for intent in intent_group:
            builder = get_policy_builder(intent.type)
            if not builder:
                raise RuntimeError(f"No policy builder registered for intent type: {intent.type}")
            selected_proto, reason, sketch_for_select = _decide_proto_and_reason(
                intent,
                sketch_obj,
                force_proto=force_proto,
                scenario=str(sketch_obj.global_.get("scenario") or "in-memory"),
            )
            entries = builder.build_policy_entries(intent, topo, sketch_for_select, proto_hint=selected_proto)
            for e in entries:
                print(f"[policy_layer] force_proto={force_proto} selected_proto={e.proto} reason={reason}")
                proto_notes[e.intent_id] = f"force_proto={force_proto} selected_proto={e.proto} reason={reason}"
            policies.extend(entries)

    if not policies:
        raise RuntimeError("No policies generated.")
    p = policies[0]
    topology_with_roles = dict(topology)
    topology_with_roles.setdefault("roles", sketch_obj.roles)
    _fill_ospf_steering(p, topology=topology_with_roles)
    _fill_change_footprint(p)
    notes = proto_notes.get(p.intent_id, "")
    if p.proto == "ospf":
        sources_source = None
        if isinstance(p.params, dict):
            ss = p.params.get("ospf_sources_source")
            if isinstance(ss, str) and ss:
                sources_source = ss
        steering = p.params.get("ospf_steering") if isinstance(p.params, dict) else None
        meta = steering.get("meta") if isinstance(steering, dict) else None
        if sources_source is None:
            sources_source = meta.get("sources_source") if isinstance(meta, dict) else None
        if sources_source:
            notes = (notes + "; " if notes else "") + f"ospf_sources={sources_source}"
        if isinstance(steering, dict):
            notes = (notes + "; " if notes else "") + "ospf_steering=enabled"
        else:
            skip_reason = p.params.get("ospf_steering_skip_reason") if isinstance(p.params, dict) else None
            if isinstance(skip_reason, str) and skip_reason:
                notes = (notes + "; " if notes else "") + skip_reason
    if notes:
        p.change_footprint["notes"] = notes
    mech_view = {"name": p.mechanism}
    mech_view.update(p.params)
    # 补充测试所需的出口集合，便于 ECMP 等检查
    if p.type == "ecmp" and "exits" not in mech_view:
        mech_view["exits"] = p.affected_devices
    role_policy_view = {
        "intent_id": p.intent_id,
        "type": p.type,
        "proto": p.proto,
        "prefix": p.prefix,
        "affected_devices": p.affected_devices,
        "affected_neighbors": p.affected_neighbors,
        "ordered_exits": p.ordered_exits,
        "pinned_exits": [p.pinned_exit] if p.pinned_exit else [],
        "avoid_exits": p.avoid_exits,
        "primary_exit": p.primary_exit,
        "backup_exit": p.backup_exit,
        "preference_tiers": p.preference_tiers,
        "change_footprint": p.change_footprint,
        "params": p.params,
        "mechanisms": [mech_view],
    }
    return role_policy_view


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PathDelta policy layer.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario-root",
        help="Path to scenario's pathdelta directory (contains IntentCard.json, topology.yaml, CurrentPolicySketch.yaml)",
    )
    group.add_argument(
        "--topology-path",
        help="Path to topology.yaml (when running single intent card)",
    )
    parser.add_argument(
        "--sketch-path",
        help="Path to CurrentPolicySketch.yaml (required if --topology-path is used)",
    )
    parser.add_argument(
        "--intent-card-path",
        help="Path to a single IntentCard.json (required if --topology-path is used)",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory to write RolePolicy/PolicySketch outputs. Defaults to scenario-root/policy or current directory for single run.",
    )
    parser.add_argument(
        "--force-proto",
        choices=["auto", "bgp", "ospf"],
        default="auto",
        help="Force protocol selection: auto/bgp/ospf (default: auto).",
    )
    args = parser.parse_args()

    try:
        if args.scenario_root:
            out_dir = Path(args.output_dir) if args.output_dir else None
            run_policy_layer(Path(args.scenario_root), output_dir=out_dir, force_proto=args.force_proto)
        else:
            if not (args.topology_path and args.sketch_path and args.intent_card_path):
                raise SystemExit("When using --topology-path, you must also provide --sketch-path and --intent-card-path.")
            topo_path = Path(args.topology_path)
            sketch_path = Path(args.sketch_path)
            intent_path = Path(args.intent_card_path)
            if not topo_path.exists():
                raise FileNotFoundError(f"topology.yaml not found: {topo_path}")
            if not sketch_path.exists():
                raise FileNotFoundError(f"CurrentPolicySketch.yaml not found: {sketch_path}")
            if not intent_path.exists():
                raise FileNotFoundError(f"IntentCard.json not found: {intent_path}")
            intent = _read_json(intent_path)
            sketch = _read_yaml(sketch_path)
            topo_data = _read_yaml(topo_path)
            rp_view = run_policy(intent_card=intent, topology=topo_data, sketch=sketch, force_proto=args.force_proto)
            # 单条运行时也输出文件，使用与 run_policy_layer 相同的命名规则
            from .models import PolicySketch, PolicyEntry, RolePolicy
            sketch_obj = PolicySketch.from_dict(sketch)
            # run_policy 返回视图，为了重用 write_policy_outputs，这里简单包装
            policy_entry = PolicyEntry(
                intent_id=rp_view.get("intent_id", "unknown"),
                type=rp_view.get("type", ""),
                proto=rp_view.get("proto", ""),
                mechanism=rp_view.get("mechanisms", [{}])[0].get("name", "none"),
                scope="prefix",
                prefix=rp_view.get("prefix"),
                ordered_exits=rp_view.get("ordered_exits"),
                pinned_exit=rp_view.get("pinned_exits")[0] if rp_view.get("pinned_exits") else None,
                avoid_exits=rp_view.get("avoid_exits"),
                preference_tiers=rp_view.get("preference_tiers"),
                affected_devices=rp_view.get("affected_devices") or [],
                affected_neighbors={},
                params=rp_view.get("params") or {},
                change_footprint=rp_view.get("change_footprint") or {},
            )
            role_policy = RolePolicy(policies=[policy_entry])
            diff = _build_diff(sketch_obj, [policy_entry])
            out_dir = Path(args.output_dir) if args.output_dir else Path(".")
            write_policy_outputs(role_policy, diff, out_dir, policy_entry.intent_id)
    except (RuntimeError, ValueError) as exc:
        print(f"Policy layer error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
