# Academic Literature Search Engine (`challenge-a2909a72`)

Evidence-bound, pure Python 3.12 stdlib Skill for multi-round academic literature retrieval, controlled term/abbreviation expansion, zero-hit postings diagnosis, three-perspective evidence tagging, 4-tier transparent ranking, and auditable provenance over finite authorized snapshots.

## Key Capabilities
- **Source Health Tri-Triage**: Separates malformed/truncated JSON (`source_load_errors`) and HTTP/cursor errors such as PyAlex #103 (`pagination_issues`) from cleanly parsed empty arrays `[]` (`valid_empty_sources`), never misreporting failures as zero hits.
- **Element-Card Decomposition & Two-Round Retrieval**: Decomposes complex research questions into a 7-slot element card and dependency-ordered sub-questions (`SQ0..SQ6`), runs Round 1 unexpanded core retrieval, diagnoses zero-hit terms across metadata/fulltext/acronyms, and runs Round 2 controlled expansion with per-sub-question marginal gain.
- **Traceable Deduplication & Anti-False-Merge Guard**: Enforces `source_count_conserved == True`, logs every duplicate in an ASReview #328 `rollback_log`, links preprints to Versions of Record (`version_pairs`), and routes same-title distinct-formal-DOI pairs to `ambiguous_candidates` instead of silently merging them.
- **Three-Perspective & 4-Tier Ranking**: Tags records across `method_developer`, `frontline_application`, and `independent_review_eval` perspectives, ranks into `core / method-only / background / excluded` with deterministic tie-breaking, and checks Leave-One-Term-Out top-10 stability.

## Package Layout
- `SKILL.md` — Executable DeepSeek Harness protocol and boundaries.
- `scripts/academic_search_pipeline.py` — Main end-to-end offline retrieval pipeline.
- `scripts/search_core/` — Pure Python 3.12 stdlib bibliographic intake, dedup, query planning, screening, and contract engine.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` — Common Harbor runtime guards (M1–M6).
- `references/search_protocol_and_rubric.md` — Screening rubric, diagnostic taxonomy, and community failure-mode guards.
- `references/output_contract_spec.md` — Full JSON/CSV output contract specification.
- `examples/` — Minimal illustrative input/output examples (marked as examples, not formal case answers).

## Usage (Offline Linux / Harbor Sandbox)
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/academic_search_pipeline.py --input /input --output /app/output --work /app/work
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require results_ranked.csv --require evidence_chains.json \
  --require query_log.json --require report.md
```

## Dependencies & Limits
- **Runtime**: Python 3.12 standard library only (zero external packages required; zero network calls).
- **Boundaries**: Finite local snapshots only. Does not perform live API calls, does not claim global literature completeness, and marks records lacking local full text as metadata-bounded.
