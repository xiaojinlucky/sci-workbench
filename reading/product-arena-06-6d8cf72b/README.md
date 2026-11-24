# Structured Paper Notes Skill (`product-arena-06-6d8cf72b`)

Offline Python 3.12 standard-library Skill for transforming scientific manuscripts (`.pdf`, `.md`, `.txt`, `.tex`, `.xml`/JATS, `.json`) into section-aligned, evidence-anchored structured literature notes.

## Package Layout
- `SKILL.md` — Agent execution contract and workflow.
- `references/`
  - `genre_and_argument_skeleton.md` — Genre taxonomy, PaperStory discovery chain, and One-Figure-One-Method lens.
  - `paper_card_field_dictionary.md` — Paper-card schema and three-layer evidence attribution rules.
  - `locator_and_granularity_playbook.md` — True PDF page/line, JATS `paragraph_id`, and Markdown `source-line` anchoring plus note-density controls.
- `scripts/`
  - `structured_notes_engine.py` — Main offline extraction, genre classification, section synthesis, discovery-chain, glossary, and knowledge-point engine.
  - `citation_anchors.py` — Exact-quote verifier, identifier extractor, and E/M/L/S/I claim fingerprinting.
  - `preflight_probe.py`, `budget_guard.py`, `result_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py` — Harbor runtime guardrails.
- `examples/`
  - `case.json` — Minimal illustrative Harbor case input (clearly marked as example).
  - `sample_paper.md` — Short illustrative research excerpt with headings, abbreviations, figure callouts, and limitations.
  - `sample_output_schema.json` — Illustrative output structure for `result.json` and `paper_card.json`.

## Quick Offline Run
```bash
python -B scripts/structured_notes_engine.py \
  --input examples \
  --output /tmp/notes_out \
  --work /tmp/notes_work \
  --max-kps 12
```

## Requirements & Boundaries
- Runtime: Python 3.12+ standard library only (zero third-party pip packages required). Optional PDF text layer extraction uses system `pdftotext -layout` or preinstalled `pypdf` when available; otherwise logs an explicit gap.
- Zero `.tsv` files, zero network calls, zero fabricated metrics or page numbers.
