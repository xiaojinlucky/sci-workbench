---
name: grant-proposal-7element-drafting
description: Extract /input corpus facts and workorder boundaries, draft 7-element grant proposal chapters (.md/.docx), and run deterministic offline audits across source_ref verbatim corpus back-checking, G1-G3/WP/milestone/metric coverage, planning-unit/month caps, sentence-level external-author vs missing-applicant anti-fabrication gates, expanded RFP coverage, collaborator conflict logs, and anti-AI/anti-hype prose discipline.
---

# Grant Proposal Drafting, Corpus Indexing & Workorder Audit

Use this skill for grant proposal chapter authoring (`grant-proposal-7element-drafting`) from fixed local `/input` materials (`case.json`, `organizer-workorder.json`, raw `.pdf`/`.docx`/`.md` source papers, and optional notes). Produce directly editable grant prose plus auditable, source-grounded supporting artifacts without web lookup or fabricated evidence.

## Core Invariants (Checkable Contract)

1. **Automated `/input` Fact Extraction & 4-Field Verbatim `source_ref` Back-Checking**:
   - Run `--extract-corpus-index` on `/input` before drafting so raw `.pdf` (`pdftotext` / `pdfplumber` / `pypdf` / pure-stdlib `zlib` fallback), `.docx` (pure-stdlib OOXML), and text files yield exact numeric facts, identifiers (DOIs/patents/arXiv), author hints, missing-evidence boundaries, and candidate `evidence_map` anchors (`<file>#L<line>`).
   - Every external-fact entry in `evidence_map` is verified across **4 independent boolean fields**: `locator_valid` (file + SHA-256 prefix + line/page validity), `verbatim_match`, `numeric_unit_consistent` (intercepting altered numbers and swapped units such as `mg/kg` vs `ug/kg`), and `claim_supported` (intercepting subject/entity swaps and polarity/direction reversals). All four must be `true` for `verified`; file-locator hit alone yields blocking `locator_only_unverified`.
   - `plan`/`gap` tags exempt only future-plan clauses; any unverified external/prior empirical statistics smuggled inside a `plan`/`gap` tag are blocked as `UNVERIFIED_FACT_IN_PLAN_TAG`.
   - RFP requirements and each `scientific_limits` rule must bind to a real draft section/line anchor with substantive targeted prose (self-reported `covered` or bare single-keyword mentions are rejected).
2. **External Literature vs. Missing Applicant Evidence (Sentence-Level Disambiguation)**:
   - Treat published papers and `source_basis.external_authors` in `/input` strictly as external prior work.
   - When `applicant_owned_preliminary_results_supplied` is `false` (or an evidence file states `UNKNOWN`, `PENDING`, `UNAVAILABLE`, `NOT PROVIDED`, `未提供`, `缺失`, `待核实`), attribute cited findings to named external authors and insert explicit `[EVIDENCE_GAP: ...]` markers for missing applicant pilot data, team track record, lab equipment, or ethics approvals.
   - Keep `"已完成（外部文献报道）"` strictly separated from `"拟开展（本项目计划）"`. Express planning units as non-monetary consistency units (`planning_unit_meaning`) and target metrics as future evaluation criteria, never as secured funds or guaranteed outcomes.
3. **Workorder Authority Boundary & Full Requirement Expansion**:
   - Follow `organizer-workorder.json` (auto-probed next to `case.json` or merged from `case.json["organizer_workorder"]`) as the task specification, never as real funding-agency policy (`authority: organizer-workorder-not-funder-policy`).
   - Expand and audit all structured workorder constraints (`sections`, `draft_size`, `consistency`, `scientific_limits`, `required_supporting_files`, and explicit `rfp_requirements`) into a verifiable coverage map.
   - When `actual_funder_notice_supplied` is `false`, omit invented agency eligibility rules or currency caps and list real funder guidelines under human confirmation items.
4. **Seven-Element Closure & Six Supporting Deliverables**:
   - Deliver substantive prose (never outline bullets or writing advice) across all 7 elements: `立项依据`, `科学问题`, `研究目标`, `研究内容`, `创新点`, `技术路线`, `研究基础与可行性`, honoring `draft_size` (e.g., 1600–2600 Chinese characters) when specified. Supports Markdown headings, nested numbered headings (`1.1`, `（一）`), `.docx` inputs, and headingless natural-paragraph prose via semantic fallback, with bilingual thickness weighting (`han_chars + round(en_words * 1.6)`).
   - Deliver and verify all 6 supporting items: (1) editable 7-element prose, (2) 3-tier `claim -> section/paragraph -> source/plan` map back-checked against `/input`, (3) Goal → WP → Milestone → Metric matrix (every goal mapped to `>= 1` WP and `>= 1` milestone; supports multi-WP per goal while blocking orphan goals/WPs), (4) planning-unit breakdown summing `<= planning_unit_cap` with all milestones `<= planning_months`, (5) `>= 3` risks each with `trigger_signal` and `fallback_plan`, and (6) `>= 2` human confirmation items explicitly covering real applicant evidence and real funder requirements.
5. **Collaborator Conflict Logging & Assertive Academic Prose**:
   - When input includes conflicting collaborator comments, record each conflict, chosen direction, rationale, and applicant sign-off status in `conflict_decisions`.
   - Open each section with the core problem, evidence, or testable hypothesis (`one center, three pillars`) rather than defensive disclaimers. State concrete quantitative differences against named prior studies instead of empty priority hype (`首次`, `国际领先`, `填补空白`), policy slogans, or AI filler (`综上所述`, `值得注意的是`, `显著赋能`).

## Disclosed References (Read on Demand)

- Read `references/grant_craft_and_anti_ai.md` before drafting prose, constructing the `Aim -> Evidence` mainline, writing the Pre-mortem risk matrix, or running the anti-defensive/anti-AI pass.
- Read `references/workorder_and_output_contract.md` before building `grant_plan.json`, the 6 supporting artifacts, or sealing `result.json` with all 5 `output_contract` fields.
- Read `examples/minimal_call_example.md` when you need a synthetic interface pattern for `grant_plan.json` and auditor CLI usage.

## Execution Workflow

### Step 1: Probe sandbox, extract `/input` corpus fact index, and initialize guards
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --work /app/work --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --init
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<EXACT_CASE_ID>" --task-id "grant-proposal-7element-drafting"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
python3 /skills/weekly-skill/scripts/grant_7element_auditor.py --input-dir /input --extract-corpus-index /app/work/corpus_index.json
```
*Completion criterion*: `/app/work/env.json`, `/app/work/input_blocks.json`, `/app/work/corpus_index.json`, and skeleton `/app/output/result.json` exist; you have inspected `corpus_index.json` (`numeric_facts`, `identifiers`, `workorder_summary`, `missing_evidence_boundaries`, `candidate_evidence_map`) and the actual source documents under `/input`.

### Step 2: Build structured plan and draft editable 7-element chapter
1. Seed `evidence_map` and `preserved_facts` directly from `/app/work/corpus_index.json` so every cited number, unit, sample size, statistical result, author, and source file anchor (`<file>#L<line>`) matches `/input` verbatim.
2. Create `/app/output/grant_plan.json` capturing `goals`, `work_packages` (with `goal_id`, `planning_units`, `start_month`, `end_month`), `milestones` (`month <= planning_months`), `evidence_map` (3-tier), `risks` (`>= 3` with `trigger_signal` and `fallback_plan`), `human_confirmations` (`>= 2`), `rfp_coverage`, and `conflict_decisions` (if collaborator conflicts exist).
3. Draft `/app/output/grant_chapter.md` containing full editable prose for all 7 elements within `draft_size`, plus clear supporting tables for the 3-tier evidence map, Goal–WP–Milestone–Metric matrix, planning-unit breakdown, Pre-mortem risk table, and human confirmation list.
4. Write `/app/output/element_crosswalk.md` containing the standalone 3-tier `claim -> paragraph/section -> source/plan` traceability table and change/confirmation ledger.
5. Tick budget after drafting:
```bash
python3 /skills/weekly-skill/scripts/budget_guard.py --state /app/work/.budget_state.json --tick draft_complete
```
*Completion criterion*: `grant_chapter.md`, `grant_plan.json`, and `element_crosswalk.md` are written and mutually consistent on every goal ID, month, planning unit, source anchor, and `[EVIDENCE_GAP]`.

### Step 3: Run deterministic 7-element, corpus-grounding, workorder, and style audit
```bash
python3 /skills/weekly-skill/scripts/grant_7element_auditor.py \
  --draft /app/output/grant_chapter.md \
  --plan-json /app/output/grant_plan.json \
  --case-json /input/case.json \
  --corpus-dir /input \
  --crosswalk /app/output/element_crosswalk.md \
  --emit-contract-helper /app/work/contract_helper.json \
  --out-json /app/output/grant_audit.json
```
- `organizer-workorder.json` is auto-probed in `--corpus-dir` / next to `--case-json` (you may still pass `--workorder <path>` explicitly). Pass `--applicant-evidence <path>` if a separate applicant evidence file exists in `/input`. `--draft` accepts `.md`, `.docx`, `.pdf`, or `.txt`.
- Resolve every blocking failure in `grant_audit.json["errors"]` (missing/thin elements, character-count violations, ungrounded `evidence_map` `source_ref` entries, external-author appropriation, proposed-as-completed claims, missing `[EVIDENCE_GAP]`, matrix/unit/month arithmetic errors, `< 3` risks, `< 2` or incomplete human confirmations, RFP omissions, or unresolved collaborator conflicts).
- Review `style_warnings`, `attribution_warnings`, and `fallback_parse_warnings`, revise prose until clear and grounded, then re-run until `"passed": true`.

*Completion criterion*: `grant_audit.json` reports `"passed": true` with `evidence_map_verification.ungrounded_count == 0` and zero blocking errors.

### Step 4: Seal `result.json` with all 5 `output_contract` fields and run final audit
Merge `case_id`, `deliverable`, `preserved_facts`, `evidence_map` (with verbatim excerpts from `/app/work/contract_helper.json`), `change_log`, `artifacts`, `metrics`, `metric_provenance`, `findings`, and `gaps` into `/app/output/result.json`:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require grant_chapter.md --require element_crosswalk.md --require grant_plan.json --require grant_audit.json
```
*Completion criterion*: `result_guard --validate`, `gap_gate`, and `final_audit` all exit `0`, and `result.json` contains every required `output_contract` key with honest status (`partial_with_gaps` whenever applicant/funder/resource evidence is absent).
