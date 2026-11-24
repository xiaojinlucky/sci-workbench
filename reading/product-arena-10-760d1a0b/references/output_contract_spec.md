# Output Contract Specification (`product-arena-10-760d1a0b`)

## Required Deliverables Under `/app/output/`

1. `result.json` — Contains `case_id`, `status` (`ok` | `partial_with_gaps`), `sources`, `locator_contract`, `uncertainty`, `artifacts`, `metrics`, `graph_summary`, `cross_paper_alignment`, `conflicts`, `incremental_update`, `metric_provenance`, `findings`, and `gaps`.
2. `knowledge_graph.json` — Full structured graph (`papers`, `entities`, `relations`, `co_occurrences`, `held_for_review`, `cross_paper_alignment`, `conflicts`, `incremental_update`, `queries`).
3. `knowledge_graph.graphml` — Valid GraphML XML export preserving directed vs undirected edge modalities.
4. `knowledge_graph_report.md` — Human-readable synthesis report with paper registry, grounded edges, alignment/non-merge audit, queries, conflicts, and gaps.

## Locator Honesty Requirement

- When inputs are JATS XML or JSON paragraphs without PDF pagination, `locator_contract.locator_type` is `"jats_or_source_paragraph_id"` and `locator_contract.pdf_page_available` is `false`. Never emit fabricated PDF page numbers.
