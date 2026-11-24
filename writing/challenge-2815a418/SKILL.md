---
name: rebuttal-revision-closure
description: Controlled peer-review rebuttal and manuscript-baseline revision workflow. Trigger when revising a supplied manuscript baseline (e.g. materials/manuscript-baseline.md) against reviewer/editor comments (e.g. materials/reviewer-comments.md), decomposing multi-part comments, binding every response quote to verified line ranges and real baseline-to-revised diffs, closing unfeasible requests in a missing-evidence ledger, controlling four-tier academic tone, and sealing a 5-field Harbor result.json.
---

# Peer-Review Rebuttal and Controlled Baseline Revision

Execute controlled point-by-point peer-review revision on the supplied baseline and comment summaries. Deliver an editable revised baseline, a verified point-by-point response letter, a tri-partite missing-evidence ledger, a machine-audited closure report, and a complete 5-field `result.json`.

## Scope and Hard Invariants

1. **Controlled baseline boundary**: Treat `/input/materials/manuscript-baseline.md` and `/input/materials/reviewer-comments.md` (or the actual text files discovered under `/input/`) as controlled summaries, never as full manuscripts or verbatim peer-review reports. Execute every feasible textual, methodological-clarification, structural, and claim-softening edit directly inside `/app/output/manuscript-revised.md`. Never skip executable baseline edits by claiming materials are incomplete, and never claim real wet-lab experiments, new cohorts, external author revisions, editor acceptance, or full-paper completion occurred.
2. **1:1:1 comment and sub-item closure**: Decompose every editor/reviewer item (`E.1`, `R1.1`, `R1.1a`, `R1.1(b)`, `Comment 1`, `审稿人1意见1`) including compound sub-requirements `(a)`/`(b)`/`(1)`/`(2)`. Assign every item one canonical disposition tag: `ACCEPT_TEXT`, `CLARIFY_EXISTING`, `SOFTEN_CLAIM`, `PARTIAL`, `DISAGREE_WITH_EVIDENCE`, or `AUTHOR_INPUT_NEEDED`.
3. **Triple-bound edit verification**: Every response claiming a manuscript edit (`ACCEPT_TEXT`, `SOFTEN_CLAIM`, or edit-bearing `PARTIAL`) must bind three elements simultaneously:
   - a verbatim quoted excerpt present in `/app/output/manuscript-revised.md`,
   - an exact location anchor (`[L12-L16]` or `[§Methods L12-L16]`) whose range contains that quote, and
   - genuine token-level change in the `baseline -> revised` diff inside that anchored span (whitespace-only reflow or quoting unchanged baseline text as "revised" fails the gate).
4. **Missing-evidence tri-partite closure**: Log every `AUTHOR_INPUT_NEEDED`, `PARTIAL`, `NOT_FEASIBLE`, or `OUT_OF_SCOPE` request in `/app/output/missing_evidence_list.md` with three mandatory components: (a) scientific/scope boundary rationale, (b) existing-material alternative clarification or claim softening applied to the baseline, and (c) concrete author confirmation or data input required. Never make empty promises of future experiments.
5. **Four-tier tone, anti-defensive framing, reviewer isolation, and clean prose**: Calibrate each reply to one of four tones (`ACCEPT`, `CLARIFY`, `POLITE_DISAGREE`, `APPEAL`), isolate reviewers (zero cross-reviewer references inside a reviewer's reply), avoid rebukes ("as we already stated") and empty-thanks replies, and write direct, evidence-forward academic prose free of defensive hedging clutter and AI boilerplate. Apply a pre-mortem self-audit (Blocking vs Optional) before sealing.
6. **Complete 5-field output contract**: Persist `/app/output/result.json` preserving `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, and `change_log`, alongside honest `status`, `sources`, `uncertainty`/`gaps`, and `artifacts`.

## Reference Pointers (Load on Demand)

- Dispositions, sub-item decomposition, readiness labels, and 3-part missing-evidence rules: read `references/disposition-and-evidence.md`.
- Four-tier tone templates, claim-strength verbs, reviewer isolation, and anti-AI prose discipline: read `references/tone-and-isolation.md`.
- Exact `result.json` 5-field schema and pre-seal QA checklist: read `references/output-contract-and-qa.md`.

## Execution Workflow

### Step 1: Probe Environment, Read Actual Inputs, and Initialize Guards
Run the M1-M4 probes first and inspect actual files under `/input/` (read `/input/case.json`, `/input/task.md`, and the exact paths under `/input/materials/` rather than guessing filenames):
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "rebuttal-revision-closure"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
**Completion criterion**: `/app/work/env.json`, `/app/work/input_blocks.json`, and skeleton `/app/output/result.json` exist; actual baseline file, comment file, and `case_id` are verified from disk.

### Step 2: Decompose Comments and Revise the Baseline Manuscript
1. Read `references/disposition-and-evidence.md` and `references/tone-and-isolation.md`.
2. Enumerate every comment ID and nested sub-requirement in the comment file, mapping each to its canonical disposition (`ACCEPT_TEXT` / `CLARIFY_EXISTING` / `SOFTEN_CLAIM` / `PARTIAL` / `DISAGREE_WITH_EVIDENCE` / `AUTHOR_INPUT_NEEDED`).
3. Copy the baseline manuscript to `/app/output/manuscript-revised.md` and execute all feasible edits directly in place:
   - preserve every verified number, unit, DOI, and citation key from the baseline unless a comment explicitly requires a justified correction,
   - strengthen methods/clarity descriptions only using facts supported by the supplied input,
   - soften overbroad causal or novelty claims when requested or when new experimental validation is unavailable, and
   - add concise limitation statements for unfeasible requests rather than inventing new results.
4. Inspect line numbers of `/app/output/manuscript-revised.md` (e.g. `nl -ba`) so every anchor in Step 3 matches the final revised line numbers exactly.

**Completion criterion**: `/app/output/manuscript-revised.md` contains substantive token-level revisions addressing every executable comment item, with zero fabricated data or dropped baseline citations.

### Step 3: Draft Point-by-Point Response Letter and Missing-Evidence Ledger
1. Write `/app/output/response-letter.md` organized by exact comment ID (preserving sub-item IDs such as `R1.2a` / `R1.2(b)`):
   - State the explicit canonical disposition tag (e.g. `Disposition: ACCEPT_TEXT`) and tone tier.
   - Give a direct, evidence-grounded answer explaining what changed or why an item is bounded/disagreed.
   - **Never hand-count line numbers**: right after saving `/app/output/manuscript-revised.md`, run the deterministic anchor extractor to obtain exact `[§Section Lstart-Lend]` tags and verbatim excerpts from the `baseline -> revised` diff, and paste those verified anchors directly into `/app/output/response-letter.md`:
```bash
python3 /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --extract-anchors \
  --baseline /input/materials/manuscript-baseline.md \
  --revised /app/output/manuscript-revised.md \
  --out-json /app/work/extracted_anchors.json
```
   - Cite those extracted location anchors (`[§Methods L18-L21]` or `[L18-L21]`) and quote the actual revised passage verbatim in a Markdown blockquote (`> ...`).
   - Keep each reviewer's section self-contained with zero cross-reviewer references.
2. Write `/app/output/missing_evidence_list.md` covering every `AUTHOR_INPUT_NEEDED`, `PARTIAL`, `NOT_FEASIBLE`, or `OUT_OF_SCOPE` item with three explicit fields per entry: **Boundary Rationale**, **Existing-Material Alternative**, and **Author Confirmation Required**. If zero items require external evidence, still write `/app/output/missing_evidence_list.md` stating that all supplied comments were resolved within baseline scope with zero pending author inputs.

**Completion criterion**: Every comment ID and sub-item appears in `/app/output/response-letter.md` with disposition, exact anchors, and verbatim quotes; every unfeasible/partial/author-dependent item has a complete 3-element entry in `/app/output/missing_evidence_list.md`.

### Step 4: Run Deterministic Triple-Bound Closure Audit
Run `rebuttal_closure_checker.py` to verify 1:1:1 closure, line+section anchor alignment, token-level diff overlap, missing-evidence closure, anti-fabrication, tone/isolation, and style warnings, while generating the structured `result.json` contract patch and Markdown report:
```bash
python3 /skills/weekly-skill/scripts/rebuttal_closure_checker.py \
  --baseline /input/materials/manuscript-baseline.md \
  --comments /input/materials/reviewer-comments.md \
  --revised /app/output/manuscript-revised.md \
  --response /app/output/response-letter.md \
  --missing-evidence /app/output/missing_evidence_list.md \
  --case-id "<CASE_ID>" \
  --out-json /app/output/rebuttal_ledger.json \
  --out-report /app/output/rebuttal_closure_report.md \
  --out-contract-patch /app/work/contract_patch.json
```
Inspect every item in `errors` and `style_warnings`. Fix all hard errors (`missing_responses`, `missing_dispositions`, `unmatched_quotes`, `invalid_line_refs`, `anchor_quote_mismatch`, `fake_edit_unchanged_baseline`, `missing_evidence_gaps`, `fabrication_hits`, `overpromise_hits`, `rebuke_hits`, `cross_reviewer_leaks`, `empty_responses`) and re-run until `passed` is `true` (exit code `0`).

**Completion criterion**: `rebuttal_closure_checker.py` exits `0` with `"passed": true`, zero hard errors, and fresh `/app/output/rebuttal_ledger.json` + `/app/output/rebuttal_closure_report.md` + `/app/work/contract_patch.json`.

### Step 5: Seal 5-Field `result.json` and Run Grounding + Final Audits
1. Read `references/output-contract-and-qa.md`. Merge `/app/work/contract_patch.json` with `deliverable`, `sources`, `uncertainty`, and `artifacts` into `/app/output/result.json` via `result_guard.py --finalize` (never leave raw placeholder markers in `/app/output/`).
2. Run M5 grounding check and M6 final deliverable audit:
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

- **Conflicting reviewer comments**: Reconcile the manuscript change internally in `change_log` and explain the single coherent manuscript state independently to each reviewer without citing the other reviewer.
- **Reviewer premise contradicts existing baseline text**: Use `CLARIFY_EXISTING` or `DISAGREE_WITH_EVIDENCE`. Present the supporting baseline passage with its exact line anchor and clarify visibility in the revised baseline without rebuking the reviewer.
- **Unfeasible new experiment or missing raw data**: Use `PARTIAL` (when paired with baseline claim-softening/limitation edits) or `AUTHOR_INPUT_NEEDED` (when no safe textual edit exists without author facts). Record all 3 mandatory fields in `missing_evidence_list.md` and set `result.json` `status` to `"partial_with_gaps"` when blocking author inputs remain.
- **Non-standard input filenames**: Discover actual baseline/comment filenames via `/app/work/input_blocks.json` or `/input/case.json` and pass those exact paths to `rebuttal_closure_checker.py`.
