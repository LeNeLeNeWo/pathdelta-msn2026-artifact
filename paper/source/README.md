# PathDelta MSN 2026 anonymous submission

This directory contains the anonymous Regular Paper manuscript built with the
official MSN 2026 IEEE conference template.

## Build in WSL

```bash
make
```

The final build input is `main.tex`; the registered v8.3 evaluation is kept in
`sections/evaluation_v83.tex`.  Both paper figures have editable draw.io sources
and PDF/SVG exports.  The paper consumes no legacy data or benchmark CSV.
Aggregate records and source manifests are published one directory above this
source tree under `results/` and `data/`; the full artifact hash list is
`MANIFEST.sha256`.

## Canonical directory layout

- `main.tex`, `references.bib`: manuscript sources.
- `figures/`: two vector figures and their editable draw.io sources.
- `sections/`: the frozen external evaluation section.
- `scripts/`: figure export and independently checked packaging scripts.
- `main.tex`, `references.bib`: manuscript sources.
- `figures/`: editable draw.io sources and vector exports.
- `sections/`: the frozen external evaluation section.

## Submission constraints

- IEEE Computer Society proceedings format, 10-point, US Letter, two columns.
- Double-blind anonymous review.
- Eight pages including references and appendices.
- No acknowledgments, author identities, private repository URLs, or machine
  paths in the PDF.

`scripts/package_submission.py` creates the canonical anonymous PDF and source
ZIP, compiles the ZIP in a fresh temporary directory, compares extracted text
with the canonical build, checks page size/count, embedded fonts, unresolved
references, overfull boxes, draw.io roots, stale title language, and private
paths, then issues the hash manifest.

See `TEMPLATE_SOURCE.md` for template provenance and hash.
