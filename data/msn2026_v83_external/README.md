# MSN 2026 v8.3 external validation data

This tree is a fresh, review-driven validation corpus. It does not consume any
candidate, label, CSV, result, or benchmark artifact from v2-v8.2. Earlier
artifacts are consulted only for code interfaces and schemas.

The public inputs are downloaded again and pinned in `source_manifest.json`.
Every derived scenario records its source repository, commit, original path,
seed, generation prompt/version, and SHA-256 hashes. Oracle observations and
candidate labels are sealed separately from the evaluated contracts.

Planned source groups:

- Cornetto public Cisco IOS configurations;
- Batfish official example networks, including multi-vendor configurations;
- independently generated adversarial candidates from a red-team prompt that
  does not describe Change Envelope internals.

