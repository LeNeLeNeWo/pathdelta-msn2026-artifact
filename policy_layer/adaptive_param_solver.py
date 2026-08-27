"""
Adaptive Parameter Solver for Brownfield Networks

This module provides robust parameter allocation for BGP local-pref and OSPF cost
values in brownfield (existing) network environments. It addresses the limitations
of GCD-based step inference and simple collision avoidance.

Key Improvements over legacy param_solver:
1. Robust Step Inference: Uses frequency-based mode detection instead of GCD
2. Interval-Aware Fallback: Three-level allocation strategy for tight spaces
3. Strict Monotonicity: Guarantees tier ordering invariants

Author: PathDelta Team
"""

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from statistics import median


@dataclass
class StepInferenceResult:
    """Result of robust step inference."""
    detected_step: int
    confidence: float  # 0.0 to 1.0, based on mode frequency
    is_fallback: bool  # True if using default step
    mode_frequency: int  # How many times the mode appeared
    total_differences: int  # Total number of differences analyzed


@dataclass
class AllocationResult:
    """Result of value allocation."""
    value: int
    level_used: int  # 1=Grid, 2=Micro-step, 3=Bisection
    strategy_name: str  # Human-readable strategy name


class AdaptiveParamSolver:
    """
    Adaptive parameter solver for brownfield network configurations.
    
    Handles noisy, hand-configured parameter values by using frequency-based
    step detection and interval-aware fallback allocation.
    
    Attributes:
        default_step: Default step when inference fails (default: 10)
        min_step: Minimum allowed step (default: 5)
        max_step: Maximum allowed step (default: 100)
        min_confidence: Minimum confidence threshold for mode (default: 0.3)
    """
    
    def __init__(
        self,
        default_step: int = 10,
        min_step: int = 5,
        max_step: int = 100,
        min_confidence: float = 0.3,
    ):
        self.default_step = default_step
        self.min_step = min_step
        self.max_step = max_step
        self.min_confidence = min_confidence
    
    def _infer_robust_step(self, values: List[int]) -> StepInferenceResult:
        """
        Infer step size using frequency-based mode detection.
        
        Unlike GCD which is sensitive to outliers, this method:
        1. Computes all adjacent differences
        2. Builds a frequency distribution
        3. Selects the mode (most common difference) as the step
        4. Falls back to default if mode confidence is too low
        
        Args:
            values: List of existing parameter values
        
        Returns:
            StepInferenceResult with detected step and confidence metrics
        
        Example:
            values = [10, 15, 30, 35, 40, 48, 57]
            differences = [5, 15, 5, 5, 8, 9]
            frequency = {5: 3, 15: 1, 8: 1, 9: 1}
            mode = 5 (appears 3 times)
            confidence = 3/6 = 0.5
        """
        # Sort and deduplicate
        sorted_values = sorted(set(values))
        
        # Edge case: fewer than 2 distinct values
        if len(sorted_values) < 2:
            return StepInferenceResult(
                detected_step=self.default_step,
                confidence=0.0,
                is_fallback=True,
                mode_frequency=0,
                total_differences=0,
            )
        
        # Calculate all adjacent differences
        differences = [
            sorted_values[i + 1] - sorted_values[i]
            for i in range(len(sorted_values) - 1)
        ]
        
        # Filter out zero or negative differences (shouldn't happen, but be safe)
        differences = [d for d in differences if d > 0]
        
        if not differences:
            return StepInferenceResult(
                detected_step=self.default_step,
                confidence=0.0,
                is_fallback=True,
                mode_frequency=0,
                total_differences=0,
            )
        
        # Build frequency distribution
        freq = Counter(differences)
        total_diffs = len(differences)
        
        # Find mode (most common difference)
        most_common = freq.most_common()
        mode_value, mode_count = most_common[0]
        
        # Calculate confidence as frequency ratio
        confidence = mode_count / total_diffs
        
        # Check if mode is reliable enough
        if confidence < self.min_confidence:
            # Mode not dominant enough, fall back to default
            return StepInferenceResult(
                detected_step=self.default_step,
                confidence=confidence,
                is_fallback=True,
                mode_frequency=mode_count,
                total_differences=total_diffs,
            )
        
        # Clamp mode to [min_step, max_step]
        if mode_value < self.min_step:
            detected_step = self.default_step
            is_fallback = True
        elif mode_value > self.max_step:
            detected_step = self.max_step
            is_fallback = False
        else:
            detected_step = mode_value
            is_fallback = False
        
        return StepInferenceResult(
            detected_step=detected_step,
            confidence=confidence,
            is_fallback=is_fallback,
            mode_frequency=mode_count,
            total_differences=total_diffs,
        )
    
    def allocate_value(
        self,
        lower_bound: int,
        upper_bound: Optional[int],
        existing_values: Set[int],
        step: int,
        higher_is_better: bool = True,
    ) -> AllocationResult:
        """
        Allocate a new value using three-level fallback strategy.
        
        This method implements interval-aware allocation that handles
        tight spaces (narrow gaps) gracefully:
        
        Level 1 (Grid): Try lower + step
            - Maintains grid alignment
            - Fails if collision or exceeds upper_bound
        
        Level 2 (Micro-step): Try lower + (step // 2)
            - Half-step for narrow gaps
            - Useful when grid points are occupied
        
        Level 3 (Bisection): Try (lower + upper) // 2
            - Geometric center as last resort
            - Guarantees a value if any space exists
        
        Args:
            lower_bound: Value of the lower tier (must be > this)
            upper_bound: Value of the upper tier (must be < this), or None if no upper
            existing_values: Set of values already in use
            step: Detected step size
            higher_is_better: True for BGP local-pref, False for OSPF cost
        
        Returns:
            AllocationResult with allocated value and strategy used
        
        Raises:
            ValueError: If no valid value can be allocated (no space)
        """
        # For higher_is_better=True (BGP): we want lower < new < upper
        # For higher_is_better=False (OSPF): we want upper < new < lower
        # Normalize so we always work with lower < new < upper
        if not higher_is_better and upper_bound is not None:
            lower_bound, upper_bound = upper_bound, lower_bound
        
        # Level 1: Grid-aligned allocation
        candidate = lower_bound + step
        if self._is_valid(candidate, lower_bound, upper_bound, existing_values):
            return AllocationResult(
                value=candidate,
                level_used=1,
                strategy_name="Grid (lower + step)",
            )
        
        # Level 2: Micro-step (half step)
        micro_step = max(1, step // 2)
        candidate = lower_bound + micro_step
        if self._is_valid(candidate, lower_bound, upper_bound, existing_values):
            return AllocationResult(
                value=candidate,
                level_used=2,
                strategy_name="Micro-step (lower + step//2)",
            )
        
        # Level 3: Bisection (geometric center)
        if upper_bound is not None:
            candidate = (lower_bound + upper_bound) // 2
            if self._is_valid(candidate, lower_bound, upper_bound, existing_values):
                return AllocationResult(
                    value=candidate,
                    level_used=3,
                    strategy_name="Bisection ((lower + upper) // 2)",
                )
            
            # Try to find any available slot in the range
            for offset in range(1, (upper_bound - lower_bound)):
                candidate = lower_bound + offset
                if self._is_valid(candidate, lower_bound, upper_bound, existing_values):
                    return AllocationResult(
                        value=candidate,
                        level_used=3,
                        strategy_name=f"Scan (lower + {offset})",
                    )
            
            # No space available in the interval
            raise ValueError(
                f"Cannot allocate value in interval ({lower_bound}, {upper_bound}): "
                f"all slots occupied. existing={sorted(existing_values)}"
            )
        
        # No upper bound - keep trying grid points
        for multiplier in range(2, 101):
            candidate = lower_bound + multiplier * step
            if candidate not in existing_values:
                return AllocationResult(
                    value=candidate,
                    level_used=1,
                    strategy_name=f"Grid (lower + {multiplier}*step)",
                )
        
        raise ValueError(
            f"Cannot allocate value: lower={lower_bound}, upper={upper_bound}, "
            f"step={step}, existing={sorted(existing_values)}"
        )
    
    def _is_valid(
        self,
        candidate: int,
        lower_bound: int,
        upper_bound: Optional[int],
        existing_values: Set[int],
    ) -> bool:
        """Check if candidate is valid (in range and not colliding)."""
        # Must be strictly greater than lower bound
        if candidate <= lower_bound:
            return False
        
        # Must be strictly less than upper bound (if exists)
        if upper_bound is not None and candidate >= upper_bound:
            return False
        
        # Must not collide with existing values
        if candidate in existing_values:
            return False
        
        return True
    
    def solve_bgp_local_pref(
        self,
        tiers: Dict[str, int],
        existing_lprefs: List[int],
        band_width: int = 1000,
    ) -> Dict[str, int]:
        """
        Solve BGP local-pref values for given tiers.
        
        Uses robust step inference and interval-aware allocation to handle
        brownfield networks with noisy configurations.
        
        Args:
            tiers: Dict mapping exit_name -> tier (higher tier = more preferred)
            existing_lprefs: List of existing local-pref values in the network
            band_width: Reserved band width for new allocations
        
        Returns:
            Dict mapping exit_name -> local_pref value
        
        Invariant: tier(A) > tier(B) => local_pref(A) > local_pref(B)
        """
        if not tiers:
            return {}
        
        # Infer step from existing values
        step_result = self._infer_robust_step(existing_lprefs)
        step = step_result.detected_step
        
        # Calculate baseline (mode or median of existing values)
        baseline = self._calculate_baseline(existing_lprefs)
        
        # Build existing values set
        existing_set = set(existing_lprefs)
        
        # Group exits by tier value
        tier_groups: Dict[int, List[str]] = {}
        for exit_name, tier_val in tiers.items():
            if tier_val not in tier_groups:
                tier_groups[tier_val] = []
            tier_groups[tier_val].append(exit_name)
        
        # Allocate values tier by tier
        lp_map: Dict[str, int] = {}
        
        # Process tiers in order: negative (lowest) -> 0 -> positive (highest)
        sorted_tiers = sorted(tier_groups.keys())
        
        # First pass: assign baseline to tier 0
        if 0 in tier_groups:
            for exit_name in tier_groups[0]:
                lp_map[exit_name] = baseline
            existing_set.add(baseline)
        
        # Second pass: allocate positive tiers (ascending order, higher value)
        pos_tiers = [t for t in sorted_tiers if t > 0]
        prev_value = baseline
        for tier_val in pos_tiers:
            exits = tier_groups[tier_val]
            
            result = self.allocate_value(
                lower_bound=prev_value,
                upper_bound=None,
                existing_values=existing_set,
                step=step,
                higher_is_better=True,
            )
            value = result.value
            
            for exit_name in exits:
                lp_map[exit_name] = value
            
            existing_set.add(value)
            prev_value = value
        
        # Third pass: allocate negative tiers (descending order, lower value)
        neg_tiers = sorted([t for t in sorted_tiers if t < 0], reverse=True)  # -1, -2, -3...
        prev_value = baseline
        for tier_val in neg_tiers:
            exits = tier_groups[tier_val]
            
            # For negative tiers, we want values BELOW the baseline
            # Use step to go down
            candidate = prev_value - step
            
            # Ensure minimum value of 10
            if candidate < 10:
                candidate = 10
            
            # Avoid collision
            attempts = 0
            while candidate in existing_set and attempts < 100:
                candidate -= max(1, step // 2)
                if candidate < 10:
                    candidate = 10
                    break
                attempts += 1
            
            value = candidate
            
            for exit_name in exits:
                lp_map[exit_name] = value
            
            existing_set.add(value)
            prev_value = value
        
        return lp_map
    
    def solve_ospf_cost(
        self,
        tiers: Dict[str, int],
        existing_costs: List[int],
        cost_step: int = 10,
    ) -> Dict[str, int]:
        """
        Solve OSPF cost values for given tiers.
        
        Uses robust step inference and interval-aware allocation.
        
        Args:
            tiers: Dict mapping exit_name -> tier (higher tier = lower cost = more preferred)
            existing_costs: List of existing OSPF cost values
            cost_step: Default cost step
        
        Returns:
            Dict mapping exit_name -> cost value
        
        Invariant: tier(A) > tier(B) => cost(A) < cost(B)
        """
        if not tiers:
            return {}
        
        # Infer step from existing values
        step_result = self._infer_robust_step(existing_costs)
        step = step_result.detected_step if not step_result.is_fallback else cost_step
        
        # Calculate baseline
        baseline = self._calculate_baseline(existing_costs)
        
        # Build existing values set
        existing_set = set(existing_costs)
        
        # Group exits by tier value
        tier_groups: Dict[int, List[str]] = {}
        for exit_name, tier_val in tiers.items():
            if tier_val not in tier_groups:
                tier_groups[tier_val] = []
            tier_groups[tier_val].append(exit_name)
        
        cost_map: Dict[str, int] = {}
        sorted_tiers = sorted(tier_groups.keys())
        
        # First pass: assign baseline to tier 0
        if 0 in tier_groups:
            for exit_name in tier_groups[0]:
                cost_map[exit_name] = baseline
            existing_set.add(baseline)
        
        # Second pass: allocate positive tiers (higher tier = lower cost)
        # Process in ascending order so we allocate from baseline downward
        pos_tiers = sorted([t for t in sorted_tiers if t > 0])
        prev_value = baseline
        for tier_val in pos_tiers:
            exits = tier_groups[tier_val]
            
            # For positive tiers, we want values BELOW the baseline (lower cost = better)
            candidate = prev_value - step
            
            # Ensure minimum value of 1
            if candidate < 1:
                candidate = 1
            
            # Avoid collision
            attempts = 0
            while candidate in existing_set and attempts < 100:
                candidate -= max(1, step // 2)
                if candidate < 1:
                    candidate = 1
                    break
                attempts += 1
            
            value = candidate
            
            for exit_name in exits:
                cost_map[exit_name] = value
            
            existing_set.add(value)
            prev_value = value
        
        # Third pass: allocate negative tiers (lower tier = higher cost)
        neg_tiers = sorted([t for t in sorted_tiers if t < 0])  # -3, -2, -1...
        prev_value = baseline
        for tier_val in neg_tiers:
            exits = tier_groups[tier_val]
            
            # For negative tiers, we want values ABOVE the baseline (higher cost = worse)
            candidate = prev_value + step
            
            # Avoid collision
            attempts = 0
            while candidate in existing_set and attempts < 100:
                candidate += max(1, step // 2)
                attempts += 1
            
            value = candidate
            
            for exit_name in exits:
                cost_map[exit_name] = value
            
            existing_set.add(value)
            prev_value = value
        
        return cost_map
    
    def _calculate_baseline(self, values: List[int]) -> int:
        """Calculate baseline value using mode or median."""
        if not values:
            return 100  # Default baseline
        
        freq = Counter(values)
        most_common = freq.most_common()
        
        # If there's a clear mode (appears more than once), use it
        if most_common[0][1] > 1:
            return most_common[0][0]
        
        # Otherwise use median
        return int(median(values))


def allocate_in_interval(
    lower: int,
    upper: int,
    existing: Set[int],
    step: int,
) -> Optional[int]:
    """
    Convenience function to allocate a value in a specific interval.
    
    This is the core function for the "narrow gap" problem.
    
    Args:
        lower: Lower bound (exclusive)
        upper: Upper bound (exclusive)
        existing: Set of existing values to avoid
        step: Preferred step size
    
    Returns:
        Allocated value, or None if no space available
    
    Example:
        >>> allocate_in_interval(35, 40, {35, 40}, step=5)
        37  # or 38, using bisection
    """
    solver = AdaptiveParamSolver()
    try:
        result = solver.allocate_value(
            lower_bound=lower,
            upper_bound=upper,
            existing_values=existing,
            step=step,
            higher_is_better=True,
        )
        return result.value
    except ValueError:
        return None
