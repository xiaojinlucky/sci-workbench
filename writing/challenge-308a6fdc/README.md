# Academic Manuscript Workbench (`academic-manuscript-writing`)

Offline, zero-dependency Skill for evidence-grounded academic manuscript drafting, 8-gate provenance verification, and editable Markdown/DOCX delivery inside air-gapped Linux Harbor containers.

## Key Capabilities

1. **Traceable Material Indexing (`index`)**: Scans `/input` research tables (`.csv`), metadata (`.json`), bibliographies (`.bib`), notes (`.md`/`.txt`), `.docx` (pure stdlib OOXML), and `.pdf` (when `pdftotext` is present) to map every empirical number, DOI, and figure caption to exact `file:line/row` coordinates.
2. **8-Gate Deterministic Manuscript Audit (`audit`)**:
   - Numeric/unit/sample-size grounding against `/input`
   - Citation 1:1 sync and DOI grounding (separating reference existence from claim support)
   - Results vs. Discussion layering & verbatim numeric repetition guard (handles `UNSPECIFIED` journal layout honestly)
   - Section & mandatory declaration completeness (`Data Availability`, `Competing Interests / Ethics`)
   - Abstract vs. Body numeric reconciliation
   - Acronym first-definition check
   - Bilingual academic de-AI, anti-translationese, and press-release anti-defensive prose check
   - Explicit `[EVIDENCE_GAP: ...]` accounting
3. **Pure-Stdlib Editable DOCX Export (`export`)**: Generates standards-compliant OOXML `.docx` (Heading 1–3 outline, three-line academic tables, `Liberation Serif` default font) with zero external packages, optionally invokes LibreOffice for PDF/PNG preview with honest degradation, and seals output-contract-compliant `/app/output/result.json`.

## Environment & Dependencies

- **Runtime**: Python 3.12+ standard library only (verified offline in `python:3.12-slim --network none`).
- **Optional tools**: `soffice` (LibreOffice Writer) + `pdftoppm` for PDF/PNG previews; `pdftotext` for PDF text extraction. Missing tools degrade gracefully without blocking core `.md`/`.docx`/`.json` output.

## Quick Verification Example

See `examples/minimal_example.md` for a synthetic end-to-end CLI invocation and sample structured output.
