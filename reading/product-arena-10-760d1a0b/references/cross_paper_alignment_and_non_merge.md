# Cross-Paper Alignment, Metadata Isolation & Explicit Non-Merge Rules

## 1. Strict Per-Paper Metadata Isolation

- Read `corpus-manifest.json`, `primary-material.json`, and `secondary-material.json` independently.
- Root `doi`, `title`, and `authors` belong exclusively to their own document — NEVER copy Paper 1's DOI/title/authors onto Paper 2.
- Multiple file formats (e.g., JSON + Markdown) sharing the same `source_id` and normalized paragraph text are deduplicated and NEVER counted as cross-paper evidence.
- When `secondary-publisher.xml` is provided, index its `<p id="...">` elements to flag `xml_cross_checked: true` for verifying linearized inline subscripts/formulas.

## 2. Within-Paper vs Cross-Paper Alignment

1. **Within-Paper Alias Resolution**: Explicit definitions of the form `Full Name (ABBR)` unify surface mentions inside that same paper while preserving `surface_forms` and `aliases`.
2. **Cross-Paper Named Standard Concept Linking (`LINK_CONCEPT_KEEP_SEPARATE_INSTANCES`)**:
   Only when two distinct papers reference the same named standard method, benchmark dataset, or domain task (e.g., `SciBERT`, `PubMedQA`, `DFT`) is a concept-level link emitted in `merged_concept_groups`. Both paper-scoped instance nodes remain separate so experimental conditions, hyperparameters, and metrics are NEVER pooled.
3. **Explicit Non-Merge (`DO_NOT_MERGE`)**:
   - Generic terms (`catalyst`, `model`, `method`, `patient cohort`, `efficiency`, `selectivity`) MUST emit `DO_NOT_MERGE`.
   - Same-spelling materials/genes/entities across papers without proof of identical physical sample, dataset split, or experimental condition MUST emit `DO_NOT_MERGE`.
   - Opposite-direction edges across papers or conditions are recorded under `conflicts` with `resolution_policy: "never_pool_conditions_preserve_both_papers"`.
