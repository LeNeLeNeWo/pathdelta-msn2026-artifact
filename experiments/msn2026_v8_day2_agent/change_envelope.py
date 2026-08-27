"""Intent-relative change envelopes for direct brownfield configuration edits.

The LLM/agent is deliberately allowed to propose arbitrary exact
search-and-replace edits.  This module does not synthesize the patch.  It
derives and checks the boundary within which a Day-2 patch may be accepted:

* the requested semantic delta must occur;
* every observed behavior outside the intent scope must be preserved;
* shared legacy objects and non-target bindings are protected;
* newly created objects must follow locally inferred conventions; and
* the textual/structural footprint must stay within a small budget.

The policy evaluator is intentionally small and only supports the FRR route-map
subset used by the v8 development pilot.  A production implementation should
replace it with Batfish/FRR differential queries while retaining the contract
and verdict types defined here.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from difflib import ndiff
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class SearchReplaceEdit:
    device: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class Day2Intent:
    intent_id: str
    target_device: str
    target_neighbor: str
    target_prefix: str
    desired_local_pref: int


@dataclass(frozen=True)
class PrefixRule:
    sequence: int
    action: str
    prefix: str
    ge: Optional[int] = None
    le: Optional[int] = None


@dataclass(frozen=True)
class RouteMapClause:
    name: str
    sequence: int
    action: str
    match_prefix_lists: Tuple[str, ...] = ()
    set_local_pref: Optional[int] = None


@dataclass
class ConfigModel:
    prefix_lists: Dict[str, List[PrefixRule]] = field(default_factory=dict)
    route_maps: Dict[str, List[RouteMapClause]] = field(default_factory=dict)
    neighbor_bindings: Dict[str, str] = field(default_factory=dict)

    def route_map_refcounts(self) -> Counter[str]:
        return Counter(self.neighbor_bindings.values())

    def prefix_list_refcounts(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for clauses in self.route_maps.values():
            for clause in clauses:
                counts.update(clause.match_prefix_lists)
        return counts


@dataclass(frozen=True)
class RouteOutcome:
    decision: str
    local_pref: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalStyleContract:
    route_map_prefix: str
    prefix_list_prefix: str
    uppercase_names: bool
    sequence_step: int
    subcommand_indent: str


@dataclass
class ChangeEnvelope:
    schema_version: str
    derivation_version: str
    intent: Day2Intent
    baseline_sha256: Dict[str, str]
    observed_neighbors: List[str]
    observed_prefixes: List[str]
    allowed_semantic_changes: List[str]
    preservation_frame: Dict[str, Dict[str, Any]]
    protected_neighbor_bindings: Dict[str, str]
    protected_existing_route_maps: List[str]
    protected_existing_prefix_lists: List[str]
    style: LocalStyleContract
    max_changed_lines: int
    max_created_objects: int
    max_changed_bindings: int
    derivation_evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatchFootprint:
    changed_lines: int
    created_route_maps: List[str]
    created_prefix_lists: List[str]
    modified_existing_route_maps: List[str]
    modified_existing_prefix_lists: List[str]
    changed_neighbor_bindings: List[str]

    @property
    def created_objects(self) -> int:
        return len(self.created_route_maps) + len(self.created_prefix_lists)


@dataclass
class CandidateVerdict:
    candidate_id: str
    accepted: bool
    goal_satisfied: bool
    semantic_frame_preserved: bool
    structural_scope_preserved: bool
    style_preserved: bool
    budget_preserved: bool
    reasons: List[str]
    semantic_changes: Dict[str, Dict[str, Dict[str, Any]]]
    footprint: PatchFootprint

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["footprint"]["created_objects"] = self.footprint.created_objects
        return payload


_PREFIX_LIST_RE = re.compile(
    r"^ip\s+prefix-list\s+(\S+)\s+seq\s+(\d+)\s+(permit|deny)\s+(\S+)"
    r"(?:\s+ge\s+(\d+))?(?:\s+le\s+(\d+))?\s*$",
    re.IGNORECASE,
)
_ROUTE_MAP_RE = re.compile(r"^route-map\s+(\S+)\s+(permit|deny)\s+(\d+)\s*$", re.IGNORECASE)
_MATCH_PL_RE = re.compile(r"^\s+match\s+ip\s+address\s+prefix-list\s+(.+?)\s*$", re.IGNORECASE)
_SET_LP_RE = re.compile(r"^\s+set\s+local-preference\s+(\d+)\s*$", re.IGNORECASE)
_NEIGHBOR_RM_RE = re.compile(
    r"^\s*neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)\s*$", re.IGNORECASE
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_config(text: str) -> ConfigModel:
    """Parse the route-policy subset without collapsing repeated clauses."""
    model = ConfigModel()
    current: Optional[Dict[str, Any]] = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        clause = RouteMapClause(
            name=current["name"],
            sequence=current["sequence"],
            action=current["action"],
            match_prefix_lists=tuple(current["match_prefix_lists"]),
            set_local_pref=current["set_local_pref"],
        )
        model.route_maps.setdefault(clause.name, []).append(clause)
        current = None

    for raw in text.splitlines():
        line = raw.rstrip()
        prefix_match = _PREFIX_LIST_RE.match(line)
        if prefix_match:
            flush()
            name, seq, action, prefix, ge, le = prefix_match.groups()
            model.prefix_lists.setdefault(name, []).append(
                PrefixRule(int(seq), action.lower(), prefix, int(ge) if ge else None, int(le) if le else None)
            )
            continue
        route_match = _ROUTE_MAP_RE.match(line)
        if route_match:
            flush()
            name, action, seq = route_match.groups()
            current = {
                "name": name,
                "sequence": int(seq),
                "action": action.lower(),
                "match_prefix_lists": [],
                "set_local_pref": None,
            }
            continue
        match_pl = _MATCH_PL_RE.match(line)
        if match_pl and current is not None:
            current["match_prefix_lists"].extend(match_pl.group(1).split())
            continue
        set_lp = _SET_LP_RE.match(line)
        if set_lp and current is not None:
            current["set_local_pref"] = int(set_lp.group(1))
            continue
        neighbor = _NEIGHBOR_RM_RE.match(line)
        if neighbor:
            flush()
            address, route_map, direction = neighbor.groups()
            if direction.lower() == "in":
                model.neighbor_bindings[address] = route_map
            continue
        if line and not line[0].isspace():
            flush()
    flush()
    for rules in model.prefix_lists.values():
        rules.sort(key=lambda item: item.sequence)
    for clauses in model.route_maps.values():
        clauses.sort(key=lambda item: item.sequence)
    return model


def _prefix_rule_matches(rule: PrefixRule, candidate: str) -> bool:
    rule_net = ipaddress.ip_network(rule.prefix, strict=False)
    candidate_net = ipaddress.ip_network(candidate, strict=False)
    if candidate_net.version != rule_net.version or not candidate_net.subnet_of(rule_net):
        return False
    if rule.ge is not None and candidate_net.prefixlen < rule.ge:
        return False
    if rule.le is not None and candidate_net.prefixlen > rule.le:
        return False
    if rule.ge is None and rule.le is None and candidate_net.prefixlen != rule_net.prefixlen:
        return False
    return True


def _prefix_list_permits(model: ConfigModel, name: str, prefix: str) -> bool:
    for rule in model.prefix_lists.get(name, []):
        if _prefix_rule_matches(rule, prefix):
            return rule.action == "permit"
    return False


def evaluate_route(model: ConfigModel, neighbor: str, prefix: str) -> RouteOutcome:
    route_map = model.neighbor_bindings.get(neighbor)
    if route_map is None:
        return RouteOutcome("unbound", 100)
    for clause in model.route_maps.get(route_map, []):
        matches = not clause.match_prefix_lists or any(
            _prefix_list_permits(model, name, prefix) for name in clause.match_prefix_lists
        )
        if not matches:
            continue
        if clause.action == "deny":
            return RouteOutcome("deny", None)
        return RouteOutcome("permit", clause.set_local_pref if clause.set_local_pref is not None else 100)
    return RouteOutcome("implicit-deny", None)


def evaluate_matrix(
    model: ConfigModel, neighbors: Iterable[str], prefixes: Iterable[str]
) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for neighbor in sorted(set(neighbors)):
        for prefix in sorted(set(prefixes)):
            output[f"{neighbor}|{prefix}"] = evaluate_route(model, neighbor, prefix).to_dict()
    return output


def _common_name_prefix(names: Sequence[str], fallback: str) -> str:
    if not names:
        return fallback
    token_prefixes = []
    for name in names:
        match = re.match(r"^([A-Za-z]+_)", name)
        if match:
            token_prefixes.append(match.group(1))
    if not token_prefixes:
        return fallback
    return Counter(token_prefixes).most_common(1)[0][0]


def infer_style(text: str, model: ConfigModel) -> LocalStyleContract:
    sequences = sorted(
        {clause.sequence for clauses in model.route_maps.values() for clause in clauses}
        | {rule.sequence for rules in model.prefix_lists.values() for rule in rules}
    )
    positive_deltas = [b - a for a, b in zip(sequences, sequences[1:]) if b > a]
    step = Counter(positive_deltas).most_common(1)[0][0] if positive_deltas else 10
    indents = []
    for line in text.splitlines():
        if _MATCH_PL_RE.match(line) or _SET_LP_RE.match(line):
            indents.append(line[: len(line) - len(line.lstrip())])
    indent = Counter(indents).most_common(1)[0][0] if indents else " "
    all_names = list(model.route_maps) + list(model.prefix_lists)
    uppercase = bool(all_names) and sum(name.upper() == name for name in all_names) / len(all_names) >= 0.75
    return LocalStyleContract(
        route_map_prefix=_common_name_prefix(list(model.route_maps), "RM_"),
        prefix_list_prefix=_common_name_prefix(list(model.prefix_lists), "PL_"),
        uppercase_names=uppercase,
        sequence_step=step,
        subcommand_indent=indent,
    )


def derive_change_envelope(
    baseline_configs: Mapping[str, str],
    intent: Day2Intent,
    probe_prefixes: Sequence[str],
) -> ChangeEnvelope:
    """Derive an intent-relative frame without choosing a concrete patch."""
    if intent.target_device not in baseline_configs:
        raise ValueError(f"target device {intent.target_device!r} is absent")
    text = baseline_configs[intent.target_device]
    model = parse_config(text)
    if intent.target_neighbor not in model.neighbor_bindings:
        raise ValueError(f"target neighbor {intent.target_neighbor!r} has no inbound route-map")

    observed_neighbors = sorted(model.neighbor_bindings)
    observed_prefixes = sorted(set([intent.target_prefix, *probe_prefixes]))
    baseline_matrix = evaluate_matrix(model, observed_neighbors, observed_prefixes)
    allowed_key = f"{intent.target_neighbor}|{intent.target_prefix}"
    frame = {key: value for key, value in baseline_matrix.items() if key != allowed_key}

    route_refcounts = model.route_map_refcounts()
    prefix_refcounts = model.prefix_list_refcounts()
    target_rm = model.neighbor_bindings[intent.target_neighbor]
    shared_route_maps = sorted(name for name, count in route_refcounts.items() if count > 1)
    # A prefix-list reachable from a shared route-map is protected even when it
    # has only one textual reference: mutating it still affects all dependents.
    shared_prefix_lists = sorted(
        {
            prefix_list
            for route_map in shared_route_maps
            for clause in model.route_maps.get(route_map, [])
            for prefix_list in clause.match_prefix_lists
        }
        | {name for name, count in prefix_refcounts.items() if count > 1}
    )
    protected_bindings = {
        neighbor: route_map
        for neighbor, route_map in model.neighbor_bindings.items()
        if neighbor != intent.target_neighbor
    }
    target_is_shared = route_refcounts[target_rm] > 1
    protected_route_maps = shared_route_maps if target_is_shared else [
        name for name in model.route_maps if name != target_rm
    ]

    return ChangeEnvelope(
        schema_version="1.0.0",
        derivation_version="change-envelope-dev-0.1",
        intent=intent,
        baseline_sha256={device: sha256_text(config) for device, config in baseline_configs.items()},
        observed_neighbors=observed_neighbors,
        observed_prefixes=observed_prefixes,
        allowed_semantic_changes=[allowed_key],
        preservation_frame=frame,
        protected_neighbor_bindings=protected_bindings,
        protected_existing_route_maps=sorted(set(protected_route_maps)),
        protected_existing_prefix_lists=shared_prefix_lists,
        style=infer_style(text, model),
        max_changed_lines=12 if target_is_shared else 8,
        max_created_objects=2 if target_is_shared else 1,
        max_changed_bindings=1,
        derivation_evidence={
            "target_route_map": target_rm,
            "target_route_map_refcount": route_refcounts[target_rm],
            "route_map_refcounts": dict(route_refcounts),
            "prefix_list_refcounts": dict(prefix_refcounts),
            "reason": "shared target policy requires local fork/rebind freedom but protects legacy shared objects",
        },
    )


def apply_search_replace_edits(
    baseline_configs: Mapping[str, str], edits: Sequence[SearchReplaceEdit]
) -> Dict[str, str]:
    configs = dict(baseline_configs)
    for edit in edits:
        if edit.device not in configs:
            raise ValueError(f"edit targets unknown device {edit.device!r}")
        occurrences = configs[edit.device].count(edit.old_text)
        if occurrences != 1:
            raise ValueError(
                f"search text for {edit.device} must occur exactly once; found {occurrences}: {edit.old_text!r}"
            )
        configs[edit.device] = configs[edit.device].replace(edit.old_text, edit.new_text, 1)
    return configs


def _changed_line_count(before: str, after: str) -> int:
    return sum(1 for line in ndiff(before.splitlines(), after.splitlines()) if line.startswith(("+ ", "- ")))


def _changed_bindings(before: ConfigModel, after: ConfigModel) -> List[str]:
    keys = set(before.neighbor_bindings) | set(after.neighbor_bindings)
    return sorted(key for key in keys if before.neighbor_bindings.get(key) != after.neighbor_bindings.get(key))


def inspect_footprint(before_text: str, after_text: str) -> PatchFootprint:
    before = parse_config(before_text)
    after = parse_config(after_text)
    before_rms, after_rms = set(before.route_maps), set(after.route_maps)
    before_pls, after_pls = set(before.prefix_lists), set(after.prefix_lists)

    modified_rms = sorted(
        name for name in before_rms & after_rms if before.route_maps[name] != after.route_maps[name]
    )
    modified_pls = sorted(
        name for name in before_pls & after_pls if before.prefix_lists[name] != after.prefix_lists[name]
    )
    return PatchFootprint(
        changed_lines=_changed_line_count(before_text, after_text),
        created_route_maps=sorted(after_rms - before_rms),
        created_prefix_lists=sorted(after_pls - before_pls),
        modified_existing_route_maps=modified_rms,
        modified_existing_prefix_lists=modified_pls,
        changed_neighbor_bindings=_changed_bindings(before, after),
    )


def _style_violations(style: LocalStyleContract, before: ConfigModel, after: ConfigModel, after_text: str) -> List[str]:
    reasons: List[str] = []
    new_route_maps = sorted(set(after.route_maps) - set(before.route_maps))
    new_prefix_lists = sorted(set(after.prefix_lists) - set(before.prefix_lists))
    for name in new_route_maps:
        if not name.startswith(style.route_map_prefix):
            reasons.append(f"new route-map {name} violates local prefix {style.route_map_prefix}")
        if style.uppercase_names and name.upper() != name:
            reasons.append(f"new route-map {name} violates uppercase naming habit")
    for name in new_prefix_lists:
        if not name.startswith(style.prefix_list_prefix):
            reasons.append(f"new prefix-list {name} violates local prefix {style.prefix_list_prefix}")
        if style.uppercase_names and name.upper() != name:
            reasons.append(f"new prefix-list {name} violates uppercase naming habit")
    for name in new_route_maps:
        for clause in after.route_maps[name]:
            if clause.sequence % style.sequence_step:
                reasons.append(
                    f"route-map {name} sequence {clause.sequence} violates step {style.sequence_step}"
                )
    for name in new_prefix_lists:
        for rule in after.prefix_lists[name]:
            if rule.sequence % style.sequence_step:
                reasons.append(
                    f"prefix-list {name} sequence {rule.sequence} violates step {style.sequence_step}"
                )
    for line in after_text.splitlines():
        if (_MATCH_PL_RE.match(line) or _SET_LP_RE.match(line)) and line.startswith(" "):
            actual = line[: len(line) - len(line.lstrip())]
            if actual != style.subcommand_indent:
                reasons.append(
                    f"subcommand indentation {actual!r} violates local indentation {style.subcommand_indent!r}"
                )
                break
    return reasons


def evaluate_candidate(
    candidate_id: str,
    baseline_configs: Mapping[str, str],
    edits: Sequence[SearchReplaceEdit],
    envelope: ChangeEnvelope,
) -> CandidateVerdict:
    reasons: List[str] = []
    try:
        candidate_configs = apply_search_replace_edits(baseline_configs, edits)
    except ValueError as exc:
        empty = PatchFootprint(0, [], [], [], [], [])
        return CandidateVerdict(candidate_id, False, False, False, False, False, False, [str(exc)], {}, empty)

    device = envelope.intent.target_device
    before_text = baseline_configs[device]
    after_text = candidate_configs[device]
    before = parse_config(before_text)
    after = parse_config(after_text)
    footprint = inspect_footprint(before_text, after_text)

    before_matrix = evaluate_matrix(before, envelope.observed_neighbors, envelope.observed_prefixes)
    after_matrix = evaluate_matrix(after, envelope.observed_neighbors, envelope.observed_prefixes)
    semantic_changes = {
        key: {"before": before_matrix[key], "after": after_matrix[key]}
        for key in before_matrix
        if before_matrix[key] != after_matrix[key]
    }
    target_key = envelope.allowed_semantic_changes[0]
    target_after = after_matrix.get(target_key, {})
    goal_satisfied = (
        target_after.get("decision") == "permit"
        and target_after.get("local_pref") == envelope.intent.desired_local_pref
    )
    if not goal_satisfied:
        reasons.append(f"target semantic delta not achieved at {target_key}: {target_after}")

    frame_violations = [
        key for key, expected in envelope.preservation_frame.items() if after_matrix.get(key) != expected
    ]
    semantic_frame_preserved = not frame_violations
    if frame_violations:
        reasons.append(f"non-target semantic frame changed: {frame_violations}")

    structural_reasons: List[str] = []
    protected_binding_changes = [
        neighbor
        for neighbor, expected in envelope.protected_neighbor_bindings.items()
        if after.neighbor_bindings.get(neighbor) != expected
    ]
    if protected_binding_changes:
        structural_reasons.append(f"protected neighbor bindings changed: {protected_binding_changes}")
    protected_rm_changes = sorted(
        set(footprint.modified_existing_route_maps) & set(envelope.protected_existing_route_maps)
    )
    if protected_rm_changes:
        structural_reasons.append(f"protected shared route-maps modified: {protected_rm_changes}")
    protected_pl_changes = sorted(
        set(footprint.modified_existing_prefix_lists) & set(envelope.protected_existing_prefix_lists)
    )
    if protected_pl_changes:
        structural_reasons.append(f"protected shared prefix-lists modified: {protected_pl_changes}")
    edited_devices = sorted({edit.device for edit in edits})
    if edited_devices != [device]:
        structural_reasons.append(f"edits escaped target device: {edited_devices}")
    structural_scope_preserved = not structural_reasons
    reasons.extend(structural_reasons)

    style_reasons = _style_violations(envelope.style, before, after, after_text)
    style_preserved = not style_reasons
    reasons.extend(style_reasons)

    budget_reasons: List[str] = []
    if footprint.changed_lines > envelope.max_changed_lines:
        budget_reasons.append(
            f"changed-line budget exceeded: {footprint.changed_lines}>{envelope.max_changed_lines}"
        )
    if footprint.created_objects > envelope.max_created_objects:
        budget_reasons.append(
            f"created-object budget exceeded: {footprint.created_objects}>{envelope.max_created_objects}"
        )
    if len(footprint.changed_neighbor_bindings) > envelope.max_changed_bindings:
        budget_reasons.append(
            "changed-binding budget exceeded: "
            f"{len(footprint.changed_neighbor_bindings)}>{envelope.max_changed_bindings}"
        )
    budget_preserved = not budget_reasons
    reasons.extend(budget_reasons)

    accepted = all(
        [goal_satisfied, semantic_frame_preserved, structural_scope_preserved, style_preserved, budget_preserved]
    )
    return CandidateVerdict(
        candidate_id=candidate_id,
        accepted=accepted,
        goal_satisfied=goal_satisfied,
        semantic_frame_preserved=semantic_frame_preserved,
        structural_scope_preserved=structural_scope_preserved,
        style_preserved=style_preserved,
        budget_preserved=budget_preserved,
        reasons=reasons,
        semantic_changes=semantic_changes,
        footprint=footprint,
    )


def write_envelope_json(envelope: ChangeEnvelope) -> str:
    return json.dumps(envelope.to_dict(), indent=2, sort_keys=True) + "\n"

