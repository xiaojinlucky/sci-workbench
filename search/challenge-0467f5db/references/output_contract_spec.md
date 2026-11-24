# Output Contract Spec (`challenge-0467f5db`)

- **Primary Deliverables**:
  - `/app/output/result.json` — Harbor runtime contract payload (`case_id`, `status`, `metrics`, `metric_provenance`, `findings`, `gaps`, and summary coverage fields).
  - `/app/output/coverage_audit_report.json` — full recomputable audit artifact containing the frozen reference fingerprint, dual coverage ratios, itemized matched/missed records, per-source/per-query breakdown, supplemental marginal-gain curve, targeted remediation actions, and verification sample.
  - `/app/output/coverage_audit_report.md` — human-readable summary table and itemized attribution report.

## Required Conservation Identities
1. `baseline_matched_count + len(missed_references_attributed) == reference_denominator_count`
2. `actionable_reference_count == reference_denominator_count - retracted_or_unavailable_reference_count`
3. `baseline_matched_count + supplemental_net_new_hits_count + len(residual_omissions) == reference_denominator_count`
4. Every entry in `matched_references[]` carries `reference_id`, `hit_id`, `match_method`, `source`, `source_file`, and `query_id`.
5. Every entry in `missed_references_attributed[]` carries `record_id`, `omission_category` (one of the 7 canonical classes), `query_id`, `field_scope`, `evidence`, and `evidence_state`.
6. Any title-similar candidate failing first-author or year corroboration remains in `missed_references_attributed[]` under `identifier_mismatch` with full `candidate_match_evidence`.
