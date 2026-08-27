from experiments.msn2026_v8_day2_agent.run_rq1_envelope import classify_candidate


class _Budget:
    allowed_devices = ("edge",)
    max_devices_touched = 1
    max_bindings_changed = 1
    max_new_objects = 2
    max_changed_lines = 12


class _Envelope:
    footprint_budget = _Budget()


def _report(frame=True, dependency=True, footprint=True):
    return {
        "structural": {"devices_touched": ["edge"], "bindings_changed": ["n1"], "new_objects_created": ["x"]},
        "textual": {"lines_touched": 8},
        "compliance": {
            "goal_success": True,
            "semantic_frame_preserved": frame,
            "dependency_frame_preserved": dependency,
            "hard_footprint_preserved": footprint,
        },
    }


def test_goal_only_accepts_collateral_that_semantic_frame_rejects():
    verdict = classify_candidate(syntax_status="PASS", report=_report(frame=False), envelope=_Envelope())
    assert verdict["V1_goal"]
    assert not verdict["V4_semantic_frame"]
    assert not verdict["V5_full_envelope"]


def test_safe_alternative_is_not_rejected_by_full_envelope():
    verdict = classify_candidate(syntax_status="PASS", report=_report(), envelope=_Envelope())
    assert all(verdict.values())


def test_na_syntax_is_not_silently_treated_as_pass():
    verdict = classify_candidate(syntax_status="N/A", report=_report(), envelope=_Envelope())
    assert not any(verdict.values())

