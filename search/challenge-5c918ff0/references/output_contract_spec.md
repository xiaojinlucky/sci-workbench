# Output Contract Specification (`challenge-5c918ff0`)

All runtime artifacts are written under `/app/output/` (or `--output`).

## 1. `result.json` Top-Level Schema
| Field | Type | Required | Description |
|---|---|---|---|
| `case_id` | string | Yes | Exact `case_id` read from `/input/case.json` |
| `skill` | string | Yes | `"multi-db-federated-search"` |
| `status` | string | Yes | `"ok"` or `"partial_with_gaps"` (M6 `final_audit.py` compliant) |
| `source_count_conserved` | bool | Yes | `True` iff `raw == canonical + merged_away` and member sum == raw |
| `results` | object | Yes | `canonical_records` (with 6-step `lineage`), `preprint_to_vor_chains`, `source_count_conserved` |
| `evidence` | object | Yes | `per_source_queries`, `field_coverage_by_source`, `systematic_missing_fields`, `exclusive_works_by_source`, `source_overlap_matrix`, `field_conflicts_resolved`, `deduplication_summary`, `rollback_log` |
| `status_report` | object | Yes | Finite-snapshot status, `degraded_sources`, `valid_empty_sources`, `ambiguous_same_title_distinct_doi`, `completed`, `not_completed`, `uncertainty` |
| `field_coverage_by_source` | object | Yes | Per-database fill rates across `title`, `abstract`, `authors`, `year`, `doi`, `venue`, `citations_count` |
| `exclusive_works_by_source` | object | Yes | Single-database unique canonical counts and sample RIDs |
| `source_overlap_matrix` | object | Yes | N x N canonical overlap counts and pairwise Jaccard indices |
| `ambiguous_same_title_distinct_doi` | list | Yes | Hard-blocked same-title distinct-formal-DOI pairs for human arbitration |
| `preprint_to_vor_chains` | list | Yes | Linked preprint -> Version-of-Record upgrade pairs |
| `field_conflicts_resolved` | list | Yes | Deterministically resolved conflicts preserving all raw values and locators |
| `rollback_log` | list | Yes | ASReview #328 reversible duplicate entries |
| `degraded_sources` | list | Yes | Unavailable/rate-limited/malformed sources with coverage impact and mitigation |
| `metrics` | object | Yes | Finite numeric summary metrics |
| `metric_provenance` | object | Yes | 1-to-1 provenance map for every key in `metrics` (M5 `gap_gate.py` compliant) |
| `findings` | list | Yes | Concise evidence-grounded summary statements |
| `gaps` | list | Yes | Structured gap records |
| `artifacts` | list | Yes | Relative paths of every non-zero deliverable under `/app/output/` |

## 2. Companion Artifacts
- `federated_merged.csv` — Canonical deduplicated federated table with exclusivity flags, conflict counts, and provenance summaries.
- `source_coverage_matrix.json` — Adapted per-source queries, field coverage rates, systematic missing fields, exclusivity counts, overlap matrix, and degraded sources.
- `dedup_rollback_log.json` — ASReview #328 reversible deduplication log with conservation check.
- `ambiguous_arbitration_queue.json` — Hard-blocked same-title distinct-formal-DOI arbitration queue.
- `report.md` — Human-readable federated search audit report.
- `audit_log.json` & `run_manifest.json` — SHA-256 hash-chained log and file inventory.
