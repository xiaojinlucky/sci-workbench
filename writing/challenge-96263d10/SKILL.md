---
name: academic-template-format-adapter
description: Extract verifiable formatting specs from academic journal/thesis templates, resolve conflicting template versions without mixing rules or guessing unspecified fields, apply lossless OOXML .docx adaptation (preserving formulas, footnotes, comments, tracked changes, and field codes), verify Liberation Serif/system fonts, and audit conversion fidelity.
---

# Academic Format & Template Adapter (Lossless & Conflict-Safe)

Adapt academic manuscripts strictly to the designated target template under `/input` while preserving every content unit (text, numbers, formulas, footnotes, comments, tracked changes, live field codes, tables, and figures) and reporting all unspecified or conflicting template rules honestly.

## Read References on Demand

- Read `references/template_adaptation_protocol.md` for conflict isolation, `UNSPECIFIED` field rules, `Liberation Serif` font handling, and the 9-point OOXML fidelity contract.
- Read `references/academic_writing_standards.md` for fact and citation preservation invariants.
- Read `references/output_contract_spec.md` before finalizing `/app/output/result.json`.

## Hard Execution Rules

1. **Extract before formatting**: Always parse the designated target template with `template_spec_and_adapter.py extract-spec` (and `compare-templates` whenever multiple templates or legacy versions exist). Never guess page margins, headers, fonts, line spacing, or Results/Discussion merging from memory; leave unstated fields strictly `UNSPECIFIED`.
2. **Never mix conflicting templates**: Apply ONLY the designated target version. Block alternate-only fields from leaking into the adapted manuscript and report every conflict in `template_comparison.json` and `change_log`.
3. **Lossless OOXML adaptation for `.docx`**: Modify `word/styles.xml` and `word/document.xml` in place via `template_spec_and_adapter.py adapt` so formulas (`m:oMath`), footnotes, comments, tracked changes (`w:ins`/`w:del`), live field codes, tables, and images remain 100% intact. Never flatten a `.docx` into plain text and re-emit it.
4. **Verify real system fonts (including `Liberation Serif`)**: Probe installed fonts via `fc-list`. If the template requests a font unavailable in the offline Linux image, record `font_fallback_reason` honestly and apply supplied `Liberation Serif` instead of silently fabricating font compliance.
5. **Bind real outline headings & check cross-references**: Use true `Heading1`–`Heading3` styles (`w:outlineLvl`), three-line tables, and check Figure/Table caption sequence and cross-reference integrity.

## Step-by-Step Workflow

### Step 1: Probe Sandbox & Index Input Files
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "academic-template-adaptation"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

**Completion criterion**: You have inspected `/input/task.md`, `/input/case.json`, and `/app/work/input_blocks.json` to identify the source manuscript path, the single designated target template, and any secondary/conflicting template files.

### Step 2: Extract Specifications & Isolate Conflicting Templates
If a single target template is provided:
```bash
python3 /skills/weekly-skill/scripts/template_spec_and_adapter.py extract-spec \
  --template /input/<target_template> \
  --out-json /app/work/target_spec.json
```
If multiple templates or conflicting guidelines exist under `/input`:
```bash
python3 /skills/weekly-skill/scripts/template_spec_and_adapter.py compare-templates \
  --target /input/<designated_target_template> \
  --alternate /input/<conflicting_template> \
  --out-json /app/work/template_comparison.json
```

**Completion criterion**: Every explicitly defined formatting field is extracted; all missing fields are listed as `UNSPECIFIED`; all conflicts with alternate templates are isolated with `APPLY_TARGET_ONLY` / `KEEP_UNSPECIFIED_DO_NOT_MIX`.

### Step 3: Run Lossless Adaptation, Fidelity Verification & Optional Preview
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --tick adapt
python3 /skills/weekly-skill/scripts/template_spec_and_adapter.py adapt \
  --source /input/<source_manuscript> \
  --target-template /input/<designated_target_template> \
  --case-id "<CASE_ID>" \
  --out-dir /app/output \
  --fallback-font "Liberation Serif" \
  --render-preview
```
(Add `--alternate-template /input/<other_template>` for each conflicting template present in `/input`.)

Inspect `/app/output/adaptation_fidelity_report.json` and `/app/output/compliance_checklist.md`:
- Supports `.docx` (lossless OOXML in-place rewrite with `Heading1`–`Heading3` + `w:outlineLvl` binding, unstyled pseudo-heading upgrade, academic three-line table normalization, and plain-text reference entry adaptation for Vancouver/GB/T 7714/APA while leaving live Zotero/EndNote fields untouched), `.tex` (preamble font/geometry adaptation with byte-identical document body preservation), and `.md` (structured OOXML `.docx` generation).
- Repeat `--alternate /input/<t>` (or `--alternate-template /input/<t>`) as many times as needed to isolate multiple conflicting legacy templates simultaneously.
- Confirm `adapt_report.fidelity_ok == true` (all 11 OOXML unit checks including `headings_outline_bound` and `three_line_tables_normalized`, or LaTeX body byte-identity + numeric/citation invariants preserved).
- Inspect `crossref_audit.issues` and fix or explicitly flag any broken Figure/Table cross-references in the manuscript change log.
- If `preview.rendered == true`, confirm generated PDF/PNG preview files are non-empty; if `soffice` Writer is unavailable, confirm `degraded_reason` is honestly recorded in `gaps`.

**Completion criterion**: Adapted editable document (`*_adapted.docx` or `*_adapted.tex`), `compliance_checklist.md`, `template_comparison.json`, `adaptation_fidelity_report.json`, and `result.json` exist under `/app/output` with `fidelity_ok == true`.

### Step 4: Final Harbor Gate Checks
```bash
python3 /skills/weekly-skill/scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json
```

**Completion criterion**: `final_audit.py` exits 0 with all `UNSPECIFIED` fields, conflict resolutions, and fidelity checks accurately reflected in `/app/output/result.json`.

## Exception & Degradation Handling

- **Unavailable requested font**: Record `font_fallback` in `gaps` and apply installed `Liberation Serif` as the open-source serif fallback.
- **No LibreOffice Writer / `pdftoppm`**: Do not fake PDF previews; log exact `degraded_reason` while delivering verified OOXML `.docx` and 9-point XML fidelity proof.
- **Corrupted `.docx` or unsupported legacy `.doc`**: Report exact file read failure in `gaps` and `status: partial_with_gaps`.
