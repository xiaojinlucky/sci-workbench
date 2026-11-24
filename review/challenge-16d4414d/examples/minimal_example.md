# Minimal Invocation Example (Illustrative Only — Not a Formal Case Answer)

## 1. Synthetic Input (`sample_manuscript.md`)
```markdown
# Abstract
In this retrospective cross-sectional cohort (n = 180), biomarker Z increased survival by 32.4% (p = 0.01).

# Introduction
Previous studies claimed that biomarker Z directly causes tumor regression [1].
We investigate whether biomarker Z predicts treatment response and evaluate longitudinal gut microbiome shifts across 12 months.

# Methods
This was an observational retrospective single-center study of 180 patients.
A negative control group was included. Because this study is observational, it cannot prove causality.

# Results
Biomarker Z was associated with a 18.2% decrease in survival rate (p = 0.04).

# Discussion
Therefore, our findings prove that biomarker Z directly causes clinical recovery and is universally effective across all patients.
A limitation of our study is its single-center retrospective design.
```

## 2. Command
```bash
python scripts/argument_chain_auditor.py \
  --input /path/to/sample_dir \
  --out-json /app/output/logic_audit.json \
  --out-md /app/output/logic_review_report.md
```

## 3. Illustrative Output Excerpt (Example Only)
- **Suppressed non-author sentences (0 false positives)**:
  - `"Previous studies claimed that biomarker Z directly causes tumor regression [1]."` -> `cited_prior_view`
  - `"Because this study is observational, it cannot prove causality."` -> `explicit_control_or_hypothesis`
- **Detected true defects**:
  - `LOG-001 [MAJOR | insufficient_evidence | causal_overreach]`: Discussion claims `"prove that biomarker Z directly causes..."` on a retrospective observational design.
  - `LOG-002 [MAJOR | insufficient_evidence | scale_over_extrapolation]`: Single-center observational study claims `"universally effective across all patients"`.
  - `LOG-003 [MAJOR | insufficient_evidence | intro_delivery_fracture]`: Introduction promises `"longitudinal gut microbiome shifts across 12 months"`, absent from Methods and Results.
  - `LOG-004 [CRITICAL | factual_error | cross_section_direction_reversal]`: Abstract reports `"increased survival by 32.4%"` whereas Results reports `"18.2% decrease in survival rate"`.
- **Result status**: `result_status: "ok"`, `audit_status: "completed"`, `verdict: "defects_detected"`.
