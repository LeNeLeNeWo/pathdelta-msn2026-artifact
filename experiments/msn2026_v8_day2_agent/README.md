# MSN 2026 v8 — PathDelta Day-2 Agent

This directory is a mechanism-development branch, not a paper-scale result set.
It tests one question: can an LLM agent directly edit a brownfield network while
PathDelta controls only the intent-relative change boundary?

The working paper direction is documented in
[`innovation_proposal.md`](innovation_proposal.md).

## What is implemented

- `change_envelope.py`: derives and checks a target semantic delta,
  non-target preservation frame, shared-object protection, local style, and
  footprint budget.
- `agent.py`: a real tool-using DeepSeek agent. The model chooses inspection
  tools and submits exact search-and-replace edits. The symbolic layer does not
  choose the patch mechanism.
- `run_pilot.py`: four controlled patches showing why goal-only verification
  and text-only minimization are insufficient.
- `run_agent_smoke.py`: one live agent smoke run using `DEEPSEEK_*` environment
  variables.
- `cross_validate.py`: real FRR `vtysh -C` cross-check through Docker.
- `run_batfish_rela_smoke.py`: Batfish dual-snapshot path extraction followed
  by Rela relational verification, including a collateral negative control.
- `change_envelope.schema.json`: development schema for the contract.

## Reproduce the small development pilot

From the project root:

```bash
python3 tools/build_msn2026_v8_day2_dev.py --seed 20260811
python3 experiments/msn2026_v8_day2_agent/run_pilot.py
python3 -m pytest -q tests/test_change_envelope_v8.py
```

For the live agent, run from an environment containing:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
```

Then run:

```bash
python experiments/msn2026_v8_day2_agent/run_agent_smoke.py --max-steps 10
python3 experiments/msn2026_v8_day2_agent/cross_validate.py
```

The independent Batfish-to-Rela integration smoke is:

```bash
python3 tools/build_msn2026_v8_batfish_rela_dev.py --seed 20260811
.venv-batfish/bin/python experiments/msn2026_v8_day2_agent/run_batfish_rela_smoke.py
```

See [`BATFISH_RELA_INTEGRATION.md`](BATFISH_RELA_INTEGRATION.md) for the proof
boundary, schemas, and paper-quality implementation plan.

## Current evidence boundary

The current data has one freshly generated scenario and the live run has one
model trajectory. It establishes only that the mechanism is executable:

- goal-only validation accepted all four controlled candidates;
- textual minimization selected the four-line shared-object mutation that
  caused collateral behavior;
- the full Change Envelope accepted only the local fork/rebind candidate;
- the live agent inspected dependencies and style, revised one rejected patch,
  and reached an accepted patch in seven model calls;
- FRR Docker accepted the final configuration syntax.

These observations must not be reported as general model performance or a
paper result.
# v8.1 continuation

The latest mechanism, corrected evidence, architecture, and novelty position are
documented in:

- `v81_evidence_report.md`
- `v81_architecture.md`
- `v81_related_work_positioning.md`
- `coverage_failure_and_fix.md`
- `v81_backend_roles.md`

The original v8 NO-GO is retained as an intermediate audit, not treated as the
terminal experiment outcome.
