---
name: scientific-figure-table-parser
description: Parse supplied scientific figures (raster/SVG) and all literal source tables under Organizer Revision v2 and Wenyon output contracts. Provides zero-dependency image inspection, least-squares linear/log10/ln/dual-Y pixel-to-data calibration with log-axis miscalibration blocking and unscaled-axis refusal, microscopy scale-bar conversion, PaperStory backbone-and-branch cross-referencing, and strict unprovided-pixel figure gap isolation.
---

# Scientific Figure & Table Parser (`challenge-f35e4ca5`)

## 1. Purpose, Scope & Revision v2 Guarantees

This skill implements the Harbor and Wenyon public contract for `challenge-f35e4ca5` (`阅读02｜科研图表解析`) under offline Linux constraints (`64` requests, `900s`, zero network, zero `fitz`/`pymupdf`/`cairosvg`/Windows COM dependencies).

It directly enforces **Organizer Revision v2** and addresses real scientific figure digitization failure modes:

1. **Supplied-Scope Fidelity**: Reads `/input/case.json` (`organizer_supplied_scope.required_actual_image_files` with SHA-256 verification and `required_literal_source_table_ids`) plus `/input/material.json`, standalone tables, and image files. Treats actual input files and literal tables as sole quantitative authority—never trusting stale domain labels.
2. **Accurate Calibration (读得准)**: Uses multi-point least-squares regression (`axis_calibrator_and_audit.py`) across `linear`, `log10`, `ln`, `reciprocal`, and dual-Y axes, outputting `R^2`, anchor residuals, and `+/-0.5 px` physical uncertainty intervals. Actively blocks treating a multi-decade log10 axis as linear (WebPlotDigitizer #282), flags truncated non-zero bar axes, and refuses exact numerical readout when fewer than 2 valid tick anchors exist (`UNSCALED_AXIS_REFUSE_EXACT_READING`).
3. **Complete Element Capture (提得全)**: Inventories axes, tick scales, units (`mV` vs `V`, `um` vs `nm`), series/legends, error-bar semantics (`SD`, `SEM`, `95% CI`, `IQR` vs `unreported_in_text`), significance notations (`*`, `**`, `ns`, `p`-values), microscopy scale bars, panel labels (`A, B, C`), and verbatim source table headers/cells without swapping columns or equating missing units to dimensionless.
4. **Evidence Tiering & PaperStory Back-Jumping (讲得清 & 交得出)**: Separates every statement into `directly_readable_from_image`, `reasonable_inference`, `undetermined_not_provided`, or `not_applicable` (for photographs/schematics without quantitative axes). Builds a PaperStory backbone-and-branch index connecting each provided figure/panel to its caption paragraph, literal source tables, and body mentions.
5. **Honest Unprovided-Object Handling**: Figures mentioned only in manuscript captions/text without supplied pixel files are logged explicitly under `missing_pixel_figure_gaps` and `uncertainties` without fabrication. Per Organizer Revision v2 `completion`, completing all supplied images and literal tables yields `contract_status: "answered"`; `partial` is reserved for unfinished *supplied* objects.

## 2. Execution Workflow (Deterministic 5-Step Pipeline)

Locate scripts relative to this `SKILL.md` directory (typically `/skills/weekly-skill/scripts/`).

### Step 1: Preflight & Scope Verification

```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --init --state /app/work/.budget_state.json
```

Read `/input/case.json`, `/input/task.md`, and `/input/organizer-supplied-scope.json` (if present). Confirm the exact target image file(s) and required literal source table IDs before reading visuals.

### Step 2: Deterministic Inventory, Literal Table Extraction & Cross-Modal Audit

Run the core parser immediately to inspect all supplied images (pure-stdlib PNG/JPEG/BMP/GIF/TIFF header reader + Pillow exposure/cropping diagnostics when installed + SVG XML parser), verify SHA-256 hashes against `organizer_supplied_scope`, extract all literal tables verbatim, identify unprovided caption-only figures, and generate initial `/app/output/result.json`, `/app/output/figure_table_bundle.json`, and `/app/output/figure_analysis_report.md`:

```bash
python3 -B scripts/figure_table_parser.py --input /input --output /app/output --work /app/work
```

### Step 3: Multimodal Visual Reading & Quantitative Axis Calibration (Agent Step)

Inspect the provided target image(s) directly alongside `/app/output/figure_table_bundle.json` and `references/axis_calibration_and_digitization.md`:

1. **Classify Figure Type First**: Distinguish quantitative plots (line/scatter, spectrum, bar/box/violin, heatmap), microscopy with scale bar, and qualitative photographs/schematics. For photographs/schematics lacking quantitative axes, explicitly set `reading_tier: "not_applicable"` for coordinate extraction—never invent numbers.
2. **Axis Scale, Unit & Truncation Check**: Identify `linear` vs `log10` vs `ln` vs dual-Y axes, exact unit prefixes (`mV` vs `V`, `nm` vs `µm`, `MPa` vs `GPa`), and non-zero baselines. Never assume an unlabeled axis or table column is dimensionless.
3. **Run Calibrated Digitization**: For any quantitative plot with visible tick marks, record `>= 2` (prefer `>= 3`) `(pixel, value)` tick anchors and call `axis_calibrator_and_audit.calibrate_axis_points(...)` (or `calibrate_scale_bar(...)` for microscopy) to compute reproducible physical values and `+/-0.5 px` uncertainty intervals. If ticks are missing or cropped, refuse exact digitization (`UNSCALED_AXIS_REFUSE_EXACT_READING`) and report qualitative trends only.
4. **Audit Figure vs. Literal Source Tables (`audit-figure-suite` & PaperStory)**:
   - Cross-check panel labels (`A`, `B`, `C`), legend-to-series mapping, error-bar definition (`SD` vs `SEM` vs `95% CI` vs `unreported`), sample sizes (`n`), and significance brackets against `parsed_tables` and caption paragraphs.
   - Preserve source table numbers and headers verbatim—never reorder columns or "correct" a source table by physical intuition; log conflicts under `uncertainties`.

### Step 4: Draft Readings Validation & Promotion

Write your structured visual reading draft to `/app/work/readings_draft.json` containing `status` (`answered | partial | insufficient_evidence | input_error`), `figure_readings[]` (with `file`, `chart_type`, `reading_tier`, `axis_calibrations[]`, `extracted_series[]`, `error_bar_semantics`, `significance_notes`), `table_summaries[]`, `claims[]` (each with `kind`, `evidence`, `source_id`, `location`), and `uncertainties[]`. Validate and promote atomically:

```bash
python3 -B scripts/figure_table_parser.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --validate-readings /app/work/readings_draft.json
```

The validator automatically rejects fabricated readings on unprovided-pixel figures, blocks log10-axis linear miscalibration, verifies required supplied figures are covered, and checks every `claim` has `kind`, `evidence`, `source_id`, and `location`.

### Step 5: Grounding Gate & Final Deliverable Audit

```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json --require figure_table_bundle.json --require figure_analysis_report.md
```

## 3. Hard Boundaries & Exception Matrix

| Condition | Required Action |
| --- | --- |
| Caption mentions Fig. 2–5, but only Fig. 1 PNG is supplied | Log Fig. 2–5 in `missing_pixel_figure_gaps` (`not_provided`) and `uncertainties`; never fabricate points/axes; keep `contract_status: "answered"` if Fig. 1 and all literal tables are complete |
| Photograph / schematic without quantitative axes | Describe visible components, labels, and layout; mark coordinate extraction `not_applicable` |
| Log10 axis spans decades (`1, 10, 100`) | Mandatory `scale="log10"`; linear fit is blocked by `LOG_AXIS_MISCALIBRATION_BLOCKED` |
| Missing ticks, cropped axis, or overexposed/blank image | Refuse exact scalar extraction (`UNSCALED_AXIS_REFUSE_EXACT_READING`); report qualitative features and `quality_flags` |
| Missing unit on axis or table header | Mark `unit_unreported`; never silently assume dimensionless or copy a unit from another column |
| Source table contradicts figure/text | Preserve verbatim table values without editing; report both locations in `claims` and `uncertainties` |

## 4. References & Examples

- `references/output_contract_spec.md` — Wenyon v2 `organizer_supplied_scope` & dual Harbor output contract.
- `references/axis_calibration_and_digitization.md` — Multi-point OLS calibration formulas, log-axis detection, dual-Y, microscopy scale bars, and uncertainty bounds.
- `references/figure_suite_and_paperstory_audit.md` — Cross-panel/cross-table consistency protocol and PaperStory backbone-branch anchoring.
- `examples/minimal_example.md` — Synthetic minimal invocation and calibration example.
