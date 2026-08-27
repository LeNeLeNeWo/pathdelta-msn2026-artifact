"""
Unit tests for AdaptiveParamSolver.

Tests the robust step inference and interval-aware allocation strategies.
"""

import pytest
from .adaptive_param_solver import (
    AdaptiveParamSolver,
    allocate_in_interval,
    StepInferenceResult,
    AllocationResult,
)


class TestRobustStepInference:
    """Tests for _infer_robust_step method."""
    
    def test_clean_step_pattern(self):
        """Test with clean, regular step pattern."""
        solver = AdaptiveParamSolver()
        values = [10, 20, 30, 40, 50]  # Step = 10
        
        result = solver._infer_robust_step(values)
        
        assert result.detected_step == 10
        assert result.confidence == 1.0  # All differences are 10
        assert not result.is_fallback
    
    def test_noisy_step_pattern(self):
        """Test with noisy data - the key improvement over GCD."""
        solver = AdaptiveParamSolver()
        # Step=5 is dominant, but 48 and 57 are noise
        values = [10, 15, 30, 35, 40, 48, 57]
        
        result = solver._infer_robust_step(values)
        
        # Differences: [5, 15, 5, 5, 8, 9]
        # Frequency: {5: 3, 15: 1, 8: 1, 9: 1}
        # Mode = 5 (appears 3 times out of 6 = 50% confidence)
        assert result.detected_step == 5
        assert result.confidence == 0.5  # 3/6
        assert not result.is_fallback
        assert result.mode_frequency == 3
        assert result.total_differences == 6
    
    def test_chaotic_pattern_fallback(self):
        """Test fallback when no clear pattern exists."""
        solver = AdaptiveParamSolver(min_confidence=0.4)
        # All different differences - no dominant mode
        values = [10, 17, 29, 44, 62]
        
        result = solver._infer_robust_step(values)
        
        # Differences: [7, 12, 15, 18] - all unique
        # Each appears once, confidence = 0.25 < 0.4
        assert result.is_fallback
        assert result.detected_step == 10  # Default
    
    def test_single_value(self):
        """Test with single value - should fallback."""
        solver = AdaptiveParamSolver()
        values = [100]
        
        result = solver._infer_robust_step(values)
        
        assert result.is_fallback
        assert result.detected_step == 10
    
    def test_empty_values(self):
        """Test with empty list - should fallback."""
        solver = AdaptiveParamSolver()
        values = []
        
        result = solver._infer_robust_step(values)
        
        assert result.is_fallback
        assert result.detected_step == 10
    
    def test_step_clamping_min(self):
        """Test that step is clamped to min_step."""
        solver = AdaptiveParamSolver(min_step=5)
        values = [10, 12, 14, 16]  # Step = 2, below min_step
        
        result = solver._infer_robust_step(values)
        
        # Mode = 2, but 2 < min_step=5, so fallback
        assert result.is_fallback
        assert result.detected_step == 10  # Default
    
    def test_step_clamping_max(self):
        """Test that step is clamped to max_step."""
        solver = AdaptiveParamSolver(max_step=100)
        values = [100, 250, 400, 550]  # Step = 150, above max_step
        
        result = solver._infer_robust_step(values)
        
        assert result.detected_step == 100  # Clamped to max
        assert not result.is_fallback


class TestIntervalAwareAllocation:
    """Tests for allocate_value method with three-level fallback."""
    
    def test_level1_grid_allocation(self):
        """Test Level 1: Grid-aligned allocation."""
        solver = AdaptiveParamSolver()
        
        result = solver.allocate_value(
            lower_bound=30,
            upper_bound=50,
            existing_values={30, 50},
            step=10,
            higher_is_better=True,
        )
        
        assert result.value == 40  # 30 + 10
        assert result.level_used == 1
        assert "Grid" in result.strategy_name
    
    def test_level2_microstep_allocation(self):
        """Test Level 2: Micro-step when grid point is occupied."""
        solver = AdaptiveParamSolver()
        
        result = solver.allocate_value(
            lower_bound=30,
            upper_bound=50,
            existing_values={30, 40, 50},  # Grid point 40 is occupied
            step=10,
            higher_is_better=True,
        )
        
        assert result.value == 35  # 30 + (10 // 2)
        assert result.level_used == 2
        assert "Micro-step" in result.strategy_name
    
    def test_level3_bisection_allocation(self):
        """Test Level 3: Bisection for narrow gaps."""
        solver = AdaptiveParamSolver()
        
        result = solver.allocate_value(
            lower_bound=35,
            upper_bound=40,
            existing_values={35, 40},  # Very narrow gap
            step=5,
            higher_is_better=True,
        )
        
        # 35 + 5 = 40 (occupied), 35 + 2 = 37 (valid)
        # or bisection: (35 + 40) // 2 = 37
        assert result.value == 37
        assert result.level_used in [2, 3]
    
    def test_narrow_gap_insertion_key_scenario(self):
        """
        KEY TEST: Insert between 35 and 40 with step=5.
        
        This is the exact scenario from the requirements:
        - Existing values: [10, 15, 30, 35, 40, 48, 57]
        - Need to insert between 35 (Primary) and 40 (Existing Top)
        - Should output 37 or 38, NOT throw exception or jump to 45
        """
        solver = AdaptiveParamSolver()
        existing = {10, 15, 30, 35, 40, 48, 57}
        
        result = solver.allocate_value(
            lower_bound=35,
            upper_bound=40,
            existing_values=existing,
            step=5,
            higher_is_better=True,
        )
        
        # Must be strictly between 35 and 40
        assert 35 < result.value < 40
        # Should be 37 or 38 (bisection or micro-step)
        assert result.value in [36, 37, 38, 39]
        # Should NOT throw exception
        # Should NOT return 45 (outside upper bound)
    
    def test_no_upper_bound(self):
        """Test allocation without upper bound."""
        solver = AdaptiveParamSolver()
        
        result = solver.allocate_value(
            lower_bound=100,
            upper_bound=None,
            existing_values={100, 110},  # 110 is occupied
            step=10,
            higher_is_better=True,
        )
        
        # Level 1 (110) is occupied, Level 2 (105) is free
        # Should return 105 (micro-step) or 120 (next grid)
        assert result.value > 100
        assert result.value not in {100, 110}
    
    def test_no_space_raises_error(self):
        """Test that ValueError is raised when no space available."""
        solver = AdaptiveParamSolver()
        
        # Completely filled interval (35, 36, 37 with bounds 35 and 37)
        # Only 36 is in the valid range (35, 37), and it's occupied
        with pytest.raises(ValueError):
            solver.allocate_value(
                lower_bound=35,
                upper_bound=37,
                existing_values={36},  # The only valid slot is occupied
                step=5,
                higher_is_better=True,
            )


class TestBGPLocalPrefSolver:
    """Tests for solve_bgp_local_pref method."""
    
    def test_basic_tier_allocation(self):
        """Test basic tier allocation with clean data."""
        solver = AdaptiveParamSolver()
        tiers = {"exit_hi": 2, "exit_mid": 1, "exit_base": 0}
        existing = [100, 100, 100]
        
        result = solver.solve_bgp_local_pref(tiers, existing)
        
        # Verify tier ordering: hi > mid > base
        assert result["exit_hi"] > result["exit_mid"]
        assert result["exit_mid"] > result["exit_base"]
        assert result["exit_base"] == 100  # Baseline
    
    def test_negative_tier_allocation(self):
        """Test negative tier (demotion) allocation."""
        solver = AdaptiveParamSolver()
        tiers = {"exit_prefer": 1, "exit_base": 0, "exit_avoid": -1}
        existing = [100]
        
        result = solver.solve_bgp_local_pref(tiers, existing)
        
        # Verify ordering: prefer > base > avoid
        assert result["exit_prefer"] > result["exit_base"]
        assert result["exit_base"] > result["exit_avoid"]
        assert result["exit_avoid"] >= 10  # Minimum valid
        assert result["exit_base"] == 100  # Baseline
    
    def test_ecmp_same_tier(self):
        """Test ECMP: same tier should get same value."""
        solver = AdaptiveParamSolver()
        tiers = {"exit_a": 1, "exit_b": 1, "exit_c": 0}
        existing = [100]
        
        result = solver.solve_bgp_local_pref(tiers, existing)
        
        # Same tier = same value (ECMP)
        assert result["exit_a"] == result["exit_b"]
        assert result["exit_a"] > result["exit_c"]
    
    def test_noisy_existing_values(self):
        """Test with noisy existing values (brownfield scenario)."""
        solver = AdaptiveParamSolver()
        tiers = {"exit_new": 1, "exit_base": 0}
        # Noisy data with step=5 dominant but outliers
        existing = [10, 15, 30, 35, 40, 48, 57]
        
        result = solver.solve_bgp_local_pref(tiers, existing)
        
        # Should still produce valid, ordered results
        assert result["exit_new"] > result["exit_base"]


class TestOSPFCostSolver:
    """Tests for solve_ospf_cost method."""
    
    def test_basic_cost_allocation(self):
        """Test basic OSPF cost allocation."""
        solver = AdaptiveParamSolver()
        tiers = {"exit_prefer": 2, "exit_mid": 1, "exit_base": 0}
        existing = [20]  # Use 20 as baseline so there's room below
        
        result = solver.solve_ospf_cost(tiers, existing)
        
        # Higher tier = lower cost (more preferred)
        assert result["exit_prefer"] < result["exit_mid"]
        assert result["exit_mid"] < result["exit_base"]
        assert result["exit_base"] == 20  # Baseline
    
    def test_cost_minimum_bound(self):
        """Test that cost doesn't go below 1."""
        solver = AdaptiveParamSolver()
        tiers = {"exit_hi": 5, "exit_base": 0}
        existing = [10]  # Low baseline
        
        result = solver.solve_ospf_cost(tiers, existing)
        
        assert result["exit_hi"] >= 1  # Minimum valid OSPF cost


class TestConvenienceFunction:
    """Tests for allocate_in_interval convenience function."""
    
    def test_narrow_gap_convenience(self):
        """Test the convenience function for narrow gap allocation."""
        result = allocate_in_interval(
            lower=35,
            upper=40,
            existing={35, 40},
            step=5,
        )
        
        assert result is not None
        assert 35 < result < 40
        assert result in [36, 37, 38, 39]
    
    def test_no_space_returns_none(self):
        """Test that None is returned when no space available."""
        # Interval (35, 37) only has slot 36, which is occupied
        result = allocate_in_interval(
            lower=35,
            upper=37,
            existing={36},
            step=5,
        )
        
        assert result is None


class TestIntegrationScenario:
    """Integration tests for complete brownfield scenarios."""
    
    def test_full_brownfield_scenario(self):
        """
        Complete brownfield scenario test.
        
        Simulates a real network with:
        - Existing noisy local-pref values
        - Need to insert new tiers without disrupting existing config
        """
        solver = AdaptiveParamSolver()
        
        # Existing brownfield configuration
        existing_lprefs = [100, 105, 110]  # Clean baseline around 100
        
        # New intent: prefer exit_a, demote exit_b, keep exit_c as baseline
        tiers = {
            "exit_a": 2,
            "exit_b": -1,
            "exit_c": 0,
        }
        
        result = solver.solve_bgp_local_pref(tiers, existing_lprefs)
        
        # Verify ordering: a > c > b
        assert result["exit_a"] > result["exit_c"]
        assert result["exit_c"] > result["exit_b"]
        
        # Verify values are reasonable
        assert all(v >= 10 for v in result.values())
        
        print(f"Brownfield scenario result: {result}")
        print(f"Step inference: {solver._infer_robust_step(existing_lprefs)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
