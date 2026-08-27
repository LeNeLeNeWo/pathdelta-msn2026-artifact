"""Strict no-LLM renderer for the Symbolic-Only PathDelta baseline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .guard import ConstraintGuard, ContractBuilder, SecurityViolationError, Severity
from .models import PatchOperation, PatchPlan
from .renderer import ConfigRenderer


SUPPORTED_TEMPLATES = {
    "bgp/prefix_list_entry.j2",
    "bgp/route_map_sequence.j2",
    "bgp/route_map_fallback.j2",
    "bgp/neighbor_route_map.j2",
    "bgp/maximum_paths.j2",
    "ospf/interface_cost.j2",
}


class UnsupportedSymbolicOperation(Exception):
    """Raised instead of silently falling back to an LLM."""


@dataclass
class SymbolicRenderResult:
    rendered_configs: Dict[str, str] = field(default_factory=dict)
    operation_count: int = 0
    unsupported_operations: List[Dict[str, str]] = field(default_factory=list)
    llm_calls: int = 0


class SymbolicOnlyRenderer:
    """Render the planner's explicit command subset with frozen templates.

    The Policy layer, ContextAnalyzer, PatchPlanner/ShadowSafe, PatchContract
    and Guard are shared with Full PathDelta.  This class changes only the
    rendering mechanism and has no fallback path.
    """

    def __init__(self) -> None:
        self._templates = ConfigRenderer(use_neural=False)
        self._guard = ConstraintGuard()
        self._contracts = ContractBuilder()

    @staticmethod
    def _validate_required_params(op: PatchOperation) -> None:
        required = {
            "bgp/prefix_list_entry.j2": ("prefix_list_name", "prefix", "action"),
            "bgp/route_map_sequence.j2": ("route_map_name", "sequence_number", "action", "prefix_list_name"),
            "bgp/route_map_fallback.j2": ("route_map_name", "sequence_number", "action"),
            "bgp/neighbor_route_map.j2": ("neighbor_address", "route_map_name", "direction", "local_as"),
            "bgp/maximum_paths.j2": ("maximum_paths", "local_as"),
            "ospf/interface_cost.j2": ("interface_name", "cost"),
        }
        missing = [name for name in required[op.template] if op.params.get(name) in (None, "")]
        if missing:
            raise UnsupportedSymbolicOperation(f"{op.template}: missing required planner fields {missing}")

    def render_operation(self, op: PatchOperation) -> str:
        if op.template not in SUPPORTED_TEMPLATES:
            raise UnsupportedSymbolicOperation(f"unsupported template: {op.template}")
        self._validate_required_params(op)
        text = self._templates.render_operation(op)
        severity, diff = self._guard.verify_contract(text, self._contracts.build_contract(op))
        if diff is not None:
            raise SecurityViolationError(
                message=f"symbolic renderer contract violation ({severity.value}): {diff.to_prompt_text()}",
                expected={"template": op.template}, actual=diff.to_dict(),
                operation_id=op.operation_id, device=op.device,
            )
        return text.strip()

    def render_plan(self, plan: PatchPlan) -> SymbolicRenderResult:
        by_device: Dict[str, List[str]] = {}
        result = SymbolicRenderResult(operation_count=len(plan.operations))
        for op in sorted(plan.operations, key=lambda item: (item.device, item.order, item.operation_id or "")):
            try:
                by_device.setdefault(op.device, []).append(self.render_operation(op))
            except UnsupportedSymbolicOperation as exc:
                result.unsupported_operations.append({
                    "operation_id": op.operation_id or "", "device": op.device,
                    "template": op.template, "reason": str(exc),
                })
        result.rendered_configs = {device: "\n".join(parts) + "\n" for device, parts in by_device.items()}
        return result
