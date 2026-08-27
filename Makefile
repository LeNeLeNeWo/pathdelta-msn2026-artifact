.PHONY: audit numbers test check

audit:
	python3 scripts/verify_artifact.py

numbers:
	python3 scripts/reproduce_paper_numbers.py

test:
	python3 -m pytest -q tests/test_change_envelope_v2.py tests/test_counterexample_feedback_v8.py tests/test_llm_metrics_v8.py tests/test_msn2026_v85_external_baselines.py

check: audit numbers test
