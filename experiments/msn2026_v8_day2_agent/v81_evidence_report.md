# PathDelta-Agent v8.1 evidence report

## Revised thesis

PathDelta-Agent is not a rule generator competing with an LLM. The LLM remains
the patch author. PathDelta infers an **intent-relative least-authority change
contract** for brownfield Day-2 operations. The contract combines:

1. coverage-directed semantic witnesses derived from observed behavior and
   configuration FEC boundaries;
2. a target delta describing what may change;
3. a complement frame describing what must remain invariant;
4. protection of external/shared dependency closure; and
5. a small structural footprint plus soft local-conformance preferences.

FRR, Batfish, Rela, and Kathara are evidence backends for different obligation
types. They do not generate the candidate patch.

## What the experiments now establish

The original 57-candidate development set was not sufficient: Full Envelope was
identical to the simpler “preserve observed complement + static scope” baseline.
This negative result is retained in
`results/msn2026_v8_nontriviality_dev/summary.json`.

The frozen v8.1 mechanism challenges contain 9 safe and 9 unsafe candidates:
3 fresh synthetic cases and 6 cases conditioned on newly re-downloaded pinned
FRRouting, Kathara-Labs, and containerlab sources.

| Contract | Safe accepted | Unsafe rejected | Interpretation |
|---|---:|---:|---|
| Preserve visible + scope | 9/9 | 1/9 | Misses latent shared-policy collateral |
| Freeze entire target closure | 0/9 | 9/9 | Safe but unusably conservative |
| Full Change Envelope | 9/9 | 9/9 | Separates local permission from shared protection |

This is a targeted discriminating challenge, not an estimate of fault
prevalence in production networks.

In six same-first-candidate paired Agent cases, both arms replay the identical
first LLM candidate; no arm-specific resampling is allowed. After correcting
two oracle-label errors and adding active FEC witnesses, all original model
submissions were replayed with **zero new API calls**:

- Goal-only accepted 2 unsafe candidates across the six cases.
- Full Envelope accepted 5 safe candidates, rejected one unsafe/unrepaired
  candidate, and accepted no unsafe candidate.
- The live runs used 8 logical calls, 8 backend attempts, zero retries, and
  26,816 total tokens. These are pilots, not inferential statistics.

## Independent backend evidence

- FRR `vtysh -C`: all 18 frozen challenge candidates parse successfully.
- Batfish `compareRoutePolicies`: six targeted checks pass, including detection
  of a value-only shared route-map edit and a shared-prefix-list effect on a
  second consumer. The parser normalizes these snippets through its CISCO_IOS
  grammar and only the FRR version/defaults metadata warnings are allowlisted.
- Kathara 3.8.0 + FRR 8.4.0: three converged labs show baseline `100/100`, unsafe
  shared edit `250/250`, and safe local fork `250/100` across the two peers.
- Rela remains the path-relation backend demonstrated by the v8 positive and
  collateral-negative integration smoke; it is not used to claim
  local-preference equivalence.

## Failure that changed the mechanism

An actual LLM candidate preserved every visible record and avoided shared
objects but silently denied an unobserved 10/8 route on the target neighbor.
Dependency protection could not catch it because the edited policy was
target-exclusive. The fix derives representative FECs from prefix-list
boundaries and actively evaluates them before envelope construction. This
failure is retained; it is the evidence for treating coverage as a first-class
contract property rather than claiming that the dependency graph is sufficient.
An exact-candidate, zero-API-call ablation accepts 2/2 safe candidates in both
modes, while passive-only coverage accepts the unsafe candidate and active
witness coverage rejects it (unsafe accepts 1 versus 0).

## Defensible claim and boundary

The current evidence supports the mechanism claim:

> On the frozen targeted challenges and six-case causal pilot, a
> coverage-directed, dependency-aware Change Envelope prevented latent
> brownfield collateral while still permitting safe local implementations; a
> visible-behavior baseline was too weak and a dependency freeze was too
> conservative.

It does **not** yet support production prevalence, broad vendor generalization,
human preference, or statistically powered end-to-end efficacy claims. Those
require a larger independently authored freeze, Batfish-backed symbolic FEC
classes, multi-vendor adapters, and human review.
