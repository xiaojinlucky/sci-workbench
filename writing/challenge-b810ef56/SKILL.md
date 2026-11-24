---
name: literature-evidence-driven-writing
description: >-
  Use when drafting, synthesizing, or auditing literature-grounded scientific prose
  requiring a verifiable three-part deliverable: Atomic Claims List -> Evidence Matrix
  with verbatim excerpts and locators -> Calibrated Draft. Enforces a strict 5-state
  verdict rubric (SUPPORTED, PARTIALLY_SUPPORTED, CONTRADICTED, UNVERIFIABLE_ABSENT,
  EXISTENCE_ONLY_NOT_SUPPORTING), blocks correlation-to-causation overclaims per cited
  source design, requires explicit synthesis of conflicting evidence and applicability
  conditions instead of paper-by-paper summary, and outputs the full 5-field structured
  output_contract result.json.
---

# Literature-Evidence-Driven Writing Skill

Target task: `evidence-driven-writing` (Literature-Evidence-Driven Writing). The goal is not verbose prose, but **traceable, evidence-calibrated synthesis**: every substantive claim must resolve to a verifiable verbatim excerpt, locator, study design, and matching epistemic strength.

## Trigger Boundaries

- **Trigger when**: the user provides literature files (full texts, abstracts, structured notes, or references) and asks to synthesize a literature-backed argument, related work, evidence table, or calibrated manuscript section.
- **Hard Red Lines**:
  - Never deliver a prose draft alone without `claims_list.json`, `evidence_matrix.md`, `evidence_audit.json`, and 5-field `result.json`.
  - Never equate "abstract-only" or "missing source" with fabrication (`UNVERIFIABLE_ABSENT`), and never upgrade an abstract-only snippet to full-text proof.
  - Never upgrade an observational/correlational study into causal wording (`proves`, `causes`, `drives`) — even if another paper in the same corpus used an RCT!
  - Never write a mechanical paper-by-paper summary (`Paper A found... Paper B found...`) when sources diverge; synthesize around the research question, explicitly reconciling applicability conditions and unresolved conflicts.

## Mandatory Three-Part Delivery Architecture

1. **Stage 1 — Atomic Claims List (`claims_list.json`)**:
   - Decompose the argument into numbered atomic claim units (`C01`, `C02`, ...), each tagged with `claim_strength` (`causal` | `associative_correlational` | `conditional_scoped` | `consensus_broad` | `descriptive`), `polarity`, `conditions`, and inline `citations`.
2. **Stage 2 — Evidence Matrix (`evidence_matrix.md` & `evidence_audit.json`)**:
   - Scan all `/input` literature files (`.md`, `.xml`/`.html` EuropePMC JATS, `.docx`, `.pdf` via `pdftotext` + stdlib content-stream fallback, `.txt`, `.json`) to extract verifiable `verbatim_excerpt` snippets with exact `file#page=N:section:line` locators, `source_type` (`primary_research` | `review` | `guideline` | `preprint` | `abstract_only`), and `study_design` (`rct_or_intervention` | `observational_correlational` | `meta_analysis` | `in_vitro_preclinical`).
   - Apply **outcome-aware polarity equivalence normalization** (`reduced mortality` / `lower error` / `decreased overpotential` == `improved survival` / `higher accuracy`), **double-negation protection** (`cannot be excluded`, `not without benefit`), and **concessive-clause qualification protection** (`Although observational, ...` preserves explicit scope rather than flagging missing qualifiers or flipping main-clause polarity).
   - Grade every claim under the **Strict 5-State Rubric** (see `references/evidence_grading_and_synthesis.md`):
     - `SUPPORTED`
     - `PARTIALLY_SUPPORTED` (e.g., observational paper cited with causal verb, or narrow applicability condition generalized universally)
     - `CONTRADICTED` (cited source reports opposite direction/null result without conflict framing)
     - `UNVERIFIABLE_ABSENT` (source missing from `/input` or abstract-only — never labeled fabricated)
     - `EXISTENCE_ONLY_NOT_SUPPORTING` (source exists in `/input`, but its content does not support the claim)
3. **Stage 3 — Calibrated Synthesis Draft (`draft.md`)**:
   - Organize around the active research question using **CNS Narrative + Press-Release Principle** (`references/academic_writing_standards.md`): Problem → Consensus vs. Conflict Axis → Condition-Bounded Mechanism/Synthesis → Open Gaps.
   - Match prose modality to each cited paper's study design, explicitly explain why divergent studies disagree (experimental conditions, cohorts, endpoints), and eliminate AI clichés and self-undermining defensive filler.

## Runtime Execution Workflow (Offline Linux Harbor)

All scripts run on Python 3.12+ stdlib with zero third-party packages.

### Step 1: Preflight, Budget, Result Init & Corpus Index
```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "evidence-driven-writing"
python3 -B /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/blocks.json
```

### Step 2: Extract Source Evidence & Draft Synthesized Prose
1. Read `/input/case.json`, `/input/task.md`, all literature files under `/input`, `references/academic_writing_standards.md`, and `references/evidence_grading_and_synthesis.md`.
2. Identify each paper's study design, polarity, sample/condition boundaries, and verbatim support passages before drafting.
3. Write `/app/output/draft.md` directly addressing the research question, integrating divergent studies by applicability condition, keeping observational claims strictly hedged, and citing exact source keys/labels.

### Step 3: Run Strict 5-State Evidence & Conflict Synthesis Guard
```bash
python3 -B /skills/weekly-skill/scripts/evidence_driven_writer_guard.py \
  --draft /app/output/draft.md \
  --corpus-dir /input \
  --input-dir /input \
  --out-dir /app/output \
  --case-id "<CASE_ID>" \
  --strict
```
- Automatically consumes `/input/case.json` (`case_id`, dynamic `output_contract` fields) and `/input/task.md` constraints via `writing_io_and_invariants.load_case_contract`.
- Inspect `/app/output/evidence_matrix.md` and `/app/output/evidence_audit.json`.
- Resolve every `PARTIALLY_SUPPORTED`, `CONTRADICTED`, `EXISTENCE_ONLY_NOT_SUPPORTING`, and `conflict_synthesis_passed: false` finding in `/app/output/draft.md`, then re-run until clean (genuine `UNVERIFIABLE_ABSENT` items where `/input` lacks full text remain explicitly logged in `gaps` and marked `[EVIDENCE_GAP]` in prose).

### Step 4: Final Grounding & Package Audit
```bash
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require draft.md --require claims_list.json --require evidence_matrix.md --require evidence_audit.json
```

## Output Contract (Strict structured 5-Field Preservation)

See `references/output_contract_spec.md`. `/app/output/result.json` MUST preserve all 5 structured `output_contract` fields verbatim:
- `case_id`
- `deliverable`
- `preserved_facts`
- `evidence_map`
- `change_log`
