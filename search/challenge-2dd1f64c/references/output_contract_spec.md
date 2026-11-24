# Output Contract Specification (`challenge-2dd1f64c`)

## Primary Deliverable: `/app/output/result.json`

`result.json` aligns with `/input/case.json:output_contract` and guarantees:
- `case_id`: Exact case identifier.
- `results`: Complete ranked list covering 100% of deduplicated candidates (`include`, `borderline`, `exclude`), each carrying `rank`, `rid`, `title`, `authors`, `year`, `venue`, `doi`, `version`, `label`, `relevance_tier`, `evidence_role`, `confidence`, `score`, `title_score`, `abstract_score`, `arbitration`, `title_only_trap`, `missing_fields`, `tie_detected`, `tie_break_key` (when tied), `rationale`, `abstract_snippet`, `reason_codes`, `n_merged_records`, `record_refs`, and `version_group`.
- `evidence`: `scoring_formula`, `tie_breaker_chain`, `rules_applied`, `per_record_evidence`, `duplicate_trace_log`, `ambiguous_candidates`, `human_review_queue`, `version_links`, `sources`.
- `status`: `finite_snapshot_only: true`, `live_remote_coverage_claimed: false`, `prisma_consistency`, `completed`, `not_completed`, `source_load_errors`, `conflicts`, `gaps`, `duplicates_merged`.
- `metrics`: Raw/unique/duplicate counts, `source_count_conserved`, include/borderline/exclude counts, `borderline_ratio`, `by_relevance_tier`, `by_evidence_role`, `missing_abstract_protected`, `title_only_traps_caught`, `conflicts_escalated`, `ties_resolved_deterministically`, `reviewer_agreement`, `cohen_kappa`, `rank_stability`, `prisma_flow`.
- `findings` & `artifacts`: Concise grounded summary claims and generated artifact paths.

## Companion Artifacts (All UTF-8, Zero `.tsv`)

- `screened_ranked.csv`: Full ranked screening spreadsheet.
- `screening_audit.json`: Complete scoring parameters, PRISMA flow, duplicate trace log, LOO stability, and per-record evidence.
- `human_review_queue.json`: Actionable queue for human conflict/missing-abstract/ambiguous-identity arbitration.
- `report.md`: Concise Markdown report.
- `audit_log.json` & `run_manifest.json`: Hash-chained audit trail and input file inventory.
