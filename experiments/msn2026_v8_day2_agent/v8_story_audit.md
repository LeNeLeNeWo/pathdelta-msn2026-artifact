# PathDelta-Agent v8 story audit

Date: 2026-08-11  
Scope: `experiments/msn2026_v8_day2_agent/` and its fresh v8 development artifacts.  
Decision: keep the direction, but do not treat the current implementation as the paper mechanism or the current smoke runs as paper results.

## Frozen research boundary

The defensible research question is not whether an LLM can beat a complete rule system. It is whether an LLM configuration editor can be given an automatically inferred, intent-relative acceptance boundary that catches unintended Day-2 co-changes in a brownfield network without prescribing one correct patch.

The intended ownership split is:

| Responsibility | Owner |
|---|---|
| Interpret the supplied intent into a typed target relation | trusted envelope inference, with uncertainty exposed |
| Discover relevant configuration and dependencies | agent tools plus trusted read-only analysis |
| Choose configuration objects, edit locations, commands, and implementation strategy | LLM agent |
| Apply candidate edits | deterministic transaction runtime |
| Decide whether the target and inferred frame hold | verifier backends |
| Produce the next candidate after a counterexample | LLM agent |

## What the LLM genuinely decides today

In `agent.py`, the model chooses which inspection tool to call, search queries, how many inspection steps to take, the exact baseline-relative search-and-replace edits, object names, route-map clauses, sequence numbers, whether to create or reuse an object, and how to revise a rejected candidate. The runtime never invokes a patch synthesizer or deterministic renderer. The retained live smoke trace contains two distinct model-submitted patches and one model revision, which is evidence that this edit path executes end to end.

That is enough to call the current component a genuine configuration-editing agent. It is not enough to claim that the experiment gives the agent neutral implementation freedom, because the prompt and verifier still bias the solution.

## Where deterministic code currently leaks or narrows the answer

1. `Day2Intent` is a single hand-shaped intent family: one device, neighbor, prefix, and desired local preference. The target selector and relation are already supplied rather than inferred from a partial Day-2 intent.
2. `derive_change_envelope` assumes that the sole allowed semantic cell is `target_neighbor|target_prefix`; everything else in a manually supplied probe matrix is preserved. This is a useful frame prototype, but not a general target/complement algorithm.
3. The lightweight evaluator implements only inbound FRR route-map prefix-list matching and `set local-preference`. It omits route-map `call`, `continue`, communities, metric, sessions, cross-device dependencies, route selection, and convergence.
4. The probe prefixes are authored in `scenario.json`. Safety therefore depends on the completeness of a hand-selected observation set.
5. Protected objects are inferred using a narrow reference-count rule. The rule cannot yet compute a typed transitive dependency closure or distinguish read-only sharing from a semantically affected dependency.
6. Footprint budgets are hard-coded to 8/12 changed lines and 1/2 created objects depending on whether one target route-map is shared. These constants can reject valid implementations and encode a preference for the motivating local-fork shape.
7. `style_preserved` is included in the hard acceptance conjunction. This conflates safety with conformance and incorrectly rejects a functionally safe alternative.
8. The system prompt explicitly says to be careful with shared route-maps/prefix-lists and to prefer a local patch. Although it does not emit exact commands, it leaks the motivating solution class and makes the existing agent comparison unsuitable for a neutral benchmark.
9. Candidate fixtures in `run_pilot.py` and the v1 verifier were written together. They are valid unit examples, not independent benchmark evidence.
10. The Batfish-to-Rela runner calls the older v2 adapter. Reusing a core adapter is allowed, but its exact-path `replace_symbol` support and the handcrafted FEC list do not yet constitute a v8 general verification backend.

## Claims and current evidence status

| Candidate claim | Status | Evidence boundary |
|---|---|---|
| An LLM can directly edit a brownfield FRR configuration in the current runtime | **SUPPORTED (execution only)** | One live DeepSeek smoke: seven API calls, two submissions, one revision, accepted candidate; raw trace retained. |
| Trusted code does not synthesize or render the final patch | **SUPPORTED for current edit path** | Candidate edits originate in raw model responses and are only transactionally applied and checked. |
| Syntax, semantic-envelope, Batfish, and Rela checks can be placed in one pipeline | **SUPPORTED (integration smoke only)** | Fresh single-scenario Docker smoke, including a positive and collateral negative. |
| Goal-only checking can miss a target-correct collateral edit | **SUPPORTED as a motivating example** | One controlled shared-policy example; not a general or statistical result. |
| Textual minimality can prefer a semantically broader patch | **SUPPORTED as a motivating example** | Four-line shared mutation versus twelve-line local fork in one fixture. |
| The current envelope inference is general across Day-2 intents | **REJECTED** | Single typed intent family and narrow parser/evaluator. |
| Full Change Envelope reduces LLM semantic regressions | **UNTESTED** | One accepted trajectory and no paired same-model comparison. |
| The system accepts multiple distinct safe implementations | **UNTESTED/CONTRADICTED by v1 hard style gate** | Current fixture accepts only one candidate. |
| Batfish plus Rela provides an end-to-end formal safety proof | **REJECTED** | Concrete sampled paths, partial parser support, and no convergence/completeness proof. |
| Brownfield conformance reflects operator preference | **UNTESTED** | Only automatically inferred naming/sequence heuristics exist. |

## Required changes before an RQ1 pilot

- Replace the family-specific intent record with typed selectors and relations whose provenance and confidence are recorded.
- Compute the semantic frame as the complement of an explicit target set over an independently enumerated behavior universe; expose uncovered FECs.
- Build a typed dependency graph and transitive closure, while keeping dependencies as protection evidence rather than patch instructions.
- Derive footprint bounds from explicit scope and observed pre-state statistics; separate hard authorization from soft textual/structural preferences.
- Remove solution hints from the common agent prompt. Give every RQ2 method identical eligible configuration information and budgets.
- Separate candidate generation from envelope inference in code, authorship, and manifests; mix independent mutation, manual audit, alternatives, and LLM candidates.
- Treat parser warnings, incomplete FEC coverage, backend N/A, and backend FAIL as distinct first-class outcomes.

## Maximum novelty risk

The largest risk is that reviewers can explain the result as a static write-scope check plus “preserve all sampled prefixes,” with the remaining complexity hidden in intent-specific handwritten rules. A second risk is benchmark co-design: if unsafe mutations mirror the same dependency heuristic used by the envelope, high rejection recall will be tautological. A third is overclaiming verification: Batfish differential analysis and Rela frame relations already exist; the possible contribution is the automatic derivation and backend compilation of an intent-relative boundary, not either verifier or their mere composition.

The paper direction survives only if later evidence shows all three of the following: the general target/complement/dependency computation catches collateral classes beyond static scope, multiple independently produced safe implementations remain accepted, and the same LLM editor has fewer final semantic regressions under counterexample feedback than under goal/write-scope feedback.

## Audit verdict

Proceed with mechanism redesign and a fresh development benchmark. Preserve the existing v1 files and results only as historical smoke evidence. Do not use their candidates or outputs as v8 benchmark inputs, do not run a formal experiment yet, and do not use “safe” without qualifying the checked behavior universe and backend coverage.
