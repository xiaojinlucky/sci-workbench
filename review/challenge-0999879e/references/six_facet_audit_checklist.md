# Six-Facet Manuscript Quality Audit Checklist (`challenge-0999879e`)

Use this checklist during Step 3 to verify all six official review facets and keep `method_flaw`, `under_reported`, `needs_reconciliation`, and `suggestion` strictly separated.

## Facet 1: `fact_verification` (Internal Numerical, Sample & Visual Consistency)
- [ ] **Abstract vs Body vs Tables**: Compare every primary percentage, sample count, p-value, and direction of effect between Abstract, Results, Tables, and Conclusion. Flag contradictions with line anchors on *both* sides.
- [ ] **Sample-Size Conservation**: Check `total enrolled N - excluded/lost = analyzed N = sum(subgroup n_i)`. If unmatched and no attrition flow is reported -> `factual_error` / `needs_reconciliation` (`major`). If attrition is mentioned but arithmetic is incomplete -> `needs_human_review` (`minor`).
- [ ] **Figure/Table Cross-References**: Every in-text `Fig. k` / `Table k` maps to an actual caption/table; no unreferenced main figures; multi-panel letters (`A, B, C...`) are contiguous.
- [ ] **Visual Asset Integrity**: Check supplied images for exact byte duplication or flipped/rotated perceptual duplication (`Hamming <= 4/64`). Report neutrally as `needs_human_review` (`needs_investigation`), never accusing intent.

## Facet 2: `citation_support` (Source Binding & Attribution Boundaries)
- [ ] **Bibliography Closure**: No dangling in-text citation keys; note uncited reference entries (`minor`).
- [ ] **Source Status**: Flag explicit `Retracted` / `Withdrawn` / `Erratum` markers (`critical`); mark preprints (`arXiv`/`bioRxiv`/`medRxiv`) transparently when relied on as established fact.
- [ ] **Citation Marker != Causal Proof**: In observational studies, causal claims bearing citations remain `needs_human_review` unless inspected source text actually establishes causal identification.
- [ ] **No Unsupported Prior-Work Numbers**: Flag specific numerical claims attributed to "prior studies" without any citation anchor.

## Facet 3: `method_design` (Design Validity vs Reporting Omissions)
- [ ] **Design-Question Fit**: Does the study design (RCT, observational cohort, cross-sectional, cell/animal model) actually answer the research question?
- [ ] **Comparator & Bias Controls**: Distinguish missing comparator in a comparative efficacy claim (`method_flaw`, `major`) from an unstated randomization/blinding detail (`under_reported`, `major` / `needs_human_review`).
- [ ] **Statistical Unit & Pseudoreplication**: Verify `df` and `N` reflect independent experimental units rather than pooled technical replicates/cells/slices (`df > N` -> `method_flaw`, `major`).
- [ ] **Multiplicity Transparency**: Multiple tests (`>= 3`) without adjustment disclosure or pre-specified primary endpoint -> `under_reported` (`needs_human_review`), NOT an automatic accusation of p-hacking.

## Facet 4: `statistical_reporting` (Completeness & Precision)
- [ ] **Effect Sizes & Uncertainty**: Primary p-values accompanied by effect size (`OR`/`HR`/`Cohen's d`/`beta`/`mean difference`) and uncertainty (`95% CI`, `SD`, or `IQR`). Missing -> `under_reported` (`minor`).
- [ ] **Error-Bar Definitions**: Figure legends mentioning error bars or `±` must define `SD` vs `SEM` vs `95% CI` and `n`.
- [ ] **Reporting Norms**: Flag `p = 0.000` (must be `p < 0.001` or scientific notation) and spin phrases (`marginally significant`, `trending toward significance`) as `norm_violation` (`minor`).

## Facet 5: `argument_chain` (Claim-Evidence Alignment & Boundaries)
- [ ] **Author vs Non-Author Filter**: Exclude cited prior views, explicit hypotheses, negative controls, and stated limitations before flagging contradictions or overclaims.
- [ ] **Causal Overreach**: Observational/correlational designs using `proves` / `causes` / `guarantees` / `必然导致` without causal identification -> `method_flaw` (`major`).
- [ ] **Intro-to-Conclusion Symmetry**: Every primary aim promised in Introduction is addressed in Methods/Results and reflected proportionally in Conclusions.
- [ ] **Scope & Generalizability**: Check whether conclusions extrapolate beyond tested species, cohort, or conditions without justification.

## Facet 6: `academic_norms` (Structure & Publication Disclosures)
- [ ] **IMRaD Completeness**: Check Abstract, Introduction, Methods, Results, Discussion (accepting merged `Results and Discussion` as valid without false positives).
- [ ] **Mandatory Disclosures**: Human/clinical or animal signals without IRB/IACUC/informed consent -> `norm_violation` (`critical`). Missing COI, funding, or data/code availability -> `norm_violation` (`minor`).
- [ ] **Fairness Self-Check**: Verify every `critical`/`major` finding survives ACL H1-H17; downgrade stylistic or speculative extras to `suggestion` (`P3`) or drop.
