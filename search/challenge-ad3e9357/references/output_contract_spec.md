# Output Contract Specification (`challenge-ad3e9357`)

All deliverables must be written under `/app/output/` with deterministic UTF-8 JSON and Markdown formatting.

## Required Files

1. `/app/output/result.json` — Primary Harbor contract JSON.
2. `/app/output/patent_search_report.json` — Full normalized patent inventory, dual DOCDB/INPADOC family structures, source-located claim analyses, dual-track queries, and separated scientific comparison papers.
3. `/app/output/patent_landscape_report.md` — Human-readable patent and technical literature landscape report.

## Required Fields in `result.json`

| Field | Type | Invariant / Meaning |
| --- | --- | --- |
| `case_id` | string | Exact `case_id` copied from `/input/case.json` |
| `status` | string | `"ok"` when grounded & audited, `"partial"` if corpus incomplete |
| `artifacts` | list[str] | Must include `["result.json", "patent_search_report.json", "patent_landscape_report.md"]` |
| `patents` | list[obj] | Normalized patent documents with separated `publication_number`, `application_number`, `grant_number`, `kind_code`, `stage`, dates, `claims_analysis`, `wo_merged_ocr_boundary`, `observed_snapshot_legal_status`, and `current_official_legal_status == "UNKNOWN"` |
| `family_consolidation` | obj | Contains `publication_document_count`, `docdb_simple_family_count`, `inpadoc_extended_family_count`, `docdb_simple_families[]`, `inpadoc_extended_families[]`, and `keyword_only_distinct_patents_separated[]` |
| `dual_track_queries` | obj | Slash-safe `patent_search_queries[]` (`uspto_safe_query`, `lucene_escaped_query`) strictly separated from `scientific_literature_queries[]` |
| `comparison_scientific_papers` | list[obj] | Non-patent scientific comparison literature kept separate from patent families |
| `three_perspective_summary` | obj | Applicant/claim-scope, technical-implementer, and independent-novelty-evaluator summaries |
| `metrics` | obj | Finite integers for publication count, DOCDB simple family count, INPADOC extended family count, scientific paper count, WO/OCR count, claims extracted count, CPC/IPC count |
| `metric_provenance` | obj | Exact JSON pointer/source for every numeric metric |
| `findings` | list[str] | Grounded summary findings |
| `gaps` | list[obj] | Must explicitly include `LIVE_LEGAL_STATUS_UNKNOWN` and `OBSERVED_FAMILY_LINKS_NON_EXHAUSTIVE`, plus `WO_MERGED_OCR_BOUNDARY_RETAINED` when applicable |

## Hard Invariants

- **Dual-Family Inequality**: `inpadoc_extended_family_count <= docdb_simple_family_count <= publication_document_count` (unless distinct DOCDB IDs are explicitly supplied).
- **Offline Legal Status**: `current_official_legal_status == "UNKNOWN"` on every patent record offline.
- **Claim Scope Honesty**: `equal_claim_scope_assumed == false` on every patent record and family.
- **Non-Exhaustive Link Honesty**: `observed_links_only_not_exhaustive == true` on every family.
