# Output Contract Specification (`paper-structure-optimization`)

All runtime deliverables must be written under `/app/output/` as valid UTF-8 files:

1. `/app/output/result.json`:
   - `case_id`: Exact case identifier from `/input/case.json`.
   - `task_id`: `"paper-structure-optimization"`.
   - `status`: `"ok"` or `"partial_with_gaps"` (when input gaps/truncation/missing references exist).
   - `deliverable`: Summary object pointing to `structural_diagnosis.json` and `editing_action_plan.md`, total card count, and six-dimension counts.
   - `preserved_facts`: Extracted invariant inventory (`numbers_with_units_count`, `pvalues_count`, `sample_sizes`, `dois`, `figure_table_refs`, `manuscript_mutated: false`).
   - `evidence_map`: List mapping every `card_id` to `(source_file, section, paragraph_id, line_range, quote_excerpt)`.
   - `change_log`: Proposed editing action record (`card_id`, `action_verb`, `dimension`, `location`, `status`).
   - `artifacts`: Exact list of output filenames (`["result.json", "structural_diagnosis.json", "editing_action_plan.md"]`).
   - `sources`, `gaps`, `limitations`, `verification`: Honest provenance, missing-input records, and firewall check summary.
2. `/app/output/structural_diagnosis.json`:
   - Structured six-dimension report containing `sections_overview`, `six_dimension_counts`, `rq_and_contribution_trace`, `chronological_laundry_lists`, `same_metric_conflicts`, `redundancy_pairs`, `edit_action_cards`, and `firewall_verification`.
3. `/app/output/editing_action_plan.md`:
   - Human-readable, priority-sorted Markdown plan of executable editing action cards with exact anchors and `Accept / Partially Accept / Reject` options.
