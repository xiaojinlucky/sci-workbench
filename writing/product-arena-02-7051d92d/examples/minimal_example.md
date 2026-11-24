# Minimal Synthetic Example (`academic-language-polishing`)

> **Notice**: Synthetic interface example only; does not represent any official evaluation test case.

## 1. Example Input (`source.md` + `glossary.json` + `alerts.json`)

**`source.md`**:
```markdown
# Results
Although the cohort was small (n = 42), baseline expression may be associated with a 18.4% reduction in lesion volume (p = 0.041, Fig. 2A) [1].
This finding sheds light on the pivotal role of tumor microenvironment (TME) and tumour microenvironment signaling.
```

**`glossary.json`**:
```json
{
  "terms": [
    {"canonical": "tumor microenvironment", "abbreviation": "TME", "forbidden_variants": ["tumour microenvironment"]}
  ]
}
```

**`alerts.json`** (simulated false-positive grammar alert):
```json
[
  {"alert_id": "LT-1", "sentence_id": "S001", "rule_id": "ALTHOUGH_FRAGMENT_12061", "message": "Possible fragment starting with Although"}
]
```

## 2. Example CLI Invocation

```bash
python3 -B scripts/polishing_invariant_engine.py \
  --source source.md \
  --revised manuscript_polished.md \
  --glossary glossary.json \
  --candidate-alerts alerts.json \
  --register journal_article \
  --out-audit-json polishing_audit.json \
  --out-redline-md polishing_redline_diff.md \
  --out-result-json result.json \
  --case-id "SYNTH-POLISH-01" \
  --strict
```

## 3. Illustrative Output (`manuscript_polished.md` + `result.json` excerpt)

**Polished text**:
```markdown
# Results
Although the cohort was small (n = 42), baseline expression may be associated with an 18.4% reduction in lesion volume (p = 0.041, Fig. 2A) [1].
This finding characterizes tumor microenvironment (TME) signaling.
```

**Audit highlights**:
- `S001`: Valid concessive clause protected (`rejected_false_positive` for `LT-1`); `may be associated with`, `n = 42`, `18.4%`, `p = 0.041`, `Fig. 2A`, and `[1]` 100% conserved (`a 18.4%` → `an 18.4%` minimal article fix).
- `S002`: Removed AI clichés (`sheds light on`, `pivotal role`) and unified `tumour microenvironment` → `tumor microenvironment`.
- `passed: true`, `modality_drift_count: 0`, `numeric_drift_count: 0`, `glossary_violations_after: 0`.
