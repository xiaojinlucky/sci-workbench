# Screening Taxonomy, Ranking & Arbitration Rubric (`challenge-2dd1f64c`)

## 1. Two-Layer Classification System

Every unique candidate receives both an actionable **Screening Decision** and a granular **Relevance Tier** + **Evidence Role**:

| Decision | Relevance Tier | Operational Criteria |
| --- | --- | --- |
| `include` | `direct_relevant` | Passes hard filters/exclusion rules, has abstract evidence (`abstract_score > 0`), reaches dual-signal agreement (`label_a == label_b == include`), no reviewer conflict, and not a `title_only_trap`. |
| `borderline` | `indirect_relevant` | Partial facet overlap, dual-signal disagreement, external reviewer conflict, or `title_only_trap` downgraded for human inspection. |
| `borderline` | `insufficient_abstract_needs_review` | Abstract missing/empty (or key filter field missing) such that eligibility cannot be verified; protected from silent exclusion. |
| `exclude` | `irrelevant` | Hits explicit exclusion criterion, fails verified year/language window, or scores zero/minimal with complete abstract confirming off-topic scope. |

## 2. Deterministic 7-Level Tie-Breaker Chain

When two or more records achieve identical composite scores, ordering is resolved reproducibly in exact order:
1. `final_decision_order`: `include` < `borderline` < `exclude`
2. `relevance_tier_order`: `direct_relevant` < `indirect_relevant` < `insufficient_abstract_needs_review` < `irrelevant`
3. `composite_relevance_score` DESC (field-weighted BM25: `title=2.4`, `abstract=2.2`, `keywords/venue=1.8`)
4. `abstract_evidence_score` DESC (prevents title-only keyword stuffing from outranking abstract-verified studies)
5. `version_authority` DESC (`published` > `conference` > `preprint` > `unknown`)
6. `publication_year` DESC (missing year treated neutrally after evidence)
7. `deterministic_rid` ASC (stable lexicographical fallback)

Every tied record carries `"tie_detected": true` and its exact `"tie_break_key"` object so reviewers can audit why one paper preceded another.

## 3. Traceable Duplicate & Conflict Handling

- **Duplicate retention**: Following systematic-review best practices, duplicates are never deleted silently. Merged copies are recorded in `duplicate_trace_log` with `duplicate_status: "duplicate_ignored_with_trace"`, `canonical_rid`, `match_tier`, `match_score`, and `rollback_action`.
- **Distinct formal DOI guard**: Two papers with matching titles but distinct non-preprint DOIs are NEVER auto-merged; they are emitted into `ambiguous_candidates` for human arbitration.
- **External conflicting opinions**: Input files containing conflicting reviewer decisions (e.g., R1 `include`, R2 `exclude`) always override automatic `include` and hold the record in `human_review_queue.json` with both raw opinions intact.
