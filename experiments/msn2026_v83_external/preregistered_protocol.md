# PathDelta v8.3 major-revision protocol

Status: draft until `protocol_freeze.json` is generated.

Protocol version: `msn2026-v83-1.0.0`

Data seed: `20260814`

Agent selection seed: `83127`

Coverage-removal seed: `83191`

## Immutable mechanism

The evaluated Change Envelope implementation is the v8.1 implementation used
by v8.2. Once this protocol is frozen, target, semantic-frame, dependency,
active-witness, footprint, and acceptance semantics cannot be changed in
response to v8.3 outcomes. Source adapters may translate vendor syntax into the
existing vendor-neutral behavior/dependency representation; adapter failures
are reported as N/A and never counted as PASS.

## E1: external adversarial challenge

- Mine 96 Day-2 scenarios from newly downloaded public configurations: 64 from
  Cornetto's public Cisco IOS corpus and 32 from Batfish official examples.
- A source file, policy object, or prefix may appear in only one split.
- Generate candidates with a red-team LLM prompt that sees the user intent and
  configuration but no Envelope internals, reference patch, oracle records, or
  protected-object list.
- Request three implementation modes per scenario: minimal edit, local fork,
  and adversarial target-achieving edit. Retain all parseable outputs and all
  failures; do not rebalance after observing method verdicts.
- An independent vendor-neutral evaluator labels target success and collateral
  from pre/post behavior. Labels are sealed before boundary evaluation.

Primary metrics: unsafe false acceptance and safe false rejection, with Wilson
95% intervals and paired exact tests. Results are reported by source, vendor,
dependency family, and candidate mode.

## E2: strong baselines

Compare on identical candidates:

1. `VerifierLoop`: full visible configuration, explicit target intent,
   Batfish goal/differential checks, and iterative patch-free counterexamples;
2. `OracleContract`: a complete manually materialized target/non-target
   contract supplied to the same agent, treated as an upper bound rather than a
   deployable automatic method;
3. `FullEnvelope`: the automatically compiled patch-independent boundary.

The claim under test is automatic derivation of the semantic authorization
boundary, not superiority over hand-written rules or novelty of Batfish.

## E3: paired Agent128

- 128 preregistered cases, balanced by source/vendor and dependency family.
- Direct, RAG, VerifierLoop, write-scope, goal-only, OracleContract, and Full.
- All compatible arms replay the exact same first candidate. Prompt-changing
  RAG is matched by scenario and seed but identified as non-causal.
- Up to five logical submissions for iterative methods; a ten-attempt
  sensitivity is secondary and cannot replace the main result.
- Record logical calls, backend attempts, transport retries, prompt/completion
  tokens, latency, API cost, revisions, footprint, unsafe release, verified
  completion, and reject-to-repair success.

## E4: component complementarity

Run Full and five leave-one-out variants: minus active witnesses, dependency
protection, semantic frame, footprint, and coverage provenance. Report unsafe
FA and safe FR overall and in the mutation family designed independently of the
component under test.

## E5: missing-coverage sensitivity

Remove 10%, 25%, and 50% of passive behavior records under both uniform-random
and adversarial class removal. For each condition report active-witness recovery,
discovered/reachable equivalence classes, unsafe FA, uncovered hard obligations,
N/A, and fail-closed rate. This is a degradation study, not a completeness
theorem.

## E6: applicability and fail-closed usability

For every candidate, emit an obligation-type x backend matrix with PASS, FAIL,
N/A, timeout, and error. Report at-least-one-applicable-backend coverage, hard
obligations without evidence, fail-closed releases, and recovery via fallback.
Analyze every failed Agent case and distinguish genuinely unsafe candidates,
budget rejection, unsupported syntax, insufficient feedback, and attempt
exhaustion.

## E7: scalability

Fresh synthetic scale-only inputs use objects 1e2/1e3/1e4, FECs
1e2/1e3/1e4/1e5, and devices/peers 10/50/100/500. Measure dependency
extraction, witness count/generation time, envelope compilation, Batfish query
time where applicable, total verifier time, and peak RSS. Dynamic emulation is
sampled rather than required at the largest points.

## Statistical policy

- Wilson 95% confidence intervals for proportions.
- Exact paired McNemar tests for identical-candidate comparisons.
- Paired bootstrap intervals for latency, token, and footprint differences.
- Holm correction within each experiment family.
- N/A, timeout, transport error, parser rejection, and truncation remain visible
  and follow the preregistered denominator rules.
- Confirmatory and exploratory results are never pooled.

## GO/NO-GO

The external-validation paper story is GO only if:

1. Full reduces unsafe acceptance by at least 15 percentage points versus
   write-scope and VerifierLoop on the external challenge;
2. Full unsafe FA is at most 5%, safe FR at most 15%, and the direction holds in
   both public source groups and at least two vendors;
3. OracleContract is not materially safer than Full by more than 5 percentage
   points, or the gap is explicitly reported as the cost of automatic contract
   inference;
4. coverage removal yields a monotone, explained degradation or fail-closed
   response rather than silent unsupported PASS.

If a gate fails, the mechanism is not tuned after unblinding. The paper narrows
its claim to the supported evidence regime and reports the failure.

