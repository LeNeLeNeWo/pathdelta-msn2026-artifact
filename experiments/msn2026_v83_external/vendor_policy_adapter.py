"""Independent multi-vendor route-policy observation adapter for v8.3.

The adapter extracts behavior records and dependency graphs from public Cisco
IOS, Arista EOS, and Junos configurations. It never proposes a patch and is not
part of the frozen Change Envelope inference algorithm.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import (
    DependencyGraph,
    DependencyNode,
)


@dataclass
class PrefixRule:
    action: str
    network: str
    seq: int = 0
    ge: int | None = None
    le: int | None = None
    mode: str = "exact"


@dataclass
class PolicyTerm:
    name: str
    action: str = "permit"
    prefix_lists: list[tuple[str, str]] = field(default_factory=list)
    route_filters: list[tuple[str, str]] = field(default_factory=list)
    set_local_pref: int | None = None
    set_communities: list[str] = field(default_factory=list)
    terminal: str | None = None


@dataclass
class VendorModel:
    vendor: str
    hostname: str
    prefix_lists: dict[str, list[PrefixRule]] = field(default_factory=dict)
    policies: dict[str, list[PolicyTerm]] = field(default_factory=dict)
    bindings: dict[str, tuple[str, str]] = field(default_factory=dict)


PL = re.compile(
    r"^ip\s+prefix-list\s+(\S+)\s+seq\s+(\d+)\s+(permit|deny)\s+(\S+)"
    r"(?:\s+ge\s+(\d+))?(?:\s+le\s+(\d+))?\s*$",
    re.I,
)
RM = re.compile(r"^route-map\s+(\S+)\s+(permit|deny)\s+(\d+)\s*$", re.I)
MATCH_PL = re.compile(r"^\s+match\s+ip\s+address\s+prefix-list\s+(.+)$", re.I)
SET_LP = re.compile(r"^\s+set\s+local-preference\s+(\d+)\s*$", re.I)
SET_COMM = re.compile(r"^\s+set\s+community\s+(.+?)(?:\s+additive)?\s*$", re.I)
BIND = re.compile(r"^\s*neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)\s*$", re.I)
HOST = re.compile(r"^hostname\s+(\S+)", re.I)

J_HOST = re.compile(r"^set\s+system\s+host-name\s+(\S+)\s*$", re.I)
J_PL = re.compile(r"^set\s+policy-options\s+prefix-list\s+(\S+)\s+(\S+)\s*$", re.I)
J_BIND = re.compile(r"^set\s+protocols\s+bgp\s+group\s+(\S+)\s+(import|export)\s+(\S+)\s*$", re.I)
J_TERM = re.compile(r"^set\s+policy-options\s+policy-statement\s+(\S+)\s+term\s+(\S+)\s+(.+)$", re.I)


def detect_vendor(text: str) -> str:
    first = "\n".join(text.splitlines()[:10]).lower()
    if "rancid-content-type: arista" in first or "service routing protocols model multi-agent" in text:
        return "arista_eos"
    if re.search(r"^set\s+system\s+host-name", text, re.M | re.I):
        return "juniper_junos"
    return "cisco_ios"


def _parse_route_map(text: str, vendor: str) -> VendorModel:
    hostname = "unknown"
    model = VendorModel(vendor=vendor, hostname=hostname)
    current: PolicyTerm | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if match := HOST.match(line):
            hostname = match.group(1)
            model.hostname = hostname
            continue
        if match := PL.match(line):
            name, seq, action, network, ge, le = match.groups()
            model.prefix_lists.setdefault(name, []).append(
                PrefixRule(action.lower(), network, int(seq), int(ge) if ge else None, int(le) if le else None)
            )
            current = None
            continue
        if match := RM.match(line):
            name, action, seq = match.groups()
            current = PolicyTerm(name=f"seq-{seq}", action=action.lower())
            model.policies.setdefault(name, []).append(current)
            continue
        if current is not None:
            if match := MATCH_PL.match(line):
                current.prefix_lists.extend((name, "prefix-list") for name in match.group(1).split())
                continue
            if match := SET_LP.match(line):
                current.set_local_pref = int(match.group(1))
                continue
            if match := SET_COMM.match(line):
                current.set_communities.extend(value for value in match.group(1).split() if ":" in value)
                continue
        if match := BIND.match(line):
            subject, policy, direction = match.groups()
            direction = direction.lower()
            model.bindings[f"{subject}@{direction}"] = (policy, direction)
            current = None
        elif line and not line.startswith((" ", "!")):
            current = None
    for rules in model.prefix_lists.values():
        rules.sort(key=lambda item: item.seq)
    return model


def _parse_junos(text: str) -> VendorModel:
    hostname = "unknown"
    model = VendorModel(vendor="juniper_junos", hostname=hostname)
    terms: dict[tuple[str, str], PolicyTerm] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if match := J_HOST.match(line):
            hostname = match.group(1)
            model.hostname = hostname
            continue
        if match := J_PL.match(line):
            name, network = match.groups()
            try:
                ipaddress.ip_network(network, strict=False)
            except ValueError:
                continue
            model.prefix_lists.setdefault(name, []).append(PrefixRule("permit", network))
            continue
        if match := J_BIND.match(line):
            group, direction, policy = match.groups()
            direction = direction.lower()
            model.bindings[f"{group}@{direction}"] = (policy, direction)
            continue
        if not (match := J_TERM.match(line)):
            continue
        policy, term_name, tail = match.groups()
        key = (policy, term_name)
        if key not in terms:
            terms[key] = PolicyTerm(name=term_name)
            model.policies.setdefault(policy, []).append(terms[key])
        term = terms[key]
        parts = tail.split()
        if parts[:2] == ["from", "prefix-list"] and len(parts) >= 3:
            term.prefix_lists.append((parts[2], "exact"))
        elif parts[:2] == ["from", "prefix-list-filter"] and len(parts) >= 4:
            term.prefix_lists.append((parts[2], parts[3].lower()))
        elif parts[:2] == ["from", "route-filter"] and len(parts) >= 4:
            term.route_filters.append((parts[2], parts[3].lower()))
        elif parts[:2] == ["then", "local-preference"] and len(parts) >= 3:
            term.set_local_pref = int(parts[2])
        elif parts[:2] == ["then", "community"] and len(parts) >= 4:
            term.set_communities.append(parts[-1])
        elif parts[:2] == ["then", "accept"]:
            term.terminal = "permit"
        elif parts[:2] == ["then", "reject"]:
            term.terminal = "deny"
        elif parts[:3] == ["then", "next", "term"]:
            term.terminal = "next-term"
        elif parts[:3] == ["then", "next", "policy"]:
            term.terminal = "next-policy"
    return model


def parse(text: str) -> VendorModel:
    vendor = detect_vendor(text)
    return _parse_junos(text) if vendor == "juniper_junos" else _parse_route_map(text, vendor)


def _network_matches(candidate: str, base: str, mode: str, ge: int | None = None, le: int | None = None) -> bool:
    route = ipaddress.ip_network(candidate, strict=False)
    network = ipaddress.ip_network(base, strict=False)
    if route.version != network.version or not route.subnet_of(network):
        return False
    if mode in {"orlonger", "longer"}:
        return route.prefixlen >= network.prefixlen + (1 if mode == "longer" else 0)
    if ge is not None and route.prefixlen < ge:
        return False
    if le is not None and route.prefixlen > le:
        return False
    if ge is None and le is None:
        return route.prefixlen == network.prefixlen
    return True


def prefix_list_permits(model: VendorModel, name: str, prefix: str, mode: str = "exact") -> bool:
    for rule in model.prefix_lists.get(name, []):
        effective = mode if mode != "prefix-list" else "exact"
        if _network_matches(prefix, rule.network, effective, rule.ge, rule.le):
            return rule.action == "permit"
    return False


def evaluate_policy(model: VendorModel, policy: str, prefix: str) -> dict[str, Any]:
    local_pref = 100
    communities: list[str] = []
    for term in model.policies.get(policy, []):
        matched = True
        if term.prefix_lists:
            matched = any(prefix_list_permits(model, name, prefix, mode) for name, mode in term.prefix_lists)
        if matched and term.route_filters:
            matched = any(_network_matches(prefix, network, mode) for network, mode in term.route_filters)
        if not matched:
            continue
        if term.set_local_pref is not None:
            local_pref = term.set_local_pref
        communities.extend(value for value in term.set_communities if value not in communities)
        terminal = term.terminal or ("deny" if term.action == "deny" else "permit")
        if terminal == "next-term":
            continue
        if terminal == "deny":
            return {"decision": "deny", "local_pref": None, "communities": sorted(communities)}
        return {"decision": "permit", "local_pref": local_pref, "communities": sorted(communities)}
    return {"decision": "implicit-deny", "local_pref": None, "communities": sorted(communities)}


def evaluate_subject(model: VendorModel, subject: str, prefix: str) -> dict[str, Any] | None:
    binding = model.bindings.get(subject)
    if binding is None:
        return None
    policy, _direction = binding
    result = evaluate_policy(model, policy, prefix)
    result["session"] = "established"
    return result


def configured_prefixes(model: VendorModel) -> list[str]:
    values = {str(ipaddress.ip_network(rule.network, strict=False)) for rules in model.prefix_lists.values() for rule in rules}
    for terms in model.policies.values():
        for term in terms:
            values.update(str(ipaddress.ip_network(network, strict=False)) for network, _mode in term.route_filters)
    return sorted(values, key=lambda value: (ipaddress.ip_network(value).version, int(ipaddress.ip_network(value).network_address), ipaddress.ip_network(value).prefixlen))


def boundary_witnesses(model: VendorModel, *, limit: int = 48) -> list[str]:
    witnesses: set[str] = set(configured_prefixes(model))
    for prefix in list(witnesses):
        network = ipaddress.ip_network(prefix, strict=False)
        if network.prefixlen < network.max_prefixlen:
            witnesses.add(str(next(network.subnets(prefixlen_diff=1))))
        if network.prefixlen > 0:
            parent = network.supernet(prefixlen_diff=1)
            for child in parent.subnets(prefixlen_diff=1):
                if child != network:
                    witnesses.add(str(child))
                    break
    for fallback in ("198.18.0.0/24", "203.0.113.0/24", "2001:db8::/48"):
        witnesses.add(fallback)
    return sorted(witnesses)[:limit]


def build_dependency_graph(configs: Mapping[str, str]) -> DependencyGraph:
    graph = DependencyGraph()
    for device, text in configs.items():
        model = parse(text)
        for name, rules in model.prefix_lists.items():
            material = "\n".join(f"{r.seq}:{r.action}:{r.network}:{r.ge}:{r.le}:{r.mode}" for r in rules)
            node_id = f"{device}:prefix_list:{name}"
            graph.add_node(DependencyNode(node_id, device, "prefix_list", name, len(rules), ("decision", "path"), hashlib.sha256(material.encode()).hexdigest()))
        for name, terms in model.policies.items():
            material = repr(terms)
            node_id = f"{device}:route_map:{name}"
            graph.add_node(DependencyNode(node_id, device, "route_map", name, max(1, len(terms)), ("decision", "local_pref", "community", "path"), hashlib.sha256(material.encode()).hexdigest()))
            for term in terms:
                for prefix_list, _mode in term.prefix_lists:
                    target = f"{device}:prefix_list:{prefix_list}"
                    if target not in graph.nodes:
                        graph.add_node(DependencyNode(target, device, "prefix_list", prefix_list, 0, ()))
                    graph.add_edge(node_id, target)
        for subject, (policy, _direction) in model.bindings.items():
            node_id = f"{device}:neighbor:{subject}"
            digest = hashlib.sha256(f"{subject}:{policy}".encode()).hexdigest()
            graph.add_node(DependencyNode(node_id, device, "neighbor", subject, 1, ("session", "path", "decision", "local_pref", "community"), digest))
            target = f"{device}:route_map:{policy}"
            if target not in graph.nodes:
                graph.add_node(DependencyNode(target, device, "route_map", policy, 0, ()))
            graph.add_edge(node_id, target)
    return graph


def behavior_rows(device: str, text: str, subject: str, prefixes: Iterable[str], source: str) -> list[dict[str, Any]]:
    model = parse(text)
    rows = []
    for prefix in prefixes:
        attributes = evaluate_subject(model, subject, prefix)
        if attributes is None:
            continue
        canonical = str(ipaddress.ip_network(prefix, strict=False))
        rows.append({
            "behavior_id": f"{device}|{subject}|{canonical}",
            "device": device,
            "subject": subject,
            "fec": canonical,
            "attributes": attributes,
            "source": source,
        })
    return rows


def find_candidate_subjects(model: VendorModel, direction: str | None = None) -> list[str]:
    subjects = []
    for subject, (policy, bound_direction) in model.bindings.items():
        if direction is not None and bound_direction != direction:
            continue
        if model.policies.get(policy):
            subjects.append(subject)
    return sorted(subjects)


def policy_for_subject(model: VendorModel, subject: str) -> str:
    return model.bindings[subject][0]
