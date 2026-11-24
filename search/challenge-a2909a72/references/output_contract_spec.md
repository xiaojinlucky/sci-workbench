# Output Contract Specification (`challenge-a2909a72`)

All runtime artifacts are written under `/app/output/` (or `--output`).

## 1. `result.json` Top-Level Schema
| Field | Type | Required | Description |
|---|---|---|---|
| `case_id` | string | Yes | Exact `case_id` read from `/input/case.json` |
| `skill` | string | Yes | `"academic-lit-search-engine"` |
| `status` | string | Yes | `"ok"` or `"partial_with_gaps"` (M6 `final_audit.py` compliant) |
| `results` | object | Yes | `ranked_candidates`, `tier_counts`, `perspective_counts`, `screening_stats`, `rank_stability`, `prisma_conservation` |
| `evidence` | object | Yes | `element_card`, `sub_questions`, `round1_vs_round2`, `per_sub_question_marginal_gain`, `term_gap_diagnosis`, `abbreviations_mined`, `cooccurrence_terms`, `stopping_verdict`, `deduplication_summary`, `ambiguous_candidates`, `rollback_log`, `version_pairs`, `citation_graph_summary` |
| `status_report` | object | Yes | `finite_snapshot_only`, `live_remote_coverage_claimed`, `source_load_errors`, `pagination_issues`, `valid_empty_sources`, `ambiguous_identity_candidates`, `conflicts`, `completed`, `not_completed`, `uncertainty` |
| `source_load_errors` | list | Yes | Malformed JSON / unsupported shape failures (never conflated with 0 hits) |
| `pagination_issues` | list | Yes | Cursor/HTTP/rate-limit error payloads (e.g., PyAlex #103) |
| `valid_empty_sources` | list | Yes | Cleanly parsed empty JSON arrays (`[]`) |
| `ambiguous_candidates` | list | Yes | Same-title distinct-formal-DOI pairs kept separate for human arbitration |
| `rollback_log` | list | Yes | ASReview #328 traceable duplicate entries with rollback action |
| `source_count_conserved` | bool | Yes | `True` iff `raw == unique + merged_away` and member sum == raw |
| `metrics` | object | Yes | Finite numeric summary metrics |
| `metric_provenance` | object | Yes | 1-to-1 provenance map for every key in `metrics` (M5 `gap_gate.py` compliant) |
| `findings` | list | Yes | Concise evidence-grounded headline statements |
| `gaps` | list | Yes | Structured gap records (source errors, pagination errors, ambiguous pairs, absent terms) |
| `artifacts` | list | Yes | Relative paths of every non-zero deliverable written under `/app/output/` |

## 2. Companion Artifacts
- `results_ranked.csv` — UTF-8-SIG CSV of ranked canonical candidates with tier, scores, perspectives, reason codes, and source locators.
- `evidence_chains.json` — Per-record provenance, field-level matched terms, snippets, perspective cues, conflicts, version groups, and `rollback_log`.
- `query_log.json` — Element card, sub-questions, Round 1 & Round 2 adapted queries, mined abbreviations, co-occurrence terms, `term_gap_diagnosis`, marginal gain per sub-question, and stopping verdict.
- `report.md` — Human-readable Markdown synthesis report.
- `audit_log.json` — SHA-256 hash-chained step log.
- `run_manifest.json` — Input file inventory, SHA-256 hashes, record counts by file, and source errors.
