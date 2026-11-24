# Omission Attribution Taxonomy, Three-Perspective Audit & Stopping Rules

## 1. Seven-Class Actionable Omission Decision Tree (Priority Order)

Evaluate each unmatched reference item in strict priority order so attribution is deterministic and independently recomputable:

1. **`retracted_or_unavailable`**: Reference carries a retraction, withdrawal, erratum, or unavailable record marker. Kept in the raw denominator for audit completeness; excluded from `actionable_reference_count`.
2. **`source_unreachable_or_failed`**: The source/database expected to index the reference reported `failed`, `timeout`, `unreachable`, `auth_required`, or `rate_limited` in the snapshot. Never collapse a failed API/database run into "0 hits".
3. **`identifier_mismatch`**: Either (a) a title-similar candidate exists in the retrieval hits (`token_sort_ratio >= 88.0`) whose first-author surname or publication year cannot be corroborated (`candidate_match_evidence` recorded), or (b) the reference lacks normalized DOI/PMID and failed corroborated matching.
4. **`date_window_cutoff`**: Publication year lies strictly outside the frozen query window `[year_min, year_max]`.
5. **`database_not_indexed`**: The reference's known indexed database(s) have zero overlap with the queried databases list.
6. **`field_restriction_too_narrow`**: Required query terms appear in the reference's abstract or body text, yet the query was restricted to `title`-only (`field_scope` excludes abstract/fulltext).
7. **`synonym_gap`**: One or more required Boolean concept blocks failed to match title/abstract terminology. Output the exact `missing_concept_blocks` alongside observed terminology from the missed paper to drive targeted remediation.

## 2. Three-Perspective Coverage Blindspot Check

When reviewing residual omissions, check whether missed papers cluster in a single research perspective:
- **Developer Perspective**: Original algorithm, assay, protocol, or primary dataset papers (often indexed with technical/chemical nomenclature rather than clinical outcomes).
- **Practitioner Perspective**: Applied replication studies, negative results, or failure-mode reports (often omitted when queries require overly positive outcome terms).
- **Independent Evaluator Perspective**: External benchmarks, systematic reviews, or cross-cohort validations (often missed by narrow single-discipline databases).

## 3. Supplemental Search Stopping Rules

Stop supplemental iteration and finalize the residual omission report when ANY of the following holds:
- **Zero Marginal Gain (`supplemental_empty_spin_detected: true`)**: A supplemental query batch recovers `0` net-new reference items despite returning candidate records.
- **Structural Boundary Reached**: All remaining omissions belong to non-recoverable snapshot boundaries (`retracted_or_unavailable`, `date_window_cutoff` intentional protocol limit, or `source_unreachable_or_failed` awaiting external access).
- **Diminishing Return Below Action Threshold**: Additional queries only duplicate already-matched seeds without changing coverage conclusions.
