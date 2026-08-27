# Backend roles in PathDelta-Agent

The Change Envelope is the backend-neutral contract. Verification backends are
selected by behavior dimension rather than treated as interchangeable badges:

- **FRR `vtysh -C`** checks configuration parsing and command support.
- **Batfish route-policy analysis** checks symbolic differences in prefix,
  community, AS-path, metric, and local-preference transformations. In v8.1 it
  independently tests whether a protected shared consumer changed.
- **Rela** checks relational path obligations over old/new snapshots: target
  path replacement/addition/removal and preservation of non-target path sets.
- **Kathara + FRR** checks converged control-plane behavior on a small dynamic
  subset, including attributes of routes learned from each neighbor.

No backend alone proves the whole envelope. A syntax pass does not imply
semantic safety; Batfish policy equivalence does not prove convergence; Rela
does not validate vendor syntax; and a finite dynamic lab cannot establish
universal preservation. Their evidence is composed at the obligation level.
