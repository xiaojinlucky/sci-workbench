# Output Contract Spec (`challenge-bace1c46`)

- **Primary Deliverables**:
  - `/app/output/result.json` — Harbor runtime contract payload (`case_id`, `status`, `scales_applied`, `overall_tier_distribution`, `appraisals[]`, `metrics`, `metric_provenance`, `gaps[]`).
  - `/app/output/quality_appraisal_table.json` — full version-pinned appraisal ledger with sentence-level anchors and tier derivation traces.
  - `/app/output/quality_appraisal_report.md` — human-readable traffic-light matrix synchronized 1:1 with the JSON domain ratings and itemized evidence quotes.

## Verification Invariants
1. Every appraisal entry declares `study_type_detected`, `scale_name`, `scale_version`, `material_tier`, `overall_quality_tier`, and `tier_derivation_rule`.
2. Every domain rating not equal to `insufficient_evidence` / `UNKNOWN` carries at least one verbatim sentence quote with `paragraph_id` (and `section`/`page_number` when available).
3. Abstract-only and scanned/unparsed PDF inputs NEVER assign `no_indication_observed` to fulltext-only domains; they abstain cleanly as `insufficient_evidence` + `UNKNOWN`.
4. Traffic-light markers in `quality_appraisal_report.md` match `quality_appraisal_table.json` domain ratings identically.
