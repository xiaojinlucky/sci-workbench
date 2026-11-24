# Search Protocol, Diagnostics & Quality Rubric (`challenge-a2909a72`)

## 1. Screening-Not-Cherry-Picking Funnel
1. **Classic Problem Variant Framing**: Before inspecting individual papers, map the question into its scientific archetype and 7-slot element card (`research_object`, `mechanism_or_outcome`, `method_or_intervention`, `comparator`, `population_or_context`, `time_scope`, `evidence_requirement`). Leave unspecified slots as `UNKNOWN`.
2. **Count Conservation Invariant**:
   - `raw_records_identified == canonical_unique + duplicates_merged_away` (`source_count_conserved == True`)
   - `canonical_unique == tier_core + tier_method_only + tier_background + tier_excluded`
   - Never drop records silently; every non-core record carries machine-readable `reason_codes`.

## 2. Two-Round Controlled Expansion & Zero-Hit Diagnosis
- **Round 1 (Unexpanded Core)**: Field-weighted BM25 (`title=2.4`, `abstract=2.2`, `keywords/mesh/venue=1.8`) with zero acronym expansion.
- **Term Postings Diagnosis (`term_gap_diagnosis`)**:
  - `metadata_present`: Term occurs in title, abstract, or keywords/venue.
  - `fulltext_only_metadata_blindspot`: Zero metadata hits, >=1 local full-text document hit.
  - `acronym_or_variant_recoverable`: Zero direct metadata hits, >=1 hit via corpus-mined abbreviation/expansion pair.
  - `corpus_absent`: Zero hits everywhere in the snapshot. Report honestly as a snapshot boundary.
- **Round 2 (Controlled Expansion)**: Expands solely via corpus-mined `ABBR (expansion)` / `expansion (ABBR)` pairs and Round 1 neighborhood co-occurring terms (support >= 2). Records per-sub-question `r1_hits`, `r2_hits`, `marginal_new_hits`, and `marginal_gain_rate`.

## 3. Three-Perspective Evidence Matrix
| Perspective | Lexical / Metadata Signals | Scientific Value |
|---|---|---|
| `method_developer` | DFT, first-principles, synthesis, design, mechanism, atomic/electronic structure, framework, protocol | Captures origin of catalysts, methods, and mechanistic hypotheses |
| `frontline_application` | PEMWE/electrolyzer, MEA, durability, dissolution, lifetime hours, operando/in situ, operating current density, failure/aging | Captures real-world validation, operating limits, and failure modes |
| `independent_review_eval` | review, survey, perspective, benchmark, comparative assessment, meta-analysis, roadmap | Captures independent consensus, cross-method trade-offs, and open gaps |

## 4. 4-Tier Ranking & Stability Guards
- **`core`**: Within time window, direct multi-facet evidence (`final_label == include` or strong abstract-backed multi-perspective match).
- **`method-only`**: Relevant method/mechanism study without full operating-context match, OR record missing abstract (protected from silent exclusion).
- **`background`**: Topically adjacent study OR strong pre-window (`year < year_min`) foundational study retained with `PRE_WINDOW_CONTEXT`.
- **`excluded`**: Hard exclusion rule hit, retracted paper, or zero-overlap record with abstract present.
- **Title-Only Trap Guard**: High title match (`title_score >= 1.2`) with zero abstract overlap (`abstract_score == 0.0`) when an abstract exists is downgraded so keyword stuffing cannot outrank abstract-backed studies.
- **Leave-One-Term-Out (LOO) Stability**: Perturbs query terms one at a time and measures mean top-10 Jaccard/set overlap (`stable >= 0.90`, `partially_stable >= 0.70`, `unstable < 0.70`).

## 5. Community Failure-Mode Protections
1. **PyAlex #103 (Broken Final-Page Cursor)**: Files containing `status=error/failed/rate_limited/timeout/unavailable` or non-empty `error`/`cursor` are logged in `pagination_issues` and trigger `status = "partial_with_gaps"`.
2. **Malformed JSON vs Valid Empty `[]`**: Truncated JSON goes to `source_load_errors`; cleanly parsed `[]` goes to `valid_empty_sources`.
3. **Distinct Formal DOIs Sharing a Title**: Blocked from auto-merging (`ambiguous:distinct_formal_dois`) and routed to `ambiguous_candidates`.
4. **ASReview #328 Reversible Deduplication**: Every merged duplicate stays in `rollback_log` with `duplicate_status = "duplicate_ignored_with_trace"` and an explicit `rollback_action`.
