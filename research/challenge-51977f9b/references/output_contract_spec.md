# Output Contract Specification (`systematic-review-pipeline`)

## Deliverables
- `/app/output/result.json`
- `/app/output/systematic_review_bundle.json`
- `/app/output/systematic_review_report.md`

## Required Fields in `result.json`
- `case_id`, `status`, `artifacts`, `sources`, `protocol` (with `protocol_sha256` and `search_strategy`), `prisma_flow` (with `balanced`, `identified_by_source`, `stage1_exclusion_reason_counts`, `fulltext_exclusion_reason_counts`, `not_retrieved_record_ids`), `screening_ledger[]`, `dual_reviewer_disagreement_queue[]`, `included_study_appraisals[]`, `opposing_evidence[]`, `sensitivity_impact_summary`, `uncertainty`, `metrics`, `metric_provenance`, `findings`, `gaps`.

## PRISMA 2020 Item 16a Conservation Rules
- `n_identified == n_dedup + n_screened`
- `n_sought == n_screened - n_screen_excl`
- `n_assessed_for_eligibility == n_sought - n_not_retrieved`
- `n_assessed_for_eligibility == n_elig_excl + n_awaiting_elig + n_included`
- `sum(fulltext_exclusion_reason_counts.values()) == n_elig_excl`
- Missing full text under `protocol.fulltext_required: true` goes strictly to `n_not_retrieved`, never to `n_elig_excl` or `n_included`.
