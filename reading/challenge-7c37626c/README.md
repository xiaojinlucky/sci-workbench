# Formula Parsing & Mathematical Reasoning Skill (`challenge-7c37626c`)

Offline-first, zero-mandatory-dependency Skill for scientific formula extraction, symbol provenance tracing, composite SI dimensional analysis, OCR noise interception, and verifiable mathematical reasoning in Harbor containers.

## Runtime & Dependencies
- **Required**: Python 3.12 standard library only (verified offline in `python:3.12-slim` with `--network none`).
- **Optional automatic enhancements**: `defusedxml` (XML safety; falls back to XXE/DOCTYPE-guarded `xml.etree.ElementTree`), `sympy` (symbolic simplification with `E, I, S, N, O, Q, C` isolation; falls back to pure-AST numeric cross-checking), `pdfplumber`/`pypdf`/`pdftotext` (for PDF prose extraction).

## Quickstart
```bash
python -B scripts/formula_math_engine.py --input /input --output /app/output --work /app/work
python -B scripts/math_markdown_validator.py /app/output/formula_notes.md --json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require formula_verification_report.json --require formula_notes.md
```

## Directory Layout
- `SKILL.md` — DeepSeek Harness executable workflow and boundaries.
- `scripts/formula_math_engine.py` — End-to-end formula extraction, provenance, dimensional, numeric, and Markdown generation pipeline.
- `scripts/math_ast_guard.py` — JATS/MathML deduplicating parser, general composite SI dimensional algebra engine, Pix2Text-class OCR noise detector, and AST/SymPy verifier.
- `scripts/math_markdown_validator.py` — Offline validator for `formula_notes.md` (checks `$`/`$$` balance, bare LaTeX, pseudo-math, literal tabs, and `E...` anchors).
- `scripts/audit_chain.py` — SHA-256 hash-chained audit log.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` — Harbor runtime guardrails (M1–M6).
- `references/` — Detailed specifications for MathML deduplication, composite SI algebra, variable/derivation auditing, and C02 missing-branch contracts.
- `examples/` — Minimal synthetic input/XML/output examples (clearly marked as illustrative only).

## Scope & Limits
- Treats `source-formulas.xml` strictly as an extracted formula catalog rather than a complete derivation.
- Preserves missing-formula branches (`C02`) honestly as `insufficient_evidence` or `partial`; never back-infers equations or substitutes `0` for unknown parameters.
- Does not perform image-to-LaTeX OCR itself; instead intercepts OCR/transcription corruption before downstream reasoning.
