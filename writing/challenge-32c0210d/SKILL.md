---
name: longdoc-thesis-revision
description: Controlled long-document and thesis writing workflow supporting multi-chapter General Discussion synthesis (convergences, conditional divergences, irreconcilable conflicts, and [ChX:Lxx]-anchored drafts), native .md/.txt/.tex/.docx/.pdf chapter ingestion with honest DOCX live citation-field (w:instrText) reporting, 4-key review-to-export closed-loop traceability, adaptive workorder anchor induction, deterministic revision state machine (annotate/replace, accept/reject, diff, SHA-256 rollback/reapply, fault injection), and domain-agnostic cross-chapter consistency auditing.
---

# Thesis and Long-Document Revision, General Discussion Synthesis & Cross-Chapter Consistency

Execute controlled, traceable authoring, multi-chapter **General Discussion Synthesis**, and surgical revision on multi-chapter manuscripts, thesis compilations, regulatory/technical long documents, and sectioned working chapters. Operate strictly on supplied files under `/input`; protect all unrequested bytes, surface every conflict, format boundary, or truncation honestly, and never fabricate missing chapters, data, or software state.

## Core Invariants

1. **Evidence-bound Multi-Chapter General Discussion Synthesis (`synthesize-discussion`)**: Extract per-chapter `findings`, `conditions/scope`, `limitations`, and `citations` with exact `[ChX:Lxx]` anchors while filtering PDF/DOCX running headers, bare page numbers, and pure headings (zero running-header evidence leaks). Build a structured 3-column evidence matrix (**`cross_chapter_convergences`** requiring `>=2` distinct chapters, **`conditional_divergences`** stratified by experimental/population conditions, and **`irreconcilable_conflicts`** flagged `needs_author_resolution`). Draft General Discussion paragraphs **strictly from `>=2`-chapter convergences** with mandatory `[ChX:Lxx]` anchors on every sentence and an explicit `pending_confirmations` checklist.
2. **4-Key Closed-Loop Review-to-Export Traceability & Honest Gap Sync**: Enforce `review_item_id <-> target_id <-> proposal_id <-> export_locator` binding across `parse-comments`, `propose --review-item-id`, `accept`/`reject`, and `export`. Triage unresolvable or out-of-scope reviewer requests explicitly as `unresolved` or `out_of_scope` (never leaving everything `open`), reject running-header evidence quotes, and run `export --audit-json ... --result-json ...` so all audit errors, warnings, truncation risks, and unresolved/out-of-scope review items merge automatically into `result.json`.
3. **Honest Native Format & DOCX Citation-Field Reporting**: Parse `.docx` via pure-stdlib OOXML (`word/document.xml` + footnotes/endnotes) to detect live Zotero/CSL/EndNote/Mendeley `w:instrText` fields and tracked changes. When binary `.docx`/`.pdf` inputs are converted into working Markdown, explicitly report `format_mode: "derived_markdown_readonly_source"`, `citation_fields_preserved: false`, and `live_citation_fields_detected`. Use `patch-docx` only for targeted plain-text OOXML paragraph updates that leave untouched ZIP members and live citation field runs intact.
4. **Byte-exact scope protection & full revision lifecycle**: Dynamically induce workorder anchor patterns (`BLOCKED_MISSING_TARGETS` if missing), support `annotate` (exact `v0` byte round-trip after stripping annotations) and substantive `replace`, individual `accept`/`reject`, `diff`, 100% SHA-256 `rollback`/`reapply`, 5-case zero-mutation `fault-test`, and offline zero-CDN HTML preview.
5. **Domain-agnostic cross-chapter consistency & source-inherent dedup separation**: Audit acronyms, symbols, `(entity, unit, value, condition)` conflicts, numbering/citation health, and inverted-index bilingual duplicates—distinguishing `source_inherent` overlaps (warning) from `newly_introduced` duplicates (blocking error).

## Reference Pointers (Load On Demand)

- Output schema and required `/app/output` artifacts: read `references/output_contract_spec.md` before finalizing `result.json`.
- Multi-chapter continuity, anti-duplication general discussion synthesis, tool-boundary comparison (`git diff`/`LaTeXdiff`/`Word`/`Zotero`), and pre-mortem risk matrix: read `references/continuity_and_revision_protocol.md`.
- Minimal end-to-end CLI invocation illustration: see `examples/minimal_workflow_example.md`.

## Execution Workflow

Set portable script root once (defaults to `/skills/weekly-skill` inside execution sandboxes):
```bash
SKILL_ROOT="${SKILL_ROOT:-/skills/weekly-skill}"
```

### Step 1: Probe sandbox, initialize guards, and map inputs
```bash
python3 "$SKILL_ROOT/scripts/preflight_probe.py" --out /app/work/env.json
python3 "$SKILL_ROOT/scripts/budget_guard.py" --init
python3 "$SKILL_ROOT/scripts/result_guard.py" --init --case-id "<CASE_ID>" --task-id "longdoc-thesis-revision"
python3 "$SKILL_ROOT/scripts/block_indexer.py" --scan /input --out /app/work/input_blocks.json
```
**Done when**: `env.json`, initial `result.json`, and `input_blocks.json` exist; actual chapter directory (`.md`/`.txt`/`.tex`/`.docx`/`.pdf`) and workorder path are confirmed from real filesystem reads.

### Step 2: Import chapters, generate chapter plan, and run baseline cross-chapter audit
1. Import working chapters into `/app/work/rev_state` to lock `v0` SHA-256 hashes, induce workorder anchor patterns, auto-anchor anchorless paragraphs, and inspect coverage/truncation:
```bash
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" import \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --state-dir /app/work/rev_state \
  --receipt-out /app/output/import_receipt.json
```
*(Pass `--toc-json <path>` if TOC metadata exists; if advisor/reviewer comments exist, decompose them into trackable items via `parse-comments --state-dir /app/work/rev_state --comments-file <path> --out-json /app/output/review_action_items.json`.)*
2. Run **Multi-Chapter General Discussion Synthesis** (`synthesize-discussion`), generate `chapter_plan.json`, and run baseline consistency audit:
```bash
python3 "$SKILL_ROOT/scripts/longdoc_chapter_consistency.py" synthesize-discussion \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --out-json /app/output/general_discussion_synthesis.json \
  --out-md /app/output/general_discussion_synthesis.md

python3 "$SKILL_ROOT/scripts/longdoc_chapter_consistency.py" \
  --chapters-dir /input/chapters \
  --generate-plan /app/work/chapter_plan.json

python3 "$SKILL_ROOT/scripts/longdoc_chapter_consistency.py" \
  --chapters-dir /input/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --chapter-plan-json /app/work/chapter_plan.json \
  --out-json /app/work/baseline_consistency.json
```
**Done when**: `general_discussion_synthesis.json` confirms `running_header_evidence_leaks == []`, `all_draft_sentences_anchored == true`, and cleanly separates `cross_chapter_convergences` (`>=2` chapters), `conditional_divergences`, and `irreconcilable_conflicts` (`needs_author_resolution`); `import_receipt.json` reports exact `format_mode` and `citation_fields_preserved`.

### Step 3: Execute controlled revision lifecycle (`propose`, `accept`/`reject`, `diff`, `rollback`, `reapply`, `fault-test`, `export`)
1. Propose grounded editorial annotations (`--mode annotate`) or authorized substantive paragraph rewrites (`--mode replace` with `--replacement-file` to avoid shell-quoting errors). Use `--target-id` when explicit/auto anchors are known, or `--file <rel> --block-index <N>` / `--anchor-query "<substring>"` for file-level workorders:
```bash
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" propose \
  --state-dir /app/work/rev_state \
  --target-id "<TARGET_ID>" \
  --mode annotate \
  --title "<CONCISE_TITLE>" \
  --annotation-file /app/work/note_01.txt \
  --source-quote "<EXACT_VERBATIM_SOURCE_QUOTE>" \
  --source-ref "<FILE_OR_ANCHOR_REF>" \
  --uncertainty "<EXPLICIT_LIMITATION_OR_OPEN_CONFLICT>"
```
2. Individually accept verified proposals and reject overbroad/ungrounded candidate proposals:
```bash
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" accept --state-dir /app/work/rev_state --proposal-id P0001 --reason "<RATIONALE>"
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" reject --state-dir /app/work/rev_state --proposal-id P0002 --reason "<RATIONALE>"
```
3. Verify unified diff, 100% SHA-256 rollback to `v0`, deterministic reapplication, and 5-case controlled fault injection (`deliberate_fault_injection`, never a platform error):
```bash
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" diff --state-dir /app/work/rev_state --from-version v0 --to-version latest --out-diff /app/output/revision.diff --out-json /app/work/diff_summary.json
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" rollback --state-dir /app/work/rev_state --to-version v0 --out-json /app/work/rollback_receipt.json
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" reapply --state-dir /app/work/rev_state --from-accepted --out-json /app/work/reapply_receipt.json
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" fault-test --state-dir /app/work/rev_state --out-json /app/output/fault_test_report.json
```
4. Export editable chapters, revision records, evidence map, and self-contained offline HTML diff preview:
```bash
python3 "$SKILL_ROOT/scripts/longdoc_revision_engine.py" export \
  --state-dir /app/work/rev_state \
  --out-dir /app/output/chapters \
  --records-out /app/output/revision_records.json \
  --evidence-out /app/output/evidence_map.json \
  --html-preview /app/output/export_preview.html
```
**Done when**: `rollback_receipt.json` confirms `sha256_restored_100pct == true`, `reapply_receipt.json` confirms `matches_pre_rollback_sha256 == true`, `fault_test_report.json` confirms `state_unchanged_verified == true`, and `export_preview.html` renders offline.

### Step 4: Re-verify exported chapters and write terminology & change log
```bash
python3 "$SKILL_ROOT/scripts/longdoc_chapter_consistency.py" \
  --chapters-dir /app/output/chapters \
  --workorder /input/organizer-edit-workorder.json \
  --baseline /app/work/baseline_consistency.json \
  --chapter-plan-json /app/work/chapter_plan.json \
  --out-json /app/output/longdoc_audit.json
```
Write `/app/output/terminology_and_change_log.md` summarizing covered vs missing chapters, unified acronym/symbol/value ledger, unresolved source conflicts (left untouched in source text), accepted vs rejected proposals, `claim_support_matrix` findings, and rollback/reapply/fault-test receipts.

### Step 5: Seal `result.json` via shell-safe `--patch-file` and run final audits
Write patch payload to `/app/work/result_patch.json` containing all five contract fields (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`) plus `artifacts`, `metrics`, `metric_provenance`, and `findings`:
```bash
python3 "$SKILL_ROOT/scripts/result_guard.py" --patch-file /app/work/result_patch.json --finalize
python3 "$SKILL_ROOT/scripts/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SKILL_ROOT/scripts/final_audit.py" \
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

## Exit Codes, Recovery & Resume Playbook

| Error Code / Exit | Cause | Deterministic Recovery Action |
| --- | --- | --- |
| `BLOCKED_MISSING_TARGETS` (rc=1) | Workorder `target_ids` absent from imported chapters | Inspect `import_receipt.json.anchor_index`; re-verify workorder IDs or pass `--allow-file-fallback` only when file-level scope is explicitly authorized |
| `UNAUTHORIZED_SCOPE` (rc=1) | Target anchor or file outside workorder | Restrict `propose` to `import_receipt.json.authorized_target_ids` / `authorized_files` |
| `TARGET_NOT_FOUND` (rc=1) | Anchor ID, `--block-index`, or `--anchor-query` unmatched | Query `manifest.json` anchor index; never guess trailing block silently |
| `INVALID_PATCH` (rc=1) | Empty body/quote or duplicate annotation | Fix proposal file; state directory remains byte-unchanged; resume from existing `/app/work/rev_state` without re-importing |
| Audit failure after edits (rc=1) | Unauthorized edit or newly broken ref | Run `rollback --to-version v0` then `reapply --proposal-ids <valid_ids>` |

## Failure & Boundary Handling
- **Missing or truncated chapters**: Record exact missing chapter numbers or abrupt endings in `import_receipt.json` and `result.json.gaps`; never claim full-thesis coverage over partial input.
- **Unresolved source contradictions**: Report both verbatim locations, conditions, and values in `longdoc_audit.json` and `change_log`; never silently overwrite source data.
- **Cross-disciplinary & anchorless documents**: Domain-agnostic `(entity, unit, value, condition)` extraction covers SI/Chinese units across clinical, regulatory, materials, chemistry, economics, and social science corpora; condition-phrase binding prevents cross-arm/cross-year false positives.
- **Offline determinism**: Runs entirely via Python 3.12 stdlib (`.docx` OOXML and `.pdf` `zlib` FlateDecode fallbacks included; `pdftotext`/`pdfplumber`/`pypdf` used automatically when present).
