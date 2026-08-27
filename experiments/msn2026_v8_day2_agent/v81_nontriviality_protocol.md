# v8.1 nontriviality protocol

The v8 development benchmark was unable to distinguish Full Change Envelope
from the strong composite baseline “preserve all observed complement behavior
+ static write scope.”  v8.1 therefore freezes two mechanism-level challenge
families before evaluation:

1. **Latent shared dependency.** The visible FEC slice omits a dormant route.
   A candidate changes a shared policy, satisfies the target, and preserves the
   entire visible complement, but independently declared held-out behavior
   changes. Dependency protection should reject it without seeing the held-out
   route.
2. **Target-exclusive in-place edit.** A policy object has no external
   dependent. A value-only in-place edit is safe. An intent-relative envelope
   should accept it, while a conservative freeze of the entire target closure
   should reject it.

Every method receives the same baseline, intent, and visible observations.
Held-out pre/post observations are used only to compute the safety label after
the candidate verdict. Candidates are concrete, predeclared configuration
mutations and are not synthesized from envelope output. This is a targeted
development challenge, not yet a prevalence estimate over production networks.
