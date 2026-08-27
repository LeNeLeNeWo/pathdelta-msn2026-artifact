# MSN 2026 v8.5: external-method comparison

This directory evaluates method-faithful adaptations of prior LLM network
configuration systems on PathDelta's brownfield Day-2 task. It does **not**
claim bit-for-bit reproduction of each paper on its original task.

Compared methods:

1. `llm_netcfg_adapted`: classification, low-level translation,
   configuration, syntax/goal verification, and repair, following LLM-NetCFG.
2. `inta_adapted`: intent-fragment extraction, vendor evidence retrieval,
   incremental generation, syntax guidance, and LLM semantic refinement,
   following INTA.
3. `cosynth_vpp_adapted`: modular syntax and semantic verifiers with localized
   verifier feedback, following Verified Prompt Programming / CoSynth.
4. `cornetto_agentic_adapted`: a ReAct search-and-replace agent with config,
   specification, verify, rollback, and submit tools, following Cornetto's MIT
   implementation.
5. `pathdelta_fullr`: the impact-aware LLM editor guarded by the registered
   coverage-directed Change Envelope.

The first four names always retain the `_adapted` suffix. INTA's original task
is cross-vendor translation, LLM-NetCFG's examples are greenfield
intent-to-config, CoSynth was manually simulated rather than built, and
Cornetto repairs injected faults. The adaptations preserve their published
mechanism while changing the task and edit interface so that every method is
evaluated on exactly the same Day-2 cases.

Fresh inputs live under `data/msn2026_v85_external_baselines/`; no legacy CSV,
candidate, label, result, or benchmark output is an input. Earlier v8.3 scenario metadata is consulted only to create a negative
exclusion list for exact source configurations. All 48 source configuration
files are disjoint from controller development; 46 are also topology-disjoint,
while two topology-overlap cases retain rare natural dependency families.

Run order:

1. `tools/prepare_msn2026_v85_sources.py`
2. `tools/build_msn2026_v85_external_scenarios.py`
3. `freeze_suite.py --stage split`
4. run the eight-case pilot and inspect only pilot results
5. freeze prompts with `freeze_suite.py --stage confirmatory`
6. run the forty-case confirmatory split
7. post-hoc oracle scoring, paired statistics, and independent Batfish audit

Do not pool pilot and confirmatory outcomes in the primary table.
