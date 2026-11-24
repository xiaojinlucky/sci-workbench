# Experimental Design, Control Estimability, and Handover Blueprint

## 1. Research-Type Routing (Avoid One-Size-Fits-All Templates)

Match the hypothesis frame to the study's actual discipline before assigning controls and metrics:

1. **Empirical / Mechanistic Causal Study (`empirical_causal`)**:
   - Express the core claim as `A -> (B -> C) -> D`, separating **solid links** (already supported by source literature/pilot data) from **dashed links** (what this experiment tests).
   - Every dashed link needs an explicit perturbation, an observable readout (`DV` with assay and unit), and complementary negative/vehicle + positive controls.
2. **Engineering / Process / Materials Optimization (`engineering_optimization`)**:
   - Anchor on target performance metrics, operating window `[min, max]`, safety ceiling, throughput, and repeatability against an existing baseline configuration under identical bench conditions.
3. **Exploratory / High-Throughput Discovery (`exploratory_discovery`)**:
   - Define the candidate universe, pre-specified filtering funnel, false-discovery control, and orthogonal confirmation gate; never label exploratory prioritization as pre-registered confirmatory proof.
4. **Computational / Algorithmic Benchmark (`computational_benchmark`)**:
   - Freeze train/validation/test splits, random seeds, estimator versions, preprocessing pipelines, and compute caps; prevent data leakage across splits.

## 2. Inference Unit vs. Technical Replicate & Power Discipline

- **Inference Unit (`inference_unit`)**: The smallest independent unit to which a treatment is assigned (e.g., independent donor, animal, batch reactor, cell passage, physical specimen).
- **Technical Replicate (`technical_unit`)**: Repeated measurements from the same independent unit (e.g., triplicate qPCR wells, repeated HPLC injections). Technical replicates reduce measurement noise; they do **not** increase `n` for statistical inference.
- **Sample Count Equation**:
  `total_samples = (treatment_arms + control_arms) * biological_replicates * technical_replicates + qc_extra_samples`
- When effect size (`expected_cohen_d` or `expected_delta / expected_sd`) is available, check power at `alpha = 0.05` and flag underpowered arms rather than hiding small `n`.

## 3. Control Matching & Augmented-Factorial Estimability

- Never deploy a single blank/untreated control alone. Pair at least:
  - A **negative / vehicle / blank / sham** arm isolating background drift and carrier effects, and
  - A **positive / reference standard / benchmark** arm verifying assay sensitivity and dynamic range.
- **Augmented-Factorial Check**: If treatments span multiple timepoints, batches, or plates (`blocks`) while a control arm only appears in one block, contrasts in blocks lacking concurrent controls are **confounded with block/timepoint shifts**. List estimable contrasts explicitly and warn against cross-block naive comparisons.

## 4. SOP Version Pinning & ELN Handover Standard

- Pin every step to `sop_id` + `sop_version` (e.g., `SOP-CAT-03` + `v2.0`). Never leave mixed `v1` and `v2` references unresolved in the same protocol.
- Bind a stable `experiment_id_pattern` (e.g., `EXP-YYYYMMDD-<ARM>-<UNIT>`) to the raw data storage directory and require a structured deviation log (`planned`, `actual`, `timestamp_utc`, `operator`, `validity_impact`) so post-run reviewers can trace anomalies without losing context.

## 5. Strict Null-vs-Zero & Prospective Boundary Discipline

- Explicit `0` in a contract (e.g., `booking_lead_days: 0`, `setup_cost_usd: 0`) is a **verified zero**.
- Missing or `null` contract values (`unit_cost: null`, `ethics_approved: null`) remain **UNVERIFIED / MISSING_INFO** and block automatic cost summation or approval clearance.
- Keep **Prospective Planning** strictly distinct from **Executed Lab Facts**: prospective pricing, booking windows, and surrogate predictions are planning scenarios requiring confirmation prior to lab execution.

## 6. Pre-mortem 5-Column Risk Matrix & 4-Question Deviation Ledger

Before locking `v3_optimized`, run a Pre-mortem ("assume the experiment has failed") and structure every critical failure mode into five columns:
| Priority | Potential Failure Mode | Earliest Warning Signal | Preventive Control | Stop-Loss / Switch Trigger & Fallback |
| --- | --- | --- | --- | --- |

During execution or revision, log every protocol deviation using four structured fields (for downstream anomaly attribution):
1. **Planned Setting / SOP Pin** (`sop_id@version` and planned parameter)
2. **Actual Observation / Deviation** (what changed and when)
3. **Conservative Containment & Validity Impact** (affected arms/units)
4. **Follow-up Verification or PI Decision Required**

## 7. Power Approximation Scope & Small-Sample / Non-Normal Boundaries

- `hypothesis_control_mapper.py` computes a two-sided normal approximation power (`alpha = 0.05`, default target `0.80`) on **independent experimental/biological units** (`n_biological`, never technical replicates).
- **Small-`n` limit (`n < 10`)**: Normal approximation slightly overestimates exact non-central `t`-test power; add `+1` to `+2` independent units per arm when `n < 10` is near the 0.80 threshold.
- **Non-normal / skewed / ordinal readouts**: When nonparametric tests (Mann-Whitney U / Kruskal-Wallis) are planned, inflate minimum `n` by ~15% (asymptotic relative efficiency ~0.864–0.955) or confirm via domain simulation.
