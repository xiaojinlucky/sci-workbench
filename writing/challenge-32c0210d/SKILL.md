---
name: longdoc-thesis-revision
description: Controlled long-document and thesis writing workflow supporting organizer-edit-workorder paragraph/chapter revision state machine (import receipt, propose/accept/reject, diff, rollback/reapply, controlled fault injection, offline HTML preview) and cross-chapter consistency auditing (acronyms, symbols, cross-chapter entity values, numbering gaps, duplicate discussion detection, citation field health, and truncation reporting).
---

# Thesis and Long-Document Revision & Cross-Chapter Consistency

Execute controlled, traceable authoring and surgical revision on multi-chapter manuscripts, thesis compilations, and sectioned working chapters (`Organizer input repair v1`). Operate strictly on supplied files under `/input`; protect all unrequested bytes, surface every conflict or truncation honestly, and never fabricate missing chapters, data, or software state.

## Core Invariants

1. **Byte-exact scope protection**: Read `/input/organizer-edit-workorder.json` (or `organizer_edit_workorder` inside `/input/case.json`) first. Support both `target_ids` (paragraph anchors such as `C01-P002`, `C01-P057`) and `edits[].file` (chapter paths). Keep original publication text, tables, formulas, citations, headings, and all unrequested chapters/blocks byte-identical to their imported SHA-256 baseline.
2. **Distinguish editorial annotations from source claims**: When adding source-grounded annotations, insert clearly tagged editorial blocks without rewriting original wording or silently correcting contradictory source numbers/units. Quote exact source text and state uncertainties explicitly.
3. **Full local revision lifecycle**: When the workorder requests local workflow operations, run the real deterministic state machine (`longdoc_revision_engine.py`) covering: import receipt, propose, individual accept/reject, diff, verified rollback and reapply, controlled invalid-operation fault injection (proving zero state/file mutation), editable manuscript export, and offline single-file HTML diff preview.
4. **Cross-chapter scientific consistency**: Audit acronym expansions, mathematical/physical symbol definitions, same-entity numerical/sample-size conflicts across chapters, figure/table/equation numbering gaps and dangling references, cross-chapter duplicate discussion (bilingual shingle Jaccard), citation field integrity (flattened Zotero/EndNote artifacts or broken reference indices), and coverage/truncation risks.
5. **Honest coverage and output contract**: Every `result.json` must preserve `case_id` and populate all five contract fields (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`). If pages, TOC entries, or chapters are missing or truncated, report the exact covered range and mark gaps explicitly.

## Reference Pointers (Load On Demand)

- Output schema and required `/app/output` artifacts: read `references/output_contract_spec.md` before finalizing `result.json`.
- Cross-chapter continuity, multi-paper thesis general discussion de-duplication, citation live-field protection, anti-defensive scientific synthesis, and pre-mortem long-doc risk matrix: read `references/continuity_and_revision_protocol.md` when drafting or reconciling multi-chapter discussions.
- Minimal end-to-end CLI invocation illustration: see `examples/minimal_workflow_example.md`.

## Execution Workflow

### Step 1: Probe sandbox, initialize guards, and map inputs
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "longdoc-thesis-revision"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
**Done when**: `env.json`, initial `result.json`, and `input_blocks.json` exist; actual chapter directory and workorder path (`/input/organizer-edit-workorder.json` or `/input/case.json`) are confirmed from real filesystem reads.

### Step 2: Import chapters and run baseline cross-chapter audit
1. Import working chapters into the local revision state directory (`/app/work/rev_state`) to lock original SHA-256 hashes, index paragraph anchors, and check coverage/truncation:
```bash
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py import \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --state-dir /app/work/rev_state \
  --receipt-out /app/output/import_receipt.json
```
*(If chapters sit directly under `/input` or another subfolder, pass that actual path to `--chapters-dir`; if TOC metadata is supplied, pass `--toc-json <path>`.)*
2. Run baseline cross-chapter consistency audit:
```bash
python3 /skills/weekly-skill/scripts/longdoc_chapter_consistency.py \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --out-json /app/work/baseline_consistency.json
```
**Done when**: `import_receipt.json` records every chapter's original SHA-256 and target anchor positions; `baseline_consistency.json` catalogs all baseline terminology, symbol, entity-value, numbering, duplicate, citation, and truncation findings.

### Step 3: Execute controlled revision lifecycle (propose, accept/reject, diff, rollback, reapply, fault-test)
1. Inspect target chapters and surrounding Methods/Results/Discussion context so every annotation is grounded in exact source quotations, scientific conditions, limitations, and unresolved conflicts.
2. Propose edits for each authorized `target_id` (or authorized chapter file). When the workorder requires testing individual rejection alongside acceptance, create a distinct proposal to reject (for example, an overly broad or redundant candidate note) and accept the verified source-grounded annotations:
```bash
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py propose \
  --state-dir /app/work/rev_state \
  --target-id "<TARGET_ID>" \
  --mode annotate \
  --title "<CONCISE_ANNOTATION_TITLE>" \
  --annotation "<SOURCE_GROUNDED_EDITORIAL_NOTE>" \
  --source-quote "<EXACT_VERBATIM_SOURCE_QUOTE>" \
  --source-ref "<FILE_OR_ANCHOR_REF>" \
  --uncertainty "<EXPLICIT_LIMITATION_OR_OPEN_CONFLICT>"
```
   - When the task explicitly authorizes substantive paragraph rewriting (rather than non-destructive annotation under strict source-byte preservation), pass `--mode replace --replacement-text "<REVISED_PARAGRAPH>"` on authorized target IDs so the engine tracks full before/after diffs, rollback, and reapplication deterministically.
3. Individually accept or reject proposals:
```bash
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py accept \
  --state-dir /app/work/rev_state --proposal-id "<PROP_ID>" --reason "<ACCEPT_RATIONALE>"
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py reject \
  --state-dir /app/work/rev_state --proposal-id "<PROP_ID_REJECTED>" --reason "<REJECT_RATIONALE>"
```
4. Verify version comparison, true byte-exact rollback to `v0`, and deterministic reapplication of accepted proposals:
```bash
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py diff \
  --state-dir /app/work/rev_state --from-version v0 --to-version latest \
  --out-diff /app/output/revision.diff --out-json /app/work/diff_summary.json

python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py rollback \
  --state-dir /app/work/rev_state --to-version v0 --out-json /app/work/rollback_receipt.json

python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py reapply \
  --state-dir /app/work/rev_state --from-accepted --out-json /app/work/reapply_receipt.json
```
5. Execute controlled invalid-operation fault injection and verify zero state/file mutation (`deliberate_fault_injection`, never reported as platform failure):
```bash
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py fault-test \
  --state-dir /app/work/rev_state --out-json /app/output/fault_test_report.json
```
6. Export editable revised chapters, structured revision records, evidence map, and self-contained offline HTML diff preview:
```bash
python3 /skills/weekly-skill/scripts/longdoc_revision_engine.py export \
  --state-dir /app/work/rev_state \
  --out-dir /app/output/chapters \
  --records-out /app/output/revision_records.json \
  --evidence-out /app/output/evidence_map.json \
  --html-preview /app/output/export_preview.html
```
**Done when**: `rollback_receipt.json` confirms 100% SHA-256 restoration to `v0`, `reapply_receipt.json` confirms deterministic byte restoration of accepted state, `fault_test_report.json` confirms `state_unchanged_verified == true`, and `export_preview.html` renders all original/annotation/decision views offline.

### Step 4: Re-verify cross-chapter consistency, chapter planning & scope protection on exported chapters
```bash
python3 /skills/weekly-skill/scripts/longdoc_chapter_consistency.py \
  --chapters-dir /app/output/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --baseline /app/work/baseline_consistency.json \
  --out-json /app/output/longdoc_audit.json
```
Pass `--chapter-plan-json /app/work/chapter_plan.json` when checking chapter word/character budgets and per-chapter argument-role assignments (`research_question`, `methods`, `results`, `discussion`, `limitations`).
Write `/app/output/terminology_and_change_log.md` summarizing:
- covered chapters and any missing/truncated segments;
- unified terminology/symbol registry and unresolved source conflicts (left untouched in source text, flagged for author decision);
- accepted vs rejected proposals with exact anchor IDs, source quotes, and rollback/reapply/fault-test receipts;
- cross-chapter synthesis guidance (if drafting a general discussion across compiled papers, link only supported common threads without copying earlier discussion prose).

**Done when**: `longdoc_audit.json` shows zero unauthorized chapter modifications (`modified_outside_workorder == []`) and zero newly introduced broken references or formatting corruption; all pre-existing source contradictions remain transparently logged rather than silently altered.

### Step 5: Seal `result.json` and run final grounding & deliverable audits
Patch `/app/output/result.json` with all five `output_contract` fields (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`) plus `artifacts`, `metrics`, `metric_provenance`, and `findings`, then validate:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --require import_receipt.json \
  --require longdoc_audit.json \
  --require revision_records.json \
  --require evidence_map.json \
  --require fault_test_report.json \
  --require export_preview.html \
  --require terminology_and_change_log.md
```
**Done when**: `result_guard.py --validate`, `gap_gate.py`, and `final_audit.py` all exit `0`.

## Failure & Boundary Handling
- **Missing or truncated chapter files**: Record exact missing chapter numbers or abrupt ending lines in `import_receipt.json` and `result.json.gaps`; never claim full-thesis inspection when segments are absent.
- **Unresolved source contradictions** (e.g., conflicting sample sizes or unit labels between source chapters): Report both verbatim locations and values in `longdoc_audit.json` and `change_log`; do not silently rewrite original publication numbers.
- **Flattened citation fields or orphan reference numbers**: Report every unmapped `[N]` or Zotero/EndNote plain-text artifact in `longdoc_audit.json` and add to author confirmation items.
- **No external client required**: All operations run offline via Python 3.12 standard library; no Word XML engine, remote coauthor server, or external CDN is required or fabricated.
