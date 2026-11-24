---
name: research-proposal-planner
description: Draft, converge, and validate thesis and research proposals across seven core elements (background/gap, 1-3 falsifiable questions, quantified objectives, technical route DAG with CPM and parallel workload, schedule milestones, 4-category risks with resource-gap fallbacks, and expected deliverables). Reconciles Markdown prose against structured JSON, grounds claims to /input sources, and exports editable DOCX and HTML using pure Python stdlib.
---

# Research Proposal Convergence, Feasibility & Audit

Use this skill for thesis proposals and research plans (`research-proposal-planner`) built from fixed local `/input` materials (`case.json`, literature/guidelines, resource and timeline constraints). Deliver an editable, source-grounded, schedule- and budget-feasible proposal without inventing data, limits, or citations.

## Core Invariants (Checkable Contract)

1. **Converged Problem & `已知—未知—待验证` Gap Structure**:
   - Narrow broad directions into **1–3 concrete, falsifiable research questions** grounded in explicit contradictions, method boundaries, or empirical constraints from `/input`.
   - Structure `研究背景与现状缺口` explicitly as `已知（前人结论与条件）— 未知（关键机制/参数断点）— 待验证（本研究收敛问题与边界）`; replace hollow priority claims (`尚无研究`, `无人研究`, `首次`, `填补空白`) and overbroad scopes (`解释所有影响因素`) with concrete scope boundaries.
2. **Seven-Element Substantive Closure & Aim-Figure Mainline**:
   - Cover all 7 required sections with substantive prose (never bare outlines): (1) `研究背景与现状缺口`, (2) `科学问题/研究问题`, (3) `研究目标`, (4) `技术路线`, (5) `阶段计划与里程碑`, (6) `风险与备选方案`, (7) `预期成果与引用溯源`.
   - Bind every objective to a quantified `metric`, `target_value`, `sample_or_unit` (inference unit / sample size), `control_or_baseline`, `checkpoint_month`, technical route step, milestone, and planned Figure/deliverable.
3. **DAG Topology, Critical Path (CPM), Parallel Workload & Budget Integrity**:
   - Ensure `route_steps` form an acyclic DAG (`TopologicalSorter`), obey `depends_on` ordering (`start_month >= dep.end_month`), fit within `[1, total_months]`, and yield a Critical Path length `<= total_months` with realistic monthly parallel workload.
   - Ensure `budget_items` contain strictly finite positive amounts summing `<= budget_limit` (no negative values, `NaN`, or zero-value padding). When `/input/case.json` does not supply a `budget_limit`, never fabricate a numeric ceiling—mark `[EVIDENCE_GAP]` and record the budget ceiling as pending human confirmation.
4. **Unavailable Sample/Resource Convergence & Four-Category Risks**:
   - Cover all 4 required risk categories: `data`, `method`, `schedule`, and `ethics_compliance`, each with explicit `trigger_signal`, `decision_checkpoint`, and `fallback_plan`.
   - Whenever `/input` reports unavailable key samples, datasets, or instruments, explicitly narrow the research question scope and wire each gap through `resource_gap -> trigger_signal -> decision_checkpoint -> fallback_plan`.
5. **Prose ↔ JSON Reconciliation, Citation Grounding & Zero-Crash Export**:
   - Keep `proposal.md` prose and `proposal_plan.json` 100% consistent on `total_months`, `budget_limit`/`budget_sum`, objective count/IDs, and milestone months.
   - Bind key claims to `/input` source IDs/DOIs or explicit `[EVIDENCE_GAP]` markers.
   - Export editable `.docx` (via `python-docx` when present, or the bundled pure-stdlib OOXML exporter when absent, plus optional standalone HTML preview) and report exact exporter status honestly.

## Disclosed References (Read on Demand)

- Read `references/opening_convergence_and_mainline.md` before converging broad directions into 1–3 research questions, pruning tasks via the 4-question mainline filter, building the Aim-Figure table, or applying Press-Release anti-defensive prose rules.
- Read `references/plan_schema_and_contract.md` before writing `/app/output/proposal_plan.json` or sealing `/app/output/result.json` with all 5 `output_contract` fields.
- Read `examples/minimal_call_example.md` when you need a synthetic template for `proposal_plan.json` and CLI invocation.

## Execution Workflow

### Step 1: Probe sandbox, index `/input`, and initialize guards
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<EXACT_CASE_ID>" --task-id "research-proposal-planner"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
*Completion criterion*: `/app/work/env.json`, `/app/work/input_blocks.json`, and skeleton `/app/output/result.json` exist; you have read `/input/case.json`, `task.md`, and every actual source document under `/input`.

### Step 2: Construct `proposal_plan.json` and draft editable `proposal.md`
1. Extract exact timeline (`total_months`), budget (`budget_limit` if supplied), staff/parallel capacity, available vs. unavailable samples/resources, and source literature from `/input`.
2. Write `/app/output/proposal_plan.json` containing `research_questions` (`1..3`), `objectives`, `route_steps` (with `start_month`, `end_month`, `depends_on`, `effort_units`), `milestones`, `budget_items`, `resource_gaps`, `risks` (covering `data`, `method`, `schedule`, `ethics_compliance`), `claim_citations`, and `human_confirmations`.
3. Write `/app/output/proposal.md` covering all 7 elements in clear, assertive academic prose, embedding the `Aim -> Method -> Figure` mainline table, schedule/CPM table, budget breakdown, 4-category Pre-mortem risk table, and claim-to-source citation table.
4. Tick budget after drafting:
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --tick draft_complete
```
*Completion criterion*: `proposal.md` and `proposal_plan.json` agree on every numeric constraint, objective ID, milestone month, resource gap, and source citation.

### Step 3: Run deterministic feasibility, CPM, reconciliation, and export checker
```bash
python3 /skills/weekly-skill/scripts/proposal_feasibility_checker.py \
  --proposal /app/output/proposal.md \
  --plan-json /app/output/proposal_plan.json \
  --case-json /input/case.json \
  --corpus /input \
  --export-docx /app/output/proposal.docx \
  --export-html /app/output/proposal_preview.html \
  --emit-contract-helper /app/work/contract_helper.json \
  --out-json /app/output/proposal_audit.json
```
- Fix every blocking failure in `proposal_audit.json["errors"]`: missing/thin sections, missing `已知—未知—待验证` structure, unfocused `> 3` questions, DAG cycles or month inversions, CPM overflow, negative/NaN/zero-padding or fabricated budget limits, missing 4-category risk triggers/checkpoints/fallbacks, unlinked unavailable resource gaps, prose↔JSON contradictions, or ungrounded DOIs.
- Review `style_warnings` and tighten defensive/hype/AI-cliché language, then re-run until `"passed": true`.

*Completion criterion*: `proposal_audit.json` reports `"passed": true`, valid editable `proposal.docx` and `proposal_preview.html` are created, and `docx_exporter` is recorded honestly.

### Step 4: Seal `result.json` with all 5 `output_contract` fields and run final audit
Merge `case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`, `artifacts`, `metrics`, `metric_provenance`, `findings`, and `gaps` into `/app/output/result.json`:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require proposal.md --require proposal_plan.json --require proposal_audit.json --require proposal.docx
```
*Completion criterion*: `result_guard --validate`, `gap_gate`, and `final_audit` all exit `0`, and `result.json` contains all 5 `output_contract` fields with honest status (`partial_with_gaps` when sample/resource/budget inputs are missing).
