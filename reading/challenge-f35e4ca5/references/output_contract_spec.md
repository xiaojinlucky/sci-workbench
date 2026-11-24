# Output Contract & Supplied-Scope Specification (`challenge-f35e4ca5`)

## 1. Organizer Revision v2 Scope Rules

When `/input/case.json` contains `organizer_supplied_scope` (or `/input/organizer-supplied-scope.json` is supplied):

1. **Required Actual Images (`required_actual_image_files`)**: Verify file presence and SHA-256 hash for every listed image. Inspect dimensions, format, and quality flags via `figure_table_parser.py`.
2. **Required Literal Source Tables (`required_literal_source_table_ids`)**: Extract every listed table ID from `material.json` / `case.json` verbatim. Preserve exact headers, cell values, ordering, and missing cells. Never silently swap columns, rewrite suspicious values, or treat unlabeled columns as dimensionless.
3. **Unprovided Objects (`unprovided_objects`)**: Figures mentioned only in captions or manuscript text without supplied pixel files are NOT hidden reconstruction tasks. List them in `missing_pixel_figure_gaps` (`status: "not_provided"`) and `uncertainties`. Never fabricate their axes, data points, or error bars.
4. **Completion Semantics (`completion`)**:
   - `contract_status: "answered"`: All supplied images and all supplied literal source tables have been parsed within honest boundaries (even when unprovided caption-only figures exist and are logged as gaps).
   - `contract_status: "partial"`: One or more *supplied* images or required tables remain unparsed or unreadable.
   - `contract_status: "insufficient_evidence"`: Neither supplied figures nor supplied tables are present.
   - `contract_status: "input_error"`: Malformed JSON inputs or failed `--validate-readings` schema/calibration checks.

## 2. Deliverable Files (`/app/output/`)

- `/app/output/result.json`: Satisfies both Wenyon `output_contract` (`case_id`, `status` + `contract_status`, `result`, `claims[]`, `uncertainties[]`) and Harbor `result_guard` (`status` in `{"ok", "partial_with_gaps"}`, `sources`, `artifacts`, `metrics`, `metric_provenance`, `findings`).
- `/app/output/figure_table_bundle.json`: Full machine-readable inventory of `organizer_scope_audit`, `provided_figures`, `parsed_tables`, `missing_pixel_figure_gaps`, `cross_modal_audit`, `paperstory_branch_index`, and validated readings.
- `/app/output/figure_analysis_report.md`: Structured human-readable summary report with anchor links.

## 3. Claim Object Schema

Every entry in `claims[]` must contain:
- `kind`: `directly_readable_from_image` | `reported` | `reasonable_inference` | `undetermined_not_provided` | `not_applicable`
- `evidence`: concise factual finding with units, conditions, and pixel-approximation note where applicable
- `source_id`: exact input file or table source identifier (e.g., `artifacts/C01-fig1.png`, `material.json:C01-P041`)
- `location`: specific panel, axis, region, or table row/column reference
