# Output Contract Specification (`product-arena-08-3027d350`)

## Dual-Contract Artifact Inventory

`product-arena-08-3027d350` is scored under `llm_judge_topic_markdown` while executing inside the Harbor runtime container. Every run must emit all locally owned artifacts under `/app/output/`:

| Artifact Path | Contract Owner | Required Contents |
| --- | --- | --- |
| `/app/output/result.json` | Harbor primary | `case_id`, `status` (`ok` or `partial_with_gaps`), `sources`, `uncertainty`, `artifacts`, `platform_managed_artifacts`, `pdf_sources`, `page_coverage_audit`, `layout_analysis`, `structural_completeness`, `symbol_fidelity_audit`, `metrics`, `metric_provenance`, `findings`, `section_hierarchy`, `reading_order_blocks`, `tables`, `continued_table_candidates`, `formulas`, `captions`, `footnotes`, `gaps` |
| `/app/output/parsed_document.md` | Human / LLM Judge | Structured Markdown parse with coverage table, section tree, anchored reading blocks `[B0001 | p.1 | left | L4-L11 | native]`, extracted tables, formulas, and explicit gaps |
| `/app/output/document_parsing_bundle.json` | Full data bundle | Unclipped `reading_blocks[]`, separated `pdf_page_texts[]` (`native_text` vs `ocr_text`), `stripped_headers_footers[]`, `layout_analysis`, `tables`, `formulas`, `gaps` |
| `/app/output/arm_manifest.json` | Topic-Markdown | `arm_version`, `challenge_id`, `case_id`, `paper`, `entrypoint` (`src/reproduce.py`), `execution`, `artifacts`, `platform_managed_artifacts` |
| `/app/output/characterization.json` | Topic-Markdown | Observed parser metrics, coverage/layout/symbol audits, and explicit disclaimer that official scores belong only to the platform evaluator |
| `/app/output/src/reproduce.py` | Topic-Markdown | Offline deterministic verifier reading `document_parsing_bundle.json` and `result.json` to regenerate `execution/results/summary.json` |
| `/app/output/execution/run.log` | Topic-Markdown | Honest local parser execution log (never masquerades as native session trace) |
| `/app/output/execution/results/summary.json` | Topic-Markdown | Machine-readable run summary aligned with `result.json` metrics |
| `/app/output/README.md` | Topic-Markdown | Concise run overview and reproduction command |
| `/app/output/trace/trace.jsonl` | Platform-managed | Captured natively by DeepSeek Harness; declared in `platform_managed_artifacts` and NEVER synthesized by skill scripts |

## Status Semantics

- `ok`: Extractable scientific body present and zero blocking or unverified gaps recorded.
- `partial_with_gaps`: Any OCR page (unverified against visual original), missing/unreadable page, encrypted PDF, rotated scan, garbled font token, or absent scientific body is recorded in `gaps[]`.
- Every metric key in `metrics` must have a matching provenance explanation in `metric_provenance` and a finite value (`int`/`float`, never `NaN`/`Inf`).
