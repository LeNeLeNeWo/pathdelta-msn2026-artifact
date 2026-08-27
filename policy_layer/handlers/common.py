from __future__ import annotations

from typing import List, Optional

from intent_layer.schema import IntentCard
from policy_layer.mechanism_selector import select_mechanism
from policy_layer.models import PolicyEntry, PolicySketch
from policy_layer.normalizer import normalize_policy
from policy_layer.topology_view import Topology, resolve_affect_scope


def build_single_entry(
    intent: IntentCard,
    topology: Topology,
    sketch: PolicySketch,
    proto_hint: Optional[str] = None,
) -> List[PolicyEntry]:
    """
    Helper: select mechanism/proto -> resolve proto-aware scope -> normalize into a PolicyEntry list (single).
    sketch: PolicySketch object (typed, not raw dict).
    """
    proto, mech = select_mechanism(intent, topology, sketch, proto_hint=proto_hint)
    
    # Convert PolicySketch to dict for resolve_affect_scope
    sketch_dict = {
        "global": sketch.global_,
        "bgp_style": sketch.bgp_style,
        "ospf_style": sketch.ospf_style,
        "risk_profile": sketch.risk_profile,
        "roles": sketch.roles,
        "capabilities": sketch.capabilities,
        "existing_objects": sketch.existing_objects,
    }
    
    scope = resolve_affect_scope(intent, topology, proto=proto, sketch=sketch_dict)
    entry = normalize_policy(intent, scope, proto, mech, sketch)
    return [entry]
