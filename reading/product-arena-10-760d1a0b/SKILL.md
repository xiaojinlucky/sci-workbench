---
name: literature-knowledge-graph-builder
description: Construct multi-paper, evidence-anchored literature knowledge graphs with strict per-paper metadata isolation, JATS paragraph_id locators, explicit directed relations strictly separated from undirected co-occurrence, conservative cross-paper alignment with DO_NOT_MERGE decisions, executable multi-query retrieval, incremental updates, and GraphML/JSON/Markdown export. Use for literature knowledge graph tasks in Harbor.
---

# Multi-Paper Evidence-Anchored Literature Knowledge Graph (`product-arena-10-760d1a0b`)

## Purpose & Trigger Scope

Use this Skill when constructing a queryable scientific knowledge graph across two or more independent papers while preserving per-paper provenance, honest locator boundaries, and strict separation between reported relations and mere sentence co-occurrence.

Critical failure modes prevented by this Skill:
1. **Cross-paper metadata bleed**: Copying Paper 1's root `doi`/`title`/`authors` onto Paper 2, or counting multiple file formats (JSON + MD) of the same paper as "cross-paper evidence".
2. **Co-occurrence promoted to causal edge**: Treating two entities mentioned in the same sentence as a causal or directional relation (`A causes B`) without explicit textual evidence. A cross-paper evidence/method network is NOT automatically a causal graph.
3. **Forced unsafe entity merging**: Merging generic terms (`catalyst`, `model`, `patient cohort`, `efficiency`) or same-spelling entities across different physical samples, datasets, or experimental conditions, or pooling conflicting conditions into one merged conclusion.
4. **Fabricated PDF page numbers**: Labeling JATS XML `paragraph_id` locators or synthetic page 1 as "original PDF page 1" when no PDF page metadata was supplied.

## Hard Invariants

1. **Strict Source Ingestion Order**: Prefer `corpus-manifest.json`, `primary-material.json`, and `secondary-material.json` (plus optional `secondary-publisher.xml` for verifying linearized inline math/subscripts). Read `material.json` ONLY as fallback when primary/secondary originals are absent, and resolve each paragraph's `source_id`/`doi` solely from its own record.
2. **Locator Honesty**: Every entity and edge MUST retain `source_id`, `paragraph_id`, and verbatim `quote`. If `locator_contract.pdf_page_available` is `false`, report locators strictly as JATS/source `paragraph_id` — NEVER claim a PDF page number.
3. **Controlled Relation Discipline & Co-occurrence Firewall**:
   - Directed edges require explicit active phrasing (`uses`, `evaluates_on`, `outperforms`, `compares_with`, `supports`, `contradicts`, `increases`, `decreases`, `inhibits`, `activates`, `associated_with`, `causes_direct`).
   - Undirected co-occurrences are isolated under `co_occurrences` (`co_occurs_with_no_causal_claim`, `directed: false`) and NEVER promoted to directed or causal edges.
   - Hedged, negated, or unanchored candidates are placed in `held_for_review` with explicit reasons (`HEDGED_OR_NEGATED_RELATION`, `MISSING_PARAGRAPH_ID`).
   - Every node and edge distinguishes `author_reported` from `cited_secondary` (`et al.`, `[12]`, `previous studies`).
4. **Conservative Cross-Paper Alignment**:
   - Default to paper-scoped nodes (`stable_id(source_id, category, canonical_name)`).
   - Within a paper, resolve explicit `Full Name (ABBR)` definitions.
   - Across papers, link only named standard methods/datasets/tasks at the concept level (`LINK_CONCEPT_KEEP_SEPARATE_INSTANCES`) while keeping paper-scoped conditions and metrics strictly unpooled; emit explicit `DO_NOT_MERGE` records for generic terms or differing physical/experimental scopes.
5. **Unreported Boundary**: Do not infer numeric values from absent image pixels, supplementary files, or raw clinical/experimental tables; preserve missing inputs and intra/inter-paper conflicts transparently.

## Execution Workflow (DeepSeek Harness / Offline Linux)

All scripts run on pure Python 3.12 stdlib with zero network access.

### Step 1: Preflight & Multi-Paper Graph Construction

```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/budget_guard.py --init --state /app/work/.budget_state.json
python3 -B /skills/weekly-skill/scripts/knowledge_graph_engine.py --input /input --output /app/output --work /app/work
```

Outputs created under `/app/output/`:
- `knowledge_graph.json` — Full entities, directed edges, isolated co-occurrences, `held_for_review`, `cross_paper_alignment`, `conflicts`, `incremental_update`, and 5 executed queries.
- `knowledge_graph.graphml` — Valid GraphML XML with separate directed evidence edges and undirected co-occurrence edges.
- `knowledge_graph_report.md` — Structured Markdown report separating source registry, directed relations, alignment/non-merge decisions, queries, and preserved contradictions.
- `result.json` — Harbor contract summary with `case_id`, `status`, `locator_contract`, `metrics`, `graph_summary`, `cross_paper_alignment`, `conflicts`, and `gaps`.

### Step 2: Ad-Hoc Entity Query & Incremental Update (When Requested by `task.md`)

- **Entity neighborhood query (Q6)**:
  ```bash
  python3 -B /skills/weekly-skill/scripts/kg_query_and_export.py --graph /app/output/knowledge_graph.json --entity "<ENTITY_NAME>"
  ```
- **Incremental update against a prior graph snapshot** (preserves existing node IDs and logs added nodes/edges/new conflicts):
  ```bash
  python3 -B /skills/weekly-skill/scripts/knowledge_graph_engine.py \
    --input /input --output /app/output --work /app/work \
    --prior-graph /input/prior_knowledge_graph.json
  ```

### Step 3: Host-Agent Semantic Enrichment & Case-Specific Queries

Inspect `/app/output/knowledge_graph_report.md`, `held_for_review`, and `secondary-publisher.xml` (if supplied):
1. Check any linearized subscripts/math expressions against `secondary-publisher.xml`.
2. Answer any additional domain-specific queries required by `/input/task.md` using strictly grounded `source_id`, `paragraph_id`, and verbatim quotes, preserving any `explicit_non_merge_decisions` and opposite-condition disagreements without pooling.

### Step 4: Grounding Gate & Final Deliverable Audit

```bash
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require knowledge_graph.json --require knowledge_graph.graphml --require knowledge_graph_report.md
```

## Reference Index

- `references/entity_relation_ontology.md` — 5 scientific entity categories, 12 directed relation types, co-occurrence firewall, and attribution rules.
- `references/cross_paper_alignment_and_non_merge.md` — Paper metadata isolation, within-paper acronym resolution, concept linking vs `DO_NOT_MERGE` rules, and conflict preservation.
- `references/query_and_incremental_protocol.md` — 5 standard queries (Q1–Q5), ad-hoc Q6 entity query, incremental update diff contract, and GraphML schema.
- `references/output_contract_spec.md` — Exact `result.json` and artifact schemas plus JATS locator discipline.
