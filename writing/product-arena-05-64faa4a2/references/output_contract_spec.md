# Output Contract Specification (`methods-experiments-writing`)

All runtime deliverables must be written under `/app/output/` as valid UTF-8 files (strictly `.csv`, `.json`, `.md` — never tab-delimited files):

1. `/app/output/result.json`:
   - `case_id`, `task_id` (`"methods-experiments-writing"`), `status` (`"ok"` or `"partial_with_gaps"`).
   - `deliverable`: Pointers to `experimental_parameters.csv`, `reproducibility_checklist.json`, `methods_audit.json`, `six_elements_coverage`, and `blocking_count`.
   - `preserved_facts`: Extracted parameter record count, unique canonical keys, sample sizes, and invariant counts.
   - `evidence_map`, `change_log`, `artifacts`, `sources` (with `ELN-SRC-###` IDs), `gaps`, `limitations`, `verification`.
2. `/app/output/experimental_parameters.csv`:
   - Comma-separated table with columns: `record_id,canonical_key,raw_key,planned_value,actual_value,unit,role,source_file_line,draft_reported_value,audit_status`.
3. `/app/output/reproducibility_checklist.json`:
   - `eln_source_backlinks`, `execution_commands_detected`, `six_elements_coverage`, `essential_items_checklist`, `unrecorded_author_confirm_required`.
4. `/app/output/methods_audit.json`:
   - `planned_vs_actual_conflicts`, `planned_reported_as_actual`, `config_key_miscopies`, `ungrounded_parameters`, `missing_unrecorded_items`, `frontend_semantic_leaks`, `findings`.
5. `/app/output/methods_and_experiments.md` (when drafting/revising chapters):
   - Clean, reproducible Methods & Experiments manuscript with zero ungrounded fabrications and zero leaked backend tokens.
