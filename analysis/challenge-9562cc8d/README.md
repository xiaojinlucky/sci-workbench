# Scientific Data Visualization & Publication Export Skill (`challenge-9562cc8d`)

- **Version**: `2.0.0`
- **Runtime Compatibility**: Pure Python `3.12+` standard library (`sqlite3` built-in, zero mandatory third-party packages); optional Matplotlib `3.8+` for synchronized 300 dpi PNG + editable Type-42 PDF export.
- **License**: MIT (reusable scripts, palettes, journal presets, and audit rules).

## Overview

A Dual-Backend Skill for publication-grade scientific data visualization inside offline Scientific Python + SQLite environments. It enforces PlotAgent 5 Candidate Gates, A/B/C scientific invariant fact sheets, Cell/Nature/Science physical column presets, pure-stdlib multivariate OLS & hypothesis testing, WCAG/CVD/grayscale palette auditing, true disk SVG geometry/text readback (`max_abs_diff`), and automatic Harbor contract sealing.

## Structure

- `SKILL.md`: Executable visualization workflow integrating PlotAgent 5 Candidate Gates, A/B/C fact sheets, Cell/Nature/Science visual baselines, statistical assumption testing, and Harbor M1–M6 guards.
- `references/`:
  - `chart_selection_tree_and_five_gates.md`: Data-structure-to-chart decision tree (all 6 grammars including `multivariate_ols`), anti-pattern blocks, and 5-Gate rules.
  - `cell_visual_baseline_and_wcag_cvd.md`: Cell/Nature/Science physical widths (`55–183 mm`), typography (`7-12 pt`), color semantics, and WCAG/CVD/grayscale rules.
  - `publication_export_and_antipattern_checklist.md`: Eight critical failure blockers, `export_tier` fallback rules, and disk-readback reconciliation checklist.
- `scripts/`:
  - Core Harbor helpers (`preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py`, `case_contract_sealer.py`).
  - `stat_assumptions.py`: Pure-stdlib multivariate OLS (classical & HC3 robust SE, VIF, Jarque-Bera, Breusch-Pagan Koenker LM, Shapiro-Francia W', descriptive stats) and grouped inference (Welch's ANOVA, Kruskal-Wallis H, Brown-Forsythe, pairwise Welch's t / Mann-Whitney U with Holm-Bonferroni correction and Hedges' g).
  - `palette_cvd_auditor.py`: Pure-stdlib WCAG 2.1 contrast, Protanopia/Deuteranopia/Tritanopia CIELAB Delta-E, non-null `grayscale_monotonic` / `grayscale_separable` audit, and rainbow-colormap blocker.
  - `chart_selector_and_auditor.py`: Data-aware 5-Gate chart selector, anti-pattern blocker, journal width presets, and A/B/C fact sheet builder.
  - `publication_viz_builder.py`: Dual-Backend renderer for `bar_with_points`, `boxplot_points` (with significance brackets), `scatter_regression`, `multivariate_ols` (dual-panel forest + observed-vs-predicted), `line_with_band` (numeric/ISO-date/CJK), and `heatmap`, with true disk SVG readback `max_abs_diff`, standalone `caption.md`, and CSV/JSON/read-only-SQLite `reproduce_figure.py`.
  - `auto_viz_from_input.py`: Zero-manual `/input -> /app/output` orchestrator with unit resolution from `task.md` & column suffixes, read-only SQLite 1:N anti-inflation aggregation, empty-group preservation, inline `gap_gate`, path sanitization, and `result.json` sealing.
- `examples/`: Minimal CSV/JSON invocation examples (zero `.tsv`, zero precomputed case answers).

## Quick Verification

```bash
python3 -B scripts/chart_selector_and_auditor.py --spec examples/viz_spec_example.json
python3 -B scripts/publication_viz_builder.py --spec examples/viz_spec_example.json --out-dir /tmp/viz_demo --stem demo_fig
```
