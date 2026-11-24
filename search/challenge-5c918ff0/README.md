# Multi-Database Federated Search Merger (`challenge-5c918ff0`)

Evidence-bound, pure Python 3.12 stdlib Skill for multi-database federated literature orchestration, cross-source field standardization, 4-tier deduplication with hard anti-false-merge protection, deterministic conflict resolution, per-field provenance lineage, and degraded-source coverage auditing.

## Key Capabilities
- **Multi-Source Query Adaptation**: Generates database-native query strings for Crossref, OpenAlex, Europe PMC, PubMed, and arXiv instead of copying one naive query across incompatible backends.
- **Per-Source Field Coverage & Missing-vs-Unknown Semantics**: Computes exact fill rates for `title`, `abstract`, `authors`, `year`, `doi`, `venue`, and `citations_count` per database, highlighting systematically absent fields (e.g., arXiv citations, Crossref publisher-withheld abstracts) without conflating missingness with zero.
- **Hard Same-Title Distinct-Formal-DOI Guard**: Blocks silent false merges whenever two papers share a title but carry distinct non-preprint DOIs, queuing them in `ambiguous_arbitration_queue.json`.
- **Preprint-to-VoR Chaining & Deterministic Conflict Resolution**: Links preprints to published Versions of Record via `alt_dois`, arXiv IDs, and title+author evidence (`preprint_to_vor_chains`), resolves cross-source field disagreements via `version_rank > source_authority > field_richness`, and retains all raw source values.
- **Full Auditability & Degraded-Source Disclosure**: Enforces `source_count_conserved == True`, logs every merged duplicate in `dedup_rollback_log.json` (ASReview #328), quantifies single-source exclusive papers and N x N overlap (`source_coverage_matrix.json`), and surfaces unavailable/rate-limited sources (e.g., PubMed HTTP 429) in `degraded_sources`.

## Package Layout
- `SKILL.md` — Executable DeepSeek Harness protocol and hard boundaries.
- `scripts/federated_search_merger.py` — Main multi-database federated merge & coverage audit pipeline.
- `scripts/search_core/` — Pure Python 3.12 stdlib multi-source parser, deduplicator, and provenance core.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` — Runtime guards (M1–M6).
- `references/federated_schema_mapping.md` — Query syntax matrix, field normalization table, and conflict resolution rules.
- `references/output_contract_spec.md` — Machine-readable output contract schema.
- `examples/` — Minimal illustrative input/output examples (explicitly marked as examples).

## Usage (Offline Linux Sandbox)
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 -B scripts/federated_search_merger.py --input /input --output /app/output --work /app/work
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require federated_merged.csv --require source_coverage_matrix.json \
  --require dedup_rollback_log.json --require ambiguous_arbitration_queue.json \
  --require report.md
```

## Dependencies & Limits
- **Runtime**: Python 3.12 standard library only (zero external dependencies, zero network access).
- **Boundaries**: Operates strictly over authorized local snapshots; never bypasses rate limits or fabricates records for unavailable databases.
