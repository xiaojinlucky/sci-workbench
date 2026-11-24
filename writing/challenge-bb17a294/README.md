# Bilingual Academic Translation and Parallel Writing Skill (`bilingual-academic-translation`)

Offline, deterministic EN-ZH academic translation, native PDF/DOCX/text extraction, sentence-and-paragraph bilingual alignment, cell-level table auditing, live-citation/reference consistency, and anti-translationese quality guard.

## Purpose and Scope

Designed for scholarly translation and bilingual parallel writing tasks requiring strict scientific fidelity and editable outputs directly from raw `.pdf`, `.docx`, `.md`, or `.txt` inputs:

1. **Zero-dependency native document extraction & `/input` bootstrapping (`scripts/doc_extract.py` + `--extract-terms-and-refs`)**: Automatically reads `.pdf` (`pdftotext` preserving physical empty pages -> `pypdf` -> `pdfplumber` -> pure-stdlib `zlib` stream fallback), `.docx` (pure-stdlib OOXML paragraphs, Markdown tables, `footnoteReference` -> `[^id]`, footnotes, and `w:instrText` / `w:fldChar` live citation field detection for Zotero/CSL, Mendeley, EndNote, Citavi, and Word `REF` with honest `citation_fields_preserved: false` reporting upon Markdown export), and multi-encoding text/Markdown files (`utf-8-sig`/`utf-8`/`gb18030`/`latin-1`), persisting `source_extracted.txt`, `source_blocks.json`, `glossary_draft.json`, and `reference_map_draft.json` with an immutable `source_citation_ledger`.
2. **`Decimal`-exact magnitude, range, scientific notation, statistics, SI unit, table-cell, and order verification**: Equivalences Chinese/English magnitudes (`千/万/亿` ↔ `thousand/million/billion`), explicit numeric ranges (`40-60`, `12.5–18.0%`, `10~20 mg/L`, `-0.2-28.4`), scientific notation (`1.5e-4` ↔ `1.5×10^-4`), percentages, `p`-value operators, confidence intervals, and SI units (`μg/mL`, `mA/cm²`, `mol/L`, `rpm`) via `decimal.Decimal`. Audits Markdown tables cell-by-cell (respecting escaped `\|`) and catches silent group/condition value swaps while supporting explicit `reordered_clause_note` attestations.
3. **Immutable `source_citation_ledger`, heading hierarchy 1:1, display, equation, and bilingual bibliography integrity**: Locks source references into an immutable `source_citation_ledger` (`cite_key`, original fields, `source_doi`, `source_page`/`source_line`) decoupled from display-only `translated_title`. Blocks unverified external additions (`unverified_external_additions`), unauthorized DOI completions of missing/truncated source DOIs (`unverified_doi_completions`), cross-entry translated title swaps (`reference_title_swap`), and 1:1 section heading drift (`HEADING_HIERARCHY_DRIFT`).
4. **Sentence-level negation scope and epistemic hedge conservation**: Abbreviation-aware EN/ZH sentence splitting checks negation polarity at both paragraph and matched-sentence level, preventing hidden polarity flips in mixed-polarity paragraphs, and blocks correlation-to-causation overclaim escalation.
5. **Automated exports & bounded long-manuscript style auditing**: Generates sentence-level editable `bilingual_aligned.md`, explicit `unaudited_paragraphs` tracking (zero silent `zip` truncation), `translation_audit.json` with unified `errors`/`blocking_issues`, bounded `style_warnings` + `style_warning_summary`, and a 5-field `result.json` contract patch.

## Directory Layout

- `SKILL.md`: Agent execution protocol, invariants, and completion criteria.
- `scripts/doc_extract.py`: Zero-dependency PDF/DOCX/text extractor with page/line offset provenance.
- `scripts/bilingual_alignment_guard.py`: `/input` term/reference bootstrapper and deterministic multi-channel bilingual auditor.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Common Harbor M1-M6 runtime guards.
- `references/bilingual-fidelity-rules.md`: Magnitude tables, `reordered_clause_note` syntax, bibliography sync, and hedge rules.
- `references/academic-chinese-style.md`: Anti-translationese and natural scholarly prose guide.
- `references/output-contract-and-qa.md`: Exact 5-field `result.json` schema and pre-seal QA checklist.
- `examples/{synthetic_example,bilingual_table_math_example}.md`: Minimal illustrative interface examples.

## Dependencies and Runtime Requirements

- Python 3.12 standard library only (`argparse`, `collections`, `json`, `math`, `re`, `pathlib`, `shutil`, `subprocess`, `xml.etree.ElementTree`, `zipfile`, `zlib`). Optional acceleration if `pdftotext`, `pypdf`, or `pdfplumber` is preinstalled; zero network access required.

## CLI Usage

### 1. Auto-Extract `/input` Source, Glossary Draft, and Reference Map Draft
```bash
python3 scripts/bilingual_alignment_guard.py \
  --extract-terms-and-refs \
  --from-input /input \
  --work-dir /app/work \
  --out-json /app/work/extraction_summary.json
```

### 2. Audit Bilingual Fidelity and Export Sentence-Level Aligned Markdown
```bash
python3 scripts/bilingual_alignment_guard.py \
  --source /input/source.pdf \
  --source-format auto \
  --extracted-source-out /app/work/source_extracted.txt \
  --target /app/output/translated.md \
  --glossary /app/work/glossary.json \
  --references /app/work/reference_map.json \
  --case-id "<CASE_ID>" \
  --out-json /app/output/translation_audit.json \
  --out-aligned /app/output/bilingual_aligned.md \
  --out-contract-patch /app/work/contract_patch.json
```

Exit code `0` indicates `"passed": true` (zero `blocking_issues`). Non-zero exit indicates at least one blocking extraction, alignment, table, numeric, order, citation, bibliography, display, equation, negation, causal-escalation, truncation, or glossary error.

## Limitations and Boundaries

- Performs deterministic structural, table-cell, numerical, citation, bibliography, sentence-polarity, and lexical-style verification offline; subtle domain phrasing still benefits from researcher review via the exported sentence-level `bilingual_aligned.md`.
- Image-only scanned PDFs without an embedded text layer cannot be decoded without OCR; `doc_extract.py` explicitly flags empty extractions rather than fabricating text.
- Does not query live external DOI registries; bibliography metadata consistency is audited against the supplied source document and optional `reference_map.json`.
- Offline benchmark numbers in companion comparison directories reflect local self-tests on open-access papers/patents, not official hidden platform whiteboards.
