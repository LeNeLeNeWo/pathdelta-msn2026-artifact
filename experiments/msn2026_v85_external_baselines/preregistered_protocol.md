# External-method comparison protocol (v8.5)

## Question

On previously unseen brownfield Day-2 routing-policy changes, does a
coverage-directed Change Envelope reduce release of goal-achieving but
collateral-inducing LLM patches relative to published LLM configuration
workflows, without rejecting most safe implementations?

## Status and terminology

This is a same-task, same-model comparison of **method-faithful adaptations**.
It is not a reproduction of the papers' headline results because the original
tasks, devices, datasets, and implementation artifacts are not interchangeable
with the Day-2 task. The paper must not write "PathDelta outperforms
INTA/Cornetto" without the qualifier "adapted to our Day-2 task".

## Data separation

- Re-download public sources into `data/msn2026_v85_external_baselines`.
- Select 48 Cornetto configurations whose exact source files were not used in
  the v8.3 controller-development corpus. The frozen set contains 46
  topology-disjoint cases plus two configuration-disjoint cases needed to
  retain rare natural dependency patterns.
- Split before any method execution: 8 pilot and 40 confirmatory cases.
- Selection may use source path, vendor, and dependency-family metadata only.
  It may not use a patch, oracle label, prior result, or verifier outcome.
- Complete active observations remain sealed until post-hoc scoring.

## Common controls

- Backend/model: `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, and
  `DEEPSEEK_API_KEY`.
- Temperature: 0.0 for the primary paired run.
- Per call maximum completion: 5,000 tokens.
- Per case maximum: 12 logical LLM calls and 40,000 completion tokens.
- Same case order, immutable baseline, edit transaction rules, syntax parser,
  and post-hoc oracle for every method.
- Methods may stop before the common ceiling. Unused budget is not reallocated.
- Cornetto's published 30-step limit is evaluated as a secondary sensitivity
  analysis on cases where the common-budget adaptation exhausts.

## Information boundary

All methods receive the natural-language intent, target device, vendor,
baseline configuration, and the same candidate-independent read-only network
status: actual neighbor-policy binding, ordered terms, resolved referenced
objects, and the pre-change target trace. This prevents legacy object names
from creating an artificial parsing advantage; the status contains no expected
patch, object name, command, or insertion point. LLM-NetCFG and INTA otherwise
receive only the evidence their published stages request. CoSynth and Cornetto
verify the target plus the operator-visible passive specification set. They do
not receive sealed active witnesses. PathDelta starts with the same visible
inputs; its own candidate-independent coverage procedure constructs active
policy-class witnesses and freezes them before candidate evaluation.

When PathDelta rejects a candidate, its feedback contains failed relations
and candidate-derived execution evidence (evaluated terms, match outcomes, and
the applied terminal action). This evidence explains the observed failure but
does not prescribe a new object, command, sequence position, or patch. The
complete finite oracle is used only after a method terminates. Oracle outcomes
are never included in prompts or repair feedback.

## Primary outcomes

- `unsafe_release`: a released/submitted candidate is unsafe under the sealed
  oracle;
- `verified_completion`: a released/submitted candidate achieves the target
  and has no measured collateral;
- `failed_intent_release`: a released candidate does not achieve the target;
- `attempt_exhaustion`: no release within the frozen budget;
- final goal success and collateral regression, regardless of release.

Secondary outcomes are logical calls, backend attempts, retries, tokens,
latency, patch lines/objects touched, verifier calls, and repair transitions.

## Statistics

- Report numerators and denominators with Wilson 95% intervals.
- Use paired exact McNemar tests for unsafe release and verified completion;
  report paired risk differences with paired bootstrap 95% intervals.
- Holm-correct PathDelta's four primary unsafe-release comparisons.
- Stratify descriptively by dependency family and configuration size.
- Do not select the headline baseline or metric after seeing confirmatory
  labels.

## Claim gates

The result supports the narrow mechanism claim only if:

1. PathDelta has fewer unsafe releases than at least three adapted methods and
   zero or near-zero measured unsafe release overall;
2. verified completion is no more than 10 percentage points below the best safe
   adapted method; and
3. the reduction is not confined to one source configuration or dependency
   family.

Failure to meet a gate is reported as-is. No prompt, split, oracle, or method
definition may be changed after the confirmatory freeze.
