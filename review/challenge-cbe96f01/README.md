# Citation & Fact Verification Skill (`challenge-cbe96f01`)

Offline-first, zero-third-party-dependency academic citation verification and factual provenance auditing Skill built for an offline Python 3.12 sandbox (`python:3.12-slim --network none`).

## Core Capabilities

1. **Multi-System Citation Extraction**: Extracts numeric brackets (`[n]`, `[1-3, 5]`), LaTeX `\cite{...}`, English/Chinese Author-Year (`(Smith et al., 2021; Lee & Kim, 2020)`), and explicit `DOI`/`PMID`/`arXiv` identifiers.
2. **Three-Way Reconciliation**: Cross-checks in-text marker counts against bibliography/BibTeX entries and actual resolved source objects to detect dangling citations, duplicate bibliography numbers, uncited entries, and many-to-one marker collapse.
3. **Atomic Sub-Claim Decomposition & Five-State Grading**: Splits compound cited sentences into atomic sub-claims and classifies each against aligned offline source texts into five explicit states:
   - `supported_by_source`
   - `partially_supported`
   - `contradicted_by_source`
   - `source_not_provided` (never conflated with fabrication)
   - `doi_or_metadata_only_unsupported`
4. **Source Reliability & Internal Consistency**: Flags retracted/withdrawn/erratum/preprint references, ungrounded prior-literature numeric claims, and Abstract-vs-Results sample-size or percentage conflicts.

## Package Layout

- `SKILL.md`: DeepSeek Harness execution protocol and anti-false-positive guardrails.
- `scripts/citation_fact_verifier.py`: Pure-stdlib verification engine producing `citation_audit.json`, `citation_review_report.md`, and dual-compatible `result.json` (via `--out-result-json`).
- `scripts/review_io_core.py`: Pure-stdlib document parser supporting MD/TXT/TEX/DOCX/PDF (cascade with wrapped-paragraph merging and low-text gap detection) and canonical section splitting.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Offline sandbox execution, grounding, and contract verification utilities.
- `references/citation_verification_rubric.md`: Domain rubric for atomic decomposition and five-state grading.
- `references/evidence_and_fairness_protocol.md`: Five-gate finding admissibility and ACL H1–H17 fairness firewall.
- `references/output_contract_spec.md`: Output schema for `result.json` (`status: ok|partial_with_gaps` + `audit_status`).
- `examples/minimal_example.md`: End-to-end minimal usage example with labeled illustrative output.

## Quick Start (Offline Python 3.10+)

```bash
python -B scripts/citation_fact_verifier.py \
  --input /input \
  --out-json /app/output/citation_audit.json \
  --out-md /app/output/citation_review_report.md \
  --out-result-json /app/output/result.json
```

## Known Scope & Limitations

- Offline execution verifies semantic claim support only against source texts provided under `/input`; references without supplied full text are transparently marked `source_not_provided`.
- Scanned PDFs lacking an embedded text layer cannot be OCR'd in a minimal pure-Python container and are logged via `PDF_LOW_TEXT_PAGE` gaps without fabricating text.
