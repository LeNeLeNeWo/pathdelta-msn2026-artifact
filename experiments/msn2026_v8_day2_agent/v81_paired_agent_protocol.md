# Same-first-candidate paired Agent protocol

The causal unit is one scenario and one common LLM trajectory. Both contracts
share generation and any syntax/goal repair until the first candidate that
passes FRR syntax and the requested target relation. That candidate is hashed
and replayed without resampling:

- Goal-only accepts and stops.
- Full Change Envelope checks the identical candidate. If rejected, it emits
  one structured, patch-free counterexample and permits one model revision.

The held-out/dormant behavior slice is disclosed to neither the model nor either
acceptance contract. It is consulted only after each arm has stopped to label
latent collateral. The design therefore isolates envelope evidence rather than
different initial samples, different retrieved contexts, or an embedded patch
renderer. This remains a small pilot until repeated over a larger frozen corpus.
