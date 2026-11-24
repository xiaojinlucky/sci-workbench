# Output Contract Specification (`challenge-9e7302fd`)

Every run against `/input/case.json` and `/input/task.md` must produce three validated deliverables in `/app/output/`:

1. `/app/output/result.json` (Primary offline container contract result, emitted directly via `--out-result-json /app/output/result.json`)
2. `/app/output/stats_audit.json` (Full deterministic + reviewed structured methods & statistical audit)
3. `/app/output/stats_review_report.md` (Author-ready five-facet review report with recomputation tables and Impact x Cost checklist)

## 1. `/app/output/result.json` Required Fields

| Field | Type | Requirement |
| --- | --- | --- |
| `case_id` | `string` | Copied verbatim from `/input/case.json` |
| `status` | `string` | **Must be `ok` or `partial_with_gaps`** (enforced by `result_guard.py --finalize` and `final_audit.py`) |
| `audit_status` | `string` | Domain-level status: `completed`, `partial`, `indeterminate`, or `failed` |
| `artifacts` | `list[str]` | Relative paths inside `/app/output/`: `["result.json", "stats_audit.json", "stats_review_report.md"]` |
| `sources` | `list[dict]` | Scanned manuscripts and data tables (`file`, `sha256`, `extractor`) |
| `metrics` | `dict` | Finite numeric counts (`documents_scanned_count`, `statcheck_count`, `effect_ci_checks_count`, `proportion_checks_count`, `grim_checks_count`, `sample_conservation_checks_count`, `pseudoreplication_flags_count`, `csv_checks_count`, `findings_count`) |
| `metric_provenance` | `dict` | Maps every key in `metrics` to `"stats_audit.json:<field>"` for `gap_gate.py` |
| `findings` | `list[dict]` | Finding objects strictly separating `defect_class` (`decision_error` / `method_flaw` / `under_reported` / `norm_violation`) |
| `uncertainty` | `list[str]` | Explicit unassessable items (e.g., absent raw data, undisclosed multiplicity protocol) |
| `gaps` | `list[dict]` | Structured parsing/coverage gaps (`PDF_LOW_TEXT_PAGE`, etc.) |

## 2. Stat-Rounding-Aware Inequality & Three-Way Classification Invariants

- Reported inequality `p < x` checks both point-value `computed_p` and the test-statistic rounding interval `[p_lo, p_hi]`:
  - If `computed_p < x` (`<=` for `p <= x`): `consistent`.
  - If `p_lo < x <= computed_p` (e.g., `t(100) = 1.98, p < 0.05` where `p_lo = 0.04988 < 0.05`): `rounding_ambiguous` (`minor | under_reported | needs_human_review`, never misclassified as `critical decision_error`).
  - If `p_lo >= x` (e.g., `t(28) = 1.98, p < 0.05` where `p_lo = 0.057 >= 0.05`): definitive `decision_error` (`critical`) or `bound_violation` (`major`).
  - Symmetrically, `p > x` / `p >= x` checks `p_hi` before flagging `decision_error`.
- Every finding must carry `defect_class` separating:
  - `decision_error` (hard mathematical/statistical/CI/arithmetic contradiction)
  - `method_flaw` (evidenced design/unit mismatch such as pseudoreplication or causal overclaim on observational data)
  - `under_reported` (missing disclosure + explicit `missing_inputs` list; never conflated with misconduct or p-hacking)
  - `norm_violation` (e.g., `p = 0.000`, vague spin wording)

## 3. Mandatory Verification Commands

```bash
python -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require stats_audit.json --require stats_review_report.md
```
