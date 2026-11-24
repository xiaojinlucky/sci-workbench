# Atomic Claim Taxonomy, 4-State Entailment Rubric & Counterexamples

## 1. Five Atomic Sub-Claim Types & 5D E/M/L/S/I Fingerprints
Split every compound claim so each atomic sub-claim can be verified independently without omission or double-counting:

| Atomic Type | What Must Be Verified | Required Evidence Elements |
| --- | --- | --- |
| `fact` | Existence, property, mechanism, or procedural statement | Exact entity identity, section context, primary vs. secondary attribution |
| `comparison` | Relative ordering (`A outperforms B`, `higher than control`, `vs. baseline`) | Both arms (`A` and `B`), direction of inequality, matching metric |
| `causation` | Causal/mechanistic link (`causes`, `induces`, `inhibits`, `thereby reduces`) | Intervention or causal design; observational association alone is NEVER full causal support |
| `scope` | Boundary condition (`in elderly patients`, `at pH 7.0`, `across all 5 cohorts`, `in vitro`) | Exact population, species, dose, time window, and universality qualifier (`all` vs. `subset`) |
| `quantitative` | Exact number, unit, percentage, fold change, sample size, or p-value/CI | Numeric value, physical unit, statistical significance (`p`, `95% CI`, `FDR`), denominator |

Every atomic sub-claim carries a 5-dimensional **E/M/L/S/I fingerprint**:
- **E (Entity)**: named drugs, genes, materials, models, diseases, agencies, or assays.
- **M (Magnitude / Unit / Stat)**: numeric values, units, percentages, confidence intervals, p-values.
- **L (Logic / Negation)**: explicit negation cues (`no`, `not`, `failed`, `no significant difference`, `未`, `无显著差异`).
- **S (Scope / Condition)**: population, species, pH, temperature, dosage, duration, or qualifier (`all`, `only`, `in vivo`).
- **I (Identifier / Comparator)**: DOIs/PMIDs/arXiv IDs and comparative markers (`vs.`, `compared to`, `outperforming`, `control`).

## 2. Strict Four-State Verdict Matrix

| Verdict | Criteria | Compound-Claim Aggregation Rule |
| --- | --- | --- |
| `supported` | All E/M/L/S/I elements match verbatim primary evidence in the cited source. | Only when **every** atomic sub-claim is `supported`. |
| `partially_supported` | Part of the statement is supported while another component is narrowed, unverified, or conflicted; OR two sources/sections conflict (must cite both). | When sub-claims mix `supported`/`partially_supported` with `insufficient_evidence` or `contradicted`. |
| `contradicted` | Direct opposing empirical evidence, explicit negation, or statistical null refutation on the **same** entity, metric, and condition. | When all verifiable sub-claims are directly `contradicted` (or the sole atomic claim is directly refuted). |
| `insufficient_evidence` | Source/DOI missing, DOI exists without supporting text, abstract-only or image-without-caption localization gap, or extrapolation to an untested scope. | When every sub-claim lacks sufficient evidence (zero `supported` and zero `contradicted`). |

## 3. Four Independent Failure Diagnostics (Never Conflate)

| Diagnostic State | Distinguishing Signal | Why Conflating Loses Points |
| --- | --- | --- |
| `doi_exists_but_unsupported` | Cited DOI matches a real supplied document, but the paper discusses a related topic without stating/proving the claim's metric, direction, or conclusion. | Naive agents see valid DOI + shared keywords and falsely emit `supported`. |
| `wrong_page_or_anchor_mismatch` | Claim cites `p. 3` or `P002`, which lacks the evidence, while the true evidence is on `p. 11` (`DOC:p11-b2`) of the **same** document. | Naive agents either fail to locate the real evidence or silently hide the wrong page citation. |
| `fulltext_unavailable_or_figure_caption_missing` | Corpus only provides an abstract, a scanned PDF with no text layer, or an image reference whose figure caption text is missing. | Naive agents treat missing text as `contradicted` instead of reporting `localization_insufficient` -> `insufficient_evidence`. |
| `inline_vs_structured_citation_drift` | Text cites `[1]`, `[2]`, `[3]` inline (3 markers), while `structured_citations[]` contains only 2 objects (or mismatched IDs). | Silently loses provenance links in multi-source reports (analogous to structured-citation drop bugs). |

## 4. Common Misjudgment Counterexamples
1. **Untested Extrapolation Mistaken for Contradiction**:
   - *Source*: "Compound X reduced tumor volume by 65% in xenograft mice (n=8, p=0.004)."
   - *Claim*: "Compound X cures late-stage human patients with zero liver toxicity."
   - *Wrong*: `contradicted` (no human trial was conducted or disproved).
   - *Correct*: `insufficient_evidence` with `untested_extrapolation_detected: true`.
2. **Direct Null Refutation Mistaken for Insufficient Evidence**:
   - *Source*: "In the Phase II trial (n=120), Compound X showed no significant difference in overall survival compared with placebo (HR=0.98, p=0.81)."
   - *Claim*: "Compound X significantly prolonged overall survival compared with placebo in the Phase II trial."
   - *Correct*: `contradicted` (same cohort, same comparator, direct statistical null refutation).
3. **Cross-Paper Borrowing on Missing/Wrong DOI**:
   - *Claim*: "Drug A achieved 92% clearance (DOI: `10.5555/missing.2024`)."
   - *Corpus*: `paper_other.md` (DOI: `10.5555/other.2024`) mentions Drug A 92% clearance.
   - *Correct*: `insufficient_evidence` with `evidence: []`—never borrow `paper_other.md` to mark a mismatched DOI `supported`.
4. **Universal Qualifier Overclaim**:
   - *Source*: "Method M outperformed Baseline B on 2 of 4 datasets, but performed worse on noisy clinical cohorts."
   - *Claim*: "Method M consistently outperformed Baseline B across all tested datasets."
   - *Correct*: `contradicted` (or `partially_supported` if split into a supported subset sub-claim and a contradicted universality sub-claim).
