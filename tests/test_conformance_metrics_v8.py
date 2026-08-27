from experiments.msn2026_v8_day2_agent.automatic_conformance_metrics import measure_conformance
from experiments.msn2026_v8_day2_agent.build_semantic_minimality_example import build


def test_conformance_metrics_are_proxy_and_do_not_emit_acceptance(tmp_path):
    build(tmp_path)
    before = {"edge-1": (tmp_path / "baseline.conf").read_text()}
    after = {"edge-1": (tmp_path / "patch_b_long_local.conf").read_text()}
    report = measure_conformance(before, after).to_dict()
    assert report["proxy_only"] is True
    assert "envelope_compliance" not in report
    assert report["devices_touched"] == 1
    assert report["new_objects"] == 2

