from __future__ import annotations

from typing import Any, Dict, List

from .schema import Assertion, IntentCard, TestCase


def generate_assertions(intent: IntentCard) -> List[Assertion]:
    asserts: List[Assertion] = []
    iid = intent.intent_id
    if intent.type == "prefer_with_backup":
        asserts.append(
            Assertion(
                id=f"a-{iid}-1",
                intent_id=iid,
                kind="path_preference",
                params={
                    "prefix": intent.prefix,
                    "primary_exit": intent.primary_exit,
                    "backup_exit": intent.backup_exit,
                },
            )
        )
    elif intent.type == "ecmp":
        asserts.append(
            Assertion(
                id=f"a-{iid}-1",
                intent_id=iid,
                kind="ecmp",
                params={"prefix": intent.prefix, "exits": intent.exits},
            )
        )
    elif intent.type == "pin_to_exit":
        asserts.append(
            Assertion(
                id=f"a-{iid}-1",
                intent_id=iid,
                kind="pin_to_exit",
                params={"prefix": intent.prefix, "pinned_exit": intent.pinned_exit},
            )
        )
    elif intent.type == "avoid_exit":
        asserts.append(
            Assertion(
                id=f"a-{iid}-1",
                intent_id=iid,
                kind="avoid_exit",
                params={"prefix": intent.prefix, "avoid_exits": intent.normalized_avoid_exits()},
            )
        )
    elif intent.type == "ordered_preference":
        asserts.append(
            Assertion(
                id=f"a-{iid}-1",
                intent_id=iid,
                kind="ordered_preference",
                params={"prefix": intent.prefix, "ordered_exits": intent.normalized_ordered_exits()},
            )
        )
    return asserts


def _pick_observer_devices(topology: Dict[str, Any], limit: int = 3) -> List[str]:
    nodes = topology.get("nodes", {}) or {}
    routers = [n for n, meta in nodes.items() if meta.get("type") in (None, "router", "core", "edge")]
    if not routers:
        routers = list(nodes.keys())
    return routers[:limit] if limit and routers else routers


def generate_testcases(intent: IntentCard, topology: Dict[str, Any]) -> List[TestCase]:
    cases: List[TestCase] = []
    iid = intent.intent_id
    observers = _pick_observer_devices(topology)

    if intent.type == "prefer_with_backup":
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-primary",
                    intent_id=iid,
                    kind="bgp_bestpath",
                    device=observers[0],
                    prefix=intent.prefix,
                    expected={"primary_exit": intent.primary_exit},
                )
            )
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-reach",
                    intent_id=iid,
                    kind="reachability",
                    devices=observers,
                    prefix=intent.prefix,
                    expected={"reachable": True},
                )
            )
    elif intent.type == "ecmp":
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-ecmp",
                    intent_id=iid,
                    kind="bgp_ecmp",
                    device=observers[0],
                    prefix=intent.prefix,
                    expected={"exits": intent.exits},
                )
            )
    elif intent.type == "pin_to_exit":
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-pinned-exit",
                    intent_id=iid,
                    kind="bgp_bestpath",
                    device=observers[0],
                    prefix=intent.prefix,
                    expected={"primary_exit": intent.pinned_exit},
                )
            )
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-reach",
                    intent_id=iid,
                    kind="reachability",
                    devices=observers,
                    prefix=intent.prefix,
                    expected={"reachable": True},
                )
            )
    elif intent.type == "avoid_exit":
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-avoid-exit",
                    intent_id=iid,
                    kind="bgp_path_not_contains",
                    device=observers[0],
                    prefix=intent.prefix,
                    expected={"avoid_exits": intent.normalized_avoid_exits()},  # unified naming: avoid_exits
                )
            )
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-reach",
                    intent_id=iid,
                    kind="reachability",
                    devices=observers,
                    prefix=intent.prefix,
                    expected={"reachable": True},
                )
            )
    elif intent.type == "ordered_preference":
        if observers:
            cases.append(
                TestCase(
                    id=f"tc-{iid}-order",
                    intent_id=iid,
                    kind="bgp_path_order",
                    device=observers[0],
                    prefix=intent.prefix,
                    expected={"ordered_exits": intent.normalized_ordered_exits()},
                )
            )
    return cases
