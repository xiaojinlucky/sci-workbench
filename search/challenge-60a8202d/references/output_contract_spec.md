# Output Contract Specification (`challenge-60a8202d`)

All output files are written under `/app/output/` (or `--output`).

## 1. `result.json`

Top-level keys guaranteed by `citation_graph_tracer.py` + `result_guard.py`:

- `case_id` (`string`): Case identifier from `/input/case.json`.
- `task_id` (`string`): `"challenge-60a8202d"`.
- `skill` (`string`): `"citation-graph-explorer"`.
- `status` (`string`): `"ok"` or `"partial_with_gaps"` (strict M6 `final_audit.py` contract).
- `seed_selection_mode` (`string`): `"explicit_input"` or `"auto_inferred"`.
- `results` (`object`):
  - `seeds`: List of resolved seed objects (`seed_rid`, `title`, `doi`, `openalex`, `year`, `resolution`, `origin`).
  - `role_counts`: Map of structural role -> integer count.
  - `backward_hops`: Map of hop depth string (`"1"`, `"2"`, ...) -> list of node `rid`s.
  - `forward_hops`: Map of hop depth string (`"1"`, `"2"`, ...) -> list of node `rid`s.
  - `parallel_works`: List of node `rid`s discovered via co-citation or bibliographic coupling.
  - `traced_nodes`: List of canonical node summaries (`rid`, `title`, `year`, `doi`, `openalex`, `version`, `role`, `in_degree`, `out_degree`, `is_retracted`, `n_merged_versions`, `sources`).
  - `shortest_directed_paths`: List of directed path objects (`seed`, `target`, `hop_count`, `reachability`, `directions`, `chain_summary`, `hops`).
- `evidence` (`object`):
  - `edge_provenance_csv`, `citation_network_json`, `snapshot_diff_json`
  - `cocitation_pairs`, `bibliographic_coupling_pairs`, `pruning_log`
  - `dedup_summary`, `ambiguous_candidates_count`, `rollback_entries_count`, `sources_used`, `audit_chain`
- `status_details` (`object`): Full finite-snapshot boundary metadata, `completed`, `not_completed`, `errors`, `source_load_errors`, `pagination_issues`, `conflicts`, `ambiguous_identity_candidates`.
- `zero_citation_seeds` (`array`): Seeds with zero observed in-degree and out-degree in local snapshot.
- `retracted_alerts` (`array`): Retracted/correction nodes with seed connectivity and severity.
- `unresolved_references` (`object`): `{ "count": int, "sample": [...] }`.
- `snapshot_diff` (`object`): CitationChaser #37 summary (`previous_snapshot_available`, `nodes_persisted`, `nodes_added_count`, `nodes_vanished`, `edges_added_count`, `edges_vanished`, `unresolved_in_current`, `coverage_warning`).
- `metrics` (`object`) & `metric_provenance` (`object`): 1-to-1 mapped numeric metrics and exact artifact pointers.
- `findings` (`array[string]`), `warnings` (`array[string]`), `gaps` (`array[object]`), `artifacts` (`array[string]`).

## 2. `edge_provenance_table.csv`

UTF-8-SIG CSV columns:
`src_rid,src_title,src_doi,src_year,src_retracted,dst_rid,dst_title,dst_doi,dst_year,dst_retracted,relation,provenance_count,source,resolution,file,pointer,raw_evidence`

## 3. `citation_network.json` & `snapshot_diff.json`

- `citation_network.json`: Full canonical graph (`nodes`, `edges`), `trace`, `dedup_summary`, `ambiguous_candidates`, `rollback_log`, `zero_citation_seeds`, `retracted_alerts`, `unresolved_references_sample`.
- `snapshot_diff.json`: Complete item-level `nodes_added`, `nodes_vanished`, `nodes_vanished_details`, `edges_added`, `edges_vanished`, `edges_vanished_details`, `unresolved_in_current`, `coverage_warning`.
