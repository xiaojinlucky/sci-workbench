# Minimal Synthetic Invocation Example (Illustrative Only)

> **Notice**: All inputs and outputs below are synthetic examples demonstrating the CLI interface and output schema. They are not formal evaluation test cases or benchmark results.

## 1. Synthetic Input Directory (`/input`)

- `case.json`: see `examples/sample_case.json`
- `synthetic_paper.md`: short two-section paper snippet with a Markdown table and display equation (or any supplied `.pdf` in `/input`).

## 2. Invocation Command

```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/doc_parser_engine.py --input /input --output /app/output --work /app/work --case-id SYNTH-DOC-01 --ocr-langs eng
python /app/output/src/reproduce.py --case-id SYNTH-DOC-01 --output-dir /app/output
python scripts/final_audit.py --dir /app/output --require result.json --require parsed_document.md --require document_parsing_bundle.json --require arm_manifest.json
```

## 3. Illustrative Output Schema (`examples/sample_output_schema.json`)

Key structures emitted to `/app/output/result.json`:
- `page_coverage_audit`: confirms `first_page_covered`, `mid_page_covered`, `last_page_covered`, and `tail_10pct_coverage_ratio`.
- `layout_analysis`: reports `deinterleaved_pages_count`, `stripped_headers_footers_count`, and per-page `gutter_col`.
- `symbol_fidelity_audit`: distinguishes `•` (U+2022) vs `·` (U+00B7) and flags `U+FFFD` / `(cid:N)` font errors.
- `reading_order_blocks[]`: each block carries `block_id`, `page`, `column`, `line_span`, and `extraction_method` (`native` or `tesseract`).
- `platform_managed_artifacts`: declares `["trace/trace.jsonl"]` without fabricating it.
