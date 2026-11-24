---
name: structured-paper-notes
description: Build section-aligned literature notes, genre-aware argumentation skeletons, PaperStory discovery chains, deduplicated terminology glossaries, and three-layer attributed paper cards with verbatim source-verified locators and explicit coverage gaps.
---

# Literature Summary & Structured Notes (`product-arena-06-6d8cf72b`)

## Purpose & Trigger
Use this skill for scientific paper summarization and structured note-taking in Harbor offline sandboxes. It converts supplied scientific sources (`.pdf`, `.md`, `.txt`, `.tex`, `.xml`/JATS, `.json`) into reusable, section-aligned, evidence-anchored research notes rather than generic prose summaries.

Core objectives:
1. **Genre & Argument Alignment**: Detect article genre (`research_article`, `review`, `perspective`, `benchmark_framework`, `comment`) and follow the paper's actual section sequence and inter-section transitions without forcing rigid templates.
2. **Discovery Chain & One-Figure-One-Method Lens**: Extract the end-to-end discovery chain (`Problem/Gap -> Method/Operation -> Key Results & Figure Evidence -> Limitations/Prerequisites`) plus per-figure method rationale, control/exclusion scope, and evidence boundary.
3. **Controlled-Granularity Notes & Three-Layer Attribution**: Emit section-level synthesis cards, a deduplicated terminology/abbreviation glossary, and evidence-density-ranked knowledge points capped for usability. Label every structured claim strictly as `author_reported`, `model_synthesized`, or `not_reported`.
4. **Authentic Locator Fidelity**: Record real `page` + `extracted_line` for PDFs (and flag blank/scanned pages needing OCR), `paragraph_id` for JATS XML / structured JSON, and `source-line` for Markdown/TXT/TeX. Never fabricate PDF page numbers for non-PDF inputs.

## Hard Invariants & Guardrails
- **Never fabricate missing fields or sections**: Unsupported card fields (e.g., missing sample size, controls, or limitations) stay `attribution: "not_reported"` and are logged in `gaps`.
- **Strict verbatim quote verification**: Every emitted knowledge point quote must pass normalized substring matching inside its exact cited paragraph/block. Cross-paragraph borrowing is forbidden.
- **Zero `.tsv` artifacts**: Never generate or bundle `.tsv` files inside the Skill or output deliverables.
- **Honest modality & OCR boundaries**: When a PDF contains blank text layers or extraction fails without OCR, preserve partial output and emit `PDF_PAGES_NEED_OCR` / `PDF_TEXT_EXTRACTION_FAILED`.
- **Read `task.md` & actual sources first**: Never infer paper facts from filenames or domain labels.

## Execution Workflow
Locate all scripts relative to this `SKILL.md` directory (`scripts/`).

### Step 1: Environment Preflight & Budget Init
```bash
python3 -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B scripts/budget_guard.py --init --state /app/work/.budget_state.json
python3 -B scripts/block_indexer.py --scan /input --out /app/work/blocks.json
```

### Step 2: Run Structured Notes Engine
```bash
python3 -B scripts/structured_notes_engine.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --max-kps 12
```
Emits four synchronized deliverables under `/app/output/`:
- `result.json` — Harbor output contract with `case_id`, `status`, `sources`, `uncertainty`, `artifacts`, `metrics`, `findings`, `structured_notes`, and `gaps`.
- `paper_card.json` — Standalone reusable structured paper card with three-layer attribution (`author_reported` / `model_synthesized` / `not_reported`) and verified anchors.
- `structured_notes.json` — Full machine-indexable graph: genre classification, ordered section cards with transitions, discovery chain, figure/method audit cards, terminology glossary, and density-ranked knowledge points.
- `structured_notes.md` — Human-readable structured research notes ready for downstream reading or comparative synthesis.

### Step 3: Host Agent Deepening & Verification
Inspect `/app/output/structured_notes.json` and `task.md`:
1. Confirm genre classification and section boundaries match the actual paper (see `references/genre_and_argument_skeleton.md`).
2. **Research Article 10-Point Deconstruction / Review Phased Synthesis**:
   - For research articles, verify coverage across bibliographic metadata, faithful abstract, Introduction gap chain, Results mechanism/evidence chain, Discussion agreement/contradiction/limitations, target-object-to-phenotype anchoring, methods/controls, multi-layer data & filtering thresholds, hypothesis triangulation, and code/data availability (`not_reported` if absent).
   - For long reviews or complex papers, synthesize in three fidelity stages (Metadata+Abstract -> Context/Scope/Logic Map -> Section-by-Section Deep Dive preserving ≥70% core technical content with terminology anchors) and explicitly separate **domain consensus**, **author-integrated interpretation**, and **unresolved controversy/conflicting observations**.
3. Verify that figure captions containing interpretive claims (e.g., *"indicating that..."*) are flagged as interpretation rather than raw observation, and keep frontend scientific phrasing free of internal processing tags.
4. Confirm every `author_reported` field in `paper_card.json` carries a valid verbatim anchor, and keep genuinely missing items `not_reported` (see `references/paper_card_field_dictionary.md`).
5. Check locator granularity so notes remain scannable without Zotero-style over-fragmentation (see `references/locator_and_granularity_playbook.md`).

### Step 4: Grounding Gate & Final Deliverable Audit
```bash
python3 -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B scripts/final_audit.py --dir /app/output --result-file result.json \
  --require structured_notes.md --require structured_notes.json --require paper_card.json
```

## References
- `references/genre_and_argument_skeleton.md` — Genre identification cues, original-order argumentation skeleton, and discovery-chain + One-Figure-One-Method checklist.
- `references/paper_card_field_dictionary.md` — Canonical paper-card field definitions and three-layer attribution (`author_reported`, `model_synthesized`, `not_reported`).
- `references/locator_and_granularity_playbook.md` — PDF/JATS/Markdown locator standards, OCR fallback policy, and anti-fragmentation annotation limits.
