# Output Contract Specification (results-discussion-writing)

Every run must write `/app/output/result.json` preserving exact `case_id` and all structured `output_contract` fields:
- `deliverable`: `/app/output/results_and_discussion.md`, `rd_audit_report.json`, and `rd_evidence_map.json`.
- `preserved_facts`: `grounded_numbers_count`, `ungrounded_numbers_count`, `row_condition_mismatches_count`, `omitted_nonsig_rows_count`, `source_error_bar_types`, and `draft_error_bar_types`.
- `evidence_map`: line-level mapping from draft numeric values and named conditions back to source `file:row:condition:col` anchors.
- `change_log`: full deterministic audit summary and resolved discrepancies.
- `gaps` & `limitations`: missing chart values, raster-only figures without source tables, or unresolved conflicts.
