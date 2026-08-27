# MSN 2026 v8.4 agent availability and heterogeneous-evidence report

## Claim partition

This iteration never overwrites or relabels the frozen v8.3 result. The original
Agent128 result remains Full-R 49/128 verified completion, zero registered
unsafe release, and 79 fail-closed exhaustions. The v8.4 claims are partitioned:

1. **Development replay:** the 79 old Full-R exhaustions are a diagnostic corpus,
   not independent confirmation.
2. **Disjoint agent holdout:** 32 candidates are selected with seed 84032 from
   the 125 candidates unused by Agent128. Selection does not open labels or
   sealed oracles, but the pool shares the v8.3 public sources.
3. **Independent semantic audit:** Batfish evaluates final agent patches in an
   attribute universe that includes metric and origin, which the registered
   adapter omits.
4. **Adaptive union repair:** Batfish counterexamples are routed back to the
   frozen editor; a candidate counts only after a fresh independent Batfish
   re-audit. N/A always fails closed.

## Agent-side mechanism change

The old loop returned abstract failure messages and retained rejected text in
context. The frozen impact-aware controller instead provides:

- behavior-level `pre_change`, `candidate_observed`, and `required` relations;
- read-only ordered policy context and target execution traces;
- clean baseline-relative retries to remove failed-patch anchoring;
- prior semantic signatures rather than raw failed patches;
- explicit preservation of every non-authorized target attribute when an
  independent backend exposes a skipped legacy action.

The verifier never supplies a command, object name, insertion point, or
replacement patch. The LLM still chooses the object, policy ordering, patch
shape, and exact configuration commands. Repaired patches touch a median four
lines, P95 five, and maximum six.

## Results

### Agent128

| Stage | Verified completion | Unsafe release | Fail-closed |
|---|---:|---:|---:|
| Frozen v8.3 Full-R | 49/128 | 0 | 79 |
| Impact-aware Full-R development replay | 128/128 | 0 | 0 |
| Independent Full-U audit | 88/128 | 0 | 40 |
| Full-U after feedback and re-audit | **120/128** | **0** | **8** |

The initial Batfish audit finds 33 FAIL and 7 N/A among the 128 Full-R final
patches. Thirty-two FAILs are Junos target terms that bypass inherited metric
and origin actions; one is an Arista symbolic-class change. The already-frozen
union controller was frozen after one v8.4 smoke while 31/128 full-audit rows
had materialized and the remaining 97 were unavailable; the metric/origin class
was already known from v8.3. It produces 32 registered-compliant alternatives; all 32 pass a
fresh Batfish re-audit. One repair exhausts three semantic attempts. The seven
original N/A cases remain fail closed.

### Disjoint holdout32

| Stage | Verified completion | Unsafe release | Fail-closed |
|---|---:|---:|---:|
| Impact-aware Full-R | 32/32 | 0 | 0 |
| Independent Full-U audit | 22/32 | 0 | 10 |
| Full-U after feedback and re-audit | **31/32** | **0** | **1** |

All nine holdout Batfish FAILs are repaired without changing the union prompt or
parameters, and all nine pass independent re-audit. The single Batfish N/A
remains fail closed. This tests unseen-candidate generalization, not new-source
external validity.

### Cost

- The 79-case impact-aware replay uses 104 logical calls, 1.037M tokens, and
  5,099 s aggregate API latency; per-call P50/P95 is 48.3/81.7 s.
- Holdout32 uses 30 logical calls, 34 backend attempts, four retries, and 0.278M
  tokens after transport recovery.
- Agent128 heterogeneous repair uses 40 logical calls, 44 backend attempts,
  four retries, and 0.351M tokens.

These totals describe retained runs. Archived transport failures have no
complete response usage and are excluded, so operational cost during the
incident was higher than the reported model-usage total.

## Transport incident

Concurrent thinking-mode jobs triggered HTTP 551 EdgeOne resets and
`IncompleteRead`. The old wrapper incorrectly charged a missing response as a
semantic submission. Both jobs were stopped. Successful records were retained;
failed traces were archived; and only missing cases or traces containing an
explicit transport marker were resumed sequentially under a frozen recovery
protocol. No prompt, model, temperature, first candidate, verifier, or semantic
budget changed. Genuine semantic exhaustion was not rerun.

## Interpretation

The preregistered external boundary result remains a 10-point reduction versus
VerifierLoop, below the preregistered 15-point effect-size threshold. We do not
claim the preregistered magnitude. The narrower mechanism claim is supported:
projection verifiers release semantic collateral; explicit Change Envelopes
close those releases within their registered model; independent evidence finds
model omissions; and obligation-level feedback repairs most omissions without
disclosing a patch.

## Reproduction and integrity

Key artifacts:

- `agent_repair_v2_freeze.json`: frozen impact-aware development replay.
- `agent_holdout32_freeze.json`: disjoint label-independent holdout.
- `union_repair_freeze.json`: heterogeneous feedback controller.
- `transport_recovery_freeze.json`: transport-only recovery eligibility.
- `union_holdout_freeze.json`: unchanged union controller on holdout.
- `results/msn2026_v84_agent_repair/analysis.json`: consolidated analysis.
- `results/msn2026_v84_agent_repair/run_manifest.json`: sources, seeds, model,
  and SHA-256 hashes. Manifest SHA-256:
  `377f612c4a9f87f2e99ecd4fe40f633001744b70cc8ffa5e6801456e86f03dfa`.

The full regression suite passes 51/51 tests. Credentials are read only from
`DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL`; no credential is
written to a manifest or result file.
