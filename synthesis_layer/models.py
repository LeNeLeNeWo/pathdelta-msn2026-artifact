"""
Synthesis Layer Data Models

Pydantic models for PatchPlan, SynthesisReport, and related structures.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


@dataclass
class StepAnalysis:
    """
    Result of step/grid detection for a parameter type.
    
    Used for style-aware parameter quantization to ensure generated values
    align with the brownfield network's existing conventions.
    
    Attributes:
        detected_step: The GCD-based step (clamped to [5, 100])
        existing_values: Sorted list of existing values
        baseline_value: Mode or median of existing values
        is_chaotic: True if GCD < 5 (chaotic pattern detected)
    """
    detected_step: int
    existing_values: List[int]
    baseline_value: int
    is_chaotic: bool


@dataclass
class TopEntryInfo:
    """
    Information about the top (lowest sequence) route-map entry.
    
    Used for active shadowing prevention to detect unsafe PREPEND scenarios.
    A top entry is considered unsafe if it has a deny action or has no match
    clauses (catch-all/match-any), as prepending before such entries could
    shadow critical security policies.
    
    Attributes:
        sequence: The sequence number of the top entry
        action: The action of the entry ("permit" or "deny")
        has_match_clauses: True if entry has explicit match clauses, False for catch-all
        is_catchall_prefix_list: True if entry matches a catch-all prefix-list
                                 (e.g., permit 0.0.0.0/0 or permit any)
    
    Requirements: 2.3, 4.3, 4.4
    """
    sequence: int
    action: str
    has_match_clauses: bool
    is_catchall_prefix_list: bool = False


@dataclass
class RouteMapHeadroom:
    """
    Headroom analysis for a route-map.
    
    Used for safe shadowing via prepend/rebind strategy to ensure new
    route-map entries take effect before existing catch-all rules.
    
    Attributes:
        route_map_name: Name of the route-map being analyzed
        min_sequence: Minimum (lowest) sequence number among all entries, None if no entries
        max_sequence: Maximum (highest) sequence number among all entries, None if no entries
        headroom: Available sequence number space before min_sequence (min_sequence - 1),
                  or a large value (999999) if unlimited (no existing entries)
        has_catchall_risk: True if sequence 10 contains a "permit any" or catch-all pattern
        existing_sequences: List of all existing sequence numbers in the route-map
    
    Requirements: 4.3
    """
    route_map_name: str
    min_sequence: Optional[int]
    max_sequence: Optional[int]
    headroom: int
    has_catchall_risk: bool
    existing_sequences: List[int]


class InsertionStrategy(str, Enum):
    """
    Strategy for inserting new route-map entries.
    
    Used for safe shadowing to ensure new entries take effect before
    existing catch-all rules.
    
    Values:
        APPEND: Add after existing entries (legacy, risky if catch-all exists)
        PREPEND: Add before existing entries (safe, ensures new rules execute first)
        REBIND: Create new route-map and swap neighbor binding (when no headroom for prepend)
    
    Requirements: 5.2, 6.1
    """
    APPEND = "append"
    PREPEND = "prepend"
    REBIND = "rebind"


@dataclass
class InsertionDecision:
    """
    Decision on how to insert a new route-map entry.
    
    Contains the strategy to use and all necessary information to execute it.
    
    Attributes:
        strategy: The insertion strategy to use (APPEND, PREPEND, or REBIND)
        target_sequence: For PREPEND/APPEND, the sequence number to use; None for REBIND
        new_route_map_name: For REBIND, the name of the new route-map; None otherwise
        original_route_map_name: The original route-map name being modified
        reason: Human-readable explanation of why this strategy was chosen
    
    Requirements: 5.2, 6.1
    """
    strategy: InsertionStrategy
    target_sequence: Optional[int]
    new_route_map_name: Optional[str]
    original_route_map_name: str
    reason: str


@dataclass
class FootprintVector:
    """
    Standardized footprint metrics for radar chart visualization.
    
    All metrics are designed to be comparable:
    - Lower is better for devices_touched, objects_touched, lines_changed
    - Higher is better for safety_score (1.0 = safe, 0.0 = unsafe)
    
    Attributes:
        metric_devices_touched: Count of unique devices in the patch plan
        metric_objects_touched: Count of unique route-map and prefix-list names
        metric_lines_changed: Count of non-empty, non-comment configuration lines
        metric_safety_score: 1.0 if prefix-isolation is enforced, 0.0 otherwise
        ospf_cost_changes: Count of OSPF interface cost modifications
    """
    metric_devices_touched: int
    metric_objects_touched: int
    metric_lines_changed: int
    metric_safety_score: float
    ospf_cost_changes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for CSV serialization."""
        return {
            "metric_devices_touched": self.metric_devices_touched,
            "metric_objects_touched": self.metric_objects_touched,
            "metric_lines_changed": self.metric_lines_changed,
            "metric_safety_score": self.metric_safety_score,
            "ospf_cost_changes": self.ospf_cost_changes,
        }


class ReuseStatus(str, Enum):
    """Status of an existing configuration object."""
    EXISTS = "EXISTS"
    MISSING = "MISSING"


class ReuseStrategy(str, Enum):
    """Strategy for handling configuration objects."""
    APPEND = "APPEND"
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"  # Deprecated - should not be used for route-maps


class ObjectType(str, Enum):
    """Types of configuration objects."""
    ROUTE_MAP = "route_map"
    PREFIX_LIST = "prefix_list"
    COMMUNITY_LIST = "community_list"
    AS_PATH_LIST = "as_path_list"
    INTERFACE = "interface"


class ReuseContext(BaseModel):
    """
    Context for a single configuration object reuse decision.
    
    Attributes:
        object_type: Type of the configuration object
        status: Whether the object exists or is missing
        strategy: Whether to append to existing or create new
        target_name: The object name to use (existing or proposed new)
        existing_sequences: For route-maps, list of existing sequence numbers
        next_sequence: Suggested next sequence number for appending
    """
    object_type: ObjectType
    status: ReuseStatus
    strategy: ReuseStrategy
    target_name: str
    existing_sequences: List[int] = Field(default_factory=list)
    next_sequence: int = 10
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeviceReuseContext(BaseModel):
    """
    Aggregated reuse context for a single device.
    
    Attributes:
        device_name: Name of the device
        route_maps: Reuse context for route-maps keyed by neighbor
        prefix_lists: Reuse context for prefix-lists keyed by prefix
        interfaces: Reuse context for interfaces keyed by interface name
    """
    device_name: str
    route_maps: Dict[str, ReuseContext] = Field(default_factory=dict)
    prefix_lists: Dict[str, ReuseContext] = Field(default_factory=dict)
    interfaces: Dict[str, ReuseContext] = Field(default_factory=dict)


class PatchOperation(BaseModel):
    """
    A single abstract patch operation.
    
    Attributes:
        op: Operation type (append, create, modify)
        template: Template name to use
        device: Target device name
        params: Parameters to pass to the template
        order: Execution order (lower = earlier)
        depends_on: List of operation IDs this depends on
    """
    op: ReuseStrategy
    template: str
    device: str
    params: Dict[str, Any] = Field(default_factory=dict)
    order: int = 0
    depends_on: List[str] = Field(default_factory=list)
    operation_id: Optional[str] = None


class PatchPlan(BaseModel):
    """
    Complete patch plan for an intent.
    
    Attributes:
        intent_id: The intent this plan implements
        operations: List of patch operations in execution order
        prefix_isolation_enforced: Whether prefix isolation is guaranteed
        affected_devices: List of devices that will be modified
        protocol: Primary protocol (bgp/ospf)
    """
    intent_id: str
    operations: List[PatchOperation] = Field(default_factory=list)
    prefix_isolation_enforced: bool = False
    affected_devices: List[str] = Field(default_factory=list)
    protocol: str = "bgp"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SynthesisReport(BaseModel):
    """
    Report on synthesis results including footprint metrics.
    
    Attributes:
        intent_id: The intent that was synthesized
        lines_changed: Count of new/modified configuration lines
        objects_touched: Count of configuration objects modified
        safety_check: Whether prefix-isolation is present in output
        devices_affected: List of affected device names
        warnings: Any warnings generated during synthesis
        rendered_configs: Map of device -> rendered config snippet
        syntax_valid: Whether the configuration passed static syntax checking
        dynamic_valid: Whether the configuration passed dynamic emulation verification
        convergence_time: Time taken for protocol convergence (seconds, 0.0 if not measured)
    """
    intent_id: str
    lines_changed: int = 0
    objects_touched: int = 0
    safety_check: bool = False
    devices_affected: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    rendered_configs: Dict[str, str] = Field(default_factory=dict)
    
    # Detailed breakdown
    prefix_lists_created: int = 0
    route_maps_modified: int = 0
    interfaces_modified: int = 0
    
    # Verification funnel fields
    syntax_valid: bool = False
    dynamic_valid: bool = False
    convergence_time: float = 0.0
