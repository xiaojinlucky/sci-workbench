# Output Contract Spec (`challenge-bace1c46`)

- **Primary Deliverables**:
  - `/app/output/result.json` — runtime contract payload bridging both retrieval-candidate triage and methodological appraisal (`case_id`, `status`, `research_question`, `query_and_screening_log`, `ranked_evidence_candidates[]`, `coverage_and_omission_disclosure`, `scales_applied`, `overall_tier_distribution`, `appraisals[]`, `metrics`, `metric_provenance`, `gaps[]`).
  - `/app/output/quality_appraisal_table.json` — full version-pinned appraisal ledger with 4-layer gate traces, sentence-level anchors, stable source IDs (`doi`/`pmid`/`arxiv_id`/`source_file`), and tier derivation traces.
  - `/app/output/quality_appraisal_report.md` — human-readable retrieval-and-appraisal deliverable containing query/screening log, ranked candidate shortlist with screening rationale, traffic-light matrix synchronized 1:1 with JSON domain ratings, itemized verbatim quotes, and missing/permission/omission disclosure.

## Verification Invariants
1. Every appraisal entry declares `study_type_detected`, `scale_name`, `scale_version`, `material_tier` (`fulltext_verified` | `partial_text` | `abstract_only` | `scanned_or_unparsed_pdf` | `metadata_only`), `overall_quality_tier`, and `tier_derivation_rule`.
2. Every domain rating not equal to `insufficient_evidence` / `UNKNOWN` carries at least one verbatim sentence quote from a substantive (non-reference) section with `paragraph_id`, `section`, `page_number` (if paginated), `sentence_index`, and 4-layer gate trace (`reporting_presence`, `negation_modality_check`, `adequacy_check`).
3. Short/partial excerpts (`partial_text`), `abstract_only`, and `scanned_or_unparsed_pdf` inputs NEVER falsely claim `fulltext_verified` and NEVER default unknown domains to low risk (`no_indication_observed`).
4. Retrieval-track bridge fields (`research_question`, `query_and_screening_log`, `ranked_evidence_candidates` with stable source IDs/locators & triage rationale, `coverage_and_omission_disclosure`) are always populated honestly from supplied inputs.
5. Traffic-light markers in `quality_appraisal_report.md` match `quality_appraisal_table.json` domain ratings identically.
