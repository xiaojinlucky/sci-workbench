# Minimal Synthetic Invocation Example (Not a Formal Test Case)

> **Synthetic interface example only**: Illustrates input structure, CLI execution, and expected audit JSON format. Real inputs are supplied under `/input` at runtime.

## 1. Synthetic Input Snippet (`/input/case.json`)
```json
{
  "case_id": "SYNTH-GRANT-01",
  "domain": "Polymer Composite Reinforcement",
  "organizer_workorder": {
    "id": "SYNTH-WO-01",
    "authority": "organizer-workorder-not-funder-policy",
    "source_basis": {
      "published_paper": "SYNTH-PAPER-01",
      "external_authors": ["Alex Rivera", "Morgan Chen"],
      "applicant_owned_preliminary_results_supplied": false,
      "actual_funder_notice_supplied": false,
      "resource_availability_evidence_supplied": false
    },
    "draft_size": "正文七节合计约1600–2600汉字",
    "planning_months": 12,
    "planning_unit_cap": 100
  }
}
```

## 2. CLI Commands
```bash
# Step 1: Extract /input fact & boundary index (.pdf/.docx/.md/.json)
python3 /skills/weekly-skill/scripts/grant_7element_auditor.py \
  --input-dir /input \
  --extract-corpus-index /app/work/corpus_index.json

# Step 3: Run 7-element, source_ref verbatim back-check & workorder audit
python3 /skills/weekly-skill/scripts/grant_7element_auditor.py \
  --draft /app/output/grant_chapter.md \
  --plan-json /app/output/grant_plan.json \
  --case-json /input/case.json \
  --corpus-dir /input \
  --crosswalk /app/output/element_crosswalk.md \
  --emit-contract-helper /app/work/contract_helper.json \
  --out-json /app/output/grant_audit.json
```

## 3. Illustrative Audit Output (`grant_audit.json` excerpt, synthetic)
```json
{
  "passed": true,
  "draft_extractor": "utf8_text",
  "parse_mode": "headings",
  "seven_elements": {
    "missing_elements": [],
    "thin_elements": [],
    "seven_section_han_chars": 2050,
    "draft_size_range": [1600, 2600],
    "draft_size_ok": true
  },
  "evidence_map_verification": {
    "checked_count": 5,
    "verified_count": 4,
    "planned_or_gap_count": 1,
    "ungrounded_count": 0
  },
  "provenance_and_boundary": {
    "applicant_evidence_present": false,
    "actual_funder_notice_supplied": false,
    "resource_availability_evidence_supplied": false,
    "explicit_evidence_gap_count": 5,
    "fabricated_applicant_claims": [],
    "external_author_appropriations": [],
    "proposed_as_completed_claims": [],
    "funder_policy_fabrications": []
  },
  "plan_consistency": {
    "goal_ids": ["G1", "G2", "G3"],
    "wp_ids": ["WP1", "WP2", "WP3"],
    "parity_ok": true,
    "planning_months": 12,
    "max_milestone_month": 12,
    "planning_unit_cap": 100,
    "planning_unit_sum": 100.0
  },
  "supporting_files_check": {
    "all_six_satisfied": true,
    "risks_count": 3,
    "human_confirmations_count": 2,
    "evidence_map_count": 6
  },
  "errors": [],
  "style_warnings": []
}
```
