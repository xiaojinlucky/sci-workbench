---
name: grounded-literature-review
description: Empirically clustered, passage-linked literature review generator that screens and deduplicates before writing, builds horizontal functional-operation matrices, separates full-text consensus from contested conflicts and abstract-only suggestive evidence, preserves citation legends with exact page/section locators, and audits final drafts for hedging inflation and overclaims.
---

# Literature Review Generation (`grounded-literature-review`)

A literature review is NOT chained abstract summaries. This Skill runs an auditable 5-stage synthesis pipeline over finite multi-source corpora (OpenAlex, Crossref, EuropePMC JSON/JATS XML, arXiv Atom XML, PDFs, BibTeX, CSV, and note-first Markdown archives) so every substantive sentence traces back to a verifiable passage and its epistemic strength matches its material access level.

## Core Methodology (Five Quality Pillars)

1. **Screen & Deduplicate BEFORE Writing (`screening_before_writing`)**
   - Enforce pre-declared inclusion/exclusion criteria and deduplicate preprint-to-VoR pairs *before* drafting prose.
   - Retain a PRISMA-conserved ledger (`n_identified = n_dedup + n_screened`, `n_screened = n_screen_excl + n_included`).
   - Strictly exclude `discovery_only` snippets and `blocked_or_unavailable` records from prose claims; keep them in the screening log so omissions are auditable.

2. **Empirical Data-Driven Theme Clustering (No Generic Boilerplate Headings)**
   - Never impose generic template sections ("Background / Methods / Challenges / Outlook") detached from the actual corpus.
   - Cluster included studies empirically by TF-IDF content similarity and domain bigrams (or explicit note `Theme:` tags), disclosing per-theme `fulltext_study_count` vs `abstract_only_study_count` and theme boundaries.

3. **Horizontal Functional-Operation Comparison (Compare What Papers Actually Did)**
   - Compare actual study objects, inputs, functional method operations, and baselines rather than method names alone (`method_comparison_matrix`).
   - Tag every method fact honestly: `explicitly_stated_in_fulltext`, `abstract_only_unverified_methods`, or `methods_not_reported_in_available_text`.
   - Never claim "field consensus" when fewer than two full-text studies converge or when opposing evidence exists.

4. **Three-Column Synthesis & Epistemic Hedging Alignment**
   Every substantive claim is matched to a 5-state grade (`SUPPORTED` / `PARTIAL` / `REFUTED` / `OUTDATED` / `UNKNOWN`) and an explicit hedging register:
   - `established_in_fulltext` — backed by inspected PDF/XML full text with `#page=N` or `#sec=...` locator.
   - `suggestive_abstract_only` — derived from `abstract_only` sources; **capped at `PARTIAL`** and phrased with cautious hedging ("abstract reports...", "suggestive evidence..."), never as conclusive full-text proof.
   - `contested_or_opposing` — opposing or mixed findings presented **side by side** with conditions/populations rather than smoothed over.
   - `prospective_protocol_only` — planned or proposed studies explicitly segregated from completed empirical trials.
   - `unverified_unknown` — missing PDF, unverified note quote, or inaccessible page; retained as `UNKNOWN`.
   - Cross-paper relation edges use strictly typed non-causal links (`observes`, `uses`, `supports`, `opposes`, `suggests`) — never upgrade cross-paper association to causation.

5. **Phased High-Fidelity Narrative & Citation Legend**
   - Anchor every draft with an explicit **Citation Legend** (`record_id` <-> year/date-basis <-> `material_access` <-> source file <-> title) so reviewers can trace any inline `[record_id]` immediately.
   - Maintain high information fidelity (>=70% of key empirical details and terms preserved with original technical terms anchored in context); avoid mechanical one-paper-per-bullet listing or empty AI boilerplate ("in summary", "notably").
   - Anchor every research gap to concrete corpus deficits (`fulltext_missing_for_theme`, `unresolved_directional_conflict`, `discovery_or_blocked_records_excluded`), never claiming global absence.

## Execution Workflow

All script paths below are relative to the directory containing this `SKILL.md` (resolve `scripts/...` against the installed skill root).

### Step 1: Preflight & Deterministic Evidence Matrix Generation
```bash
python3 scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 scripts/review_synthesis_builder.py \
  --input /input \
  --output /app/output \
  --work /app/work
```
Outputs `/app/output/review_evidence_matrix.json`, `/app/output/review_synthesis_draft.md`, and `/app/output/result.json`. The builder automatically invokes `block_indexer.py` for large local files, cleans HTML entities and PDF masthead titles, uses domain-general syntactic extraction for methods/objects across arbitrary scientific disciplines, and screens with word-boundary matching.

**Note-First Input Support**: If `/input/notes/` contains Markdown notes with `Record-ID:`, `Title:`, `DOI:`, `PDF: papers/file.pdf`, `Theme:`, and `## Claim: <key>` sections (`Claim:`, `Stance:`, `Locator: page N`, `Quote:`), the builder preserves exact note line spans, verifies the verbatim quote on the cited PDF page, and emits `cross_paper_synthesis[]` while keeping missing/unverified quotes strictly `UNKNOWN`.

### Step 2: Narrative Synthesis Refinement (Agent Reasoning Pass)
Inspect `/app/output/review_evidence_matrix.json` and elevate `/app/output/review_synthesis_draft.md` into coherent scholarly prose following `references/review_synthesis_protocol.md`:
- Keep the Citation Legend table intact at the top so every `[record_id]` is immediately resolvable.
- Weave included studies within each empirical theme into analytical narrative paragraphs comparing *what each study actually did* and *where conclusions diverge*.
- Strictly match prose certainty to each record's `hedging_register` and `material_access`.
- Keep Consensus, Contested Conflicts, Prospective Protocols, and Evidence-Anchored Gaps in clearly separated sections.

### Step 3: Read-Only Draft Grounding, Hedging & Final Deliverable Audit
```bash
python3 scripts/review_synthesis_builder.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --verify-draft /app/output/review_synthesis_draft.md \
  --out-json /app/work/draft_audit.json \
  --strict
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require review_synthesis_draft.md \
  --require review_evidence_matrix.json
```
`--verify-draft --strict` runs strictly read-only (never overwrites `result.json`, `review_evidence_matrix.json`, or `review_synthesis_draft.md`), validates **every** `[...]` citation token against included records (catching fabricated IDs such as `[FAKE-999]`, `[W99999]`, or `[Smith2025]`), and flags hedging inflation or global overclaims.

## Exception Handling & Hard Boundaries
- **Abstract-only corpus**: Clearly state that zero full-text PDF/XML sources were inspected and keep all findings hedged as `PARTIAL` / suggestive.
- **Unlinked or unparseable PDFs**: Retain claim rows as `UNKNOWN` with exact `pdf_locator_status`; never substitute abstract text for an unverified page quote.
- **Global completeness / unseen generalization**: Strictly prohibited. Scope every synthesis explicitly to the bounded finite snapshot.

## References & Examples
- `references/output_contract_spec.md` — Output files, required fields, and note-first verification semantics.
- `references/review_synthesis_protocol.md` — Phased high-fidelity synthesis rules, hedging table, and anti-boilerplate checks.
- `examples/minimal_example.md` — Illustrative interface example.
