# Evidence-Anchored Literature Retrieval & Appraisal Protocol

This shared protocol distills field-tested research methodology into offline, verifiable rules for literature coverage auditing, systematic screening, quality appraisal, cross-lingual matching, and patent/technical retrieval.

## 1. Decompose the Scientific Question Before Inspecting Outputs

Before scoring, filtering, or attributing records, extract the minimal structured task frame from `/input/case.json` and `/input/task.md`:

- **Target Object & Context**: population, species/tissue/material, chemical/biological system, or technical device.
- **Comparison / Intervention / Mechanism**: treatment vs control, catalyst/process variant, exposure vs unexposed.
- **Data Structure & Inference Unit**: independent study / canonical work / patent family vs view / publication / replicate.
- **Protocol Constraints**: frozen date window, allowed languages, required study designs, source authorization tiers.
- **Decision Boundary**: what concrete decision this output changes (e.g., stop supplemental search, route record to human arbitration, downgrade evidence confidence, or keep patent families separate).

Keep any unstated constraint explicitly `unknown` rather than imputing defaults from retrieved records.

## 2. Five Epistemic Invariants

1. **Source-Grounded Separation of Facts vs Inferences**: Distinguish what the source explicitly reports (`explicit`), what is functionally equivalent after verified normalization (`verified_equivalence`), and what remains unverified or absent (`unverified` / `insufficient_evidence`).
2. **Identity Conservation Over Count Inflation**: Never count duplicate exports, preprint/VoR pairs, publisher translations, abstract-only mirrors, or multiple patent kind codes (A1/A4/B1/B2) as independent works or independent families.
3. **Never Silently Drop or Impute Missingness**: Missing abstracts, missing protocol-bound years, unretrieved full-texts, scanned/empty PDF layers, unreachable sources, and unknown live patent legal status must remain explicitly visible in dedicated queues or `UNKNOWN` states.
4. **Actionable Provenance Anchoring**: Every substantive judgment (omission cause, exclusion clause, risk-of-bias rating, cross-lingual alignment, claim-version attribution) must bind to a verifiable anchor (`source_file`, `record_id`, `query_id`, `paragraph_id` / `page_number`, and verbatim quote).
5. **Finite-Snapshot Honesty & Stopping Discipline**: Offline snapshots cannot establish live remote database coverage, global recall, exhaustive global patent families, or current official patent register status. Stop iterating once marginal gain reaches zero or no new evidence changes the decision.

## 3. Horizontal vs Vertical Verification

- **Horizontal Verification (Cross-Record)**: Compare records on what they actually measured, screened, or claimed—not merely superficial title words or method names. Keep conflicting findings and reviewer disagreements side-by-side instead of averaging or majority-voting them away.
- **Vertical Verification (Within-Record)**: Trace `objective -> design/sample -> measurement/analysis -> reported outcome -> explicit limitations` at sentence/paragraph granularity. Do not generalize abstract wording into full-text methodological verdicts.
