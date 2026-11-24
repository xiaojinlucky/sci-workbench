---
name: translational-science-writing
description: Audience-calibrated translational science writing skill converting technical papers into public science, technical reports, grant briefs, policy memos, or industry pitches while enforcing zero numeric/unit/benchmark drift (with bilingual magnitude equivalence and derived-rate verification), a 4-tier evidence-strength ladder preventing overclaiming or model-boundary omission, first-use acronym expansion, glossary lock, natural non-AI prose auditing with scientific whitelist, and paragraph-by-paragraph omission traceability with side-by-side author review export.
---

# Translational Science Writing & Evidence-Boundary Adaptation

Transform technical scientific manuscripts under `/input` into audience-ready deliverables (`public_science`, `tech_report`, `grant_brief`, `policy_memo`, `industry_pitch`) that non-specialists can read without background lookup, while keeping every retained number, comparison benchmark, experimental model, and causal limit faithful to the inspected source.

## Core Invariants

1. **Audience-calibrated Keep / Compress / Omit priority**: Select the target audience profile first and apply its explicit rules for jargon density, first-use acronym expansion, sentence length, and mandatory evidentiary elements. Delete redundancy and tutorial filler; never delete sample size, experimental model, control baseline, or limiting conditions under the guise of simplification.
2. **Zero factual, numerical, and benchmark drift**: Every number, unit, sample size, statistical metric (`p`, `95% CI`, `r`, `R²`), and comparison baseline must trace directly to the source. Bilingual magnitude equivalences (e.g., `12,000` <-> `1.2万`, `1,500,000` <-> `1.5 million`) are recognized cleanly; any derived percentage or fold-change must show its source numerator and denominator. Never round a borderline `p`-value into a stronger significance threshold.
3. **4-tier evidence ladder & hard anti-escalation gate**: Classify source evidence into its highest supported tier:
   - Tier 1: `in_vitro_or_computational` (cell lines, ex vivo assays, simulations, bioinformatics)
   - Tier 2: `animal_or_preclinical` (mouse/rat/zebrafish/organoid preclinical models)
   - Tier 3: `observational_correlational` (cohort, cross-sectional, retrospective, single-center association)
   - Tier 4: `rct_or_clinical_causal` (randomized controlled clinical trials, direct human causal validation)
   The adapted draft must never assert a higher tier (e.g., promising patient cure, human clinical efficacy, or proven causality from cell/mouse/correlational data) and must explicitly retain the source model boundary.
4. **Meaning-first natural prose without AI hype or defensive clutter**: Write directly from substantiated findings ("what phenomenon was observed, why this method was used, what it informs next"). State evidence boundaries clearly once where they govern interpretation ("what the evidence establishes so far + what validation step comes next") rather than piling up defensive disclaimers. Eliminate marketing hype, business jargon, rhetorical false-pivot formulas, and AI transition signposts without disturbing real scientific terminology.
5. **Paragraph-level traceability & author-editable handoff**: Emit `/app/output/paragraph_trace.json` mapping every source paragraph to `KEEP`, `COMPRESS`, or `OMIT_REDUNDANCY` with retained facts and explicit reasons, plus `/app/output/side_by_side_review.md` so the original researcher can verify every cut and continue editing.

## Reference Pointers (Load On Demand)

- Output schema and required `/app/output` artifacts: read `references/output_contract_spec.md` before sealing `result.json`.
- 5-audience matrix, Keep/Compress/Omit priorities, meaning-first translation, and natural scientific prose rules: read `references/audience_matrix_and_style_rules.md` before drafting.
- 4-tier evidence ladder, model-boundary conservation, numeric/derived-rate standards, and paragraph-trace schema: read `references/evidence_ladder_and_omission_rules.md` before writing `paragraph_trace.json`.
- Minimal synthetic invocation walkthrough: see `examples/minimal_workflow_example.md`.

## Execution Workflow

### Step 1: Probe sandbox, initialize guards, and extract source constraints
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "translational-science-writing"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
Read `/input/case.json`, `/input/task.md`, and the actual source manuscript files. Identify:
- target audience (`public_science`, `tech_report`, `grant_brief`, `policy_memo`, or `industry_pitch`), genre, language, and length limit;
- source evidence tier (Tier 1–4), experimental model/species, sample size, comparison baseline, and key statistical caveats;
- whether any source section is missing, internally contradictory, unpublished/confidential, or lacking citations.

**Done when**: Input facts, audience profile, evidence tier, and any source gaps are verified from actual `/input` files.

### Step 2: Lock terminology glossary and draft the audience-adapted deliverable
1. Create `/app/output/glossary_lock.json` fixing one canonical target term per domain concept (and banning unauthorized synonyms or inflated clinical phrasing).
2. Draft the complete, ready-to-use adapted manuscript in `/app/output/adapted_article.md`:
   - Front-load the concrete finding and why it matters to the target reader;
   - Expand every domain acronym on first appearance according to audience rules;
   - Keep exact numbers, units, comparison baselines, and experimental model boundaries intact;
   - Write in direct, concrete sentences free of promotional hype, corporate buzzwords, rhetorical false pivots, and AI transition fillers.
3. Write `/app/output/paragraph_trace.json` covering every source paragraph with `paragraph_id`, `source_section`, `source_excerpt`, `action` (`KEEP` | `COMPRESS` | `OMIT_REDUNDANCY`), `retained_facts`, `omitted_items`, `keep_or_omit_reason`, and `author_check_needed`.

**Done when**: `adapted_article.md`, `glossary_lock.json`, and `paragraph_trace.json` are written under `/app/output` with every source key fact accounted for.

### Step 3: Run deterministic fidelity, evidence-ladder, prose & trace audit
```bash
python3 /skills/weekly-skill/scripts/translational_fidelity_auditor.py \
  --source /input/<SOURCE_FILE> \
  --target /app/output/adapted_article.md \
  --audience <public_science|tech_report|grant_brief|policy_memo|industry_pitch> \
  --glossary-json /app/output/glossary_lock.json \
  --trace-json /app/output/paragraph_trace.json \
  --out-json /app/output/translation_fidelity.json \
  --review-md /app/output/side_by_side_review.md
```
When the prompt specifies an explicit length cap, pass `--max-words <N>` or `--max-chars <N>` to enforce deterministic ceiling checks alongside the 5-genre required-section checks (`genre_structure_checks`).
Inspect `translation_fidelity.json` and `side_by_side_review.md`. Fix every blocking error:
- ungrounded numbers or undocumented derived ratios;
- `p`-value rounding toward stronger significance or dropped comparison baseline;
- `CLAIM_LEVEL_ESCALATION` or `MODEL_BOUNDARY_OMITTED`;
- unexpanded first-use acronyms or glossary drift;
- `prose_hard_blocks` (hype, corporate jargon, false-pivot clichés, AI road signs, em-dash overuse);
- `CRITICAL_LIMITATION_OMITTED` in `paragraph_trace.json`.
Re-run the auditor until `passed == true`.

**Done when**: `translational_fidelity_auditor.py` exits `0` (`passed: true`) and `side_by_side_review.md` is freshly generated from the final draft.

### Step 4: Seal `result.json` and run grounding & deliverable audits
Patch `/app/output/result.json` with all five `output_contract` fields (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`) plus `artifacts`, `metrics`, `metric_provenance`, and `findings`:
```bash
python3 /skills/weekly-skill/scripts/result_guard.py --patch-json '<PATCH_JSON>' --finalize
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py \
  --dir /app/output \
  --require adapted_article.md \
  --require glossary_lock.json \
  --require paragraph_trace.json \
  --require translation_fidelity.json \
  --require side_by_side_review.md
```
**Done when**: `result_guard.py --validate`, `gap_gate.py`, and `final_audit.py` all exit `0`.

## Failure & Boundary Handling
- **Missing citations or unverified external comparisons**: Keep claims strictly inside inspected `/input` files; list missing references in `result.json.gaps` and `side_by_side_review.md`.
- **Internal source contradictions**: Surface both values with exact locations in `change_log` and `side_by_side_review.md` rather than silently choosing one.
- **Length vs. fidelity conflict**: When a tight word budget prevents covering every secondary assay, compress secondary methods via `COMPRESS` while preserving primary numbers, model species, and key boundary conditions—never cut the limitation to fit word count.
- **Confidential or unpublished material**: Honor explicit non-disclosure constraints in `/input/task.md`, mark restricted items clearly, and omit restricted details with explicit trace rationale.
