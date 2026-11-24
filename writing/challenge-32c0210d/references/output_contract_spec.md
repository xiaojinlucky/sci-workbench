# Output Contract Specification

Every run must write `/app/output/result.json` with valid RFC-8259 UTF-8 JSON (no NaN/Inf) preserving the exact `case_id` from `/input/case.json` and all five required contract fields:

| Field | Required Content | Verification Standard |
| --- | --- | --- |
| `case_id` | Exact string from `/input/case.json` | Unmodified verbatim match |
| `deliverable` | Paths and concise summary of editable revised chapters, `revision.diff`, `export_preview.html`, and `terminology_and_change_log.md` | Every listed path exists under `/app/output` and is non-empty |
| `preserved_facts` | Structured record of protected source bytes (`initial_sha256` vs unrequested chapter hashes), preserved numbers, formulas, citations, and unresolved source conflicts left untouched | Zero unapproved modifications outside workorder targets |
| `evidence_map` | Array mapping each editorial annotation or consistency finding to exact source file, anchor ID (`C01-P002`, etc.), verbatim quote, and verification status | Every claim traces back to an inspected `/input` file |
| `change_log` | Complete ledger of accepted and rejected proposals, rollback/reapply SHA-256 verification receipts, `deliberate_fault_injection` test outcomes, author confirmation items, and material/truncation gaps | Separates verified changes, rejected candidates, deliberate fault injections, and open gaps |

## Standard Deliverable Files Under `/app/output/`

- `result.json`: Final structured output containing the 5 contract fields plus `status` (`ok` or `partial_with_gaps`), `artifacts`, `metrics`, `metric_provenance`, `findings`, and `gaps`.
- `import_receipt.json`: Original SHA-256 inventory, paragraph anchor map, coverage manifest, and truncation flags.
- `chapters/`: Editable revised chapter copies (with unrequested chapters byte-identical to `/input`).
- `revision.diff`: Standard unified diff between `v0` and final applied state.
- `revision_records.json`: Full proposal history (accepted + rejected), version chain, rollback and reapply SHA-256 verification records.
- `evidence_map.json`: Machine-readable anchor-to-quote mapping.
- `fault_test_report.json`: Controlled invalid-operation exception records proving zero state/file mutation (`state_unchanged_verified: true`).
- `export_preview.html`: Single-file offline HTML diff and audit preview (zero external requests).
- `longdoc_audit.json`: Post-revision cross-chapter consistency and scope protection report.
- `terminology_and_change_log.md`: Human-readable synthesis of glossary/symbols, cross-chapter conflicts, change rationale, and open author confirmations.
