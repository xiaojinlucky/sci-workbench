# Minimal Synthetic Invocation Example (Illustrative Only)

> **Notice**: All inputs and outputs below are synthetic examples demonstrating the CLI workflow and calibration API. They are not formal test cases or benchmark results.

## 1. Synthetic Input Layout (`/input`)

- `case.json`: see `examples/sample_case.json` (includes `organizer_supplied_scope` with 1 supplied image and 1 required literal table ID).
- `material.json`: literal paragraphs + table block `SYN-T01`.
- `artifacts/SYN-fig1.png`: supplied target figure image.

## 2. Running the Parser & Axis Calibrator

```bash
python scripts/figure_table_parser.py --input /input --output /app/output --work /app/work
python -c "
import sys; sys.path.insert(0, 'scripts')
import axis_calibrator_and_audit as aca
print(aca.calibrate_axis_points([(100, 1.0), (200, 10.0), (300, 100.0)], scale='log10', query_pixels=[250], unit='mg/L'))
"
python scripts/figure_table_parser.py --input /input --output /app/output --work /app/work --validate-readings /app/work/readings_draft.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require figure_table_bundle.json --require figure_analysis_report.md
```

## 3. Illustrative Calibration Behavior

- Passing `scale='linear'` on `(100, 1.0), (200, 10.0), (300, 100.0)` triggers `ok: false`, `refusal_code: "LOG_AXIS_MISCALIBRATION_BLOCKED"`.
- Passing `scale='log10'` returns `ok: true`, `r_squared: 1.0`, and `query_pixels=[250]` -> `value: 31.622777` with explicit `uncertainty_half_px_interval`.
- Passing `< 2` tick anchors triggers `ok: false`, `refusal_code: "UNSCALED_AXIS_REFUSE_EXACT_READING"`.
