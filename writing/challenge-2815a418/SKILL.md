---
name: rebuttal-revision-closure
description: Controlled peer-review rebuttal and manuscript-baseline revision workflow with native PDF/DOCX/MD/TXT auto-extraction from /input. Trigger when revising a supplied manuscript baseline against reviewer/editor comments (Markdown, PDF, or DOCX), auto-extracting parent and sub-item comment checklists (including non-standard Reviewer #2/Q1/numbered/prose formats), binding every response quote to verified line+section+quote_sha1 anchors and substantive baseline-to-revised diffs, re-anchoring drifted lines, auditing multi-round prior-commitment continuity, closing unfeasible requests in a substantive tri-partite missing-evidence ledger, controlling four-tier academic tone, and sealing a 5-field Harbor result.json.
---

# Peer-Review Rebuttal and Controlled Baseline Revision

Execute controlled point-by-point peer-review revision directly from raw `/input/` manuscript and reviewer comment files (`.md`, `.txt`, `.pdf`, or `.docx`). Deliver an editable revised manuscript, a verified point-by-point response letter, a substantive tri-partite missing-evidence ledger, a machine-audited closure report, and a complete 5-field `result.json`.

## Scope and Hard Invariants

1. **Controlled baseline boundary & native `/input` extraction**: Auto-discover and extract actual manuscript and comment files under `/input/` via `rebuttal_closure_checker.py --extract-review-items --from-input /input --work-dir /app/work` (backed by zero-dependency `scripts/doc_extract.py`: `pdftotext` -> `pypdf` -> `pdfplumber` -> pure-stdlib `zlib` PDF stream fallback; pure-stdlib OOXML `.docx` parser; safe multi-encoding text reader). Execute every feasible textual, methodological-clarification, structural, and claim-softening edit directly inside `/app/output/manuscript-revised.md`. Never skip executable edits by claiming materials are incomplete, and never claim real wet-lab experiments, new cohorts, external author revisions, editor acceptance, or full-paper completion occurred.
2. **1:1:1 parent+sub-item closure with composite keys & full-line coverage**: Match composite comment headings (`### Reviewer #1 - Comment 1`) before pure reviewer section headers (`## Reviewer #1`), key every item by composite `(round, reviewer_id, local_comment_id)` so cross-reviewer `Q1`/`Comment 1` never collide, capture unnumbered standalone requests into `unassigned_requests` for mandatory coverage, decompose single/multi sub-bullets and intra-paragraph multi-requests with `parent_id` and `source_span`, and report `parent_items_covered/total` separately from `atomic_subitems_covered/total`. Assign every item a canonical disposition: `ACCEPT_TEXT`, `CLARIFY_EXISTING`, `SOFTEN_CLAIM`, `PARTIAL`, `DISAGREE_WITH_EVIDENCE`, or `AUTHOR_INPUT_NEEDED`.
3. **Triple-bound edit verification + shared-anchor guard + substantive `CLARIFY` gate**:
   - Every response claiming a manuscript edit (`ACCEPT_TEXT`, `SOFTEN_CLAIM`, or edit-bearing `PARTIAL`) must simultaneously bind: (i) a verbatim quoted excerpt in `/app/output/manuscript-revised.md`, (ii) an exact `[§Section Lstart-Lend]` anchor containing that quote, and (iii) genuine token-level `baseline -> revised` diff overlap with at least `--min-substantive-tokens` (default `3`, relaxed to `>=1` for exact numeric/identifier fixes).
   - Distinct top-level comments must hold distinguishable anchors; full anchor collision across distinct comments triggers `SHARED_ANCHOR_RISK` blocking failure unless explicitly justified.
   - Every `CLARIFY_EXISTING` or `DISAGREE_WITH_EVIDENCE` reply must satisfy the substantive action gate: either genuine manuscript diff (line + token diff) OR verifiable verbatim manuscript quote/line anchor paired with an explicit `no_manuscript_edit_justification` (`>= 15` chars); otherwise `CLARIFY_NO_SUBSTANTIVE_ACTION` blocks closure.
   - Preserve baseline DOIs and key statistical/sample-size tokens unless explicitly justified in `response-letter.md` (`unexplained_baseline_numeric_drop`).
4. **Drift-proof anchoring (`quote_sha1` + `--reanchor`) & multi-round continuity (`--previous-ledger`)**: Never hand-count line numbers. Extract `[§Section Lstart-Lend]` + `quote_sha1` deterministically with `--extract-anchors`; if later edits shift line numbers, run `--reanchor` to recompute exact anchors automatically. In multi-round revisions, pass `--previous-ledger` to verify previously verified edits remain intact (`prior_commitment_unmet`).
5. **Substantive missing-evidence tri-partite closure**: Log every `AUTHOR_INPUT_NEEDED`, `PARTIAL`, `NOT_FEASIBLE`, or `OUT_OF_SCOPE` request in `/app/output/missing_evidence_list.md` with three mandatory components plus concrete anchor/data-item specificity (`>= 80` chars, preventing keyword-stuffing): (a) scientific/scope boundary rationale, (b) existing-material alternative clarification or claim softening applied to the baseline, and (c) concrete author confirmation or data input required.
6. **Four-tier tone, reviewer isolation, clean prose, and complete 5-field contract**: Calibrate tones (`ACCEPT`, `CLARIFY`, `POLITE_DISAGREE`, `APPEAL`), isolate reviewers (zero cross-reviewer leaks), block rebukes/overpromises/empty-thanks replies, and seal `/app/output/result.json` with `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, honest `status`, `sources`, `uncertainty`/`gaps`, and `artifacts`.

## Reference Pointers (Load on Demand)

- Dispositions, sub-item decomposition, readiness labels, and 3-part missing-evidence rules: read `references/disposition-and-evidence.md`.
- Four-tier tone templates, claim-strength verbs, reviewer isolation, and anti-AI prose discipline: read `references/tone-and-isolation.md`.
- Exact `result.json` 5-field schema and pre-seal QA checklist: read `references/output-contract-and-qa.md`.

## Execution Workflow

### Step 1: Probe Environment, Auto-Extract `/input` Baseline & Review Items, and Initialize Guards
Run M1-M4 probes and automatically extract manuscript baseline text and structured review items from `/input/` (supports `.md`, `.txt`, `.pdf`, `.docx`):
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "rebuttal-revision-closure"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
python3 /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --extract-review-items \
  --from-input /input \
  --work-dir /app/work \
  --out-json /app/work/review_items.json
```
**Completion criterion**: `/app/work/review_items.json`, `/app/work/baseline_extracted.md`, `/app/work/comments_extracted.md`, and skeleton `/app/output/result.json` exist with `parse_underflow_ids == []`.

### Step 2: Review Extracted Items and Execute Substantive Baseline Revisions
1. Read `references/disposition-and-evidence.md` and `references/tone-and-isolation.md`.
2. Inspect `/app/work/review_items.json` (which preserves every parent item, nested sub-item, category, severity, and external-evidence hint). Assign each item its canonical disposition.
3. Copy `/app/work/baseline_extracted.md` (or the original Markdown baseline) to `/app/output/manuscript-revised.md` and execute all feasible edits in place:
   - preserve every verified number, statistical token, unit, DOI, and citation key from the baseline unless a comment requires a documented correction,
   - strengthen methods/clarity descriptions strictly from supplied input facts,
   - soften overbroad causal/novelty claims when requested or when new experimental validation is unavailable, and
   - add concise limitation statements for unfeasible requests rather than inventing results.

**Completion criterion**: `/app/output/manuscript-revised.md` contains substantive token-level revisions addressing every executable item, with zero fabricated data or unexplained dropped baseline DOIs/statistics.

### Step 3: Extract Verified Anchors, Draft Response Letter & Missing-Evidence Ledger, and Re-Anchor if Needed
1. Immediately after saving `/app/output/manuscript-revised.md`, extract exact `[§Section Lstart-Lend]` tags, `quote_sha1` hashes, and verbatim revised excerpts:
```bash
python3 /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --extract-anchors \
  --baseline /app/work/baseline_extracted.md \
  --revised /app/output/manuscript-revised.md \
  --out-json /app/work/extracted_anchors.json
```
2. Write `/app/output/response-letter.md` organized by exact comment ID (covering parent and every sub-item): state canonical `Disposition:`, cite extracted `[§Section Lstart-Lend]` anchors, quote revised or existing baseline passages verbatim in Markdown blockquotes (`> ...`), and keep each reviewer section strictly isolated.
3. If subsequent edits shift line numbers in `/app/output/manuscript-revised.md`, automatically fix drifted anchors without hand-counting:
```bash
python3 /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --reanchor \
  --revised /app/output/manuscript-revised.md \
  --response /app/output/response-letter.md \
  --out-response /app/output/response-letter.md \
  --out-json /app/work/reanchor_report.json
```
4. Write `/app/output/missing_evidence_list.md` for every `AUTHOR_INPUT_NEEDED`, `PARTIAL`, `NOT_FEASIBLE`, or `OUT_OF_SCOPE` item with three explicit substantive fields (**Boundary Rationale**, **Existing-Material Alternative**, **Author Confirmation Required**) citing concrete section anchors and data items (`>= 80` chars per entry). If zero items require external evidence, still write the file confirming zero pending author inputs.

**Completion criterion**: Every comment ID and sub-item appears in `/app/output/response-letter.md` with disposition, exact anchors, and verbatim quotes; every unfeasible/partial item has a complete substantive 3-element entry in `/app/output/missing_evidence_list.md`.

### Step 4: Run Deterministic Triple-Bound & Multi-Round Closure Audit
Run `rebuttal_closure_checker.py` (passing `--previous-ledger <prev_ledger.json>` in multi-round revisions to verify prior commitments remain intact):
```bash
python3 /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --baseline /app/work/baseline_extracted.md \
  --comments /app/work/comments_extracted.md \
  --revised /app/output/manuscript-revised.md \
  --response /app/output/response-letter.md \
  --missing-evidence /app/output/missing_evidence_list.md \
  --case-id "<CASE_ID>" \
  --out-json /app/output/rebuttal_ledger.json \
  --out-report /app/output/rebuttal_closure_report.md \
  --out-contract-patch /app/work/contract_patch.json
```
Resolve every hard error (`comment_parse_underflow`, `missing_responses`, `missing_dispositions`, `unmatched_quotes`, `invalid_line_refs`, `anchor_quote_mismatch`, `fake_edit_violations`, `clarify_ungrounded`, `missing_evidence_gaps`, `prior_commitment_unmet`, `fabrication_hits`, `overpromise_hits`, `rebuke_hits`, `cross_reviewer_leaks`, `dropped_baseline_dois`, `unexplained_stat_drops`) and re-run until `passed` is `true` (exit code `0`).

**Completion criterion**: `rebuttal_closure_checker.py` exits `0` with `"passed": true`, zero hard errors, and fresh `/app/output/rebuttal_ledger.json`, `/app/output/rebuttal_closure_report.md`, and `/app/work/contract_patch.json`.

### Step 5: Seal 5-Field `result.json` and Run Grounding + Final Audits
1. Read `references/output-contract-and-qa.md`. Merge `/app/work/contract_patch.json` with `deliverable`, `sources`, `uncertainty`, and `artifacts` into `/app/output/result.json` via `result_guard.py --finalize`.
2. Run M5 grounding verification and M6 final deliverable audit:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output \
  --require manuscript-revised.md \
  --require response-letter.md \
  --require missing_evidence_list.md \
  --require rebuttal_ledger.json \
  --require rebuttal_closure_report.md
```
**Completion criterion**: `result_guard.py --validate`, `gap_gate.py`, and `final_audit.py` all exit `0`, and `/app/output/result.json` contains all 5 `output_contract` fields with honest `status` (`"ok"` or `"partial_with_gaps"`).

## Exception and Edge-Case Handling

- **PDF/DOCX or non-standard comment formats**: `--extract-review-items` natively decodes `.pdf`/`.docx`/multi-encoding text, handles `Reviewer #2`, `Q1`/`W1`, `问题一`, section-numbered lists, and unnumbered prose (`AUTO-1..N` with `id_source="heuristic"`).
- **Conflicting reviewer comments**: Reconcile into one coherent manuscript state in `change_log` and explain independently to each reviewer without mentioning the other reviewer.
- **Reviewer premise contradicts existing baseline text**: Use `CLARIFY_EXISTING` or `DISAGREE_WITH_EVIDENCE` with an exact verbatim baseline quote and line anchor (verified by `clarify_ungrounded`), without rebuking the reviewer.
- **Unfeasible new experiment or missing raw data**: Use `PARTIAL` (paired with baseline claim-softening/limitation edits) or `AUTHOR_INPUT_NEEDED`, logging all 3 substantive fields with concrete data-item/section anchors in `missing_evidence_list.md` and setting `result.json` `status` to `"partial_with_gaps"`.
- **Multi-round line drift or prior-round reversion**: Run `--reanchor` after any late manuscript edit and pass `--previous-ledger` to catch silently reverted prior commitments.
