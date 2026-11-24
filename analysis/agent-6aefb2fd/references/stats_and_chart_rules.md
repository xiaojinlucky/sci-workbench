# Statistical Routing & Publication Visualization Standards

## 1. Assumption-Driven Statistical Decision Tree

1. **Pre-Test Hygiene**:
   - Block continuous summary on low-cardinality coded integer columns (`cultivar_code`, `species_code`).
   - Drop exact duplicates and log every excluded missing/unparseable row (zero silent imputation).
2. **Assumption Checks**:
   - Normality per group: Shapiro-Wilk (SciPy backend) or Jarque-Bera screen (pure-stdlib backend). Flag `n < 3` as `underpowered_n_lt_3` and constant groups (`sd == 0`) as `zero_variance_degenerate`.
   - Variance equality: Brown-Forsythe median-centered Levene test.
3. **Method Routing**:
   - Normal + equal variance -> One-way ANOVA (`eta_squared`) + Student's t-test + 95% difference CI.
   - Normal + unequal variance -> **Welch's ANOVA** + **Welch's t-test with Welch-Satterthwaite df** + 95% difference CI.
   - Non-normal, `n < 3`, or zero variance -> **Kruskal-Wallis** (`epsilon_squared`) + **Mann-Whitney U** + **Hodges-Lehmann median shift** + **Cliff's delta**.
4. **Multiple Testing & Dual Effect Sizes**:
   - Always apply Holm-Bonferroni correction across pairwise comparisons (`p_holm`).
   - Report both standardized mean effect (`Hedges' g`) and distribution-free effect (`Cliff's delta`, `Hodges-Lehmann shift`).

## 2. Publication-Grade Visual Invariants

- **Fixed Physical Dimensions**: Default Cell 1.5-column width (`114 x 82 mm`), exported with `bbox_inches=None` (never let `tight` crop silently alter declared canvas dimensions).
- **Centered Panel Title & Clean Axes**: Title centered on the plotting area (`loc="center"`), white background, top/right spines hidden, subtle horizontal gridlines.
- **Full Data Visibility**: Overlay jittered raw individual points over mean + 95% CI error bars; include exact per-group `n` on x-axis ticks.
- **Signed-Value Integrity**: When negative observations or CI bounds exist, render an explicit `y = 0` baseline and audit `value_range_covered: true` so no negative value or whisker is clipped.
- **Low-Saturation Accessible Palette**: Use the restrained Cell-inspired qualitative palette (`#547FA3`, `#D77C70`, `#4E9187`, `#D19A45`, `#967A9C`, `#66A9B4`).
