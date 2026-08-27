from experiments.msn2026_v8_day2_agent.build_semantic_minimality_example import build
from experiments.msn2026_v8_day2_agent.semantic_metrics import textual_footprint


def test_textual_replacement_is_not_double_counted():
    result = textual_footprint("a\nb\nc\n", "a\nx\nc\n")
    assert result.lines_modified == 1
    assert result.lines_added == 0
    assert result.lines_removed == 0


def test_short_shared_patch_has_larger_collateral_than_long_local_patch(tmp_path):
    payload = build(tmp_path)
    short = payload["candidates"]["patch_a_short_shared"]
    local = payload["candidates"]["patch_b_long_local"]
    assert short["textual"]["lines_touched"] < local["textual"]["lines_touched"]
    assert short["compliance"]["goal_success"]
    assert short["compliance"]["collateral_change"]
    assert not short["compliance"]["envelope_compliance"]
    assert local["compliance"]["goal_success"]
    assert not local["compliance"]["collateral_change"]
    assert local["compliance"]["envelope_compliance"]
