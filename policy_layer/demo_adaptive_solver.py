"""Demo script for AdaptiveParamSolver."""

from adaptive_param_solver import AdaptiveParamSolver, allocate_in_interval

# 关键测试场景：在 35 和 40 之间插入值，步长为 5
solver = AdaptiveParamSolver()
existing = [10, 15, 30, 35, 40, 48, 57]

# 1. 测试步长推断（应该用众数而非 GCD）
step_result = solver._infer_robust_step(existing)
print("=== Step Inference Test ===")
print(f"Input values: {existing}")
print(f"Detected step: {step_result.detected_step}")
print(f"Confidence: {step_result.confidence:.2%}")
print(f"Is fallback: {step_result.is_fallback}")
print()

# 2. 测试窄缝插值（35 和 40 之间）
print("=== Narrow Gap Insertion Test ===")
result = solver.allocate_value(
    lower_bound=35,
    upper_bound=40,
    existing_values=set(existing),
    step=5,
    higher_is_better=True,
)
print("Insert between 35 and 40:")
print(f"  Allocated value: {result.value}")
print(f"  Strategy level: Level {result.level_used}")
print(f"  Strategy name: {result.strategy_name}")
print()

# 3. 便捷函数测试
print("=== Convenience Function Test ===")
value = allocate_in_interval(35, 40, {35, 40}, step=5)
print(f"allocate_in_interval(35, 40, {{35, 40}}, step=5) = {value}")
print()

# 4. 完整 BGP local-pref 求解测试
print("=== BGP Local-Pref Solver Test ===")
tiers = {"exit_prefer": 1, "exit_base": 0, "exit_avoid": -1}
lp_result = solver.solve_bgp_local_pref(tiers, existing)
print(f"Tiers: {tiers}")
print(f"Result: {lp_result}")
prefer = lp_result["exit_prefer"]
base = lp_result["exit_base"]
avoid = lp_result["exit_avoid"]
print(f"Verify ordering: prefer({prefer}) > base({base}) > avoid({avoid})")
print(f"Ordering valid: {prefer > base > avoid}")
