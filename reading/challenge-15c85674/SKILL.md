---
name: literature-qa-grounded
description: >-
  Use for multi-domain scientific literature question answering (Reading 04 / challenge-15c85674)
  when every answer must cover all atomic sub-questions, cite exact paragraph_id and verbatim
  short quotes, preserve numeric/SI unit experimental conditions, audit four-stage document
  coverage (ingested vs extracted vs indexed vs cited), surface cross-section conflicts, and
  honestly abstain with insufficient_evidence when material is missing. Writes both
  /app/output/result.json and /app/answers.json.
---

# Grounded Scientific Literature Q&A Skill

Answer multi-domain scientific questions exclusively from supplied paper materials
(`/input/case.json` and any bundled PDF/text files) with exact `paragraph_id` anchors,
verbatim short quotes, preserved numerical and experimental conditions, observable
four-stage coverage auditing, and honest abstention whenever evidence is missing.

Locate all scripts relative to this `SKILL.md` directory so execution works identically
whether mounted under `/skills/literature-qa` or `/skills/weekly-skill`. Every bundled
script uses Python 3.12 standard library only (with reading-order `pdftotext -enc UTF-8`
without `-layout`, plus optional `pypdf` fallback for PDF inputs) and requires zero network
access. Designed for the current runtime budget of **64 model requests / 16384 output tokens
per response / 900 seconds** with dual-path atomic delivery to `/app/output/result.json`
and `/app/answers.json`.

## Hard Invariants (Apply on Every Run)

1. **Four-Stage Observable Coverage Audit (Anti-First-Page-Blindness)**:
   Never equate "file ingested" with "full text covered". Track four distinct metrics:
   `ingested_pages` (declared pages/sections), `extracted_pages` (pages yielding real text,
   listing `empty_or_scanned_pages`), `indexed_paragraphs` (with explicit
   `tail_page_coverage_check` and `table_footnote_coverage_check`), and `cited_paragraphs`.
   When pages lack a text layer or sections are omitted, record them in
   `coverage.unread_sections` / `coverage.unread_pages` and `unanswered_parts[]`; never
   extrapolate answers from the title or abstract.
2. **Closed-World Evidence Grounding**:
   Answer solely from supplied paragraphs, tables, captions, footnotes, and references.
   Every `kind: "reported"` claim must carry >= 1 `evidence` item whose `paragraph_id`
   exists in the input and whose `quote` is a verbatim substring of **that exact paragraph**
   (light whitespace normalization only; cross-paragraph quote borrowing is rejected).
3. **Atomic Sub-Question Exhaustiveness**:
   Decompose every compound Chinese or English question into atomic sub-questions (target
   fact, mechanism/method, experimental conditions, comparator & quantitative delta, sample
   size/statistics, limitations). Address each sub-question explicitly; if even one sub-question
   lacks evidence, set `status: "partial"` and list the gap in `unanswered_parts[]`.
4. **Epistemic Tagging & Conflict Surfacing**:
   Separate four evidence states cleanly:
   - `reported`: directly stated or measured in the supplied text/table/footnote.
   - `inferred`: transparent logical synthesis across multiple supplied paragraphs (explain
     the reasoning chain and cite every supporting paragraph).
   - `conflict`: internal disagreement across Abstract, Body, Tables, or Discussion (cite
     **both** conflicting `paragraph_id` anchors and quotes without silently voting one away).
   - `insufficient_evidence`: requested fact, condition, or comparison is absent or resides
     on an unreadable/unprovided section.
5. **Condition & Quantitative Conservation**:
   Preserve exact numbers, SI units (`mg` vs `μg`, `mV` vs `V`, `°C` vs `K`), comparators,
   sample sizes, p-values, and validity bounds in `conditions[]`. Every numeric-unit pair
   listed in `conditions[]` must match the cited source paragraphs.
6. **Dual-Path Deliverable Parity**:
   Write the validated answer schema to `/app/output/result.json` (preserving top-level
   Harbor `status` in `{"ok", "partial_with_gaps"}` plus all original fields `case_id`,
   `paper_id`, `material_version`, `answers[]`, `coverage`) AND mirror the clean answer
   payload to `/app/answers.json` and `/app/output/answers.json`.

## Reference Routing

Read sibling references on demand when the corresponding condition arises:
- Exact JSON schema, dual-path mapping, and validation exit rules: [references/output_contract_spec.md](references/output_contract_spec.md)
- Four-stage coverage audit, tail-page / table-footnote traps, and PDF fallback protocol: [references/qa_evidence_and_coverage_protocol.md](references/qa_evidence_and_coverage_protocol.md)
- Atomic sub-question decomposition, epistemic tagging, and cross-section conflict rules: [references/subquestion_and_conflict_rules.md](references/subquestion_and_conflict_rules.md)

## Execution Workflow

Execute Steps 1 to 5 in order. Do not bypass `--validate-answers` or edit `/app/output/result.json`
without running verification.

### Step 1. Preflight, Corpus Indexing, Sub-Question Split & Coverage Audit (`M1` + `M3` + `M4`)

Run the preflight probe, initialize budget tracking, and execute the indexing & retrieval
pass:

```bash
python3 scripts/preflight_probe.py --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 scripts/literature_qa_engine.py \
  --input /input --output /app/output --work /app/work
```

What this step deterministically generates:
- `/app/work/qa_retrieval_pack.json`:
  - Full `coverage_audit` (four-stage page/section counts, `empty_or_scanned_pages`,
    `tail_page_coverage_check`, `table_footnote_coverage_check`).
  - Per-question `sub_questions[]` from the bilingual compound-question splitter.
  - Per-question and per-subquestion top-ranked paragraphs via Okapi BM25 + numeric/SI unit
    + entity + tail-page/table-footnote boosting.
  - `conflict_candidates[]`: automatically flagged paragraph pairs where Abstract, Body,
    or Tables share key entities/metrics but diverge in numerical values or negation polarity.
- Initial valid skeleton in `/app/output/result.json` and `/app/answers.json` so budget or
  time limits never produce an empty submission.

**Completion criterion**: `/app/work/qa_retrieval_pack.json` exists and its `coverage_audit`
has been inspected for missing sections, scanned/empty pages, tail-page entries, and tables/footnotes.

### Step 2. Deep Evidence Reading & Conflict Inspection

Read `/app/work/qa_retrieval_pack.json` and inspect the full relevant paragraphs, table rows,
captions, and footnotes in `/input/case.json` (or `/app/work/indexed_corpus.json` for PDF runs).

For each `question_id`:
1. Check every item in `sub_questions[]` individually—never stop after answering the first clause.
2. Explicitly inspect `tail_page_hits` and `table_footnote_hits` whenever the question asks
   for edge conditions, exclusions, dosing thresholds, footnotes, or limitations.
3. Inspect `conflict_candidates[]`: if two sections report conflicting numbers or opposing
   effects (for example Abstract vs Table 2 or Methods vs Results), verify both original
   paragraphs directly and prepare two `reported` claims citing each `paragraph_id`.
4. Check `coverage_audit.unread_sections` and `coverage_audit.empty_or_scanned_pages`: if a
   sub-question targets an absent section or unreadable page, plan an honest `partial` or
   `insufficient_evidence` response naming the exact missing section/page.

**Completion criterion**: Every atomic sub-question of every `question_id` is mapped either
to verified verbatim paragraph evidence or to an explicit evidence gap.

### Step 3. Synthesize Grounded Draft (`/app/work/answers_draft.json`)

Write `/app/work/answers_draft.json` matching the official schema:
- Top-level: `case_id`, `paper_id`, `material_version`, `answers[]`, `coverage`.
- Each item in `answers[]`:
  - `question_id`: exact ID from input.
  - `answer`: clear, concise Chinese answer covering every resolved sub-question, preserving
    exact numbers, SI units, comparators, and noting any internal conflict or evidence gap.
  - `conditions`: list of concrete experimental/scope conditions (`"temperature = 25 °C"`,
    `"n = 120 patients"`, `"pH 7.4"`, `"excluding patients under 18 years"`) strictly grounded
    in the cited paragraphs.
  - `status`: strictly one of:
    - `"answered"`: every sub-question resolved with evidence (`unanswered_parts` must be `[]`).
    - `"partial"`: at least one sub-question resolved and at least one gap recorded in `unanswered_parts[]`.
    - `"insufficient_evidence"`: supplied material does not report the answer (`unanswered_parts[]` non-empty).
    - `"input_error"`: unreadable, empty, or corrupted source material.
  - `claims`: array of `{claim_id, text, kind, evidence[]}` where `kind` is `"reported"` or
    `"inferred"`; every `"reported"` claim has >= 1 `{paragraph_id, quote}` with `quote`
    copied verbatim from that exact `paragraph_id`.
  - `unanswered_parts`: list of `{"part": "...", "reason": "..."}` (empty `[]` iff `status == "answered"`).
- `coverage`: include `read_sections`, `unread_sections`, `unread_reason`,
  `total_paragraphs_read`, and preserve `page_coverage_audit` from Step 1.

**Completion criterion**: `/app/work/answers_draft.json` covers 100% of input `question_id`s
with zero fabricated quotes or unsupported condition numbers.

### Step 4. Zero-Fabrication Validation & Atomic Promotion

Validate the draft against the indexed corpus before promoting to final deliverables:

```bash
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --tick validate
python3 scripts/literature_qa_engine.py \
  --input /input --output /app/output --work /app/work \
  --validate-answers /app/work/answers_draft.json
```

The validator enforces **10 blocking gates** (7 structural/evidence + 3 semantic gates; exit code `1` on any failure):
1. `case_id`, `paper_id`, and `material_version` match input exactly.
2. 100% of input `question_id`s appear once with zero missing or duplicate questions.
3. Every `paragraph_id` exists in the corpus and every `quote` is a verbatim substring of
   **that exact paragraph** (cross-paragraph borrowing fails).
4. Every `kind == "reported"` claim carries >= 1 verified verbatim `evidence` quote.
5. `status == "answered"` has `unanswered_parts == []`; `partial` and `insufficient_evidence`
   have non-empty `unanswered_parts[]`.
6. Every numeric+SI unit pair in `conditions[]` is verified against the cited evidence
   paragraphs for that question (blocks unit drift such as `mg -> μg` or invented numbers).
7. `coverage` faithfully records unread sections and empty/scanned PDF pages.
8. **Semantic Reported Numeric-Core Fidelity**: Every quantitative value (including
   thousands-separated sample sizes such as `n=1,010`/`n=5,539`, percentages, and rates)
   stated in a `kind == "reported"` claim must actually appear in its verified `quote`(s),
   blocking number tampering or off-topic quotes.
9. **Inferred Reasoning Premise & Anti-Disguise Gate**: Every `kind == "inferred"` claim
   must supply an explicit reasoning premise (`reasoning_premise` field or synthesis cues)
   and grounded supporting evidence, and inferred calculations must never be disguised as
   `kind == "reported"`.
10. **Atomic Sub-Question Coverage Gate**: Every automatically decomposed atomic sub-question
    must have an explicit landing in `answer`/`claims`/`conditions` (when `answered`) or in
    `unanswered_parts[]` (when `partial`/`insufficient_evidence`), preventing silent omission
    of sub-questions under `status: "answered"`.

If exit code is `1`, inspect `schema_errors` printed by the CLI, repair `/app/work/answers_draft.json`,
and re-run Step 4 until `ok: true` and exit code `0`.

**Completion criterion**: Validator exits `0` and atomically writes `/app/output/result.json`,
`/app/output/answers.json`, and `/app/answers.json`.

### Step 5. Grounding Check & Final Harbor Audit (`M5` + `M6`)

```bash
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require answers.json
```

**Completion criterion**: `final_audit.py` returns `"passed": true`, both `/app/output/result.json`
and `/app/answers.json` are valid UTF-8 JSON with identical `answers[]` and `coverage`, and
top-level `result.json` has `status` in `{"ok", "partial_with_gaps"}`.

## Edge-Case & Failure Protocol

- **Missing or unreadable `/input/case.json`**: Report `status: "input_error"` per question
  (or top-level `partial_with_gaps` with `EMPTY_PARAGRAPH_CORPUS`) and never fabricate text.
- **Unanswered question is valid**: An evidence-backed `insufficient_evidence` answer citing
  what sections were checked and what is absent scores higher than a hallucinated `answered`.
- **Internal paper contradiction**: Keep both conflicting quotes in `claims[]`, state the
  discrepancy explicitly in `answer`, and explain any unresolved ambiguity in `unanswered_parts[]`.
