# Minimal Usage Example (Illustrative Only — Not a Formal Case Result)

## Illustrative Input (`/input/manuscript.md` + `/input/case.json`)
```json
{
  "case_id": "example-quality-01",
  "title": "Observational Cohort Biomarker Study (Illustrative)"
}
```
```markdown
Abstract
In this retrospective observational cohort (N=120), high biomarker X was observed in 42.5% of patients and proves that X directly causes relapse [1].

1. Introduction
We evaluated whether biomarker X correlates with relapse risk.

2. Methods
Patients (N=120) were grouped into high-X (n=55) and low-X (n=50) cohorts. Statistical comparison yielded t(135) = 2.84, p = 0.000.

3. Results
High biomarker X appeared in 36.7% of patients (Fig. 2).
Figure 1. Biomarker distribution across cohorts (A) baseline and (C) follow-up; error bars indicate dispersion.

References
[1] Smith J. Biomarker X correlation study. J Example. 2023. (Retracted)
```

## Invocation Command
```bash
python /skills/weekly-skill/scripts/manuscript_quality_auditor.py \
  --input /input \
  --out-json /app/output/quality_audit.json \
  --out-md /app/output/quality_review_report.md \
  --out-result-json /app/output/result.json
```

## Illustrative Output Excerpt (Example Only)
```json
{
  "case_id": "example-quality-01",
  "status": "partial_with_gaps",
  "audit_status": "partial",
  "passed": false,
  "verdict": "defects_detected",
  "coverage_ratio": 1.0,
  "findings_count": 10,
  "severity_counts": {"critical": 2, "major": 4, "minor": 4, "suggestion": 0}
}
```
Key illustrative detections:
- Retracted reference `[1]` (`critical`, `citation_support`)
- Missing IRB/ethics disclosure for patient cohort (`critical`, `academic_norms`)
- Abstract `42.5%` vs Results `36.7%` numerical conflict (`major`, `fact_verification`)
- Subgroup sum `55 + 50 = 105 != N=120` (`major`, `fact_verification`)
- Impossible `t(135)` with `N=120` (`major`, `statistical_reporting` / `method_flaw`)
- Missing `Figure 2` caption (`major`, `fact_verification`) & skipped panel `B` in `Figure 1` (`minor`, `academic_norms`)
- Observational causal claim with citation `[1]` retained as `needs_human_review` (`citation_support`)
