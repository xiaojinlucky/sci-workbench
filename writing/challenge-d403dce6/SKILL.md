---
name: grant-proposal-7element-drafting
description: Draft, align, and audit academic grant proposal chapters against runtime case.json and organizer-workorder.json constraints. Enforces 7-element prose closure, G1-G3/WP/milestone/metric parity, planning-unit and month caps, strict external-author vs missing-applicant separation, RFP coverage, collaborator conflict logging, anti-AI/anti-hype prose discipline, and 6 required supporting deliverables.
---

# Grant Proposal Drafting & Workorder Audit

Use this skill for grant proposal chapter authoring (`grant-proposal-7element-drafting`) from fixed local `/input` materials (`case.json`, `organizer-workorder.json`, source papers, and optional notes). Produce directly editable grant prose plus auditable supporting artifacts without web lookup or fabricated evidence.

## Core Invariants (Checkable Contract)

1. **External Literature vs. Missing Applicant Evidence**:
   - Treat published papers and `source_basis.external_authors` in `/input` strictly as external prior work.
   - When `applicant_owned_preliminary_results_supplied` is `false` (or an evidence file states `UNKNOWN`, `PENDING`, `UNAVAILABLE`, `NOT PROVIDED`, `未提供`, `缺失`, `待核实`), attribute cited findings to the named external authors and insert explicit `[EVIDENCE_GAP: ...]` markers for missing applicant pilot data, team track record, lab equipment, or ethics approvals.
   - Keep `"已完成（外部文献报道）"` strictly separated from `"拟开展（本项目计划）"`. Express planning units as non-monetary consistency units (`planning_unit_meaning`) and target metrics as future evaluation criteria, never as secured funds or guaranteed outcomes.
2. **Workorder Authority Boundary**:
   - Follow `organizer-workorder.json` (or `case.json["organizer_workorder"]`) as the task specification, never as real funding-agency policy (`authority: organizer-workorder-not-funder-policy`).
   - When `actual_funder_notice_supplied` is `false`, omit invented agency eligibility rules, currency caps, or real policy citations and list real funder guidelines under human confirmation items.
   - Rely exclusively on actual source files in `/input`; discard stale or mismatched domain labels rather than inventing off-topic facts.
3. **Seven-Element Closure & Six Supporting Deliverables**:
   - Deliver substantive prose (never outline bullets or writing advice) across all 7 elements: `立项依据`, `科学问题`, `研究目标`, `研究内容`, `创新点`, `技术路线`, `研究基础与可行性`, honoring `draft_size` (e.g., 1600–2600 Chinese characters) when specified.
   - Deliver and verify all 6 supporting items: (1) editable 7-element prose, (2) 3-tier `claim -> section/paragraph -> source/plan` map, (3) 1:1 `G1/G2/G3 -> WP -> milestone -> metric` matrix, (4) planning-unit breakdown summing `<= planning_unit_cap` with all milestones `<= planning_months`, (5) `>= 3` risks each with `trigger_signal` and `fallback_plan`, and (6) `>= 2` human confirmation items explicitly covering real applicant evidence and real funder requirements.
4. **RFP Completeness & Collaborator Conflict Resolution**:
   - Map every workorder/RFP requirement to a draft location; flag omissions explicitly.
   - When input includes conflicting collaborator comments, record each conflict, the chosen direction, rationale, and applicant confirmation status in a decision log rather than silently blending contradictions.
5. **Assertive Academic Prose (Zero Hype, Zero AI Clichés)**:
   - Open each section with the core problem, evidence, or testable hypothesis (`one center, three pillars`) rather than defensive disclaimers.
   - State concrete quantitative differences against named prior studies instead of empty priority hype (`首次`, `国际领先`, `填补空白`), policy slogans, or AI filler (`综上所述`, `值得注意的是`, `显著赋能`).

## Disclosed References (Read on Demand)

- Read `references/grant_craft_and_anti_ai.md` before drafting prose, constructing the `Aim -> Evidence` mainline, writing the Pre-mortem risk matrix, or running the anti-defensive/anti-AI pass.
- Read `references/workorder_and_output_contract.md` before building `grant_plan.json`, the 6 supporting artifacts, or sealing `result.json` with all 5 `output_contract` fields.
- Read `examples/minimal_call_example.md` when you need a synthetic interface pattern for `grant_plan.json` and auditor CLI usage.

## Execution Workflow

### Step 1: Probe sandbox, index inputs, and initialize guards
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<EXACT_CASE_ID>" --task-id "grant-proposal-7element-drafting"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
*Completion criterion*: `/app/work/env.json`, `/app/work/input_blocks.json`, and skeleton `/app/output/result.json` exist; you have read `/input/case.json`, `/input/organizer-workorder.json` (if separate), and the actual source paper/material files.

### Step 2: Build structured plan and draft editable 7-element chapter
1. Extract exact numeric values, units, sample sizes, conditions, statistical findings, and author names from `/input` source files; preserve every fact verbatim.
2. Create `/app/output/grant_plan.json` capturing `goals` (`G1..G3`), `work_packages` (`WP1..WP3` with `planning_units`), `milestones` (`month <= planning_months`), `metrics`, `evidence_map` (3-tier), `risks` (`>= 3` with `trigger_signal` and `fallback_plan`), `human_confirmations` (`>= 2`), `rfp_coverage`, and `conflict_decisions` (if collaborator conflicts exist).
3. Draft `/app/output/grant_chapter.md` containing full editable prose for all 7 elements within `draft_size`, plus clear supporting tables for the 3-tier evidence map, G1–G3 matrix, planning-unit budget table, Pre-mortem risk table, and human confirmation list.
4. Write `/app/output/element_crosswalk.md` containing the standalone 3-tier `claim -> paragraph/section -> source/plan` traceability table and change/confirmation ledger.
5. Tick budget after drafting:
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --tick draft_complete
```
*Completion criterion*: `grant_chapter.md`, `grant_plan.json`, and `element_crosswalk.md` are written and mutually consistent on every goal ID, month, planning unit, source citation, and `[EVIDENCE_GAP]`.

### Step 3: Run deterministic 7-element, workorder, and style audit
```bash
python3 /skills/weekly-skill/scripts/grant_7element_auditor.py \
  --draft /app/output/grant_chapter.md \
  --plan-json /app/output/grant_plan.json \
  --case-json /input/case.json \
  --workorder /input/organizer-workorder.json \
  --crosswalk /app/output/element_crosswalk.md \
  --emit-contract-helper /app/work/contract_helper.json \
  --out-json /app/output/grant_audit.json
```
- Omit `--workorder` only when `/input/organizer-workorder.json` does not exist (the auditor automatically falls back to `case.json["organizer_workorder"]`). Pass `--applicant-evidence <path>` if a separate applicant evidence file exists in `/input`.
- Resolve every blocking failure in `grant_audit.json["errors"]` (missing/thin elements, character-count violations, external-author appropriation, proposed-as-completed claims, missing `[EVIDENCE_GAP]`, matrix/unit/month arithmetic errors, `< 3` risks, `< 2` or incomplete human confirmations, RFP omissions, or unresolved collaborator conflicts).
- Review `style_warnings` (priority hype, AI clichés, policy slogans, defensive openings) and revise prose until clear, direct, and grounded, then re-run until `passed: true`.

*Completion criterion*: `grant_audit.json` reports `"passed": true` with zero blocking errors and acceptable prose thickness.

### Step 4: Seal `result.json` with all 5 `output_contract` fields and run final audit
Merge `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `artifacts`, `metrics`, `metric_provenance`, `findings`, and `gaps` (honestly recording missing applicant evidence and funder notice gaps) into `/app/output/result.json`:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require grant_chapter.md --require element_crosswalk.md --require grant_plan.json --require grant_audit.json
```
*Completion criterion*: `result_guard --validate`, `gap_gate`, and `final_audit` all exit `0`, and `result.json` contains every required `output_contract` key with honest status (`partial_with_gaps` whenever applicant/funder evidence is absent).
