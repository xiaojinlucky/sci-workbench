# Minimal Synthetic Invocation Example (`rebuttal-revision-closure`)

> **Notice**: This is a synthetic interface illustration only and does not represent any official evaluation case or test answer.

## Synthetic Input Excerpts

### `materials/manuscript-baseline.md`
```markdown
# Methods
Samples (n = 40) were evaluated using Assay X.

# Results
Assay X proved causal improvement across all patient cohorts (p = 0.012).
```

### `materials/reviewer-comments.md`
```markdown
- R1.1a: Clarify the Assay X detection threshold in Methods.
- R1.1b: Soften the causal claim in Results because the design is observational.
- R1.2: Add an independent multicenter validation cohort (n >= 200).
```

## CLI Invocation

```bash
python /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --baseline /input/materials/manuscript-baseline.md \
  --comments /input/materials/reviewer-comments.md \
  --revised /app/output/manuscript-revised.md \
  --response /app/output/response-letter.md \
  --missing-evidence /app/output/missing_evidence_list.md \
  --case-id "SYNTHETIC-EXAMPLE-01" \
  --out-json /app/output/rebuttal_ledger.json \
  --out-report /app/output/rebuttal_closure_report.md \
  --out-contract-patch /app/work/contract_patch.json
```

## Illustrative Output Snippet (`rebuttal_ledger.json`, synthetic example)

```json
{
  "passed": true,
  "expected_comment_ids": ["R1.1a", "R1.1b", "R1.2"],
  "answered_comment_ids": ["R1.1a", "R1.1b", "R1.2"],
  "missing_responses": [],
  "triple_bound_edits_verified": 2,
  "missing_evidence_closed_count": 1,
  "fabrication_hits": [],
  "rebuke_hits": [],
  "cross_reviewer_leaks": [],
  "errors": []
}
```
