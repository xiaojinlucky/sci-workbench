# Disposition Classification, Sub-Item Decomposition, and Missing-Evidence Protocol

## 1. Canonical Disposition Tags (Choose Exactly One Primary Tag Per Item)

| Tag | When to Use | Manuscript Edit Required? | Missing-Evidence Entry? |
| --- | --- | --- | --- |
| `ACCEPT_TEXT` | Reviewer requests wording, methods clarity, structural organization, table/legend clarity, or scope detail supported by existing input. | Yes (token-level diff verified) | No |
| `CLARIFY_EXISTING` | Existing baseline text already contains the fact, but presentation invited confusion; answer directly and improve signposting if helpful. | Optional (existing text anchor verified) | No |
| `SOFTEN_CLAIM` | Original causal, clinical, mechanistic, or novelty claim exceeds supplied evidence; narrow verb strength and add boundary qualifier. | Yes (token-level diff verified) | No |
| `PARTIAL` | Valid request contains both an executable textual/limitation part and an unfeasible experimental/data part. | Yes for the executable part | Yes (mandatory 3-part entry) |
| `DISAGREE_WITH_EVIDENCE` | Reviewer premise conflicts with manuscript facts, data scope, or methodology; rebut narrowly with cited evidence. | Optional (clarification recommended) | Only if external verification is pending |
| `AUTHOR_INPUT_NEEDED` | Request requires unavailable wet-lab data, raw files, ethics approval, author decision, or unverified external citations. | Only safe limitation/boundary note if appropriate | Yes (mandatory 3-part entry) |

## 2. Sub-Item Decomposition Rules

- Preserve exact IDs from the comment summary (`E.1`, `R1.1`, `R1.1a`, `R1.1(b)`, `Comment 1`, `审稿人1意见1`).
- When a single comment contains multiple distinct requests (marked by `(a)`/`(b)`, `(1)`/`(2)`, `①`/`②`, or separate imperative clauses asking for both a method clarification and a new validation experiment), split into explicit sub-items (`R1.2(a)` and `R1.2(b)`).
- Assign each sub-item its own disposition tag so an unfeasible sub-request (`R1.2(b) -> AUTHOR_INPUT_NEEDED`) never blocks an executable sub-request (`R1.2(a) -> ACCEPT_TEXT`).

## 3. Work Status vs Package Readiness Calibration

Keep execution truth separate from prose drafting:
- `VERIFIED_DONE`: Checked by `rebuttal_closure_checker.py` against an actual diff or verified baseline anchor.
- `PENDING_AUTHOR_CONFIRM`: Requires author factual sign-off, raw data, or decision.
- `NOT_FEASIBLE_IN_CONTROLLED_SCOPE`: Requires new experiments, new cohorts, or full-manuscript files outside the supplied baseline summary.

Derive `package_readiness` conservatively:
- `ready_to_submit`: Zero `AUTHOR_INPUT_NEEDED` / blocking items remain and `rebuttal_closure_checker.py` passes with zero errors.
- `needs_author_input`: Executable baseline edits are complete and verified, while one or more `PARTIAL` / `AUTHOR_INPUT_NEEDED` items await author evidence or approval (set `result.json` `status` to `"partial_with_gaps"`).

## 4. Mandatory 3-Element Missing-Evidence Protocol

For every `AUTHOR_INPUT_NEEDED`, `PARTIAL`, `NOT_FEASIBLE`, or `OUT_OF_SCOPE` item, record a structured entry in `/app/output/missing_evidence_list.md` using these exact headings or labels:

```markdown
### R1.4 — Independent External Cohort Validation
- **Disposition**: PARTIAL (unfeasible new data + baseline claim softening)
- **Boundary Rationale**: Controlled revision scope supplies only the baseline summary without raw patient cohorts or institutional IRB access for new enrollment.
- **Existing-Material Alternative**: Added an explicit scope boundary in [§Discussion L44-L47] restricting conclusions to the reported internal dataset and softened generalizability verbs.
- **Author Confirmation Required**: Confirm whether an external validation dataset already exists in internal records; if not, approve keeping the single-cohort limitation statement for resubmission.
```

Never write open-ended promises ("we will perform this experiment before publication"). Ground every boundary in scientific scope and available evidence.
