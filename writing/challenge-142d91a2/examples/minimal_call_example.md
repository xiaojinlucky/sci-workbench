# Minimal Synthetic Invocation Example (Not a Formal Test Case)

> **Synthetic interface example only**: Shows CLI usage and expected audit JSON structure. Actual inputs are mounted under `/input` at evaluation time.

## 1. Synthetic Input (`/input/case.json`)
```json
{
  "case_id": "SYNTH-PROP-01",
  "title": "Controlled resin-fill geometry for FDM structural brackets",
  "total_months": 12,
  "budget_limit": 50000,
  "unavailable_resources": ["High-temperature fatigue chamber"]
}
```

## 2. CLI Invocation
```bash
python3 /skills/weekly-skill/scripts/proposal_feasibility_checker.py \
  --proposal /app/output/proposal.md \
  --plan-json /app/output/proposal_plan.json \
  --case-json /input/case.json \
  --corpus /input \
  --export-docx /app/output/proposal.docx \
  --export-html /app/output/proposal_preview.html \
  --emit-contract-helper /app/work/contract_helper.json \
  --out-json /app/output/proposal_audit.json
```

## 3. Illustrative Audit Output (`proposal_audit.json` excerpt, synthetic)
```json
{
  "passed": true,
  "case_id": "SYNTH-PROP-01",
  "seven_elements": {
    "missing_sections": [],
    "thin_sections": [],
    "known_unknown_verify_ok": true,
    "research_questions_count": 2
  },
  "schedule_and_cpm": {
    "total_months": 12,
    "critical_path_steps": ["S1", "S2", "S4"],
    "critical_path_months": 12,
    "peak_parallel_steps": 2,
    "peak_parallel_month": 6,
    "peak_monthly_workload": 2.0
  },
  "budget": {
    "budget_limit": 50000.0,
    "budget_sum": 48500.0,
    "budget_limit_fabricated": false
  },
  "risks_and_resource_gaps": {
    "missing_risk_categories": [],
    "unavailable_resources_detected": ["High-temperature fatigue chamber"],
    "resource_gaps_linked_ok": true
  },
  "prose_json_reconciliation": {
    "mismatches": []
  },
  "exports": {
    "python_docx_installed": false,
    "docx_exported": true,
    "docx_exporter": "stdlib_ooxml",
    "html_exported": true
  },
  "errors": [],
  "style_warnings": []
}
```
