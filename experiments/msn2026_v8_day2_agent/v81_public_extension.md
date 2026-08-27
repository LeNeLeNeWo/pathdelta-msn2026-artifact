# Fresh public-source-conditioned extension

The v8.1 mechanism challenge is extended over six newly regenerated pre-states
conditioned on pinned FRRouting, Kathara-Labs, and containerlab sources. Source
files are downloaded again into `data/msn2026_v81_public_brownfield/`; no old
CSV, result, benchmark verdict, or candidate patch is reused.

Each source-conditioned case freezes two candidate mutation classes before
evaluation: a target-local fork that preserves shared objects, and an edit of a
shared route-map, shared prefix-list, or shared called policy whose collateral
appears only in an oracle-held FEC. The extension measures mechanism coverage
over different dependency shapes. It remains explicitly distinct from a claim
about the prevalence of these hazards in production configurations.
