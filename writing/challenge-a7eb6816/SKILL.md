---
name: translational-science-writing
description: Audience-calibrated translational science writing skill supporting native .pdf/.docx/.md ingestion, automated --extract-source-brief, dual-axis evidence tiering (study_design_axis + translational_maturity_axis) with background-RCT isolation and all-tier population/indication extrapolation checks, strict interval-entailment p-value verification, clause-bound group/metric swap interception, exact figure/table/citation ID verification, and side-by-side author review export.
---

# Translational Science Writing & Evidence-Boundary Adaptation

Transform technical scientific manuscripts (`.pdf`, `.docx`, `.md`, `.txt`) under `/input` into audience-ready deliverables (`public_science`, `tech_report`, `grant_brief`, `policy_memo`, `industry_pitch`) that target readers can understand without external lookup, while preserving every retained number, unit, group/metric binding, comparison baseline, exact figure/table/citation ID, experimental/engineering model boundary, and statistical caveat.

## Core Invariants

1. **Native multi-format ingestion & zero-manual-bootstrapping extraction**: Run `translational_fidelity_auditor.py --extract-source-brief` directly on `/input/<SOURCE_FILE>` (`.pdf` via `pdftotext` -> `pdfplumber` -> `pypdf` -> pure-stdlib `zlib` fallback; `.docx` via pure-stdlib OOXML) to extract dual-axis evidence tier, displays, baselines, internal source contradictions, `paragraph_trace.json` skeleton, and `glossary_lock.json` draft. Empty extractions fail explicitly (`EMPTY_SOURCE_OR_TARGET`).
2. **Strict interval-entailment p-values & clause-bound group/metric conservation**:
   - Extract `(operator, numeric_value, raw)` triples (`<`, `<=`, `=`, `>`, `>=`) and enforce mathematical interval entailment: any target p-value without a source p-value triggers `UNVERIFIED_P_VALUE`; rewriting `p < 0.05` as `p = 0.05` or `p < 0.001` triggers `P_VALUE_OPERATOR_OR_BOUND_DRIFT` (no repeated-mention operator bypass).
   - Extract numeric facts with `(line, sentence, clause, group_tokens, metric_tokens, value, unit)` so swapping group values (e.g., treatment 61.5% / control 53.1% swapped to treatment 53.1% / control 61.5%) immediately blocks with `GROUP_OR_METRIC_SWAP_MISMATCH`.
3. **Exact Figure, Table & Citation ID verification**: Audit specific display and citation identifier sets (`Figure 1`, `Table 2`, `[3]`) even when the source contains zero figures/tables/citations. Fabricating `Figure 3` or an ungrounded `[99]` immediately blocks with `FABRICATED_FIGURE_TABLE_OR_CITATION`.
4. **Dual-axis evidence tiering (`study_design_axis` + `translational_maturity_axis`) with background-citation isolation & all-tier extrapolation checks**:
   - Separate `background_citation` sentences (e.g. Introduction/Background mentioning a `previous RCT` / `prior trial` / `in the literature`) from `present_study_empirical` sentences so background mentions never inflate an in-vitro or observational study to Tier 4.
   - Retain `POPULATION_OR_INDICATION_EXTRAPOLATION` checks across **all tiers including Tier 4** (blocking pediatric-to-adult, single-center-to-universal, or unrestricted off-label overclaims).
5. **Calibrated natural prose & paragraph-level traceability**: Block promotional hype, corporate buzzwords, AI signposts, and em-dash overuse while protecting scientific terms (`SCIENTIFIC_WHITELIST`) and legitimate scientific contrasts (`并非因果，而是相关`); export `side_by_side_review.md` for researcher sign-off.

## Audience Selection Decision Table

| Reader & Task Signal in `/input/task.md` | Target `--audience` | Acronym Expansion | Display Refs | Default Boundary Policy |
| --- | --- | --- | --- | --- |
| General public, science media, WeChat/press article | `public_science` | Mandatory on first use | Retain or log omission in trace | `primary` |
| Industry R&D partners, licensing, patent commercialization brief | `industry_pitch` | Mandatory on first use | Retain or log omission in trace | `primary` |
| Government, health agency, regulatory/public-health brief | `policy_memo` | Mandatory on first use | Strict retention (`FIGURE_TABLE_CITATION_DROPPED`) | `all` |
| Funding agency, grant proposal abstract/preliminary data | `grant_brief` | Mandatory on first use | Strict retention | `all` |
| Cross-team engineering/clinical technical report | `tech_report` | Optional | Strict retention | `all` |

## Reference Pointers (Load On Demand)

- Output schema and required `/app/output` artifacts: read `references/output_contract_spec.md` before sealing `result.json`.
- 5-audience matrix, Keep/Compress/Omit priorities, and natural prose standards: read `references/audience_matrix_and_style_rules.md` before drafting.
- 4-tier evidence ladder (clinical + engineering/patent), numeric/p-value conservation, and `paragraph_trace.json` schema: read `references/evidence_ladder_and_omission_rules.md`.
- Minimal invocation walkthrough: see `examples/minimal_workflow_example.md`.

## Execution Workflow

Set portable script root once (defaults to `/skills/weekly-skill` inside execution sandboxes):
```bash
SKILL_ROOT="${SKILL_ROOT:-/skills/weekly-skill}"
```

### Step 1: Probe sandbox, initialize guards, and auto-extract source brief & skeletons
```bash
python3 "$SKILL_ROOT/scripts/preflight_probe.py" --out /app/work/env.json
python3 "$SKILL_ROOT/scripts/budget_guard.py" --init
python3 "$SKILL_ROOT/scripts/result_guard.py" --init --case-id "<CASE_ID>" --task-id "translational-science-writing"
python3 "$SKILL_ROOT/scripts/block_indexer.py" --scan /input --out /app/work/input_blocks.json

python3 "$SKILL_ROOT/scripts/translational_fidelity_auditor.py" \
  --source /input/<SOURCE_FILE> \
  --audience <public_science|tech_report|grant_brief|policy_memo|industry_pitch> \
  --extract-source-brief \
  --extracted-source-out /app/work/source_extracted.txt \
  --emit-trace-skeleton /app/output/paragraph_trace.json \
  --emit-glossary-draft /app/output/glossary_lock.json \
  --out-json /app/work/source_brief.json
```
**Done when**: `source_brief.json` confirms the exact extractor (`pdftotext`/`pdfplumber`/`pypdf`/`stdlib_zlib_fallback`/`stdlib_ooxml`/`utf8_text`), source evidence tier, display inventory (`figures`/`tables`), baselines, internal source contradictions, and both editable skeleton files (`paragraph_trace.json`, `glossary_lock.json`) are generated directly from `/input`.

### Step 2: Refine glossary lock, draft adapted deliverable, and finalize paragraph trace
1. Review `/app/output/glossary_lock.json` to lock one canonical target term per key concept and list disallowed inflated synonyms.
2. Draft the complete, ready-to-use adapted manuscript in `/app/output/adapted_article.md` grounded strictly in `/app/work/source_extracted.txt` and `source_brief.json`:
   - Front-load the concrete substantiated finding and why it matters to the target reader;
   - Expand domain acronyms on first appearance per audience profile;
   - Preserve exact numbers, units, `p`-values, comparison baselines, display anchors (`Fig.`/`Table`/`图`/`表`), and experimental/bench-scale boundaries;
   - Write direct, meaning-first scientific prose without promotional hype or defensive disclaimer stacking.
3. Refine the auto-generated `/app/output/paragraph_trace.json` actions (`KEEP` | `COMPRESS` | `OMIT_REDUNDANCY`), `retained_facts`, `omitted_items`, and `keep_or_omit_reason`.

**Done when**: `adapted_article.md`, `glossary_lock.json`, and `paragraph_trace.json` are complete under `/app/output`.

### Step 3: Run deterministic fidelity, display-preservation, evidence-ladder & prose audit
```bash
python3 "$SKILL_ROOT/scripts/translational_fidelity_auditor.py" \
  --source /input/<SOURCE_FILE> \
  --target /app/output/adapted_article.md \
  --audience <public_science|tech_report|grant_brief|policy_memo|industry_pitch> \
  --glossary-json /app/output/glossary_lock.json \
  --trace-json /app/output/paragraph_trace.json \
  --out-json /app/output/translation_fidelity.json \
  --review-md /app/output/side_by_side_review.md
```
*(Pass `--max-words <N>` or `--max-chars <N>` when the prompt gives a hard length cap; pass `--require-figure-refs` or `--strict-structure` when strict display or section enforcement is requested.)*
Fix every blocking error (`ungrounded_target_numbers`, `NUMERIC_UNIT_MISMATCH`, `p_value_violations`, `HALLUCINATED_FIGURE_OR_TABLE_REF`, `FIGURE_TABLE_CITATION_DROPPED`, `CLAIM_LEVEL_ESCALATION`, `UNHEDGED_TRANSLATIONAL_PROMISE`, `MODEL_BOUNDARY_OMITTED`, `unexpanded_acronyms`, `glossary_violations`, `prose_hard_blocks`, `CRITICAL_LIMITATION_OMITTED`) and re-run until `passed == true`.

**Done when**: `translational_fidelity_auditor.py` exits `0` (`passed: true`) and `side_by_side_review.md` matches the final draft.

### Step 4: Seal `result.json` via shell-safe `--patch-file` and run final audits
Write patch JSON to `/app/work/result_patch.json` containing all five contract fields (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`) plus `artifacts`, `metrics`, `metric_provenance`, and `findings`:
```bash
python3 "$SKILL_ROOT/scripts/result_guard.py" --patch-file /app/work/result_patch.json --finalize
python3 "$SKILL_ROOT/scripts/gap_gate.py" --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 "$SKILL_ROOT/scripts/final_audit.py" \
  --dir /app/output \
  --require adapted_article.md \
  --require glossary_lock.json \
  --require paragraph_trace.json \
  --require translation_fidelity.json \
  --require side_by_side_review.md
```
**Done when**: `result_guard.py --validate`, `gap_gate.py`, and `final_audit.py` all exit `0`.

## Failure & Boundary Handling
- **Native PDF/DOCX extraction fallback**: Uses `pdftotext` -> `pdfplumber` -> `pypdf` -> pure-stdlib `zlib` FlateDecode for `.pdf` and pure-stdlib OOXML for `.docx`. Image-only scanned PDFs with zero embedded text layer are reported explicitly in `source_read_metadata.chars == 0` and logged in `result.json.gaps` rather than hallucinated.
- **Lexicon & paraphrasing boundary**: Deterministic regexes + proximity hedging catch explicit escalations, unhedged translational promises, and statistical/unit distortions, but subtle domain analogies and regulatory nuance still require researcher sign-off via `side_by_side_review.md`.
- **Internal source contradictions**: `source_internal_contradictions` flags conflicting values within the source automatically; surface both values and locations in `change_log` and `side_by_side_review.md` rather than picking one silently.
- **Length vs. fidelity conflict**: Compress background and instrument detail via `COMPRESS`; never delete sample size, model boundary, comparison baseline, or primary limitation to fit word count.
- **Confidential or unpublished material**: Honor explicit non-disclosure constraints in `/input/task.md` and log redacted segments in `paragraph_trace.json`.
