---
name: source-bound-scientific-chart-builder
description: >-
  Use when generating, auditing, or revising publication-grade scientific data
  charts (bar+points, box/violin+points, line+band, scatter/volcano, heatmap,
  dotplot) from CSV/JSON tables. Enforces Figure Data Profile & 5-Gate checks,
  negative-value preservation, Cell physical mm lock (85/114/174 mm,
  bbox:standard), 2-6pt single-row bottom legend, persistent named colors,
  edge-clipping & backend-leak audit, and editable SVG/.drawio/PDF/PNG under
  /app/output.
---

# Source-Bound Scientific Data Chart Builder & Figure-Suite Auditor

Skill resources live at `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, source data tables, or existing chart specs/scripts). Work under `/app/work` and place final deliverables under `/app/output`.

## 1. Pre-Plot Scientific Decision Contract (5-Gate Check)

Before plotting any data table, run `source_data_chart_guard.py` to generate a `figure_data_profile` and verify five scientific gates:

1. **Statistical Unit**: Distinguish biological replicates (`n`) from pooled single cells or technical sub-samples; never treat pooled cells as biological `n`.
2. **Axis & Sign Semantics**:
   - Strictly non-negative magnitude bars anchor at `y = 0`.
   - Data containing **negative values** (e.g., log2 fold-change, ΔΔCt, Z-score) MUST preserve the negative range and draw an explicit `y = 0` reference line — NEVER clamp `ylim(bottom=0)`.
3. **Evidence Visibility (No Lazy Mean-Only Bars)**: When replicate `n >= 3`, overlay individual raw points AND `SD`/`SEM`/`CI95` error bars (`allow_summary_only` is blocked by default).
4. **Scale, Zero vs Missing, & Sentinel Hygiene**:
   - Separate explicit numeric `0.0` from missing (`NA`/`ND`/`BDL`) and sentinel codes (`-999`, `999`, `-9999`, `9999`). Pass `--fail-on-sentinel` when sentinel contamination must halt execution.
   - Block incompatible mixed units inside one value column (`MIXED_UNIT_BLOCKER`).
   - Only draw significance brackets when exact statistical results exist in `/input/` (`source_ref` required); never invent stars.
5. **Visual Encoding & Guide Separation**:
   - Persist named entity colors across panels via `--style-map /app/work/figure_style.json` keyed by `domain/entity` (never reshuffle colors when filtering categories or switching chart types).
   - Separate different scientific variables into distinct guide blocks (e.g., DotPlot continuous colorbar + exact area-proportional size legend).
   - Zero backend/QC vocabulary leaked onto visible chart text (no `QC_`, `post_basic`, `(in-sample)`, `preselector`, raw `#RRGGBB`, or `.py` filenames).

## 2. Six Supported Publication Chart Families

| `chart_type` | Scientific Use | Key Built-in Safeguards |
| --- | --- | --- |
| `bar_with_points` | Group magnitude comparison (`n >= 3`) | Negative-value safe + `y=0` line, raw points, `SD`/`SEM`/`CI95`, source-bound significance brackets |
| `box_with_points` / `violin_with_points` | Sample-level distribution & heterogeneity | Median line, jittered raw points, optional `y=0` ref line |
| `line_with_band` | Time course, dose-response, trajectory | Mean curve + shaded `SD`/`SEM`/`CI95` band + optional raw points |
| `scatter_volcano` | Differential expression / 2D correlation | Neutral `#B0B0B0` NS points, Cell steel-blue/coral hits, dashed thresholds, de-overlapped direct labels |
| `heatmap` | Matrix (genes × samples, pairwise effects) | Cell diverging `#3F6FAE -> #F7F7F5 -> #C6544D` (`center=0`) or `viridis`, `#737373` NA swatch separate from `0.0`, lossless label wrapping, **colorbar top/height-aligned to data body via GridSpec** |
| `dotplot` | Dual-encoding enrichment / expression | Exact area-proportional bubbles (`max(18, val * scale)`), two separate side guide blocks, Top-10 display cap with full `in_plot` boolean provenance |

## 3. Physical Dimensions, Editability & Suite Audit

- **Physical mm Lock (`bbox: standard`)**: Presets `cell_single` (85 mm), `cell_1_5col` (114 mm), `cell_double` (174 mm) with `savefig.bbox="standard"` and `savefig.pad_inches=0`. Never pass `bbox_inches="tight"`, which mutates declared physical dimensions.
- **Typography & Legend**: Arial/Liberation Sans/CJK fallback, 7 pt ticks/legends, 8 pt axis titles & uppercase bold panel letter (`A`, `B`, `C`), panel-centered title (`loc="center"`), 0.75/0.5/1.0/0.8 pt strokes. Single-row bottom categorical legend measured `2–6 pt` below complete x-axis decorations (never silent `ncol=2`).
- **Dual Editable Vector Outputs**:
  - Editable SVG (`svg.fonttype="none"`), editable vector PDF (`pdf.fonttype=42`), 300 dpi PNG, and native `.drawio` via `svg2drawio`.
  - Safe clip-path unboxing converts strictly interior open lines (error bars/curves), interior axis-aligned rectangles (bars/heatmap cells), and interior circles into **native editable Draw.io objects**, while strictly preserving clip paths on any boundary-intersecting shape (no fake clip deletion).
- **Offline Figure-Suite Audit (`figure_suite_audit.py`)**: Verifies physical SVG/PNG mm dimensions, scans PNG 2-px outer borders for edge clipping (catching Agg border-clip bugs), blocks backend-word leaks, reports Draw.io native editable ratio, and checks cross-panel `figure_style.json` color identity consistency.

## 4. Standard Execution Pipeline

```bash
SCRIPTS=/skills/weekly-skill/scripts

# Step 1: Probe environment, initialize budget & result.json
python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "<TASK_ID>"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Step 2: Audit raw source table (Figure Data Profile + 5-Gate + sentinel/missing/zero check)
python3 "$SCRIPTS/source_data_chart_guard.py" \
  --table /input/data.csv --group-col group --value-col value --series-col condition \
  --fail-on-sentinel --out /app/work/source_prov.json

# Step 3: Build Cell-locked vector chart with persistent cross-panel color map + optional --case-json auto-seal
python3 "$SCRIPTS/cell_chart_builder.py" \
  --spec /app/work/chart_spec.json \
  --style-map /app/work/figure_style.json \
  --case-json /input/case.json \
  --out-dir /app/output --stem data_chart

# Step 3b (Optional In-Place Restyle / Data Refresh without losing provenance)
python3 "$SCRIPTS/cell_chart_builder.py" \
  --restyle /app/work/chart_spec.json \
  --new-table /input/updated_data.csv \
  --override-json '{"column_preset":"cell_double","error_bar_type":"CI95"}' \
  --style-map /app/work/figure_style.json \
  --out-dir /app/output --stem data_chart_revised

# Step 4: Palette CVD/WCAG audit + Figure-Suite physical/edge-clip/leak/consistency audit
python3 "$SCRIPTS/check_palette.py" "#547FA3,#D77C70,#4E9187,#D19A45" --role categorical --mark bar
python3 "$SCRIPTS/figure_suite_audit.py" \
  --svg /app/output/data_chart.svg --png /app/output/data_chart.png \
  --drawio /app/output/data_chart.drawio --provenance /app/output/data_chart_provenance.json \
  --declared-width-mm 114 --out /app/work/suite_audit.json

# Step 5: Seal result.json preserving original output_contract fields and run final_audit
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json \
  --patch-json '{"artifacts":["data_chart.svg","data_chart.drawio","data_chart.png","data_chart.pdf","data_chart_provenance.json"]}' --finalize
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output \
  --require data_chart.svg --require data_chart.drawio --require data_chart.png --require data_chart.pdf --require data_chart_provenance.json
```

## 5. Failure Handling & Honest Boundaries

- Missing columns, mixed units, sentinel codes (`--fail-on-sentinel`), or lazy mean-only bars (`n >= 3`) halt cleanly at `source_data_audit` with explicit diagnostics — never fabricate values or impute `NA` as `0`.
- If `PNG_EDGE_CLIPPING` or `PHYSICAL_DIM_DRIFT` triggers in `figure_suite_audit.py`, adjust axes margins or choose a wider Cell column preset (`85 -> 114 -> 174 mm`) and re-render; never fix clipping by applying `bbox_inches="tight"` or shrinking fonts below 6 pt.
- `cell_chart_builder.py` lazily imports `matplotlib`/`numpy` so CLI inspection (`--help`) and `source_data_chart_guard.py` run cleanly in stdlib-only containers; if `matplotlib` is absent at render time, it exits with structured `MISSING_MATPLOTLIB_DEPENDENCY` JSON instead of crashing silently.
- Technical lint and structural editability do not substitute for visual inspection of rendered PNG/PDF outputs; report any unverified visual or domain aspect honestly in `result.json`.
