---
name: manuscript-structural-logic-optimizer
description: Six-dimension source-grounded manuscript structural and logical diagnosis producing paragraph-anchored executable editing action cards (JSON/Markdown). Detects RQ-to-conclusion drift, chronological literature laundry lists, unreconciled same-metric contradictions, cross-section redundancy, causal scope escalation, and truncation while enforcing an anti-style-bias firewall and avoiding unsolicited full rewrites.
---

# Manuscript Structural and Logical Optimization (`paper-structure-optimization`)

Use this Skill to audit a scientific manuscript's macro-structure and argumentative logic and deliver an **editable, paragraph-anchored editing action plan** (`structural_diagnosis.json`, `editing_action_plan.md`, `result.json`). Follow the official `Concrete structural review scope`: ground every finding in the actual input manuscript, cover all six structural dimensions, avoid unsolicited full rewrites, and never substitute the author's scientific stance.

## Core Principles & Non-Negotiable Guardrails

1. **Read Actual Files First**: Inspect `/input/case.json`, `/input/task.md`, and the actual manuscript files (`.md`, `.tex`, `.txt`, `.docx`, `.pdf`). Never invent defects or rely on erroneous historical domain labels.
2. **Six Mandatory Structural Dimensions**:
   - **D1. Research Question & Contribution Alignment**: Verify Intro RQs/contributions close cleanly across Methods, Results, and Conclusion; flag unfulfilled promises and conclusion drift.
   - **D2. Section Structure & Narrative Order**: Audit IMRaD flow, misplaced methods/background in Results, trial-and-error chronological logs (apply Press-Release Argument-First principle), and **chronological literature laundry lists** (multi-paper year-by-year summaries lacking comparative dimensions).
   - **D3. Argument Chain (`Claim -> Evidence -> Warrant`)**: Extract broken inferential links (`therefore/thus` without premise), unsupported quantitative superiority claims, and **unreconciled same-metric contradictions** across paragraphs or cited works.
   - **D4. Paragraph Relations & Cross-Section Redundancy**: Locate sentence/paragraph-level duplication via lexical and n-gram overlap while distinguishing **legitimate Abstract summary restatements** from redundant cross-section repetition; flag overloaded monolithic paragraphs.
   - **D5. Conclusion Boundary & Epistemic Scale**: Catch observational/correlational results escalating into causal claims (`proves`, `causes`, `drives`), out-of-sample universal extrapolations, missing explicit Limitations, and defensive self-undermining phrasing in primary claims.
   - **D6. Structural Integrity & Anomalies**: Explicitly report missing canonical sections (`[MISSING]`), inline citations lacking a References section, and abrupt mid-sentence or bracket-imbalanced truncation.
3. **Executable Editing Action Card Protocol**:
   - Every finding MUST carry exact `(section, paragraph_id, line_range, quote_excerpt)` (or explicit `MISSING_SECTION`/`MISSING_REFERENCES` marker) and map to one concrete verb from the closed grammar:
     `MOVE` | `MERGE` | `SPLIT` | `NARROW_CLAIM` | `ADD_BRIDGE` | `REGROUP_BY_DIMENSION` | `DEFER_TO_AUTHOR`.
   - **Zero vague advice**: Never emit generic slogans ("strengthen logic", "improve flow").
   - **Anti-style-bias firewall**: Consult `references/structural_review_protocol.md`. Never flag subjective prose preferences, passive voice, or venue-specific formatting choices as structural errors.
   - **Author sovereignty**: When reconciling conflicting studies or narrowing an overclaimed conclusion requires scientific judgment, use `DEFER_TO_AUTHOR` or `NARROW_CLAIM` with `Accept / Partially Accept / Reject` decision options. Do not deliver an unsolicited full rewrite.

## Runtime Execution Workflow

### Step 1: Preflight, budget & skeleton initialization
```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/result_guard.py --init --case-id "<CASE_ID>" --task-id "paper-structure-optimization"
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

### Step 2: Run deterministic six-dimension structural engine
```bash
python3 /skills/weekly-skill/scripts/structural_logic_engine.py \
  --input-dir /input \
  --out-dir /app/output
```
(Or pass `--manuscript /input/<FILE> --case-id "<CASE_ID>" --out-dir /app/output` when targeting a specific manuscript file.)

### Step 3: Deep semantic verification & synthesis (Agent pass)
1. Read `references/structural_review_protocol.md`, `references/academic_writing_standards.md`, and `references/output_contract_spec.md`.
2. Review every automated card in `/app/output/structural_diagnosis.json` against the exact cited lines in `/input/`. Remove any false positives (e.g., an intentional journal-required Methods-after-Discussion order).
3. Add domain-level logical breaks that lexical rules cannot catch (subtle circular reasoning, misaligned control-group comparisons, implicit assumptions in multi-step derivations), strictly formatted with `(section, paragraph_id, line_range, quote_excerpt)` and closed-set edit verbs.
4. Update `/app/output/editing_action_plan.md`, `/app/output/structural_diagnosis.json`, and `/app/output/result.json` so all three stay 100% consistent and respect `preserved_facts` (zero mutated numbers, units, p-values, sample sizes, or citations).

### Step 4: Grounding gate & final Harbor audit
```bash
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output \
  --require structural_diagnosis.json \
  --require editing_action_plan.md
```

## Exception & Boundary Handling

- **Short excerpt or partial manuscript**: Report missing canonical sections honestly under `D6_structural_integrity_anomalies` and `gaps` without fabricating missing sections.
- **PDF/DOCX input**: `writing_io_and_invariants.py` parses `.docx` via pure stdlib OOXML XML and `.pdf` via `pdftotext`; if `pdftotext` is unavailable in an air-gapped environment, record `degraded_mode` in `gaps` and `limitations` without guessing text.
- **No unsolicited full rewrite**: Editable JSON + Markdown action plan suffice per official scope; if `task.md` explicitly requests targeted paragraph rewrites for specific broken bridges, apply them only to those anchored paragraphs and run `writing_io_and_invariants.diff_invariants()` to prove fact conservation.
