---
name: reading-arena-literature-translation
description: >-
  Use for scientific literature translation between English and Chinese (Reading Arena 03)
  when terms, paragraph anchoring, bibliography identity, numbers, SI units, formulas,
  citations, negation polarity, and epistemic hedges must remain verifiable. Runs a
  two-stage prepare-then-audit pipeline over /input/case.json, PDF, or Markdown and writes
  /app/output/result.json and /app/output/translation_bilingual.md.
---

# Scientific Literature Translation Skill

Translate scientific papers between English and Chinese with paragraph-level verifiability,
locked terminology, protected source bibliography identity, and deterministic invariant
auditing. Never collapse a full-text or section translation request into a summary or loose
paraphrase.

Locate scripts relative to this `SKILL.md` directory (`scripts/...` under `/skills/weekly-skill`
or any mounted skill path). All bundled scripts use Python 3.12 standard library only and
require no network access.

## Hard Invariants (Apply on Every Run)

1. **Source Bibliography Identity Lock**: Treat original `item_id`, `source_title`,
   `authors`, `doi`, and `year` as immutable. Write translations to dedicated target fields
   (`translated_title`, `bilingual_segments[].target_zh`) without overwriting source metadata
   or mixing segments across distinct source items.
2. **Two-Stage Terminology Lock**: Extract candidate domain terms first, freeze canonical
   Chinese/English mappings in `/app/work/glossary.json`, and enforce those exact mappings
   across Title, Abstract, Headings, Body, Table Headers, and Figure Captions.
3. **1:1 Paragraph, Sentence-Density & Section Anchoring**: Preserve every immutable `paragraph_id` in original
   order with zero dropped, duplicated, merged, or reordered paragraphs, and maintain faithful sentence count
   and EN-word-to-ZH-char information density (blocking implicit half-sentence omission or summary collapse).
   For multi-page or multi-column PDFs, extract in reading order via `pdftotext -enc UTF-8` (without `-layout`,
   with optional `pypdf` fallback) to avoid cross-column bleed, record page extraction coverage, and carry
   the prior 2 sentences plus active glossary into each chunk so cross-page sentences join cleanly.
4. **Quantitative, Compound-Unit, Formula, Citation & Discourse-Logic Conservation**: Preserve all numbers
   including thousands-separated sample sizes (`n=1,010`, `n=5,539`), rate denominators (`55.69/10,000`),
   Chinese/English magnitude multipliers (`1.5万` = `15,000`, `2.3亿` = `230 million`), simple and compound
   SI units paired with their exact values (`5 mg`, `10 mA cm^-2`, `60 mV dec^-1`, `600.83 inhabitants/km2`),
   ranges (`±`, `95% CI`), LaTeX/Greek symbols, figure/table tags (`Fig. 2B`, `Table 1`), citation markers
   (`[1, 2]`, `\cite{}`), negation polarity (`not/never/rarely/without/failed`), discourse connectors
   (`However/Whereas/Although/While/Despite/Because/Therefore`), and modal hedges (`may/suggests/could`
   must never be upgraded to certainty words such as “证明/确定/必然”).
5. **Explanatory Fluency with Term Anchoring**: Unpack dense English clauses into natural
   academic Chinese, explicitly restoring omitted logical connectors (`While`, `However`,
   `Whereas` -> `尽管…但…`, `然而`, `相比之下`). Anchor key receptors, genes, algorithms,
   and metrics with original English in parentheses at first mention (for example,
   `β2-肾上腺素能受体 (β2-adrenergic receptor, β2-AR)`).
6. **Honest Uncertainty & Degraded Input Handling**: When a PDF page has no text layer,
   a figure/formula is rasterized, or a phrase is genuinely ambiguous, keep the `paragraph_id`
   anchor, mark `needs_human_review: true`, explain the exact reason in `uncertainty_or_gaps`,
   and never fabricate missing source text.

## Reference Routing

Read these sibling references on demand when the corresponding branch applies:
- Schema and exact JSON/Markdown fields: [references/output_contract_spec.md](references/output_contract_spec.md)
- Numeric, SI unit, formula, negation, modal hedge, and bibliography-lock rules: [references/terminology_and_invariants_protocol.md](references/terminology_and_invariants_protocol.md)
- Chunk continuation, term anchoring, and anti-translationese / de-AI rewrite rules: [references/bilingual_quality_and_deai_guide.md](references/bilingual_quality_and_deai_guide.md)

## Execution Workflow

Execute Steps 1 to 6 in order. Do not skip audit scripts or fabricate script outputs.

### Step 1. Preflight, Budget Init & Safe Skeleton (`M1` + `M2` + `M3`)

Run environment probing, initialize budget tracking, and prepare immutable source segments,
bibliography lock, and glossary candidates:

```bash
python3 scripts/preflight_probe.py --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 scripts/bilingual_alignment_guard.py --prepare \
  --input /input --work /app/work --output /app/output
```

**Completion criterion**: `/app/work/source_segments.json`, `/app/work/source_metadata_lock.json`,
`/app/work/glossary_candidates.json`, and initial `/app/output/result.json` exist and parse
as valid JSON. Inspect `page_coverage` in `source_segments.json` immediately; if any PDF
page yielded empty text, record it as an OCR/image-only gap rather than guessing content.

### Step 2. Freeze Domain Glossary (`/app/work/glossary.json`)

Inspect `/app/work/glossary_candidates.json` alongside `/input/task.md` and any user-supplied
glossary in `/input/case.json`. Produce `/app/work/glossary.json` with schema:

```json
{
  "terms": [
    {
      "source": "pharmacovigilance",
      "target": "药物警戒",
      "first_mention_anchor": "药物警戒 (pharmacovigilance)",
      "forbidden_variants": ["药品警惕", "药效监测"],
      "domain": "clinical pharmacology"
    }
  ]
}
```

Rules for locking terms:
- Prefer user/case-specified canonical translations whenever supplied.
- Lock every recurring acronym pair (`Full Name (ABBR)`), gene/protein/receptor symbol,
  chemical formula, statistical metric, and method name appearing >= 2 times or in Title/Abstract.
- Add common mis-translations to `forbidden_variants` so Step 4 catches silent drift.

**Completion criterion**: `/app/work/glossary.json` covers every domain term in Title, Abstract,
Captions, and recurring body terminology with unambiguous `source` -> `target` pairs.

### Step 3. Context-Carrying Sectioned Translation (`/app/work/translation_draft.json`)

Read `/app/work/source_segments.json` and `/app/work/source_metadata_lock.json`. Translate
every segment in order and write `/app/work/translation_draft.json`.

For long documents (> 12 paragraphs or multi-page PDFs):
- Translate in contiguous chunks of 6–10 paragraphs.
- Carry the frozen `/app/work/glossary.json` plus the last 2 translated sentences of the
  previous chunk as read-only context for cohesive pronouns and terminology.
- Keep `source_metadata` byte-identical to `/app/work/source_metadata_lock.json`; put the
  Chinese title in `translated_title` only.
- For each segment record:
  - `paragraph_id`: exact immutable ID from `source_segments.json`
  - `section`: original section label (`Title`, `Abstract`, `Methods`, `Results`, `Caption`, etc.)
  - `page`: source page integer if PDF, else `null`
  - `item_id`: source document identifier (prevents cross-item contamination)
  - `source_en`: verbatim source text (never overwritten)
  - `target_zh`: accurate, fluent academic Chinese translation
  - `terms_used`: list of locked terms applied in this segment
  - `translation_basis`: concise note linking key terminology choices, unpacked clause logic,
    or preserved formulas/units back to the source sentence
  - `needs_human_review`: `false` unless source text is degraded, formula/figure is missing,
    or genuine ambiguity remains (if `true`, add `review_reason`)

**Completion criterion**: Every `paragraph_id` in `source_segments.json` appears once in
`/app/work/translation_draft.json` in identical order, with non-empty `target_zh` (or explicit
untranslatable placeholder + `needs_human_review: true` when source is unreadable).

### Step 4. Deterministic Invariant, Bibliography & Terminology Audit

Run the full offline alignment and invariant guard against the draft, lock file, and glossary:

```bash
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --tick audit
python3 scripts/bilingual_alignment_guard.py --audit \
  --draft /app/work/translation_draft.json \
  --source-segments /app/work/source_segments.json \
  --metadata-lock /app/work/source_metadata_lock.json \
  --glossary /app/work/glossary.json \
  --out-json /app/work/alignment_audit.json \
  --emit-result /app/output/result.json \
  --emit-markdown /app/output/translation_bilingual.md \
  --export-glossary-csv /app/output/glossary.csv
```

The audit fails (exit code `1`) on any blocking violation:
- Bibliography identity overwrite (`source_title`, `authors`, `doi`, `item_id`) or cross-item leak
- Missing, duplicated, or reordered `paragraph_id`
- Implicit half-sentence omission or summary collapse (`sentence_density_drift`: abnormal sentence count or EN-word/ZH-char density drop)
- Dropped contrast/concession or causality/consequence discourse connectors (`discourse_logic_drift`)
- Terminology canonical mismatch or `forbidden_variants` hit in any paragraph
- Numeric mismatch, thousands-separated sample-size truncation (`n=1,010`), or broken Chinese/English magnitude conversion
- Simple or compound SI unit drift or value-to-unit swap (for example `5 mg` vs `5 μg`, `10 mA cm^-2` vs `10 A cm^-2`, `55.69/10,000`, `25 °C` vs `25 K`)
- Formula/LaTeX/Greek symbol, figure/table tag, or citation marker drift
- Negation polarity flip or epistemic hedge upgrade into certainty claims

If exit code is non-zero, read `/app/work/alignment_audit.json`, fix every flagged
`paragraph_id` in `/app/work/translation_draft.json`, and re-run Step 4 until `passed: true`
(or until only verified source-side defects remain, recorded honestly in `uncertainty_or_gaps`).

**Completion criterion**: `alignment_audit.json` reports zero avoidable blocking errors and
both `/app/output/result.json` and `/app/output/translation_bilingual.md` are emitted.

### Step 5. Anti-Translationese & De-AI Lexical Lint (Surgical Polish)

Run the glossary-aware lint gate on the emitted bilingual deliverable:

```bash
python3 scripts/academic_deai_lint.py \
  --input /app/work/translation_draft.json \
  --glossary /app/work/glossary.json \
  --out-json /app/work/deai_lint.json
```

Inspect `/app/work/deai_lint.json`. Rewrite only flagged Chinese translationese patterns
(such as `进行了…的评估`, `被…所证实`, `在…的背景下`, redundant passive shells) and AI
filler phrases (`综上所述`, `值得注意的是`, `显著赋能`). Preserve every locked term,
number, SI unit, citation marker, negation, and hedge untouched. After any edit, re-run
Step 4 to verify zero regression in invariants.

**Completion criterion**: High-severity AI clichés and translationese findings are cleared
without breaking Step 4 invariants.

### Step 6. Grounding Verification & Final Harbor Audit (`M5` + `M6`)

```bash
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require translation_bilingual.md
```

**Completion criterion**: `final_audit.py` prints `"passed": true`, `/app/output/result.json`
holds top-level `status` in `{"ok", "partial_with_gaps"}` plus complete `bilingual_segments`,
`terminology_table`, `source_metadata`, `invariants_check`, and `uncertainty_or_gaps`, and
`/app/output/translation_bilingual.md` is non-empty valid UTF-8.

## Failure & Edge-Case Handling

- **Empty or scanned PDF pages**: Keep page anchors, report `extracted_pages < total_pages`
  in `invariants_check.page_coverage`, set `needs_human_review: true` on affected blocks,
  and finalize with `status: "partial_with_gaps"` and `translation_status: "partial"`.
- **Formula or table rendered only as image**: Translate surrounding caption/prose faithfully,
  note `[Formula/Table requires visual verification from source page N]` in `review_reason`,
  and never invent equations or cell values.
- **Mixed-language or malformed `/input/case.json`**: Fallback to reading raw `.md`/`.txt`/`.pdf`
  under `/input`; if no readable source exists, emit an honest `input_error` report in
  `/app/output/result.json` rather than crashing with no output.
