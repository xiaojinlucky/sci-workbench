# Output Contract Specification (`evidence-driven-writing`)

This contract strictly preserves the official structured public attachment (`resources/example-input.json` and `resources/output-contract.md`) alongside the three-part evidence-writing deliverable.

## 1. Required Deliverables under `/app/output/`

| File | Format | Mandatory Content |
| --- | --- | --- |
| `draft.md` | UTF-8 Markdown | Synthesized, evidence-calibrated academic draft answering the research question with explicit applicability conditions and conflict reconciliation |
| `claims_list.json` | UTF-8 JSON | Numbered atomic claims (`C01`, `C02`, ...) with `claim_strength`, `polarity`, `conditions`, `citations`, and extracted invariants |
| `evidence_matrix.md` | UTF-8 Markdown | Auditable table mapping every `claim_id` to `verdict` (5-state), `source_type`/`study_design`, `file#page:section:line` locator, `verbatim_excerpt`, and rationale |
| `evidence_audit.json` | UTF-8 JSON | Machine-readable 5-state verdict counts, conflict synthesis audit, prose audit, and per-claim verification records |
| `result.json` | UTF-8 JSON | Strictly preserves all 5 structured `output_contract` fields + Harbor traceability fields |

## 2. Mandatory structured 5-Field `result.json` Schema

1. `case_id`: Verbatim identifier copied from `/input/case.json`.
2. `deliverable`: Object referencing `draft_file`, `claims_list_json`, `evidence_matrix_md`, `evidence_audit_json`.
3. `preserved_facts`: Object capturing extracted numerical/statistical/citation invariants, `sources_indexed_count`, `verdict_counts`, and `conflict_synthesis_passed`.
4. `evidence_map`: Array of per-claim records linking `claim_id`, `section`, `line`, `claim_strength`, `citations`, `verdict`, `locators`, and `verbatim_excerpts`.
5. `change_log`: Array listing all unresolved or modified claims, required revisions, author confirmations, and remaining evidence gaps.

Additional Harbor fields retained: `task_id` (`"evidence-driven-writing"`), `status` (`"ok"` | `"partial_with_gaps"`), `artifacts`, `sources`, `gaps`, `limitations`, `verification`.
