from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from intent_layer.schema import IntentCard
from .models import PolicySketch
from .topology_view import Topology

MECH_RULES: Dict[str, Dict[str, List[str]]] = {
    "prefer_with_backup": {
        "bgp": ["local_pref"],
        "ospf": ["cost"],
        "static": ["static_route"],
    },
    "ecmp": {
        "bgp": ["maximum_paths", "local_pref_equal"],
        "ospf": ["cost_equal"],
    },
    "ordered_preference": {
        "bgp": ["local_pref_ladder"],
        # OSPF cost_ladder removed - ordered_preference is BGP-only per CCF-B scope
    },
    "pin_to_exit": {
        "bgp": ["local_pref_pin", "bgp_community_pin"],
        "ospf": ["cost"],
    },
    "avoid_exit": {
        "bgp": ["local_pref_degrade", "bgp_community_avoid"],
        "ospf": ["cost"],
    },
    "path_migration": {
        "bgp": ["local_pref_ladder", "local_pref_pin"],
    },
    "ospf_steering": {
        "ospf": ["cost", "cost_ladder"],
    },
}

# 机制所需协议/能力标注，用于选择前过滤不可用机制
MECH_CAPABILITIES: Dict[str, List[str]] = {
    "local_pref": ["bgp"],
    "as_prepend": ["bgp"],
    "local_pref_ladder": ["bgp"],
    "maximum_paths": ["bgp"],
    "local_pref_equal": ["bgp"],
    "local_pref_pin": ["bgp"],
    "local_pref_degrade": ["bgp"],
    "bgp_community_pin": ["bgp"],
    "bgp_community_avoid": ["bgp"],
    "cost": ["ospf"],
    "cost_equal": ["ospf"],
    "cost_ladder": ["ospf"],
    "static_route": ["static"],
}


def detect_protocols_from_sketch(sketch: PolicySketch) -> List[str]:
    """
    尝试从 sketch 中推断可用的路由协议，避免硬依赖 global.protocols。
    优先顺序：
    - global.protocols
    - bgp_style.enabled / ospf_style.enabled
    - capabilities.routing_protocols
    """
    def _parse_enabled_flag(style: Any) -> Optional[bool]:
        """
        enabled 字段是“硬开关（authoritative）”：
        - 显式 False：无论 global.protocols / capabilities.routing_protocols 写了什么，都必须视为未启用；
        - 显式 True：视为启用；
        - 字段缺失：才允许回退用其它字段推断。
        """
        if not isinstance(style, dict) or "enabled" not in style:
            return None
        val = style.get("enabled")
        if isinstance(val, bool):
            return val
        # 兼容弱类型（例如手写 JSON/YAML，把 true/false 写成字符串/数字）
        if isinstance(val, int) and val in (0, 1):
            return bool(val)
        if isinstance(val, str):
            lowered = val.strip().lower()
            if lowered in {"true", "yes", "y", "1"}:
                return True
            if lowered in {"false", "no", "n", "0"}:
                return False
        return None

    bgp_enabled_flag = _parse_enabled_flag(sketch.bgp_style)
    ospf_enabled_flag = _parse_enabled_flag(sketch.ospf_style)

    protos: set[str] = set()

    global_protos = sketch.global_.get("protocols") if isinstance(sketch.global_, dict) else None
    if isinstance(global_protos, dict):
        global_protos = list(global_protos.keys())
    if isinstance(global_protos, (list, tuple, set)):
        for p in global_protos:
            if p in ("bgp", "ospf"):
                protos.add(p)

    caps = sketch.capabilities.get("routing_protocols") if isinstance(sketch.capabilities, dict) else None
    if isinstance(caps, dict):
        caps = list(caps.keys())
    if isinstance(caps, (list, tuple, set)):
        for p in caps:
            if p in ("bgp", "ospf"):
                protos.add(p)

    # ===== 硬开关过滤（优先级最高）=====
    # 仅当 enabled 字段缺失（None）时，才允许 global/capabilities 的推断生效。
    if bgp_enabled_flag is False:
        protos.discard("bgp")
    elif bgp_enabled_flag is True:
        protos.add("bgp")

    if ospf_enabled_flag is False:
        protos.discard("ospf")
    elif ospf_enabled_flag is True:
        protos.add("ospf")

    # 保持输出稳定顺序（便于日志/调试）
    return [p for p in ("bgp", "ospf") if p in protos]


def decide_proto(intent: IntentCard, sketch: PolicySketch, force_proto: str = "auto") -> Tuple[str, str]:
    """
    协议决策：返回 (proto, reason)。

    规则顺序：
    1) force_proto != "auto"：强制（并校验 enabled 硬开关不为 False）
    2) auto 且仅一协议 enabled：选择该协议
    3) auto 且两协议 enabled：按启发式 A/B 判断，否则默认 BGP
    """

    def _parse_enabled_flag(style: Any) -> Optional[bool]:
        # enabled 是 authoritative 硬开关：False 必须压过其它字段的“推断”
        if not isinstance(style, dict) or "enabled" not in style:
            return None
        val = style.get("enabled")
        if isinstance(val, bool):
            return val
        if isinstance(val, int) and val in (0, 1):
            return bool(val)
        if isinstance(val, str):
            lowered = val.strip().lower()
            if lowered in {"true", "yes", "y", "1"}:
                return True
            if lowered in {"false", "no", "n", "0"}:
                return False
        return None

    def _as_proto_set(v: object) -> set[str]:
        res: set[str] = set()
        if isinstance(v, dict):
            it = v.keys()
        elif isinstance(v, (list, tuple, set)):
            it = v
        else:
            return res
        for p in it:
            if p in ("bgp", "ospf"):
                res.add(p)
        return res

    raw_global = sketch.global_.get("protocols") if isinstance(sketch.global_, dict) else None
    raw_caps = sketch.capabilities.get("routing_protocols") if isinstance(sketch.capabilities, dict) else None
    raw_mentions = _as_proto_set(raw_global) | _as_proto_set(raw_caps)

    bgp_enabled_flag = _parse_enabled_flag(sketch.bgp_style)
    ospf_enabled_flag = _parse_enabled_flag(sketch.ospf_style)

    warnings: List[str] = []
    if bgp_enabled_flag is False and "bgp" in raw_mentions:
        warnings.append("bgp_style.enabled=false but global/capabilities mention bgp -> ignore bgp")
    if ospf_enabled_flag is False and "ospf" in raw_mentions:
        warnings.append("ospf_style.enabled=false but global/capabilities mention ospf -> ignore ospf")

    detected = detect_protocols_from_sketch(sketch)  # 已应用 enabled 硬开关
    detected_set = set(detected)

    def _with_warnings(reason: str) -> str:
        return f"{reason}; warnings={warnings}" if warnings else reason

    # 1) 强制
    if force_proto not in {"auto", "bgp", "ospf"}:
        raise ValueError(f"Invalid force_proto={force_proto!r}, expected one of: auto/bgp/ospf")
    if force_proto == "bgp":
        if bgp_enabled_flag is False:
            raise ValueError(
                f"force_proto=bgp but bgp_style.enabled=False; "
                f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} prefix={getattr(intent, 'prefix', None)} "
                f"bgp_enabled={bgp_enabled_flag} ospf_enabled={ospf_enabled_flag} raw_mentions={sorted(raw_mentions)}"
            )
        return "bgp", _with_warnings(f"forced: proto=bgp detected_protocols={detected}")
    if force_proto == "ospf":
        if ospf_enabled_flag is False:
            raise ValueError(
                f"force_proto=ospf but ospf_style.enabled=False; "
                f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} prefix={getattr(intent, 'prefix', None)} "
                f"bgp_enabled={bgp_enabled_flag} ospf_enabled={ospf_enabled_flag} raw_mentions={sorted(raw_mentions)}"
            )
        return "ospf", _with_warnings(f"forced: proto=ospf detected_protocols={detected}")

    # 2) auto：只有一个协议可用
    if detected_set == {"bgp"}:
        return "bgp", _with_warnings(f"auto: only bgp enabled (detected_protocols={detected})")
    if detected_set == {"ospf"}:
        return "ospf", _with_warnings(f"auto: only ospf enabled (detected_protocols={detected})")
    if not detected_set:
        raise ValueError(
            f"No routing protocols detected from sketch (bgp/ospf). "
            f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} raw_mentions={sorted(raw_mentions)}"
        )

    # 3) auto：bgp+ospf 都可用 -> 启发式 A/B
    prefix = (getattr(intent, "prefix", None) or "").strip()

    # 条件 A（强）：默认路由优先 OSPF steering
    if prefix == "0.0.0.0/0":
        return "ospf", _with_warnings("auto: both bgp+ospf enabled; condition_A(default_route) -> ospf")

    # 条件 B（保守）：BGP 属性工程很轻 + OSPF cost 工程有迹象
    bgp_style = sketch.bgp_style if isinstance(sketch.bgp_style, dict) else {}
    ospf_style = sketch.ospf_style if isinstance(sketch.ospf_style, dict) else {}

    # B 要求 enabled 显式为 True（缺失则保守不触发）
    if bgp_enabled_flag is True and ospf_enabled_flag is True:
        bgp_used = bgp_style.get("used_mechanisms") or []
        if not isinstance(bgp_used, list):
            bgp_used = []
        bgp_prefer = bgp_style.get("prefer_mechanism")

        heavy_markers = {
            "local_pref",
            "local_pref_ladder",
            "local_pref_pin",
            "local_pref_degrade",
            "as_prepend",
            "med",
            "route_map_policy",
        }

        def _is_bgp_attr_engineering(x: object) -> bool:
            s = str(x).strip().lower()
            if not s:
                return False
            if s in heavy_markers:
                return True
            if "community" in s:
                return True
            if "route_map" in s or "policy" in s:
                return True
            return False

        bgp_heavy = any(_is_bgp_attr_engineering(x) for x in bgp_used)
        if bgp_prefer not in (None, "", "none"):
            bgp_heavy = bgp_heavy or _is_bgp_attr_engineering(bgp_prefer)

        ospf_used = ospf_style.get("used_mechanisms") or []
        if not isinstance(ospf_used, list):
            ospf_used = []
        ospf_prefer = ospf_style.get("prefer_mechanism")
        ospf_cost_evidence = any(str(x).strip().lower() in {"cost", "cost_ladder", "cost_equal"} for x in ospf_used) or (
            str(ospf_prefer).strip().lower() == "cost" if ospf_prefer is not None else False
        )

        if (not bgp_heavy) and ospf_cost_evidence:
            return "ospf", _with_warnings(
                "auto: both bgp+ospf enabled; condition_B(bgp_light & ospf_cost_engineering) -> ospf"
            )

    return "bgp", _with_warnings("auto: both bgp+ospf enabled; condition_A/B not met -> bgp")


def is_mechanism_supported(mech: str, protocols: List[str]) -> bool:
    """
    根据机制所需协议与当前检测到的协议能力决定是否可用。
    未标注能力的机制默认认为可用（向后兼容）。
    """
    # static_route 暂无实现，屏蔽以免生成不可落地策略（保留占位供未来扩展）
    if mech == "static_route":
        return False
    requires = MECH_CAPABILITIES.get(mech)
    if not requires:
        return True
    return all(req in protocols for req in requires)


def _has_community_capability(sketch: PolicySketch) -> bool:
    """
    粗略判定是否可以安全使用基于 community 的机制：
    - 当前策略草图已声明 community 列表，或
    - bgp_style.used_mechanisms 里已有 community 相关痕迹，或
    - bgp_style 显式标记 community_capable。
    """
    bgp_style = sketch.bgp_style if isinstance(sketch.bgp_style, dict) else {}
    # 优先查看 top-level existing_objects（tools 产出在顶层）
    existing_objs = {}
    if isinstance(sketch.global_, dict):
        existing_objs = sketch.global_.get("existing_objects") or {}
    if not existing_objs and isinstance(sketch.__dict__.get("existing_objects"), dict):
        existing_objs = sketch.__dict__["existing_objects"]
    if not existing_objs and isinstance(sketch.capabilities, dict):
        existing_objs = sketch.capabilities.get("existing_objects") or {}
    if isinstance(existing_objs, dict) and existing_objs.get("community_lists"):
        return True
    used = bgp_style.get("used_mechanisms") or []
    if any("community" in str(x) for x in used):
        return True
    if bgp_style.get("community_capable"):
        return True
    if isinstance(sketch.capabilities, dict) and sketch.capabilities.get("community_capable"):
        return True
    return False


def _score_mechanism(mech: str, sketch: PolicySketch, device_caps: Dict | None) -> int:
    score = 0
    bgp_style = sketch.bgp_style
    used = set(bgp_style.get("used_mechanisms") or [])
    prefer = bgp_style.get("prefer_mechanism")
    if mech in used:
        score += 1
    if prefer and mech == prefer:
        score += 1
    if device_caps:
        unsupported = set()
        if isinstance(device_caps, dict):
            unsupported.update(device_caps.get("unsupported", []) or [])
        if mech in unsupported:
            score -= 5
    return score


def select_mechanism(
    intent: IntentCard,
    topo: Topology,
    sketch: PolicySketch,
    proto_hint: str | None = None,
    device_caps: Dict | None = None,
) -> Tuple[str, str]:
    """Select protocol and mechanism for the given intent based on sketch."""
    protocols = detect_protocols_from_sketch(sketch)
    capabilities = sketch.capabilities.get("routing_protocols") if isinstance(sketch.capabilities, dict) else None
    if not capabilities:
        capabilities = protocols

    # 明确拒绝 RIP-only 或协议列表为空的场景，带上关键信息便于排障
    sketch_info = {
        "protocols": protocols,
        "capabilities": capabilities,
        "bgp_enabled": bool(getattr(sketch, "bgp_style", {}).get("enabled")) if isinstance(getattr(sketch, "bgp_style", {}), dict) else False,
        "ospf_enabled": bool(getattr(sketch, "ospf_style", {}).get("enabled")) if isinstance(getattr(sketch, "ospf_style", {}), dict) else False,
        "roles": {k: len(v) for k, v in (sketch.roles or {}).items()} if isinstance(getattr(sketch, "roles", {}), dict) else {},
    }
    if capabilities and not any(p in capabilities for p in ("bgp", "ospf")):
        raise ValueError(
            f"PathDelta does not support policy synthesis for RIP-only/unsupported topologies. "
            f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} sketch_info={sketch_info}"
        )
    if not protocols and not capabilities:
        raise ValueError(
            f"No routing protocols detected from sketch (bgp/ospf). "
            f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} sketch_info={sketch_info}"
        )

    # 协议决策：若上层已决定 proto（proto_hint），则只在该 proto 下选机制，避免 runner/builder 双重决策
    if proto_hint is not None:
        if proto_hint not in {"bgp", "ospf"}:
            raise ValueError(
                f"Invalid proto_hint={proto_hint!r}, expected bgp/ospf. "
                f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} sketch_info={sketch_info}"
            )
        if proto_hint not in protocols and proto_hint not in (capabilities or []):
            raise ValueError(
                f"proto_hint={proto_hint} but it is not enabled/detected in sketch. "
                f"intent_id={getattr(intent, 'intent_id', None)} intent_type={intent.type} sketch_info={sketch_info}"
            )
        proto = proto_hint
        proto_reason = f"proto_hint={proto_hint}"
    else:
        # auto 模式下按启发式选择（不再硬编码“BGP 优先”）
        proto, proto_reason = decide_proto(intent, sketch, force_proto="auto")
        
        # Check if the selected protocol has mechanisms for this intent type.
        # Some intent types are protocol-specific (e.g., ordered_preference is BGP-only,
        # path_migration is BGP-only). If the heuristic selected a protocol without
        # mechanisms, fall back to a protocol that has mechanisms.
        intent_mech_rules = MECH_RULES.get(intent.type, {})
        if not intent_mech_rules.get(proto):
            # No mechanisms for selected protocol, try to find an alternative
            available_protos_for_intent = [p for p in intent_mech_rules.keys() if p in protocols]
            if available_protos_for_intent:
                # Use the first available protocol that has mechanisms
                proto = available_protos_for_intent[0]
                proto_reason = f"auto: fallback to {proto} (intent '{intent.type}' has no mechanisms for originally selected protocol)"
    sketch_info["proto_reason"] = proto_reason

    raw_candidates = MECH_RULES.get(intent.type, {}).get(proto) or []
    candidates = list(raw_candidates)

    # static_route 仅在 OSPF-only/Stub 场景下作为兜底（避免在 BGP 可用时误选）
    if proto == "ospf":
        ospf_style = sketch.ospf_style or {}
        stub_like = bool(ospf_style.get("has_stub")) or ("bgp" not in protocols)
        if stub_like:
            static_candidates = MECH_RULES.get(intent.type, {}).get("static") or []
            if static_candidates:
                existing = candidates or []
                merged = list(existing)
                for m in static_candidates:
                    if m not in merged:
                        merged.append(m)
                candidates = merged
    # 若当前环境不具备 community 能力，则过滤掉社区类机制，避免生成不可落地的策略
    if proto == "bgp" and candidates:
        community_cap = _has_community_capability(sketch)
        def _is_community(mech: str) -> bool:
            return "community" in mech
        filtered = []
        for mech in candidates:
            if _is_community(mech) and not community_cap:
                continue  # 预留机制，当前不启用
            filtered.append(mech)
        candidates = filtered

    # 基于协议能力过滤机制，防止选择当前拓扑不支持的机制
    if candidates:
        candidates = [m for m in candidates if is_mechanism_supported(m, protocols)]

    supported_candidates = []
    if candidates:
        supported_candidates = [m for m in candidates if is_mechanism_supported(m, protocols)]
        candidates = supported_candidates
    if not candidates:
        sample = sorted(set(supported_candidates or raw_candidates))[:8]
        raise ValueError(
            f"No supported mechanisms for intent '{intent.type}' under protocols {protocols}. "
            f"intent_id={getattr(intent, 'intent_id', None)} "
            f"prefix={getattr(intent, 'prefix', None)} "
            f"raw_candidates_count={len(raw_candidates)} "
            f"supported_candidates_count={len(supported_candidates)} "
            f"supported_candidates_sample={sample} "
            f"sketch_info={sketch_info}"
        )

    best_mech = candidates[0]
    best_score = _score_mechanism(best_mech, sketch, device_caps)
    for mech in candidates[1:]:
        score = _score_mechanism(mech, sketch, device_caps)
        if score > best_score:
            best_mech = mech
            best_score = score
    return proto, best_mech
