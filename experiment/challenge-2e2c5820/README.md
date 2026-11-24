# Experiment Feasibility Reviewer (`challenge-2e2c5820`)

Offline-native Harbor Skill for 7-category, source-grounded feasibility auditing across **3 versioned candidate experimental plans** under organizer prospective resource, pricing, booking, and approval contracts.

## Included Components

- `SKILL.md`: 7-category feasibility protocol, 4-state resource rules (`available` / `outsourceable` / `needs_procurement` / `unavailable`), deterministic verdict hierarchy, and exception handling.
- `scripts/validate_and_sync_result.py`: Pre-validates intermediate `parsed_plans.json` schema and `source_ref` coverage (fail-loud) and auto-syncs `/input/case.json` `case_id` + all `output_contract` fields into `/app/output/result.json`.
- `scripts/plan_arithmetic_verifier.py`: Strict null-vs-zero sample/cost verifier, topological DAG critical-path scheduler, sample-stability vs approval/procurement lead cross-checker, instrument precision/maintenance/quote-expiry checker, and internal plan contradiction detector.
- `scripts/feasibility_contract_auditor.py`: 7-category + SOP handover auditor separating `FAIL`, `MISSING_INFO` (including `"in_review"` / `"pending_amendment"` approvals), `CONDITIONAL` (including expired quote refresh), and `PASS` with owner-tagged actionable conditions and version progression deltas.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit}.py`: Deterministic runtime, budget, atomic result JSON, grounding, and final output checks.
- `references/seven_category_rubric_and_states.md` & `references/input_output_schemas.md`: Complete rubric, SOP/manual/policy source-attribution matrix, and JSON schemas.
- `examples/minimal_feasibility_case.json` & `examples/example_review_summary.md`: Minimal illustrative example.

## Quick Verification & Troubleshooting

- **Dependencies**: Python 3.10+ standard library only (zero external packages or network calls).
- **Common Failure Modes**:
  - `validate-input` exits `1`: populate missing `source_ref` anchors on resources, parameter domains, or candidate plans before running arithmetic checks.
  - `REQUIRES_INFO_OR_CONFIRMATION`: inspect `missing_information` for unpriced `null` rates, missing quote dates, or `"in_review"` approvals (never coerced to `0` or `True`).
- **Scope Boundary**: Evaluates prospective planning contracts only; does not execute experiments, analyze experimental datasets, or grant live IRB/IACUC/biosafety approvals.
