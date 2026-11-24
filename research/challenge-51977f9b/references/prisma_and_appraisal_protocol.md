# PRISMA 2020 Screening, Decoupled RoB Appraisal & Resumable Audit Protocol

## 1. Concept-Group Search Strategy & Resumable Export Manifest
- Construct search queries strictly as **OR within each PICO/PECO concept group, AND across distinct concept groups** (`(G1_intervention_or_exposure) AND (G2_population_or_object) AND (G3_outcome_anchor) NOT (exclusions)`), translated to database-specific syntax (`pubmed_europepmc` `[tiab]`, `openalex`, `arxiv`, `crossref`).
- Local offline query replay (`local_query_replay`) is explicitly bounded to `offline_local_subset_only` and records a resumable export manifest (`resumable_export_manifest`) so paginated API exports can be continued without double-counting.

## 2. Clause-Priority Screening Order & Human Dual-Review Queue
1. Year window (`EXC_OUT_OF_YEAR_WINDOW`)
2. Language restriction (`EXC_LANGUAGE_RESTRICTION`)
3. Excluded study type (`EXC_STUDY_TYPE`)
4. Pre-declared exclusion clauses (`EXC_EXCLUSION_CLAUSE`) — if a record also matches required concepts or falls in a low-confidence boundary, route it to `disagreement_and_human_review_queue` for independent dual-reviewer adjudication while retaining deterministic primary exclusion priority.
5. Missing required concept (`EXC_MISSING_REQUIRED_CONCEPT`)
6. Full-text retrieval check (`NOT_RETRIEVED_FULLTEXT_REQUIRED` when `fulltext_required: true`)
7. Full-text exclusion clauses (`EXC_FULLTEXT_EXCLUSION_TERM`)

## 3. Strictly Decoupled Methodological Risk of Bias (RoB 2 / ROBINS-I) vs. Evidence Access
**Hard Rule**: NEVER conflate missing full text (`evidence_access`) with methodological high risk of bias (`HIGH_RISK`). Missing full text is recorded on the orthogonal `evidence_access` axis (`FULLTEXT_VERIFIABLE` vs `UNASSESSABLE_NO_FULLTEXT`), while RoB domains remain `NOT_ASSESSABLE_FROM_ABSTRACT` or `NO_INFORMATION` until full text is retrieved.

| Methodological RoB Domain (RoB 2 / ROBINS-I aligned) | What Is Checked | Missing in Abstract | Missing in Full Text |
|---|---|---|---|
| D1 Study Design & Protocol | Explicit primary trial/observational design & pre-registration | `NO_INFORMATION` | `NO_INFORMATION` |
| D2 Sample & Inference Unit | Sample size / biological or analytical replicate unit reported | `NOT_ASSESSABLE_FROM_ABSTRACT` | `NO_INFORMATION` |
| D3 Comparator & Controls | Control group, placebo, or baseline/ablation comparator reported | `NOT_ASSESSABLE_FROM_ABSTRACT` | `NO_INFORMATION` |
| D4 Bias, Confounding & Transparency | Confounding adjustment, blinding, attrition, COI, or data/code disclosure | `NOT_ASSESSABLE_FROM_ABSTRACT` | `NO_INFORMATION` |

| Orthogonal Access Axis (`evidence_access`) | Meaning | Effect on Overall Tier |
|---|---|---|
| `FULLTEXT_VERIFIABLE` (`fulltext_pdf`/`fulltext_xml`/`fulltext_local_text`) | Full Methods/Results inspected | Eligible for `HIGH` / `MODERATE` / `LOW` methodological tier |
| `UNASSESSABLE_NO_FULLTEXT` (`abstract_only`/`secondary_note`) | Full Methods/Results unavailable locally | Overall tier MUST be `UNASSESSABLE_ABSTRACT_ONLY` (NEVER `HIGH_RISK` bias and NEVER `LOW_RISK`!) |

Never convert `NOT_ASSESSABLE_FROM_ABSTRACT` or `NO_INFORMATION` into `LOW_RISK`, and never convert missing full text into `HIGH_RISK` methodological bias.
