---
name: scientific-data-viz-advisor
description: >-
  Use when designing, auditing, and exporting publication-grade scientific data
  visualizations from CSV/JSON tables or read-only SQLite databases inside offline
  Scientific Python + SQLite runtimes. Enforces PlotAgent 5 Candidate Gates,
  A/B/C scientific invariant fact sheets, Cell/Nature/Science physical column
  baselines (55-183 mm, 7-12 pt Arial/DejaVu Sans, bbox_inches=None,
  pdf.fonttype=42, panel-centered regular-weight titles, zero backend jargon on
  canvas), pure-stdlib multivariate OLS (HC3 SE, VIF, Jarque-Bera, Breusch-Pagan)
  and grouped assumption/hypothesis tests (Shapiro-Francia, Welch ANOVA,
  Kruskal-Wallis, Holm pairwise significance brackets), WCAG/CVD/grayscale
  palette verification, true disk SVG readback reconciliation (max_abs_diff),
  honest export_tier & gap_gate syncing, and standalone figure captions.
---

# Scientific Data Visualization & Publication Export Skill

- **Version**: `2.0.0` ｜ **Compatibility**: Pure Python `3.12+` stdlib (`sqlite3` built-in); optional Matplotlib `3.8+` ｜ **License**: MIT

Skill resources live at `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, data dictionaries, CSV/JSON tables, and SQLite snapshots). Working intermediates belong under `/app/work/`; final verified figures, captions, reconciliation audits, reproduction scripts, and `result.json` belong under `/app/output/`.

All bundled scripts are **Dual-Backend**: they run natively on pure Python 3.12 standard library (emitting editable, journal-sized Vector SVG figures with true disk geometry/text readback, full statistical assumption tests, and WCAG/CVD/grayscale audits, explicitly tagged `export_tier: "stdlib_vector_svg_fallback"`) and automatically synchronize **300 dpi PNG + editable PDF (`pdf.fonttype=42`, `bbox_inches=None`) + SVG** (`export_tier: "matplotlib_300dpi_png_pdf_svg"`) whenever Matplotlib is available. Never fabricate data values, never hardcode `max_abs_diff=0.0`, never claim unresolved units as verified, and never emit host absolute paths.

---

## Scope & Boundaries

- **In scope**: Data hygiene before plotting (exact deduplication, missing-vs-zero separation, unit resolution from `task.md` & column suffixes, read-only SQLite 1:N anti-inflation pre-aggregation & empty-group preservation), pure-stdlib statistical assumption verification & multivariable OLS / grouped hypothesis testing, data-structure-to-chart selection (6 grammars), WCAG/CVD/grayscale palette auditing, journal column-width canvas compliance, true disk-readback source-to-visual reconciliation, standalone publication captions, and reproducible CSV/JSON/SQLite scripts.
- **Out of scope**: Experimental design, novel statistical estimator development, full manuscript prose writing, slide-deck authoring, and decorative raster retouching.

---

## Stage 0: Freeze Scientific Invariants & A/B/C Fact Sheets

Before choosing a chart or running a renderer, inspect `/input/case.json`, `/input/task.md`, and the raw source table to freeze three immutable fact sheets (recorded in `<stem>_selection_audit.json` and `<stem>_audit.json`):

1. **Fact Sheet A (`scientific_invariants`)**:
   - Relative source filename (zero host paths) & SHA-256 fingerprint, field roles (`group_col`, `x_col`, `x_cols`, `value_col`, `series_col`), resolved measurement `unit` and `unit_source` (`task.md` > `column_suffix` > `spec` > `unresolved`), `predictor_units`, deduplication count, and missing-vs-true-zero handling (`exclude_without_zero_imputation`).
   - **Honest unit rule**: If a unit cannot be resolved from `task.md` or column suffixes, record `unit: "unresolved"` and log a `UNIT_UNRESOLVED` gap—never assert `"unit verified from source schema"`.
   - **Hard rule**: Visual layout fixes must **never** alter underlying values, sample membership, denominators, or statistical definitions.
2. **Fact Sheet B (`visual_grammar_retained_or_redesigned`)**:
   - Chosen chart grammar (`bar_with_points`, `boxplot_points`, `scatter_regression`, `multivariate_ols`, `line_with_band`, `heatmap`), rationale over rejected alternatives, semantic color role (`qualitative`, `sequential`, `diverging`), CVD-verified palette, and title/legend geometry contract.
3. **Fact Sheet C (`surface_specific_overrides`)**:
   - `target_surface` (`paper`, `presentation`, or `standalone`), journal column preset (`cell_single` `85 mm`, `cell_1_5col` `114 mm`, `cell_double` `174 mm`, `nature_single` `89 mm`, `nature_double` `183 mm`, `science_single` `55 mm`, `science_1_5col` `120 mm`, `science_double` `180 mm`), font size (`7–12 pt`), `bbox_inches=None`, `pdf.fonttype=42`, `svg.fonttype="none"`.

---

## Stage 1: PlotAgent 5 Candidate Gates & Statistical Assumption Loop

Every figure specification passes all five deterministic gates via `chart_selector_and_auditor.py` before rendering. See [Chart Selection Decision Tree & Five Gates](references/chart_selection_tree_and_five_gates.md).

### Gate 1 — Scientific & Data Fidelity + Read-Only SQLite Guard
- Inspect the real source table (CSV, JSON, or strict read-only `mode=ro` SQLite URI) rather than trusting column labels.
- For multi-table SQLite databases, select the true fact table, preserve empty dimension groups (`n=0`) without silent dropping, pre-aggregate 1:N child tables before `LEFT JOIN` to guarantee zero row inflation, and never average coded integer columns (`*_code`, `*_id`).
- Detect exact duplicate rows and missing tokens; never fill missing entries with `0`.
- Detect negative and cross-zero values (`value_min < 0 < value_max`) so the renderer reserves negative axis space and draws an explicit `y = 0` reference baseline.

### Gate 2 — Chart Grammar, Statistical Assumptions & Anti-Pattern Blocking

| Data Structure | Analysis Goal | Recommended Chart | Built-in Pure-Stdlib Statistics | Explicitly Blocked Anti-Patterns |
| --- | --- | --- | --- | --- |
| Categorical × Continuous | Group comparison / distribution | `boxplot_points`, `bar_with_points` | Per-group Shapiro-Francia $W'$, Brown-Forsythe homoscedasticity, Welch's ANOVA, Kruskal-Wallis $H$, Holm-Bonferroni pairwise tests & Hedges' $g$ with **drawn significance brackets** | `pie`, `donut`, `3d_bar`, `radar`, mean-only bar hiding points (`n >= 3`), truncated `ylim_min > 0` |
| Multivariate Continuous | Multivariable regression / effects | `multivariate_ols` | Full OLS + **HC3 robust SE**, standardized $\beta^*$ 95% CI, VIF, Jarque-Bera, Shapiro-Francia, Breusch-Pagan (Koenker) LM, per-variable mean/SD/IQR; **dual-panel SVG (a: forest plot, b: observed vs. predicted)** | Collapsing multi-predictor tasks into single-predictor bivariate scatter |
| Ordered Time / Date × Continuous | Trend / trajectory | `line_with_band` | Ordered numeric, ISO-date, and UTF-8 CJK x-axes with mean ± SD/SEM/95% CI bands | Side-by-side bars for continuous time series |
| Continuous × Continuous | Bivariate correlation | `scatter_regression` | OLS fit, Pearson $r$, $R^2$, optional group color encoding | Binned bar charts hiding bivariate spread |
| Feature Matrix ($M \times M$) | Pairwise correlation / pattern | `heatmap` | Pairwise Pearson $r$, per-feature descriptive stats, WCAG contrast-adaptive numeric cell labels | `jet`, `rainbow`, `hsv`, `turbo` colormaps |

### Gate 3 — Color Semantics, WCAG 2.1, CVD & Grayscale Audit
Run `palette_cvd_auditor.py` (invoked automatically by `chart_selector_and_auditor.py`) to verify:
- **Zero banned colormaps**: Hard block on `jet`, `rainbow`, `hsv`, `gist_rainbow`, `nipy_spectral`, `turbo`.
- **WCAG 2.1 contrast**: Non-text contrast checked against `#FFFFFF`; heatmap cell labels auto-select `#111827` or `#FFFFFF`.
- **CVD separation**: Linear-RGB Viénot/Machado simulation for **Protanopia**, **Deuteranopia**, and **Tritanopia** verifying CIELAB $\Delta E_{76}$ pairwise separation (blocking red-green collapse `< 9.0`).
- **Explicit Grayscale Audit**: Always emits a non-null boolean `grayscale_monotonic` (monotonic for sequential, symmetric-monotonic for diverging, luminance-checked + `grayscale_separable` for qualitative).

### Gate 4 — Journal Visual Baseline & Front-End Semantics
See [Cell Visual Baseline, Color Semantics & WCAG-CVD Specification](references/cell_visual_baseline_and_wcag_cvd.md):
- Fixed physical column widths (`85/114/174 mm` Cell; Nature/Science presets supported) with `bbox_inches=None` (**never** `bbox_inches="tight"`).
- `7–12 pt` sans-serif (`Arial` / `DejaVu Sans` / `Liberation Sans`), embedded editable fonts (`pdf.fonttype=42`, `svg.fonttype="none"`).
- Panel-centered regular-weight titles (`loc="center"`), explicit panel letters (`a`, `b`) on multi-panel figures, clean spines, single-row legends with `2–6 pt` clearance, and **zero backend jargon on canvas**.

### Gate 5 — Identity-Keyed Disk-Readback Reconciliation & Complete Statistical Tables
Every call to `publication_viz_builder.py` tags every plotted SVG element with explicit identity and fill attributes (`data-row-id`, `data-group-id`, `data-series-id`, `data-fill`), writes `<stem>_element_manifest.json`, and re-reads the written `<stem>.svg` from disk to verify **1-to-1 identity-matched geometry, x/y slot bounds, band polygon vertices, and heatmap fill-color mapping** (never sorting grouped y-values across groups; never hardcoding `max_abs_diff=0.0`). `auto_viz_from_input.py` persists all core statistical tables (`*_coefficients.csv`, `*_pairwise_stats.csv`, `*_group_stats.csv`, `*_corr_matrix.csv`, `*_hplc_reconcile.csv`) to both standalone CSV files and `result.json` (`metrics` + `results`) alongside embedded caption tables.

---

## Stage 2: Zero-Manual Entrypoint & Custom Spec Workflow

### Recommended Zero-Manual One-Command Entrypoint

```bash
SCRIPTS=/skills/weekly-skill/scripts

python3 -B "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 -B "$SCRIPTS/budget_guard.py" --init
python3 -B "$SCRIPTS/case_contract_sealer.py" discover --input-dir /input --out /app/work/case_discovery.json
python3 -B "$SCRIPTS/result_guard.py" --init --case-id "<CASE_ID>" --task-id "challenge-9562cc8d"
python3 -B "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Auto-discovers /input CSV/JSON/SQLite + task.md + case.json, resolves units,
# runs stat_assumptions + 5-Gate audit + publication_viz_builder + disk SVG readback
# + inline gap_gate + path sanitization + seals /app/output/result.json:
python3 -B "$SCRIPTS/auto_viz_from_input.py" \
  --input-dir /input \
  --out-dir /app/output \
  --stem fig1_response

python3 -B "$SCRIPTS/budget_guard.py" --tick "finalize"
python3 -B "$SCRIPTS/result_guard.py" --path /app/output/result.json --validate
python3 -B "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require fig1_response.svg --require fig1_response_audit.json
```

### Custom `viz_spec.json` Recipes (Advanced Override)

1. **Multivariate OLS (`multivariate_ols`)**: Set `"data_kind": "multivariate_continuous"`, `"analysis_goal": "multivariable_regression"`, `"chart_type": "multivariate_ols"`, `"x_cols": ["cement_kg_m3", "water_kg_m3", "age_days"]`, `"value_col": "compressive_strength_mpa"`, `"unit": "MPa"`, `"column_preset": "cell_double"`.
2. **Grouped Comparison with Significance Brackets (`boxplot_points` or `bar_with_points`)**: Set `"data_kind": "categorical_continuous"`, `"analysis_goal": "group_comparison"`, `"group_col": "species"`, `"value_col": "petal_length_cm"`, `"unit": "cm"`.
3. **Bivariate Association (`scatter_regression`)**: Set `"x_col"`, `"value_col"`, and optional `"group_col"`.
4. **Time-Series / ISO-Date / CJK Trajectory (`line_with_band`)**: Set `"x_col"`, `"value_col"`, optional `"series_col"`, `"error_type": "SD"|"SEM"|"95% CI"`.
5. **Correlation Matrix (`heatmap`)**: Set `"color_role": "diverging"` and optional `"matrix_cols"`.

---

## Stage 3: Honest Degradation, Failure Recovery & Deliverables

- **Matplotlib Unavailable (`export_tier: "stdlib_vector_svg_fallback"`)**: Pure-stdlib Vector SVG + geometry readback succeeds deterministically; `publication_viz_builder.py` and `auto_viz_from_input.py` automatically record `EXPORT_MATPLOTLIB_UNAVAILABLE_FALLBACK` in `result.json`'s `gaps` and `uncertainty` and set honest `status: "partial_with_gaps"` instead of silently claiming `gaps: []`.
- **Assumption Violations**: Jarque-Bera/Shapiro-Francia non-normality, Breusch-Pagan heteroscedasticity, Brown-Forsythe unequal variance, or VIF > 5 are surfaced in `diagnostics`/`statistical_tests` and copied into `gaps` & `uncertainty`, automatically activating HC3 robust SEs or Kruskal-Wallis + Mann-Whitney U inference.
- **Negative / Cross-Zero & Tiny-Sample Groups (`n < 3`)**: Full y-range preserved with dashed `y = 0` baseline; tiny groups retained with `n=` annotations and `unestimable_small_n` logged honestly.
- **Zero Host Path Leaks**: `case_contract_sealer.py` and `auto_viz_from_input.py` strip host paths so `*_auto_spec.json`, `*_audit.json`, and `result.json` contain relative filenames only.
- **Reproducible Script (`reproduce_figure.py`)**: Supports CSV, TSV, JSON, and strict read-only SQLite (`mode=ro` URI), verifying SHA-256, deduplication, bounds, and optional `--rerender` check.

Consult [Publication Export & Anti-Pattern Review Checklist](references/publication_export_and_antipattern_checklist.md) before finalizing any figure.
