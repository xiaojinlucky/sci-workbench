# Output Contract Specification (`academic-citation-management`)

All final deliverables must be placed under `/app/output/` and verified by `citation_lifecycle_manager.py`, `gap_gate.py`, and `final_audit.py`.

## 1. Required Deliverables under `/app/output/`

| File | Format | Mandatory Content |
| --- | --- | --- |
| `citation_audit_report.md` | UTF-8 Markdown | OOXML live-field diagnosis, deduplication decisions, homonymous-author protections, bidirectional sync status, and two-layer verification table |
| `citation_audit.json` | UTF-8 JSON | Machine-readable live-field audit, dedup clusters, renumbering map, NotebookLM #2416 collapse check, two-layer checks, formatted GB/T 7714 & APA 7 records |
| `cleaned_references.bib` | UTF-8 BibTeX | Deduplicated, normalized BibTeX library preserving peer-reviewed survivors |
| `formatted_references_gbt7714.md` | UTF-8 Markdown | GB/T 7714-2015 formatted bibliography with explicit missing-field tags |
| `formatted_references_apa7.md` | UTF-8 Markdown | APA 7th formatted bibliography with explicit missing-field tags |
| `renumbered_manuscript.md` | UTF-8 Markdown | Synchronized manuscript with sequential in-text markers, captions, and reordered reference list (when manuscript supplied) |
| `result.json` | UTF-8 JSON | structured/Harbor 5-field contract payload + verification metadata |

## 2. Mandatory `result.json` Schema

- `case_id`, `task_id` (`"academic-citation-management"`), `status` (`"ok"` | `"partial_with_gaps"`).
- `deliverable`: Paths to audit report, JSON audit, cleaned `.bib`, GB/T 7714 & APA 7 outputs, and renumbered manuscript.
- `preserved_facts`: `deduplicated_records_count`, `protected_homonymous_pairs_count`, `live_field_status`, `renumbering_map`.
- `evidence_map`: Per-citation two-layer records (`original_marker`, `renumbered_marker`, `cited_lines`, `metadata_existence_status`, `claim_support_status`, `support_note`).
- `change_log`: Explicit deduplication merges (with criterion and rationale) and sequential renumbering mappings.
- `artifacts`, `sources`, `gaps`, `limitations`, `verification`.
