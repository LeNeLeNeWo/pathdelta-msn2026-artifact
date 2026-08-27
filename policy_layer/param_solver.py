"""
参数求解骨架：在现网数值分布上，结合偏好层级生成具体可下发的数值。
偏好层级（tiers）的语义约定：
- tier > 0：提升优先级（BGP local-pref 更大 / OSPF cost 更小）
- tier == 0：保持基线（尽量不改动）
- tier < 0：显式降权/规避（BGP local-pref 更小 / OSPF cost 更大）

Style-Aware Parameter Quantization:
When a detected_step is provided (from ContextAnalyzer), generated values
align to the brownfield network's existing step/grid pattern:
- BGP local-pref: baseline + (k * detected_step)
- OSPF cost: baseline - (tier * detected_step)
"""

from collections import Counter
from typing import Dict, Any, List, Optional, Set

from .preference_tiers import PreferenceTiers


# Maximum collision avoidance attempts before raising an error
MAX_COLLISION_ATTEMPTS = 100


def assert_tier_invariants(
    tiers: PreferenceTiers,
    metric_map: Dict[str, int],
    higher_is_better: bool,
) -> None:
    """
    Formal verification of tier-to-metric mapping.

    This function verifies that the mapping from preference tiers to protocol-specific
    metrics preserves the ordering semantics:

    Invariants:
    - tier(A) > tier(B) => metric(A) is_better_than metric(B)
    - tier(A) == tier(B) => metric(A) == metric(B)

    For BGP (higher_is_better=True):
      - Higher tier => higher local-pref value
    For OSPF (higher_is_better=False):
      - Higher tier => lower cost value

    Args:
        tiers: PreferenceTiers mapping exit_name -> tier (integer)
        metric_map: Dict mapping exit_name -> metric value (e.g., local_pref or cost)
        higher_is_better: True for BGP local-pref (higher is better),
                          False for OSPF cost (lower is better)

    Raises:
        ValueError: If any invariant constraint is violated, with diagnostic information
                    including the exit names, tier values, and metric values involved.

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
    """
    if not tiers or not metric_map:
        return  # Nothing to verify

    # Get common exits that exist in both tiers and metric_map
    common_exits = set(tiers.keys()) & set(metric_map.keys())
    exits = list(common_exits)

    for i, e1 in enumerate(exits):
        for e2 in exits[i + 1:]:
            t1, t2 = tiers[e1], tiers[e2]
            m1, m2 = metric_map.get(e1), metric_map.get(e2)

            if m1 is None or m2 is None:
                continue

            # Check ordering invariant: tier(A) > tier(B) => metric ordering
            if t1 > t2:
                if higher_is_better:
                    # BGP: higher tier => higher local-pref
                    if not (m1 > m2):
                        raise ValueError(
                            f"Invariant violated (BGP): tier({e1})={t1} > tier({e2})={t2} "
                            f"but local_pref({e1})={m1} <= local_pref({e2})={m2}. "
                            f"Higher tier should have higher local-pref."
                        )
                else:
                    # OSPF: higher tier => lower cost
                    if not (m1 < m2):
                        raise ValueError(
                            f"Invariant violated (OSPF): tier({e1})={t1} > tier({e2})={t2} "
                            f"but cost({e1})={m1} >= cost({e2})={m2}. "
                            f"Higher tier should have lower cost."
                        )
            elif t1 < t2:
                if higher_is_better:
                    # BGP: lower tier => lower local-pref
                    if not (m1 < m2):
                        raise ValueError(
                            f"Invariant violated (BGP): tier({e1})={t1} < tier({e2})={t2} "
                            f"but local_pref({e1})={m1} >= local_pref({e2})={m2}. "
                            f"Lower tier should have lower local-pref."
                        )
                else:
                    # OSPF: lower tier => higher cost
                    if not (m1 > m2):
                        raise ValueError(
                            f"Invariant violated (OSPF): tier({e1})={t1} < tier({e2})={t2} "
                            f"but cost({e1})={m1} <= cost({e2})={m2}. "
                            f"Lower tier should have higher cost."
                        )
            else:
                # t1 == t2: metrics must be equal
                if m1 != m2:
                    metric_name = "local_pref" if higher_is_better else "cost"
                    raise ValueError(
                        f"Invariant violated: tier({e1})={t1} == tier({e2})={t2} "
                        f"but {metric_name}({e1})={m1} != {metric_name}({e2})={m2}. "
                        f"Equal tiers should have equal metrics."
                    )


def _median_int(values: list[int]) -> int:
    """整数中位数（偶数个取 lower-median，保证返回值来自原集合）。"""
    if not values:
        raise ValueError("median of empty list")
    vs = sorted(values)
    n = len(vs)
    mid = n // 2
    if n % 2 == 1:
        return int(vs[mid])
    return int(vs[mid - 1])


def _mode_or_median(values: list[int]) -> int:
    """
    优先取众数（若唯一），否则回退到中位数。
    用于近似"网络默认值/常用值"，以满足 tier==0 的"保持基线"语义。
    """
    if not values:
        raise ValueError("mode/median of empty list")
    cnt = Counter(values)
    most = cnt.most_common()
    top_count = most[0][1]
    top_vals = sorted([v for v, c in most if c == top_count])
    if len(top_vals) == 1:
        return int(top_vals[0])
    return _median_int(values)


def solve_bgp_local_pref(
    tiers: PreferenceTiers,
    sketch: Dict[str, Any],
    *,
    band_width: int = 1000,
    detected_step: Optional[int] = None,
) -> Dict[str, int]:
    """
    在现有 BGP local-pref 分布的基础上, 为每个出口分配一个具体的 local-pref 值。

    输入:
    - tiers: PreferenceTiers, exit_name -> tier（整数，越大越优先；0 表示保持基线；负数表示降权）
    - sketch: CurrentPolicySketch 中与 BGP 相关的子树 (例如 sketch["bgp_style"])
    - band_width: PathDelta 预留的 local-pref 带宽 (默认 1000), 将用于在 [L_max, L_max+band_width] 区间内分配新的等级
    - detected_step: Optional step from ContextAnalyzer for style-aware quantization.
                     When provided, values are calculated as baseline + (k * detected_step).
                     When None, falls back to default step of 10.

    Style-Aware Quantization (Requirements 2.1, 2.2, 2.3):
    When detected_step is provided:
    - New values are calculated as: baseline_value + (k * detected_step)
    - This ensures generated values align with the brownfield network's existing grid pattern
    
    Collision Avoidance (Requirements 2.4, 2.5, 8.1, 8.2):
    - Generated values are checked against existing_lprefs in the sketch
    - If a collision is detected, k is incremented until a non-colliding value is found
    - Raises ValueError if no non-colliding value can be found within MAX_COLLISION_ATTEMPTS

    Tier Ordering Invariant (Requirement 2.6):
    - Higher tier implies higher local-pref value
    - tier(A) > tier(B) => local_pref(A) > local_pref(B)

    基本思路:
    1. 从 sketch 中提取现网 local-pref 集合 L = {l1 < l2 < ... < lk}, 若为空则假定 {100}。
    2. 计算 L_min, L_max, gap_min (忽略过小 gap)。
    3. 定义 PathDelta 预留带:
       LP_band_start = L_max + max(gap_min, 10)
       LP_band_width = band_width
    4. 统计 tiers 中的正 tier 数量 (去掉 tier=0), 记为 K。
    5. 如果 K>0, 在线性区间 [LP_band_start, LP_band_start+LP_band_width] 内为每个 tier 分配一个数值:
          step = max(1, LP_band_width // (K + 1))
          LP_value(j) = LP_band_start + j * step
       将 tier(e)=j 的出口映射到 LP_value(j)。
    6. baseline（tier==0）映射：取 existing_lprefs 的众数（若唯一）或中位数，作为"保持基线"的目标值。
    7. tier==0：映射为 baseline_lp（不主动降权）。
    8. tier<0：映射为 max(10, baseline_lp - abs(tier)*delta)，delta 默认取 max(gap_min, 10)。
       语义：只对显式负 tier 的出口降权，避免 pin/ordered 等场景把大量未提及出口整体拉低。
    9. tier>0：仍使用 L_max 之上的预留带分配递增值，避免与现网冲突。
    10. 返回一个字典: exit_name -> local_pref_value

    约束:
    - 如果 tiers 中出现 tier(e1)>tier(e2) 却导致 LP(e1) <= LP(e2), 需要在后续实现中做断言/异常。
    - 本函数不修改 sketch 本身, 只读。
    """
    # 提取现有 local-pref 集合（保留重复，用于 mode）
    raw_vals: list[int] = []
    if isinstance(sketch, dict):
        for x in (sketch.get("existing_lprefs") or []):
            if isinstance(x, (int, float)):
                raw_vals.append(int(x))
    if not raw_vals:
        raw_vals = [100]

    baseline_lp = _mode_or_median(raw_vals)
    uniq = sorted(set(raw_vals))
    L_min = min(uniq)
    L_max = max(uniq)
    gaps = [b - a for a, b in zip(uniq, uniq[1:]) if b - a > 0]
    gap_min = min(gaps) if gaps else 10

    # Use detected_step if provided, otherwise fall back to default (Requirement 2.3)
    effective_step = detected_step if detected_step is not None else 10
    
    # Build set of existing values for collision avoidance (Requirements 2.4, 8.1)
    existing_lprefs: Set[int] = set(uniq)

    LP_band_start = L_max + max(gap_min, effective_step)
    LP_band_width = band_width
    delta = max(gap_min, effective_step)

    # 收集正 tier
    tier_values = sorted(set(v for v in tiers.values() if v > 0))
    K = len(tier_values)
    lp_map: Dict[str, int] = {}
    
    if K > 0:
        if detected_step is not None:
            # Style-aware quantized generation (Requirements 2.1, 2.2)
            # Calculate values as baseline + (k * detected_step)
            tier_to_lp: Dict[int, int] = {}
            for idx, tv in enumerate(tier_values, start=1):
                # Start from baseline and add k * step
                candidate = baseline_lp + idx * detected_step
                
                # Collision avoidance (Requirements 2.4, 2.5, 8.2)
                attempts = 0
                while candidate in existing_lprefs and attempts < MAX_COLLISION_ATTEMPTS:
                    attempts += 1
                    candidate += detected_step
                
                if attempts >= MAX_COLLISION_ATTEMPTS:
                    raise ValueError(
                        f"Cannot find non-colliding local-pref value after {MAX_COLLISION_ATTEMPTS} attempts. "
                        f"Existing values: {sorted(existing_lprefs)}, "
                        f"Attempted range: [{baseline_lp + idx * detected_step}, {candidate}]. "
                        f"Consider expanding the value range or reducing tier count."
                    )
                
                tier_to_lp[tv] = candidate
                # Add to existing set to prevent collisions with subsequent tiers
                existing_lprefs.add(candidate)
        else:
            # Legacy band-based allocation
            step = max(1, LP_band_width // (K + 1))
            tier_to_lp = {tv: LP_band_start + idx * step for idx, tv in enumerate(tier_values, start=1)}
        
        for exit_name, tv in tiers.items():
            if tv > 0:
                lp_map[exit_name] = int(tier_to_lp[tv])

    # 收集负 tier（用于确保不同负 tier 有不同的 local-pref）
    neg_tier_values = sorted(set(v for v in tiers.values() if v < 0), reverse=True)  # -1, -2, -3, ...
    
    # tier==0（保持基线）与 tier<0（显式降权）
    for exit_name, tv in tiers.items():
        if tv == 0:
            lp_map[exit_name] = int(baseline_lp)
        elif tv < 0:
            # 为负 tier 分配递减的 local-pref 值
            if detected_step is not None:
                # Style-aware: use detected_step for negative tiers too
                neg_step = detected_step
            else:
                # Legacy: use smaller step to ensure different values
                neg_step = max(1, min(delta, (baseline_lp - 10) // max(1, len(neg_tier_values))))
            
            # 找到当前 tier 在负 tier 列表中的索引（-1 是最高的负 tier，索引 0）
            neg_idx = neg_tier_values.index(tv)
            # 计算 local-pref：baseline_lp - (neg_idx + 1) * neg_step
            computed_lp = baseline_lp - (neg_idx + 1) * neg_step
            
            # Collision avoidance for negative tiers
            if detected_step is not None:
                attempts = 0
                while computed_lp in existing_lprefs and attempts < MAX_COLLISION_ATTEMPTS:
                    attempts += 1
                    computed_lp -= detected_step
                
                if attempts >= MAX_COLLISION_ATTEMPTS:
                    raise ValueError(
                        f"Cannot find non-colliding local-pref value for negative tier after "
                        f"{MAX_COLLISION_ATTEMPTS} attempts. "
                        f"Existing values: {sorted(existing_lprefs)}, "
                        f"Consider expanding the value range or reducing tier count."
                    )
            
            # 确保最小值为 10，但不同负 tier 仍有不同值
            if computed_lp < 10:
                # 为每个负 tier 分配 10 + (len - idx - 1) 确保严格递减
                lp_map[exit_name] = 10 + (len(neg_tier_values) - neg_idx - 1)
            else:
                lp_map[exit_name] = int(computed_lp)
            
            # Track for collision avoidance
            if detected_step is not None:
                existing_lprefs.add(lp_map[exit_name])

    return lp_map


def solve_ospf_cost(
    tiers: PreferenceTiers,
    sketch: Dict[str, Any],
    *,
    cost_step: int = 10,
    detected_step: Optional[int] = None,
) -> Dict[str, int]:
    """
    在现有 OSPF cost 分布基础上, 为每个出口分配新的 cost。

    输入:
    - tiers: PreferenceTiers, exit_name -> tier（整数；0 表示保持基线；负数表示降权）
    - sketch: CurrentPolicySketch 中与 OSPF 相关的子树 (例如 sketch["ospf_style"])
    - cost_step: 每一档 tier 之间的 cost 差值 (默认 10)
    - detected_step: Optional step from ContextAnalyzer for style-aware quantization.
                     When provided, values are calculated as baseline - (tier * detected_step).
                     When None, falls back to cost_step parameter.

    Style-Aware Quantization (Requirements 3.1, 3.2, 3.3):
    When detected_step is provided:
    - New values are calculated as: baseline_cost - (tier * detected_step)
    - This ensures generated values align with the brownfield network's existing grid pattern
    
    Collision Avoidance (Requirements 8.3, 8.4):
    - Generated values are checked against existing_costs in the sketch
    - If a collision is detected, cost is decremented by detected_step until non-colliding
    - Raises ValueError if no non-colliding value can be found within MAX_COLLISION_ATTEMPTS

    Cost Validity (Requirements 3.4, 3.5):
    - Generated cost values must be >= 1 (valid OSPF cost range)
    - Raises ValueError if calculated cost would be < 1

    Tier Ordering Invariant (Requirement 3.6):
    - Higher tier implies lower cost value
    - tier(A) > tier(B) => cost(A) < cost(B)

    基本思路（tier 语义收敛）:
    1. 从 sketch 中提取相关链路的 cost 集合 C, 若为空则默认 {10}。
    2. baseline_cost：取 existing_costs 的中位数，作为 tier==0 的"保持基线"目标值。
    3. 令 K_pos = tiers 中的最大正 tier 值（若无则 0）。
    4. 对 tier=j>0 的出口，定义 cost 越小越优先：
          cost(e) = baseline_cost - j * step
       其中 step 会根据 baseline_cost 的 headroom 自动缩小，保证 cost>=1 且严格单调。
    5. 对于 ECMP (多出口同 tier) 的情况, 多个出口会得到相同 cost, 从而形成等价多路径。
    6. tier==0：cost=baseline_cost（尽量不改动）。
    7. tier<0：cost=baseline_cost + abs(tier) * penalty，其中 penalty 默认取 3*cost_step。

    返回:
    - exit_name -> cost_value
    """
    raw_costs: list[int] = []
    if isinstance(sketch, dict):
        for x in (sketch.get("existing_costs") or []):
            if isinstance(x, (int, float)):
                raw_costs.append(int(x))
    if not raw_costs:
        raw_costs = [10]
    baseline_cost = _median_int(raw_costs)

    if not tiers:
        return {}

    # Use detected_step if provided, otherwise fall back to cost_step (Requirement 3.3)
    effective_step = detected_step if detected_step is not None else cost_step
    
    # Build set of existing values for collision avoidance (Requirements 8.3)
    existing_costs: Set[int] = set(raw_costs)

    pos = [v for v in tiers.values() if v > 0]
    K_pos = max(pos) if pos else 0
    
    # step 必须保证在 baseline_cost 之下留出足够 headroom，避免 cost<=0 或失去严格单调性
    if K_pos > 0:
        headroom = baseline_cost - 1
        step_cap = headroom // (K_pos + 1)
        if step_cap <= 0:
            raise ValueError(
                f"Not enough OSPF cost headroom to encode tiers: baseline_cost={baseline_cost} "
                f"K_pos={K_pos}. Consider increasing baseline_cost in sketch or reducing tier range."
            )
        step = min(int(effective_step), int(step_cap))
    else:
        step = int(effective_step)

    penalty = 3 * int(cost_step)
    cost_map: Dict[str, int] = {}
    
    # First, compute cost for each unique tier value to ensure equal tiers get equal costs
    # This is critical for ECMP (Requirement 3.6: tier(A) == tier(B) => cost(A) == cost(B))
    tier_to_cost: Dict[int, int] = {}
    
    # Get unique positive tier values sorted (highest first for processing)
    pos_tier_values = sorted(set(v for v in tiers.values() if v > 0), reverse=True)
    
    # Track assigned costs for collision avoidance
    assigned_costs: Set[int] = set()
    
    # Compute cost for each positive tier
    for tv in pos_tier_values:
        # Style-aware quantized generation (Requirements 3.1, 3.2)
        candidate = int(baseline_cost - int(tv) * step)
        
        # Collision avoidance (Requirements 8.3, 8.4)
        if detected_step is not None:
            attempts = 0
            while (candidate in existing_costs or candidate in assigned_costs) and attempts < MAX_COLLISION_ATTEMPTS:
                attempts += 1
                candidate -= detected_step
            
            if attempts >= MAX_COLLISION_ATTEMPTS:
                raise ValueError(
                    f"Cannot find non-colliding OSPF cost value after {MAX_COLLISION_ATTEMPTS} attempts. "
                    f"Existing values: {sorted(existing_costs)}, "
                    f"Consider expanding the value range or reducing tier count."
                )
        
        # Ensure cost >= 1 (Requirement 3.4)
        if candidate < 1:
            raise ValueError(
                f"Insufficient OSPF cost headroom: baseline_cost={baseline_cost}, "
                f"tier={tv}, detected_step={detected_step}. "
                f"Calculated cost would be {candidate} < 1. "
                f"Consider increasing baseline_cost or reducing tier range."
            )
        
        tier_to_cost[tv] = candidate
        assigned_costs.add(candidate)
    
    # Compute cost for each negative tier
    neg_tier_values = sorted(set(v for v in tiers.values() if v < 0))  # Most negative first
    
    for tv in neg_tier_values:
        # Negative tier: increase cost (penalty)
        candidate = int(baseline_cost + abs(int(tv)) * penalty)
        
        # Collision avoidance for negative tiers
        if detected_step is not None:
            attempts = 0
            while (candidate in existing_costs or candidate in assigned_costs) and attempts < MAX_COLLISION_ATTEMPTS:
                attempts += 1
                candidate += detected_step
            
            if attempts >= MAX_COLLISION_ATTEMPTS:
                raise ValueError(
                    f"Cannot find non-colliding OSPF cost value for negative tier after "
                    f"{MAX_COLLISION_ATTEMPTS} attempts. "
                    f"Existing values: {sorted(existing_costs)}, "
                    f"Consider expanding the value range or reducing tier count."
                )
        
        tier_to_cost[tv] = candidate
        assigned_costs.add(candidate)
    
    # Now assign costs to exits based on their tier
    for exit_name, tv in tiers.items():
        if tv == 0:
            cost_map[exit_name] = int(baseline_cost)
        else:
            cost_map[exit_name] = tier_to_cost[tv]
    
    return cost_map
