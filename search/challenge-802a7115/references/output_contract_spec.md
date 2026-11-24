# Output Contract Specification (`challenge-802a7115`)

All deliverables must be written under `/app/output/` with deterministic UTF-8 JSON and Markdown formatting.

## Required Files

1. `/app/output/result.json` — Primary Harbor contract JSON.
2. `/app/output/cross_lingual_alignment.json` — Complete multilingual work/view registry, controlled terminology expansion trace, title-overwrite audit, homonym separation audit, false-friend/acronym diagnostics, and verified sentence-level evidence anchors.
3. `/app/output/cross_lingual_report.md` — Human-readable report summarizing canonical works, view taxonomy, bidirectional queries, evidence quotes, and explicit coverage limits.

## Required Fields in `result.json`

| Field | Type | Invariant / Meaning |
| --- | --- | --- |
| `case_id` | string | Exact `case_id` copied from `/input/case.json` |
| `status` | string | `"ok"` when grounded & audited, `"partial"` if corpus/evidence incomplete |
| `artifacts` | list[str] | Must include `["result.json", "cross_lingual_alignment.json", "cross_lingual_report.md"]` |
| `canonical_works` | list[obj] | Deduplicated works (`work_id`, `doi`, `scielo_pid`, `original_title`, `titles_by_language`, `languages_present`, `view_count_raw`, `distinct_non_duplicate_views`, `views[]`) |
| `exact_duplicates_flagged` | list[obj] | Duplicate views linked to their canonical `original_record_id` and `work_id` |
| `title_overwrite_audit` | obj | Proves `overwritten_count == 0` and byte-level preservation of every `original_title` / `raw_title` |
| `homonym_distinct_doi_separated` | list[obj] | Records with identical/near-identical titles but distinct normalized DOIs kept as separate works |
| `cross_lingual_query_results` | list[obj] | Per-query `source_language`, `target_language_queries`, `terminology_alignments`, `false_friend_or_acronym_risk`, `matched_unique_work_count`, `matched_works[]` with sentence-level `evidence_anchors[]` |
| `metrics` | obj | Finite integers: `raw_view_count`, `unique_work_count`, `exact_duplicate_count`, `homonym_distinct_doi_separated_count`, `query_count`, `title_overwritten_count` |
| `metric_provenance` | obj | Exact JSON pointer/source for every numeric metric |
| `findings` | list[str] | Grounded summary findings referencing only observed corpus facts |
| `gaps` | list[obj] | Explicit structured gaps (abstract-only views, acronym/false-friend risk, sparse PDFs, finite-snapshot limit) |

## Hard Invariants

- **Unique Study Conservation**: `unique_work_count <= raw_view_count`; translations (`publisher_full_translation`), `english_abstract_only` mirrors, and `exact_duplicate` entries never increment `unique_work_count`.
- **Distinct-DOI Isolation**: Two records with different non-empty normalized DOIs are never placed in the same `work_id`.
- **Original Title Integrity**: `title_overwrite_audit.overwritten_count == 0`.
- **Quote Grounding**: Every sentence anchor reports `verified_in_source` checked verbatim against the underlying view text.
