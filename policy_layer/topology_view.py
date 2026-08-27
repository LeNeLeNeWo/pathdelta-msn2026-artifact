from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

from intent_layer.schema import IntentCard
from .models import AffectScope
from .ospf_steering_resolver import resolve_ospf_steering_scope

NodeInfo = Dict[str, object]
Link = Tuple[str, str]


def _read_yaml(path: Path) -> Dict:
    if yaml:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return json.loads(path.read_text(encoding="utf-8"))


class Topology:
    def __init__(self, nodes: Dict[str, NodeInfo], edges: List[Link], roles: Optional[Dict[str, List[str]]] = None) -> None:
        self.nodes = nodes
        self.edges = edges
        self.roles = roles or {}
        self._neighbor_cache: Dict[str, List[str]] = {}

    def neighbors(self, node: str) -> List[str]:
        if node in self._neighbor_cache:
            return self._neighbor_cache[node]
        neigh: Set[str] = set()
        for a, b in self.edges:
            if a == node:
                neigh.add(b)
            elif b == node:
                neigh.add(a)
        res = sorted(neigh)
        self._neighbor_cache[node] = res
        return res

    def get_asn(self, node: str) -> Optional[int]:
        meta = self.nodes.get(node, {})
        asn = meta.get("asn")
        if isinstance(asn, int):
            return asn
        return None

    def get_role(self, node: str) -> Optional[str]:
        if node in set(self.roles.get("edge_routers", [])):
            return "edge"
        if node in set(self.roles.get("core_routers", [])):
            return "core"
        if node in set(self.roles.get("access_routers", []) + self.roles.get("acc_routers", [])):
            return "access"
        if node in set(self.roles.get("route_reflectors", []) + self.roles.get("rr_routers", [])):
            return "rr"
        return None

    def get_route_reflectors(self) -> List[str]:
        """Get route reflector routers from topology roles."""
        rrs = self.roles.get("route_reflectors", []) or self.roles.get("rr_routers", [])
        return sorted({str(x) for x in rrs if str(x)})

    def get_core_routers(self) -> List[str]:
        """Get core routers from topology roles."""
        cores = self.roles.get("core_routers", [])
        return sorted({str(x) for x in cores if str(x)})

    def get_access_routers(self) -> List[str]:
        """Get access/ingress routers from topology roles."""
        access = self.roles.get("access_routers", []) or self.roles.get("acc_routers", [])
        return sorted({str(x) for x in access if str(x)})

    def get_exits(self, src_as: Optional[int] = None) -> List[str]:
        exits: List[str] = []
        edge_candidates = self.roles.get("edge_routers") or []
        if src_as is None:
            # src_as 为空时，不允许退化为“exits = 所有 nodes”（会导致 scope/tiers/steering 全网化）
            if edge_candidates:
                return sorted({str(x) for x in edge_candidates if str(x)})
            raise ValueError(
                "src_as is None 且 roles.edge_routers 缺失，会导致 exits=all nodes；"
                "请在 IntentCard 提供 src_as 或在 CurrentPolicySketch/topology roles 提供 edge_routers"
            )
        candidates = edge_candidates if edge_candidates else list(self.nodes.keys())
        for n in candidates:
            if src_as is None:
                exits.append(n)
                continue
            asn = self.get_asn(n)
            if asn is None:
                continue  # skip nodes without ASN when filtering by src_as
            if asn == src_as:
                exits.append(n)
        # 如果 roles 未提供 edge_routers，且 src_as 有值，尝试推断：与其他 ASN 相邻的节点作为出口
        if src_as is not None and not edge_candidates:
            inferred: List[str] = []
            for n in self.nodes.keys():
                asn = self.get_asn(n)
                if asn != src_as:
                    continue
                for nbr in self.neighbors(n):
                    asn_nbr = self.get_asn(nbr)
                    if asn_nbr is not None and asn_nbr != src_as:
                        inferred.append(n)
                        break
            if inferred:
                exits = inferred
            else:
                # 弱退化：若仍无法推断，则返回同 ASN 的节点集合
                exits = [n for n in self.nodes.keys() if self.get_asn(n) == src_as]
        return sorted(set(exits))

    def set_roles(self, roles: Dict[str, List[str]]) -> None:
        self.roles = roles or {}
        self._neighbor_cache.clear()


def load_topology(topology_path: str, roles: Optional[Dict[str, List[str]]] = None) -> Topology:
    data = _read_yaml(Path(topology_path))
    nodes: Dict[str, NodeInfo] = data.get("nodes", {}) or {}
    edges: List[Link] = []
    # LAN-style fanout
    for lan in (data.get("lans") or {}).values():
        members = [m.get("device") for m in lan.get("members", []) if m.get("device")]
        for a, b in combinations(members, 2):
            edges.append((a, b))
    # Explicit links
    for link in data.get("links", []) or []:
        if isinstance(link, dict):
            endpoints = link.get("endpoints") or link.get("nodes") or []
            if len(endpoints) == 2:
                edges.append((endpoints[0], endpoints[1]))
        elif isinstance(link, (list, tuple)) and len(link) == 2:
            edges.append((str(link[0]), str(link[1])))
    topo = Topology(nodes=nodes, edges=edges, roles=roles)
    return topo


def _neighbors_for(devs: List[str], topo: Topology) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    for d in devs:
        mapping[d] = topo.neighbors(d)
    return mapping


def _build_topology_dict(topo: Topology) -> Dict[str, Any]:
    """
    Build a topology dict suitable for ospf_steering_resolver.
    Converts Topology object to the dict format expected by resolve_ospf_steering_scope.
    """
    # Build lans from edges (group edges by common nodes)
    lans: Dict[str, Dict[str, Any]] = {}
    lan_idx = 0
    
    # For simplicity, create a LAN for each edge
    for a, b in topo.edges:
        lan_name = f"L{lan_idx}"
        lans[lan_name] = {
            "members": [{"device": a}, {"device": b}]
        }
        lan_idx += 1
    
    return {
        "nodes": dict(topo.nodes),
        "lans": lans,
        "roles": dict(topo.roles) if topo.roles else {},
    }


def _build_initial_tiers(intent: IntentCard, exits: List[str]) -> Dict[str, int]:
    """
    Build initial preference tiers from intent for OSPF steering.
    This is a simplified version; the full tier calculation is done by preference_tiers module.
    """
    tiers: Dict[str, int] = {}
    intent_type = intent.type
    
    if intent_type == "prefer_with_backup":
        if intent.primary_exit:
            tiers[intent.primary_exit] = 2
        if intent.backup_exit:
            tiers[intent.backup_exit] = 1
        for e in exits:
            tiers.setdefault(e, 0)
    
    elif intent_type == "ordered_preference":
        ordered = intent.normalized_ordered_exits()
        for i, e in enumerate(ordered):
            tiers[e] = len(ordered) - i
        for e in exits:
            tiers.setdefault(e, 0)
    
    elif intent_type == "pin_to_exit":
        if intent.pinned_exit:
            tiers[intent.pinned_exit] = 2
        for e in exits:
            tiers.setdefault(e, 0)
    
    elif intent_type == "avoid_exit":
        avoid_list = intent.normalized_avoid_exits()
        for a in avoid_list:
            tiers[a] = -1
        for e in exits:
            tiers.setdefault(e, 1)
    
    elif intent_type == "path_migration":
        new_exit = getattr(intent, "new_exit", None)
        old_exits = getattr(intent, "old_exits", None) or []
        mode = getattr(intent, "mode", "soft")
        
        if new_exit:
            tiers[new_exit] = 3
        for old in old_exits:
            if mode == "hard":
                tiers[old] = -1
            else:
                tiers[old] = 1
        for e in exits:
            tiers.setdefault(e, 0)
    
    elif intent_type == "ecmp":
        # All exits have equal preference
        for e in exits:
            tiers[e] = 1
    
    else:
        # Default: all exits equal
        for e in exits:
            tiers[e] = 0
    
    return tiers


def resolve_affect_scope(
    intent: IntentCard, 
    topo: Topology, 
    proto: Optional[str] = None,
    sketch: Optional[Dict] = None,
) -> AffectScope:
    """
    Unified entry point for scope resolution.
    
    For BGP: Role-Based Filtering
      - Select Ingress (access/source routers)
      - Select Route Reflectors (if any)
      - Select specific Exits involved in intent
      - EXCLUDE Core routers and unrelated Access routers
    
    For OSPF: Budget-Aware Steering
      - Integrate ospf_steering_resolver
      - Return touched_edges for cost modifications
    
    Args:
        intent: The IntentCard to resolve scope for
        topo: The network topology
        proto: Protocol hint ("bgp", "ospf", or None for auto-detection)
        sketch: Optional PolicySketch dict for additional configuration
    
    Returns:
        AffectScope with affected_devices, exits, sources, and for OSPF: touched_edges, cost_overrides
    """
    if intent.scope and intent.scope != "prefix":
        raise NotImplementedError("Non-prefix scope is not supported yet.")

    def _infer_default_src_as() -> Optional[int]:
        """
        intent.src_as 缺失时的保守推断（用于 exits 计算）：
        1) 优先从 topology.roles 中可能存在的数值字段推断（例如 core_as/local_as 等）；
        2) 否则从 topology.nodes[*].asn 的统计中取“出现次数最多”的 ASN（常见为单 AS 内部网）。
        """
        roles = topo.roles if isinstance(getattr(topo, "roles", None), dict) else {}
        if isinstance(roles, dict):
            for key in ("core_as", "core_asn", "local_as", "local_asn", "src_as", "asn", "internal_as", "internal_asn"):
                v = roles.get(key)
                if isinstance(v, int):
                    return v
                if isinstance(v, str) and v.strip().isdigit():
                    return int(v.strip())

        counts: Dict[int, int] = {}
        for n in topo.nodes.keys():
            asn = topo.get_asn(n)
            if isinstance(asn, int):
                counts[asn] = counts.get(asn, 0) + 1
        if not counts:
            return None
        # 稳定选择：出现次数最多，其次取较小 ASN（避免随机性）
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def _require_src_as_for_exits() -> int:
        """
        ecmp/pin_to_exit/avoid_exit 需要“内部 AS”来计算 exits。
        若 intent.src_as 缺失，则尝试推断；仍失败则抛 ValueError（带 intent 上下文）。
        """
        src_as = getattr(intent, "src_as", None)
        if isinstance(src_as, int):
            return src_as
        inferred = _infer_default_src_as()
        if inferred is None:
            raise ValueError(
                "Cannot infer src_as for exit selection. "
                f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} prefix={getattr(intent, 'prefix', None)}; "
                "intent.src_as is None and topology roles/nodes provide no usable ASN hints. "
                "Please set IntentCard.src_as or provide roles.edge_routers with ASN-able nodes."
            )
        return inferred

    def _infer_sources(exits: List[str]) -> tuple[List[str], str]:
        """
        sources 必须是“明确且可解释”的集合：
        - 优先：topology.roles.access_routers / acc_routers
        - 启发式：从 topology 图结构推断“接入侧节点”（不做“全量内部节点”兜底）
          * 优先选择“接入路由器”：自身非 exit，且邻居中存在内部 leaf 节点（degree<=1）的节点
          * 若无法推断接入路由器，则退化为内部 leaf 节点集合（degree<=1 且非 exit）
        - 若仍无法得到：sources=[]（将导致上层跳过 ospf_steering）
        """
        roles = topo.roles if isinstance(getattr(topo, "roles", None), dict) else {}
        if isinstance(roles, dict):
            role_sources = roles.get("access_routers") or roles.get("acc_routers") or []
            if isinstance(role_sources, list) and role_sources:
                return sorted({str(x) for x in role_sources if str(x)}), "roles"

        # internal ASN：优先 intent.src_as，其次从 exits 中能解析 ASN 的出口推断
        internal_asn = getattr(intent, "src_as", None)
        if internal_asn is None:
            for e in exits:
                asn = topo.get_asn(e)
                if asn is not None:
                    internal_asn = asn
                    break

        edge_router_set: set[str] = set()
        if isinstance(roles, dict):
            ers = roles.get("edge_routers") or []
            if isinstance(ers, list):
                edge_router_set = {str(x) for x in ers if str(x)}

        # For OSPF steering, non-selected edge routers are often the ingress side and
        # must remain eligible as sources. Treat all edge routers as exits only on BGP.
        exit_set = set(exits)
        if proto != "ospf":
            exit_set |= edge_router_set

        def _is_internal(n: str) -> bool:
            if internal_asn is None:
                return True
            return topo.get_asn(n) == internal_asn

        # 度数（基于 topology.edges），用于 leaf/access 启发式
        degrees: Dict[str, int] = {n: len(topo.neighbors(n)) for n in topo.nodes.keys()}

        leaf_nodes = [
            n
            for n in topo.nodes.keys()
            if _is_internal(n) and n not in exit_set and degrees.get(n, 0) <= 1
        ]
        leaf_set = set(leaf_nodes)

        access_nodes = [
            n
            for n in topo.nodes.keys()
            if _is_internal(n) and n not in exit_set and any(nb in leaf_set for nb in topo.neighbors(n))
        ]
        if access_nodes:
            return sorted(set(access_nodes)), "heuristic_access"
        if leaf_nodes:
            return sorted(set(leaf_nodes)), "heuristic_leaf"
        return [], "none"

    def _finalize_scope(exits: List[str]) -> AffectScope:
        """
        Finalize the AffectScope based on protocol.
        
        For BGP: Role-Based Filtering
          - Select Ingress (access/source routers)
          - Select Route Reflectors (if any)
          - Select specific Exits involved in intent
          - EXCLUDE Core routers and unrelated Access routers
        
        For OSPF: Budget-Aware Steering
          - Integrate ospf_steering_resolver
          - Return touched_edges for cost modifications
        """
        exits = [e for e in exits if e]
        
        # SAFETY NET: Warn if exits count exceeds threshold (Design 3.3.1)
        # This catches scope resolution bugs that would cause 40+ devices in synthesis
        MAX_EXPECTED_EXITS = 5
        if len(exits) > MAX_EXPECTED_EXITS:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                f"[SCOPE_SAFETY_NET] exits count ({len(exits)}) exceeds threshold ({MAX_EXPECTED_EXITS}). "
                f"intent_id={getattr(intent, 'intent_id', 'unknown')} intent_type={intent.type} "
                f"exits_sample={exits[:5]}... This may indicate a scope resolution bug."
            )
        sources: List[str] = []
        sources_source = ""
        touched_edges: List[Tuple[str, str]] = []
        cost_overrides: Dict[str, int] = {}
        
        if proto == "bgp":
            # BGP Role-Based Filtering (Requirements 4.2, 4.3, 4.4)
            # Get role-based routers
            route_reflectors = topo.get_route_reflectors()
            all_access_routers = topo.get_access_routers()
            core_routers_set = set(topo.get_core_routers())
            
            # Build affected devices: ONLY exits + their direct neighbors
            # DO NOT add ALL access_routers - that causes scope explosion (Design 3.3.1)
            affected_set: Set[str] = set()
            
            # Add exits (specific exits involved in intent)
            for e in exits:
                if e not in core_routers_set:
                    affected_set.add(e)
            
            # CRITICAL FIX: Only add access_routers that are direct neighbors of exits
            # This prevents scope explosion for pin_to_exit, avoid_exit, path_migration
            exit_set = set(exits)
            for acc in all_access_routers:
                if acc not in core_routers_set:
                    # Check if this access router is a neighbor of any exit
                    acc_neighbors = set(topo.neighbors(acc))
                    if acc_neighbors & exit_set:  # Intersection with exits
                        affected_set.add(acc)
            
            # Add route reflectors only if they connect to the exits
            # (Route reflectors propagate routes, so only include those in the path)
            for rr in route_reflectors:
                if rr not in core_routers_set:
                    rr_neighbors = set(topo.neighbors(rr))
                    # Include RR if it's adjacent to any exit or any already-affected device
                    if rr_neighbors & exit_set or rr_neighbors & affected_set:
                        affected_set.add(rr)
            
            # Sources: access routers that are in affected_set
            sources = [acc for acc in all_access_routers if acc in affected_set]
            if sources:
                sources_source = "roles_filtered"
            else:
                # Fallback: infer sources from topology
                inferred_sources, sources_source = _infer_sources(exits)
                for src in inferred_sources:
                    if src not in core_routers_set:
                        affected_set.add(src)
                sources = inferred_sources
            
            affected = sorted(affected_set)
            neighbors = _neighbors_for(affected, topo)
            
            return AffectScope(
                affected_devices=affected,
                affected_neighbors=neighbors,
                exits=exits,
                sources=sources,
                sources_source=sources_source,
                touched_edges=[],  # Empty for BGP (Requirement 9.3)
                cost_overrides={},
            )
        
        elif proto == "ospf":
            # OSPF Budget-Aware Steering (Requirements 4.5, 4.6)
            sources, sources_source = _infer_sources(exits)
            seen = set(exits)
            # affected_devices includes exits + sources
            affected = list(exits) + [s for s in sources if s not in seen]
            
            # Integrate OSPF steering resolver if we have sources and exits
            if sources and exits:
                # Build topology dict for ospf_steering_resolver
                topo_dict = _build_topology_dict(topo)
                
                # Build initial tiers from intent (will be refined by preference_tiers later)
                # For now, use a simple tier assignment based on intent type
                tiers = _build_initial_tiers(intent, exits)
                
                # Get OSPF steering configuration from sketch
                base_cost = 10
                penalty = 20
                budget = 5
                if sketch:
                    ospf_style = sketch.get("ospf_style", {})
                    base_cost = ospf_style.get("base_cost", 10)
                    penalty = ospf_style.get("penalty", 20)
                    budget = ospf_style.get("budget", 5)
                
                # Call OSPF steering resolver
                steering_result = resolve_ospf_steering_scope(
                    topology=topo_dict,
                    sources=sources,
                    exits=exits,
                    tiers=tiers,
                    base_cost=base_cost,
                    penalty=penalty,
                    budget=budget,
                )
                
                # Extract touched_edges and cost_overrides
                raw_touched = steering_result.get("touched_edges", [])
                touched_edges = [(str(e[0]), str(e[1])) for e in raw_touched]
                cost_overrides = steering_result.get("cost_overrides", {})
                
                # Add edge endpoints to affected devices
                for u, v in touched_edges:
                    if u not in seen:
                        affected.append(u)
                        seen.add(u)
                    if v not in seen:
                        affected.append(v)
                        seen.add(v)
            
            neighbors = _neighbors_for(affected, topo)
            
            return AffectScope(
                affected_devices=affected,
                affected_neighbors=neighbors,
                exits=exits,
                sources=sources,
                sources_source=sources_source,
                touched_edges=touched_edges,
                cost_overrides=cost_overrides,
            )
        
        else:
            # Default behavior (no specific protocol)
            sources, sources_source = _infer_sources(exits)
            seen = set(exits)
            affected = list(exits) + [s for s in sources if s not in seen]
            neighbors = _neighbors_for(affected, topo)
            
            return AffectScope(
                affected_devices=affected,
                affected_neighbors=neighbors,
                exits=exits,
                sources=sources,
                sources_source=sources_source,
                touched_edges=[],
                cost_overrides={},
            )

    intent_type = intent.type

    if intent_type == "prefer_with_backup":
        devs = [d for d in [intent.primary_exit, intent.backup_exit] if d]
        return _finalize_scope(devs)

    if intent_type == "ordered_preference":
        exits = intent.normalized_ordered_exits()
        return _finalize_scope(exits)

    if intent_type == "ospf_steering":
        exits_set: Set[str] = set(intent.normalized_ordered_exits())
        primary = getattr(intent, "primary_exit", None)
        backup = getattr(intent, "backup_exit", None)
        if primary:
            exits_set.add(primary)
        if backup:
            exits_set.add(backup)
        if not exits_set:
            raise ValueError(
                "ospf_steering requires ordered_exits, exits, or primary/backup exits. "
                f"intent_id={getattr(intent, 'intent_id', None)} prefix={getattr(intent, 'prefix', None)}"
            )
        return _finalize_scope(sorted(exits_set))

    if intent_type == "ecmp":
        candidates = intent.normalized_ordered_exits()
        if not candidates:
            src_as = _require_src_as_for_exits()
            candidates = topo.get_exits(src_as)
            if not candidates:
                raise ValueError(
                    "No exits found for given src_as; ASN missing in topology/roles. "
                    f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} prefix={getattr(intent, 'prefix', None)} src_as={src_as}"
                )
        return _finalize_scope(candidates)

    if intent_type == "pin_to_exit":
        # STRICT SCOPE: pin_to_exit only affects the pinned exit device (Design 3.3.1)
        # DO NOT call topo.get_exits() - that returns ALL edge routers (40+ devices)
        pinned = getattr(intent, "pinned_exit", None)
        if not pinned:
            raise ValueError(
                "pin_to_exit requires pinned_exit to be set. "
                f"intent_id={getattr(intent, 'intent_id', None)} prefix={getattr(intent, 'prefix', None)}"
            )
        exits = [pinned]
        return _finalize_scope(exits)

    if intent_type == "avoid_exit":
        # STRICT SCOPE: avoid_exit only affects avoid_exits + explicit fallback devices (Design 3.3.1)
        # DO NOT call topo.get_exits() - that returns ALL edge routers
        exits_set: Set[str] = set()
        
        # Add avoided exits (traffic will be steered away from these)
        avoid_list = intent.normalized_avoid_exits()
        for a in avoid_list:
            if a:
                exits_set.add(a)
        
        # Add explicit fallback exits if defined (primary/backup that will receive redirected traffic)
        primary = getattr(intent, "primary_exit", None)
        backup = getattr(intent, "backup_exit", None)
        if primary:
            exits_set.add(primary)
        if backup:
            exits_set.add(backup)
        
        if not exits_set:
            raise ValueError(
                "avoid_exit requires at least one avoid_exit or fallback exit. "
                f"intent_id={getattr(intent, 'intent_id', None)} prefix={getattr(intent, 'prefix', None)}"
            )
        
        exits = sorted(exits_set)
        return _finalize_scope(exits)

    if intent_type == "path_migration":
        # STRICT SCOPE: path_migration only affects old and new exit devices (Design 3.3.1)
        # DO NOT include all edge_routers - that causes 40+ devices in scope
        new_exit = getattr(intent, "new_exit", None)
        old_exits = getattr(intent, "old_exits", None) or []
        
        # Collect ONLY the exits explicitly involved in the migration
        exits_set: Set[str] = set()
        if new_exit:
            exits_set.add(new_exit)
        for old in old_exits:
            if old:
                exits_set.add(old)
        
        if not exits_set:
            raise ValueError(
                "path_migration requires at least new_exit or old_exits. "
                f"intent_id={getattr(intent, 'intent_id', None)} prefix={getattr(intent, 'prefix', None)}"
            )
        
        exits = sorted(exits_set)
        return _finalize_scope(exits)

    # default: fallback to all nodes
    all_nodes = sorted(topo.nodes.keys())
    return _finalize_scope(all_nodes)
