# Executable Query Suite, Incremental Update & GraphML Contract

## 1. Standard Queries Executed Automatically (Q1–Q5)

1. **`Q1_papers_with_evidence_edges`**: Lists every distinct paper `source_id` contributing at least one grounded directed edge and its exact `relation_ids`.
2. **`Q2_reported_methods_and_uses`**: Returns all `uses` and `evaluates_on` edges with `source_id`, `paragraph_id`, `condition`, and verbatim `quote`.
3. **`Q3_cross_paper_same_spelling_audit`**: Summarizes `merged_concept_groups` vs `explicit_non_merge_decisions` across distinct papers.
4. **`Q4_comparisons_outperforms_and_conflicts`**: Returns `compares_with`, `outperforms`, `contradicts`, and opposite-direction cross-condition disagreements.
5. **`Q5_co_occurrence_vs_causal_guard_audit`**: Reports isolated undirected co-occurrence counts vs `causes_direct` counts, explicitly confirming zero co-occurrence promotion.
6. **`Q6_entity_neighborhood:<name>`** (via `--query-entity` or `kg_query_and_export.py`): Returns matching nodes across papers and all incident evidence edges.

## 2. Incremental Update Contract (`--prior-graph`)

When `--prior-graph <old_knowledge_graph.json>` is provided:
- Existing `entity_id` values are preserved unchanged; new evidence locators are appended non-destructively.
- Added nodes and added edges are enumerated in `incremental_update.added_node_ids` and `added_edge_ids`.
- Any new edge contradicting or opposing a prior edge is flagged in `incremental_update.new_conflicts_with_prior` without overwriting prior relations.

## 3. GraphML Export (`knowledge_graph.graphml`)

- Directed evidence edges carry `edge_modality="directed_evidence_backed"` and `source_id`, `paragraph_id`, `relation_type`, `condition`.
- Isolated co-occurrences carry `directed="false"` and `edge_modality="undirected_co_occurrence"` so downstream tools (NetworkX/Gephi/Cytoscape) never confuse co-occurrence with directed causality.
