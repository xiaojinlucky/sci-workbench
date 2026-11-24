# Field Binding, Table Coordinates & Conflict Resolution Rules

## 1. Anti-Distractor Number Filtering

Automated extraction and manual review must exclude non-measurement numbers from usurping target scalars:
- Citation brackets (`[12]`, `[3–5]`) and publication years (`(2024)`, `in 2023`)
- Structural labels (`Figure 2`, `Table 1`, `Section 3.2`, `Eq. (4)`)
- Inferential statistics (`p < 0.05`, `p = 0.003`, `n = 12`) belong in `statistic`, not `scalar`, unless the requested `field_id` explicitly asks for a p-value or sample size.

## 2. Cell-Level Table Coordinate Integrity

- JSON (`columns`+`rows`), CSV, and Markdown tables are expanded into 2D cell anchors (`<table_id>:R<row>:C<col>`), preserving `row_label` (experimental condition/group) and `col_header` (metric + `header_unit`).
- Missing cells (`NA`, `N/A`, `ND`, `n.d.`, `N/R`, `—`, `-`, blank) stay `status: "not_reported"`, `scalar: null`—never coerced to `0`.
- Missing units in both cell and column header stay `units: null` with `unit_status: "unit_missing_in_source"`—never borrowed from adjacent sentences or columns.
- Preserve censored limit-of-detection values (`<0.05`, `>99`) verbatim without stripping `<` or `>`.

## 3. Distinguishing True Conflicts from Condition Stratification

| Pattern | Classification | Required Output |
| --- | --- | --- |
| Prose says `88.5%` for `Cat-B at 300 °C`, while Table 2 says `84.2%` for `Cat-B at 300 °C` | True cross-source conflict | `status: "source_conflict"`, `conflicting_sources` listing both prose `paragraph_id` and table cell `Table2:R...:C...` |
| Passage A reports `72.5%` at `25 °C`, Passage B reports `88.9%` at `60 °C` | Condition-stratified valid values | Select the passage matching `target_condition` as `reported`, record `experimental_condition`, and retain alternatives in `condition_stratified_candidates` |
| Metric never measured or cell says `ND` | Absent in source | `status: "not_reported"`, `scalar: null` |

## 4. Numeric-in-Quote Fidelity Rule

During `--validate-fields`, a `reported` record is rejected if its numeric core (e.g., `88.5`) does not appear verbatim in its cited `quote`. Do not silently round (`88.47` -> `88.5`) or convert units (`500 mV` -> `0.5 V`).
