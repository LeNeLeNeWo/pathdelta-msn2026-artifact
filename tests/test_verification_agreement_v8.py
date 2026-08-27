import json
from pathlib import Path

from experiments.msn2026_v8_day2_agent.batfish_rela_runner import (
    ExtractedPathSet,
    FECMapping,
    agreement_record,
    audit_batfish_parse,
    canonical_ecmp_paths,
    compile_rela_payload,
    make_extracted_path_set,
    normalize_backend_status,
)


def test_ecmp_is_deduplicated_and_order_independent():
    assert canonical_ecmp_paths([["a", "c"], ["a", "b"], ["a", "c"]]) == (("a", "b"), ("a", "c"))


def test_envelope_target_and_complement_compile_to_rela_relations():
    target = FECMapping("t", "203.0.113.0/24", ("203.0.113.1",), "target", "target-1", "replace", {"kind": "replace_symbol", "from": "b", "to": "a"})
    control = FECMapping("c", "192.0.2.0/24", ("192.0.2.1",), "non_target", "frame-1", "preserve", None)
    pre = {
        "t": make_extracted_path_set(target, "pre", [["edge", "b"]], max_traces=32, trace_rows_returned=1, network="n", query="traceroute"),
        "c": make_extracted_path_set(control, "pre", [["edge", "b"]], max_traces=32, trace_rows_returned=1, network="n", query="traceroute"),
    }
    post = {
        "t": make_extracted_path_set(target, "post", [["edge", "a"]], max_traces=32, trace_rows_returned=1, network="n", query="traceroute"),
        "c": make_extracted_path_set(control, "post", [["edge", "b"]], max_traces=32, trace_rows_returned=1, network="n", query="traceroute"),
    }
    payload = compile_rela_payload("x", [target, control], pre, post)
    assert payload["fecs"][0]["obligation"]["relation"] == "replace"
    assert payload["fecs"][1]["obligation"]["relation"] == "preserve"
    assert payload["batfish_to_rela_provenance"][0]["coverage"]["pre"] == "bounded_destination_samples"


def test_na_is_distinct_from_fail_and_warning_audit_is_explicit():
    assert normalize_backend_status(False, None)["status"] == "N/A"
    assert normalize_backend_status(True, False)["status"] == "FAIL"
    audit = audit_batfish_parse([{"File_Name": "edge", "Status": "PARTIALLY_UNRECOGNIZED"}], [{"Text": "frr version 8.4"}], warning_allowlist=["frr version"])
    assert audit["status"] == "PASS"
    agreement = agreement_record(
        syntax={"status": "PASS"},
        reachability={"status": "PASS"},
        rela={"status": "FAIL"},
        dynamic={"status": "N/A"},
    )
    assert not agreement["all_executed_backends_pass"]
    assert agreement["has_na"]


def test_retained_smoke_demonstrates_reachability_path_relation_complementarity():
    path = Path(__file__).resolve().parents[1] / "results/msn2026_v8_batfish_rela_dev/batfish_rela_summary.json"
    if not path.exists():
        return
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["batfish"]["differential_reachability_rows"] == 0
    assert result["rela"]["result"]["passed"]
    assert result["rela"]["collateral_negative"]["batfish_differential_reachability_rows"] == 0
    assert not result["rela"]["collateral_negative"]["result"]["passed"]

