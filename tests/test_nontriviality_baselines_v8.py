from experiments.msn2026_v8_day2_agent.nontriviality_baselines import classify


def test_preserve_plus_scope_is_explicit_strong_composite():
    row = {
        "accepted": {
            "V1_goal": True,
            "V2_write_scope": True,
            "V3_dependency": False,
            "V4_semantic_frame": True,
            "V5_full_envelope": False,
        }
    }
    result = classify(row)
    assert result["preserve_observed_plus_scope"] is True
    assert result["full_envelope"] is False


def test_composite_requires_both_components():
    row = {
        "accepted": {
            "V1_goal": True,
            "V2_write_scope": False,
            "V3_dependency": True,
            "V4_semantic_frame": True,
            "V5_full_envelope": False,
        }
    }
    assert classify(row)["preserve_observed_plus_scope"] is False
