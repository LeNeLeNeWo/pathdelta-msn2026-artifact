from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from intent_layer.schema import IntentCard

from .models import AffectScope, PolicyEntry, PolicySketch
from .param_solver import solve_bgp_local_pref, solve_ospf_cost
from .preference_tiers import PreferenceTiers, build_preference_tiers

logger = logging.getLogger(__name__)

# 机制归一化注册表：mechanism_name -> normalizer_fn
# normalizer_fn 只负责补充“该机制专属参数”，数值类统一优先走 tiers + param_solver
MECHANISM_NORMALIZERS: Dict[
    str, Callable[[IntentCard, AffectScope, str, str, PolicySketch, Dict[str, object]], Dict[str, object]]
] = {}


def register_mechanism_normalizer(name: str) -> Callable:
    """装饰器：注册 mechanism 的专属参数归一化函数。"""

    def decorator(fn: Callable) -> Callable:
        MECHANISM_NORMALIZERS[name] = fn
        return fn

    return decorator


def _safe_avoid_exits(intent: IntentCard) -> List[str]:
    """
    兼容新旧字段：
    - 新：avoid_exits / normalized_avoid_exits()
    - 旧：avoid_exit
    """
    if hasattr(intent, "normalized_avoid_exits"):
        v = intent.normalized_avoid_exits() or []
        if isinstance(v, list):
            return [str(x) for x in v]
    v = getattr(intent, "avoid_exits", None)
    if isinstance(v, list):
        return [str(x) for x in v]
    old = getattr(intent, "avoid_exit", None)
    if isinstance(old, str) and old:
        return [old]
    return []


def _existing_lprefs(sketch: PolicySketch) -> List[int]:
    """从 CurrentPolicySketch 的 bgp_style 中提取现有 local-pref 值集合，用于求解器参考。"""
    lp = []
    if isinstance(sketch.bgp_style, dict):
        lp = sketch.bgp_style.get("existing_lprefs") or []
    vals: List[int] = []
    for x in lp:
        if isinstance(x, (int, float)):
            vals.append(int(x))
    return sorted(set(vals)) or [100]


def _base_ospf_cost(sketch: PolicySketch) -> int:
    """OSPF cost 的参考基线（可从 sketch 扩展读取）。"""
    if isinstance(sketch.ospf_style, dict):
        v = sketch.ospf_style.get("base_cost")
        if isinstance(v, (int, float)):
            return int(v)
    return 10


def _extract_detected_step(style: Dict[str, Any], key: str = "detected_step") -> Optional[int]:
    """
    Extract detected_step from bgp_style or ospf_style section of the sketch.
    
    The detected_step is used for style-aware parameter quantization, ensuring
    generated values align with the brownfield network's existing grid pattern.
    
    Args:
        style: The bgp_style or ospf_style dict from PolicySketch
        key: The key to look for (default: "detected_step")
    
    Returns:
        The detected step value if present and valid, None otherwise
    
    Requirements: 2.2, 3.2
    """
    if not isinstance(style, dict):
        return None
    
    step = style.get(key)
    if isinstance(step, (int, float)) and step > 0:
        return int(step)
    
    return None


def _assert_bgp_tier_constraints(
    intent: IntentCard, mechanism: str, tiers: PreferenceTiers, lp_map: Dict[str, int]
) -> None:
    """
    强制约束：tiers 单调性/等价性必须映射到 local-pref 上。
    - tier(e1) > tier(e2) => lp(e1) > lp(e2)
    - tier(e1) = tier(e2) => lp(e1) = lp(e2)
    """
    exits = list(tiers.keys())
    for i in range(len(exits)):
        for j in range(len(exits)):
            if i == j:
                continue
            e1, e2 = exits[i], exits[j]
            t1, t2 = tiers[e1], tiers[e2]
            lp1, lp2 = lp_map.get(e1), lp_map.get(e2)
            if lp1 is None or lp2 is None:
                continue
            if t1 > t2 and not (lp1 > lp2):
                raise ValueError(
                    f"local-pref order violated: intent={intent.intent_id} mech={mechanism} "
                    f"tier({e1})={t1} tier({e2})={t2} but lp({e1})={lp1} <= lp({e2})={lp2}"
                )
            if t1 == t2 and not (lp1 == lp2):
                raise ValueError(
                    f"local-pref equality violated: intent={intent.intent_id} mech={mechanism} "
                    f"tier({e1})={t1} tier({e2})={t2} but lp({e1})={lp1} != lp({e2})={lp2}"
                )


def _assert_ospf_tier_constraints(
    intent: IntentCard, mechanism: str, tiers: PreferenceTiers, cost_map: Dict[str, int]
) -> None:
    """
    强制约束：tiers 单调性/等价性必须映射到 OSPF cost 上。
    - tier(e1) > tier(e2) => cost(e1) < cost(e2)
    - tier(e1) = tier(e2) => cost(e1) = cost(e2)
    """
    exits = list(tiers.keys())
    for i in range(len(exits)):
        for j in range(len(exits)):
            if i == j:
                continue
            e1, e2 = exits[i], exits[j]
            t1, t2 = tiers[e1], tiers[e2]
            c1, c2 = cost_map.get(e1), cost_map.get(e2)
            if c1 is None or c2 is None:
                continue
            if t1 > t2 and not (c1 < c2):
                raise ValueError(
                    f"ospf-cost order violated: intent={intent.intent_id} mech={mechanism} "
                    f"tier({e1})={t1} tier({e2})={t2} but cost({e1})={c1} >= cost({e2})={c2}"
                )
            if t1 == t2 and not (c1 == c2):
                raise ValueError(
                    f"ospf-cost equality violated: intent={intent.intent_id} mech={mechanism} "
                    f"tier({e1})={t1} tier({e2})={t2} but cost({e1})={c1} != cost({e2})={c2}"
                )


@register_mechanism_normalizer("bgp_community_pin")
def _normalize_bgp_community_pin(
    intent: IntentCard,
    scope: AffectScope,
    proto: str,
    mechanism: str,
    sketch: PolicySketch,
    ctx: Dict[str, object],
) -> Dict[str, object]:
    """
    基于 community 的 pin：这里只输出“community 标签建议”，不负责合成层具体 route-map/policy 细节。
    合成层若实现，可优先复用 existing community-list/route-map，否则从模板库实例化。
    """
    pinned = getattr(intent, "pinned_exit", None)
    if not pinned:
        # 兼容：如果 pin_to_exit 仍用 primary_exit 字段
        pinned = getattr(intent, "primary_exit", None)

    # 生成稳定可读的 community 标记（合成层可按现网风格重命名）
    tag = f"65000:100{hash(pinned) % 1000:03d}" if pinned else "65000:100000"
    return {"community_action": "pin", "community_tag": tag, "pinned_exit": pinned}


@register_mechanism_normalizer("bgp_community_avoid")
def _normalize_bgp_community_avoid(
    intent: IntentCard,
    scope: AffectScope,
    proto: str,
    mechanism: str,
    sketch: PolicySketch,
    ctx: Dict[str, object],
) -> Dict[str, object]:
    avoids = _safe_avoid_exits(intent)
    tag = f"65000:200{hash(tuple(avoids)) % 1000:03d}" if avoids else "65000:200000"
    return {"community_action": "avoid", "community_tag": tag, "avoid_exits": avoids}


def normalize_policy(
    intent: IntentCard,
    scope: AffectScope,
    proto: str,
    mechanism: str,
    sketch: PolicySketch,
) -> PolicyEntry:
    """
    根据 (intent + scope + proto + mechanism + sketch) 生成 PolicyEntry（含 preference_tiers 与 params）。

    设计原则：
    - 先构造抽象 tiers（出口 -> 偏好层级），再用 param_solver 映射为 local-pref / ospf cost；
    - 机制专属参数（例如 community 元信息）通过注册表补充；
    - 对 tiers->params 强制做单调性/等价性约束检查，避免参数把语义映射反。
    
    Safety: Prefix-Level Isolation
    - All policies MUST be bound to a specific prefix (sandbox guarantee)
    - path_migration uses prefixes[0] as the primary prefix
    - Missing prefix raises ValueError with descriptive message
    """
    if mechanism == "static_route":
        raise NotImplementedError("static_route is reserved for future extension; not supported now.")

    # SAFETY: Enforce Prefix-Level Isolation (Requirements 5.1, 5.2, 5.3, 5.4)
    # All policies must be bound to a specific prefix for safety.
    prefix = getattr(intent, "prefix", None)
    
    # Handle path_migration: use first prefix from prefixes list
    if intent.type == "path_migration":
        prefixes = getattr(intent, "prefixes", None)
        if prefixes and len(prefixes) > 0:
            prefix = prefixes[0]
        else:
            raise ValueError(
                f"Prefix isolation violated: path_migration intent '{intent.intent_id}' "
                "requires non-empty prefixes list. All policies must be bound to a specific prefix for safety."
            )
    
    # Validate prefix is present for all intent types
    if not prefix:
        raise ValueError(
            f"Prefix isolation violated: intent '{intent.intent_id}' (type={intent.type}) has no prefix. "
            "All policies must be bound to a specific prefix for safety."
        )

    tiers = build_preference_tiers(intent, scope)
    params: Dict[str, object] = {}

    # ---------- BGP numeric params ----------
    bgp_mechs = {
        "local_pref",
        "local_pref_ladder",
        "maximum_paths",
        "local_pref_equal",
        "local_pref_pin",
        "local_pref_degrade",
        "bgp_community_pin",
        "bgp_community_avoid",
    }
    ospf_mechs = {"cost", "cost_equal", "cost_ladder"}

    if proto == "bgp" and mechanism in bgp_mechs:
        # Extract detected_step from sketch for style-aware quantization (Requirements 2.2)
        bgp_detected_step = _extract_detected_step(sketch.bgp_style)
        lp_map = solve_bgp_local_pref(tiers, sketch.bgp_style, detected_step=bgp_detected_step)
        params["local_pref_by_exit"] = lp_map

        # maximum-paths: 对 ECMP 类意图需要
        if mechanism == "maximum_paths":
            base_max_paths = 2
            if isinstance(sketch.bgp_style, dict):
                v = sketch.bgp_style.get("max_paths")
                if isinstance(v, (int, float)):
                    base_max_paths = int(v)

            # 期望的 ECMP 路数应基于“参与并行的出口数量”，而不是 scope.affected_devices 的大小。
            # 约定：ECMP 的出口通常对应最高 tier（或所有 tier>0 的集合）。
            if tiers:
                max_tier = max(tiers.values())
                ecmp_exits = [e for e, tv in tiers.items() if tv == max_tier and tv > 0]
                k = len(ecmp_exits) if ecmp_exits else len([e for e, tv in tiers.items() if tv > 0])
            else:
                k = 1
            params["maximum_paths"] = max(1, min(int(k), int(base_max_paths)))

        # 兼容旧字段（不影响新 IR）
        primary = getattr(intent, "primary_exit", None)
        backup = getattr(intent, "backup_exit", None)
        if primary and primary in lp_map:
            params["lp_primary"] = lp_map[primary]
        if backup and backup in lp_map:
            params["lp_backup"] = lp_map[backup]

        # community 机制：补充 community 元信息（仍保留 local_pref_by_exit 便于调试/测试）
        if mechanism in {"bgp_community_pin", "bgp_community_avoid"}:
            normalizer_fn = MECHANISM_NORMALIZERS.get(mechanism)
            if normalizer_fn:
                ctx = {
                    "lp_existing": _existing_lprefs(sketch),
                    "base_ospf_cost": _base_ospf_cost(sketch),
                }
                params.update(normalizer_fn(intent, scope, proto, mechanism, sketch, ctx))

        # tiers 约束检查（强制）
        _assert_bgp_tier_constraints(intent, mechanism, tiers, lp_map)

    # ---------- OSPF numeric params ----------
    elif proto == "ospf" and mechanism in ospf_mechs:
        # Extract detected_step from sketch for style-aware quantization (Requirements 3.2)
        ospf_detected_step = _extract_detected_step(sketch.ospf_style)
        cost_map = solve_ospf_cost(tiers, sketch.ospf_style, detected_step=ospf_detected_step)
        params["ospf_cost_by_exit"] = cost_map
        _assert_ospf_tier_constraints(intent, mechanism, tiers, cost_map)

    # ---------- Other/extension mechanisms ----------
    else:
        normalizer_fn = MECHANISM_NORMALIZERS.get(mechanism)
        if normalizer_fn:
            ctx = {
                "lp_existing": _existing_lprefs(sketch),
                "base_ospf_cost": _base_ospf_cost(sketch),
            }
            params.update(normalizer_fn(intent, scope, proto, mechanism, sketch, ctx))
        else:
            logger.info(
                "No normalizer registered for mechanism=%s (proto=%s). params will be empty/solver-only.",
                mechanism,
                proto,
            )

    # 将 scope.sources 显式写入 params，供 runner/后处理的 ospf_steering 使用（仅 OSPF）
    if proto == "ospf":
        srcs = scope.sources or []
        if isinstance(srcs, list):
            params["ospf_sources"] = [str(x) for x in srcs if str(x)]
        else:  # pragma: no cover - defensive
            params["ospf_sources"] = []
        sources_source = getattr(scope, "sources_source", "") or ""
        if sources_source:
            params["ospf_sources_source"] = str(sources_source)

    ordered_exits_value: Optional[List[str]] = None
    if hasattr(intent, "normalized_ordered_exits"):
        v = intent.normalized_ordered_exits()
        if isinstance(v, list) and v:
            ordered_exits_value = [str(x) for x in v]

    entry = PolicyEntry(
        intent_id=intent.intent_id,
        type=intent.type,
        proto=proto,
        mechanism=mechanism,
        scope=getattr(intent, "scope", "prefix"),
        prefix=prefix,  # Use validated prefix (enforces prefix isolation)
        src_as=getattr(intent, "src_as", None),
        primary_exit=getattr(intent, "primary_exit", None),
        backup_exit=getattr(intent, "backup_exit", None),
        ordered_exits=ordered_exits_value,
        pinned_exit=getattr(intent, "pinned_exit", None),
        avoid_exits=_safe_avoid_exits(intent),
        preference_tiers=tiers,
        affected_devices=scope.affected_devices or [],
        affected_neighbors=scope.affected_neighbors or {},
        params=params,
    )
    return entry
