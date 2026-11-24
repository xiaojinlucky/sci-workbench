# Output Contract Specification

Every run must write `/app/output/result.json` as strict RFC-8259 UTF-8 JSON (no NaN/Inf), preserving `case_id` verbatim and populating all five required contract fields:

| Field | Required Content | Verification Standard |
| --- | --- | --- |
| `case_id` | Exact string from `/input/case.json` | Unmodified verbatim match |
| `deliverable` | Paths and summary of `adapted_article.md`, `glossary_lock.json`, `paragraph_trace.json`, `translation_fidelity.json`, and `side_by_side_review.md` | Every listed path exists under `/app/output` and is non-empty |
| `preserved_facts` | Structured inventory of retained core metrics, units, sample sizes, comparison baselines, source evidence tier, and preserved model/scope boundaries | Zero ungrounded numeric drift, zero claim escalation |
| `evidence_map` | Claim-to-source mapping linking each adapted claim and analogy to its source section, verbatim anchor, and confirmation status | Every factual statement traces to an inspected `/input` file |
| `change_log` | Summary of audience adaptations (`KEEP`/`COMPRESS`/`OMIT_REDUNDANCY` counts), resolved audit findings, author confirmation items (e.g., analogies), and source gaps/contradictions | Enables paragraph-by-paragraph verification by the original author |

## Standard Deliverable Files Under `/app/output/`

- `result.json`: Final structured output with the 5 contract fields plus `status` (`ok` or `partial_with_gaps`), `artifacts`, `metrics`, `metric_provenance`, `findings`, and `gaps`.
- `adapted_article.md`: Ready-to-use audience-calibrated deliverable.
- `glossary_lock.json`: Locked cross-genre terminology mapping (`canonical_term`, `source_term`, `allowed_variants`, `disallowed_synonyms`).
- `paragraph_trace.json`: Complete paragraph-by-paragraph `KEEP`/`COMPRESS`/`OMIT_REDUNDANCY` traceability ledger.
- `translation_fidelity.json`: Deterministic output from `translational_fidelity_auditor.py`.
- `side_by_side_review.md`: Human-readable review table generated for the original researcher to verify cuts, analogies, and boundaries.
