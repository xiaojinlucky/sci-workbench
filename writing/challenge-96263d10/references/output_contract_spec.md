# Output Contract Specification (academic-template-adaptation)

Write `/app/output/result.json` preserving `case_id` and all required fields:
- `deliverable`: adapted editable document (`*_adapted.docx` / `.tex` / `.md`), `designated_target_template`, `applied_explicit_fields`, and `unspecified_fields`.
- `preserved_facts`: `fidelity_ok`, 9-item OOXML `unit_checks`, and `crossref_issues`.
- `evidence_map`: each applied formatting rule mapped directly to the designated target template.
- `change_log`: isolated template conflicts, blocked alternate-only fields, applied adaptations, font fallback decisions, and preview rendering status.
- `gaps` & `limitations`: explicit `UNSPECIFIED` template fields, unavailable font fallbacks, and missing renderer disclosures.
