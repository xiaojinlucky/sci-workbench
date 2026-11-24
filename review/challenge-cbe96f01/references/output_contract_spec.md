# Output Contract Specification (`challenge-cbe96f01`)

Every run against `/input/case.json` and `/input/task.md` must produce three validated deliverables in `/app/output/`:

1. `/app/output/result.json` (Primary offline container contract result, emitted directly via `--out-result-json /app/output/result.json`)
2. `/app/output/citation_audit.json` (Full deterministic + reviewed structured citation audit)
3. `/app/output/citation_review_report.md` (Author-ready review report with five-state matrix and Impact x Cost checklist)

## 1. `/app/output/result.json` Required Fields

| Field | Type | Requirement |
| --- | --- | --- |
| `case_id` | `string` | Copied verbatim from `/input/case.json` |
| `status` | `string` | **Must be `ok` or `partial_with_gaps`** (enforced by `result_guard.py --finalize` and `final_audit.py`) |
| `audit_status` | `string` | Domain-level status: `completed`, `partial`, `indeterminate`, or `failed` |
| `artifacts` | `list[str]` | Relative paths inside `/app/output/`: `["result.json", "citation_audit.json", "citation_review_report.md"]` |
| `sources` | `list[dict]` | Scanned manuscripts and indexed source files (`file`, `sha256`, `extractor`) |
| `metrics` | `dict` | Finite numeric counts (`in_text_markers_total`, `unique_cited_keys_count`, `bibliography_count`, `atomic_claims_count`, `findings_count`, five-state counts) |
| `metric_provenance` | `dict` | Maps every key in `metrics` to `"citation_audit.json:<field>"` so `gap_gate.py` verifies provenance |
| `findings` | `list[dict]` | Finding objects following the unified schema below |
| `uncertainty` | `list[str]` | Explicit unverified boundaries (e.g., `source_not_provided` counts, preprint status caveats) |
| `gaps` | `list[dict]` | Structured parsing or coverage gaps (`PDF_LOW_TEXT_PAGE`, `PDF_EXTRACTOR_UNAVAILABLE`, etc.) |

## 2. Unified Finding Object Schema

Each item in `findings` must include:
- `finding_id`: e.g. `"CIT-001"`
- `facet`: `"citation_support"` | `"fact_verification"` | `"numerical_consistency"` | `"method_design"` | `"argument_chain"` | `"academic_norms"`
- `finding_type`: `"factual_error"` | `"insufficient_evidence"` | `"norm_violation"` | `"suggestion"`
- `severity`: `"critical"` (`P0`) | `"major"` (`P1`) | `"minor"` (`P2`) | `"suggestion"` (`P3`)
- `location`: `{"file": "...", "section": "...", "line_start": int, "line_end": int, "page": optional int}`
- `verbatim_quote`: exact quote from input (`<= 200` chars)
- `evidence_basis`: specific discrepancy, aligned source location/quote, or reconciliation count
- `actionable_revision`: concrete edit instruction for the author
- `impact_cost`: `{"impact": "high|medium|low", "cost": "low|medium|high"}`
- `status`: `"confirmed"` | `"needs_human_review"`

## 3. Five-State `citation_matrix` Contract

Every evaluated atomic sub-claim in `citation_audit.json["citation_matrix"]` must record:
- `manuscript`, `section`, `line`, `page`, `key`, `citation_style`, `subclaim_idx`, `atomic_subclaim`, `sentence_role`
- `support_status`: one of `supported_by_source`, `partially_supported`, `contradicted_by_source`, `source_not_provided`, `doi_or_metadata_only_unsupported`
- `source_file`, `source_location`, `source_quote`, `match_method`, `publication_flags`, `needs_human_review`, `rationale`

## 4. Mandatory Verification Commands

```bash
python -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require citation_audit.json --require citation_review_report.md
```
