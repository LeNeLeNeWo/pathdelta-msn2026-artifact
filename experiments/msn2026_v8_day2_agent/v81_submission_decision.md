# v8.1 submission decision

## Decision: GO for the revised mechanism story, not for broad efficacy claims

The evidence now distinguishes the proposed mechanism from strong alternatives,
retains negative results, includes a same-first-candidate causal pilot, and has
independent FRR/Batfish/Kathara checks. The paper can credibly pivot from the
old “trusted planner + neural renderer” story to:

> **Coverage-Directed Change Envelopes for Safe LLM-Authored Brownfield Network
> Changes.** Let the Agent choose and edit the configuration; automatically
> infer the least-authority semantic and dependency boundary within which that
> edit may be released.

The current evidence is sufficient to motivate and support the mechanism and
its nontriviality on targeted challenges. Before a final camera-ready empirical
claim, the following remain required:

1. replace the development FEC evaluator with Batfish symbolic equivalence
   classes and measure class coverage;
2. obtain an independently authored, blinded challenge freeze;
3. enlarge the same-first-candidate pilot and report confidence intervals;
4. add multi-vendor dependency adapters or explicitly scope the paper to FRR;
5. run operator review for minimality/conformance claims; and
6. run Rela on a larger path-relation subset and dynamic labs on more than one
   dependency family.

The old formal NO-GO remains historically correct for v8.0 evidence. It is
superseded, not deleted, by the v8.1 mechanism evidence and narrower claim.
