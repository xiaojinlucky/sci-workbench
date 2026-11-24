# Rebuttal Revision Closure Skill (`rebuttal-revision-closure`)

Offline, deterministic peer-review rebuttal and controlled manuscript-baseline revision skill for Harbor DeepSeek Harness execution.

## Purpose and Scope

Designed for controlled peer-review revision tasks where an agent receives a baseline manuscript summary (`materials/manuscript-baseline.md`) and a structured reviewer/editor comment list (`materials/reviewer-comments.md`). The skill enforces:

1. **Multi-format comment & sub-item 1:1:1 closure**: Parses `E.1`, `R1.1`, `R1.1a`, `R1.1(b)`, `Comment 1`, Chinese `审稿人1意见1`, and nested `(a)`/`(b)`/`(1)`/`(2)` sub-requirements, verifying an explicit canonical disposition tag for every item.
2. **Triple-bound edit verification**: Validates that every quoted revision exists verbatim in `manuscript-revised.md`, sits inside the exact claimed `[L12-L16]` / `[§Section L12-L16]` anchor span, and overlaps genuine token-level changes in the `baseline -> revised` diff (blocking whitespace-only reflow and unchanged baseline quotes masquerading as edits).
3. **Missing-evidence tri-partite closure**: Audits `missing_evidence_list.md` to confirm every `AUTHOR_INPUT_NEEDED` / `PARTIAL` / `NOT_FEASIBLE` item documents a boundary rationale, an existing-material alternative clarification, and a concrete author confirmation item.
4. **Four-tier tone, reviewer isolation, anti-fabrication, and anti-AI prose auditing**: Blocks fabricated experiments/acceptance claims, hostile rebukes, overpromising, and cross-reviewer leaks while flagging AI boilerplate with exact line numbers.
5. **5-field `output_contract` generation**: Emits `preserved_facts`, `evidence_map`, and `change_log` patch snippets ready for `result_guard.py --finalize`.

## Directory Layout

- `SKILL.md`: Agent execution protocol, invariants, and completion criteria.
- `scripts/rebuttal_closure_checker.py`: Zero-dependency Python 3.12 closure, diff, anchor, evidence, and tone auditor.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Common Harbor M1-M6 runtime guards.
- `references/disposition-and-evidence.md`: Disposition taxonomy, sub-item splitting, and 3-part missing-evidence rules.
- `references/tone-and-isolation.md`: Four-tier tone calibration, claim-strength verbs, reviewer isolation, and prose discipline.
- `references/output-contract-and-qa.md`: Exact `result.json` 5-field contract and pre-seal QA checklist.
- `examples/synthetic_example.md`: Minimal illustrative interface example.

## Dependencies and Runtime Requirements

- Python 3.12 standard library only (`argparse`, `difflib`, `json`, `re`, `pathlib`). Zero third-party packages and zero network access required.

## CLI Usage

```bash
python3 scripts/rebuttal_closure_checker.py \
  --baseline /input/materials/manuscript-baseline.md \
  --comments /input/materials/reviewer-comments.md \
  --revised /app/output/manuscript-revised.md \
  --response /app/output/response-letter.md \
  --missing-evidence /app/output/missing_evidence_list.md \
  --case-id "<CASE_ID>" \
  --out-json /app/output/rebuttal_ledger.json \
  --out-report /app/output/rebuttal_closure_report.md \
  --out-contract-patch /app/work/contract_patch.json
```

Exit code `0` indicates `"passed": true` (zero hard errors). Non-zero exit indicates at least one blocking closure, diff, anchor, evidence, fabrication, or isolation failure.

## Limitations and Boundaries

- Operates strictly on text/Markdown baselines and summaries supplied in `/input/`; does not perform external literature search, wet-lab experiments, or live journal submission.
- Semantic scientific validity of domain arguments remains the responsibility of the author/agent; the checker deterministically enforces structural completeness, verbatim grounding, diff truth, evidence logging, and tone/isolation guardrails.
