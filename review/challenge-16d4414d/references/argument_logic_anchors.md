# Argument Logic & Breakpoint Anchors (challenge-16d4414d)

## 1. Three-Way Sentence Identity Rules (Anti-False-Positive Core)
Before checking causal overreach, scale extrapolation, or self-contradiction, classify each sentence:

| Identity | Trigger Cues | Audit Treatment |
| --- | --- | --- |
| `author_claim` | Direct assertion by authors without prior-attribution framing or control/hypothesis caveat | Eligible for argument-graph extraction and breakpoint auditing |
| `cited_prior_view` | Prior-attribution verbs (`prior studies reported`, `Smith et al. claimed`, `前人研究认为`) or citation-anchored non-first-person statements | **Excluded** from author self-contradiction and author causal-overclaim flags |
| `explicit_control_or_hypothesis` | Negative/positive control, placebo, ablation, hypothesis (`suppose`, `assuming`), negated causal disclaimer (`cannot prove causality`, `不能证明因果`), or author-disclosed limitation | **Excluded** from causal-overreach and over-extrapolation flags |

## 2. Argument-Chain Reconstruction (`Claim -> Evidence -> Warrant -> Boundary`)
For every core claim in Abstract, Introduction, Results, Discussion, and Conclusion, bind four slots:
1. **Claim**: Exact line-anchored statement and strength (`causal`, `universal_extrapolation`, `objective_promise`, `empirical_assertion`, `hedged_associative`).
2. **Evidence**: Specific anchor (`Table X`, `Fig. Y`, exact `p`/`CI`/`n`/`%`, or `unanchored_in_sentence`).
3. **Warrant**: Inferential bridge connecting design/evidence to claim (flags `observational_to_causal_leap`, `scale_or_sample_extrapolation`, `cross_section_numeric_conflict`, `unverified_promise`).
4. **Boundary**: Explicit model, cohort, or design scope (`in vitro`, `mouse model`, `single-center`, `observational_design`, `hedged`, or `unspecified`).

## 3. Six Logical Breakpoints & Severity Calibration

| Breakpoint | Subtype | Finding Type | Severity | Concrete Actionable Fix |
| --- | --- | --- | --- | --- |
| B1. Causal overreach in observational study | `causal_overreach` | `insufficient_evidence` | `major` | Replace causal verb with associative wording (`is associated with` / `suggests`) or document causal identification |
| B2. Intro promise missing in Methods/Results | `intro_delivery_fracture` | `insufficient_evidence` | `major` | Add missing analysis subsection or narrow the Introduction objective |
| B3a. Abstract/Conclusion direction or significance reversal vs Results | `cross_section_direction_reversal` / `cross_section_significance_reversal` | `factual_error` | `critical` | Reconcile direction/significance against raw results table at both line anchors |
| B3b. Abstract/Conclusion percentage conflicts with matched Results metric (`> 0.5pp`) | `cross_section_numeric_conflict` | `factual_error` | `major` | Verify denominator/table and harmonize exact percentages |
| B3c. Unmatched summary percentage without direct partner | `unmatched_summary_percentage` | `insufficient_evidence` | `minor` (`needs_human_review`) | Verify whether value appears only in a table/figure before editing |
| B4. Preclinical/animal/single-center extrapolated to universal clinical efficacy | `scale_over_extrapolation` | `insufficient_evidence` | `major` | Scope claim to tested model/cohort and note need for multicenter clinical validation |
| B5. Missing limitations or boundary conditions in Discussion/Conclusion | `missing_limitations` | `suggestion` | `suggestion` (`P3`) | Add concise Limitations paragraph; never blocks `passed` |
| B6. Multi-round revision closure failure | `revision_unclosed_claimed_fixed` / `revision_unclosed_unaddressed` | `factual_error` / `insufficient_evidence` | `major` | Commit promised edit to revised draft or correct rebuttal with transparent rationale |

## 4. Common False-Positive Traps to Avoid
- **Trap 1 (Negated causal disclaimer)**: "Because our design is retrospective, it cannot prove that X causes Y." -> `explicit_control_or_hypothesis`, NOT `causal_overreach`.
- **Trap 2 (Cited controversy)**: "Previous studies claimed that drug X directly causes recovery [12]; however, our cohort found no association." -> `cited_prior_view`, NOT author contradiction.
- **Trap 3 (Rounding compatibility)**: Abstract says `32%` while Results says `32.4%` -> within `0.5pp` rounding tolerance, NOT a numeric conflict.
- **Trap 4 (Disclosed limitation)**: Author explicitly states "A key limitation is the small single-center cohort" -> compliant with ACL H16; never penalize.
