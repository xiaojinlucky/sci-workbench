# Output Contract Specification (challenge-de9f0708)

Every execution against `/input/case.json` must emit `/app/output/result.json` plus domain deliverables `/app/output/compliance_audit.json` and `/app/output/compliance_report.md`.

## 1. Dual-Compatible Status Contract (`result.json`)
- `status` (`result_guard.py` / `final_audit.py` contract):
  - `"ok"` when all readable inputs were screened with zero unresolved parsing/grounding gaps.
  - `"partial_with_gaps"` when input parsing gaps (`PDF_LOW_TEXT_PAGE`, `PDF_EXTRACTOR_UNAVAILABLE`, `IMAGE_DECODE_ERROR`, etc.) exist.
- `audit_status` (domain compliance status): `"completed"` | `"partial"` | `"indeterminate"` | `"failed"`.
- `verdict`: `"compliant_no_major_issues"` | `"violations_detected"` | `"needs_human_investigation"` | `"incomplete_inputs"`.

## 2. Required Top-Level Keys in `/app/output/result.json`
- `schema_version`: `"3.0"`
- `case_id`: exact `case_id` from `/input/case.json`
- `task_id`: `"challenge-de9f0708"`
- `status`: `"ok"` | `"partial_with_gaps"`
- `audit_status`: `"completed"` | `"partial"` | `"indeterminate"` | `"failed"`
- `verdict`: `"compliant_no_major_issues"` | `"violations_detected"` | `"needs_human_investigation"` | `"incomplete_inputs"`
- `scope_disclaimer`: explicit non-legal / non-IRB screening boundary notice
- `guideline_status`: `"guideline_supplied"` | `"guideline_not_supplied"`
- `guidelines_applied`: exact guideline object used (empty `{}` when not supplied)
- `sources`, `artifacts`, `metrics`, `metric_provenance`, `gaps`, `uncertainty`
- `findings`: list of findings with `compliance_state` (`confirmed_violation` | `material_not_supplied` | `needs_human_or_legal_ethics_review`)
- `image_integrity_matches`, `third_party_assets`, `rejected_pseudo_ethics_ids`, `checklist_status`

## 3. Finding Object Schema
Each finding MUST include:
- `finding_id`: `"CMP-001"`, ...
- `facet`: `"academic_norms"`
- `subtype`: concrete defect/cue code
- `finding_type`: `"norm_violation"` | `"insufficient_evidence"` | `"factual_error"` | `"suggestion"`
- `compliance_state`: `"confirmed_violation"` | `"material_not_supplied"` | `"needs_human_or_legal_ethics_review"`
- `severity`: `"critical"` | `"major"` | `"minor"` | `"suggestion"`
- `location`: `{file, line_start, line_end, ...}`
- `verbatim_quote`: exact source quote (strictly redacted via `_mask_match` for any PII/host-path/double-blind leak) or `[MISSING: ...]`
- `evidence_basis`: concrete anchor linking source observation to rule/boundary
- `rule_reference`: exact standard or supplied `guidelines.json` parameter and version
- `actionable_revision`: specific remediation step
- `status`: `"confirmed"` | `"needs_human_review"` | `"similarity_detected_needs_investigation"`

## 4. Mandatory Final Audit Command
```bash
python /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require compliance_audit.json \
  --require compliance_report.md
```
