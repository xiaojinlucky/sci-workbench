# Scientific Data Visualization & Publication Export Skill (`challenge-9562cc8d`)

A Dual-Backend (pure Python 3.12 standard library Vector SVG renderer + optional Matplotlib 300 dpi PNG/PDF/SVG synchronizer) Skill for publication-grade scientific data visualization inside offline Scientific Python + SQLite environments.

## Structure

- `SKILL.md`: Executable visualization workflow integrating PlotAgent 5 Candidate Gates, A/B/C fact sheets, Cell visual baselines, and Harbor M1–M6 guards.
- `references/`:
  - `chart_selection_tree_and_five_gates.md`: Data-structure-to-chart decision tree, anti-pattern blocks, and 5-Gate rules.
  - `cell_visual_baseline_and_wcag_cvd.md`: Cell physical dimensions (`85/114/174 mm`), typography (`7-12 pt`), color semantics, and WCAG/CVD/grayscale rules.
  - `publication_export_and_antipattern_checklist.md`: Eight critical failure blockers and export reconciliation checklist.
- `scripts/`:
  - Core Harbor helpers (`preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py`).
  - `palette_cvd_auditor.py`: Pure-stdlib WCAG 2.1 contrast, Protanopia/Deuteranopia/Tritanopia CIELAB Delta-E, grayscale monotonicity, and rainbow-colormap blocker.
  - `chart_selector_and_auditor.py`: Data-aware 5-Gate chart selector, anti-pattern blocker, and A/B/C fact sheet builder.
  - `publication_viz_builder.py`: Dual-Backend renderer for `bar_with_points`, `boxplot_points`, `scatter_regression`, `line_with_band`, and `heatmap`, with cross-zero handling, `source_to_visual_reconciliation`, standalone `caption.md`, and linear `reproduce_figure.py`.
- `examples/`: Minimal CSV/JSON invocation example (zero `.tsv`, zero precomputed case answers).

## Quick Verification

```bash
python3 scripts/chart_selector_and_auditor.py --spec examples/viz_spec_example.json
python3 scripts/publication_viz_builder.py --spec examples/viz_spec_example.json --out-dir /tmp/viz_demo --stem demo_fig
```
