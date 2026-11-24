# Cross-Lingual Scientific Literature Retriever (`challenge-802a7115`)

Offline, pure-stdlib Python 3.12 Skill for auditable bidirectional cross-language literature search, controlled terminology expansion, 4-class view consolidation, original-title preservation, and sentence-level evidence alignment.

## Capabilities

- **Zero-Dependency Corpus Ingestion (`scripts/corpus_loader.py`)**: Reads `case.json`, `task.md`, SciELO ArticleMeta JSON, multilingual HTML (SciELO/JATS), BibTeX, RIS, arXiv XML, CSV, Markdown/text, and PDFs (via non-banned extractors `pdfplumber` / `pypdf` / `pdftotext` when available).
- **Original Field Immutability (`title_overwrite_audit`)**: Preserves `original_title`, `raw_title`, `titles_by_language`, and `translated_title` separately; prevents translation text from overwriting native titles.
- **Canonical 4-Class View Deduplication**: Consolidates `original_fulltext`, `publisher_full_translation`, `english_abstract_only`, and `exact_duplicate` under canonical `work_id` via normalized DOI and SciELO PID without double-counting, while strictly separating homonyms carrying distinct DOIs (`homonym_distinct_doi_separated`).
- **Controlled Terminology & False-Friend Auditing**: Supports EN/PT/ZH/ES/DE/FR domain glossary expansion, diacritic folding, zero-translation locking for gene/chemical symbols, word-boundary locking for short acronyms (`<= 4` chars), and false-friend / transliteration drift alerts.
- **Verifiable Outputs**: Produces `/app/output/cross_lingual_alignment.json`, `/app/output/cross_lingual_report.md`, and `/app/output/result.json`, backed by SHA-256 hash-chained `/app/work/audit_log.json`.

## Quick Execution

```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/budget_guard.py --state /app/work/.budget_state.json --init
python scripts/cross_lingual_matcher.py --input /input --output /app/output --work /app/work
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require cross_lingual_alignment.json --require cross_lingual_report.md
```

## Dependencies & Limits

- **Runtime**: Python 3.10+ standard library only (zero external network calls; zero banned packages).
- **Scope Limits**: Operates strictly over the finite supplied corpus snapshot; never claims live database coverage, unseen languages, or full-text verification from abstract-only views.
- **Examples**: See `examples/sample_input_case.json` and `examples/sample_output_schema_example.json` (explicitly marked synthetic).
