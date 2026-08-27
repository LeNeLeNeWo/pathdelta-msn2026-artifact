# Verification pipeline v2

PathDelta compiles an intent-relative Change Envelope into obligations for multiple existing backends. It does not claim to invent FRR, Batfish, Rela, or Kathara, and the composition is not an end-to-end formal safety proof.

FRR `vtysh -C` checks parser/config validity. Batfish builds immutable `C_old`/`C_new` snapshots and supplies parse warnings, route-policy/control-plane differences, RIB/session/reachability queries, and sampled FEC path sets. Rela never parses FRR and never proves BGP convergence: it receives only independently extracted canonical pre/post node sequences and verifies preserve/replace/add/remove relations. Kathara is an independent dynamic sample for a stratified subset.

## FEC and ECMP representation

Each FEC mapping records a prefix, one or more destination samples, target/non-target class, and envelope obligation ID. ECMP is a sorted set of complete node sequences, not one arbitrarily selected trace. Extraction records `max_traces`, whether the trace limit was reached, and a completeness label. Current concrete destination sampling is `bounded_destination_samples`; it must never be described as complete FEC coverage.

Every Batfish-to-Rela row includes network/snapshot/query/destination provenance and hashes of canonical path sets. Non-target mappings compile to `preState = postState`; target mappings compile to declared replace/add/remove relations. Unsupported relations are Rela `N/A`, not PASS. Empty parser results, backend unavailable, and unsupported queries are `N/A`; an executed violated obligation is FAIL.

## Parser warnings and complementary evidence

Batfish warnings are classified against a frozen allowlist. The current generated FRR smoke has only `frr version`/`frr defaults` warnings; unexpected warnings fail the parser-audit gate. Parser PASS still says nothing about routing correctness.

The retained positive changes the target path from peer-b to peer-a while preserving a control path. The collateral negative moves both paths. Batfish differential reachability has zero rows for both because reachability remains, whereas Rela rejects the non-target preserve relation. This establishes complementarity for a path-only example, not superiority or general completeness.

The verification claim is therefore limited to: **PathDelta compiles intent-relative obligations into multiple verifier backends and retains their evidence/coverage boundaries.** Terms such as “safe” must be qualified as bounded/verified over the extracted behaviors.

