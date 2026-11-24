---
name: manuscript-evidence-workbench
description: Draft, structure, verify, and export evidence-grounded academic manuscripts from research notes, data tables, figures, and bibliography files. Triggers when producing or revising full papers or core sections with strict numeric/unit/citation provenance, Results-vs-Discussion layering, bilingual academic de-AI cleanup, and editable Markdown/DOCX delivery.
---

# Academic Manuscript Workbench (Evidence-Grounded & Editable)

Produce submission-ready, fully editable academic manuscripts from `/input/case.json`, `/input/task.md`, and supplied source files while keeping every number, unit, sample size, statistical metric, figure reference, and citation traceable to `/input`.

## Read References on Demand

- Always follow core fact-preservation, epistemic boundaries, press-release narrative discipline, and bilingual de-AI standards in `references/academic_writing_standards.md`.
- Consult `references/manuscript_structure_and_alignment.md` before choosing separate vs. combined Results/Discussion layout, three-way alignment checks, and declaration fields.
- Consult `references/output_contract_spec.md` before sealing `/app/output/result.json`.

## Hard Execution Rules

1. **Read raw materials first**: Inspect actual files under `/input` via `block_indexer.py` and `manuscript_workbench_engine.py index` before drafting; never infer findings from filenames or category labels.
2. **Preserve all empirical invariants**: Keep numbers, units, sample sizes (`n`), error-bar types (`SD`/`SEM`/`IQR`/`95% CI`), exact `p`/`FDR` values, figure/table numbers, and citation keys identical to `/input`.
3. **Separate Results from Discussion**: Follow the target journal's structure specification if supplied. If `journal_structure_spec` is `UNSPECIFIED`, default to separate `# Results` (observations only, zero mechanistic speculation) and `# Discussion` (synthesis, literature comparison, explicitly hedged hypotheses, and concrete limitations without verbatim repetition of all Results numbers) while flagging the unspecified journal layout for author confirmation.
4. **Never fabricate missing details**: When source files omit statistical tests, captions, ethics approvals, DOIs, or journal formatting rules, insert `[EVIDENCE_GAP: <exact missing item>]` and log it in `gaps` rather than guessing.
5. **Apply press-release narrative & de-AI discipline**: Open Abstract and Introduction with the four-beat arc (`Problem -> Gap -> Approach -> Key Finding`); bind every table/figure to an explicit argumentative role; remove empty clichés, corporate jargon, translationese, and self-undermining defensive filler from primary result statements while retaining honest scientific limitations.
6. **Budget & early checkpoint**: Initialize `/app/output/result.json` at Step 1 via `result_guard.py --init`, track steps via `budget_guard.py`, and keep every required `output_contract` field (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`).

## Step-by-Step Workflow

### Step 1: Probe Environment & Index Raw Materials
Run preflight probes, initialize checkpoints, and index every numeric token, DOI, bibliography entry, and figure caption under `/input`:

```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "academic-manuscript-writing"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
python3 /skills/weekly-skill/scripts/manuscript_workbench_engine.py index \
  --input-dir /input \
  --out-json /app/work/material_index.json
```

**Completion criterion**: `/app/work/env.json`, `/app/work/input_blocks.json`, and `/app/work/material_index.json` exist; you have read `/input/task.md`, `/input/case.json`, and `material_index.json` to know the exact required sections, target journal structure (`SEPARATE` / `COMBINED` / `UNSPECIFIED`), available tables/figures, and bibliography entries.

### Step 2: Generate Zero-Hallucination Scaffold & Draft Editable Manuscript
1. Generate a grounded IMRaD scaffold with verbatim transcribed Markdown tables, caption anchors, and formatted references (works with either `/skills/weekly-skill/scripts/` or relative `scripts/`):
```bash
python3 /skills/weekly-skill/scripts/manuscript_workbench_engine.py scaffold \
  --input-dir /input \
  --out-md /app/work/manuscript_scaffold.md
```
2. Resolve any cross-file numeric or unit discrepancies explicitly: cite both values and mark `[EVIDENCE_GAP: resolve discrepancy between <fileA> and <fileB>]` instead of silently picking one.
3. Expand `/app/work/manuscript_scaffold.md` into the complete editable manuscript at `/app/output/manuscript.md`:
   - Abstract & Introduction follow `Problem -> Gap -> Approach -> Key Finding` with zero hollow openers.
   - Methods report only recorded procedures, parameters, sample sizes, and software/instrument versions; missing items carry `[EVIDENCE_GAP: ...]`.
   - Results present observed group comparisons, directions, magnitudes, exact statistics, and negative/non-significant findings anchored to figure/table IDs, with zero mechanistic speculation.
   - Discussion synthesizes implications, compares agreement/disagreement with cited references (separating literature existence from claim support), marks mechanistic hypotheses explicitly, and lists concrete methodological/sample/statistical limitations.
   - Include `References`, `Data and Code Availability`, and `Competing Interests / Ethics` (explicitly marked if absent from `/input`).

**Completion criterion**: `/app/output/manuscript.md` contains a complete, directly editable manuscript covering all task-requested sections with zero placeholder outlines.

### Step 3: Run Deterministic 8-Gate Manuscript Audit & Repair Loop
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --tick audit
python3 /skills/weekly-skill/scripts/manuscript_workbench_engine.py audit \
  --input-dir /input \
  --draft /app/output/manuscript.md \
  --out-json /app/work/manuscript_audit.json
```

Inspect `/app/work/manuscript_audit.json` and resolve every blocking finding before export:
- `ungrounded_numbers` / `ungrounded_dois` / `CONDITION_BINDING_MISMATCH`: trace every value to `/input` and verify condition-row binding, or replace unsupported claims with `[EVIDENCE_GAP: ...]`.
- `dangling_citations` / `uncited_references` / `CLAIM_SUPPORT_LOW`: synchronize in-text `[n]` citations with `References` 1:1 AND verify topical claim-to-reference support (never attach unrelated input bibliography records solely because their DOI exists).
- `MECHANISM_INTERPRETATION_IN_RESULTS` / `DISCUSSION_VERBATIM_NUMERIC_REPETITION`: move mechanistic claims to Discussion and replace repeated Result dumps with synthesis.
- `abstract_body_mismatch_numbers` / `image_only_without_caption`: reconcile Abstract metrics with Body Results and explicitly flag image-only figures lacking caption text.
- `prose_deai_audit.high_count > 0`: eliminate flagged jargon, translationese, and contrarian clichés without altering numbers or hedges.

Re-run `audit` until `blocking_issue_count == 0` (or every remaining gap is genuinely absent from `/input` and explicitly tagged).

**Completion criterion**: `manuscript_audit.json` confirms `blocking_issue_count == 0` (or all residual items are honest input gaps).

### Step 4: Export Editable DOCX, Optional Visual Preview & Seal `result.json`
```bash
python3 /skills/weekly-skill/scripts/manuscript_workbench_engine.py export \
  --input-dir /input \
  --draft /app/output/manuscript.md \
  --case-id "<CASE_ID>" \
  --out-dir /app/output \
  --font "Liberation Serif" \
  --render-preview
```

Then run Harbor grounding and final output checks:
```bash
python3 /skills/weekly-skill/scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json
```

**Completion criterion**: `/app/output/manuscript.md`, `/app/output/manuscript.docx`, `/app/output/evidence_map.json`, `/app/output/manuscript_audit.json`, and `/app/output/result.json` exist, pass `final_audit.py`, and preserve all structured `output_contract` fields.

## Exception & Degradation Handling

- **Missing PDF/DOCX extractor or LibreOffice Writer**: `manuscript_workbench_engine.py` uses pure Python 3.12 stdlib OOXML for `.docx` reading and writing so editable Word delivery never fails even without `python-docx`. If `soffice` Writer or `pdftoppm` is absent, PDF/PNG preview failure is logged honestly under `gaps` and `verification.preview.degraded_reason` while retaining verified `.md` and `.docx` artifacts.
- **Unreadable or incomplete source files**: Record exact filenames and missing fields in `gaps`; never invent numbers, p-values, or citations to fill gaps.
- **Conflicting target journal structure**: Never guess whether Results and Discussion should merge; record `journal_structure_spec: UNSPECIFIED` and keep author-switchable separate headings.
