# Evidence Grading Rubric, Conflict Synthesis & CNS Narrative Standards

## 1. Strict 5-State Evidence Verdict Rubric

| Verdict | Exact Trigger Condition | Required Writing Action |
| --- | --- | --- |
| `SUPPORTED` | Cited full-text source exists in `/input`; verbatim excerpt matches claim topic, polarity, applicability conditions, and study-design causal strength | Retain claim as written with locator in `evidence_matrix.md` |
| `PARTIALLY_SUPPORTED` | Source supports topic/direction, BUT claim escalates observational correlation to causation, omits critical applicability conditions, or overstates a single/preprint study as consensus | Calibrate verb strength (`is associated with`, `suggests`) and restore exact applicability boundary |
| `CONTRADICTED` | Cited source reports opposite direction or null effect (`no significant difference`, `failed to improve`) while claim asserts positive effect without acknowledging divergence | Correct claim polarity or reframe explicitly around conflict conditions |
| `UNVERIFIABLE_ABSENT` | Source is absent from `/input`, or only an abstract/citation stub is supplied without full text | Mark `[EVIDENCE_GAP]` / `abstract_only`; **never** accuse of fabrication without falsifying evidence |
| `EXISTENCE_ONLY_NOT_SUPPORTING` | Source record/file exists in `/input`, but its actual text addresses a different topic/endpoint and does not support the claim | Remove misattributed citation and bind a genuinely supporting source |

## 2. Study-Design Strength Alignment Table

| Cited Study Design / Source Type | Permitted Claim Verbs | Prohibited Escalations |
| --- | --- | --- |
| `rct_or_intervention` (RCT, double-blind, CRISPR/knockout perturbation) | `demonstrates`, `reduces`, `improves`, `establishes under [conditions]` | Generalizing beyond tested cohort/protocol without boundary |
| `observational_correlational` (retrospective, cross-sectional, cohort correlation) | `is associated with`, `correlates with`, `suggests`, `co-occurs with` | `proves`, `causes`, `drives`, `directly triggers`, `establishes causality` |
| `in_vitro_preclinical` (cell line, animal model) | `in vitro assays indicate`, `in murine models`, `preclinical evidence suggests` | Clinical efficacy claims in patients |
| `preprint` (bioRxiv, medRxiv, arXiv) | `preliminary non-peer-reviewed report suggests` | `established consensus`, `definitively proved` |
| `abstract_only` | Explicitly tag `(abstract only; full methods unverified)` | Citing methods/subgroup details absent from abstract |

## 3. Conflict Synthesis vs. Paper-by-Paper Recitation

When supplied literature contains conflicting findings across studies:
1. **Forbidden anti-pattern (Paper-by-Paper Recitation)**: Writing "Study 1 found X. Study 2 found Y. Study 3 found Z." without connecting them to the current research question or explaining why they differ.
2. **Required pattern (Condition-Bounded Synthesis)**:
   - Open with the direct synthesis answering the target research question.
   - Contrast divergent outcomes along explicit **applicability dimensions** (e.g., acidic vs. neutral pH, acute vs. chronic cohort, in vitro vs. clinical endpoint).
   - Keep correlational evidence explicitly bounded as associative support rather than causal tie-breaking proof.
   - State clearly what remains unresolved as an explicit research gap.

## 4. CNS Narrative & Press-Release Principle Integration

- Anchor every paragraph to one explicit analytical point supporting the research question (no experimental diary or aimless literature dump).
- Eliminate AI filler (`delve into`, `pivotal role`, `综上所述`, `值得注意的是`) and self-undermining hedges (`遗憾的是`, `merely a preliminary attempt`) in main synthesis paragraphs while keeping genuine scientific uncertainty precise and concrete.
