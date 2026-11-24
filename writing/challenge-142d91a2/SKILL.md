---
name: research-proposal-planner
description: Extract /input constraints and facts (PDF/DOCX/MD/JSON), converge broad directions into 1-3 falsifiable questions and quantified objectives, validate 7-element prose, declared-start DAG/CPM and parallel workload, anti-fabrication budget arithmetic, 4-category risks with resource-gap fallbacks, prose-JSON reconciliation (including Chinese year synonyms), verbatim claim_citations source_ref grounding, and zero-crash stdlib OOXML .docx and HTML export.
---

# Research Proposal Convergence, Source Extraction, Feasibility & Audit

Use this skill for thesis proposals and research plans (`research-proposal-planner`) built from fixed local `/input` materials (`case.json`, literature/patents/guidelines in `.pdf`/`.docx`/`.md`, and resource/timeline constraints). Deliver an editable, source-grounded, schedule- and budget-feasible proposal without inventing data, limits, or citations.

## Core Invariants (Checkable Contract)

1. **Pre-Draft Clarification & Automated `/input` Plan Seeding (Zero Fabricated Defaults)**:
   - **Interactive / ambiguous requests**: If research guidelines, total duration, budget ceiling, or data/sample availability are missing, first solicit those four inputs from the user. In non-interactive batch execution, never fabricate missing durations (`total_months: null`, `total_months_source: "missing_in_input"`, `needs_human_confirmation: true`), ceilings, or sample access—when `total_months` is unconfirmed, CPM is hard-blocked (`status: "partial_with_gaps"`, `cpm_blocked_reason: "MISSING_CONFIRMED_DURATION"`).
   - Run `--extract-plan-seed` on `/input` before drafting so raw `.pdf` (`pdftotext`/`pdfplumber`/`pypdf`/pure-stdlib `zlib` fallback), `.docx` (pure-stdlib OOXML), and text files automatically yield grounded constraints, candidate RQs/objectives with `<file>#L<line>` anchors, DOIs/patents, and numeric facts.
2. **Converged Problem & `已知—未知—待验证` Gap Structure**:
   - Narrow broad directions into **1–3 concrete, falsifiable research questions** grounded in explicit contradictions, method boundaries, or empirical constraints from `/input`.
   - Structure `研究背景与现状缺口` explicitly as `已知（前人结论与条件）— 未知（关键机制/参数断点）— 待验证（本研究收敛问题与边界）`; replace hollow priority claims (`尚无研究`, `无人研究`, `首次`, `填补空白`) and overbroad scopes (`解释所有影响因素`) with concrete scope boundaries.
3. **Seven-Element Substantive Closure & Aim-Figure Mainline**:
   - Cover all 7 required sections with substantive prose (`effective_units >= 75`, bilingual `han_chars + round(en_words * 1.6)`): (1) `研究背景与现状缺口`, (2) `科学问题/研究问题`, (3) `研究目标`, (4) `技术路线` (also accepts `研究内容`/`可行性分析`), (5) `阶段计划与里程碑`, (6) `风险与备选方案`, (7) `预期成果与引用溯源`. Supports Markdown headings, Chinese/Arabic numbered headings (`一、`, `1.1`, `（二）`), `.docx` inputs, and headingless natural-paragraph semantic fallback.
   - Bind every objective to a quantified `metric`, `target_value`, `sample_or_unit`, `control_or_baseline`, `checkpoint_month`, technical route step, milestone, and planned Figure/deliverable.
4. **Declared-Start DAG/CPM, Parallel Workload & Anti-Fabrication Budget Integrity**:
   - Ensure `route_steps` form an acyclic DAG (`TopologicalSorter`), obey `depends_on` ordering (`start_month >= dep.end_month`), fit within `[1, total_months]`, and anchor Critical Path ES/EF/LS/LF/slack to declared `start_month` with realistic monthly parallel workload.
   - Ensure `budget_items` contain strictly finite positive amounts summing `<= budget_limit` (no negative values, `NaN`, or zero-value padding). When `/input/case.json` omits `budget_limit`, set `"budget_limit": null`, mark `[EVIDENCE_GAP]`, and record pending human confirmation.
5. **Unavailable Resource Convergence, Four-Category Risks & Verbatim Citation Grounding**:
   - Cover all 4 required risk categories (`data`, `method`, `schedule`, `ethics_compliance`), each with explicit `trigger_signal`, `decision_checkpoint`, and `fallback_plan`. Wire every unavailable sample/resource through `resource_gap -> trigger_signal -> decision_checkpoint -> fallback_plan`.
   - Reconcile `proposal.md` (`--proposal` / `--manuscript`) against `proposal_plan.json` on `total_months` (including Chinese year synonyms such as `为期一年` = 12 months), RQ IDs, objective IDs, budget breakdown/total, milestone months, and per-step CPM `ES/EF/LS/LF` tables (any mismatch raises blocking `PROSE_PLAN_MISMATCH`).
   - Strictly separate `external_fact`, `plan_assumption`, and `pending_evidence`: only verified `external_fact` entries count toward `verified_citations_count` (`>= 3` required; `plan_assumption`/`PLANNED_REF_RE` never satisfy the citation floor). Scanned/unparsed PDFs downgrade only citations explicitly pointing to those scanned PDFs (`unverifiable_scanned_pdf`) and never exempt fabricated DOIs or wrong-file citations.
   - Export editable `.docx` with native tables (`python-docx` or pure-stdlib OOXML generator) plus standalone offline HTML preview.

## Disclosed References (Read on Demand)

- Read `references/opening_convergence_and_mainline.md` before converging broad directions into 1–3 research questions, pruning tasks via the 4-question mainline filter, building the Aim-Figure table, or applying Press-Release anti-defensive prose rules.
- Read `references/plan_schema_and_contract.md` before writing `/app/output/proposal_plan.json` or sealing `/app/output/result.json` with all 5 `output_contract` fields.
- Read `examples/minimal_call_example.md` when you need a synthetic template for `proposal_plan.json` and CLI invocation.

## Execution Workflow

### Step 0: Check Input Completeness (Interactive Clarification Gate)
- Check whether research guidelines/literature, `total_months`, `budget_limit`, and available/unavailable samples or instruments are provided in `/input` or the user prompt.
- In interactive sessions, if any of the four is missing or contradictory, ask targeted clarifying questions before drafting. In automated batch runs, proceed immediately and log each missing constraint via `[EVIDENCE_GAP]` and `human_confirmations`.

### Step 1: Probe sandbox, extract `/input` plan seed, and initialize guards
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<EXACT_CASE_ID>" --task-id "research-proposal-planner"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
python3 /skills/weekly-skill/scripts/proposal_feasibility_checker.py --input-dir /input --extract-plan-seed /app/work/plan_seed.json
```
*Completion criterion*: `/app/work/env.json`, `/app/work/input_blocks.json`, `/app/work/plan_seed.json`, and skeleton `/app/output/result.json` exist; you have inspected `plan_seed.json` (`numeric_facts`, `identifiers`, `unparsed_pdfs`, `plan_seed`) and the actual source documents under `/input`.

### Step 2: Refine `proposal_plan.json` from seed and draft editable `proposal.md`
1. Refine `/app/work/plan_seed.json["plan_seed"]` into `/app/output/proposal_plan.json`, preserving all verbatim `/input` numeric anchors, DOIs/patents, `resource_gaps`, and constraints while filling converged `research_questions` (`1..3`), quantified `objectives`, DAG `route_steps`, `milestones`, `budget_items`, 4-category `risks`, `claim_citations`, and `human_confirmations`.
2. Write `/app/output/proposal.md` covering all 7 elements in clear, assertive academic prose, embedding the `Aim -> Method -> Figure` mainline table, schedule/CPM table, budget breakdown, 4-category Pre-mortem risk table, and claim-to-source citation table.
3. Tick budget after drafting:
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --tick draft_complete
```
*Completion criterion*: `proposal.md` and `proposal_plan.json` agree on every numeric constraint, objective ID, milestone month, resource gap, and source citation.

### Step 3: Run deterministic feasibility, CPM, source-grounding, reconciliation, and export checker
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
- Fix every blocking failure in `proposal_audit.json["errors"]`: missing/thin sections, missing `已知—未知—待验证` structure, unfocused `> 3` questions, DAG cycles or month inversions, CPM overflow, negative/NaN/zero-padding or fabricated budget limits, missing 4-category risk triggers/checkpoints/fallbacks, unlinked unavailable resource gaps, prose↔JSON contradictions, or ungrounded `claim_citations`/DOIs.
- Review `style_warnings` and tighten defensive/hype/AI-cliché language, then re-run until `"passed": true`.

*Completion criterion*: `proposal_audit.json` reports `"passed": true` with `source_grounding_verification.ungrounded_count == 0`, valid editable `proposal.docx` and `proposal_preview.html` exist, and `docx_exporter` is recorded honestly.

### Step 4: Seal `result.json` with all 5 `output_contract` fields and run final audit
Merge `case_id`, `deliverable`, `preserved_facts`, `evidence_map` (with verbatim excerpts from `/app/work/contract_helper.json`), `change_log`, `artifacts`, `metrics`, `metric_provenance`, `findings`, and `gaps` into `/app/output/result.json`:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require proposal.md --require proposal_plan.json --require proposal_audit.json --require proposal.docx
```
*Completion criterion*: `result_guard --validate`, `gap_gate`, and `final_audit` all exit `0`, and `result.json` contains all 5 `output_contract` fields with honest status (`partial_with_gaps` when sample/resource/budget/PDF inputs are missing or unparsed).
