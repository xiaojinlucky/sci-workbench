# Epistemic Classification & Multi-Layer Data Deconstruction Guide

Adapted from structured research-paper deconstruction and literature-mining workflows:

## 1. Three-Way Epistemic Tagging (`epistemic_class`)

Every extracted conclusion or quantitative claim must preserve the author's actual epistemic commitment:

| `epistemic_class` | Linguistic & Evidentiary Cues | Guardrail |
| --- | --- | --- |
| `reported_result` | Direct empirical measurement or verified table/result statement ("reached 88.5%", "reduced by 2.4-fold, p = 0.004") | Requires exact verbatim quote + condition + comparator |
| `conditional_limitation` | Explicit scope bound or caveat ("limited to acidic electrolytes", "only under 25 °C", "did not generalize to cohort B") | Must NOT be stripped away to make a claim look universal |
| `speculative` | Hedged author interpretation ("may indicate", "could be attributed to", "suggests a potential mechanism", "可能", "提示") | Must NOT be rewritten as proven causation or measured fact |

## 2. Multi-Layer Experimental Data & Threshold Checklist

When extracting conclusions and parameters from complex multi-dataset studies, check and separate:
1. **Source & Cohort Layer**: Public dataset vs. self-generated experimental cohort, exact sample size (`n`), and statistical inference unit.
2. **Method & Model Layer**: Experimental/computational model, control/baseline comparator, and key protocol parameters (temperature, concentration, duration, hyperparameters).
3. **Selection / Filtering Thresholds**: Exact cutoffs (`p < 0.05`, `FDR < 0.01`, `|log2FC| > 1`, `SNR > 3`) linked to the specific sub-question each data block supports.
4. **Reproducibility Boundary**: Record whether raw data or analysis code availability is reported (`reported` vs `not_reported`).
