# Axis Calibration, Curve Digitization & Scale-Bar Protocol

## 1. Multi-Point Least-Squares Calibration (`axis_calibrator_and_audit.py`)

For any quantitative chart with visible axis ticks:
1. Record at least 2 (recommended `>= 3`) `(pixel_coord, tick_value)` pairs along the axis.
2. Select scale explicitly:
   - `linear`: `value = slope * px + intercept`
   - `log10`: `value = 10 ** (slope * px + intercept)` (all tick values strictly `> 0`)
   - `ln`: `value = exp(slope * px + intercept)` (all tick values strictly `> 0`)
   - `reciprocal`: `value = 1 / (slope * px + intercept)` (all tick values `!= 0`)
3. Report for every calibrated axis: `scale`, `unit`, `r_squared`, `max_anchor_residual`, and for every digitized point its `uncertainty_half_px_interval` (`[val(px - 0.5), val(px + 0.5)]`) tagged `pixel_digitized_approx`.

## 2. Hard Failure Guards

- **Log-Axis Miscalibration Guard (WebPlotDigitizer #282)**: If `>= 3` positive anchors span `>= 50x` and `log10` fit achieves `R^2 > 0.995` while `linear` fit has `R^2 < 0.92`, calling `scale="linear"` returns `ok: false` (`LOG_AXIS_MISCALIBRATION_BLOCKED`). Always inspect tick spacing before choosing scale.
- **Unscaled-Axis Refusal**: If `< 2` legible tick marks exist (cropped figure, schematic, missing axis labels), `calibrate_axis_points` returns `UNSCALED_AXIS_REFUSE_EXACT_READING`. Report qualitative patterns only; never guess absolute values.
- **Truncated-Axis Warning**: For bar or area charts where `min(tick_value) > 0`, emit `TRUNCATED_AXIS_WARNING` so reviewers note that bar height ratios exaggerate relative differences.
- **Dual-Y Axis Isolation**: Calibrate `y_left` and `y_right` as separate objects and explicitly bind each series to its corresponding axis and unit (`left_y` vs `right_y`).

## 3. Chart-Type Specific Reading Checklist

- **Line / Scatter / Kinetics**: Record axis units, series legend-to-color/marker mapping, peak/inflection coordinates, baseline, and curve overlap regions (mark occluded segments `unreadable_occluded`).
- **Spectra (XRD, FTIR, Raman, UV-Vis, NMR, MS)**: Record x-axis direction (note reversed wavenumbers `4000 -> 400 cm^-1` or ppm!), peak positions, relative intensities, and FWHM if legible. Do not assign chemical identity from pixels alone unless explicitly labeled in figure/text.
- **Statistical Bar / Box / Violin**: Distinguish central tendency (mean vs median), dispersion (`SD`, `SEM`, `95% CI`, `IQR`, `min-max` vs `unreported`), sample size `n`, and exact significance pairs (`*`, `**`, `ns`, `p`).
- **Microscopy / Morphology**: Locate scale bar pixel length and labeled value/unit, call `calibrate_scale_bar(...)`, and propagate `+/-0.5 px` uncertainty on both bar and measured feature.
