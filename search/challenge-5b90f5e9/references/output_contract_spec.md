# Output Contract Spec (`challenge-5b90f5e9`)

- **Primary Deliverables**:
  - `/app/output/result.json` — Harbor runtime contract payload (`case_id`, `status`, `protocol_version`, `protocol_sha256`, `prisma_flow`, `dual_reviewer_summary`, `screening_decisions[]`, `arbitration_queue[]`, `fulltext_not_retrieved_queue[]`, `deduplication_log[]`, `near_duplicate_review_queue[]`, `criteria_drift_audit[]`, `metrics`, `metric_provenance`, `gaps[]`).
  - `/app/output/prisma_screening_ledger.json` — complete machine-auditable screening ledger.
  - `/app/output/prisma_screening_ledger.md` — human-readable PRISMA 2020 conservation diagram and arbitration ledger.

## Required Conservation Identities
1. `n_identified - n_dedup - n_automation_excl - n_awaiting_screen == n_screened`
2. `n_screened - n_screen_excl == n_sought`
3. `n_sought - n_not_retrieved == n_assessed`
4. `n_assessed - n_elig_excl - n_awaiting_elig == n_included`
5. `prisma_flow.balanced == true` before finalization.
6. Every canonical record appears exactly once in `screening_decisions[]` with a canonical `reason_code`, `triggered_clause`, and verbatim `evidence_quote`.
