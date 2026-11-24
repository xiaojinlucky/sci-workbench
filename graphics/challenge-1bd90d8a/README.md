# Source-Bound Scientific Data Chart Builder

Offline scientific Skill for generating, auditing, and revising publication-grade Cell-oriented scientific data charts with strict source-data provenance, physical dimension lock, and editable vector outputs.

## Core Capabilities
- **Figure Data Profile & 5-Gate Pre-Plot Guard (`scripts/source_data_chart_guard.py`)**: Profiles statistical unit, replicate `n`, negative values, explicit `0.0` vs missing (`NA`/`ND`), sentinel codes (`-999`/`999`), mixed units, and blocks lazy mean-only bars (`n >= 3`).
- **6 Publication Chart Families (`scripts/cell_chart_builder.py`)**: `bar_with_points` (negative-safe + `y=0` line + SD/SEM/CI95 + source-bound significance brackets), `box_with_points`/`violin_with_points`, `line_with_band`, `scatter_volcano`, pure-vector `heatmap` (diverging `center=0`, `#737373` `NA`, GridSpec body-aligned pure-vector colorbar), and `dotplot` (dual separate colorbar + area-proportional size guides, Top-10 cap with `in_plot` provenance).
- **Persistent Named Color Maps & In-Place Revision**: `--style-map figure_style.json` prevents color reshuffling across panels; `--restyle` updates styles/column widths (`85/114/174 mm`) or refreshed tables while logging `restyle_from` lineage.
- **Native Draw.io Editability (`scripts/svg2drawio/`)**: Unboxes strictly interior lines, bars, and points into native Draw.io objects while preserving clip paths on boundary-intersecting shapes.
- **Offline Figure-Suite Audit (`scripts/figure_suite_audit.py`)**: Verifies physical mm lock (`savefig.bbox="standard"`), scans PNG 2-px borders for Agg edge clipping, blocks backend/QC text leaks, and validates cross-panel color consistency.

## Quick Verification
```bash
python3 scripts/source_data_chart_guard.py --table examples/observations.json --group-col group --value-col value --series-col condition --out /tmp/prov.json
```
See `SKILL.md` for the full `/input` -> `/app/work` -> `/app/output` Harbor workflow and `references/` for Cell standards and provenance rules.
