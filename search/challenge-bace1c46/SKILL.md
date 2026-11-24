---
name: study-quality-assessor
description: Appraise scientific literature methodological quality and risk-of-bias using study-type-routed, version-pinned scales (RoB 2, ROBINS-I V2, NOS/STROBE, AMSTAR-2, and Experimental/Computational Quality Framework) with sentence-level and page/section evidence anchors, explicit 4-level bias ratings paired with 5-state evidence levels, strict abstention on abstract-only or unparsed inputs (never turning unknown into low risk), and transparent tier derivation rules.
---

# Literature Study Quality & Risk-of-Bias Appraisal (`challenge-bace1c46`)

## 1. Mission & Non-Negotiable Invariants

This skill replaces ungrounded abstract paraphrases ("sound design, adequate sample, reliable conclusions") with an independently reviewable **`study_type -> versioned_scale -> domain -> bias_rating -> evidence_state -> sentence/page anchor -> explainable_overall_tier`** appraisal table over finite local inputs.

### Six Epistemic Invariants
1. **Study-Type Routing & Explicit Scale Version Pinning**: Select the appraisal scale matching the paper's actual design (or strictly bind the scale & version requested in `/input/case.json`, e.g., `ROBINS-I V2 (2024)` vs `RoB 2.0 (2019)` vs `AMSTAR-2 (2017)` vs `NOS/STROBE` vs `Experimental-Computational v1`). Always record `scale_name`, `scale_version`, and domain IDs so outdated template fields are never silently emitted for a newer scale version.
2. **Sentence-Level & Location-Anchored Provenance**: Every substantive rating (`explicitly_present`, `plausible_concern`, or `no_indication_observed`) must cite at least one exact verbatim sentence quote together with its `paragraph_id`, `section`, and `page_number` (when PDF pagination is available).
3. **Strict Abstention on Abstract-Only / Scanned Inputs**: Never infer full-text methodological details (allocation concealment, attrition handling, multiple-testing correction, statistical inference unit, or code/data availability) from an abstract or an empty/scanned PDF layer. Assign `bias_rating: "insufficient_evidence"` and `evidence_state: "UNKNOWN"`—**an unknown field must NEVER silently default to low risk (`no_indication_observed`)**.
4. **Separate Four-Level Bias Scale & Five-State Evidence Scale**:
   - Risk-of-Bias: `explicitly_present` | `plausible_concern` | `no_indication_observed` | `insufficient_evidence`
   - Evidence Support: `SUPPORTED` | `PARTIAL` | `REFUTED` | `OUTDATED` | `UNKNOWN`
5. **Explainable Tier Aggregation (No Orphan Labels)**: Derive `overall_quality_tier` through an explicit, auditable rule trace (`tier_derivation_rule`) referencing exact domain ratings; never output an unexplained summary grade.
6. **Inference-Unit, Causality & Reproducibility Checks**: Explicitly inspect whether sample/inference unit is reported (avoiding pseudoreplication), whether observational designs overclaim causality, whether preprints lack peer review, and whether Data/Code Availability is disclosed.

## 2. Information Hierarchy & References

Consult on demand (paths relative to this `SKILL.md`):
- `references/output_contract_spec.md` — schema for `/app/output/result.json`, `quality_appraisal_table.json`, and `quality_appraisal_report.md`.
- `references/scales_and_bias_rubric.md` — version-pinned domain definitions (RoB 2.0, ROBINS-I V2, NOS/STROBE, AMSTAR-2, Experimental/Computational), abstract-vs-fulltext boundary table, and tier derivation thresholds.
- `references/search_methodology_protocol.md` — vertical verification discipline and finite-snapshot boundaries.

## 3. Step-by-Step Execution Workflow

Run all commands from the directory containing this `SKILL.md` (or resolve `scripts/` relative to it).

### Step 1: Preflight Environment & Budget Initialization
```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
```
**Completion criterion**: `/app/work/env.json` confirms readable `/input` records/PDFs/JSON and writable `/app/output`.

### Step 2: Execute Scale-Routed Quality Appraisal Engine
```bash
python -B scripts/study_quality_evaluator.py --input /input --output /app/output --work /app/work
```
What this engine deterministically performs:
1. **Material Tier Classification**: Inspects each record's text layer (`fulltext_verified`, `abstract_only`, `scanned_or_unparsed_pdf`, `metadata_only`) and publication status (`peer_reviewed_or_unspecified`, `preprint`, `retracted_or_erratum`).
2. **Study-Type Detection & Version-Pinned Scale Selection**: Routes each study to the matching appraisal scale (respecting any explicit `scale` / `scale_version` / `custom_domains` provided in `/input/case.json`), ensuring domain labels and versions match the active schema.
3. **Sentence-Level Evidence Extraction & Dual-Scale Rating**: Evaluates every domain across the record's anchored paragraphs (skipping fulltext-only domains cleanly when input is abstract-only or scanned), extracting exact matching sentences with `paragraph_id`, `section`, and `page_number`.
4. **Explainable Aggregation & Traffic-Light Summary**: Computes `overall_quality_tier` with an explicit `tier_derivation_rule`, preserves any supplied human-review overrides/disagreements (`human_review_delta`), and generates `/app/output/quality_appraisal_table.json`, `/app/output/quality_appraisal_report.md` (including a domain traffic-light matrix synchronized 1:1 with raw domain rationales), and `/app/output/result.json`.

**Completion criterion**: All 3 deliverables exist, every non-UNKNOWN rating carries a verbatim sentence anchor, and traffic-light summary icons match the JSON domain ratings 100%.

### Step 3: Independent Reviewer Calibration Check (Host Agent)
Inspect `/app/output/quality_appraisal_table.json` and `/app/output/quality_appraisal_report.md`:
1. Verify every `abstract_only` or `scanned_or_unparsed_pdf` study abstained (`insufficient_evidence` / `UNKNOWN`) on full-text-only domains and never received a low-risk pass by default.
2. Verify any specified scale version (e.g., `ROBINS-I V2`) uses its exact domain schema rather than legacy field names.
3. Check whether an observational study makes unsupported causal claims or omits sample/inference units or code/data availability, and confirm the corresponding domain rationale quotes the exact text.

### Step 4: Grounding Verification & Final Deliverable Gate
```bash
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require quality_appraisal_table.json quality_appraisal_report.md
```
**Completion criterion**: `final_audit.py` exits `0` with `passed: true`.

## 4. Edge-Case & Degraded-Input Behavior

| Condition | Required Behavior |
| --- | --- |
| Abstract-only record (`fulltext_available: false` or only abstract supplied) | Appraise abstract-assessable design cues only; set all fulltext-only domains to `insufficient_evidence` + `UNKNOWN`; set tier to `incomplete_material_abstract_only` if `>= 3` domains are `UNKNOWN`. |
| Scanned/empty PDF (`text_layer_status: empty_or_scanned`) | Never guess from filename/title; mark `material_tier: scanned_or_unparsed_pdf`, abstain (`UNKNOWN`), and emit explicit gap `PDF_TEXT_LAYER_UNAVAILABLE`. |
| Preprint record (arXiv/bioRxiv/medRxiv) | Flag `publication_status: preprint_not_peer_reviewed` explicitly in transparency domain without auto-rejecting scientific content. |
| Retracted / erratum / expression of concern | Flag `bias_rating: explicitly_present`, `evidence_state: OUTDATED`, and `overall_quality_tier: high_risk_or_outdated`. |
| Explicit scale version in `case.json` (e.g. `ROBINS-I V2`) | Bind exact requested version schema; flag `UNKNOWN` on any unassessable domain instead of collapsing to low risk. |

## 5. Hard Boundaries
- Appraisal supports methodological screening and reproducibility review; it does NOT substitute for formal peer review, clinical guidance, or policy decisions.
- Never fabricate statistical tests, p-values, sample sizes, or blinding details absent from supplied text.
