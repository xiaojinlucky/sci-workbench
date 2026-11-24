# Scientific Figure & Table Parser Skill (`challenge-f35e4ca5`)

Offline Python 3.12 skill for verifiable scientific figure and literal source table parsing under Harbor and public Revision v2 contracts.

## Key Capabilities

- **Organizer Revision v2 Scope Verification**: Reads `organizer_supplied_scope` (`required_actual_image_files` SHA-256 verification and `required_literal_source_table_ids`) plus `material.json`, CSV, JSON, Markdown, and PDF sources.
- **Zero-Dependency Image Inspection**: Pure-stdlib PNG/JPEG/BMP/GIF/TIFF header reader (dimensions & bit depth) enhanced automatically with Pillow luminance/exposure/edge-clipping diagnostics and SVG XML text/tick extraction when available.
- **Multi-Point Axis Calibration & Safety Guards**: Least-squares `linear`, `log10`, `ln`, `reciprocal`, and dual-Y calibration with `R^2`, `+/-0.5 px` physical uncertainty intervals, automatic log-vs-linear miscalibration blocking, truncated-axis alerts, unscaled-axis exact-reading refusal, and microscopy scale-bar conversion.
- **PaperStory Backbone-Branch Index & Cross-Modal Audit**: Links provided figures/panels to captions and literal tables, checks unit magnitude consistency (`mV` vs `V`, `nm` vs `µm`), classifies error-bar/significance semantics, and isolates unprovided caption-only figures without fabrication.

## Quick Start

```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/figure_table_parser.py --input /input --output /app/output --work /app/work
# After visual inspection & calibration, validate and promote draft readings:
python scripts/figure_table_parser.py --input /input --output /app/output --work /app/work --validate-readings /app/work/readings_draft.json
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require figure_table_bundle.json --require figure_analysis_report.md
```
