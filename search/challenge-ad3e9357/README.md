# Patent & Technical Literature Retriever (`challenge-ad3e9357`)

Offline, pure-stdlib Python 3.12 Skill for auditable patent normalization, dual DOCDB/INPADOC family consolidation, slash-safe CPC/IPC query construction, source-located claim decomposition, WO OCR boundary auditing, and technical literature comparison.

## Capabilities

- **WIPO ST.16 Patent Parser (`scripts/citation_graph_dedup.py`)**: Strictly separates `publication_number`, `application_number`, `grant_number`, `kind_code` (`A1/A2/A3/A4`, `B/B1/B2/C`, `U/U1/Y`, `E`), `jurisdiction`, dates, assignees, and inventors.
- **Dual-Metric Family Engine (`scripts/patent_retrieval_auditor.py`)**: Reports `publication_document_count`, `docdb_simple_family_count` (exact priority set), and `inpadoc_extended_family_count` (Union-Find connected priority components) side-by-side without ever pooling unrelated keyword-matched patents.
- **CPC/IPC Slash-Safe Queries**: Normalizes CPC/IPC classes and emits USPTO-quoted and Lucene slash-escaped query strings separated from academic literature queries.
- **Claim Decomposition & Honest Legal/OCR Boundaries**: Extracts source-located Claim 1 and dependent claim counts (`equal_claim_scope_assumed: false`), detects WO/HTML `claims ocr`/scanned PDF boundaries, enforces `current_official_legal_status: "UNKNOWN"` offline, and separates scientific comparison papers cleanly.

## Quick Execution

```bash
python scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python scripts/budget_guard.py --state /app/work/.budget_state.json --init
python scripts/patent_retrieval_auditor.py --input /input --output /app/output --work /app/work
python scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python scripts/final_audit.py --dir /app/output --result-file result.json --require patent_search_report.json --require patent_landscape_report.md
```

## Dependencies & Limits

- **Runtime**: Python 3.10+ standard library only (zero external network calls; zero banned packages).
- **Legal & Coverage Limits**: Informational retrieval only (not FTO/infringement legal advice); finite offline snapshots cannot establish exhaustive global families or live official legal status.
- **Examples**: See `examples/sample_input_case.json` and `examples/sample_output_schema_example.json` (explicitly marked synthetic).
