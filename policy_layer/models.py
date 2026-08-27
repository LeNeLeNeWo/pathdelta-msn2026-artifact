from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# IntentCard is now unified in intent_layer.schema (Pydantic model)
# All modules should import from: pathdelta.intent_layer.schema.IntentCard


@dataclass
class PolicySketch:
    """
    Structured representation of CurrentPolicySketch.yaml.
    Replaces raw dict for better type safety and clarity.
    """
    global_: Dict[str, Any] = field(default_factory=dict)
    bgp_style: Dict[str, Any] = field(default_factory=dict)
    ospf_style: Dict[str, Any] = field(default_factory=dict)
    risk_profile: Dict[str, Any] = field(default_factory=dict)
    roles: Dict[str, List[str]] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    existing_objects: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicySketch":
        """Construct PolicySketch from raw YAML dict."""
        return cls(
            global_=data.get("global", {}),
            bgp_style=data.get("bgp_style", {}),
            ospf_style=data.get("ospf_style", {}),
            risk_profile=data.get("risk_profile", {}),
            roles=data.get("roles", {}),
            capabilities=data.get("capabilities", {}),
            existing_objects=data.get("existing_objects", {}),
        )


@dataclass
class AffectScope:
    affected_devices: List[str] = field(default_factory=list)
    affected_neighbors: Dict[str, List[str]] = field(default_factory=dict)
    # 可选：用于区分“出口集合(用于 tier/参数求解)”与“sources(用于 OSPF steering)”
    # 不强制所有调用方填写；缺失时可回退到 affected_devices。
    exits: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    # sources 的推断来源（roles/heuristic/none），用于可解释性与调试（可选）
    sources_source: str = ""
    
    # NEW: Unified output for IGP steering (Requirements 9.1, 9.2)
    # Edges requiring cost modifications for OSPF steering
    touched_edges: List[Tuple[str, str]] = field(default_factory=list)
    # New cost values for edges (OSPF)
    cost_overrides: Dict[str, int] = field(default_factory=dict)


@dataclass
class PolicyEntry:
    intent_id: str
    type: str
    proto: str
    mechanism: str
    scope: str
    prefix: Optional[str] = None
    src_as: Optional[int] = None
    primary_exit: Optional[str] = None
    backup_exit: Optional[str] = None
    ordered_exits: Optional[List[str]] = None
    pinned_exit: Optional[str] = None
    avoid_exits: Optional[List[str]] = None
    preference_tiers: Optional[Dict[str, int]] = None
    affected_devices: List[str] = field(default_factory=list)
    affected_neighbors: Dict[str, List[str]] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    # 量化变更代价/爆炸半径的简要视图，使用原生类型便于序列化
    change_footprint: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RolePolicy:
    policies: List[PolicyEntry] = field(default_factory=list)


@dataclass
class PolicySketchDiff:
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
