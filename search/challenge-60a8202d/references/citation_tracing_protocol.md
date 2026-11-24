# Citation Tracing & Identity Resolution Protocol (`challenge-60a8202d`)

## 1. Seed Discovery Priority

1. Explicit `seeds` / `seed_papers` / `seed_ids` declared in explicit graph/seed files (e.g., `seed_and_graph.json`).
2. Explicit seed declarations or identifiers (`seeds`, `seed_papers`, `primary_doi`, `seed_openalex_id`, `seed_arxiv_id`) in `case.json`.
3. **Auto-inferred fallback**: If neither source supplies explicit seeds, choose the canonical node with the highest total degree (`in_degree + out_degree`, tie-breaking by `in_degree`, `year`, and `rid`), or highest query match if the graph has zero edges, and emit `seed_selection_mode: "auto_inferred"`.

If a declared seed is absent from API responses (for instance, a brand-new preprint with zero citations), register it as an explicit external node so it appears in `zero_citation_seeds` rather than being dropped.

## 2. Cross-Form Identity Merging & False-Merge Guard

Deduplication runs via `search_core.dedup.cluster_records_detailed()` prior to graph construction:

- **Tier 1 (Exact strong identifiers)**: Normalized DOI (`T1:doi`), PMID (`T1:pmid`), arXiv base ID stripping `vN` (`T1:arxiv`), or canonical patent number (`T1:patent_number`).
- **Tier 2 (Cross-version bridges)**: Shared `alt_dois` (`T2:alt_doi_relation`), preprint DOI (`10.48550/arxiv.*`, `10.1101/*`, `10.21203/*`, `10.26434/*`, `10.31219/*`, `10.20944/*`, `10.2139/ssrn.*`, `10.5281/zenodo.*`) paired with a formal DOI when title similarity `>= 0.88` and first-author matches (`T2:preprint_doi_to_vor`), or arXiv + formal DOI title bridge (`T2:arxiv-doi-title` / `T2:arxiv-doi-fuzzy`).
- **Tier 3–4 (Metadata fallback when one side lacks strong IDs)**: Exact/near-exact normalized title (`>= 0.92`) with year gap `<= 1` or identical first author (`T3:title`), or fuzzy title (`>= 0.82`) with matching first author and year gap `<= 1` (`T4:title+author+year`).
- **Hard False-Merge Guard**: If two records carry **two distinct formal (non-preprint) DOIs**, automatic merging is **prohibited** regardless of title similarity. Pairs with title similarity `>= 0.80` are emitted into `ambiguous_candidates` (`reason: "ambiguous:distinct_formal_dois"`, `action: "kept_separate_needs_human_arbitration"`). This protects homonymous papers and main-article vs. supplementary-file DOIs (e.g., `.s001`, `.s002`).
- **Canonical Selection & Conflict Preservation**: Canonical record priority is `version_rank (published=4 > conference=3 > preprint=2 > unknown=0) > field completeness`. Conflicting fields across merged sources are resolved by `version_rank > source_authority > field_richness`, with all raw values retained in `conflicts`.
- **Conservation & Rollback**: `raw_input_records == canonical_unique + duplicates_merged_away` is verified and every merged-away record is logged in `rollback_log` with `duplicate_ignored_with_trace`.

## 3. Direction-Aware Multi-Hop Tracing & Pruning

- **Edge Semantics**: Every directed edge `u -> v` represents `u cites v` (`u` is the citing paper; `v` is the cited reference). Explicit edges labeled `cited_by` / `is_cited_by` are normalized by reversing `(u, v)` to `cites`.
- **Canonical Endpoint Reconciliation**: Explicit graph edges whose endpoints use OpenAlex IDs (`W...`), DOIs, arXiv IDs, or PMIDs are resolved onto canonical cluster `rid`s so edges never dangle when API records use file-pointer `rid`s.
- **Backward Expansion (`out_edges`)**: Moves from `seed` to its references (`hop 1`), then references-of-references (`hop 2`), uncovering methodological and dataset origins.
- **Forward Expansion (`in_edges`)**: Moves from `seed` to citing works (`hop 1`), then citers-of-citers (`hop 2`), uncovering follow-up improvements, benchmarks, and critiques.
- **Stopping & Pruning**:
  1. Stop when `hop == depth` or the new frontier is empty.
  2. When a hop frontier exceeds `max_branch_per_hop` (default `60`), sort deterministically by `(-in_degree, -year, rid)`, retain the top `max_branch_per_hop`, and log `pruned_count` in `pruning_log`.

## 4. Parallel Discovery & Structural Roles

- **Bibliographic Coupling**: Non-seed nodes sharing `>= 1` backward reference (`out_edges`) with a seed.
- **Co-Citation**: Non-seed nodes sharing `>= 1` forward citing work (`in_edges`) with a seed.
- **Parallel Works**: Nodes discovered via coupling or co-citation that are not already in direct/indirect `related` hops.
- **Role Precedence**:
  1. `retracted_caution`: `is_retracted == True` (always takes precedence).
  2. `seed`: In `seed_rids`.
  3. `foundational`: `in_degree >= max(2, |U| // 3)` and (`year == 0` or `year <= 2018`).
  4. `bridge`: `in_degree > 0` and `out_degree > 0`.
  5. `parallel_coupled`: In `parallel_works_by_coupling_or_cocitation`.
  6. `recent_followup`: `year >= 2023`.
  7. `supporting`: Remaining connected nodes.

## 5. Per-Hop Shortest Directed Paths & CitationChaser #37 Snapshot Diff

- **Paths**: Bidirectional BFS from each `seed` to reachable `related` nodes (up to `depth + 1` hops), labeling each hop explicitly as `cites` or `cited_by`, tagging `direct` (`1` hop) vs. `indirect` (`>= 2` hops), and attaching source provenance per hop.
- **Snapshot Diff**: Align prior graph snapshot nodes onto current canonical IDs before calling `diff_graph_snapshots()`. Report `nodes_persisted`, `nodes_added`, `nodes_vanished`, `edges_added`, `edges_vanished`, and `unresolved_in_current`, paired with an explicit finite-snapshot coverage warning.
