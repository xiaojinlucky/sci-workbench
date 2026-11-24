# Minimal Usage Example (`challenge-9e7302fd`)

> **[ILLUSTRATIVE EXAMPLE ONLY]** The input and output below demonstrate the CLI interface and output contract; they do not represent formal benchmark case data or test results.

## 1. Example Input File (`/tmp/demo_stats/manuscript.md`)

```markdown
# Methods
This retrospective observational study enrolled N = 120 patients (treatment n = 62, control n = 54).
In a separate preclinical experiment, 6 mice per group were imaged (300 neurons per group) and compared with two-sample t-tests.

# Results
Group comparison yielded t(28) = 1.98, p < 0.05 and F(2, 45) = 4.10, p = 0.000.
Logistic regression showed OR = 1.45 (95% CI: 0.92-2.28, p = 0.021).
Response occurred in 18/40 (52.0%) participants.
Neuron firing rate differed across slices, t(598) = 3.12, p = 0.002.
These observational findings prove that the biomarker directly causes disease progression.
```

## 2. CLI Invocation

```bash
python -B /skills/weekly-skill/scripts/statcheck_and_phack_auditor.py \
  --input /tmp/demo_stats \
  --out-json /app/output/stats_audit.json \
  --out-md /app/output/stats_review_report.md
```

## 3. Illustrative Output (`[EXAMPLE]`)

Stdout summary:
```json
{
  "status": "ok",
  "audit_status": "completed",
  "math_backend": "pure_stdlib_lentz",
  "passed": false,
  "statcheck_count": 3,
  "effect_ci_checks_count": 1,
  "findings_count": 10
}
```

Key findings produced in `stats_review_report.md` (`[EXAMPLE]`):
- **STAT-001 [MAJOR | decision_error]**: Total `N = 120` disagrees with subgroup sum `62 + 54 = 116` (`diff = -4`).
- **STAT-002 [CRITICAL | decision_error]**: `t(28) = 1.98, p < 0.05` flips significance — exact two-sided `p = 0.05762` violates strict bound `< 0.05`.
- **STAT-004 [MINOR | norm_violation]**: Invalid `p = 0.000` notation (should be `p < 0.001` or exact `p = 0.0231`).
- **STAT-005 [CRITICAL | decision_error]**: `OR = 1.45 (95% CI: 0.92-2.28, p = 0.021)` — 95% CI strictly spans null `1.0` yet reports `p < 0.05`.
- **STAT-006 [MAJOR | decision_error]**: `18/40` equals `45.0%`, contradicting reported `52.0%`.
- **STAT-007 [MAJOR | method_flaw]**: Suspected pseudoreplication — `t(598)` reported with `6 mice` and `neurons/slices` cues without a mixed-effects model.
- **STAT-008 [MAJOR | method_flaw]**: Causal overclaim ("proves ... directly causes") in a retrospective observational study.
- **STAT-009 [MINOR | under_reported]**: Multiple hypothesis tests without multiplicity disclosure — logged strictly as `under_reported` with `missing_inputs` (not p-hacking).
