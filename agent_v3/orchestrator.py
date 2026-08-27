"""Fail-closed orchestration policy for the tool-augmented v3 agent."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from common.llm_driver import clear_intent_deadline, clear_max_llm_calls, get_driver, get_intent_llm_usage, set_intent_deadline, set_max_llm_calls

from .schema import AgentOutcome, TraceEvent
from .tools import TrustedToolbox


def _canonical(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, list):
        value = [x.model_dump() if hasattr(x, "model_dump") else x for x in value]
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class ToolAugmentedAgent:
    """An LLM may select read-only inspection tools but cannot skip gates."""

    inspection_allowlist = {"inspect_brownfield_context", "infer_local_conventions"}

    def __init__(self, toolbox: TrustedToolbox, driver: Any | None = None):
        self.toolbox = toolbox
        self.driver = driver or get_driver()
        self.trace: List[TraceEvent] = []

    def _record(self, kind: str, name: str, status: str, started: float, input_value: Any,
                output: Any, detail: Dict[str, Any] | None = None) -> Any:
        self.trace.append(TraceEvent(len(self.trace) + 1, kind, name, status,
            datetime.now(timezone.utc).isoformat(), round(time.monotonic() - started, 6),
            _sha(input_value), _sha(output), detail or {}))
        return output

    def _tool(self, name: str, fn: Callable[..., Any], *args: Any) -> Any:
        started = time.monotonic()
        try:
            return self._record("tool", name, "ok", started, args, fn(*args))
        except Exception as exc:
            self._record("tool", name, "failed", started, args, {"error": str(exc)}, {"error": str(exc)})
            raise

    def _decide_inspection(self, topology: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
        started = time.monotonic()
        prompt = {"topology_summary": topology, "current_policy_summary": current,
                  "allowed_tools": sorted(self.inspection_allowlist),
                  "instruction": "Choose zero or more tools useful before planning. Return JSON {tools:[...], rationale:string}."}
        raw = self.driver.chat_completion(
            "You are a constrained network-change inspection router. You cannot render config or waive safety gates.",
            _canonical(prompt), json_mode=True, temperature=0.0, max_tokens=400, max_retries=2)
        try:
            parsed = json.loads(raw)
            requested = parsed.get("tools", [])
            if not isinstance(requested, list) or any(x not in self.inspection_allowlist for x in requested):
                raise ValueError("tool selection outside allowlist")
            selected = list(dict.fromkeys(str(x) for x in requested))
        except Exception as exc:
            self._record("llm_decision", "select_inspection_tools", "blocked", started, prompt, raw,
                         {"raw_response": raw, "parse_error": str(exc)})
            raise RuntimeError(f"invalid agent decision: {exc}") from exc
        self._record("llm_decision", "select_inspection_tools", "ok", started, prompt, parsed,
                     {"raw_response": raw, "selected_tools": selected})
        return selected

    def run(self) -> AgentOutcome:
        set_intent_deadline(120)
        set_max_llm_calls(2)
        try:
            topology = self._tool("inspect_topology", self.toolbox.inspect_topology)
            current = self._tool("inspect_current_policy", self.toolbox.inspect_current_policy)
            for name in self._decide_inspection(topology, current):
                self._tool(name, getattr(self.toolbox, name))
            policy = self._tool("solve_policy_parameters", self.toolbox.solve_policy_parameters)
            plans = self._tool("plan_safe_patch", self.toolbox.plan_safe_patch, policy)
            patches, unsupported = self._tool("render_deterministic_fragment", self.toolbox.render_deterministic_fragment, plans)
            if unsupported:
                self._record("gate", "unsupported_fragment_hard_stop", "unsupported", time.monotonic(), unsupported, {},
                             {"reason": "LLM fragment repair path is not released in prototype; never bypass contract"})
                return AgentOutcome("unsupported", {}, "unsupported deterministic fragment", self.trace, get_intent_llm_usage())
            contract = self._tool("check_patch_contract", self.toolbox.check_patch_contract, plans, patches)
            if not contract["ok"]:
                self._record("gate", "contract_hard_stop", "blocked", time.monotonic(), contract, {}, contract)
                return AgentOutcome("fail_closed", {}, "patch contract violation", self.trace, get_intent_llm_usage())
            syntax = self._tool("validate_frr", self.toolbox.validate_frr, patches)
            if not syntax["ok"]:
                self._record("gate", "final_verifier", "blocked", time.monotonic(), syntax, {}, syntax)
                return AgentOutcome("fail_closed", {}, "FRR validation failed", self.trace, get_intent_llm_usage())
            self._record("verdict", "final_verifier", "ok", time.monotonic(), syntax, {"release_eligible": True})
            return AgentOutcome("release_eligible", patches, None, self.trace, get_intent_llm_usage())
        except Exception as exc:
            return AgentOutcome("fail_closed", {}, str(exc), self.trace, get_intent_llm_usage())
        finally:
            clear_max_llm_calls()
            # Capture usage before clear_intent_deadline resets it in returned outcome.
            clear_intent_deadline()
