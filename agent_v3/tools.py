"""Narrow, typed tools exposed to the v3 orchestration policy.

There is intentionally no generic shell, file-write, or patch-apply tool here.
All paths are rooted in one immutable scenario directory, and every mutating
network action is gated behind the contract and final-verifier states.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

from intent_layer.schema import IntentCard
from policy_layer.models import PolicyEntry, RolePolicy
from policy_layer.runner import run_policy
from synthesis_layer.context_analyzer import ContextAnalyzer, analyze_affected_devices, infer_naming_pattern_by_type
from synthesis_layer.guard import ConstraintGuard, ContractBuilder
from synthesis_layer.models import PatchPlan
from synthesis_layer.planner import PatchPlanner
from synthesis_layer.symbolic_renderer import SymbolicOnlyRenderer
from tools.demo_pathdelta_workflow import _enrich_patch_plans_for_execution


class ToolError(RuntimeError):
    pass


class _QuietLogger:
    def log(self, _: str) -> None:
        pass


def _role_policy(policy: Dict[str, Any]) -> RolePolicy:
    mechanisms = policy.get("mechanisms") or []
    return RolePolicy(policies=[PolicyEntry(
        intent_id=policy.get("intent_id", "agent-v3"), type=policy.get("type", ""),
        proto=policy.get("proto", "bgp"), mechanism=(mechanisms[0].get("name", "none") if mechanisms else "none"),
        scope="prefix", prefix=policy.get("prefix"), primary_exit=policy.get("primary_exit"),
        backup_exit=policy.get("backup_exit"), ordered_exits=policy.get("ordered_exits"),
        pinned_exit=(policy.get("pinned_exits") or [None])[0], avoid_exits=policy.get("avoid_exits"),
        preference_tiers=policy.get("preference_tiers"), affected_devices=policy.get("affected_devices", []),
        affected_neighbors=policy.get("affected_neighbors", {}), params=policy.get("params", {}),
        change_footprint=policy.get("change_footprint", {}),
    )])


class TrustedToolbox:
    """Trusted operations over a single explicit scenario root."""

    public_tools = {
        "inspect_topology", "inspect_current_policy", "inspect_brownfield_context",
        "infer_local_conventions", "solve_policy_parameters", "plan_safe_patch",
        "render_deterministic_fragment", "check_patch_contract", "validate_frr",
        "query_batfish", "validate_rela", "run_kathara",
    }

    def __init__(self, scenario_root: Path, work_root: Path):
        self.scenario_root = scenario_root.resolve()
        self.work_root = work_root.resolve()
        self.topology = yaml.safe_load((self.scenario_root / "topology.yaml").read_text(encoding="utf-8"))
        self.sketch = yaml.safe_load((self.scenario_root / "CurrentPolicySketch.yaml").read_text(encoding="utf-8"))
        intent_paths = sorted((self.scenario_root / "intent").glob("*.json"))
        if len(intent_paths) != 1:
            raise ToolError(f"prototype scenario requires exactly one intent, found {len(intent_paths)}")
        self.intent = IntentCard(**json.loads(intent_paths[0].read_text(encoding="utf-8")))
        self.baseline = {p.stem: p.read_text(encoding="utf-8") for p in (self.scenario_root / "baseline_configs").glob("*.frr")}
        if not self.baseline:
            raise ToolError("baseline_configs is empty")

    def inspect_topology(self) -> Dict[str, Any]:
        nodes = self.topology.get("nodes", {})
        return {"name": self.topology.get("name"), "node_count": len(nodes),
                "roles": {name: meta.get("role") for name, meta in nodes.items()},
                "lan_count": len(self.topology.get("lans", {}))}

    def inspect_current_policy(self) -> Dict[str, Any]:
        return {"intent": self.intent.model_dump(), "sketch": self.sketch,
                "config_devices": sorted(self.baseline)}

    def inspect_brownfield_context(self) -> Dict[str, Any]:
        names: Dict[str, List[str]] = {"route_maps": [], "prefix_lists": [], "community_lists": []}
        bindings: List[Dict[str, str]] = []
        for device, text in self.baseline.items():
            names["route_maps"] += re.findall(r"^route-map\s+(\S+)", text, re.MULTILINE)
            names["prefix_lists"] += re.findall(r"^ip prefix-list\s+(\S+)", text, re.MULTILINE)
            names["community_lists"] += re.findall(r"^bgp community-list \S+\s+(\S+)", text, re.MULTILINE)
            for neighbor, route_map, direction in re.findall(r"^\s*neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)", text, re.MULTILINE):
                bindings.append({"device": device, "neighbor": neighbor, "route_map": route_map, "direction": direction})
        return {"objects": {k: sorted(set(v)) for k, v in names.items()}, "bindings": bindings}

    def infer_local_conventions(self) -> Dict[str, Any]:
        joined = "\n".join(self.baseline.values())
        inferred = infer_naming_pattern_by_type(joined).to_json()
        seqs = [int(x) for x in re.findall(r"^route-map\s+\S+\s+\S+\s+(\d+)", joined, re.MULTILINE)]
        lprefs = [int(x) for x in re.findall(r"^\s*set local-preference\s+(\d+)", joined, re.MULTILINE)]
        inferred.update({"route_map_sequences": sorted(set(seqs)), "local_preference_grid": sorted(set(lprefs))})
        return inferred

    def solve_policy_parameters(self) -> Dict[str, Any]:
        return run_policy(self.intent.model_dump(), self.topology, self.sketch)

    def plan_safe_patch(self, policy: Dict[str, Any]) -> List[PatchPlan]:
        context = ContextAnalyzer()
        for device in policy.get("affected_devices", []):
            if device in self.baseline:
                context.slice_config(self.baseline[device])
        prefixes = [policy["prefix"]] if policy.get("prefix") else list(self.intent.prefixes or [])
        reuse = analyze_affected_devices(self.baseline, policy.get("affected_devices", []), policy.get("affected_neighbors", {}), prefixes)
        plans = PatchPlanner(context_analyzer=context).plan(_role_policy(policy), reuse, self.baseline)
        _enrich_patch_plans_for_execution(plans, self.topology, _QuietLogger())
        return plans

    def render_deterministic_fragment(self, plans: Iterable[PatchPlan]) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
        renderer = SymbolicOnlyRenderer()
        parts: Dict[str, List[str]] = {}
        unsupported: List[Dict[str, str]] = []
        for plan in plans:
            result = renderer.render_plan(plan)
            unsupported.extend(result.unsupported_operations)
            for device, text in result.rendered_configs.items():
                parts.setdefault(device, []).append(text)
        return {device: "\n".join(texts) for device, texts in parts.items()}, unsupported

    def check_patch_contract(self, plans: Iterable[PatchPlan], patches: Dict[str, str]) -> Dict[str, Any]:
        # Contracts are operation scoped.  Checking an aggregate device patch
        # against a PREFIX_LIST contract would (correctly) reject its adjacent
        # route-map operations, so re-render/verify each operation separately
        # and then prove that exact trusted fragment is present in the aggregate.
        renderer = SymbolicOnlyRenderer()
        failures: List[Dict[str, Any]] = []
        for plan in plans:
            for op in plan.operations:
                try:
                    fragment = renderer.render_operation(op)
                    if fragment not in patches.get(op.device, ""):
                        failures.append({"device": op.device, "operation_id": op.operation_id,
                                         "reason": "verified fragment absent from aggregate patch"})
                except Exception as exc:
                    failures.append({"device": op.device, "operation_id": op.operation_id, "reason": str(exc)})
        return {"ok": not failures, "failures": failures}

    @staticmethod
    def _merge(base: str, patch: str) -> str:
        lines = base.rstrip().splitlines()
        if lines and lines[-1].strip() == "end":
            lines.pop()
        return "\n".join(lines) + "\n" + patch.rstrip() + "\nend\n"

    def validate_frr(self, patches: Dict[str, str]) -> Dict[str, Any]:
        merged = self.work_root / "merged_configs"
        merged.mkdir(parents=True, exist_ok=True)
        for device, base in self.baseline.items():
            (merged / f"{device}.frr").write_text(self._merge(base, patches.get(device, "")), encoding="utf-8")
        command = "set +e; printf 'service integrated-vtysh-config\\n' >/etc/frr/vtysh.conf; failed=0; " \
                  "for f in /configs/*.frr; do out=$(vtysh -C -f \"$f\" 2>&1); rc=$?; " \
                  "if [ $rc -ne 0 ] || printf '%s' \"$out\" | grep -Eq 'Unknown command|Command incomplete|Failure|Error:'; " \
                  "then failed=$((failed+1)); printf 'FAIL|%s|%s\\n' \"$f\" \"$out\"; fi; done; exit $failed"
        run = subprocess.run(["docker", "run", "--rm", "-v", f"{merged}:/configs:ro", "--entrypoint", "/bin/sh",
                              "frrouting/frr:v8.4.0", "-lc", command], capture_output=True, text=True, timeout=300)
        return {"ok": run.returncode == 0, "returncode": run.returncode,
                "failures": [x for x in run.stdout.splitlines() if x.startswith("FAIL|")], "stderr": run.stderr[-2000:]}

    def query_batfish(self, _: Dict[str, str]) -> Dict[str, Any]:
        return {"status": "not_run", "reason": "prototype syntax gate is sufficient; semantic Batfish is mandatory in formal phases"}

    def validate_rela(self, _: Dict[str, str]) -> Dict[str, Any]:
        return {"status": "available" if shutil.which("rela") else "not_available", "reason": "requires a compiled topology model"}

    def run_kathara(self, _: Dict[str, str]) -> Dict[str, Any]:
        return {"status": "available" if shutil.which("kathara") else "not_available", "reason": "explicitly gated off for the one-case prototype"}
