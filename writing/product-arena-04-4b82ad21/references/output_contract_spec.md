# Output Contract Specification (`introduction-related-work-writing`)

All runtime deliverables must be written under `/app/output/` as valid UTF-8 files:

1. `/app/output/result.json`:
   - `case_id`, `task_id` (`"introduction-related-work-writing"`), `status` (`"ok"` or `"partial_with_gaps"`).
   - `deliverable`: Pointers to `related_work_taxonomy.json`, `gap_contribution_alignment.md` (and drafted manuscript markdown when drafting), plus `four_beat_complete` and `blocking_count`.
   - `preserved_facts`: Source invariants (`numbers_with_units_count`, `dois`, `figure_table_refs`, `draft_citations_count`).
   - `evidence_map`: Mapping every audit finding (`IRW-001`, ...) to `(source_file, section, line, quote)`.
   - `change_log`, `artifacts`, `sources`, `gaps`, `limitations`, `verification`.
2. `/app/output/related_work_taxonomy.json`:
   - `four_beat_opening`, `chronological_stitching_detected`, `supplied_literature_conflict_detected`, `taxonomy_by_method_route`, `unsubstantiated_gap_claims_count`, `contribution_alignment`, `overclaimed_contributions_count`, `citation_coverage`, `findings`.
3. `/app/output/gap_contribution_alignment.md`:
   - Comparative Related Work taxonomy table, 1:1 Contribution-to-Evidence alignment matrix, and prioritized remediation instructions.
4. `/app/output/introduction_and_related_work.md` (when drafting/revising chapters):
   - Clean, publication-grade Introduction and Related Work draft passing `intro_related_work_auditor.py` with `blocking_count == 0`.
