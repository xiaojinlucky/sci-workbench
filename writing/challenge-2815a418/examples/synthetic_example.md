# Minimal End-to-End Illustrative Walkthrough (`rebuttal-revision-closure`)

> **Example Notice**: This file is a self-contained illustrative example demonstrating the CLI workflow and schemas. It is not an official formal test case or prepared benchmark answer.

## 1. Illustrative Input Files

### `mini-baseline.md`
```markdown
# Study Summary
## Methods
We analyzed 240 retrospective records (2018-2022) from public registry data (DOI: 10.1000/example.2022.01).
## Results
Event rates rose from 12.4% to 18.7% (p < 0.01), proving that household exposure directly caused the increase.
```

### `mini-comments.md`
```markdown
## Reviewer #1
R1.1 Please address two issues:
(a) Soften the causal claim in Results because the design is retrospective.
(b) Provide a new prospective multicenter cohort validating this mechanism.
R1.2 Clarify the study period and sample size in Methods.
```

## 2. Command Sequence

```bash
python3 scripts/rebuttal_closure_checker.py \
  --extract-review-items --from-input ./input --work-dir ./work --out-json ./work/review_items.json

python3 scripts/rebuttal_closure_checker.py \
  --extract-anchors --baseline ./work/baseline_extracted.md --revised ./output/manuscript-revised.md \
  --out-json ./work/extracted_anchors.json

python3 scripts/rebuttal_closure_checker.py \
  --baseline ./work/baseline_extracted.md --comments ./work/comments_extracted.md \
  --revised ./output/manuscript-revised.md --response ./output/response-letter.md \
  --missing-evidence ./output/missing_evidence_list.md --case-id "example-001" \
  --out-json ./output/rebuttal_ledger.json --out-report ./output/rebuttal_closure_report.md \
  --out-contract-patch ./work/contract_patch.json
```

## 3. Illustrative Output Snippets

### `output/manuscript-revised.md` (edited excerpt)
```markdown
Event rates rose from 12.4% to 18.7% (p < 0.01), suggesting an observational association with household exposure; prospective multicenter validation remains outside the present retrospective registry scope.
```

### `output/response-letter.md` (excerpt)
```markdown
### R1.1(a)
Disposition: SOFTEN_CLAIM
We replaced causal wording with observational association framing in [§Results L5].
> Event rates rose from 12.4% to 18.7% (p < 0.01), suggesting an observational association with household exposure; prospective multicenter validation remains outside the present retrospective registry scope.

### R1.1(b)
Disposition: PARTIAL
Collecting a new prospective multicenter cohort lies outside this controlled retrospective baseline scope. We added an explicit scope limitation in [§Results L5] and logged required author inputs in `missing_evidence_list.md`.
> prospective multicenter validation remains outside the present retrospective registry scope.

### R1.2
Disposition: CLARIFY_EXISTING
The retrospective sample size (240 records) and period (2018-2022) are reported in [§Methods L3].
> We analyzed 240 retrospective records (2018-2022) from public registry data (DOI: 10.1000/example.2022.01).
```

### `output/missing_evidence_list.md` (excerpt)
```markdown
### R1.1(b)
- **Boundary Rationale**: Prospective multicenter cohort collection is outside the controlled retrospective registry baseline scope and cannot be performed offline.
- **Existing-Material Alternative**: Added explicit observational-scope limitation in [§Results L5] without fabricating prospective cohort data.
- **Author Confirmation Required**: Authors must confirm whether prospective validation data exist in an external dataset or retain the stated limitation for editorial review.
```
