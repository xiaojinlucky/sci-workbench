---
name: intro-related-work-architect
description: Evidence-grounded Introduction and Related Work drafting and verification Skill . Enforces Press-Release four-beat opening (Problem -> Gap -> Approach -> Key Finding), dimension-grouped comparative Related Work taxonomy (anti-chronological-laundry-list), bounded gap derivation blocking unsubstantiated "first ever / no prior work" claims, and 1:1 contribution-to-evidence alignment with citation coverage checks.
---

# Introduction & Related Work Drafting and Verification (`introduction-related-work-writing`)

Use this Skill to draft, revise, and audit academic **Introduction** and **Related Work** chapters strictly grounded in supplied literature and the author's own Methods/Results/Figure/Table materials.

## Core Four-Pillar Quality Contract

1. **Press-Release Four-Beat Opening (立得住)**:
   - Open directly with `Problem -> Gap -> Approach -> Key Finding`.
   - **Ban hollow openers**: Never start with "近年来受到广泛关注" / "In recent years, ... has attracted widespread attention". Point immediately to the concrete scientific/technical constraint and cited baseline.
2. **Dimension-Grouped Comparative Related Work (分得清)**:
   - Organize prior studies along **shared comparison dimensions** (problem setting, method route, experimental/cohort condition, evaluation protocol, and trade-offs).
   - **Ban chronological summary stitching**: Never write disconnected year-by-year abstracts (`Author A (2019) did X. Author B (2020) did Y.`).
   - When supplied prior studies report **conflicting conclusions across narrow scopes**, explicitly contrast their applicability conditions rather than smoothing over the divergence.
3. **Bounded Gap Derivation — Zero Baseless "First" Claims (说得实)**:
   - Every research gap must be derived from specific limitations, uncovered regimes, or conflicting findings of **cited supplied literature**, AND specify **who cares / why the gap matters** within an explicit scope boundary.
   - **Interceptor rule (`UNSUBSTANTIATED_GAP_CLAIM`)**: Reject unsupported declarations such as "尚无研究", "首次", "填补空白", "first ever", "no prior work", or "unprecedented" unless strictly scoped and anchored to cited limitations. Never infer "novelty" merely from empty search results.
4. **1:1 Contribution-to-Evidence & Citation Alignment (对得上)**:
   - Every itemized contribution in the Introduction must map 1:1 to supplied Methods, Figures, Tables, and quantitative Results.
   - **Interceptor rule (`OVERCLAIMED_CONTRIBUTION`)**: Flag any contribution asserting untested datasets, missing Figure/Table pointers, or ungrounded metrics.
   - Verify every inline citation against supplied bibliography metadata and flag uncited key supplied works (`UNCOVERED_SUPPLIED_REFERENCES`) or unverified citations (`UNVERIFIED_CITATION`).

## Runtime Execution Workflow

### Step 1: Preflight, budget & input indexing
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "introduction-related-work-writing"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

### Step 2: Draft / Revise Introduction & Related Work from actual `/input/` files
1. Read `references/intro_related_work_protocol.md`, `references/academic_writing_standards.md`, and `references/output_contract_spec.md`.
2. Inspect every supplied literature card, manuscript excerpt, and Methods/Results/Table/Figure file under `/input/`. Record missing metadata or missing full-text evidence honestly as `[EVIDENCE_GAP: ...]`.
3. Draft `/app/output/introduction_and_related_work.md` following the four-beat opening, dimension-grouped comparative Related Work, bounded gap derivation, and verifiable 1:1 contributions.

### Step 3: Run deterministic taxonomy, gap, and contribution alignment auditor
```bash
python3 /skills/weekly-skill/scripts/intro_related_work_auditor.py \
  --draft /app/output/introduction_and_related_work.md \
  --corpus-dir /input \
  --evidence-dir /input \
  --case-id "<CASE_ID>" \
  --out-dir /app/output
```
(When auditing an existing user draft directly in `/input/`, pass `--input-dir /input --out-dir /app/output` first to locate every blocking defect before revising.)
- Resolve every `blocking` finding (`HOLLOW_GENERIC_OPENER`, `CHRONOLOGICAL_SUMMARY_STITCHING`, `UNRECONCILED_PRIOR_WORK_DIVERGENCE`, `UNSUBSTANTIATED_GAP_CLAIM`, `OVERCLAIMED_CONTRIBUTION`, `UNVERIFIED_CITATION`), then re-run the auditor until `blocking_count == 0` (or honestly record unresolvable input shortages in `gaps`).

### Step 4: Grounding gate & final Harbor audit
```bash
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output \
  --require related_work_taxonomy.json \
  --require gap_contribution_alignment.md
```

## Exception & Boundary Handling

- **Empty or incomplete bibliography**: Mark uncitable claims with `[CITATION_NEEDED]` and log `NO_BIBLIOGRAPHY_CORPUS_SUPPLIED` in `gaps`; never fabricate author names, years, or DOIs.
- **Abstract-only sources**: Explicitly restrict synthesis to reported abstract scope and note `abstract_only` in limitations rather than inventing full-text experimental details.
- **Unknown target journal citation style**: Preserve the citation style present in `/input/` (defaulting to author-year if unspecified) and declare `target_citation_style: UNSPECIFIED` in `limitations`.
