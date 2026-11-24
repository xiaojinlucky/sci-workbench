---
name: bilingual-academic-translation
description: Scholarly EN-ZH translation and bilingual parallel writing workflow. Trigger when translating or aligning scientific manuscripts, abstracts, tables, or references between English and Chinese while enforcing exact magnitude/unit/order parity (with attested clause reordering), live citation and bilingual bibliography 1:1 integrity, negation and modal-hedge conservation, locked terminology, anti-translationese academic prose, and a complete 5-field Harbor result.json.
---

# Bilingual Academic Translation and Parallel Writing

Translate or align scholarly manuscripts between English and Chinese with zero drift across numbers, magnitude units, SI units, live citations, bilingual reference entries, equations, negation polarity, modal hedges, and locked terminology.

## Scope and Hard Invariants

1. **Zero numeric, magnitude, unit, or silent-order drift**: Preserve every numerical value, precision, percentage, statistical test (`p < 0.001`, `95% CI [1.12, 1.85]`, `n = 128`), scientific notation (`1.5e-4` ↔ `1.5×10^-4`), range, and SI unit. Convert EN↔ZH magnitudes accurately (`1.5万` = `15,000` / `15 thousand`, `2.3亿` = `230 million`, `1.2千` = `1,200`) without rounding. Preserve numeric appearance order within each paragraph; when natural EN↔ZH syntax genuinely requires reordering clauses, attest it explicitly with a `reordered_clause_note` (never silently swap group/condition numbers).
2. **Live citation, display, equation, and bilingual bibliography 1:1 integrity**: Preserve every in-text citation key (`[1]`, `[1-3]`, `\cite{k}`, `(Author et al., 2024)`), display identifier (`Fig. 1` ↔ `图 1`, `Table 2` ↔ `表 2`, `Eq. (3)` ↔ `式 (3)`), and LaTeX math block (`$...$`, `$$...$$`). When translating references between Chinese and English, synchronize BOTH in-text citations and the reference list so every in-text key resolves cleanly to its exact reference entry with matching DOI, year, author, and title (preventing orphaned Chinese in-text citations or cross-entry title swaps).
3. **Negation scope and modal-hedge conservation**: Preserve negation polarity and count (`not`/`never`/`without` ↔ `未`/`不`/`并非`/`无`) and epistemic limiters (`may`/`might`/`suggests`/`associated with`/`only` ↔ `可能`/`提示`/`相关`/`仅`). Never escalate correlational or preliminary findings into definitive causal claims (`proves`/`causes`/`证明了`/`直接导致`).
4. **Locked terminology, anti-defensive scholarly prose, and pre-mortem check**: Lock canonical terms and forbidden variants in `/app/work/glossary.json` across Abstract, Body, Captions, Tables, and Appendices. Anchor discipline-specific abbreviations on first use (e.g. `β2-adrenergic receptor (β2-AR)`). Eliminate translationese, unprompted defensive clutter, and AI boilerplate without altering scientific terms, numbers, or claim strength; complete a pre-mortem Blocking-vs-Optional check before sealing.
5. **Complete 5-field output contract**: Persist `/app/output/result.json` preserving `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, and `change_log`, alongside honest `status`, `sources`, `uncertainty`/`gaps`, and `artifacts`. Read actual files under `/input/` and never fabricate content from topic labels.

## Reference Pointers (Load on Demand)

- Magnitude conversion rules, statistical/unit normalization, `reordered_clause_note` syntax, negation/hedge calibration, and bilingual reference mapping: read `references/bilingual-fidelity-rules.md`.
- Academic Chinese & English anti-translationese and natural scholarly prose rules: read `references/academic-chinese-style.md`.
- Exact 5-field `result.json` schema and pre-seal QA checklist: read `references/output-contract-and-qa.md`.

## Execution Workflow

### Step 1: Probe Environment, Read Actual Source Files, and Initialize Guards
Run M1-M4 probes first and inspect actual source documents under `/input/`:
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "bilingual-academic-translation"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
**Completion criterion**: `/app/work/env.json`, `/app/work/input_blocks.json`, and skeleton `/app/output/result.json` exist; actual source document path, target language/format requirement, and `case_id` are verified from `/input/case.json` and `/input/task.md`.

### Step 2: Build Locked Glossary and Reference Mapping Ledger
1. Read `references/bilingual-fidelity-rules.md` and `references/academic-chinese-style.md`.
2. Extract domain terms, first-use abbreviations, and prohibited synonym drift into `/app/work/glossary.json`:
   ```json
   {
     "terms": [
       {"source": "single-cell RNA sequencing", "target": "单细胞RNA测序", "forbidden_variants": ["单细胞核糖核酸序列测定"]}
     ],
     "reordered_clause_notes": []
   }
   ```
3. If the source contains a bilingual reference list or explicit reference metadata, extract a 1:1 reference ledger `/app/work/reference_map.json` linking each citation key to its exact `doi`, `year`, `source_author`, `target_author`, `source_title`, and `target_title` so zero entries swap or detach during translation.

**Completion criterion**: `/app/work/glossary.json` locks every recurring domain term; `/app/work/reference_map.json` is prepared whenever references require cross-script conversion.

### Step 3: Execute Positive Bilingual Academic Translation & Editable Alignment
1. Translate paragraph-by-paragraph into `/app/output/translated.md`, maintaining exact paragraph count, sentence coverage (zero dropped clauses), and structural parity (headings, tables, captions, footnotes, equations, reference list).
2. Apply explicit **EN→ZH scholarly restructuring rules** (never word-for-word calques):
   - **Unpack stacked modifiers**: Break long English post-modifying relative/participial clauses (`which ...`, `characterized by ...`, `observed in ...`) into chronological or cause-effect Chinese sub-clauses with explicit subjects rather than stacking 3+ `的` modifiers.
   - **Surface implicit logic**: Make English implicit transitions (`While`, `Whereas`, `Given`, `Despite`) explicit (`尽管…但…`, `相比之下`, `鉴于`) without escalating correlation into causation.
   - **Replace passive/nominalized translationese with direct domain verbs**: Convert `performed an evaluation of` / `was observed to exhibit a significant reduction` into `评估了` / `显著下降`; eliminate `扮演……角色`, `以一种……方式`, `值得注意的是`, `不是……而是……`, and excessive `——`.
   - **Anchor technical terms & symbols**: On first mention, anchor discipline terms with canonical English/abbreviations (`β2-肾上腺素能受体（β2-AR）`), keeping gene symbols (`TP53`), chemical formulas, and software names untranslated.
3. Apply explicit **ZH→EN scholarly register rules**:
   - **Convert parataxis to hypotaxis**: Restructure Chinese topic-comment comma chains into clear English main clauses plus subordinate condition/scope clauses.
   - **Enforce IMRaD tense & modality discipline**: Use past tense for specific Methods and Results, present tense for established mechanisms and `Fig.`/`Table` pointers; match exact epistemic strength (`associated with`, `suggests`, `may reflect` — never escalating `提示`/`相关` into `proves`/`causes`).
4. **Tables, captions, and inline math discipline**:
   - Translate table headers, row labels, and footnotes while keeping numeric values, units, SD/95% CI precision, and column alignment byte-accurate.
   - Keep `Fig. X` ↔ `图 X`, `Table X` ↔ `表 X`, `Eq. (X)` ↔ `式 (X)` consistent across prose and captions.
   - Preserve `$...$` / `$$...$$` LaTeX math verbatim, translating only surrounding prose or explicit `\text{...}` annotations.
5. Whenever natural EN↔ZH syntax legitimately changes the order of two numeric mentions inside a paragraph while keeping every group/condition value intact, record an entry in `/app/work/glossary.json` under `"reordered_clause_notes"` (with `paragraph`, `reason`, and verified value-to-entity mapping) or attach `<!-- reordered_clause_note: ... -->` on that paragraph.
6. Synchronize all in-text citations with the target reference list script and format; keep every DOI, year, equation, and figure/table identifier intact.

**Completion criterion**: `/app/output/translated.md` covers 100% of source paragraphs, sentences, tables, captions, equations, and references in idiomatic scholarly prose with zero semantic or numeric drift.

### Step 4: Run Deterministic Bilingual Alignment, Citation, and Style Guard
Execute `bilingual_alignment_guard.py` to audit numeric/magnitude/unit/order parity, live citations, display numbering, math blocks, bilingual bibliography 1:1 integrity, negation/hedge conservation, glossary lock, and translationese warnings, while exporting `/app/output/bilingual_aligned.md` and `/app/work/contract_patch.json`:
```bash
python3 /skills/weekly-skill/scripts/bilingual_alignment_guard.py \
  --source /input/<SOURCE_FILE> \
  --target /app/output/translated.md \
  --glossary /app/work/glossary.json \
  --case-id "<CASE_ID>" \
  --out-json /app/output/translation_audit.json \
  --out-aligned /app/output/bilingual_aligned.md \
  --out-contract-patch /app/work/contract_patch.json
```
Add `--references /app/work/reference_map.json` whenever bilingual reference metadata is provided. Inspect every hard error and style warning; resolve all blocking errors (`numeric_or_unit_drift`, `numeric_order_swap_unattested`, `citation_drift`, `bilingual_citation_script_mismatch`, `reference_doi_or_year_swap`, `reference_title_swap`, `display_numbering_drift`, `equation_drift`, `negation_polarity_shift`, `causal_overclaim_escalation`, `glossary_conflict`) and re-run until `passed` is `true` (exit code `0`).

**Completion criterion**: `bilingual_alignment_guard.py` exits `0` with `"passed": true`, zero hard errors, and fresh `/app/output/translation_audit.json`, `/app/output/bilingual_aligned.md`, and `/app/work/contract_patch.json`.

### Step 5: Seal 5-Field `result.json` and Run Grounding + Final Audits
1. Read `references/output-contract-and-qa.md`. Merge `/app/work/contract_patch.json` with `deliverable`, `sources`, `uncertainty`, and `artifacts` into `/app/output/result.json` via `result_guard.py --finalize`.
2. Run M5 grounding verification and M6 final audit:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output \
  --require translated.md \
  --require bilingual_aligned.md \
  --require translation_audit.json
```
**Completion criterion**: `result_guard.py --validate`, `gap_gate.py`, and `final_audit.py` all exit `0`, and `/app/output/result.json` contains all 5 `output_contract` fields with honest status.

## Exception and Edge-Case Handling

- **Legitimate EN↔ZH clause reordering**: Never alter accurate syntax back into awkward translationese just to match numeric order; verify that every condition/group owns the exact same number, then add an explicit `reordered_clause_note` so the guard records `"verified_reordered"`.
- **Ambiguous or missing English metadata for Chinese references**: Preserve the original Chinese reference record alongside the transliterated/translated metadata and log `UNVERIFIED_EXTERNAL_REF_METADATA` in `gaps` rather than guessing an official English title or DOI.
- **Conflicting terms across sections or polysemous discipline terms**: Document the chosen canonical term and rationale in `glossary.json` and `change_log`; flag genuine source-side contradictions in `uncertainty` and `gaps` instead of silently overwriting source numbers.
