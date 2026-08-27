# Prompt 0–14 execution index

| Prompt | Status | Primary artifacts |
|---:|---|---|
| 0 | Completed | `v8_story_audit.md` |
| 1 | Completed | `change_envelope_v2.py`, `change_envelope.schema.json`, `envelope_inference.md`, v2 tests |
| 2 | Completed | `semantic_metrics.py`, `semantic_minimality.md`, `example_artifacts/semantic_minimality/` |
| 3 | Completed (development corpus) | `data/msn2026_v8_envelope_benchmark/benchmark_manifest.json`, `candidate_provenance.jsonl`, `ground_truth_protocol.md`, `leakage_audit.md` |
| 4 | Completed (development, not formal) | `rq1_envelope_results.md`, `results/msn2026_v8_rq1_dev/rq1_results.csv`, `failure_examples/` |
| 5 | Completed and live-smoked | `agent_benchmark.py`, `agent_methods.md`, `results/msn2026_v8_agent_methods_smoke_v2/` |
| 6 | Completed | `counterexample_feedback.py`, schema, `counterexample_loop.md`, tests |
| 7 | Completed at integration-smoke scope | `batfish_rela_runner.py`, `rela_adapter_v2.py`, `verification_pipeline_v2.md`, agreement tests |
| 8 | Completed at source-conditioned pre-state scope | `data/msn2026_v8_public_brownfield/`, public download/mutation builder and provenance |
| 9 | Completed as proxy protocol | `automatic_conformance_metrics.py`, `conformance_protocol.md`, `human_eval_package/` (unpopulated) |
| 10 | Completed | `v8_pilot_report.md`, `v8_pivot_decision.md`, `results/msn2026_v8_evidence_pilot/` |
| 11 | Completed | `formal_go_no_go_protocol.md`; admission decision NO-GO |
| 12 | Condition evaluated; formal run correctly not started | `formal_experiment_not_run.md` |
| 13 | Completed | `final_v8_reviewer_attack.md`, `final_v8_claim_evidence_map.md`, `final_v8_submission_decision.md` |
| 14 | Condition evaluated; outline correctly not generated because decision is NO-GO | `prompt14_disposition.md` |

All new experiment data were generated or downloaded afresh under v8 directories. No old PathDelta CSV/result/benchmark is a v8 input. Existing v8 smoke artifacts are clearly separated from development and pilot results; no artifact is labeled formal.

