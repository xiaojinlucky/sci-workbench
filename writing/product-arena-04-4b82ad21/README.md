# Introduction & Related Work Drafting and Verification Skill (`introduction-related-work-writing`)

Offline, zero-dependency Python 3.12+ Skill for drafting and auditing academic Introduction and Related Work chapters.

## Core Capabilities (`scripts/intro_related_work_auditor.py`)

1. **Press-Release Four-Beat Opening Audit**: Verifies `Problem -> Gap -> Approach -> Key Finding` completeness and blocks hollow generic openers ("近年来受到广泛关注", "In recent years... attracted widespread attention").
2. **Dimension-Grouped Comparative Taxonomy & Chronological Stitching Detector**: Builds a structured comparative matrix grouped by method route/condition axis (`related_work_taxonomy.json`), flags chronological author-by-author laundry lists, and checks whether conflicting narrow-scope prior studies are explicitly reconciled.
3. **Unsubstantiated Gap / Baseless "First" Interceptor**: Blocks `UNSUBSTANTIATED_GAP_CLAIM` ("尚无研究", "首次", "first ever", "no prior work", "unprecedented") whenever cited limitations or applicability scope boundaries are missing.
4. **1:1 Contribution-to-Evidence & Citation Alignment**: Verifies every Introduction contribution bullet against supplied Methods, Figures, Tables, and Results (`OVERCLAIMED_CONTRIBUTION`), and audits citation coverage (`UNCOVERED_SUPPLIED_REFERENCES`, `UNVERIFIED_CITATION`).

## Quick Usage

```bash
python3 scripts/intro_related_work_auditor.py \
  --draft /input/intro_draft.md \
  --bib /input/literature.json \
  --evidence-dir /input \
  --case-id case-01 \
  --out-dir /app/output
```

## Runtime Requirements & Boundaries

- **Runtime**: Python 3.12 standard library only (zero third-party dependencies; offline Linux safe).
- **Outputs**: `related_work_taxonomy.json`, `gap_contribution_alignment.md`, `result.json` (plus drafted `introduction_and_related_work.md`).
- **Boundaries**: Citation and novelty verification are strictly scoped to supplied `/input/` files; missing bibliography entries are flagged honestly in `gaps` rather than invented.
