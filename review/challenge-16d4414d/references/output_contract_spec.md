# Output Contract Specification (challenge-16d4414d)

Every execution against `/input/case.json` must produce `/app/output/result.json` plus the domain deliverables `/app/output/logic_audit.json` and `/app/output/logic_review_report.md`.

## 1. Dual-Compatible Status Contract (`result.json`)
- `status` (`result_guard.py` / `final_audit.py` contract):
  - `"ok"` when all readable inputs were audited with zero unresolved parsing/grounding gaps.
  - `"partial_with_gaps"` when any input gap (e.g., `PDF_LOW_TEXT_PAGE`, `PDF_EXTRACTOR_UNAVAILABLE`, truncated input, missing revision pair) exists.
- `audit_status` (domain status): `"completed"` | `"partial"` | `"indeterminate"` | `"failed"`.
- `verdict`: `"no_major_defects_detected"` | `"defects_detected"` | `"needs_human_review"` | `"incomplete_inputs"`.

## 2. Required Top-Level Keys in `/app/output/result.json`
- `schema_version`: `"3.0"`
- `case_id`: exact `case_id` copied from `/input/case.json`
- `task_id`: `"challenge-16d4414d"`
- `status`: `"ok"` | `"partial_with_gaps"`
- `audit_status`: `"completed"` | `"partial"` | `"indeterminate"` | `"failed"`
- `verdict`: `"no_major_defects_detected"` | `"defects_detected"` | `"needs_human_review"` | `"incomplete_inputs"`
- `sources`: list of `{file, sha256, extractor, lines, pages}`
- `artifacts`: `["result.json", "logic_audit.json", "logic_review_report.md"]`
- `metrics` & `metric_provenance`: numeric counts (`findings_count`, `critical_count`, `major_count`, `argument_graph_count`, etc.) mapped to `logic_audit.json`
- `findings`: list of five-gate validated finding objects
- `argument_graph`: structured `Claim -> Evidence -> Warrant -> Boundary` records
- `revision_closure`: closure summary when prior reviews / rebuttal / `v1`+`v2` drafts are supplied
- `gaps`: structured list of input or verification gaps (never omitted when non-empty)
- `uncertainty`: explicit scope and verification limits

## 3. Finding Object Schema
Each item in `findings` MUST include:
- `finding_id`: `"LOG-001"`, ...
- `facet`: `"argument_chain"` | `"fact_verification"`
- `subtype`: `"causal_overreach"` | `"intro_delivery_fracture"` | `"cross_section_direction_reversal"` | `"cross_section_significance_reversal"` | `"cross_section_numeric_conflict"` | `"unmatched_summary_percentage"` | `"scale_over_extrapolation"` | `"missing_limitations"` | `"revision_unclosed_claimed_fixed"` | `"revision_unclosed_unaddressed"` | `"revision_partially_closed"`
- `finding_type`: `"factual_error"` | `"insufficient_evidence"` | `"norm_violation"` | `"suggestion"`
- `severity`: `"critical"` | `"major"` | `"minor"` | `"suggestion"`
- `severity_rationale`: concrete impact explanation
- `location`: `{file, section, page, line_start, line_end}`
- `verbatim_quote`: exact source quote (`<= 200` chars) or `[MISSING: ...]`
- `evidence_basis`: specific comparison/design anchor proving the failure mode
- `actionable_revision`: concrete sentence-level fix
- `firewall_check`: ACL H1-H17 check result
- `status`: `"confirmed"` | `"needs_human_review"`

## 4. Mandatory Final Audit Command
```bash
python /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require logic_audit.json \
  --require logic_review_report.md
```
