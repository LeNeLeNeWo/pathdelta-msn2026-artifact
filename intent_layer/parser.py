from __future__ import annotations

import ipaddress
import json
from typing import Any, Dict
import re

from .llm_client import call_llm_for_intent, LLMClientError
from .registry import get_handler, list_supported_types
from .schema import IntentCard


class UnsupportedIntentType(Exception):
    pass


class UnsupportedScope(Exception):
    pass


class IntentValidationError(Exception):
    pass


class IntentRequestRejected(Exception):
    """A natural-language request needs clarification or is unsafe to apply."""

    def __init__(self, decision: str, reason: str):
        super().__init__(f"{decision}: {reason}")
        self.decision = decision
        self.reason = reason


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_block(text: str) -> str:
    """
    Extract JSON content from LLM output that may include markdown fences or extra text.
    """
    if not text:
        return text
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    if "{" in text and "}" in text and text.find("{") < text.rfind("}"):
        return text[text.find("{") : text.rfind("}") + 1].strip()
    return text.strip()


def _validate_intent_fields(intent: IntentCard, topology: Dict[str, Any], check_topology: bool = True) -> None:
    if check_topology:
        validate_topology_coverage(intent, topology)

    if intent.prefix:
        try:
            ipaddress.ip_network(intent.prefix, strict=False)
        except ValueError as exc:
            raise IntentValidationError(f"Invalid prefix: {intent.prefix}") from exc

    # Minimal required-field checks per type
    if intent.type == "prefer_with_backup":
        if not (intent.prefix and intent.primary_exit and intent.backup_exit):
            raise IntentValidationError(
                "prefer_with_backup requires prefix, primary_exit, backup_exit"
            )

    elif intent.type == "ecmp":
        exits = intent.exits or []
        if not (intent.prefix and len(exits) >= 2):
            raise IntentValidationError(
                "ecmp requires prefix and exits (>=2)"
            )

    elif intent.type == "ordered_preference":
        ordered = intent.normalized_ordered_exits()
        if not (intent.prefix and len(ordered) >= 2):
            raise IntentValidationError(
                "ordered_preference requires prefix and an ordered_exits list (>=2)"
            )

    elif intent.type == "pin_to_exit":
        if not (intent.prefix and intent.pinned_exit):
            raise IntentValidationError(
                "pin_to_exit requires prefix and pinned_exit"
            )

    elif intent.type == "avoid_exit":
        avoids = intent.normalized_avoid_exits()
        if not (intent.prefix and len(avoids) >= 1):
            raise IntentValidationError(
                "avoid_exit requires prefix and at least one avoid_exits entry"
            )

    elif intent.type == "path_migration":
        # Validate prefixes is non-empty list
        if not intent.prefixes or len(intent.prefixes) == 0:
            raise IntentValidationError(
                "path_migration requires non-empty prefixes list"
            )
        # Validate new_exit is present
        if not intent.new_exit:
            raise IntentValidationError(
                "path_migration requires new_exit"
            )
        # Validate each prefix is a valid IP network
        for prefix in intent.prefixes:
            try:
                ipaddress.ip_network(prefix, strict=False)
            except ValueError as exc:
                raise IntentValidationError(f"Invalid prefix in path_migration: {prefix}") from exc




def validate_topology_coverage(intent: IntentCard, topology: Dict[str, Any]) -> None:
    """
    Ensure devices referenced in intent exist in topology. Raises IntentValidationError on mismatch.
    """
    nodes = set((topology or {}).get("nodes", {}).keys())

    def check_node(name: str) -> None:
        if name and name not in nodes:
            raise IntentValidationError(f"Device '{name}' not found in topology nodes: {nodes}")

    to_check = set()

    if intent.primary_exit:
        to_check.add(intent.primary_exit)
    if intent.backup_exit:
        to_check.add(intent.backup_exit)
    if intent.exits:
        to_check.update(intent.exits)
    to_check.update(intent.normalized_ordered_exits())
    to_check.update(intent.normalized_avoid_exits())
    if intent.pinned_exit:
        to_check.add(intent.pinned_exit)
    # path_migration specific fields
    if intent.new_exit:
        to_check.add(intent.new_exit)
    if intent.old_exits:
        to_check.update(intent.old_exits)

    for node in to_check:
        check_node(node)


def parse_intent_text(intent_text: str, topology: Dict[str, Any], check_topology: bool = True) -> IntentCard:
    """
    Convert natural language intent text to an IntentCard via LLM + validation.
    """
    try:
        llm_json = call_llm_for_intent(intent_text)
    except LLMClientError:
        raise
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc

    cleaned = _extract_json_block(llm_json)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned non-JSON: {cleaned}") from exc

    decision = str(data.get("decision", "accept")).lower()
    if decision in {"clarify", "reject"}:
        raise IntentRequestRejected(decision, str(data.get("reason", "unspecified")))
    if decision != "accept":
        raise RuntimeError(f"LLM returned unknown intent decision: {decision}")
    data.pop("decision", None)
    data.pop("reason", None)

    # Backward compatibility: convert avoid_exit -> avoid_exits if present
    if "avoid_exit" in data and not data.get("avoid_exits"):
        val = data.pop("avoid_exit")
        if val:
            data["avoid_exits"] = [val]
    # Drop avoid_exit key to satisfy single-field schema
    data.pop("avoid_exit", None)

    intent = IntentCard(**data)
    if intent.type not in list_supported_types():
        raise UnsupportedIntentType(f"Intent type '{intent.type}' not supported.")
    if intent.scope != "prefix":
        raise UnsupportedScope("Only scope='prefix' is supported currently; other scopes are reserved for future use.")

    # Normalize ordered_exits vs exits for backward compatibility
    if intent.type == "ordered_preference" and not intent.ordered_exits and intent.exits:
        # LLM may have output 'exits' instead of 'ordered_exits'; auto-convert
        intent.ordered_exits = intent.exits
        intent.exits = None

    handler = get_handler(intent.type)
    if handler:
        intent = handler.postprocess_intent(intent, None)
    _validate_intent_fields(intent, topology, check_topology=check_topology)
    return intent
