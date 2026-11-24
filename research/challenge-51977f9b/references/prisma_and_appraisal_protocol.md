# PRISMA 2020 Screening & 5-Domain Quality Appraisal Rubric

## 1. Clause-Priority Screening Order
1. Year window (`EXC_OUT_OF_YEAR_WINDOW`)
2. Language restriction (`EXC_LANGUAGE_RESTRICTION`)
3. Excluded study type (`EXC_STUDY_TYPE`)
4. Pre-declared exclusion clauses (`EXC_EXCLUSION_CLAUSE`) — if a record also matches required concepts, queue for dual-reviewer audit while retaining deterministic exclusion priority
5. Missing required concept (`EXC_MISSING_REQUIRED_CONCEPT`)
6. Full-text retrieval check (`NOT_RETRIEVED_FULLTEXT_REQUIRED` when `fulltext_required: true`)
7. Full-text exclusion clauses (`EXC_FULLTEXT_EXCLUSION_TERM`)

## 2. Five-Domain Appraisal Scale (`SLR-Appraisal-v2-MultiDomain`)
| Domain | What Is Checked | Missing in Abstract | Missing in Full Text |
|---|---|---|---|
| D1 Study Design & Protocol | Explicit design/methodology stated | `NO_INFORMATION` | `NO_INFORMATION` |
| D2 Sample & Inference Unit | Sample size / biological or analytical replicate unit reported | `NOT_ASSESSABLE_FROM_ABSTRACT` | `NO_INFORMATION` |
| D3 Comparator & Controls | Control group, baseline, or ablation comparator reported | `NOT_ASSESSABLE_FROM_ABSTRACT` | `NO_INFORMATION` |
| D4 Bias, Confounding & Transparency | Limitations, confounding, COI, or data/code availability disclosed | `NOT_ASSESSABLE_FROM_ABSTRACT` | `NO_INFORMATION` |
| D5 Full-Text Access & Verifiability | Full PDF/XML/text inspected (`LOW_RISK`) vs abstract-only (`HIGH_RISK`) | `HIGH_RISK` | `LOW_RISK` |

Never convert `NOT_ASSESSABLE_FROM_ABSTRACT` or `NO_INFORMATION` into `LOW_RISK`.
