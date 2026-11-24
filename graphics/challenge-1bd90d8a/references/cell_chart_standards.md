# Cell-Oriented Scientific Chart Standards (Offline Production Baseline)

These defaults provide a consistent, publication-ready Cell-oriented visual system. Explicit manuscript or journal rules always take precedence when supplied in `/input/`.

## 1. Physical Dimensions & Export Lock
- Reference full-figure column widths: `cell_single` = 85 mm, `cell_1_5col` = 114 mm, `cell_double` = 174 mm.
- Always design at final physical placement with `savefig.bbox = "standard"` and `savefig.pad_inches = 0`. Never use `bbox_inches = "tight"`, which silently mutates canvas width/height and breaks multi-panel alignment.
- Export paired vector SVG (`svg.fonttype = "none"`), vector PDF (`pdf.fonttype = 42`), 300 dpi PNG preview, and native `.drawio` from the same layout.

## 2. Typography, Strokes & Legend Geometry
- Font stack: `Arial`, falling back cleanly to `Liberation Sans` / `DejaVu Sans` (and installed CJK sans-serif when CJK labels exist).
- Final-size type hierarchy: ticks & legends 7 pt; axis titles & uppercase bold panel labels (`A`, `B`, `C`) 8 pt; panel-centered titles (`loc="center"`) 8.5 pt. Never drop visible text below 6 pt.
- Strokes: axes 0.75 pt, outward ticks 0.5 pt, data lines 1.0–1.2 pt, error bars 0.8 pt. White background (`#FFFFFF`), no 3D effects, shadows, or decorative frames.
- Bottom single-row categorical legend sits `2–6 pt` below complete x-axis tick labels and axis title (`ncol = n_series`); never silently stack into `ncol = 2`.
- Multi-variable charts (e.g., DotPlot) split continuous colorbar and exact area-proportional size key into separate compact guide blocks.

## 3. Semantic Color System & Persistence
- Default Cell-tendency qualitative tokens: Steel Blue `#547FA3`, Coral `#D77C70`, Blue-Teal `#4E9187`, Ochre `#D19A45`, Purple `#967A9C`, Cyan `#66A9B4`, Sage `#83A47B`, Terracotta `#DF986D`.
- Background/NS points: `#B0B0B0`. Missing (`NA`): `#737373` with explicit `NA` key (strictly distinct from numeric `0.0`).
- Diverging quantitative scale: `#3F6FAE -> #F7F7F5 -> #C6544D` with explicit `center = 0`; monotonic intensity: `viridis`. Never use rainbow/jet.
- Persist entity-to-color bindings in `figure_style.json` keyed by `domain/entity` so categories keep identical colors across filtered views and sibling panels.

## 4. Figure-Suite Audit Gates
- Verify zero non-white ink on the outer 2-px border of exported PNGs (prevents Agg edge clipping).
- Block backend/QC terms (`QC_`, `post_basic`, `(in-sample)`, `preselector`, hex codes, script filenames) from visible figure text.
- Preserve clip paths on boundary-intersecting geometry in Draw.io while unboxing strictly interior vector lines, bars, and points for direct editing.
