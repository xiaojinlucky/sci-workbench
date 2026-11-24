---
name: scientific-data-viz-advisor
description: >-
  Use when designing, auditing, and exporting publication-grade scientific data
  visualizations from CSV/JSON tables or SQLite databases inside offline
  Scientific Python + SQLite runtimes. Enforces PlotAgent 5 Candidate Gates,
  A/B/C scientific invariant fact sheets, Cell-tendency visual baselines
  (85/114/174 mm physical widths, 7-12 pt Arial/DejaVu Sans, bbox_inches=None,
  pdf.fonttype=42, panel-centered regular-weight titles, 2-6 pt legend
  clearance, zero backend jargon on canvas), pure-stdlib WCAG/CVD/grayscale
  palette verification, full negative/cross-zero y-axis coverage,
  source-to-visual numeric reconciliation, and standalone figure captions.
---

# Scientific Data Visualization & Publication Export Skill

Skill resources live at `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, data dictionaries, CSV/JSON tables, and SQLite snapshots). Working intermediates belong under `/app/work/`; final verified figures, captions, reconciliation audits, reproduction scripts, and `result.json` belong under `/app/output/`.

All bundled scripts are **Dual-Backend**: they run natively on pure Python 3.12 standard library (emitting editable, Cell-sized Vector SVG figures plus full WCAG/CVD/reconciliation audits with zero third-party packages) and automatically synchronize **300 dpi PNG + editable PDF (`pdf.fonttype=42`, `bbox_inches=None`) + SVG** whenever Matplotlib is available. Never fabricate data values, never ship truncated or unverified charts, and never hide data hygiene steps.

---

## Scope & Boundaries

- **In scope**: Data hygiene before plotting (exact deduplication, missing-vs-zero separation, unit binding), data-structure-to-chart selection, visual encoding audit (position, length, color, shape, faceting), WCAG/CVD/grayscale color verification, Cell-tendency physical canvas and typography compliance, exact source-to-visual numeric reconciliation, standalone publication captions, and reproducible plotting scripts.
- **Out of scope**: Experimental design, novel statistical method development, full manuscript prose writing, slide-deck authoring, and decorative raster image retouching.

---

## Stage 0: Freeze Scientific Invariants & A/B/C Fact Sheets

Before choosing a chart or running a renderer, inspect `/input/case.json`, `/input/task.md`, and the raw source table to freeze three immutable fact sheets (recorded in `viz_selection.json` and `<stem>_audit.json`):

1. **Fact Sheet A (`scientific_invariants`)**:
   - Exact source file & SHA-256 fingerprint, field roles (`group_col`, `x_col`, `value_col`, `series_col`), measurement units from the data dictionary, statistical inference unit (biological sample vs. technical replicate), category ordering, deduplication count, and missing-vs-true-zero handling (`exclude_without_zero_imputation`).
   - **Hard rule**: Visual layout fixes must **never** alter underlying values, sample membership, denominators, or statistical definitions.
2. **Fact Sheet B (`visual_grammar_retained_or_redesigned`)**:
   - Chosen chart grammar (`bar_with_points`, `boxplot_points`, `scatter_regression`, `line_with_band`, `heatmap`), rationale over rejected alternatives, semantic color role (`qualitative`, `sequential`, `diverging`), CVD-verified palette, and title/legend geometry contract.
3. **Fact Sheet C (`surface_specific_overrides`)**:
   - `target_surface` (`paper`, `presentation`, or `standalone`), exact physical canvas dimensions (`85 / 114 / 174 mm`), font size (`7–12 pt`), `bbox_inches=None`, `pdf.fonttype=42`, `svg.fonttype="none"`.
   - For `presentation`, require actual slide picture-frame dimensions (`frame_width_mm`, `frame_height_mm`) before judging placed legibility; without a real frame, mark presentation legibility as precheck-only rather than claiming pass.

---

## Stage 1: PlotAgent 5 Candidate Gates

Every figure specification (`viz_spec.json`) must pass all five deterministic gates via `chart_selector_and_auditor.py` before rendering. See [Chart Selection Decision Tree & Five Gates](references/chart_selection_tree_and_five_gates.md).

### Gate 1 — Scientific & Data Fidelity
- Inspect the real source table (CSV, JSON, or read-only SQLite) rather than trusting column labels.
- Detect exact duplicate rows and missing tokens; never fill missing entries with `0`.
- Detect negative and cross-zero values (`value_min < 0 < value_max`) so the renderer reserves negative axis space and draws an explicit `y = 0` reference baseline.

### Gate 2 — Chart Grammar & Anti-Pattern Blocking
Match data structure and analytical intent to visual grammar:

| Data Structure | Analysis Goal | Recommended Chart | Explicitly Blocked Anti-Patterns |
| --- | --- | --- | --- |
| Categorical × Continuous | Group comparison / distribution | `boxplot_points`, `bar_with_points` | `pie`, `donut`, `3d_bar`, `radar`, mean-only bar hiding points (`n >= 3`), truncated `ylim_min > 0` |
| Ordered Time × Continuous | Trend / trajectory | `line_with_band` | Side-by-side bars for continuous time series |
| Continuous × Continuous | Correlation / regression | `scatter_regression` | Binned bar charts hiding bivariate spread |
| Feature Matrix ($M \times M$) | Pairwise correlation / pattern | `heatmap` | `jet`, `rainbow`, `hsv`, `turbo` colormaps |

### Gate 3 — Color Semantics, WCAG 2.1, CVD & Grayscale Audit
Run `palette_cvd_auditor.py` (invoked automatically by `chart_selector_and_auditor.py`) to verify:
- **Zero banned colormaps**: Hard block on `jet`, `rainbow`, `hsv`, `gist_rainbow`, `nipy_spectral`, `turbo`.
- **WCAG 2.1 contrast**: Non-text contrast checked against canvas `#FFFFFF`; heatmap cell numeric labels auto-select `#111827` or `#FFFFFF` for maximum contrast.
- **Color Vision Deficiency (CVD)**: Linear-RGB Viénot/Machado simulation for **Protanopia**, **Deuteranopia**, and **Tritanopia** verifying CIELAB $\Delta E_{76}$ pairwise separation (blocking red-green collapse `< 9.0`).
- **Grayscale readability**: Verifies monotonic relative luminance for sequential ramps and flags near-identical luminance pairs in qualitative palettes.

### Gate 4 — Cell Visual Baseline & Front-End Semantics
See [Cell Visual Baseline, Color Semantics & WCAG-CVD Specification](references/cell_visual_baseline_and_wcag_cvd.md):
- **Physical column widths**: Single column `85 mm`, 1.5-column `114 mm`, double column `174 mm` with natural content-driven aspect ratios (never blindly forcing squares).
- **Typography**: `7–12 pt` sans-serif (`Arial` / `DejaVu Sans` / `Liberation Sans`), embedded editable fonts (`pdf.fonttype=42`, `svg.fonttype="none"`).
- **Fixed physical canvas**: Always save with `bbox_inches=None` (`bbox_mode="standard"`). **Strictly forbid** `bbox_inches="tight"`, which silently shrinks or distorts final column width and typography scale.
- **Panel-centered title**: Short, clear English main title in **regular weight**, horizontally centered on the **panel plotting area** (`ax.set_title(..., loc="center", fontweight="normal")`), never left-bolded or centered over legend whitespace.
- **Clean chrome & legend clearance**: Remove top/right spines, omit distracting background gridlines, keep categorical legends single-row by default with `2–6 pt` clearance from axes/labels.
- **Zero backend jargon on canvas**: Never place internal processing flags (`selected_flag`, `pass_filter`, `qc_branch`, `route_enum`, backend names) or auto-generated pipeline footers onto visible figure axes, titles, or legends.

### Gate 5 — Source-to-Visual Reconciliation & Readback
Completed during rendering by `publication_viz_builder.py` (see Stage 3).

---

## Stage 2: Audit Spec & Build Publication Figures

Create `/app/work/viz_spec.json` and run both audit and build steps:

```bash
SCRIPTS=/skills/weekly-skill/scripts

# 1. Run 5-Gate selection, anti-pattern, and WCAG/CVD palette audit
python3 "$SCRIPTS/chart_selector_and_auditor.py" \
  --spec /app/work/viz_spec.json \
  --out /app/work/viz_selection.json

# 2. Optional standalone palette audit for custom color sets
python3 "$SCRIPTS/palette_cvd_auditor.py" \
  "#0072B2,#D55E00,#009E73,#CC79A7" \
  --role qualitative --mark bar --out /app/work/palette_audit.json

# 3. Build editable Vector SVG (+ 300 dpi PNG & editable PDF when Matplotlib is present)
python3 "$SCRIPTS/publication_viz_builder.py" \
  --spec /app/work/viz_spec.json \
  --out-dir /app/output \
  --stem fig1_response
```

### Example `viz_spec.json` Fields & Chart-Specific Recipes

1. **Cross-Zero / Multi-Group Comparison (`bar_with_points` or `boxplot_points`)**:

```json
{
  "table": "/input/data.csv",
  "sqlite_table": null,
  "data_kind": "categorical_continuous",
  "analysis_goal": "group_comparison",
  "chart_type": "bar_with_points",
  "group_col": "treatment",
  "value_col": "delta_response",
  "unit": "a.u.",
  "error_type": "SD",
  "column_preset": "cell_1_5col",
  "font_size_pt": 8.0,
  "title": "Response change across treatment arms",
  "xlabel": "Treatment arm",
  "ylabel": "Delta response",
  "stat_summary": "Group means +/- SD with individual observations overlaid"
}
```

2. **Bivariate Association (`scatter_regression`)**: Set `"data_kind": "bivariate_continuous"`, `"analysis_goal": "correlation"`, `"chart_type": "scatter_regression"`, `"x_col": "petal_length_cm"`, `"value_col": "petal_width_cm"`, and optional `"group_col": "species"` for CVD-safe category color encoding with a single-row bottom legend.
3. **Time-Series Trajectory (`line_with_band`)**: Set `"data_kind": "time_series"`, `"analysis_goal": "trend"`, `"chart_type": "line_with_band"`, `"x_col": "time_h"`, `"value_col": "delta_response"`, optional `"series_col"`, and `"error_type": "SEM"` or `"95% CI"`.
4. **Correlation / Feature Matrix (`heatmap`)**: Set `"data_kind": "matrix"`, `"analysis_goal": "correlation"`, `"chart_type": "heatmap"`, `"color_role": "diverging"`, and `"matrix_cols": ["alcohol", "malic_acid", "flavanoids", "proline"]` (omitting `matrix_cols` auto-selects up to 8 non-ID numeric columns).
5. **Read-Only SQLite Direct Plotting**: Point `"table": "/input/scientific_uci_multi.sqlite"` and set `"sqlite_table": "wine_samples"`; all SQLite queries open in strict `mode=ro` read-only URI mode and record the full database SHA-256.

### Failure Recovery & Edge-Case Handling

- **Negative / Cross-Zero Data**: Never enforce `ylim(0, max)`; `publication_viz_builder.py` expands `actual_ylim` symmetrically across negative values, positive outliers, and error-bar endpoints while adding a dashed `y = 0` reference line.
- **Zero-Variance or Tiny-Sample Groups (`n < 3`)**: Retained honestly in the figure with `n=` annotated on the x-axis and `sd=0` recorded in `source_to_visual_reconciliation`; never dropped silently or padded with synthetic jitter noise.
- **High-Cardinality Categories (`n_groups > 10`)**: Gate 2 issues a crowding warning; split into faceted panels or horizontal bars rather than shrinking text below `7 pt`.
- **Blocked Anti-Patterns**: When `chart_selector_and_auditor.py` returns exit code `1` (e.g., `pie`, `jet`, truncated bar axis, or backend jargon on canvas), repair `viz_spec.json` using the returned `recommended_charts` or `recommended_fallback` palette and re-run—never bypass a failed gate.

---

## Stage 3: Mandatory Source-to-Visual Reconciliation & Standalone Caption

Every call to `publication_viz_builder.py` automatically writes and validates:

1. **`<stem>.svg`** (always emitted via pure stdlib; synchronized with Matplotlib when available) + **`<stem>.png` (300 dpi)** & **`<stem>.pdf` (editable vector)** when Matplotlib is installed.
2. **`<stem>_audit.json`**: Contains `source_to_visual_reconciliation` verifying:
   - `rows_raw`, `exact_duplicates_removed`, `missing_excluded`, `clean_source_points == plotted_points_count`;
   - `all_raw_points_within_ylim == true` and `points_clipped_outside_ylim == 0` (hard safeguard against negative/outlier truncation);
   - `all_error_bars_within_ylim == true` and `zero_baseline_drawn` for signed/cross-zero values;
   - Exact per-group (`n`, `mean`, `sd`, `median`, `q1`, `q3`, `min`, `max`), regression (`r`, `R^2`), or heatmap matrix values with `max_abs_diff == 0.0`.
3. **`<stem>_caption.md`**: Self-contained caption readable independently of main prose, stating exact units, per-group `n`, deduplication/missingness counts, error bar definition (`SD` / `SEM` / `95% CI`), visual encodings, physical dimensions, and source SHA-256.
4. **`reproduce_figure.py`**: Concise linear imperative script verifying source SHA-256 and plotted bounds.

Consult [Publication Export & Anti-Pattern Review Checklist](references/publication_export_and_antipattern_checklist.md) before finalizing any figure. If visual readback of rendered raster images is not supported in the runtime, record that readback gap honestly instead of claiming visual inspection passed.

---

## Stage 4: Harbor Grounding, Output Contract Sealing & M1–M6 Execution Loop

- **Dual-Backend Transparency**: In minimal pure-stdlib containers without Matplotlib, `publication_viz_builder.py` renders editable vector `.svg` with exact physical mm dimensions and full `source_to_visual_reconciliation`; when Matplotlib is present, it additionally exports 300 dpi `.png` and Type-42 `.pdf`. Always record the actual backend and any raster readback limit in `result.json`.
- **CJK / Date / Continuous-Regression Boundaries**: Support UTF-8 Chinese/Unicode axis labels, ISO date strings on x-axes, and continuous multivariable regression scatter / correlation heatmaps (e.g., Concrete strength vs cement/age with exact $r$ and $R^2$ annotations).

```bash
SCRIPTS=/skills/weekly-skill/scripts

python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/case_contract_sealer.py" discover --input-dir /input --out /app/work/case_discovery.json
python3 "$SCRIPTS/result_guard.py" --init --case-id "<CASE_ID>" --task-id "challenge-9562cc8d"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Build viz_spec.json -> run chart_selector_and_auditor.py -> run publication_viz_builder.py ...

python3 "$SCRIPTS/budget_guard.py" --tick "finalize"
python3 "$SCRIPTS/case_contract_sealer.py" seal \
  --case-json /input/case.json \
  --result-json /app/output/result.json \
  --task-id "challenge-9562cc8d" \
  --status ok \
  --payload-json /app/output/fig1_response_audit.json \
  --artifacts "fig1_response.svg,fig1_response_caption.md,fig1_response_audit.json,reproduce_figure.py"
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --validate
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require fig1_response.svg --require fig1_response_audit.json
```

Verify `result.json` carries all required `output_contract` keys, exact `case_id`, finite numeric metrics, honest status (`ok` vs `partial_with_gaps`), and complete provenance before ending the run.
