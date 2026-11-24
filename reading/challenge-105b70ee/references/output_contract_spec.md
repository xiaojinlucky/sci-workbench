# Output Contract Specification (`challenge-105b70ee`)

## 1. Input Interface (`/input/target-fields.json`)

Organizer Revision v2 specifies concrete extraction fields in `/input/target-fields.json` (array of field objects or object with `fields`/`target_fields` array). Each requested `field_id` must produce exactly one output record with all 9 required keys:

| Key | Type | Requirement |
| --- | --- | --- |
| `field_id` | string | Exact match to `/input/target-fields.json` (1:1, no duplicates, no omissions, no extras) |
| `scalar` | string / number / `null` | Exact extracted value (`88.5`, `88.5 ± 1.2`, `10–25`, `<0.05`); MUST be `null` for `not_reported` or `extraction_failed` (never `0`) |
| `units` | string / `null` | Verbatim source unit (`%`, `mA cm^-2`, `mg/L`, `°C`); `null` if unreported in source |
| `statistic` | string / `null` | Dispersion/significance/sample descriptor (`mean ± SD`, `95% CI [...]`, `p < 0.01`, `n = 6`) |
| `experimental_condition` | string / `null` | Conditions under which the scalar holds (temperature, pH, catalyst, sample group, protocol) |
| `comparator` | string / `null` | Explicit baseline/control group if reported (`vs. uncoated control`) |
| `paragraph_id` | string / `null` | Exact paragraph or 2D table-cell anchor (`P012`, `Table1:R2:C3`) present in the corpus |
| `quote` | string | Verbatim substring from the referenced `paragraph_id` containing the scalar's numeric core |
| `status` | enum | `reported` \| `not_reported` \| `source_conflict` \| `extraction_failed` |

## 2. Top-Level `/app/output/result.json` Contract

Preserves both Wenyon `output_contract` (`case_id`, `contract_status` in `{answered, partial, insufficient_evidence, input_error}`, `result`, `claims[]`, `uncertainties[]`) and Harbor `result_guard` (`status` in `{ok, partial_with_gaps}`, `sources`, `artifacts`, `metrics`, `metric_provenance`, `findings`).

## 3. Claim Emission Rule

Only `reported` records (with verified `paragraph_id`, exact `quote`, and numeric-core fidelity) and `source_conflict` records (citing `>= 2` conflicting locations) emit entries in `claims[]`. Never emit claims from `not_reported` or `extraction_failed` fields.
