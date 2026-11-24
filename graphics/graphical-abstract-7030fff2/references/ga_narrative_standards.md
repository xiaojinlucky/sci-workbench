# Graphical Abstract Narrative, Adaptive Layout & Vector QA Standards

## 1. Four-Stage Narrative Structure
1. **`question`**: Scientific bottleneck, clinical gap, or baseline pathology.
2. **`method`**: Experimental intervention, engineered carrier, device, or analytical strategy.
3. **`result`**: Core quantitative or mechanistic readout grounded strictly in `/input/` data (`source_ref` required).
4. **`conclusion`**: Validated mechanistic takeaway and translational impact.

## 2. Journal Canvas Presets & Adaptive Reflow
- **Cell / Elsevier landscape (`1x4`)**: `174 × 96 mm` (or `174 × 104 mm` when subtitle + legend + takeaway bar are all present).
- **Compact / Narrow journals (`2x2` or `auto`)**: `120 × 95 mm` or `132 × 96 mm` — `layout: "auto"` switches to a `2×2` reading grid (`1 -> 2` top row, `2 -> 3` vertical connector, `3 -> 4` bottom row) so long labels wrap cleanly with zero collisions.
- **Typography floors**: Body/bullets `>= 6.5 pt`, headings/bar labels `>= 7.2–8.8 pt`, banner title `8.8–9.8 pt` (Arial / Liberation Sans / DejaVu Sans). Never silently shrink text below `6.4 pt` to hide overflow; fail fast with `LAYOUT_OVERFLOW`.

## 3. Scientific Visual Primitives & Color Consistency
- Bind every biological/physical entity to one consistent Cell/Okabe-Ito color in `entity_colors` (`#547FA3` carrier/primary, `#D77C70` barrier/inhibition, `#D19A45` trigger/stimulus, `#4E9187` outcome/gain, `#967A9C` pathway/control) and display it in the shared legend bar.
- Use typed mechanism arrows (`activate` solid arrow, `inhibit` coral T-bar, `flow` dashed open arrow) — never generic unmarked lines when polarity is known.
- In quantitative `mini_bars`, preserve exact units, draw error bars (`err`) when provided, mark exact `0` as an editable hollow circle on the baseline (never a fake positive bar), and support negative bars cleanly.

## 4. Canvas vs Caption Boundary & 10-Point Vector QA
- Keep statistical test descriptions, replicate `n` tables, and QC criteria in `*_caption_draft.md`, never cluttering the GA canvas or leaking backend tokens (`QC_`, `post_basic`, `(in-sample)`, `#RRGGBB`).
- Verify after export: zero raster `<image>` bloat, zero text/card overlaps, zero PNG border clipping, clean arrow-to-box alignment, selectable vector text in librsvg PDF, and same-source `.svg`/`.drawio`/`.png`/`.pdf` consistency.
