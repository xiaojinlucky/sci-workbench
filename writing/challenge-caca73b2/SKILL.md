---
name: results-discussion-provenance-writer
description: Draft and audit publication-ready Results and Discussion sections from experimental tables, figure captions, and reference studies. Enforces strict Results-vs-Discussion separation, row-condition numeric binding, SD/SEM/CI error-bar fidelity, negative/non-significant finding retention, methodological literature comparison, and causal boundary calibration.
---

# Results & Discussion Chapter Writer (Row-Condition Grounded)

Transform experimental tables, figure captions, statistical outputs, and supplied reference summaries under `/input` into submission-ready `# Results` and `# Discussion` sections where every value traces to its exact condition row and every inference matches its evidence strength.

## Read References on Demand

- Read `references/results_discussion_protocol.md` for Results vs. Discussion layering, row-condition binding, null-result handling, and the four-beat Discussion structure.
- Read `references/academic_writing_standards.md` for epistemic boundaries, press-release anti-defensive prose, and bilingual de-AI standards.
- Read `references/output_contract_spec.md` before sealing `/app/output/result.json`.

## Hard Execution Rules

1. **Index condition rows before writing**: Run `rd_row_provenance_auditor.py index` on `/input` first to enumerate every `(condition, metric, value, error_bar_type, p_value)` tuple and every non-significant row (`p >= 0.05` / `FDR >= 0.05`).
2. **Zero row-condition misattribution**: Bind every reported value to its exact condition/group/dose row. Never attach a High-dose or responder subgroup value to a Low-dose, control, or overall cohort sentence.
3. **Preserve error-bar definitions and negative results**: Keep `SD` vs `SEM` vs `IQR` vs `95% CI` identical to source headers/captions. Report every non-significant or null row honestly with exact `p` values; never promote a non-significant directional trend into significance or causality.
4. **Enforce strict section separation**:
   - `# Results` reports observed magnitudes, directions, sample sizes, exact statistics, and figure/table pointers only (zero mechanistic speculation).
   - `# Discussion` synthesizes the main answer, compares agreement/disagreement and methodological differences with supplied prior studies, marks mechanistic hypotheses explicitly, and details concrete methodological/sample/measurement/statistical limitations without mechanically repeating all Results numbers.
5. **Honor structured `output_contract`**: Emit `/app/output/result.json` with verbatim `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `gaps`, and `limitations`.

## Step-by-Step Workflow

### Step 1: Probe Environment & Index Structured Data Rows
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "results-discussion-writing"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
python3 /skills/weekly-skill/scripts/rd_row_provenance_auditor.py index \
  --data-dir /input \
  --out-json /app/work/rd_data_index.json
```

**Completion criterion**: `/app/work/rd_data_index.json` lists all structured condition rows, declared error-bar types (`SD`/`SEM`/`IQR`/`CI95`), and non-significant rows (`nonsig_rows`).

### Step 2: Generate Row-Bound Scaffold & Draft `# Results` and `# Discussion`
First emit a row-bound scaffold preserving every condition row, non-significant flag, and declared error-bar type:
```bash
python3 /skills/weekly-skill/scripts/rd_row_provenance_auditor.py scaffold \
  --data-dir /input \
  --out-md /app/work/rd_scaffold.md
```
Expand `/app/work/rd_scaffold.md` into `/app/output/results_and_discussion.md`:
1. `# Results`: Organize by research question and figure/table sequence. State comparison groups, `n`, direction, exact magnitude, units, declared error metric, and exact `p`/`FDR` values. Explicitly report directional-but-non-significant findings as non-significant trends. If a raster figure lacks an accompanying numeric table, state `[EVIDENCE_GAP: numeric source table unavailable for Fig. X]` instead of visually guessing digits.
2. `# Discussion`: Follow the four-beat structure:
   - Synthesize how the primary findings answer the research question (do not copy-paste all Results numbers).
   - Compare with supplied prior studies, explicitly analyzing methodological/design/population differences behind discrepancies.
   - Present mechanistic hypotheses clearly hedged (`may reflect`, `consistent with`).
   - State concrete limitations across method, sample, measurement, and statistical dimensions.

**Completion criterion**: `/app/output/results_and_discussion.md` contains complete, editable `# Results` and `# Discussion` prose covering all source tables, non-significant rows, literature comparisons, and concrete limitations.

### Step 3: Run Row-Condition & Layering Audit Loop
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --tick audit
python3 /skills/weekly-skill/scripts/rd_row_provenance_auditor.py audit \
  --data-dir /input \
  --draft /app/output/results_and_discussion.md \
  --out-json /app/work/rd_audit_report.json
```

Resolve every high-severity issue before sealing:
- `ROW_CONDITION_VALUE_MISMATCH`: fix any value attached to the wrong condition row.
- `DANGLING_FIGURE_OR_TABLE_ANCHOR`: remove or reconcile any cited Figure/Table/Supplementary panel ID absent from `/input` captions and tables.
- `OMITTED_NULL_OR_NONSIG_ROW` / `OMITTED_CONDITION_ROW`: restore omitted non-significant/null findings and ensure full condition-row retention (`condition_rows_retained == total_condition_rows`).
- `NONSIG_PROMOTED_TO_SIGNIFICANT_OR_CAUSAL`: downgrade causal/significant claims on `p >= 0.05` rows to hedged non-significant trend descriptions.
- `ERROR_BAR_DEFINITION_CONFLICT`: align `SD`/`SEM`/`IQR`/`95% CI` with source definitions, or quarantine unverified dispersion metrics non-destructively with an explicit `[EVIDENCE_GAP: ...]` tag rather than deleting informative rows.
- `UNGROUNDED_VALUE`: verify every numeric token against `/input` (unit conversions pass only when both the converted number and target unit match a source `(value, unit)` pair).
- `MECHANISM_INTERPRETATION_IN_RESULTS` / `MISSING_CONCRETE_LIMITATIONS_IN_DISCUSSION`.

Re-run `audit` until `high_issue_count == 0`.

**Completion criterion**: `rd_audit_report.json` reports `ok: true` and `high_issue_count == 0`.

### Step 4: Seal structured `result.json` & Final Audit
```bash
python3 /skills/weekly-skill/scripts/rd_row_provenance_auditor.py seal \
  --data-dir /input \
  --draft /app/output/results_and_discussion.md \
  --case-id "<CASE_ID>" \
  --out-dir /app/output

python3 /skills/weekly-skill/scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json

python3 /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json
```

**Completion criterion**: `/app/output/results_and_discussion.md`, `/app/output/rd_audit_report.json`, `/app/output/rd_evidence_map.json`, and `/app/output/result.json` exist and pass `final_audit.py`.

## Exception & Degradation Handling

- **Missing statistical tests or error definitions**: Report observed point estimates only and log `[EVIDENCE_GAP: missing test statistic / error-bar definition]` in `gaps`.
- **No reference studies supplied**: Note honestly in Discussion and `limitations` that external literature comparisons require author-supplied references rather than inventing citations.
- **Raster-only charts without underlying tables**: Describe qualitative direction only when visually verifiable; never fabricate precise decimals.
