"""Schemas shared by the v3 agent and its audit traces."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class TraceEvent:
    sequence: int
    kind: Literal["tool", "llm_decision", "gate", "verdict"]
    name: str
    status: Literal["ok", "failed", "unsupported", "blocked"]
    started_at: str
    duration_seconds: float
    input_sha256: str
    output_sha256: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentOutcome:
    status: Literal["release_eligible", "fail_closed", "unsupported"]
    patches: Dict[str, str] = field(default_factory=dict)
    reason: Optional[str] = None
    trace: List[TraceEvent] = field(default_factory=list)
    llm_usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["trace"] = [event.to_dict() for event in self.trace]
        return value
