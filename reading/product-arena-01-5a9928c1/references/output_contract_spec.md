# Output Contract Specification (`product-arena-01-5a9928c1`)

All final deliverables reside under `/app/output/`. Working intermediates belong in `/app/work/`.

## 1. `/app/output/result.json`

Must be valid RFC-8259 UTF-8 JSON with finite numbers only (no `NaN`/`Infinity`).

| Field | Type | Required Contract |
| --- | --- | --- |
| `schema_version` | string | `"2.0"` |
| `case_id` | string | Exact match to `/input/case.json` `case_id`. |
| `task_id` | string | `"product-arena-01-5a9928c1"` |
| `status` | string | Harbor top-level status: `"ok"` when audit passes with zero blocking gaps, else `"partial_with_gaps"`. |
| `translation_status` | string | Task-level translation state: `"completed"`, `"partial"`, `"needs_human_review"`, or `"input_error"`. |
| `summary` | object | `{domain, total_paragraphs, translated_paragraphs, coverage_ratio, needs_human_review_count, overview}`. |
| `source_metadata` | object | Immutable source bibliography block: `{item_id, source_title, translated_title, authors, doi, year, source_file, source_sha256}`. `source_title`, `authors`, `doi`, and `item_id` must match `/app/work/source_metadata_lock.json` byte-for-byte. |
| `bilingual_segments` | array | Ordered 1:1 list matching `source_segments.json`. Each item contains: `paragraph_id`, `section`, `page`, `item_id`, `source_en`, `target_zh`, `terms_used[]`, `translation_basis`, `needs_human_review` (bool), `review_reason` (string). |
| `terminology_table` | array | Locked terms with observed enforcement stats: `{source, target, first_mention_anchor, occurrences_source, occurrences_target, violations_paragraphs[], status}`. |
| `invariants_check` | object | Deterministic summary: `{passed, bibliography_identity_ok, cross_item_clean, paragraph_alignment_ok, terminology_consistency_rate, numeric_drift, unit_drift, formula_drift, citation_drift, figure_table_drift, negation_drift, modal_upgrade_drift, sentence_density_drift, discourse_logic_drift, page_coverage, blocking_errors[], warnings[]}`. |
| `uncertainty_or_gaps` | array | Explicit list of `{paragraph_id, category, reason, recommended_action}` for unreadable pages, rasterized equations/tables, ambiguous terms, or unresolved audit warnings. |
| `artifacts` | array | Relative filenames under `/app/output/`: `["result.json", "translation_bilingual.md"]` (plus `"glossary.csv"` when `--export-glossary-csv` targets `/app/output`). |
| `metrics` | object | Finite numeric metrics with matching keys in `metric_provenance`. |
| `findings` | array | Concise factual summary statements grounded in audit outputs. |
| `gaps` | array | Structured Harbor gap entries (`code`, `severity`, `source`, `message`); empty list `[]` when fully clean. |

## 2. `/app/output/translation_bilingual.md`

Human-verifiable bilingual Markdown document generated atomically by `bilingual_alignment_guard.py --audit`:

1. **Document Header & Protected Source Metadata**: Displays immutable `item_id`, `source_title`, `translated_title`, `authors`, `doi`, `year`, and extraction coverage summary.
2. **Locked Terminology Table**: Markdown table listing `Source Term`, `Canonical Chinese`, `First-Mention Anchor`, `Source Hits`, `Status`.
3. **Paragraph-Aligned Bilingual Body**: Each paragraph rendered under its immutable anchor (`### [P001] Section (Page N)`), showing:
   - **Original (`source_en`)**: Verbatim source paragraph.
   - **Translation (`target_zh`)**: Natural academic Chinese translation.
   - **Verification Note**: `Terms`, `Basis`, and explicit `[NEEDS HUMAN REVIEW]` flag when triggered.
4. **Human Review & Uncertainty Checklist**: Consolidated list of all flagged segments and source-side gaps (or explicit confirmation of zero pending flags).
