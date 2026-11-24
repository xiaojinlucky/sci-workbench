# Multi-Paper Evidence-Anchored Literature Knowledge Graph (`product-arena-10-760d1a0b`)

Offline, pure Python 3.12 stdlib Skill for constructing multi-paper literature knowledge graphs with strict per-paper metadata isolation, JATS `paragraph_id` evidence anchors, undirected co-occurrence isolation (zero fabricated causal edges), conservative cross-paper entity alignment (`LINK_CONCEPT_KEEP_SEPARATE_INSTANCES` vs `DO_NOT_MERGE`), 5+ executable queries, incremental snapshot merging, and GraphML/JSON/Markdown export.

## Package Structure

- `SKILL.md` — DeepSeek Harness workflow, hard invariants, and locator/alignment boundaries.
- `scripts/knowledge_graph_engine.py` — Multi-paper corpus loader, abbreviation resolver, entity/relation extractor, co-occurrence firewall, and cross-paper alignment engine.
- `scripts/kg_query_and_export.py` — 5 standard queries (Q1–Q5) + ad-hoc Q6 entity query, incremental graph updater (`--prior-graph`), GraphML exporter, and Markdown report generator.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` — Standard Harbor M1–M6 runtime guards.
- `references/` — Entity/relation ontology, cross-paper alignment & non-merge rules, query/incremental protocol, and output contract.
- `examples/` — Minimal illustrative two-paper example and output schema (labeled as examples; not formal evaluation outputs).

## Quick Local Run (Offline Linux / Python 3.12+)

```bash
python scripts/knowledge_graph_engine.py --input /input --output /app/output --work /app/work
python scripts/kg_query_and_export.py --graph /app/output/knowledge_graph.json --entity "DFT"
```

## Boundaries

- Never copies Paper 1's DOI/title/authors onto Paper 2, and never counts multiple file formats of the same paper as cross-paper evidence.
- Never fabricates PDF page numbers when inputs supply JATS `paragraph_id` locators.
- Never promotes undirected sentence co-occurrence to a causal or directional edge.
