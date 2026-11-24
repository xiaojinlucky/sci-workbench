---
name: statistical-inference-interpreter
description: >-
  Use when conducting descriptive and inferential statistical analysis on
  scientific tables or SQLite databases, requiring pre-test data hygiene
  (duplicate removal, missing-vs-zero separation, encoded integer blocking,
  inference-unit locking), assumption diagnostics (normality and Brown-Forsythe
  homogeneity), design-matched test routing (Student/Welch t, One-way/Welch
  ANOVA, Kruskal-Wallis, Mann-Whitney U, Paired t/Wilcoxon, Pearson/Spearman,
  Chi-square/Fisher exact), mandatory effect sizes with 95% confidence
  intervals, Holm/BH multiple-testing correction, reproducible linear scripts,
  and restrained scientific interpretation under offline Scientific Python +
  SQLite runtimes.
---

# Statistical Inference, Effect Size & Interpretation Skill

Skill resources are installed at `/skills/weekly-skill`. Case inputs live under `/input/` (`case.json`, `task.md`, data dictionaries, CSV/JSON tables, and SQLite databases). Working artifacts go under `/app/work/`; final verified deliverables and `result.json` go under `/app/output/`.

This Skill is designed for **offline Scientific Python + SQLite runtimes** (`python:3.12-slim` or full scientific-python stacks). All bundled scripts are **Dual-Backend**: they run natively on the Python 3.12 standard library with zero third-party dependencies and automatically enable SciPy/Matplotlib enhancements (exact Shapiro-Wilk, 300 dpi PNG/PDF export) when available. Never fabricate missing measurements, never assume a library is installed before probing, and never replace real execution with precomputed answers.

---

## Scope & Boundaries

- **In scope**: Data hygiene audit (duplicates, missing values vs. true zeros, outliers, encoded integer/ID columns, database field types and units), descriptive statistics, statistical assumption verification, inferential test routing across independent, paired, correlation, and contingency designs, effect sizes with 95% confidence intervals, multiple-testing corrections (Holm-Bonferroni and Benjamini-Hochberg FDR), reproducible standalone scripts, publication-grade vector plots, and restrained scientific interpretation.
- **Out of scope**: Prospective experimental design, a priori power/sample-size calculation, causal mediation modeling from observational tables, and full manuscript drafting. When a request crosses these boundaries, complete all supported analytical work and record the exact boundary in `result.json`.

---

## Stage 0: Lock Inference Unit, Data Dictionary & Field Hygiene

Before running any hypothesis test, lock the scientific and data semantics from `/input/case.json`, `/input/task.md`, and any attached data dictionary or SQLite schema:

1. **Runtime & Input Discovery (M1/M3/M2/M4)**:
   - Run `preflight_probe.py` to detect available libraries (`scipy`, `numpy`, `pandas`, `matplotlib`, `sqlite3`) and `/app/output` paths.
   - Initialize `budget_guard.py` (64 requests / 900 s ceiling) and `result_guard.py` early so partial verified progress is never lost.
   - Index `/input/` with `block_indexer.py` to extract exact column definitions, units (`mg/L`, `cm`, `h`, `%`), and constraints.
2. **Lock the Statistical Inference Unit**:
   - Distinguish **biological replicates** (independent subjects/samples) from **technical repeats** (e.g., repeated HPLC injections per sample) and **paired repeated measures** (same subject observed under two conditions).
   - Never treat technical replicates or repeated measurements on the same subject as independent observations in an unpaired test; aggregate technical repeats to the biological unit or switch to `--design paired --subject-col <col>`.
3. **Separate Missing, Not-Tested, and True Zero**:
   - Missing tokens (`""`, `NA`, `NaN`, `null`, `None`, `ND`, `-`) are **never** silently imputed to `0`.
   - Record exact counts of excluded missing rows per group and true numeric `0.0` values retained.
   - Report both raw row counts and post-deduplication counts; never silently drop exact duplicate rows without logging `exact_duplicates_removed`.
4. **Block Encoded Integer & Identifier Columns**:
   - Discrete numeric codes (`cultivar_code`, `species_code`, `qc_pass`, `region_code`, `*_id`, leading-zero strings like `"0001"`) must **never** be averaged or fed into continuous ANOVA/t-tests.
   - `assumption_diagnostics.py` automatically detects code-like column names, leading-zero numeric IDs, binary `0/1` indicators, and 1-to-1 bijections with string label columns, blocking continuous inference with `ENCODED_INTEGER_COLUMN`. Route categorical codes to `--design contingency` instead.

---

## Stage 1: Assumption Diagnostics & Small-Sample Degradation

Always execute `assumption_diagnostics.py` before selecting an inferential test. See [Statistical Decision Tree & Small-Sample Degradation](references/statistical_decision_tree_and_small_sample.md) for exact thresholds and mathematical formulas.

```bash
SCRIPTS=/skills/weekly-skill/scripts

python3 "$SCRIPTS/assumption_diagnostics.py" \
  --input /input/data.csv \
  --design independent \
  --group-col treatment \
  --value-col delta_response \
  --subject-col paired_subject \
  --out /app/work/assumptions.json
```

For SQLite sources, pass `--input /input/database.sqlite --table <table_name>` (connections strictly enforce `mode=ro` read-only URI mode and record source SHA-256).

### Diagnostic Checks Performed

1. **Per-group Sample Size & Missing-Cell Audit**:
   - `n == 0`: marked `UNESTIMABLE_EMPTY_CELL`.
   - `n == 1`: marked `UNESTIMABLE_SINGLETON` (excluded from variance-based contrasts, preserved in descriptive table).
   - `n == 2` (`n < 3`): marked `DEGRADED_SMALL_N` (normality testing is underpowered; parametric assumptions cannot be verified).
2. **Zero-Variance Degeneracy Audit**:
   - Groups with `sd == 0` (all identical values) are marked `DEGRADED_ZERO_VARIANCE`. Welch weights ($n_i / s_i^2$) and pooled Hedges' $g$ denominators are undefined when variance vanishes; the runner explicitly prevents division-by-zero explosions and documents `estimability_status`.
3. **Outlier Dual-Audit (Non-Destructive)**:
   - Computes both **Tukey 1.5×IQR** fences and **Iglewicz-Hoaglin Modified Z-Score (|MZ| > 3.5 via MAD)** per group.
   - Outliers are **never silently deleted**; heavy MAD outliers trigger routing to robust rank-based methods (`nonparametric_kruskal_mannwhitney` / `Spearman`) while reporting full raw observations.
4. **Normality & Homogeneity of Variance**:
   - Normality: SciPy exact Shapiro-Wilk when SciPy is installed, or pure-stdlib Royston (1993) Shapiro-Francia approximation in slim containers (backend recorded honestly in `normality.method`).
   - Homogeneity of variance: **Brown-Forsythe median-centered Levene test** (robust against skewness, implemented in both SciPy and pure stdlib).

---

## Stage 2: Design-Matched Inferential Routing

Route strictly by research design, sample size, normality, and homoscedasticity:

| Research Design | Condition | Omnibus / Primary Test | Pairwise / Contrast Test | Primary Effect Size & 95% CI |
| --- | --- | --- | --- | --- |
| Independent, 2 groups | Normal + equal var (`Levene p >= 0.05`) | Student's two-sample t-test | Student's t | Mean diff + 95% t-CI, Hedges' $g$ |
| Independent, 2 groups | Normal + unequal var (`Levene p < 0.05`) | **Welch's t-test** (exact Welch-Satterthwaite $df$) | Welch's t | Mean diff + 95% Welch-df CI, Hedges' $g$ |
| Independent, 2 groups | Non-normal, MAD outliers, or `n < 3` / zero-var | **Mann-Whitney U** (tie-corrected) | Mann-Whitney U | **Hodges-Lehmann median shift** + Moses 95% CI, **Cliff's $\delta$** & rank-biserial $r$ |
| Independent, $\ge 3$ groups | Normal + equal var | One-way ANOVA | Student's t + Holm / BH | $\eta^2$, $\omega^2$; pairwise Hedges' $g$ + 95% CI |
| Independent, $\ge 3$ groups | Normal + unequal var | **Exact Welch's ANOVA (Welch 1951)** | **Welch's t** (exact $df_W$) + Holm / BH | $\omega^2_{\text{est}}$; pairwise mean diff (Welch 95% CI) & Hedges' $g$ |
| Independent, $\ge 3$ groups | Non-normal, MAD outliers, or degraded group | **Kruskal-Wallis H** (tie-corrected) | **Mann-Whitney U** + Holm / BH | $\epsilon^2$; pairwise **Hodges-Lehmann shift** (95% CI) & **Cliff's $\delta$** |
| Paired, 2 conditions | Normal differences, no extreme outliers | **Paired t-test** (complete pairs only) | Paired t | Mean diff + 95% CI, Hedges' $g_z$ |
| Paired, 2 conditions | Non-normal differences or outliers | **Wilcoxon signed-rank** (exact for $n \le 12$) | Wilcoxon | **Hodges-Lehmann pseudomedian** + 95% CI, matched-pairs rank-biserial $r$ |
| Bivariate continuous | Both normal, no extreme MAD outliers | **Pearson $r$** (with Spearman sensitivity) | — | $r$, $R^2$, **exact Fisher-z 95% CI** |
| Bivariate continuous | Skewed, ordinal, or outliers | **Spearman $\rho$** (with Pearson sensitivity) | — | $\rho$, Bonett-Wright 95% CI |
| Categorical table | 2×2 with any expected count < 5 | **Exact 2×2 Fisher's exact test** | — | Odds Ratio + 95% CI (Haldane 0.5 disclosed if 0-cell), Cramér's $V$ |
| Categorical table | $R \times C$ with adequate expected counts | **Pearson $\chi^2$** (flags cells < 5) | — | **Cramér's $V$** & bias-corrected Cramér's $V$ |

See [Effect Size & Confidence Interval Selection Matrix](references/effect_size_and_ci_matrix.md) for direction conventions and interpretation thresholds.

---

## Stage 3: Execute Inference, Multiple Testing & Reproducible Artifacts

Run `inference_effect_ci_runner.py` with explicit `--design`, column arguments, and `--unit` from the data dictionary:

```bash
# Example A: Independent multi-group comparison (CSV or SQLite)
python3 "$SCRIPTS/inference_effect_ci_runner.py" \
  --input /input/data.csv \
  --design independent \
  --group-col cultivar_name \
  --value-col proline \
  --unit "mg/L" \
  --out-dir /app/output \
  --stem stats_proline

# Example B: Paired comparison (excludes incomplete subjects without imputation)
python3 "$SCRIPTS/inference_effect_ci_runner.py" \
  --input /input/paired.csv \
  --design paired \
  --group-col treatment \
  --value-col delta_response \
  --subject-col paired_subject \
  --out-dir /app/output \
  --stem stats_paired

# Example C: Correlation (Pearson Fisher-z CI + Spearman rho)
python3 "$SCRIPTS/inference_effect_ci_runner.py" \
  --input /input/iris.csv \
  --design correlation \
  --x-col petal_length_cm \
  --value-col petal_width_cm \
  --unit "cm" \
  --out-dir /app/output \
  --stem stats_corr

# Example D: Contingency table (Chi-square + Cramér's V + 2x2 Fisher exact)
python3 "$SCRIPTS/inference_effect_ci_runner.py" \
  --input "$INPUT_DB" \
  --table hplc_injections \
  --design contingency \
  --group-col run_batch \
  --value-col qc_pass \
  --out-dir /app/output \
  --stem stats_qc

# Example E: Continuous multivariable OLS regression (e.g., UCI Concrete 1030 without natural groups)
python3 "$SCRIPTS/inference_effect_ci_runner.py" \
  --input "$INPUT_TABLE" \
  --design ols_regression \
  --y-col compressive_strength_mpa \
  --x-cols cement_kg_m3,blast_furnace_slag_kg_m3,fly_ash_kg_m3,water_kg_m3,superplasticizer_kg_m3,coarse_aggregate_kg_m3,fine_aggregate_kg_m3,age_days \
  --stratify-col age_days \
  --unit "MPa" \
  --out-dir /app/output \
  --stem stats_ols
```

### Mandatory Outputs Generated

1. `<stem>.json`: Structured evidence containing runtime environment, source SHA-256, deduplication/missingness audit, assumption diagnostics, `estimability_status`, omnibus test, pairwise contrasts (raw $p$, Holm $p_{\text{holm}}$, Benjamini-Hochberg $q_{\text{BH}}$, effect sizes, 95% CIs), plot coverage audit, and interpretation boundaries.
2. `<stem>.md`: Human-readable scientific report with full descriptive table ($n$, missing, Mean, SD, 95% CI, Median, IQR, Min, Max), adjusted contrasts, and restrained interpretation.
3. `reproduce_stats.py`: Linear, imperative, top-to-bottom research script (parameters adjacent to calls, source SHA-256 verification, in-place summary prints) that researchers can inspect and re-run independently.
4. `<stem>_plot.svg` (always generated via pure stdlib vector SVG; when Matplotlib is available, simultaneously exports 300 dpi `<stem>_plot.png`, editable `<stem>_plot.pdf` with `pdf.fonttype=42`, `bbox_inches=None`, panel-centered title `loc="center"`, and `<stem>_plot.svg`).

---

## Stage 4: Front-End Scientific Expression vs. Back-End State Separation

Follow [Misuse Checklist & Reporting Specification](references/misuse_checklist_and_reporting_spec.md) when drafting final interpretations in `<stem>.md` and `result.json`:

1. **Separate front-end scientific language from back-end states**: Never copy internal routing enums (e.g., `parametric_welch_anova`, `DEGRADED_SMALL_N`) verbatim into scientific conclusions without plain-language domain translation; keep diagnostic codes in structured JSON metadata and technical notes.
2. **Never equate significance with causality or practical importance**: Write "associated with" or "higher/lower than" under the observed design—never "proves efficacy" or "causes". Always quote the point estimate, unit, 95% CI, effect size, and adjusted $p$-value together.
3. **Never conflate non-significance with equivalence**: `p_holm >= 0.05` indicates insufficient evidence against the null given the observed sample size and variance, especially when $n$ is small or the 95% CI remains wide.
4. **Distinguish `untested`, `missing`, `not significant`, and `true zero`**: Report missing rows per group and zero-variance / tiny-sample caveats transparently.
5. **No p-hacking or silent re-routing**: If a sensitivity check (e.g., parametric vs. rank-based) changes qualitative conclusions due to outliers or skewness, report both results transparently and explain why the primary method fits the observed distribution.

---

## Stage 5: Harbor Grounding, Verification & Final Packaging

Before finishing a case, close the full M1–M6 loop:

```bash
SCRIPTS=/skills/weekly-skill/scripts

python3 "$SCRIPTS/preflight_probe.py" --out /app/work/env.json
python3 "$SCRIPTS/budget_guard.py" --init
python3 "$SCRIPTS/case_contract_sealer.py" discover --input-dir /input --out /app/work/case_discovery.json
python3 "$SCRIPTS/result_guard.py" --init --case-id "<CASE_ID>" --task-id "challenge-c0e1e220"
python3 "$SCRIPTS/block_indexer.py" --scan /input --out /app/work/input_blocks.json

# Run zero-manual end-to-end orchestrator (auto-discovers /input CSV/SQLite + task.md + case.json,
# routes independent / ols_regression / contingency automatically, and seals result.json):
python3 "$SCRIPTS/auto_stats_orchestrator.py" \
  --input-dir /input \
  --out-dir /app/output \
  --stem stats_report

# Or run modular assumption_diagnostics.py + inference_effect_ci_runner.py explicitly for custom contrasts ...

python3 "$SCRIPTS/budget_guard.py" --tick "finalize"
python3 "$SCRIPTS/result_guard.py" --path /app/output/result.json --validate
python3 "$SCRIPTS/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SCRIPTS/final_audit.py" --dir /app/output --result-file result.json --require stats_report.json --require stats_report.md
```

Always check `result.json` contains all case-mandated `output_contract` fields, exact `case_id`, finite numeric metrics (no `NaN`/`Inf`), honest `status` (`ok` vs. `partial_with_gaps`), source file names, and explicit residual uncertainties.
