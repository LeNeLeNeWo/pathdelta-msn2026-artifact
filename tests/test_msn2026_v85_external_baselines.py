from __future__ import annotations

import json

from experiments.msn2026_v8_day2_agent.change_envelope_v2 import BehaviorRecord
from experiments.msn2026_v83_external import run_agent128 as v83
from experiments.msn2026_v83_external.vendor_policy_adapter import behavior_rows
from experiments.msn2026_v85_external_baselines import method_adapters as adapters


BASELINE_TEXT = """hostname r1
!
ip prefix-list PL_ALL seq 5 permit 10.0.0.0/8 le 32
!
route-map RM_IN permit 10
 match ip address prefix-list PL_ALL
 set local-preference 100
!
router bgp 65000
 neighbor 192.0.2.1 remote-as 65001
 neighbor 192.0.2.1 route-map RM_IN in
!
"""

SAFE_OLD = "route-map RM_IN permit 10"
SAFE_NEW = """route-map RM_IN permit 5
 match ip address prefix-list PD_TARGET
 set local-preference 150
!
ip prefix-list PD_TARGET seq 5 permit 10.1.0.0/16
!
route-map RM_IN permit 10"""

UNSAFE_OLD = " set local-preference 100"
UNSAFE_NEW = " set local-preference 150"


class FakeModel:
    def __init__(self, values):
        self.values = list(values)
        self.max_calls = 12
        self.calls = 0

    def complete(self, _messages):
        self.calls += 1
        if not self.values:
            raise adapters.BudgetExhausted("fake output queue exhausted")
        value = self.values.pop(0)
        return value if isinstance(value, str) else json.dumps(value)

    def metrics(self):
        return {
            "provider": "fake",
            "backend": "unit_test",
            "base_url": "",
            "configured_model": "fake",
            "thinking_mode": "disabled",
            "logical_llm_calls": self.calls,
            "backend_attempts": self.calls,
            "retry_count": 0,
            "token_usage": {"prompt": 0, "completion": 0, "total": 0},
            "latency_ms": 0.0,
            "calls": [],
        }


def patch(old, new):
    return {
        "edits": [{"device": "r1", "old_text": old, "new_text": new}],
        "summary": "unit test",
    }


def fixture():
    baseline = {"r1": BASELINE_TEXT}
    metadata = {
        "device": "r1",
        "vendor": "cisco_ios",
        "subject": "192.0.2.1@in",
        "source_scenario": "unit",
    }
    intent = {
        "raw_text": (
            "On r1 set local preference 150 only for 10.1.0.0/16 received "
            "from 192.0.2.1; preserve unrelated behavior."
        ),
        "selector": {
            "devices": ["r1"],
            "subjects": ["192.0.2.1@in"],
            "fecs": ["10.1.0.0/16"],
            "dimensions": ["local_pref"],
        },
        "changes": [{
            "dimension": "local_pref",
            "relation": "replace",
            "desired": 150,
        }],
    }
    rows = behavior_rows(
        "r1",
        BASELINE_TEXT,
        "192.0.2.1@in",
        ["10.1.0.0/16", "10.2.0.0/16"],
        "unit_visible",
    )
    visible = [BehaviorRecord(**row) for row in rows]
    env = v83.make_envelope(intent, baseline, visible, "unit_visible")
    return metadata, intent, baseline, visible, env


def test_common_status_resolves_actual_binding_without_patch_hint():
    metadata, intent, baseline, _visible, _env = fixture()
    payload = adapters.common_payload(metadata, intent, baseline)
    status = payload["read_only_network_status"]
    context = status["bound_policy_context"]
    assert context["bound_policy"] == "RM_IN"
    assert context["direction"] == "in"
    assert status["pre_change_target_trace"]["result"]["local_pref"] == 100
    assert "expected patch" in status["provenance"]


def test_all_adapters_accept_a_safe_implementation():
    metadata, intent, baseline, visible, env = fixture()
    safe = patch(SAFE_OLD, SAFE_NEW)

    llm = adapters.run_llm_netcfg(
        FakeModel([
            {"type": "RP", "goal": "set target local preference", "ordered_steps": [], "facts_needed": []},
            safe,
        ]),
        metadata, intent, baseline, visible, env,
    )
    assert llm["accepted"]

    inta = adapters.run_inta(
        FakeModel([
            {"fragments": [], "retrieval_queries": []},
            safe,
            {"consistent": True, "issues": [], "refinement_instruction": ""},
        ]),
        metadata, intent, baseline, visible, env,
    )
    assert inta["accepted"]

    cosynth = adapters.run_cosynth(
        FakeModel([safe]),
        metadata, intent, baseline, visible, env,
    )
    assert cosynth["accepted"]

    cornetto = adapters.run_cornetto(
        FakeModel([
            {"thought": "inspect", "action": "inspect_config", "action_input": {"device": "r1"}},
            {
                "thought": "apply",
                "action": "apply_patch",
                "action_input": {"device": "r1", "old_text": SAFE_OLD, "new_text": SAFE_NEW},
            },
            {"thought": "verify", "action": "verify", "action_input": {}},
            {"thought": "submit", "action": "submit", "action_input": {}},
        ]),
        metadata, intent, baseline, visible, env,
    )
    assert cornetto["accepted"]
    assert cornetto["trace"][-1]["evaluation"]["contract_pass"]["verifier_loop"]

    pathdelta = adapters.run_pathdelta(
        FakeModel([safe]),
        metadata, intent, baseline, visible, env, [], env,
    )
    assert pathdelta["accepted"]


def test_missing_device_has_actionable_transaction_error():
    metadata, intent, baseline, visible, env = fixture()
    malformed = json.dumps({
        "edits": [{"old_text": SAFE_OLD, "new_text": SAFE_NEW}],
        "summary": "missing device",
    })
    result = adapters.evaluate_raw_patch(
        malformed, baseline, visible, env
    )
    error = result["evaluation"]["transaction_error"]
    assert "missing required keys" in error
    assert "structured_target.device" in error


def test_goal_only_can_accept_collateral_that_visible_loop_rejects():
    metadata, intent, baseline, visible, env = fixture()
    unsafe = patch(UNSAFE_OLD, UNSAFE_NEW)
    llm = adapters.run_llm_netcfg(
        FakeModel([
            {"type": "RP", "goal": "set target local preference", "ordered_steps": [], "facts_needed": []},
            unsafe,
        ]),
        metadata, intent, baseline, visible, env,
    )
    assert llm["accepted"]
    final = llm["trace"][-1]["evaluation"]
    assert final["contract_pass"]["goal_only"]
    assert not final["contract_pass"]["verifier_loop"]

    cosynth = adapters.run_cosynth(
        FakeModel([unsafe]),
        metadata, intent, baseline, visible, env,
    )
    assert not cosynth["accepted"]
