# Source Data Provenance & SFL 5-Gate Rules

1. **Bidirectional Mark-to-Row Traceability**: Every chart emits `*_provenance.json` recording source table filename, SHA-256 digest, mapped column names, exact 1-based CSV/JSON row indices, statistical unit, aggregation (`mean +- SD/SEM/CI95`), and `in_plot` booleans when Top-10 display caps apply.
2. **Zero vs Missing vs Sentinel Distinction**:
   - Explicit `0` / `0.0` is a valid observation and stays in `n`, mean, and range calculations.
   - Missing strings (`""`, `NA`, `N/A`, `NaN`, `ND`, `BDL`) are excluded from numeric calculations, logged in `gaps`, and shown as `#737373` `NA` cells in heatmaps — never imputed as zero.
   - Suspicious sentinel codes (`-999`, `999`, `-9999`, `9999`, `888`) raise warnings or hard blockers under `--fail-on-sentinel`.
3. **Negative Values**: Bar charts containing negative measurements automatically retain negative y-axis ranges and draw a `y = 0` reference line; `ylim(bottom=0)` is strictly restricted to non-negative data.
4. **No Fabricated Statistics**: Significance brackets and p-value annotations are drawn only when supplied explicitly from input statistical tables with `source_ref`.
5. **In-Place Revision Lineage**: `--restyle` records `restyle_from` (`spec_name@sha256[:12]`) and updates the source table SHA-256 while preserving persistent `figure_style.json` color identities.
