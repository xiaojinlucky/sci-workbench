# Grounded Scientific Literature Q&A Skill (`challenge-15c85674`)

A self-contained, zero-dependency Python 3.12 Harbor Skill for verifiable scientific literature question answering across multi-domain papers (`/input/case.json` and multi-page PDF inputs).

## Core Improvements Over Unassisted Baseline

1. **Four-Stage Observable Coverage Audit**: Explicitly separates `ingested_pages`, `extracted_pages` (detecting empty/scanned pages), `indexed_paragraphs` (with dedicated `tail_page_coverage_check` and `table_footnote_coverage_check`), and `cited_paragraphs` so long documents never suffer silent "first-page-only" blindness.
2. **BM25 + Numeric/Entity + Table/Footnote/Tail-Page Retrieval**: Upgrades basic keyword lookup to Okapi BM25 with numeric/SI unit matching, table/footnote boosting, tail-page visibility, and bilingual atomic sub-question decomposition.
3. **Cross-Section Conflict Pre-Detection**: Automatically flags paragraph pairs (Abstract vs Body vs Tables/Discussion) sharing key metrics/entities with diverging numerical values or opposite polarity so the Agent cites both sides transparently.
4. **10-Gate Semantic Zero-Fabrication Validator (`--validate-answers`)**: Verifies 100% question coverage, exact paragraph-scoped verbatim quotes (blocking cross-paragraph borrowing), `reported` numeric-core fidelity (every thousands-separated sample size/percentage/count in `reported` claims must appear in its verified quote), `inferred` reasoning-premise & anti-disguise rules, atomic sub-question landing coverage (blocking dropped sub-questions falsely marked `answered`), `conditions[]` numeric/SI unit fidelity, and honest coverage reporting before atomically writing `/app/output/result.json` and `/app/answers.json` under the current `64 requests / 16384 tokens / 900s` runtime budget.

## Directory Layout

- `SKILL.md`: Primary Agent execution workflow (compatible with `/skills/literature-qa` and `/skills/weekly-skill`).
- `references/output_contract_spec.md`: Exact schema for `/app/output/result.json` and `/app/answers.json`.
- `references/qa_evidence_and_coverage_protocol.md`: Four-stage coverage audit, tail-page & footnote checks, and honest abstention protocol.
- `references/subquestion_and_conflict_rules.md`: Bilingual compound sub-question splitting, epistemic tagging (`reported`/`inferred`/`conflict`/`insufficient_evidence`), and condition preservation.
- `scripts/literature_qa_engine.py`: Indexing, BM25 retrieval, conflict pre-check, coverage auditor, and 7-gate validator.
- `scripts/citation_anchors.py`: Paragraph-scoped verbatim quote verifier, E/M/L/S/I fingerprint extractor, numeric/SI unit condition verifier, and conflict detector.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Harbor runtime safety guardrails (M1–M6).
- `examples/`: Minimal synthetic interface examples (`sample_input_case.json`, `sample_output_schema_example.json`, `minimal_example.md`).

## Requirements & Known Limits

- Python 3.12 standard library only; no external Python packages or network access required.
- Optional system Poppler binaries (`pdftotext`/`pdfinfo`) are used automatically when a PDF is present; scanned image-only pages are detected and reported as `empty_or_scanned_pages` rather than silently skipped or fabricated.
- Automatic lexical/BM25 retrieval and conflict pre-detection assist reading but do not replace full semantic verification by the host Agent.
