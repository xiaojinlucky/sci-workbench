# Question Decomposition, Sufficiency & Stopping Protocol (`challenge-e7598a44`)

## 1. Element Card Decomposition Rules

Before issuing any query, map the compound scientific question onto 7 explicit slots. Any slot not stated in the input question or case constraints MUST stay `"UNKNOWN"` (never filled from assumptions):

| Slot | What to Extract | Role in Sub-Question DAG |
| --- | --- | --- |
| `research_object` | Target molecule, catalyst, pathway, disease, architecture, or phenomenon | Anchors `SQ1` (prerequisite) |
| `mechanism_or_outcome` | Causal pathway, degradation mode, phenotype, or target readout | Generates `SQ2` (parallel after `SQ1`) |
| `method_or_intervention` | Experimental/computational method, assay, or model class | Generates `SQ3` (parallel after `SQ1`) |
| `comparator` | Explicit baseline, alternative (`X vs Y`, `X or Y`), or control arm | Generates `SQ4` (parallel after `SQ1`) |
| `population_or_context` | Species, patient cohort, cell type, medium, or operating condition | Generates `SQ5` (parallel after `SQ1`) |
| `time_scope` | Explicit year range or relative window anchored to snapshot reference year | Hard filter (missing years protected, not silently dropped) |
| `evidence_requirement` | Required study design (RCT, cohort, meta-analysis, benchmark, in vivo) | Generates `SQ6` (verification after `SQ1`) |

## 2. Controlled Two-Round Query Expansion

- **Round 1 (Core)**: Uses stemmed tokens directly extracted from each sub-question slot.
- **Term Gap Diagnosis**: Classifies every slot term into four states:
  1. `present_in_metadata`: Active postings in titles/abstracts/keywords.
  2. `metadata_blind_fulltext_only`: Zero metadata postings, non-zero postings in attached full-text documents (signals need for full-text anchor lookup).
  3. `zero_postings_acronym_expansion_found`: Zero postings in raw form, but corpus defines an acronym/expansion pair (e.g., `SCFA <-> short-chain fatty acids`).
  4. `zero_postings_absent_from_finite_snapshot`: Term absent from entire local snapshot; kept in query log and reported as explicit gap.
- **Round 2 (Controlled Expansion)**:
  - Expands ONLY corpus-grounded acronym pairs for all sub-questions.
  - Applies co-occurrence pseudo-relevance terms ONLY to core `SQ1` so strict comparator/evidence-type sub-questions (`SQ4`, `SQ5`, `SQ6`) do not suffer topic drift.

## 3. 5-State Sub-Question Sufficiency Rubric

| State | Exact Operational Criterion |
| --- | --- |
| `SUPPORTED` | >= 2 non-excluded, non-retracted deduplicated works matching sub-question terms (and dimension-specific terms for `SQ4`/`SQ5`/`SQ6`). |
| `PARTIAL` | Exactly 1 matching non-excluded work in finite snapshot (insufficient for corroborated synthesis). |
| `REFUTED` | Assigned during host semantic check when retrieved primary studies directly contradict the question premise with explicit evidence anchors. |
| `OUTDATED` | All matching works in the snapshot are retracted or superseded without valid replacements. |
| `UNKNOWN` | 0 matching non-excluded works in finite snapshot, or unresolved known-item anchor. |

## 4. Three-Perspective Balance Check

To avoid confirmation bias or cherry-picking, retained works are tagged across three complementary perspectives:
- `method_developer`: Original algorithm, synthesis, catalyst/assay design, or benchmark proposal.
- `empirical_application`: Clinical, animal, cellular, or real-device experimental validation.
- `independent_evaluator`: Systematic reviews, meta-analyses, comparative benchmarks, or negative/limitation reports.

## 5. Auditable Stopping Verdict Matrix

| Decision | Trigger Conditions | Required Next Action |
| --- | --- | --- |
| `STOP_SUFFICIENT_CONVERGED` | 0 `UNKNOWN`, 0 `PARTIAL`, 0 `source_load_errors`, 0 conflicts, Round 2 marginal hits = 0 | Deliver complete evidence bundle. |
| `STOP_DIMINISHING_RETURNS` | 0 `UNKNOWN`, 0 `source_load_errors`, 0 conflicts, Round 2 marginal hits = 0, >=1 `PARTIAL` | Deliver with explicit partial-coverage disclosure. |
| `STOP_FINITE_SNAPSHOT_COMPLETE` | 0 `UNKNOWN`, 0 `source_load_errors`, 0 conflicts, Round 2 marginal hits > 0 | Deliver; note snapshot boundary exhausted. |
| `STOP_WITH_GAPS_ESCALATE_HUMAN` | Any `UNKNOWN` sub-question OR `source_load_errors > 0` OR escalated reviewer/identity conflict | Deliver partial grounded bundle + explicit escalation list. |
