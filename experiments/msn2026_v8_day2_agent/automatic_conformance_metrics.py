"""Automatic brownfield-conformance proxies, separate from safety acceptance."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    ConformancePreferences,
    DependencyGraph,
    build_frr_dependency_graph,
    infer_conformance,
)
from experiments.msn2026_v8_day2_agent.semantic_metrics import structural_footprint, textual_footprint


@dataclass(frozen=True)
class ConformanceReport:
    object_reuse_rate: float
    reused_existing_objects: int
    new_objects: int
    unnecessary_new_object_proxy: int
    naming_family_match_rate: float
    sequence_spacing_mean_deviation: float
    parameter_grid_mean_deviation: float
    local_structural_similarity: float
    config_ast_edit_distance: int
    devices_touched: int
    objects_touched: int
    lines_changed: int
    proxy_only: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _blocks(text: str) -> Dict[str, List[str]]:
    blocks: Dict[str, List[str]] = {}
    current = None
    for line in text.splitlines():
        route_map = re.match(r"^route-map\s+(\S+)\s+", line, re.I)
        prefix_list = re.match(r"^ip\s+prefix-list\s+(\S+)\s+", line, re.I)
        community = re.match(r"^(?:ip|bgp)\s+community-list(?:\s+\S+)?\s+(\S+)\s+", line, re.I)
        match = route_map or prefix_list or community
        if match:
            kind = "route_map" if route_map else "prefix_list" if prefix_list else "community_list"
            current = f"{kind}:{match.group(1)}"
            blocks.setdefault(current, []).append(line)
        elif current and line.startswith(" "):
            blocks[current].append(line)
        elif line and not line.startswith("!"):
            current = None
    return blocks


def _normalized_signature(lines: Sequence[str]) -> Tuple[str, ...]:
    normalized = []
    for line in lines:
        value = re.sub(r"^(route-map|ip\s+prefix-list|(?:ip|bgp)\s+community-list(?:\s+\S+)?)\s+\S+", r"\1 <NAME>", line, flags=re.I)
        value = re.sub(r"\b\d+\b", "<N>", value)
        normalized.append(value.strip().lower())
    return tuple(normalized)


def _command_ast(text: str) -> List[Tuple[int, str]]:
    ast = []
    for line in text.splitlines():
        if not line or line.lstrip().startswith("!"):
            continue
        indent = len(line) - len(line.lstrip())
        command = re.sub(r"\b\d+(?:\.\d+){0,3}(?:/\d+)?\b", "<VALUE>", line.strip().lower())
        ast.append((indent, command))
    return ast


def _levenshtein(left: Sequence[Any], right: Sequence[Any]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _motifs(graph: DependencyGraph) -> Set[Tuple[str, str]]:
    return {
        (graph.nodes[source].kind, graph.nodes[target].kind)
        for source, targets in graph.edges.items()
        for target in targets
        if source in graph.nodes and target in graph.nodes
    }


def _new_sequences(before: str, after: str) -> List[int]:
    before_lines = set(before.splitlines())
    values = []
    for line in after.splitlines():
        if line in before_lines:
            continue
        match = re.search(r"\b(?:seq\s+|route-map\s+\S+\s+\S+\s+)(\d+)\b", line, re.I)
        if match:
            values.append(int(match.group(1)))
    return values


def _mean_nearest_deviation(values: Sequence[int], grid: Sequence[int]) -> float:
    if not values or not grid:
        return 0.0
    return sum(min(abs(value - point) for point in grid) for value in values) / len(values)


def measure_conformance(before_configs: Mapping[str, str], after_configs: Mapping[str, str]) -> ConformanceReport:
    before_graph = build_frr_dependency_graph(before_configs)
    after_graph = build_frr_dependency_graph(after_configs)
    preferences = infer_conformance(before_configs, before_graph, set())
    structural = structural_footprint(before_configs, after_configs, before_graph, after_graph)
    textual_parts = [
        textual_footprint(before_configs.get(device, ""), after_configs.get(device, ""))
        for device in sorted(set(before_configs) | set(after_configs))
    ]
    lines_changed = sum(part.lines_touched for part in textual_parts)
    reused = len(structural.existing_objects_reused)
    new = len(structural.new_objects_created)
    reuse_rate = reused / (reused + new) if reused + new else 1.0

    before_blocks = {f"{device}:{key}": value for device, text in before_configs.items() for key, value in _blocks(text).items()}
    after_blocks = {f"{device}:{key}": value for device, text in after_configs.items() for key, value in _blocks(text).items()}
    before_signatures = {_normalized_signature(lines) for lines in before_blocks.values()}
    new_block_keys = set(after_blocks) - set(before_blocks)
    unnecessary_proxy = sum(_normalized_signature(after_blocks[key]) in before_signatures for key in new_block_keys)

    family_matches = []
    for node_id in structural.new_objects_created:
        node = after_graph.nodes[node_id]
        families = preferences.naming_families.get(node.kind, ())
        family_matches.append(not families or any(node.name.upper().startswith(family.upper()) for family in families))
    naming_rate = sum(family_matches) / len(family_matches) if family_matches else 1.0

    sequences = []
    parameter_values: Dict[str, List[int]] = {}
    ast_distance = 0
    for device in sorted(set(before_configs) | set(after_configs)):
        before, after = before_configs.get(device, ""), after_configs.get(device, "")
        sequences.extend(_new_sequences(before, after))
        ast_distance += _levenshtein(_command_ast(before), _command_ast(after))
        before_lines = set(before.splitlines())
        for line in after.splitlines():
            if line in before_lines:
                continue
            match = re.match(r"^\s*set\s+([\w-]+)(?:\s+[\w-]+)*\s+(\d+)\s*$", line, re.I)
            if match:
                parameter_values.setdefault(match.group(1).lower(), []).append(int(match.group(2)))
    spacing_grid = preferences.sequence_spacing
    sequence_deviation = _mean_nearest_deviation(sequences, spacing_grid)
    deviations = []
    for command, values in parameter_values.items():
        deviations.extend(
            min(abs(value - point) for point in preferences.parameter_grids.get(command, (value,)))
            for value in values
        )
    parameter_deviation = sum(deviations) / len(deviations) if deviations else 0.0
    before_motifs, after_motifs = _motifs(before_graph), _motifs(after_graph)
    similarity = len(before_motifs & after_motifs) / len(before_motifs | after_motifs) if before_motifs | after_motifs else 1.0
    return ConformanceReport(
        object_reuse_rate=reuse_rate,
        reused_existing_objects=reused,
        new_objects=new,
        unnecessary_new_object_proxy=unnecessary_proxy,
        naming_family_match_rate=naming_rate,
        sequence_spacing_mean_deviation=sequence_deviation,
        parameter_grid_mean_deviation=parameter_deviation,
        local_structural_similarity=similarity,
        config_ast_edit_distance=ast_distance,
        devices_touched=len(structural.devices_touched),
        objects_touched=len(structural.policy_objects_touched),
        lines_changed=lines_changed,
    )

