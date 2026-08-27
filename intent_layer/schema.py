from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SUPPORTED_INTENT_TYPES: List[str] = [
    "prefer_with_backup",
    "ecmp",
    "ordered_preference",
    "pin_to_exit",
    "avoid_exit",
    "path_migration",
    "ospf_steering",
]


class IntentCard(BaseModel):
    intent_id: str
    type: Literal[
        "prefer_with_backup",
        "ecmp",
        "ordered_preference",
        "pin_to_exit",
        "avoid_exit",
        "path_migration",
        "ospf_steering",
    ]
    scope: Literal["prefix", "as", "neighbor"] = "prefix"
    prefix: Optional[str] = None
    src_as: Optional[int] = None
    primary_exit: Optional[str] = None
    backup_exit: Optional[str] = None
    exits: Optional[List[str]] = None
    ordered_exits: Optional[List[str]] = None  # for ordered_preference type
    pinned_exit: Optional[str] = None
    avoid_exits: Optional[List[str]] = None
    constraints: Dict[str, Any] = Field(default_factory=dict)
    
    # path_migration specific fields
    prefixes: Optional[List[str]] = None  # Multiple prefixes for migration
    new_exit: Optional[str] = None        # Target exit for migration
    old_exits: Optional[List[str]] = None # Exits to migrate away from
    mode: Optional[Literal["soft", "hard"]] = "soft"  # Migration mode
    
    @model_validator(mode='before')
    @classmethod
    def normalize_legacy_types(cls, data: Any) -> Any:
        if isinstance(data, dict):
            t = data.get("type")
            if t == "prefer":
                data["type"] = "prefer_with_backup"
            elif t == "avoid":
                data["type"] = "avoid_exit"
            elif t == "pin":
                data["type"] = "pin_to_exit"
            elif t == "migration":
                data["type"] = "path_migration"
        return data

    def normalized_ordered_exits(self) -> List[str]:
        """Return ordered exits list, falling back to exits if needed."""
        if self.ordered_exits:
            return list(self.ordered_exits)
        if self.exits:
            return list(self.exits)
        return []

    def normalized_avoid_exits(self) -> List[str]:
        """Return avoid exits list (or empty list if unset)."""
        return list(self.avoid_exits) if self.avoid_exits else []


class Assertion(BaseModel):
    id: str
    intent_id: str
    kind: str
    params: Dict[str, Any] = Field(default_factory=dict)


class TestCase(BaseModel):
    id: str
    intent_id: str
    kind: str
    device: Optional[str] = None
    devices: Optional[List[str]] = None
    prefix: Optional[str] = None
    expected: Dict[str, Any] = Field(default_factory=dict)


def get_intentcard_schema() -> Dict[str, Any]:
    return IntentCard.model_json_schema()
