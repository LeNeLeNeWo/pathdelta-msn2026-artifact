# Reproducibility guide

## 1. Environment

Create an isolated Python environment and install the compact dependency set:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The offline audit and table reproduction use only the Python standard library.
`pybatfish` and containerized FRR/Kathara are needed only for their respective
evidence routes.

## 2. Integrity and anonymity

```bash
python3 scripts/verify_artifact.py
```

The checker validates `MANIFEST.sha256`, rejects credential-shaped content,
and rejects author names, private directories, home-directory paths, generated
caches, and environment files. It does not print environment-variable values.

## 3. Paper-number reconstruction

```bash
python3 scripts/reproduce_paper_numbers.py
```

The script reads, but does not rewrite, the following frozen records:

- `results/msn2026_v83_external/external_boundaries/analysis.json` for Table I;
- `results/msn2026_v83_external/agent128/analysis.json` for Table III;
- `results/msn2026_v83_external/cross_backend_audit.json` for Table II;
- `results/msn2026_v85_external_baselines/confirmatory/summary.json` for Table IV;
- `results/msn2026_v84_agent_repair/analysis.json` for developmental replay and holdout claims.

It emits a compact Markdown summary and asserts the counts stated in the
anonymous manuscript.

## 4. Fresh reconstruction of public inputs

The large public corpora are not vendored. Reconstruct them in a clean checkout:

```bash
python3 tools/prepare_msn2026_v83_sources.py
python3 tools/build_msn2026_v83_external_scenarios.py
python3 tools/prepare_msn2026_v85_sources.py
python3 tools/build_msn2026_v85_external_scenarios.py
```

Expected source revisions and tree hashes are in:

- `data/msn2026_v83_external/source_manifest.json`;
- `data/msn2026_v85_external_baselines/source_manifest.json`.

The original Cornetto public dataset is roughly 369 MB. Rebuilding therefore
requires network access and substantially more disk space than this repository.
No older PathDelta data or result directory is an input.

## 5. Registered public-corpus run

First verify the protocol freeze:

```bash
python3 experiments/msn2026_v83_external/freeze_protocol.py --check
```

Then inspect runner help before starting any paid or long-running job:

```bash
python3 experiments/msn2026_v83_external/run_external_boundaries.py --help
python3 experiments/msn2026_v83_external/run_agent128.py --help
```

For LLM calls, export only environment variables:

```bash
export DEEPSEEK_API_KEY='<secret>'
export DEEPSEEK_BASE_URL='https://api.deepseek.com/v1'
export DEEPSEEK_MODEL='<available-model-id>'
```

Never place credentials in a manifest. A newly available endpoint may differ
from the endpoint/model snapshot reported by the paper; treat such a run as a
new replication with a new manifest, not as the registered result.

## 6. Same-task workflow adaptations

The 40-case split and the excluded eight-case pilot are independently listed:

```text
data/msn2026_v85_external_baselines/splits/confirmatory40.json
data/msn2026_v85_external_baselines/splits/pilot8.json
```

Review method stages in `method_adapters.py` and `method_prompts.py`, then run:

```bash
python3 experiments/msn2026_v85_external_baselines/run_comparison.py --help
python3 experiments/msn2026_v85_external_baselines/analyze_comparison.py --help
```

These are author-built adaptations on a shared Day-2 task. They preserve the
principal stages of the cited workflows but are not native-system rankings.

## 7. Claim boundaries

- Zero unsafe release means zero **measured** unsafe release in the declared
  evidence model, not universal safety.
- FullR and the registered oracle share a finite attribute model. The separate
  Batfish audit is included precisely to expose non-overlapping model gaps.
- The v84 repair replay is developmental; the disjoint 32-case holdout is kept
  separate from registered v83 confirmation.
- Per-backend attempts are recorded, but the current artifact does not claim a
  fully aggregated heterogeneous-container wall-clock cost.
