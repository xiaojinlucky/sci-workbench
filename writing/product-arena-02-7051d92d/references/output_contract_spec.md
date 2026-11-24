# Output Contract Specification (`academic-language-polishing`)

All final runtime deliverables must be written under `/app/output/` (or the configured output directory) and verified by `polishing_invariant_engine.py --strict`, `gap_gate.py`, and `final_audit.py`.

## 1. Required Files under `/app/output/`

| File | Format | Mandatory Content |
| --- | --- | --- |
| `polished_manuscript.md` | UTF-8 Markdown (or `.docx` when requested) | Clean, publication-ready edited manuscript with zero residual markup errors or altered empirical facts |
| `polishing_redline_diff.md` | UTF-8 Markdown | Sentence-level table with `sentence_id`, `section`, `edit_type`, `status`, `original`, `revised`, `rationale` + dismissed false positives + glossary/abbreviation notes |
| `polishing_audit.json` | UTF-8 JSON | Deterministic invariant diff, modality drift count, false-positive guard records, glossary & abbreviation consistency audit, before/after de-AI lint counts |
| `result.json` | UTF-8 JSON | Machine-readable contract payload containing all 5 core fields plus Harbor verification fields |

## 2. Mandatory `result.json` Schema

- `case_id`: Verbatim case identifier from `/input/case.json`.
- `task_id`: `"academic-language-polishing"`.
- `status`: `"ok"` when all hard invariants and required checks pass with zero blocking gaps; `"partial_with_gaps"` when author confirmation, missing glossary resolution, or blocked edits remain.
- `deliverable`: Object pointing to `polished_manuscript`, `redline_diff`, `audit_json`, and target `register`.
- `preserved_facts`: Object recording `invariants_preserved` (bool), extracted source vs. revised numbers/units/p-values/sample sizes/citations/figure refs, plus `modality_drift_count`, `numeric_drift_count`, and `citation_drop_count`.
- `evidence_map`: Array mapping every edited or protected `sentence_id` back to `source_file`, `section`, `status`, and `invariants_preserved`.
- `change_log`: Array of per-sentence records (`sentence_id`, `edit_type`, `status`, `original`, `revised`, `rationale`).
- `artifacts`, `sources`, `gaps`, `limitations`, `verification`: Standard Harbor traceability fields.
