# Formal experiment admission protocol

Version: 1.0, preregistered after the v8 development pilot and before any formal freeze. All six core gates are conjunctive. PathDelta need not have the highest absolute target-success rate, but it must satisfy the safety/utility thesis without data or baseline manipulation.

## Data-quality gate (prerequisite)

- Candidate labels receive two independent blinded audits; raw agreement and Cohen's kappa are reported, with kappa at least 0.80 for semantic acceptability and collateral change.
- Source/topology families are atomic across development/pilot/formal-test splits; all files, source commits, licenses, prompts, versions, and seeds are hashed.
- At least 25% of formal candidates are naturally generated LLM editing attempts or independently authored alternatives, not fault templates. No failed case is removed after inspection.
- FRR parser FAIL and backend N/A are reported separately. Unexpected parser warnings block the affected case rather than being interpreted as semantic failure.

## Gate 1 — Envelope utility

On goal-satisfying collateral candidates, V5 false-accept rate must be at least 30 percentage points lower than Goal-only, with a source/topology-cluster bootstrap 95% CI whose lower bound is above zero. The reduction must appear in at least three independent source/topology clusters. Safe-alternative false-reject rate must be at most 10%, and its cluster-bootstrap upper 95% bound at most 20%.

## Gate 2 — LLM editing utility

All main methods use the same frozen model, temperature, eligible pre-state, edit interface, attempt cap, and total token cap. On paired case/seed trials, Full feedback must reduce final observed collateral regression by at least 5 absolute percentage points or 50% relative to Goal-Verified (whichever threshold is smaller), with a paired cluster-bootstrap 95% CI excluding zero. Target success is evaluated with a non-inferiority margin of -10 percentage points. Syntax-only improvements do not count.

## Gate 3 — non-triviality

V5 must outperform static write scope, a target-complement “preserve all observed FECs” baseline, and a dependency-blacklist baseline on false accepts without materially worse safe false rejects. At least two independent collateral families must require different envelope dimensions. Removing either semantic-frame or dependency/footprint components must expose a distinct failure class. If a simple baseline explains the full effect within the CI, this gate fails.

## Gate 4 — generality

The qualifying effect must cover at least three public/generated source families, two topology families, four brownfield patterns, and two independently generated fault/candidate families. No single source/topology cluster may contribute more than 50% of the total false-accept reduction. BGP policy is sufficient; multi-protocol breadth is not required and cannot be claimed.

## Gate 5 — Agent freedom

At least 80% of evaluated intents must have two or more independently produced semantically safe implementations. V5 must accept at least 90% of those alternatives, including both object reuse and new local-object implementations where available. Style-nonconformant but safe patches must not fail only because of conformance. Trace audit must find zero trusted patch renderings or strategy disclosures.

## Gate 6 — verification evidence

At least 30 candidate transitions across three source/topology clusters must have executed FRR+Batfish+Rela evidence with audited FEC mapping and no more than 10% backend N/A. At least 12 stratified cases across three patterns must receive independent Kathara dynamic checks. All ECMP paths returned under the frozen limit are retained; trace-limit hits and destination-sampled coverage are reported. Agreement/disagreement and parser warnings are published.

## Frozen analysis if admitted

Freeze datasets/commits, candidate corpus, labels, splits, envelope code, prompts, `DEEPSEEK_*` backend/model, method budgets, FRR/Batfish/Rela/Kathara versions, and analysis code. Report source/topology-cluster bootstrap 95% CIs, paired tests, risk differences/ratios, effect sizes, per-pattern outcomes, failure taxonomy, tokens, calls, attempts, retries, and latency. A semantic bug creates a new freeze and reruns every affected method/case, never only failures.

## Pilot admission assessment

| Gate | Pilot status | Reason |
|---|---|---|
| Data quality | **FAIL** | One-author development labels; only 3/57 candidates are fresh direct-LLM; public-source cases lack candidate audit. |
| 1 Envelope utility | **PARTIAL** | Large development separation and zero safe false rejects, but mutation-heavy and not across independently labeled public clusters. |
| 2 LLM editing utility | **FAIL / INCONCLUSIVE** | Frozen paired candidate was safe under both contracts; no measured feedback benefit. |
| 3 Non-triviality | **FAIL** | Preserve-all and dependency-blacklist baselines not yet implemented; V5 perfect separation may reflect candidate taxonomy. |
| 4 Generality | **FAIL** | General inference runs on three public sources, but classification effects do not yet. |
| 5 Agent freedom | **PARTIAL** | Five safe classes accepted in development data, not independently authored for most intents. |
| 6 Verification | **FAIL** | One Batfish/Rela scenario; no new Kathara dynamic sample. |

**Admission decision: NO-GO for a formal freeze/run.** This is a process decision, not abandonment of the research direction.

