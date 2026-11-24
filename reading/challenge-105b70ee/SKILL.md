---
name: structured-paper-extractor
description: Read /input/target-fields.json and extract one verified structured record per requested field_id across prose paragraphs, cell-anchored tables (JSON/CSV/Markdown), captions, and PDFs. Preserves scalar, compound units, statistics, experimental conditions, comparators, verbatim quotes, epistemic class (reported_result/conditional_limitation/speculative), and 4-state status (reported/not_reported/source_conflict/extraction_failed) with strict zero-default-zero and numeric-in-quote verification.
---

# Literature Conclusion & Data Extractor (`challenge-105b70ee`)

## 1. Purpose, Scope & Organizer Revision v2 Guarantees

This skill implements the Harbor and Wenyon public contract for `challenge-105b70ee` (`文献结论与数据提取`) under offline Linux constraints (`64` requests, `900s`, zero network, zero `fitz`/`pymupdf`/`cairosvg`/Windows COM dependencies).

It directly solves the highest-frequency extraction failures observed on real scientific papers:

1. **One Record per Requested `field_id`**: Reads `/input/target-fields.json` (or `case.json.target_fields`) and emits strictly one record per requested `field_id`—zero omissions, zero duplicates, zero unexpected extra IDs.
2. **Cell-Level Table & Multi-Source Indexing**: Expands JSON (`columns`+`rows`), CSV, and Markdown tables into exact 2D cell-anchored blocks (`TableID:R{row}:C{col}`) preserving `row_label`, `col_header`, and `header_unit` rather than flattening tables into unanchored strings.
3. **Sentence-Level Condition-Value Binding**: Filters out years `(2024)`, citation brackets `[12]`, figure/table/section numbers, sample counts `n=6`, and `p`-values so they never hijack primary numeric scalars. Binds `scalar` (exact value, `mean ± SD`, range `a–b`, or censored `<0.05`) together with compound `units`, `statistic`, `experimental_condition`, and `comparator` from the same sentence or table row.
4. **Zero-Default-Zero & Unit Honesty**: An absent field or table placeholder (`NA`, `N/A`, `ND`, `—`, `-`, blank) is strictly `status: "not_reported"` with `scalar: null`—NEVER `0`. A missing unit in the source remains `units: null` (`unit_status: "unit_missing_in_source"`)—never borrowed from neighboring sentences or silently converted.
5. **True Cross-Source Conflict vs. Condition Stratification**: Distinguishes genuine same-condition conflicts between prose and tables (`status: "source_conflict"` preserving all `conflicting_sources[]`) from valid multi-condition measurements (e.g., 25 °C vs. 80 °C, preserved in `condition_stratified_candidates`).
6. **Epistemic Strength Separation**: Classifies every extracted statement as `reported_result` (explicit empirical finding), `conditional_limitation` (boundary/scope restriction), or `speculative` (`may`/`might`/`could`/`suggests`/`可能`/`提示`)—never rewriting tentative author phrasing as proven fact.

## 2. Execution Workflow (Deterministic 4-Step Pipeline)

Locate scripts relative to this `SKILL.md` directory (typically `/skills/weekly-skill/scripts/`).

### Step 1: Preflight & Automated Multi-Source Candidate Extraction

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --init --state /app/work/.budget_state.json
python3 -B scripts/field_extraction_engine.py --input /input --output /app/output --work /app/work
```

This indexes every paragraph, caption, PDF page, and 2D table cell across `/input`, extracts initial candidate field records, flags potential cross-source conflicts, and writes `/app/output/extracted_fields.json`, `/app/output/extracted_fields_report.md`, and an initial `/app/output/result.json`.

### Step 2: Semantic Binding, Condition & Epistemic Verification (Agent Step)

Inspect `/app/output/extracted_fields.json` and `/app/output/extracted_fields_report.md` against the actual source text and `references/field_binding_and_unit_rules.md`:

1. **Semantic Target Check (Literal Quote != Semantic Match)**: Confirm the extracted scalar truly measures the requested `field_id` under the requested condition, not a baseline control, comparator group, or literature benchmark cited in the Introduction.
2. **Unit & Statistical Fidelity**: Keep original units (`mA cm^-2`, `mg/L`, `wt%`, `MPa`, `%`) and statistical descriptors (`mean ± SD`, `median (IQR)`, `95% CI`, `p < 0.01`, `n = 12`) verbatim without silent unit conversion or rounding.
3. **Table-vs-Prose Reconciliation**: If prose and a table report conflicting values for the exact same group and condition, set `status: "source_conflict"` and list both anchors in `conflicting_sources[]`. If they describe different experimental conditions, select the condition matching `target-fields.json` and preserve the condition string explicitly.
4. **Epistemic Tagging**: Keep `speculative` and `conditional_limitation` statements strictly labeled; never promote a hypothesis or discussion caveat into an unqualified `reported_result`.

### Step 3: Strict Dual Validation (Quote + Numeric Core + Zero-Default-Zero) & Promotion

Save your verified fields array to `/app/work/fields_draft.json` and run the strict validator:

```bash
python3 -B scripts/field_extraction_engine.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --validate-fields /app/work/fields_draft.json
```

When `--validate-fields` is supplied:
- A missing or malformed draft exits non-zero (`contract_status: "input_error"`) and NEVER silently substitutes automatic extraction.
- Every `reported` record must pass BOTH exact verbatim quote matching at `paragraph_id` AND verbatim presence of the scalar's numeric core inside that quote.
- Every `not_reported` or `extraction_failed` record must keep `scalar: null`.
- Every `source_conflict` record must include `>= 2` verified entries in `conflicting_sources[]`.

### Step 4: Grounding Gate & Final Deliverable Audit

```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json --require extracted_fields.json --require extracted_fields_report.md
```

## 3. Wenyon Output Contract & Hard Boundaries

- `/app/output/result.json` preserves `case_id` and all Wenyon public contract keys (`case_id`, `status`/`contract_status`, `result`, `claims[]`, `uncertainties[]`) plus Harbor `metrics` and `metric_provenance`.
- `contract_status` rules: `answered` (all requested `field_id`s validated as `reported`), `partial` (mix of resolved and `not_reported`/`source_conflict`/`extraction_failed`), `insufficient_evidence` (zero requested fields supported), `input_error` (malformed input or failed `--validate-fields`).
- **Never** guess missing numbers, **never** default absent fields to `0`, and **never** emit `claims[]` from unresolved or quote-mismatched fields.

## 4. References & Examples

- `references/output_contract_spec.md` — Organizer Revision v2 `/input/target-fields.json` schema and Wenyon contract rules.
- `references/field_binding_and_unit_rules.md` — Condition-value binding, cell-level table coordinates, missing placeholders, and conflict resolution rules.
- `references/epistemic_and_deconstruction_guide.md` — 10-part paper data/threshold deconstruction and epistemic classification guide.
- `examples/minimal_example.md` — Synthetic target-fields input and validated output schema example.
