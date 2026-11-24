# Version-Pinned Appraisal Scales, Material Tiers, Dual-Review Protocol & Aggregation Rules

## 1. Study-Type Routing & Version Pinning

Always record both `scale_name` and `scale_version` in machine and human outputs to eliminate schema drift (e.g., preventing legacy 2016 ROBINS-I domain fields from being emitted when evaluating under ROBINS-I V2 (2024)):

| Study Design | Default Scale & Version | Domains | Signaling Checkpoints |
| --- | --- | --- | --- |
| Randomized Controlled Trial (RCT) | Cochrane RoB 2.0 (2019) | 5 | D1 randomization & allocation concealment; D2 blinding & ITT adherence; D3 missing outcome data/attrition; D4 blinded/validated outcome measurement; D5 pre-registration (NCT/ChiCTR/ISRCTN) & selective reporting |
| Non-Randomized Intervention | ROBINS-I V2 (2024) | 7 | D1 confounding adjustment (propensity/IPTW/DAG); D2 intervention classification; D3 participant selection/target trial; D4 deviations & co-interventions; D5 missing data/imputation; D6 blinded/objective outcome measurement; D7 pre-specified SAP & reported result selection |
| Systematic Review / Meta-Analysis | AMSTAR-2 (2017) | 5 grouped | D1 PICO & PROSPERO/a priori protocol; D2 multi-database reproducible Boolean search; D3 duplicate independent screening/extraction & Kappa; D4 RoB appraisal + heterogeneity (`I^2`)/publication bias; D5 funding, COI & data/code availability |
| Experimental / Observational / Computational | Structured Scientific Quality v1.0 | 5 | D1 study design, comparator & causal scope; D2 sample size, biological/inference unit (pseudoreplication check); D3 measurement validity, calibration & primary endpoint; D4 statistical tests, uncertainty (CI/SD/IQR) & multiple-testing correction (FDR/Bonferroni); D5 limitations, ethics/IRB/ARRIVE & Data/Code availability |

## 2. Boundary Comparison vs Generic Summaries & Static Checklists

- **vs Generic LLM Abstract Summaries**: Abstract summaries conflate manuscript prose with verified methodology and fabricate "adequate sample / sound design" verdicts. This protocol mandates sentence-level anchors (`paragraph_id`, `section`, `page_number`) and forbids inferring full-text items from abstracts.
- **vs Static Reporting Checklists (CONSORT/STROBE/ARRIVE)**: Reporting checklists track presence/absence of reporting items but do not separate bias direction, evidence state (`SUPPORTED` vs `REFUTED` vs `OUTDATED` vs `UNKNOWN`), or study-design-specific risk domains.
- **vs Journal Desk Triage**: Desk triage outputs an opaque pass/reject decision; this skill exposes an explicit `tier_derivation_rule` and `ranked_evidence_candidates` triage list so a second reviewer can reproduce or override any domain independently.

## 3. Material Tier & Strict Abstention Boundary

| `material_tier` | Fulltext-Only Domains | Required Behavior |
| --- | --- | --- |
| `fulltext_verified` | Evaluated with sentence-level quotes | Unreported checkpoints stay `insufficient_evidence` / `UNKNOWN` (never default to low risk) |
| `abstract_only` | **Mandatory abstention** | Assign `insufficient_evidence` / `UNKNOWN` to all fulltext-only domains; tier `incomplete_material_abstract_only` when `>= half` unknown |
| `scanned_or_unparsed_pdf` | **Mandatory abstention** | Overall tier `unassessable_scanned_pdf` + explicit gap `PDF_TEXT_LAYER_UNAVAILABLE` |
| `metadata_only` | **Mandatory abstention** | Never score quality from title, author reputation, or journal name |

## 4. Explainable Overall-Tier Derivation Rules & Candidate Triage

- **T1 (`high_risk_or_outdated`)**: Any domain `OUTDATED` (retraction/erratum/withdrawal) -> `exclude_or_flag_retracted_outdated`.
- **T2 (`unassessable_scanned_pdf`)**: Empty/scanned PDF text layer -> `hold_pending_ocr_or_clean_pdf`.
- **T3 (`incomplete_material_abstract_only`)**: Missing full text with `>= half` domains `UNKNOWN` -> `hold_pending_fulltext_retrieval`.
- **T4 (`moderate_or_high_concern`)**: `>= 1` `explicitly_present`/`REFUTED` OR `>= 2` `plausible_concern`/`PARTIAL` domains -> `downgrade_or_sensitivity_analysis`.
- **T5 (`incomplete_reporting`)**: Full text present, yet `> half` domains unreported (`UNKNOWN`) -> `retain_with_reporting_caveat`.
- **T6 (`low_observed_concern`)**: All domains grounded (`no_indication_observed` / `SUPPORTED`) -> `retain_primary_evidence`.
- **T7 (`mixed_needs_review`)**: Mixed grounded/partial/unknown domains -> `retain_with_targeted_domain_verification`.

## 5. Dual-Reviewer Reconciliation & Scale Evolution

When a second human reviewer overrides a domain via `human_review_overrides`, preserve both `automated_rating` and `human_rating` in `human_review_delta[]` with `human_rationale`. Repeated human overrides on a specific domain signal a scale/pattern calibration update rather than a one-off silent edit.
