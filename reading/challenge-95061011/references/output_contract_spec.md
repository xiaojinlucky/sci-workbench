# Output Contract Specification (`challenge-95061011`)

## 1. Inputs
- Primary workorder: `/input/comparison-workorder.json` (defines `fields`/`common_fields`, optional `documents`, optional `scope_keywords`/`research_question`, optional `comparability_axes`).
- Case metadata: `/input/case.json` (`case_id`, optional `title`, `fields`, `documents`).
- Corpus files under `/input/`: `.pdf`, `.json`, `.md`, `.txt`, `.csv`.

## 2. Required Deliverables under `/app/output/`
1. `result.json` — primary structured result following Harbor contract (`case_id`, `status` in `{"ok", "partial_with_gaps"}`, `artifacts`, `metrics`, `metric_provenance`, `findings`, `gaps`) plus comparison payloads below.
2. `comparison_matrix.md` — human-auditable Markdown containing:
   - Section 1: Citation Legend (`[1]..[N]` -> `source_document_id`, Title, DOI, Source File, SHA-256 prefix, Version Status)
   - Section 2: Document Inventory & Deduplication / Scope Exclusions
   - Section 3: Common-Field Comparison Matrix (values with `[n]` + `paragraph_id` anchors; `not_reported` explicitly shown)
   - Section 4: Functional Method Comparison Matrix (`method_name`, `inputs`, `grouping`, `transformation`, `filtering_rule`, `output_semantics`, `fact_tag`)
   - Section 5: Six-Axis Comparability Gate (`comparable_subgroups`, `noncomparable_pairs`, `indeterminate_pairs`, Unit Normalization Advisories)
   - Section 6: Consensus — Partial Agreement — Conflict Attribution Three-Column Matrix
3. `comparability_audit.json` — standalone machine-auditable comparability and validation ledger.

## 3. Core JSON Schema Fields in `result.json`

### `citation_legend[]`
Each entry maps a deterministic 1-based citation integer to a corpus document:
```json
{
  "citation_number": 1,
  "citation_tag": "[1]",
  "source_document_id": "DOC01",
  "title": "Paper Title",
  "doi": "10.1000/example.01",
  "source_file": "paper1.pdf",
  "file_sha256": "e3b0c44298fc1c14...",
  "version_status": "version_of_record",
  "paragraph_count": 24,
  "page_count": 8
}
```
Allowed `version_status` values: `version_of_record`, `preprint`, `duplicate_superseded`, `out_of_scope_excluded`, `unclassified`.

### `comparison_matrix[]`
One row per effective (canonical, in-scope) document:
```json
{
  "citation_number": 1,
  "citation_tag": "[1]",
  "source_document_id": "DOC01",
  "title": "Paper Title",
  "paragraph_count": 24,
  "cells": {
    "pollutant": {
      "field": "pollutant",
      "citation_number": 1,
      "source_document_id": "DOC01",
      "paragraph_id": "DOC01:p2-b3",
      "value": "methylene blue",
      "raw_value": "methylene blue",
      "units": "",
      "conditions": "pH 7.0, 25 C",
      "quote": "Methylene blue degradation was measured at pH 7.0 and 25 C.",
      "status": "reported",
      "fact_tag": "explicitly_stated"
    }
  }
}
```
Allowed `status`: `reported` | `not_reported` | `ambiguous`.
Allowed `fact_tag`: `explicitly_stated` | `functionally_equivalent` | `inferred_unverified`.

### `functional_method_comparison[]`
```json
{
  "citation_number": 1,
  "source_document_id": "DOC01",
  "paragraph_id": "DOC01:p3-b1",
  "method_name": "pseudobulk edgeR LRT",
  "inputs": "raw UMI count matrix",
  "grouping": "summed per biological replicate and cell type",
  "transformation": "TMM library-size normalization",
  "filtering_rule": "FDR < 0.05 and |log2FC| >= 1",
  "output_semantics": "replicate-aware differential expression gene list",
  "fact_tag": "explicitly_stated",
  "quote": "Counts were summed per biological replicate and tested via edgeR LRT."
}
```

### `comparability_assessment`
- `multi_document_satisfied`: boolean (`false` when effective independent in-scope documents < 2)
- `pooling_allowed`: boolean (`true` only when `multi_document_satisfied` is true, `noncomparable_pairs` is empty, `indeterminate_pairs` is empty, and `comparable_subgroups` is non-empty)
- `axes_tested`: `["task", "metric", "units", "material", "pollutant", "budget"]`
- `comparable_subgroups[]`: list of cliques (`{"subgroup_id": "SG1", "documents": [...], "shared_axes": {...}}`)
- `noncomparable_pairs[]`: list of pairs with confirmed axis mismatches (`pair`, `mismatched_axes[]`, `unreported_axes[]`, `pooling_blocked: true`)
- `indeterminate_pairs[]`: list of pairs with zero confirmed mismatches but >=1 unreported/ambiguous axis (`pair`, `unreported_axes[]`, `pooling_blocked: true`)
- `unit_normalization_advisories[]`: explicit scale-conversion hints preserving original `raw_value` and `units`

### `consensus_conflict_matrix`
```json
{
  "consensus": [],
  "partial_agreement": [],
  "conflicts": [
    {
      "topic": "main_conclusion",
      "attribution_dimensions": ["condition", "method"],
      "attribution_rationale": "DOC01 and DOC02 differ in pH and catalyst loading.",
      "positions": [
        {"citation_tag": "[1]", "source_document_id": "DOC01", "paragraph_id": "DOC01:p4-b2", "direction": "positive", "quote": "..."},
        {"citation_tag": "[2]", "source_document_id": "DOC02", "paragraph_id": "DOC02:p5-b1", "direction": "negative", "quote": "..."}
      ]
    }
  ]
}
```
Allowed `attribution_dimensions`: `sample`, `method`, `condition`, `metric`, `unattributable`.

### `matrix_validation`
Produced by `--validate-matrix` and embedded in both `result.json` and `comparability_audit.json`:
- `ok`: boolean
- `checked_cells`: integer
- `cross_paper_bleed_errors[]`: list of `{source_document_id, paragraph_id, actual_owner_document_id, field}`
- `quote_grounding_errors[]`: list of cells whose quote is not a substring of the cited paragraph
- `illegal_pooling_errors[]`: list of pooled groups violating comparability or deduplication rules
- `citation_legend_errors[]`: list of dangling, mismatched, or duplicate citation indices
