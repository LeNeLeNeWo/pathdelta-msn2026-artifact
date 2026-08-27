"""Textual, structural, and semantic blast-radius metrics for v8.

These metrics compare immutable pre/post artifacts. They never infer or
synthesize a patch. Safety acceptance and brownfield conformance are reported
separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    BehaviorRecord,
    ChangeEnvelopeV2,
    DependencyGraph,
    FrameObligation,
    TargetObligation,
)


@dataclass(frozen=True)
class TextualFootprint:
    lines_added: int
    lines_removed: int
    lines_modified: int

    @property
    def lines_touched(self) -> int:
        return self.lines_added + self.lines_removed + self.lines_modified


@dataclass(frozen=True)
class StructuralFootprint:
    devices_touched: Tuple[str, ...]
    policy_objects_touched: Tuple[str, ...]
    bindings_changed: Tuple[str, ...]
    new_objects_created: Tuple[str, ...]
    existing_objects_reused: Tuple[str, ...]


@dataclass(frozen=True)
class SemanticFootprint:
    target_attribute_changes: Tuple[str, ...]
    non_target_attribute_changes: Tuple[str, ...]
    session_or_path_relation_changes: Tuple[str, ...]
    protected_dependency_violations: Tuple[str, ...]
    missing_post_behaviors: Tuple[str, ...]

    @property
    def semantic_delta_size(self) -> int:
        """Cardinality of unique changed semantic/protection atoms.

        This scalar deliberately assigns no domain-specific severity weights.
        Its components must be reported alongside it.
        """

        return len(
            set(self.target_attribute_changes)
            | set(self.non_target_attribute_changes)
            | set(self.session_or_path_relation_changes)
            | set(self.protected_dependency_violations)
            | set(self.missing_post_behaviors)
        )


@dataclass(frozen=True)
class ComplianceResult:
    goal_success: bool
    collateral_change: bool
    semantic_frame_preserved: bool
    dependency_frame_preserved: bool
    hard_footprint_preserved: bool
    envelope_compliance: bool
    target_failures: Tuple[str, ...]
    frame_failures: Tuple[str, ...]
    footprint_failures: Tuple[str, ...]


@dataclass(frozen=True)
class BlastRadiusReport:
    textual: TextualFootprint
    structural: StructuralFootprint
    semantic: SemanticFootprint
    compliance: ComplianceResult

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["textual"]["lines_touched"] = self.textual.lines_touched
        payload["semantic"]["semantic_delta_size"] = self.semantic.semantic_delta_size
        return payload


def textual_footprint(before: str, after: str) -> TextualFootprint:
    # Footprint authorization counts operational configuration commands, not
    # formatting-only blank lines or comment separators. Public brownfield
    # corpora often contain long runs of blank lines; counting their movement
    # made a four-command local fork appear to touch 15-22 lines.
    def operational_lines(text: str) -> List[str]:
        return [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("!", "#"))
        ]

    matcher = SequenceMatcher(a=operational_lines(before), b=operational_lines(after), autojunk=False)
    added = removed = modified = 0
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "insert":
            added += right_end - right_start
        elif tag == "delete":
            removed += left_end - left_start
        elif tag == "replace":
            left_count = left_end - left_start
            right_count = right_end - right_start
            modified += min(left_count, right_count)
            removed += max(0, left_count - right_count)
            added += max(0, right_count - left_count)
    return TextualFootprint(added, removed, modified)


def _edge_set(graph: DependencyGraph) -> Set[Tuple[str, str]]:
    return {(source, target) for source, targets in graph.edges.items() for target in targets}


def structural_footprint(
    before_configs: Mapping[str, str],
    after_configs: Mapping[str, str],
    before_graph: DependencyGraph,
    after_graph: DependencyGraph,
) -> StructuralFootprint:
    devices = tuple(
        sorted(
            device
            for device in set(before_configs) | set(after_configs)
            if before_configs.get(device) != after_configs.get(device)
        )
    )
    before_nodes, after_nodes = before_graph.nodes, after_graph.nodes
    new_objects = set(after_nodes) - set(before_nodes)
    removed_objects = set(before_nodes) - set(after_nodes)
    before_edges, after_edges = _edge_set(before_graph), _edge_set(after_graph)
    changed_edges = before_edges ^ after_edges
    touched = set(new_objects) | set(removed_objects)
    touched.update(source for edge in changed_edges for source in edge)

    # Existing object reuse is a newly introduced dependency edge whose target
    # object existed before. This is a measured outcome, not a preference gate.
    reused = {
        target
        for source, target in after_edges - before_edges
        if target in before_nodes and before_nodes[target].kind not in {"neighbor", "device"}
    }
    bindings = {
        source
        for source, _ in changed_edges
        if (before_nodes.get(source) or after_nodes.get(source))
        and (before_nodes.get(source) or after_nodes.get(source)).kind == "neighbor"
    }
    policy_touched = {
        node
        for node in touched
        if (before_nodes.get(node) or after_nodes.get(node))
        and (before_nodes.get(node) or after_nodes.get(node)).kind not in {"neighbor", "device"}
    }
    return StructuralFootprint(
        devices_touched=devices,
        policy_objects_touched=tuple(sorted(policy_touched)),
        bindings_changed=tuple(sorted(bindings)),
        new_objects_created=tuple(sorted(new_objects)),
        existing_objects_reused=tuple(sorted(reused)),
    )


def _behavior_map(records: Sequence[BehaviorRecord]) -> Dict[str, BehaviorRecord]:
    return {record.behavior_id: record for record in records}


def _changed_atoms(
    before_records: Sequence[BehaviorRecord], after_records: Sequence[BehaviorRecord]
) -> Tuple[Set[str], Set[str]]:
    before, after = _behavior_map(before_records), _behavior_map(after_records)
    atoms: Set[str] = set()
    missing: Set[str] = set()
    for behavior_id, old in before.items():
        new = after.get(behavior_id)
        if new is None:
            missing.add(behavior_id)
            continue
        for dimension in set(old.attributes) | set(new.attributes):
            if old.attributes.get(dimension) != new.attributes.get(dimension):
                atoms.add(f"{behavior_id}::{dimension}")
    for behavior_id in set(after) - set(before):
        for dimension in after[behavior_id].attributes:
            atoms.add(f"{behavior_id}::{dimension}")
    return atoms, missing


def semantic_footprint(
    before_records: Sequence[BehaviorRecord],
    after_records: Sequence[BehaviorRecord],
    envelope: ChangeEnvelopeV2,
    before_graph: DependencyGraph,
    after_graph: DependencyGraph,
) -> SemanticFootprint:
    changed, missing = _changed_atoms(before_records, after_records)
    target_atoms = {f"{item.behavior_id}::{item.dimension}" for item in envelope.target_delta}
    target_changes = changed & target_atoms
    non_target_changes = changed - target_atoms
    session_path = {
        atom for atom in changed if atom.rsplit("::", 1)[-1] in {"session", "path", "preferred_exit", "next_hop"}
    }
    before_edges, after_edges = _edge_set(before_graph), _edge_set(after_graph)
    dependency_violations = {
        node
        for node in envelope.protected_dependencies
        if node not in after_graph.nodes
        or before_graph.nodes.get(node) != after_graph.nodes.get(node)
        # New consumers may safely reuse a protected object. Its own outgoing
        # definition/dependencies must remain unchanged.
        or {edge for edge in before_edges if edge[0] == node}
        != {edge for edge in after_edges if edge[0] == node}
    }
    return SemanticFootprint(
        target_attribute_changes=tuple(sorted(target_changes)),
        non_target_attribute_changes=tuple(sorted(non_target_changes)),
        session_or_path_relation_changes=tuple(sorted(session_path)),
        protected_dependency_violations=tuple(sorted(dependency_violations)),
        missing_post_behaviors=tuple(sorted(missing)),
    )


def _relation_holds(relation: str, before: Any, after: Any, desired: Any) -> bool:
    if relation == "preserve":
        return after == before
    if relation == "replace":
        return after == desired
    if relation == "add":
        return set(before or []) | set(desired or []) <= set(after or [])
    if relation == "remove":
        return not (set(desired or []) & set(after or []))
    if relation in {"preferred_exit_change", "metric_order_change"}:
        return after == desired and after != before
    raise ValueError(f"unknown relation {relation!r}")


def evaluate_compliance(
    envelope: ChangeEnvelopeV2,
    after_records: Sequence[BehaviorRecord],
    structural: StructuralFootprint,
    textual: TextualFootprint,
    semantic: SemanticFootprint,
) -> ComplianceResult:
    after = _behavior_map(after_records)
    target_failures: List[str] = []
    for obligation in envelope.target_delta:
        record = after.get(obligation.behavior_id)
        value = None if record is None else record.attributes.get(obligation.dimension)
        if not _relation_holds(obligation.relation, obligation.before, value, obligation.desired):
            target_failures.append(obligation.obligation_id)
    frame_failures: List[str] = []
    for obligation in envelope.semantic_frame:
        record = after.get(obligation.behavior_id)
        value = None if record is None else record.attributes.get(obligation.dimension)
        if not _relation_holds("preserve", obligation.before, value, None):
            frame_failures.append(obligation.obligation_id)

    budget = envelope.footprint_budget
    footprint_failures: List[str] = []
    if not set(structural.devices_touched) <= set(budget.allowed_devices):
        footprint_failures.append("device_authorization")
    if len(structural.devices_touched) > budget.max_devices_touched:
        footprint_failures.append("device_count")
    if len(structural.bindings_changed) > budget.max_bindings_changed:
        footprint_failures.append("binding_count")
    if len(structural.new_objects_created) > budget.max_new_objects:
        footprint_failures.append("new_object_count")
    if textual.lines_touched > budget.max_changed_lines:
        footprint_failures.append("changed_line_count")

    goal = not target_failures
    frame_ok = not frame_failures
    dep_ok = not semantic.protected_dependency_violations
    footprint_ok = not footprint_failures
    return ComplianceResult(
        goal_success=goal,
        collateral_change=bool(semantic.non_target_attribute_changes or semantic.missing_post_behaviors),
        semantic_frame_preserved=frame_ok,
        dependency_frame_preserved=dep_ok,
        hard_footprint_preserved=footprint_ok,
        envelope_compliance=goal and frame_ok and dep_ok and footprint_ok,
        target_failures=tuple(target_failures),
        frame_failures=tuple(frame_failures),
        footprint_failures=tuple(footprint_failures),
    )


def build_blast_radius_report(
    before_configs: Mapping[str, str],
    after_configs: Mapping[str, str],
    before_records: Sequence[BehaviorRecord],
    after_records: Sequence[BehaviorRecord],
    envelope: ChangeEnvelopeV2,
    before_graph: DependencyGraph,
    after_graph: DependencyGraph,
) -> BlastRadiusReport:
    textual_parts = [
        textual_footprint(before_configs.get(device, ""), after_configs.get(device, ""))
        for device in sorted(set(before_configs) | set(after_configs))
    ]
    textual = TextualFootprint(
        sum(part.lines_added for part in textual_parts),
        sum(part.lines_removed for part in textual_parts),
        sum(part.lines_modified for part in textual_parts),
    )
    structural = structural_footprint(before_configs, after_configs, before_graph, after_graph)
    semantic = semantic_footprint(before_records, after_records, envelope, before_graph, after_graph)
    compliance = evaluate_compliance(envelope, after_records, structural, textual, semantic)
    return BlastRadiusReport(textual, structural, semantic, compliance)
