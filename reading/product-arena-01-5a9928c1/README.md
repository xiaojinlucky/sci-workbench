# Scientific Literature Translation Skill (`product-arena-01-5a9928c1`)

A self-contained, zero-dependency Python 3.12 Harbor Skill for verifiable English-Chinese scientific literature translation.

## What It Solves

Compared with an unassisted baseline Agent that translates ad-hoc and silently drifts on terms, units, or bibliography headers, this skill enforces four verifiable guarantees:

1. **Terminology Stability**: Two-stage candidate extraction (`--prepare`) -> frozen `glossary.json` -> paragraph-by-paragraph canonical enforcement & forbidden-variant blocking (`--audit`).
2. **Paragraph Completeness & Bibliography Identity Protection**: Immutable `paragraph_id` mapping across JSON, Markdown, and multi-page PDF (`pdftotext` when available), paired with `source_metadata_lock.json` that prevents overwriting original `source_title`, `authors`, `doi`, or leaking across distinct `item_id` entries.
3. **Quantitative, Formula, Citation & Logic Invariants**: Deterministic checking of numbers, Chinese/English magnitude scaling (`1.5万` = `15,000`), SI unit-value pairing (`5 mg` vs `5 μg`, `50 mV` vs `50 V`, `25 °C` vs `25 K`), statistical ranges (`±`, `95% CI`), LaTeX/Greek symbols, figure/table tags, citation markers, negation polarity, and modal hedge preservation (blocking unauthorized certainty upgrades).
4. **Academic Fluency Without Translationese**: Glossary-aware lint (`scripts/academic_deai_lint.py`) flagging Chinese translationese and AI template clichés while protecting technical terms and numeric expressions.

## Directory Layout

- `SKILL.md`: Primary Agent execution contract and 6-step workflow.
- `references/output_contract_spec.md`: Exact schema for `/app/output/result.json` and `/app/output/translation_bilingual.md`.
- `references/terminology_and_invariants_protocol.md`: Rules for terminology locking, bibliography identity, SI units, magnitudes, negation, and modal hedges.
- `references/bilingual_quality_and_deai_guide.md`: Long-document chunk continuation, term anchoring, and anti-translationese / de-AI rewrite rules.
- `scripts/bilingual_alignment_guard.py`: Dual-mode `--prepare` and `--audit` engine (also supports legacy `--source`/`--target` file pair auditing).
- `scripts/academic_deai_lint.py`: Glossary-aware translationese and AI-cliché detector.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Harbor runtime safety guardrails (M1–M6).
- `examples/`: Minimal synthetic input/output illustration (`sample_case.json`, `sample_bilingual_output.json`, `minimal_example.md`).

## Requirements & Constraints

- Python 3.12 standard library only; no external `pip` packages or network access required.
- Optional system binary `pdftotext` / `pdfinfo` (Poppler) is automatically detected for page-numbered PDF text extraction; if absent or if pages lack a text layer, the script degrades cleanly and records explicit coverage gaps.
- No physical unit conversion is performed automatically; unit changes are flagged for human or translator verification.
