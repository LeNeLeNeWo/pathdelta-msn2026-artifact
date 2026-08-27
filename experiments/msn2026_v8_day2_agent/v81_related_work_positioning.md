# Related-work positioning and novelty audit

## The closest systems solve different contracts

| System | Main task | Who decides the patch? | Where correctness specification comes from? | Difference from v8.1 |
|---|---|---|---|---|
| Original PathDelta | Brownfield intent-to-patch | Trusted planner fixes mechanism and APPEND/PREPEND/REBIND; LLM is a bounded renderer | Predefined intent family, symbolic planner, mechanism-aware oracle | v8.1 removes the strategy oracle: the LLM authors arbitrary exact edits, while PathDelta derives only an implementation-agnostic permission/invariant contract |
| LLM-NetCFG | NL intent to configuration and zero-touch deployment | LLM configuration agents | Intent-driven verification | Focuses generation/automation, not automatic inference of a least-authority Day-2 delta against shared legacy objects ([paper](https://arxiv.org/abs/2408.13298)) |
| INTA | Cross-vendor configuration translation | LLM incremental translator | Source intent plus syntax and LLM semantic verification | Translation seeks semantic equivalence across vendors; v8.1 seeks a new local delta while preserving the brownfield complement ([paper](https://arxiv.org/abs/2501.08760)) |
| Accessible Network Control | Natural-language control for non-experts | LLM maps NL to YANG IR; deterministic compilation follows | IR syntax, memory, and external feedback | Optimizes accessibility and multi-vendor interface; it does not infer external-dependency protections or semantic complement witnesses for arbitrary direct edits ([paper](https://arxiv.org/abs/2509.20600)) |
| Cornetto | Benchmark LLM repair of broken configurations | System under test | A known healthy `C_gold`; Batfish mines data-plane predicates, then faults are injected | Excellent evaluator, but assumes a golden healthy state/post-repair specification. v8.1 addresses a new Day-2 intent with no golden post-state and must infer what may change versus remain invariant ([paper](https://arxiv.org/abs/2604.22513)) |
| Agentic Configuration Repair | Diagnose and repair a broken network using retrieval and iterative formal verification | LLM Agent | Cornetto problem symptoms/specifications and Batfish feedback | Demonstrates retrieval + verifier feedback, but does not make intent-relative change-authority inference the core object. v8.1 adds coverage-directed witness discovery, shared-dependency protection, and patch-free counterexamples for an otherwise unconstrained patch author ([paper](https://arxiv.org/abs/2606.06212)) |

## Precise novelty claim

The contribution is not “we also connect an LLM to Batfish.” It is the automatic
construction of an **implementation-agnostic, intent-relative least-authority
contract** from a single brownfield pre-state and a requested new delta:

1. actively derive representative FEC witnesses from policy boundaries;
2. express the requested behavior as relational target obligations;
3. freeze every witnessed target-complement behavior;
4. protect policy nodes shared with external subjects, including transitive
   call/match dependencies;
5. authorize target-exclusive edits and bounded new objects without selecting a
   patch strategy; and
6. translate violations into evidence-only counterexamples so the same LLM can
   revise its own edit.

No expected patch, replacement object, APPEND/PREPEND/REBIND label, or trusted
renderer is exposed. This is the clean break from the original paper and the
reason the Agent remains substantive rather than ceremonial.

## Why Cornetto does not subsume this contribution

Cornetto formalizes repair as `(T, C_gold, C_broken, Φ)` and mines `Φ` from the
golden state. That is appropriate for injected fault repair. A Day-2 change has
no `C_gold` for the desired future: the system must permit a requested
difference while preserving everything outside it. Treating the whole pre-state
as golden would reject the intended change; checking only the new goal permits
regressions. The Change Envelope is the missing asymmetric specification:
`allowed target relation + preserved complement + protected dependencies`.

## Claim wording to avoid

- Do not claim that LLM correctness exceeds rules.
- Do not claim complete verification from a finite FEC set.
- Do not claim that dependency protection alone catches unobserved behavior.
- Do not claim that targeted challenges estimate production fault prevalence.
- Do not claim vendor generality until Batfish-backed equivalence classes and
  additional adapters are evaluated.
