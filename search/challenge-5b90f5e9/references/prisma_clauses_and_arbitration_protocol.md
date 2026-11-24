# PRISMA 2020 Clauses, Reason Codes & Conflict Arbitration Protocol

## 1. Frozen Protocol & Criteria-Drift Prevention

Before screening record #1, serialize the eligibility protocol (`version`, `year_min`, `year_max`, `allowed_languages`, `required_terms`, `exclude_terms`, `exclude_designs`, `require_fulltext`) and compute `protocol_sha256`.
- Any mid-stream change to an eligibility clause increments `version` and triggers retroactive comparison (`criteria_drift_audit`) against all previously screened records.
- Never silently keep old screening labels under a modified criterion.

## 2. Canonical Reason-Code Dictionary

| Code | Outcome | When Triggered |
| --- | --- | --- |
| `INC_MATCH` | `include` | Satisfies all frozen inclusion terms, language, design, and date bounds. |
| `EXC_WRONG_LANGUAGE` | `exclude` | Declared language falls outside `allowed_languages`. |
| `EXC_OUT_OF_DATE_RANGE` | `exclude` | Known publication year lies outside `[year_min, year_max]`. |
| `EXC_WRONG_DESIGN` | `exclude` | Non-empirical / excluded study type (editorial, commentary, erratum, retraction, protocol). |
| `EXC_WRONG_POPULATION_OR_OBJECT` | `exclude` | Matches explicit exclusion term or matches zero required inclusion clauses. |
| `FULLTEXT_NOT_RETRIEVED` | `not_retrieved` | Passed title/abstract screening, but full text cannot be retrieved (counted in PRISMA `n_not_retrieved`, never as `n_screen_excl`). |
| `BORDERLINE_NEEDS_ARBITRATION` | `borderline` | Dual-reviewer disagreement or missing abstract/body text. |
| `BORDERLINE_MISSING_REQUIRED_METADATA` | `borderline` | Potentially eligible record lacks a protocol-bounded field (e.g., `year: null` when `year_min`/`year_max` is active). |
| `BORDERLINE_PARTIAL_CLAUSE_MATCH` | `borderline` | Matches only part of multi-clause required terms; needs human inspection. |

## 3. Dual-Reviewer Conflict Classification

- **`include_vs_exclude`**: Highest priority; mandatory third-reviewer arbitration. Never average or silently drop one reviewer.
- **`decision_vs_borderline`**: Routed to arbitration with both original rationales preserved.
- **`reason_code_disagreement`**: Both excluded the record with different codes; retained in conflict log for PRISMA exclusion-breakdown reconciliation.
- Synthetic reviewer opinions or synthetic duplicate exports provided as test fixtures must stay explicitly labeled as input fixtures rather than claiming real human review.
