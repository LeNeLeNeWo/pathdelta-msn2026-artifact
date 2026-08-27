"""General, patch-agnostic Change Envelope inference for PathDelta-Agent v8.

The module operates on a typed behavior universe and an object dependency
graph.  It deliberately does not synthesize configuration, choose an edit
strategy, or expose an expected patch.  Protocol adapters are responsible for
extracting behavior records and dependency edges; the inference algorithm is
the same for every intent family.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from math import gcd
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


RELATIONS = {"preserve", "replace", "add", "remove", "preferred_exit_change", "metric_order_change"}


@dataclass(frozen=True)
class BehaviorRecord:
    """One independently observable behavior unit in the pre-state."""

    behavior_id: str
    device: str
    subject: str
    fec: str
    attributes: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class DependencyNode:
    node_id: str
    device: str
    kind: str
    name: str
    line_count: int = 1
    affects_dimensions: Tuple[str, ...] = ()
    # Hash of the normalized object definition.  Line count and dependency
    # edges alone cannot detect value-only edits such as 100 -> 250.
    definition_sha256: str = ""


@dataclass
class DependencyGraph:
    nodes: Dict[str, DependencyNode] = field(default_factory=dict)
    # directed edge a -> b means a reads, invokes, or is bound to b
    edges: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_node(self, node: DependencyNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source].add(target)

    def closure(self, roots: Iterable[str]) -> Set[str]:
        visited: Set[str] = set()
        queue = deque(root for root in roots if root in self.nodes)
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            queue.extend(sorted(self.edges.get(node, set()) - visited))
        return visited

    def reverse_edges(self) -> Dict[str, Set[str]]:
        reverse: Dict[str, Set[str]] = defaultdict(set)
        for source, targets in self.edges.items():
            for target in targets:
                reverse[target].add(source)
        return reverse


@dataclass(frozen=True)
class GroundedSelector:
    devices: Tuple[str, ...]
    subjects: Tuple[str, ...]
    fecs: Tuple[str, ...]
    dimensions: Tuple[str, ...]
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class TargetObligation:
    obligation_id: str
    behavior_id: str
    dimension: str
    relation: str
    before: Any
    desired: Any


@dataclass(frozen=True)
class FrameObligation:
    obligation_id: str
    behavior_id: str
    dimension: str
    relation: str
    before: Any


@dataclass(frozen=True)
class FootprintBudget:
    allowed_devices: Tuple[str, ...]
    allowed_command_classes: Tuple[str, ...]
    protected_existing_objects: Tuple[str, ...]
    max_devices_touched: int
    max_bindings_changed: int
    max_existing_objects_modified: int
    max_new_objects: int
    max_changed_lines: int
    derivation: Mapping[str, Any]


@dataclass(frozen=True)
class ConformancePreferences:
    naming_families: Mapping[str, Tuple[str, ...]]
    sequence_spacing: Tuple[int, ...]
    parameter_grids: Mapping[str, Tuple[int, ...]]
    reusable_objects: Tuple[str, ...]
    structural_idioms: Tuple[str, ...]


@dataclass
class ChangeEnvelopeV2:
    schema_version: str
    derivation_version: str
    intent_id: str
    raw_intent: str
    selector: GroundedSelector
    target_delta: List[TargetObligation]
    semantic_frame: List[FrameObligation]
    dependency_closure: List[str]
    protected_dependencies: List[str]
    footprint_budget: FootprintBudget
    conformance_preferences: ConformancePreferences
    coverage: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        # JSON round-trip normalizes tuples emitted by frozen dataclasses to
        # arrays, so the returned object is directly schema-valid and writable.
        return json.loads(json.dumps(asdict(self), sort_keys=True, default=str))


def _stable_id(prefix: str, *parts: Any) -> str:
    material = json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:16]}"


def _canonical_fec(value: str) -> str:
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        return value.strip().lower()


def _mentioned(raw: str, value: str) -> bool:
    return value.lower() in raw.lower()


def ground_selector(
    partial_intent: Mapping[str, Any], behavior_universe: Sequence[BehaviorRecord]
) -> GroundedSelector:
    """Ground a selector against the observed entity catalog.

    Explicit selector fields take precedence. Missing entity fields are filled
    only by literal mentions of catalog entities in the raw request. There is
    no intent-family branch or inferred configuration strategy.
    """

    raw = str(partial_intent.get("raw_text", ""))
    supplied = partial_intent.get("selector") or {}
    catalog = {
        "devices": sorted({record.device for record in behavior_universe}),
        "subjects": sorted({record.subject for record in behavior_universe}),
        "fecs": sorted({_canonical_fec(record.fec) for record in behavior_universe}),
        "dimensions": sorted({key for record in behavior_universe for key in record.attributes}),
    }
    evidence: Dict[str, Any] = {"explicit": {}, "literal_catalog_mentions": {}}
    grounded: Dict[str, Tuple[str, ...]] = {}
    for field_name, candidates in catalog.items():
        explicit = tuple(str(item) for item in supplied.get(field_name, []) if str(item))
        if field_name == "fecs":
            explicit = tuple(_canonical_fec(item) for item in explicit)
        mentioned = tuple(item for item in candidates if _mentioned(raw, item))
        selected = explicit or mentioned
        grounded[field_name] = tuple(sorted(set(selected)))
        evidence["explicit"][field_name] = list(explicit)
        evidence["literal_catalog_mentions"][field_name] = list(mentioned)

    # Dimensions can also be declared by change atoms. This is generic over
    # attribute names and does not interpret a policy operation.
    changed_dimensions = tuple(
        str(change["dimension"])
        for change in partial_intent.get("changes", [])
        if isinstance(change, Mapping) and change.get("dimension")
    )
    grounded["dimensions"] = tuple(sorted(set(grounded["dimensions"] + changed_dimensions)))
    evidence["catalog_sizes"] = {key: len(value) for key, value in catalog.items()}
    return GroundedSelector(
        devices=grounded["devices"],
        subjects=grounded["subjects"],
        fecs=grounded["fecs"],
        dimensions=grounded["dimensions"],
        evidence=evidence,
    )


def _record_matches(record: BehaviorRecord, selector: GroundedSelector) -> bool:
    return bool(
        (not selector.devices or record.device in selector.devices)
        and (not selector.subjects or record.subject in selector.subjects)
        and (not selector.fecs or _canonical_fec(record.fec) in selector.fecs)
    )


def infer_target_and_frame(
    partial_intent: Mapping[str, Any],
    selector: GroundedSelector,
    behavior_universe: Sequence[BehaviorRecord],
) -> Tuple[List[TargetObligation], List[FrameObligation]]:
    changes = list(partial_intent.get("changes") or [])
    if not changes:
        raise ValueError("partial intent must declare at least one typed semantic change")
    for change in changes:
        relation = str(change.get("relation", ""))
        if relation not in RELATIONS or relation == "preserve":
            raise ValueError(f"invalid target relation: {relation!r}")
        if not change.get("dimension"):
            raise ValueError("each change atom requires a behavior dimension")

    targets: List[TargetObligation] = []
    targeted_pairs: Set[Tuple[str, str]] = set()
    for record in behavior_universe:
        if not _record_matches(record, selector):
            continue
        for change in changes:
            dimension = str(change["dimension"])
            if selector.dimensions and dimension not in selector.dimensions:
                continue
            before = record.attributes.get(dimension)
            relation = str(change["relation"])
            desired = change.get("desired")
            targets.append(
                TargetObligation(
                    obligation_id=_stable_id("target", record.behavior_id, dimension, relation, desired),
                    behavior_id=record.behavior_id,
                    dimension=dimension,
                    relation=relation,
                    before=before,
                    desired=desired,
                )
            )
            targeted_pairs.add((record.behavior_id, dimension))
    if not targets:
        raise ValueError("selector/change atoms match no observed behavior")

    frame: List[FrameObligation] = []
    for record in behavior_universe:
        for dimension, before in sorted(record.attributes.items()):
            if (record.behavior_id, dimension) in targeted_pairs:
                continue
            frame.append(
                FrameObligation(
                    obligation_id=_stable_id("frame", record.behavior_id, dimension),
                    behavior_id=record.behavior_id,
                    dimension=dimension,
                    relation="preserve",
                    before=before,
                )
            )
    return targets, frame


def _subject_node_candidates(record: BehaviorRecord, graph: DependencyGraph) -> Set[str]:
    exact = {
        node_id
        for node_id, node in graph.nodes.items()
        if node.device == record.device and (node.name == record.subject or node_id.endswith(":" + record.subject))
    }
    return exact


def infer_dependency_sets(
    targets: Sequence[TargetObligation],
    behavior_universe: Sequence[BehaviorRecord],
    graph: DependencyGraph,
) -> Tuple[Set[str], Set[str], Dict[str, Any]]:
    records = {record.behavior_id: record for record in behavior_universe}
    target_records = {target.behavior_id for target in targets}
    target_subjects = {
        (records[behavior_id].device, records[behavior_id].subject)
        for behavior_id in target_records
    }
    target_roots: Set[str] = set()
    non_target_roots: Set[str] = set()
    for record in behavior_universe:
        roots = _subject_node_candidates(record, graph)
        if record.behavior_id in target_records:
            target_roots.update(roots)
        elif (record.device, record.subject) not in target_subjects:
            non_target_roots.update(roots)
    target_closure = graph.closure(target_roots)
    non_target_closure = graph.closure(non_target_roots)
    shared = target_closure & non_target_closure

    # An object reached from a target root is protected when any direct or
    # transitive non-target behavior also depends on it. No replacement object
    # or edit strategy is proposed.
    # A selected subject/binding is an authorized change boundary even when
    # other attributes of that subject remain in the semantic frame. Protect
    # shared policy dependencies, not the selector root itself.
    protected = set(shared) - target_roots
    evidence = {
        "target_roots": sorted(target_roots),
        "non_target_roots": sorted(non_target_roots),
        "shared_dependency_nodes": sorted(shared),
        "target_closure_size": len(target_closure),
        "non_target_closure_size": len(non_target_closure),
    }
    return target_closure, protected, evidence


def _name_family(name: str) -> str:
    tokens = re.split(r"(?=[A-Z][a-z])|[_\-.]+|\d+", name)
    tokens = [token for token in tokens if token]
    if not tokens:
        return "<unclassified>"
    return tokens[0].upper() + ("_" if "_" in name else "")


def _positive_gcd(values: Sequence[int]) -> int:
    answer = 0
    for value in values:
        if value > 0:
            answer = gcd(answer, value)
    return answer


def infer_conformance(
    configs: Mapping[str, str], graph: DependencyGraph, target_closure: Set[str]
) -> ConformancePreferences:
    names: Dict[str, List[str]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind not in {"neighbor", "device"}:
            names[node.kind].append(node.name)
    naming_families = {
        kind: tuple(family for family, _ in Counter(_name_family(name) for name in values).most_common(3))
        for kind, values in sorted(names.items())
    }
    sequences: List[int] = []
    parameter_grids: Dict[str, Set[int]] = defaultdict(set)
    for text in configs.values():
        sequences.extend(int(value) for value in re.findall(r"\bseq(?:uence)?\s+(\d+)\b", text, re.I))
        sequences.extend(int(value) for value in re.findall(r"^route-map\s+\S+\s+\S+\s+(\d+)\s*$", text, re.I | re.M))
        for command, value in re.findall(r"^\s*set\s+([\w-]+)(?:\s+[\w-]+)*\s+(\d+)\s*$", text, re.I | re.M):
            parameter_grids[command.lower()].add(int(value))
    ordered = sorted(set(sequences))
    deltas = [right - left for left, right in zip(ordered, ordered[1:]) if right > left]
    spacing = tuple(sorted(set(deltas + ([_positive_gcd(deltas)] if deltas else []))))

    reusable = tuple(
        sorted(
            node_id
            for node_id, node in graph.nodes.items()
            if node_id not in target_closure and node.kind not in {"neighbor", "device"}
        )
    )
    idioms: List[str] = []
    if any("call" in text.lower() for text in configs.values()):
        idioms.append("route-map-call")
    if any("continue" in text.lower() for text in configs.values()):
        idioms.append("route-map-continue")
    if any("community" in text.lower() for text in configs.values()):
        idioms.append("community-composition")
    if len({family for families in naming_families.values() for family in families}) > 1:
        idioms.append("multiple-naming-families")
    return ConformancePreferences(
        naming_families=naming_families,
        sequence_spacing=spacing,
        parameter_grids={key: tuple(sorted(values)) for key, values in sorted(parameter_grids.items())},
        reusable_objects=reusable,
        structural_idioms=tuple(sorted(idioms)),
    )


def infer_footprint_budget(
    selector: GroundedSelector,
    targets: Sequence[TargetObligation],
    target_closure: Set[str],
    protected: Set[str],
    graph: DependencyGraph,
) -> FootprintBudget:
    target_devices = tuple(sorted(selector.devices or {graph.nodes[node].device for node in target_closure}))
    target_subjects = {target.behavior_id for target in targets}
    editable_existing = target_closure - protected
    object_kinds = {
        graph.nodes[node].kind
        for node in target_closure
        if node in graph.nodes and graph.nodes[node].kind not in {"device"}
    }
    affected_dimensions = {target.dimension for target in targets}
    effect_kinds = {
        node.kind
        for node in graph.nodes.values()
        if affected_dimensions & set(node.affects_dimensions)
    }
    allowed_classes = tuple(sorted(object_kinds | effect_kinds | {"binding"}))
    closure_lines = sum(graph.nodes[node].line_count for node in target_closure if node in graph.nodes)
    # The count limits follow selector cardinality and observed dependency
    # density. They contain no operation, object name, or expected patch.
    max_bindings = max(1, len(selector.subjects) or len(target_subjects))
    max_new_objects = max(1, len({graph.nodes[n].kind for n in protected if n in graph.nodes})) + max_bindings
    max_lines = max(1, closure_lines + 2 * max_new_objects + 2 * max_bindings)
    return FootprintBudget(
        allowed_devices=target_devices,
        allowed_command_classes=allowed_classes,
        protected_existing_objects=tuple(sorted(protected)),
        max_devices_touched=max(1, len(target_devices)),
        max_bindings_changed=max_bindings,
        max_existing_objects_modified=len(editable_existing),
        max_new_objects=max_new_objects,
        max_changed_lines=max_lines,
        derivation={
            "formula": "observed target closure line count + 2*new-object allowance + 2*binding allowance",
            "target_closure_line_count": closure_lines,
            "selector_subject_count": len(selector.subjects),
            "protected_kind_count": len({graph.nodes[n].kind for n in protected if n in graph.nodes}),
            "note": "Counts authorize blast radius; they do not choose a patch strategy.",
        },
    )


def derive_change_envelope_v2(
    partial_intent: Mapping[str, Any],
    configs: Mapping[str, str],
    behavior_universe: Sequence[BehaviorRecord],
    dependency_graph: DependencyGraph,
    *,
    behavior_universe_provenance: Mapping[str, Any],
) -> ChangeEnvelopeV2:
    selector = ground_selector(partial_intent, behavior_universe)
    targets, frame = infer_target_and_frame(partial_intent, selector, behavior_universe)
    closure, protected, dependency_evidence = infer_dependency_sets(
        targets, behavior_universe, dependency_graph
    )
    conformance = infer_conformance(configs, dependency_graph, closure)
    footprint = infer_footprint_budget(selector, targets, closure, protected, dependency_graph)
    observed_pairs = sum(len(record.attributes) for record in behavior_universe)
    return ChangeEnvelopeV2(
        schema_version="2.0.0-dev",
        derivation_version="intent-relative-envelope-0.2.1",
        intent_id=str(partial_intent.get("intent_id", "unnamed-intent")),
        raw_intent=str(partial_intent.get("raw_text", "")),
        selector=selector,
        target_delta=targets,
        semantic_frame=frame,
        dependency_closure=sorted(closure),
        protected_dependencies=sorted(protected),
        footprint_budget=footprint,
        conformance_preferences=conformance,
        coverage={
            "behavior_records": len(behavior_universe),
            "observed_attribute_pairs": observed_pairs,
            "target_obligations": len(targets),
            "frame_obligations": len(frame),
            "universe_complete": bool(behavior_universe_provenance.get("complete", False)),
            "uncovered_reason": behavior_universe_provenance.get("uncovered_reason"),
        },
        provenance={
            "configs_sha256": {
                device: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for device, text in sorted(configs.items())
            },
            "behavior_universe": dict(behavior_universe_provenance),
            "dependency_inference": dependency_evidence,
            "patch_strategy_emitted": False,
            "expected_patch_used": False,
        },
    )


_PL_RE = re.compile(r"^ip\s+prefix-list\s+(\S+)\s+seq\s+\d+\s+", re.I)
_CL_RE = re.compile(r"^(?:ip|bgp)\s+community-list(?:\s+\S+)?\s+(\S+)\s+", re.I)
_RM_RE = re.compile(r"^route-map\s+(\S+)\s+\S+\s+\d+\s*$", re.I)
_RM_MATCH_PL_RE = re.compile(r"^\s+match\s+ip\s+address\s+prefix-list\s+(.+)$", re.I)
_RM_MATCH_COMM_RE = re.compile(r"^\s+match\s+community\s+(.+)$", re.I)
_RM_CALL_RE = re.compile(r"^\s+call\s+(\S+)\s*$", re.I)
_NEIGHBOR_RM_RE = re.compile(r"^\s*neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)\s*$", re.I)


def build_frr_dependency_graph(configs: Mapping[str, str]) -> DependencyGraph:
    """Extract a typed graph for common FRR policy composition constructs."""

    graph = DependencyGraph()
    deferred_edges: List[Tuple[str, str, str, str]] = []
    for device, text in configs.items():
        current_rm: Optional[str] = None
        line_counts: Counter[str] = Counter()
        definition_lines: Dict[str, List[str]] = defaultdict(list)
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            rm = _RM_RE.match(line)
            if rm:
                current_rm = rm.group(1)
                node_id = f"{device}:route_map:{current_rm}"
                graph.add_node(DependencyNode(node_id, device, "route_map", current_rm, 1, ("decision", "local_pref", "metric", "community", "path")))
                line_counts[node_id] += 1
                definition_lines[node_id].append(line.strip())
                continue
            pl = _PL_RE.match(line)
            if pl:
                current_rm = None
                name = pl.group(1)
                node_id = f"{device}:prefix_list:{name}"
                graph.add_node(DependencyNode(node_id, device, "prefix_list", name, 1, ("decision", "path")))
                line_counts[node_id] += 1
                definition_lines[node_id].append(line.strip())
                continue
            cl = _CL_RE.match(line)
            if cl:
                current_rm = None
                name = cl.group(1)
                node_id = f"{device}:community_list:{name}"
                graph.add_node(DependencyNode(node_id, device, "community_list", name, 1, ("decision", "community", "path")))
                line_counts[node_id] += 1
                definition_lines[node_id].append(line.strip())
                continue
            binding = _NEIGHBOR_RM_RE.match(line)
            if binding:
                current_rm = None
                neighbor, route_map, direction = binding.groups()
                node_id = f"{device}:neighbor:{neighbor}"
                graph.add_node(DependencyNode(node_id, device, "neighbor", neighbor, 1, ("session", "path", "decision", "local_pref", "metric")))
                definition_lines[node_id].append(line.strip())
                deferred_edges.append((node_id, "route_map", route_map, device))
                continue
            if current_rm:
                rm_id = f"{device}:route_map:{current_rm}"
                if line.startswith(" "):
                    line_counts[rm_id] += 1
                    definition_lines[rm_id].append(line.strip())
                match_pl = _RM_MATCH_PL_RE.match(line)
                match_comm = _RM_MATCH_COMM_RE.match(line)
                call = _RM_CALL_RE.match(line)
                if match_pl:
                    deferred_edges.extend((rm_id, "prefix_list", name, device) for name in match_pl.group(1).split())
                if match_comm:
                    deferred_edges.extend((rm_id, "community_list", name, device) for name in match_comm.group(1).split())
                if call:
                    deferred_edges.append((rm_id, "route_map", call.group(1), device))
            if line and not line.startswith((" ", "!")) and not rm:
                current_rm = None

        for node_id, count in line_counts.items():
            node = graph.nodes[node_id]
            graph.nodes[node_id] = DependencyNode(
                node.node_id,
                node.device,
                node.kind,
                node.name,
                count,
                node.affects_dimensions,
                hashlib.sha256("\n".join(definition_lines[node_id]).encode("utf-8")).hexdigest(),
            )

        # Neighbor definitions have no entry in line_counts but are still
        # protected roots whose binding value must be fingerprinted.
        for node_id, lines in definition_lines.items():
            if node_id in line_counts or node_id not in graph.nodes:
                continue
            node = graph.nodes[node_id]
            graph.nodes[node_id] = DependencyNode(
                node.node_id,
                node.device,
                node.kind,
                node.name,
                node.line_count,
                node.affects_dimensions,
                hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
            )

    for source, kind, name, device in deferred_edges:
        target = f"{device}:{kind}:{name}"
        if target not in graph.nodes:
            graph.add_node(DependencyNode(target, device, kind, name, 0, ()))
        graph.add_edge(source, target)
    return graph


def handwritten_special_case_audit() -> Mapping[str, Any]:
    """Machine-readable disclosure used by the paper audit.

    The inference path has no intent-family dispatch. Protocol-specific work is
    confined to extracting a graph and behavior universe.
    """

    return {
        "intent_family_branches": 0,
        "expected_patch_templates": 0,
        "strategy_labels_emitted": 0,
        "protocol_adapters": {"frr_dependency_extractor": 1},
        "declared_relational_operators": sorted(RELATIONS),
    }


def discover_frr_fec_probes(configs: Mapping[str, str]) -> Mapping[str, Tuple[str, ...]]:
    """Derive representative FEC witnesses from configured prefix boundaries.

    The result is an observation plan, not a predicted post-state. Exact rule
    networks plus `ge`/`le` boundary representatives cover common prefix-list
    equivalence classes without using a candidate patch.
    """

    rule = re.compile(
        r"^ip\s+prefix-list\s+\S+\s+seq\s+\d+\s+(?:permit|deny)\s+(\S+)"
        r"(?:\s+ge\s+(\d+))?(?:\s+le\s+(\d+))?\s*$",
        re.I,
    )
    output: Dict[str, Set[str]] = defaultdict(set)
    for device, text in configs.items():
        for line in text.splitlines():
            match = rule.match(line.strip())
            if not match:
                continue
            prefix, ge, le = match.groups()
            try:
                network = ipaddress.ip_network(prefix, strict=False)
            except ValueError:
                continue
            output[device].add(str(network))
            for length in (ge, le):
                if length is None:
                    continue
                new_prefix = int(length)
                if network.prefixlen <= new_prefix <= network.max_prefixlen:
                    representative = (
                        network if new_prefix == network.prefixlen else next(network.subnets(new_prefix=new_prefix))
                    )
                    output[device].add(str(representative))
    return {device: tuple(sorted(prefixes)) for device, prefixes in sorted(output.items())}


def augment_behavior_universe_with_frr_probes(
    configs: Mapping[str, str],
    observed: Sequence[BehaviorRecord],
) -> Tuple[List[BehaviorRecord], Mapping[str, Any]]:
    """Actively evaluate config-derived FEC representatives for known subjects.

    This development adapter uses the small FRR evaluator. Batfish-backed
    symbolic classes are the intended formal backend. Existing observations
    remain authoritative and are never overwritten.
    """

    from experiments.msn2026_v8_day2_agent.change_envelope import evaluate_matrix, parse_config

    rows = list(observed)
    existing = {row.behavior_id for row in rows}
    configured = discover_frr_fec_probes(configs)
    added: List[str] = []
    by_device: Dict[str, List[BehaviorRecord]] = defaultdict(list)
    for row in observed:
        by_device[row.device].append(row)
    for device, device_rows in sorted(by_device.items()):
        subjects = sorted({row.subject for row in device_rows})
        # Cross known FECs with known subjects, then add configuration boundary
        # representatives. This is an explicit active query plan.
        fecs = sorted({row.fec for row in device_rows} | set(configured.get(device, ())))
        matrix = evaluate_matrix(parse_config(configs[device]), subjects, fecs)
        for subject in subjects:
            session = next(
                (row.attributes.get("session") for row in device_rows if row.subject == subject and "session" in row.attributes),
                None,
            )
            for fec in fecs:
                behavior_id = f"{device}|{subject}|{fec}"
                if behavior_id in existing:
                    continue
                attributes = dict(matrix[f"{subject}|{fec}"])
                if session is not None:
                    attributes["session"] = session
                rows.append(
                    BehaviorRecord(
                        behavior_id,
                        device,
                        subject,
                        fec,
                        attributes,
                        "active_config_boundary_probe_v1",
                    )
                )
                existing.add(behavior_id)
                added.append(behavior_id)
    return rows, {
        "backend": "development_frr_equivalence_class_probe",
        "configured_prefix_boundaries": {key: list(value) for key, value in configured.items()},
        "added_behavior_records": added,
        "candidate_patch_used": False,
    }
