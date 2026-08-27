"""
Patch Planner for Synthesis Layer

Generates abstract PatchPlan from RolePolicy + ReuseContext.
Implements the "Compiler" that enforces safety constraints.
"""
from __future__ import annotations

import logging
import math
import re
import uuid
import warnings
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from policy_layer.models import PolicyEntry, RolePolicy

from .models import (
    DeviceReuseContext,
    InsertionDecision,
    InsertionStrategy,
    PatchOperation,
    PatchPlan,
    ReuseContext,
    ReuseStatus,
    ReuseStrategy,
    RouteMapHeadroom,
    TopEntryInfo,
)

if TYPE_CHECKING:
    from .context_analyzer import ContextAnalyzer

# Logger for PatchPlanner
logger = logging.getLogger(__name__)


def calculate_safe_fallback_seq(
    existing_seqs: List[int],
    step: int = 100,
    default_base: int = 10,
    max_sequence: int = 65535,
) -> int:
    """
    Calculate a safe fallback sequence number that avoids collisions.
    
    This function determines the appropriate sequence number for fallback entries
    in REBIND strategy route-maps, ensuring they don't collide with existing
    high sequence numbers.
    
    Args:
        existing_seqs: List of existing sequence numbers in the route-map
        step: Step size to add after max (detected or default 100)
        default_base: Base value when no existing sequences (default 10)
        max_sequence: Maximum allowed sequence number (FRR limit 65535)
    
    Returns:
        Safe sequence number for fallback entry
    
    Logic:
        - If existing_seqs is empty: return default_base + step
        - If >= 2 values exist: detect step from GCD of differences
        - If GCD < 5 (chaotic pattern): use default step
        - Return min(max(existing_seqs) + detected_step, max_sequence)
    
    Requirements: 1.1, 1.2, 1.3, 1.4, 1.6
    """
    # Filter out invalid (negative) values
    valid_seqs = [s for s in existing_seqs if s >= 0]
    
    # Case: No existing sequences - return default_base + step
    if not valid_seqs:
        return min(default_base + step, max_sequence)
    
    # Detect step from GCD of differences if >= 2 values exist
    detected_step = step  # Default step
    if len(valid_seqs) >= 2:
        sorted_seqs = sorted(valid_seqs)
        differences = [sorted_seqs[i+1] - sorted_seqs[i] for i in range(len(sorted_seqs) - 1)]
        
        # Calculate GCD of all differences
        if differences:
            gcd_value = differences[0]
            for diff in differences[1:]:
                gcd_value = math.gcd(gcd_value, diff)
            
            # Use detected step if GCD >= 5 (not chaotic)
            if gcd_value >= 5:
                detected_step = gcd_value
    
    # Calculate result: max(existing_seqs) + detected_step
    result = max(valid_seqs) + detected_step
    
    # Clamp to max_sequence
    if result > max_sequence:
        logger.warning(
            f"Fallback sequence {result} exceeds max {max_sequence}, clamping"
        )
        result = max_sequence
    
    return result


def generate_unique_name(
    base_name: str,
    existing_names: set,
    max_suffix: int = 99,
) -> str:
    """
    Generate a unique name by appending numeric suffix if collision detected.
    
    This function ensures that generated names do not collide with existing
    names in the configuration. If the base_name already exists, it appends
    _1, _2, etc. until a unique name is found.
    
    Args:
        base_name: Proposed name
        existing_names: Set of existing names to avoid collision with
        max_suffix: Maximum suffix number to try (default 99)
    
    Returns:
        Unique name (base_name or base_name_N)
    
    Raises:
        ValueError: If cannot generate unique name within max_suffix attempts
    
    Requirements: 5.4
    """
    # If base_name doesn't collide, return it directly
    if base_name not in existing_names:
        return base_name
    
    # Try appending _1, _2, etc. until we find a unique name
    for suffix in range(1, max_suffix + 1):
        candidate = f"{base_name}_{suffix}"
        if candidate not in existing_names:
            logger.info(
                f"Name collision detected for '{base_name}', "
                f"using unique name '{candidate}'"
            )
            return candidate
    
    # Could not find a unique name within max_suffix attempts
    raise ValueError(
        f"Cannot generate unique name for '{base_name}': "
        f"all suffixes from _1 to _{max_suffix} are taken"
    )


class PatchPlanner:
    """
    Generates PatchPlan from RolePolicy and ReuseContext.
    
    Core Constraints:
    1. Prefix Isolation: ALL BGP intents MUST have prefix-list match before any set
    2. OSPF Direct Mapping: touched_edges map directly to interface cost patches
    3. Minimization: Use APPEND strategy when possible
    
    Supports optional ContextAnalyzer for style-aware neural synthesis.
    When provided, the analyzer is passed to ConfigRenderer for RAG-based generation.
    """
    
    # Constants for insertion strategy decisions
    PREPEND_OFFSET = 5      # Insert at min_seq - 5
    MIN_HEADROOM = 5        # Minimum headroom required for PREPEND
    
    def __init__(
        self,
        context_analyzer: Optional["ContextAnalyzer"] = None,
        naive_mode: bool = False,
    ):
        """
        Initialize the PatchPlanner.
        
        Args:
            context_analyzer: Optional ContextAnalyzer with style_snippets populated
                             for RAG-based neural synthesis. When provided, it will
                             be passed to ConfigRenderer during plan rendering.
            naive_mode: If True, force PREPEND strategy and disable catch-all/deny
                       detection. Used for ShadowSafe ablation experiments (E3).
                       Default is False (full ShadowSafe protection enabled).
        
        **Validates: Requirements 3.4 (ShadowSafe naive mode)**
        """
        self._operation_counter = 0
        self.context_analyzer = context_analyzer
        self.naive_mode = naive_mode
    
    def decide_insertion_strategy(
        self,
        headroom: RouteMapHeadroom,
        num_entries_needed: int = 1,
        top_entry_info: Optional[TopEntryInfo] = None,
    ) -> InsertionDecision:
        """
        Decide insertion strategy based on headroom analysis.
        
        Implements safe shadowing logic to ensure new route-map entries
        take effect before existing catch-all rules.
        
        Strategy Selection:
        - Case A (No Existing Entries): Use APPEND with standard sequence 10
        - Case B (Unsafe Top Entry): Force REBIND if top entry is deny or catch-all
        - Case C (Has Headroom): min_seq >= MIN_HEADROOM + PREPEND_OFFSET
            -> PREPEND at min_seq - PREPEND_OFFSET
        - Case D (No Headroom): min_seq < MIN_HEADROOM
            -> REBIND: create new route-map, swap binding
        
        For multiple entries needing prepend, they are spaced by 1:
        - First entry: min_seq - PREPEND_OFFSET
        - Second entry: min_seq - PREPEND_OFFSET - 1
        - etc.
        
        When naive_mode is True, this method forces PREPEND strategy and
        disables catch-all/deny detection. This is used for ShadowSafe
        ablation experiments (E3) to demonstrate the value of the safety checks.
        
        Args:
            headroom: RouteMapHeadroom analysis result from ContextAnalyzer
            num_entries_needed: Number of entries that need to be inserted (default 1)
            top_entry_info: Optional information about the top (lowest sequence) entry
                           for active shadowing prevention
        
        Returns:
            InsertionDecision with strategy, target_sequence, and reason
        
        Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.4, 5.1, 5.3, 5.5, 6.2
        """
        # NAIVE MODE: Force PREPEND and disable catch-all/deny detection
        # Used for ShadowSafe ablation experiments (E3)
        # **Validates: Requirements 3.4**
        if self.naive_mode:
            # In naive mode, always use PREPEND regardless of safety concerns
            if headroom.min_sequence is None:
                target_sequence = 10
            else:
                target_sequence = max(1, headroom.min_sequence - self.PREPEND_OFFSET)
            
            return InsertionDecision(
                strategy=InsertionStrategy.PREPEND,
                target_sequence=target_sequence,
                new_route_map_name=None,
                original_route_map_name=headroom.route_map_name,
                reason="NAIVE MODE: Forced PREPEND (safety checks disabled)",
            )
        
        # Case A: No existing entries - safe to use standard sequence
        if headroom.min_sequence is None:
            return InsertionDecision(
                strategy=InsertionStrategy.APPEND,
                target_sequence=10,
                new_route_map_name=None,
                original_route_map_name=headroom.route_map_name,
                reason="No existing entries, using standard sequence 10",
            )
        
        # Case B: Check for unsafe top entry BEFORE considering PREPEND
        # Requirements: 2.1, 2.2, 2.4, 2.5, 4.5
        if top_entry_info is not None:
            is_unsafe = (
                top_entry_info.action == "deny" or
                not top_entry_info.has_match_clauses or
                top_entry_info.is_catchall_prefix_list  # NEW: catch-all prefix-list detection
            )
            if is_unsafe:
                # Force REBIND - PREPEND would shadow critical rule
                new_name = f"{headroom.route_map_name}_PATHDELTA"
                
                # Determine the reason for blocking PREPEND
                if top_entry_info.action == "deny":
                    block_reason = "deny"
                elif top_entry_info.is_catchall_prefix_list:
                    block_reason = "catch-all prefix-list"
                else:
                    block_reason = "catch-all"
                
                logger.warning(
                    f"Blocking PREPEND for {headroom.route_map_name}: "
                    f"top entry (seq {top_entry_info.sequence}) is {block_reason}. "
                    f"Using REBIND strategy instead."
                )
                
                return InsertionDecision(
                    strategy=InsertionStrategy.REBIND,
                    target_sequence=10,
                    new_route_map_name=new_name,
                    original_route_map_name=headroom.route_map_name,
                    reason=f"PREPEND blocked: top entry is unsafe ({block_reason})",
                )
        
        # Calculate available headroom and space needed
        # Available headroom is min_sequence - 1 (sequences must be >= 1)
        available = headroom.min_sequence - 1
        
        # Space needed: PREPEND_OFFSET for first entry, plus (num_entries_needed - 1) for spacing
        # E.g., if min_seq=10, PREPEND_OFFSET=5, num_entries=2:
        #   - First entry at seq 5 (10 - 5)
        #   - Second entry at seq 4 (10 - 5 - 1)
        #   - Need headroom of at least 5 + 1 = 6 (sequences 4 and 5)
        needed = self.PREPEND_OFFSET + num_entries_needed - 1
        
        # Case B: Has headroom for prepend
        if available >= needed:
            target_sequence = headroom.min_sequence - self.PREPEND_OFFSET
            
            # Ensure target_sequence is >= 1
            if target_sequence < 1:
                target_sequence = 1
            
            return InsertionDecision(
                strategy=InsertionStrategy.PREPEND,
                target_sequence=target_sequence,
                new_route_map_name=None,
                original_route_map_name=headroom.route_map_name,
                reason=f"Prepending before seq {headroom.min_sequence}",
            )
        
        # Case C: No headroom - must rebind
        new_name = f"{headroom.route_map_name}_PATHDELTA"
        return InsertionDecision(
            strategy=InsertionStrategy.REBIND,
            target_sequence=10,
            new_route_map_name=new_name,
            original_route_map_name=headroom.route_map_name,
            reason=f"Insufficient headroom (min_seq={headroom.min_sequence}), rebinding to {new_name}",
        )
    
    def _guard_against_delete_operation(
        self,
        strategy: ReuseStrategy,
        route_map_name: str,
        sequence: Optional[int] = None,
    ) -> None:
        """
        Guard against DELETE operations for route-maps.
        
        Raises DeprecationWarning if DELETE strategy is used for route-maps.
        PathDelta should use PREPEND or REBIND strategies instead of DELETE
        to ensure existing configurations are not destroyed.
        
        Args:
            strategy: The ReuseStrategy being used
            route_map_name: Name of the route-map being operated on
            sequence: Optional sequence number being targeted
        
        Raises:
            DeprecationWarning: If DELETE strategy is used for route-maps
        
        Requirements: 7.4, 7.5
        """
        if strategy == ReuseStrategy.DELETE:
            seq_info = f" seq {sequence}" if sequence is not None else ""
            warnings.warn(
                f"DELETE operations for route-maps are deprecated. "
                f"Use PREPEND or REBIND strategy instead. "
                f"Attempted to delete: {route_map_name}{seq_info}",
                DeprecationWarning,
                stacklevel=3,
            )
    
    def _log_shadowing_warning(
        self,
        route_map_name: str,
        existing_sequence: int,
        new_sequence: int,
        device: str,
    ) -> None:
        """
        Log a warning when shadowing is used to override an existing entry.
        
        This is called when a new route-map entry is prepended before an
        existing entry, effectively shadowing (overriding) the existing
        entry's behavior for matching traffic.
        
        Args:
            route_map_name: Name of the route-map being modified
            existing_sequence: The existing sequence number being shadowed
            new_sequence: The new sequence number being inserted
            device: The device where the shadowing is occurring
        
        Requirements: 7.4
        """
        logger.warning(
            f"Shadowing existing route-map entry: {route_map_name} seq {existing_sequence} "
            f"on device {device}. New entry at seq {new_sequence} will take precedence."
        )
    
    def validate_route_map_operation(
        self,
        strategy: ReuseStrategy,
        route_map_name: str,
        sequence: Optional[int] = None,
    ) -> None:
        """
        Validate a route-map operation and raise DeprecationWarning if DELETE is used.
        
        This is the public interface for validating route-map operations.
        It should be called before creating any route-map operation to ensure
        DELETE operations are not used.
        
        Args:
            strategy: The ReuseStrategy being used
            route_map_name: Name of the route-map being operated on
            sequence: Optional sequence number being targeted
        
        Raises:
            DeprecationWarning: If DELETE strategy is used for route-maps
        
        Requirements: 7.4, 7.5
        """
        self._guard_against_delete_operation(strategy, route_map_name, sequence)
    
    def plan(
        self,
        role_policy: RolePolicy,
        reuse_contexts: Dict[str, DeviceReuseContext],
        configs: Optional[Dict[str, str]] = None,
    ) -> List[PatchPlan]:
        """
        Generate patch plans for all policies in a RolePolicy.
        
        Args:
            role_policy: The RolePolicy containing PolicyEntry objects
            reuse_contexts: Map of device -> DeviceReuseContext
            configs: Optional raw configs for additional analysis (headroom, etc.)
        
        Returns:
            List of PatchPlan objects, one per PolicyEntry
        """
        plans: List[PatchPlan] = []
        
        for policy in role_policy.policies:
            plan = self._plan_single_policy(policy, reuse_contexts, configs)
            plans.append(plan)
        
        return plans
    
    def _plan_single_policy(
        self,
        policy: PolicyEntry,
        reuse_contexts: Dict[str, DeviceReuseContext],
        configs: Optional[Dict[str, str]] = None,
    ) -> PatchPlan:
        """Generate a PatchPlan for a single PolicyEntry."""
        
        if policy.proto == "bgp":
            return self._plan_bgp_policy(policy, reuse_contexts, configs)
        elif policy.proto == "ospf":
            return self._plan_ospf_policy(policy, reuse_contexts, configs)
        else:
            # Fallback for unknown protocols
            return PatchPlan(
                intent_id=policy.intent_id,
                operations=[],
                prefix_isolation_enforced=False,
                affected_devices=policy.affected_devices,
                protocol=policy.proto,
                metadata={"warning": f"Unknown protocol: {policy.proto}"},
            )
    
    def _plan_bgp_policy(
        self,
        policy: PolicyEntry,
        reuse_contexts: Dict[str, DeviceReuseContext],
        configs: Optional[Dict[str, str]] = None,
    ) -> PatchPlan:
        """
        Plan BGP policy changes.
        
        CRITICAL CONSTRAINT: Prefix-list match MUST come before any set operations.
        This enforces the "traffic sandbox" safety guarantee.
        
        Implements safe shadowing via PREPEND/REBIND strategies to ensure new
        route-map entries take effect before existing catch-all rules.
        
        Args:
            policy: The PolicyEntry to plan
            reuse_contexts: Map of device -> DeviceReuseContext
            configs: Optional raw configs for headroom analysis
        
        Returns:
            PatchPlan with operations for implementing the policy
        
        Requirements: 6.3, 6.4
        """
        operations: List[PatchOperation] = []
        prefix_isolation_enforced = False
        rebind_metadata: List[Dict[str, Any]] = []

        # ECMP is a process-level BGP knob. It should not be synthesized as a
        # per-neighbor route-map/neighbor-binding patch.
        if policy.mechanism == "maximum_paths":
            max_paths = int((policy.params or {}).get("maximum_paths") or 2)
            for device in policy.affected_devices:
                operations.append(
                    self._create_maximum_paths_operation(
                        device=device,
                        maximum_paths=max_paths,
                        asn=policy.src_as,
                        intent_id=policy.intent_id,
                        order=len(operations),
                    )
                )

            return PatchPlan(
                intent_id=policy.intent_id,
                operations=operations,
                prefix_isolation_enforced=False,
                affected_devices=policy.affected_devices,
                protocol="bgp",
                metadata={
                    "mechanism": policy.mechanism,
                    "maximum_paths": max_paths,
                },
            )
        
        # Extract prefix from policy
        prefix = policy.prefix
        if not prefix:
            # Safety violation - cannot proceed without prefix
            return PatchPlan(
                intent_id=policy.intent_id,
                operations=[],
                prefix_isolation_enforced=False,
                affected_devices=policy.affected_devices,
                protocol="bgp",
                metadata={"error": "No prefix specified - prefix isolation violated"},
            )
        
        # For each affected device, generate operations
        for device in policy.affected_devices:
            device_context = reuse_contexts.get(device)
            neighbors = policy.affected_neighbors.get(device, [])
            config_text = configs.get(device) if configs else None
            
            if not neighbors:
                continue
            
            for neighbor in neighbors:
                # Step 1: ALWAYS create/ensure prefix-list first (SAFETY CONSTRAINT)
                prefix_list_op = self._create_prefix_list_operation(
                    device=device,
                    prefix=prefix,
                    context=device_context,
                    intent_id=policy.intent_id,
                    order=0,  # Prefix-list MUST be first
                )
                operations.append(prefix_list_op)
                prefix_isolation_enforced = True
                
                # Check if REBIND strategy is needed before creating route-map operation
                original_route_map_name = None
                needs_rebind = False
                rebind_decision = None
                
                if device_context and neighbor in device_context.route_maps:
                    rm_context = device_context.route_maps[neighbor]
                    if rm_context.existing_sequences and self.context_analyzer and config_text:
                        # Analyze headroom
                        headroom = self.context_analyzer.analyze_route_map_headroom(
                            config_text, rm_context.target_name
                        )
                        
                        # Extract top entry info for active shadowing prevention
                        # Requirements: 2.1, 2.2
                        top_entry_info = self.context_analyzer.extract_top_entry_info(
                            config_text, rm_context.target_name
                        )
                        
                        rebind_decision = self.decide_insertion_strategy(
                            headroom, 
                            num_entries_needed=1,
                            top_entry_info=top_entry_info,
                        )
                        
                        if rebind_decision.strategy == InsertionStrategy.REBIND:
                            needs_rebind = True
                            original_route_map_name = rm_context.target_name
                
                # Step 2: Create/append route-map with prefix-list match
                route_map_op = self._create_route_map_operation(
                    device=device,
                    neighbor=neighbor,
                    prefix=prefix,
                    prefix_list_name=prefix_list_op.params.get("prefix_list_name", ""),
                    policy=policy,
                    context=device_context,
                    intent_id=policy.intent_id,
                    order=1,  # Route-map comes after prefix-list
                    depends_on=[prefix_list_op.operation_id] if prefix_list_op.operation_id else [],
                    config_text=config_text,
                )
                operations.append(route_map_op)
                
                # Step 3: Handle REBIND - copy fallback logic and swap binding
                if needs_rebind and rebind_decision and original_route_map_name:
                    new_route_map_name = route_map_op.params.get("route_map_name", "")
                    
                    # Get existing sequences from route-map context for dynamic fallback calculation
                    existing_seqs = []
                    if device_context and neighbor in device_context.route_maps:
                        existing_seqs = device_context.route_maps[neighbor].existing_sequences or []
                    
                    # Create fallback entry (copy permit-any logic from original)
                    fallback_op = self._create_rebind_fallback_operation(
                        device=device,
                        new_route_map_name=new_route_map_name,
                        original_route_map_name=original_route_map_name,
                        intent_id=policy.intent_id,
                        order=2,
                        depends_on=[route_map_op.operation_id] if route_map_op.operation_id else [],
                        existing_sequences=existing_seqs,
                    )
                    operations.append(fallback_op)
                    
                    # Create neighbor binding swap command
                    swap_op = self._create_neighbor_rebind_operation(
                        device=device,
                        neighbor=neighbor,
                        new_route_map_name=new_route_map_name,
                        original_route_map_name=original_route_map_name,
                        policy=policy,
                        intent_id=policy.intent_id,
                        order=3,
                        depends_on=[fallback_op.operation_id] if fallback_op.operation_id else [],
                    )
                    operations.append(swap_op)
                    
                    # Track rebind for metadata
                    rebind_metadata.append({
                        "device": device,
                        "neighbor": neighbor,
                        "original_route_map": original_route_map_name,
                        "new_route_map": new_route_map_name,
                        "reason": rebind_decision.reason,
                    })
                
                # Step 4: Apply route-map to neighbor if newly created (and not rebind)
                elif device_context:
                    rm_context = device_context.route_maps.get(neighbor)
                    if rm_context and rm_context.strategy == ReuseStrategy.CREATE:
                        apply_op = self._create_neighbor_apply_operation(
                            device=device,
                            neighbor=neighbor,
                            route_map_name=route_map_op.params.get("route_map_name", ""),
                            policy=policy,
                            intent_id=policy.intent_id,
                            order=2,
                            depends_on=[route_map_op.operation_id] if route_map_op.operation_id else [],
                        )
                        operations.append(apply_op)
        
        metadata: Dict[str, Any] = {
            "mechanism": policy.mechanism,
            "prefix": prefix,
        }
        if rebind_metadata:
            metadata["rebind_operations"] = rebind_metadata
        
        return PatchPlan(
            intent_id=policy.intent_id,
            operations=operations,
            prefix_isolation_enforced=prefix_isolation_enforced,
            affected_devices=policy.affected_devices,
            protocol="bgp",
            metadata=metadata,
        )
    
    def _plan_ospf_policy(
        self,
        policy: PolicyEntry,
        reuse_contexts: Dict[str, DeviceReuseContext],
        configs: Optional[Dict[str, str]] = None,
    ) -> PatchPlan:
        """
        Plan OSPF policy changes.
        
        OSPF Handling: If RolePolicy contains touched_edges, generate direct
        interface cost patches without heuristics.
        
        Fallback: When touched_edges is empty (policy layer didn't produce
        OSPF steering data), generate cost operations from affected_devices
        and their neighbor relationships.
        """
        operations: List[PatchOperation] = []
        
        # Get touched_edges from change_footprint or params
        touched_edges: List[Tuple[str, str]] = []
        cost_overrides: Dict[str, int] = {}
        
        # Check change_footprint for OSPF steering data
        if policy.change_footprint:
            touched_edges = policy.change_footprint.get("touched_edges", [])
            cost_overrides = policy.change_footprint.get("cost_overrides", {})
        
        # Also check params
        if policy.params:
            if "touched_edges" in policy.params:
                touched_edges = policy.params["touched_edges"]
            if "cost_overrides" in policy.params:
                cost_overrides = policy.params["cost_overrides"]
            # Check nested ospf_steering in params
            ospf_steering = policy.params.get("ospf_steering")
            if isinstance(ospf_steering, dict):
                if not touched_edges:
                    touched_edges = ospf_steering.get("touched_edges", [])
                if not cost_overrides:
                    cost_overrides = ospf_steering.get("cost_overrides", {})
        
        # === FALLBACK: Generate edges from affected_devices + affected_neighbors ===
        # When the policy layer doesn't provide explicit touched_edges (e.g., ospf_sources
        # not populated by normalizer), synthesize edges from topology relationships.
        if not touched_edges and policy.affected_devices:
            logger.info(
                f"OSPF fallback: No touched_edges from policy layer for {policy.intent_id}. "
                f"Generating from affected_devices={policy.affected_devices}"
            )
            ospf_cost_by_exit = {}
            if policy.params:
                ospf_cost_by_exit = policy.params.get("ospf_cost_by_exit", {}) or {}

            # Keep planner output aligned with the policy-layer OSPF solver when
            # steering is skipped due to missing sources.
            if ospf_cost_by_exit and configs:
                for device in policy.affected_devices:
                    desired_cost = ospf_cost_by_exit.get(device)
                    if desired_cost is None:
                        continue
                    interface_names = self._extract_real_interfaces(configs.get(device, ""))
                    if not interface_names:
                        interface_names = [device]
                    for interface_name in interface_names:
                        op = self._create_ospf_cost_operation(
                            device=device,
                            interface=interface_name,
                            cost=int(desired_cost),
                            edge=(device, interface_name),
                            intent_id=policy.intent_id,
                            order=len(operations),
                        )
                        operations.append(op)

                return PatchPlan(
                    intent_id=policy.intent_id,
                    operations=operations,
                    prefix_isolation_enforced=True,
                    affected_devices=policy.affected_devices,
                    protocol="ospf",
                    metadata={
                        "mechanism": policy.mechanism,
                        "touched_edges_count": 0,
                        "fallback_used": True,
                        "fallback_mode": "ospf_cost_by_exit",
                    },
                )

            affected_neighbors = policy.affected_neighbors or {}
            fallback_cost = policy.params.get("cost", 100) if policy.params else 100
            
            for device in policy.affected_devices:
                neighbors = affected_neighbors.get(device, [])
                if neighbors:
                    for neighbor in neighbors:
                        touched_edges.append([device, neighbor])
                        edge_key = f"{device}->{neighbor}"
                        if edge_key not in cost_overrides:
                            cost_overrides[edge_key] = fallback_cost
                else:
                    # No neighbor info — create a self-edge placeholder so we still get an operation
                    touched_edges.append([device, device])
                    edge_key = f"{device}->{device}"
                    if edge_key not in cost_overrides:
                        cost_overrides[edge_key] = fallback_cost
        
        # Generate interface cost operations for each touched edge
        for edge in touched_edges:
            if isinstance(edge, (list, tuple)) and len(edge) >= 2:
                src, dst = edge[0], edge[1]
                edge_key = f"{src}->{dst}"
                normalized_edge_key = "--".join(sorted([str(src), str(dst)]))
                cost = cost_overrides.get(
                    edge_key,
                    cost_overrides.get(
                        normalized_edge_key,
                        cost_overrides.get(src, 100),
                    ),
                )
                
                # Determine interface name (simplified - in real impl would use topology)
                interface_name = self._edge_to_interface(src, dst)
                
                op = self._create_ospf_cost_operation(
                    device=src,
                    interface=interface_name,
                    cost=cost,
                    edge=(src, dst),
                    intent_id=policy.intent_id,
                    order=len(operations),
                )
                operations.append(op)
        
        return PatchPlan(
            intent_id=policy.intent_id,
            operations=operations,
            prefix_isolation_enforced=True,  # OSPF is inherently prefix-isolated via routing
            affected_devices=policy.affected_devices,
            protocol="ospf",
            metadata={
                "mechanism": policy.mechanism,
                "touched_edges_count": len(touched_edges),
                "fallback_used": len(operations) > 0 and not policy.change_footprint.get("touched_edges", []) if policy.change_footprint else True,
            },
        )
    
    def _create_prefix_list_operation(
        self,
        device: str,
        prefix: str,
        context: Optional[DeviceReuseContext],
        intent_id: str,
        order: int,
        action: str = "permit",
    ) -> PatchOperation:
        """
        Create a prefix-list operation with idempotent reuse support.
        
        Implements idempotent prefix-list operations by checking if an existing
        prefix-list already matches the target prefix and action. If a match is
        found, returns a reuse operation instead of creating a duplicate.
        
        Args:
            device: Target device name
            prefix: The IP prefix to match (e.g., "192.168.0.0/24")
            context: Device reuse context containing existing prefix-lists
            intent_id: Intent identifier
            order: Execution order
            action: The action for the prefix-list entry ("permit" or "deny")
        
        Returns:
            PatchOperation with appropriate strategy (APPEND for reuse, CREATE for new)
        
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
        """
        op_id = self._generate_operation_id()
        
        # Default values for new prefix-list creation
        strategy = ReuseStrategy.CREATE
        prefix_list_name = f"PL_PATHDELTA_{prefix.replace('.', '_').replace('/', '_')}"
        sequence = 10
        reused = False
        
        # Check for existing prefix-list that matches target prefix
        # Requirements: 3.1, 3.3
        if context and prefix in context.prefix_lists:
            pl_context = context.prefix_lists[prefix]
            
            # Check if existing prefix-list matches both prefix and action
            # The prefix match is implicit (keyed by prefix in context.prefix_lists)
            # Check action match via metadata if available, or assume permit for existing
            existing_action = pl_context.metadata.get("action", "permit")
            
            if pl_context.status == ReuseStatus.EXISTS and existing_action == action:
                # Reuse existing prefix-list - Requirements: 3.2, 3.5, 3.6
                strategy = ReuseStrategy.APPEND
                prefix_list_name = pl_context.target_name
                # Preserve existing sequence number instead of generating new one
                # Use the first existing sequence if available, otherwise use next_sequence
                if pl_context.existing_sequences:
                    sequence = pl_context.existing_sequences[0]
                else:
                    sequence = pl_context.next_sequence
                reused = True
                
                logger.info(
                    f"Reusing existing prefix-list {prefix_list_name} for prefix {prefix} "
                    f"on device {device} (idempotent operation)"
                )
            else:
                # Existing context but different action or status - use context values
                strategy = pl_context.strategy
                prefix_list_name = pl_context.target_name
                sequence = pl_context.next_sequence
        
        params = {
            "prefix_list_name": prefix_list_name,
            "sequence_number": sequence,
            "action": action,
            "prefix": prefix,
            "strategy": strategy.value,
            "intent_id": intent_id,
            # CRITICAL: Prefix-list operation does NOT contain route-map definitions,
            # so prefix isolation check should be skipped for this operation
            "enforce_prefix_isolation": False,
        }
        
        # Set reused metadata flag when prefix-list is reused - Requirement: 3.6
        if reused:
            params["reused"] = True
        
        return PatchOperation(
            op=strategy,
            template="bgp/prefix_list_entry.j2",
            device=device,
            params=params,
            order=order,
            operation_id=op_id,
        )
    
    def _create_route_map_operation(
        self,
        device: str,
        neighbor: str,
        prefix: str,
        prefix_list_name: str,
        policy: PolicyEntry,
        context: Optional[DeviceReuseContext],
        intent_id: str,
        order: int,
        depends_on: List[str],
        config_text: Optional[str] = None,
    ) -> PatchOperation:
        """
        Create a route-map operation with prefix-list match.
        
        Uses insertion strategy (PREPEND/REBIND/APPEND) based on headroom analysis
        to ensure new entries take effect before existing catch-all rules.
        
        Args:
            device: Target device name
            neighbor: BGP neighbor address
            prefix: Target prefix for the policy
            prefix_list_name: Name of the prefix-list to match
            policy: The PolicyEntry being implemented
            context: Device reuse context (if available)
            intent_id: Intent identifier
            order: Execution order
            depends_on: List of operation IDs this depends on
            config_text: Optional raw config text for headroom analysis
        
        Returns:
            PatchOperation with appropriate insertion strategy
        
        Requirements: 5.4, 6.6
        """
        op_id = self._generate_operation_id()
        
        # Determine base strategy from context
        strategy = ReuseStrategy.CREATE
        route_map_name = f"RM_PATHDELTA_{neighbor.replace('.', '_')}_IN"
        sequence = 10
        insertion_strategy = InsertionStrategy.APPEND
        insertion_reason = "New route-map, using standard sequence 10"
        
        if context and neighbor in context.route_maps:
            rm_context = context.route_maps[neighbor]
            strategy = rm_context.strategy
            route_map_name = rm_context.target_name
            sequence = rm_context.next_sequence
            
            # If we have existing sequences and a context analyzer, use insertion strategy
            if rm_context.existing_sequences and self.context_analyzer and config_text:
                # Analyze headroom for this route-map
                headroom = self.context_analyzer.analyze_route_map_headroom(
                    config_text, route_map_name
                )
                
                # Extract top entry info for active shadowing prevention
                # Requirements: 2.1, 2.2
                top_entry_info = self.context_analyzer.extract_top_entry_info(
                    config_text, route_map_name
                )
                
                # Decide insertion strategy
                decision = self.decide_insertion_strategy(
                    headroom, 
                    num_entries_needed=1,
                    top_entry_info=top_entry_info,
                )
                insertion_strategy = decision.strategy
                insertion_reason = decision.reason
                
                # Apply strategy-specific logic
                if decision.strategy == InsertionStrategy.PREPEND:
                    # Use the prepend sequence number
                    sequence = decision.target_sequence
                    strategy = ReuseStrategy.APPEND  # Still appending to config, but at lower seq
                    # Log shadowing warning when prepending before existing entries
                    if headroom.min_sequence is not None:
                        self._log_shadowing_warning(
                            route_map_name=route_map_name,
                            existing_sequence=headroom.min_sequence,
                            new_sequence=sequence,
                            device=device,
                        )
                elif decision.strategy == InsertionStrategy.REBIND:
                    # Will be handled in _plan_bgp_policy - mark for rebind
                    route_map_name = decision.new_route_map_name
                    sequence = decision.target_sequence
                    strategy = ReuseStrategy.CREATE  # Creating new route-map
                # APPEND uses the existing next_sequence from context
            elif rm_context.existing_sequences:
                # Have existing sequences but no analyzer - check for catch-all risk
                # Use simple heuristic: if min_seq >= 10, prepend at min_seq - 5
                min_seq = min(rm_context.existing_sequences)
                if min_seq >= 10:
                    sequence = min_seq - self.PREPEND_OFFSET
                    insertion_strategy = InsertionStrategy.PREPEND
                    insertion_reason = f"PathDelta: prepended before seq {min_seq}"
                    # Log shadowing warning when prepending before existing entries
                    self._log_shadowing_warning(
                        route_map_name=route_map_name,
                        existing_sequence=min_seq,
                        new_sequence=sequence,
                        device=device,
                    )
                elif min_seq >= 1:
                    # Insufficient headroom - would need REBIND but no analyzer
                    # Fall back to append with warning
                    insertion_reason = f"Warning: low headroom (min_seq={min_seq}), appending may not take effect"
        
        # Guard against DELETE operations for route-maps (Requirements: 7.4, 7.5)
        self._guard_against_delete_operation(strategy, route_map_name, sequence)
        
        # Determine set attributes based on mechanism
        params: Dict[str, Any] = {
            "route_map_name": route_map_name,
            "sequence_number": sequence,
            "action": "permit",
            "prefix_list_name": prefix_list_name,  # CRITICAL: Always match prefix-list
            "strategy": strategy.value,
            "insertion_strategy": insertion_strategy.value,
            "intent_id": intent_id,
            "description": f"PathDelta intent {intent_id}",
            "insertion_comment": insertion_reason,  # Comment indicating prepend/rebind reason
        }
        
        # Add mechanism-specific set attributes
        # Support all local-pref mechanism variants
        local_pref_mechanisms = (
            "local_pref",
            "local_pref_ladder",
            "local_pref_pin",
            "local_pref_equal",
            "local_pref_degrade",
        )
        if policy.mechanism in local_pref_mechanisms:
            # Get local-pref from params or preference_tiers
            # Pass device_name for correct lookup in local_pref_by_exit map
            local_pref = self._get_local_pref_for_neighbor(policy, neighbor, device_name=device)
            if local_pref is not None:
                params["set_local_pref"] = local_pref
        
        return PatchOperation(
            op=strategy,
            template="bgp/route_map_sequence.j2",
            device=device,
            params=params,
            order=order,
            depends_on=depends_on,
            operation_id=op_id,
        )
    
    def _create_neighbor_apply_operation(
        self,
        device: str,
        neighbor: str,
        route_map_name: str,
        policy: PolicyEntry,
        intent_id: str,
        order: int,
        depends_on: List[str],
    ) -> PatchOperation:
        """Create operation to apply route-map to neighbor.
        
        Note: This operation only creates a neighbor binding statement,
        NOT a route-map definition. Therefore enforce_prefix_isolation=False
        because prefix isolation is enforced in the route-map operation.
        """
        op_id = self._generate_operation_id()
        
        return PatchOperation(
            op=ReuseStrategy.CREATE,
            template="bgp/neighbor_route_map.j2",
            device=device,
            params={
                "neighbor_address": neighbor,
                "route_map_name": route_map_name,
                "direction": "in",
                "strategy": "CREATE",
                "intent_id": intent_id,
                "local_as": policy.src_as or "",
                "asn": policy.src_as or "",
                # CRITICAL: Neighbor binding does NOT contain route-map definitions,
                # so prefix isolation check should be skipped for this operation
                "enforce_prefix_isolation": False,
            },
            order=order,
            depends_on=depends_on,
            operation_id=op_id,
        )

    def _create_maximum_paths_operation(
        self,
        device: str,
        maximum_paths: int,
        asn: Optional[int],
        intent_id: str,
        order: int,
    ) -> PatchOperation:
        """Create a deterministic BGP maximum-paths operation."""
        op_id = self._generate_operation_id()

        return PatchOperation(
            op=ReuseStrategy.MODIFY,
            template="bgp/maximum_paths.j2",
            device=device,
            params={
                "maximum_paths": int(maximum_paths),
                "asn": asn or "",
                "local_as": asn or "",
                "strategy": "MODIFY",
                "intent_id": intent_id,
            },
            order=order,
            operation_id=op_id,
        )

    def _create_rebind_fallback_operation(
        self,
        device: str,
        new_route_map_name: str,
        original_route_map_name: str,
        intent_id: str,
        order: int,
        depends_on: List[str],
        existing_sequences: Optional[List[int]] = None,
    ) -> PatchOperation:
        """
        Create fallback entry for REBIND strategy.
        
        When using REBIND, we create a new route-map and need to copy the
        fallback/permit-any logic from the original route-map to ensure
        traffic that doesn't match our new policy still flows correctly.
        
        Args:
            device: Target device name
            new_route_map_name: Name of the new route-map (with _PATHDELTA suffix)
            original_route_map_name: Name of the original route-map being replaced
            intent_id: Intent identifier
            order: Execution order
            depends_on: List of operation IDs this depends on
            existing_sequences: Optional list of existing sequence numbers from original route-map
        
        Returns:
            PatchOperation for the fallback entry
        
        Requirements: 1.5, 6.3
        """
        op_id = self._generate_operation_id()
        
        # Calculate dynamic fallback sequence instead of hardcoded 1000
        fallback_sequence = calculate_safe_fallback_seq(
            existing_sequences if existing_sequences else []
        )
        
        return PatchOperation(
            op=ReuseStrategy.CREATE,
            template="bgp/route_map_fallback.j2",
            device=device,
            params={
                "route_map_name": new_route_map_name,
                "sequence_number": fallback_sequence,
                "action": "permit",
                "strategy": "CREATE",
                "intent_id": intent_id,
                "original_route_map": original_route_map_name,
                "description": f"PathDelta: fallback from {original_route_map_name}",
                "insertion_comment": f"PathDelta: REBIND fallback - copied from {original_route_map_name}",
            },
            order=order,
            depends_on=depends_on,
            operation_id=op_id,
        )
    
    def _create_neighbor_rebind_operation(
        self,
        device: str,
        neighbor: str,
        new_route_map_name: str,
        original_route_map_name: str,
        policy: PolicyEntry,
        intent_id: str,
        order: int,
        depends_on: List[str],
    ) -> PatchOperation:
        """
        Create operation to swap neighbor route-map binding for REBIND strategy.
        
        Generates a `neighbor ... route-map {new_name} in` command to swap
        the binding from the original route-map to the new one.
        
        Note: This operation only creates a neighbor binding statement,
        NOT a route-map definition. Therefore enforce_prefix_isolation=False
        because prefix isolation is enforced in the route-map operation.
        
        Args:
            device: Target device name
            neighbor: BGP neighbor address
            new_route_map_name: Name of the new route-map (with _PATHDELTA suffix)
            original_route_map_name: Name of the original route-map being replaced
            policy: The PolicyEntry being implemented
            intent_id: Intent identifier
            order: Execution order
            depends_on: List of operation IDs this depends on
        
        Returns:
            PatchOperation for the neighbor binding swap
        
        Requirements: 6.4
        """
        op_id = self._generate_operation_id()
        
        return PatchOperation(
            op=ReuseStrategy.MODIFY,
            template="bgp/neighbor_route_map.j2",
            device=device,
            params={
                "neighbor_address": neighbor,
                "route_map_name": new_route_map_name,
                "direction": "in",
                "strategy": "MODIFY",
                "intent_id": intent_id,
                "local_as": policy.src_as or "",
                "asn": policy.src_as or "",
                "original_route_map": original_route_map_name,
                "is_rebind": True,
                "insertion_comment": f"PathDelta: REBIND - swapping from {original_route_map_name} to {new_route_map_name}",
                # CRITICAL: Neighbor binding does NOT contain route-map definitions,
                # so prefix isolation check should be skipped for this operation
                "enforce_prefix_isolation": False,
            },
            order=order,
            depends_on=depends_on,
            operation_id=op_id,
        )
    
    def _create_ospf_cost_operation(
        self,
        device: str,
        interface: str,
        cost: int,
        edge: Tuple[str, str],
        intent_id: str,
        order: int,
    ) -> PatchOperation:
        """Create an OSPF interface cost operation."""
        op_id = self._generate_operation_id()
        
        return PatchOperation(
            op=ReuseStrategy.MODIFY,
            template="ospf/interface_cost.j2",
            device=device,
            params={
                "interface_name": interface,
                "interface": interface,
                "cost": cost,
                "strategy": "MODIFY",
                "intent_id": intent_id,
                "edge": edge,
            },
            order=order,
            operation_id=op_id,
        )
    
    def _get_local_pref_for_device(
        self,
        device_name: str,
        local_pref_by_exit: Dict[str, int],
        baseline_lp: int = 100,
    ) -> int:
        """Extract local-pref value for a device from local_pref_by_exit map.
        
        The local_pref_by_exit map is keyed by exit router names (e.g., "exit_a"),
        NOT by neighbor IPs. This function looks up by device_name (the router
        being configured).
        
        Lookup order:
        1. local_pref_by_exit[device_name] - PRIMARY LOOKUP
        2. baseline_lp (default: 100) - FALLBACK
        
        REMOVED: next(iter(lp_map.values())) fallback which was non-deterministic
        and could cause primary/backup exits to receive the same local-pref.
        
        Args:
            device_name: The router being configured (should match exit name in map)
            local_pref_by_exit: Map of exit_name -> local_pref value
            baseline_lp: Fallback local-pref value (default: 100)
            
        Returns:
            The local-pref value to use for this device
        """
        # Primary lookup: device_name in local_pref_by_exit
        if device_name in local_pref_by_exit:
            return local_pref_by_exit[device_name]
        
        # Fallback: use baseline_lp (NOT first value from map)
        return baseline_lp

    def _get_local_pref_for_neighbor(
        self, policy: PolicyEntry, neighbor: str, device_name: str
    ) -> Optional[int]:
        """Extract local-pref value for a neighbor from policy params.
        
        Lookup order:
        1. Direct local_pref in params (simple case)
        2. local_pref_by_exit[device_name] - PRIMARY LOOKUP (device being configured)
        3. baseline_lp from sketch (default: 100) - FALLBACK
        4. preference_tiers heuristic (legacy)
        
        REMOVED: next(iter(lp_map.values())) fallback which was non-deterministic.
        
        Args:
            policy: The PolicyEntry containing params
            neighbor: BGP neighbor IP address (used for legacy preference_tiers)
            device_name: The router being configured (used for local_pref_by_exit lookup)
            
        Returns:
            The local-pref value, or None if not applicable
        """
        # First, check for direct local_pref in params (from mechanism)
        if policy.params and "local_pref" in policy.params:
            return policy.params["local_pref"]
        
        # Check params.local_pref_by_exit - lookup by device_name (NOT neighbor)
        if policy.params and "local_pref_by_exit" in policy.params:
            lp_map = policy.params["local_pref_by_exit"]
            if lp_map:
                # Get baseline_lp from params or use default
                baseline_lp = policy.params.get("baseline_lp", 100)
                return self._get_local_pref_for_device(device_name, lp_map, baseline_lp)
        
        # Check preference_tiers and convert to local-pref
        if policy.preference_tiers:
            # Find which exit this neighbor corresponds to
            # For now, use a default mapping
            for exit_name, tier in policy.preference_tiers.items():
                # Simple heuristic: if neighbor IP contains exit name pattern
                if exit_name.lower() in neighbor.lower():
                    # Convert tier to local-pref (higher tier = higher local-pref)
                    return 100 + (tier * 10)
        
        return None
    
    def _edge_to_interface(self, src: str, dst: str) -> str:
        """
        Convert edge (src, dst) to interface name.
        
        In a real implementation, this would use topology data.
        For now, generate a placeholder name.
        """
        return f"eth-{dst}"

    def _extract_real_interfaces(self, config_text: str) -> List[str]:
        """Extract non-loopback interface names from baseline FRR config."""
        if not config_text:
            return []
        interfaces: List[str] = []
        for match in re.finditer(r"^interface\s+(\S+)", config_text, re.MULTILINE):
            interface_name = match.group(1)
            if interface_name.lower() == "lo":
                continue
            interfaces.append(interface_name)
        return interfaces
    
    def _generate_operation_id(self) -> str:
        """Generate a unique operation ID."""
        self._operation_counter += 1
        return f"op_{self._operation_counter}_{uuid.uuid4().hex[:8]}"


def create_patch_plan(
    role_policy: RolePolicy,
    reuse_contexts: Dict[str, DeviceReuseContext],
    context_analyzer: Optional["ContextAnalyzer"] = None,
    naive_mode: bool = False,
) -> List[PatchPlan]:
    """
    Convenience function to create patch plans.
    
    Args:
        role_policy: The RolePolicy to plan
        reuse_contexts: Reuse context for each device
        context_analyzer: Optional ContextAnalyzer for style-aware neural synthesis
        naive_mode: If True, force PREPEND strategy and disable catch-all/deny
                   detection. Used for ShadowSafe ablation experiments (E3).
    
    Returns:
        List of PatchPlan objects
    
    **Validates: Requirements 3.4 (ShadowSafe naive mode)**
    """
    planner = PatchPlanner(context_analyzer=context_analyzer, naive_mode=naive_mode)
    return planner.plan(role_policy, reuse_contexts)
