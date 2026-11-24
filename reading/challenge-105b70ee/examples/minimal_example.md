# Minimal Synthetic Invocation Example (Illustrative Only)

> **Notice**: All inputs and outputs below are synthetic examples demonstrating the `/input/target-fields.json` contract and validation CLI. They are not formal test cases or benchmark results.

## 1. Synthetic Input Layout (`/input`)

- `case.json`: paper paragraphs and tables
- `target-fields.json`: see `examples/sample_target_fields.json`

## 2. Invocation & Strict Validation

```bash
python scripts/field_extraction_engine.py --input /input --output /app/output --work /app/work
python scripts/field_extraction_engine.py --input /input --output /app/output --work /app/work --validate-fields /app/work/fields_draft.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require extracted_fields.json --require extracted_fields_report.md
```

## 3. Expected Guardrail Behavior

- Present field (`faradaic_efficiency`) -> `status: "reported"`, exact scalar + unit + condition + verbatim quote.
- Absent field (`long_term_stability_1000h`) -> `status: "not_reported"`, `scalar: null` (never `0`).
- Conflicting prose vs. table value under identical conditions -> `status: "source_conflict"`, `conflicting_sources` listing both locations.
