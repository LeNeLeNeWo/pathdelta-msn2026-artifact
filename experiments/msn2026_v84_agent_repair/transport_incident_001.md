# Transport incident 001

During concurrent holdout and heterogeneous-repair execution, the configured
OpenAI-compatible endpoint returned `HTTP 551: Connection Reset by EdgeOne`,
`IncompleteRead`, and related transport exceptions. The frozen controllers
incorrectly represented a failed logical call as a failed candidate evaluation,
thereby consuming the semantic submission budget even though no patch existed.

The incident was detected before either run completed. Both concurrent jobs
were terminated, successful case records were retained, and every failed record
was archived before replacement. Recovery is sequential. A whole case is rerun
only if its failed trace contains a transport marker; a genuine semantic
exhaustion is never selected. The original model, temperature, thinking mode,
prompt, verifier, first candidate, and semantic submission limits remain fixed.
Transport recovery rounds and archived hashes are recorded separately.
