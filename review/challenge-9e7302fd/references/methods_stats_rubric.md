# Research Methods & Statistical Review Rubric (`challenge-9e7302fd`)

This rubric anchors the five required review facets, CNS inferential-unit calibration, and strict boundaries between hard calculation errors, methodological flaws, and reporting gaps.

## 1. Five-Facet Methodology Audit Matrix

| Facet | Concrete Audit Checkpoints | Common `under_reported` vs `method_flaw` Distinction |
| --- | --- | --- |
| 1. **Study Design** | Design type vs research question; control/comparator validity; randomization & blinding; pre-specified primary endpoint; confounding & bias control | Missing blinding disclosure -> `under_reported`; asserting causality from cross-sectional/observational design -> `method_flaw` |
| 2. **Variable Definition** | Operational definitions of exposure, outcome, covariates; measurement instruments & units; categorical cut-offs; missing-data mechanism & handling | Unspecified missing-data handling -> `under_reported`; inconsistent outcome definition between Methods and Results -> `method_flaw` / `factual_error` |
| 3. **Statistical Methods** | Test vs data structure & distribution; **independent experimental unit** (pseudoreplication check); multiplicity adjustment; repeated measures / mixed models; sensitivity analyses | Undisclosed multiplicity correction without raw data -> `under_reported` (NEVER call p-hacking); treating 600 cells from 6 mice as independent (`t(598)`) -> `method_flaw` |
| 4. **Sample Size & Power** | A priori sample-size calculation inputs (effect size source, alpha, power); enrolled vs analyzed denominator funnel; attrition/exclusion accounting | Missing power calculation -> `under_reported` (`suggestion`); group counts not summing to total `N` without exclusion explanation -> `decision_error` |
| 5. **Results Reporting** | Exact StatCheck recomputation (`t/F/chi2/r/z`); effect size & 95% CI vs p-value consistency; `k/n (pct%)` arithmetic; GRIM on integer scales; reporting guidelines (CONSORT/STROBE/ARRIVE); banning `p = 0.000` and spin phrasing | Missing 95% CI alongside exact p -> `under_reported`; CI strictly crossing null while reporting `p < 0.05` or StatCheck significance flip -> `decision_error` (`critical`) |

## 2. Strict Boundary Table: `decision_error` vs `method_flaw` vs `under_reported`

| Class | Default Severity | Evidence Standard | Forbidden Overreach |
| --- | --- | --- | --- |
| `decision_error` | `critical` (`P0`) or `major` (`P1`) | Deterministic mathematical recomputation (`stat_math_core.py`), CI-vs-p contradiction, `k/n` percentage error, GRIM impossibility on discrete integer scale, or unreconciled `sum(group_n) != total_N` | Do not fire CI-vs-p contradiction when text explicitly states multiplicity-adjusted p-values explain an unadjusted CI excluding null with `p_adj > 0.05` |
| `method_flaw` | `major` (`P1`) | Manuscript explicitly states an incompatible design/analysis (e.g., `N = 6 mice` with `t(598)` across cells without mixed-effects nesting; causal verbs in observational study; `df > N` without repeated measures) | Do not label exploratory or small-sample studies as flawed solely for being simple or null (ACL H6/H7/H8) |
| `under_reported` | `minor` (`P2`) or `suggestion` (`P3`) | Required reporting element is absent from manuscript; **must list `missing_inputs` and `unassessable_items`** | **Never escalate absent multiplicity disclosure or borderline p-values into a p-hacking or misconduct verdict** |

## 3. CNS Peer-Review Calibration (Inferential Unit & Selection Funnel)

1. **Inferential Unit (Pseudoreplication)**: Always verify whether `df` matches independent biological units (patients, animals, cages, clinical centers) rather than nested technical observations (cells, brain slices, image ROIs, repeated frames). Flag suspected unit inflation neutrally as a methodological flaw with a concrete fix (per-subject aggregation or mixed-effects modeling with subject random intercept).
2. **Selection & Attrition Funnel**: Trace `screened -> eligible -> randomized/enrolled -> excluded/lost -> analyzed` across Abstract, Methods, Results, and Figures. Unexplained gaps between total `N` and subgroup sums require reconciliation.
3. **Anti-False-Positive Calibration**:
   - Never apply GRIM to continuous physical/biological measurements (blood pressure, reaction time, expression level); restrict GRIM strictly to discrete integer/Likert/count scales.
   - Respect declared one-sided tests when justified in context (`consistent_one_sided`).
   - Apply the ACL H1–H17 fairness firewall from `evidence_and_fairness_protocol.md`.
