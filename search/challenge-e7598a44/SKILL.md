---
name: research-question-search-planner
description: Decompose complex scientific questions into an Element Card and dependency-ordered sub-question DAG (object, mechanism, method, comparator, conditions, time scope, evidence requirement), run two-round controlled retrieval with acronym and co-occurrence expansion over finite offline snapshots, grade each sub-question on a 5-state sufficiency scale (SUPPORTED/PARTIAL/REFUTED/OUTDATED/UNKNOWN), arbitrate conflicting input fixtures honestly, and issue an auditable stopping verdict. Use for research-question-driven literature search tasks.
---

# Research-Question Driven Search (`challenge-e7598a44`)

## 1. Purpose, Scope & Hard Invariants

This Skill prevents single-shot keyword collapse when researchers submit compound scientific questions containing multiple facets (object, mechanism/outcome, method, comparator, operating condition, time scope, and evidence level).

**In Scope**:
- Structured Element Card extraction (leaving unspecified facets explicitly `UNKNOWN` rather than guessing from domain priors).
- Dependency-ordered sub-question decomposition (`SQ0` known-items -> `SQ1` core anchor -> `SQ2..SQ5` parallel facets -> `SQ6` evidence-requirement verification).
- Multi-round offline retrieval over authorized local snapshots (`/input/`): Round 1 core queries -> postings gap diagnosis (metadata vs full text vs acronym-only vs corpus-absent) -> Round 2 controlled expansion.
- Three-perspective evidence tagging (`method_developer`, `empirical_application`, `independent_evaluator`) to support unbiased screening rather than cherry-picking.
- 5-state sub-question sufficiency grading (`SUPPORTED`, `PARTIAL`, `REFUTED`, `OUTDATED`, `UNKNOWN`) and auditable stopping decisions (`STOP_SUFFICIENT_CONVERGED`, `STOP_DIMINISHING_RETURNS`, `STOP_WITH_GAPS_ESCALATE_HUMAN`, `STOP_FINITE_SNAPSHOT_COMPLETE`).

**Out of Scope & Hard Boundaries**:
1. **Finite snapshot only**: Never claim live remote database recall, global completeness, or fabricated human approval.
2. **Input files are ground truth**: Read actual files under `/input/`; never invent data from mismatched domain labels or treat synthetic duplicate/conflicting-opinion fixtures (e.g., `search09`) as pre-baked answers.
3. **Screening, not cherry-picking**: Retain complete PRISMA-consistent counts (`identified == unique + traceable_duplicates`, `unique == included + borderline + excluded`) and preserve exclusion reasons.
4. **Zero silent merges or silent drops**: Distinct formal DOIs with identical titles are kept separate in `ambiguous_candidates`; records lacking abstracts or years are protected in `borderline` (`insufficient_abstract_needs_review`) rather than silently excluded; malformed JSON or source errors are surfaced in `source_load_errors`.

## 2. Deterministic Execution Workflow

All scripts live under `scripts/` relative to this `SKILL.md` and run on pure Python 3.12 standard library (`--network none`).

### Step 1: Preflight & Budget Initialization (`M1` + `M3`)
```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/budget.json --init
python -B scripts/budget_guard.py --state /app/work/budget.json --tick decompose_and_search
```
Inspect `/app/work/env.json` to confirm available files and whether optional PDF extractors (`pdfplumber`/`pypdf`/`pdftotext`) exist. If no PDF backend is present, the pipeline automatically degrades to metadata-only mode and records the limitation explicitly.

### Step 2: Run Question-Driven Planning & Two-Round Retrieval
```bash
python -B scripts/question_search_planner.py --input /input --output /app/output --work /app/work
```
Pass `--question "<explicit question>"` only if `/input/case.json` and `/input/task.md` omit a machine-readable question string.

This command executes the full auditable chain and writes:
- `/app/output/question_search_plan.json` (Element Card, Requirement Ledger, Sub-Question DAG, Round 1/2 queries, term gap diagnosis, per-sub-question 5-state sufficiency, stopping verdict, rollback log)
- `/app/output/subquestion_evidence_matrix.csv` (compact auditable table mapping every sub-question to hit counts, state, and gaps)
- `/app/output/ranked_candidates.csv` (deduplicated candidates with 4-tier relevance, three-perspective tag, matched sub-questions, and arbitration status)
- `/app/output/report.md` (human-readable research brief with Element Card, DAG table, stopping rationale, and gap disclosures)
- `/app/output/audit_log.json` & `/app/output/run_manifest.json` (SHA-256 hash-chained execution log and input file inventory)
- `/app/output/result.json` (complete Harbor `output_contract` deliverable with `case_id`, `results`, `evidence`, `status`, `summary`, `metrics`, `findings`)

### Step 3: Agent Semantic Review & Refinement Checklist

After `question_search_planner.py` completes, inspect `/app/output/question_search_plan.json` and `/app/output/report.md` to verify semantic accuracy before finalizing:

1. **Sub-Question Coverage Audit**:
   - Check every row in `sub_question_sufficiency`. If a specialized dimension (`comparison`, `conditions`, `evidence-type`) is marked `UNKNOWN` or `PARTIAL`, verify against `/input/` raw records whether the corpus truly lacks that evidence or uses an uncaptured domain synonym.
   - If a valid domain synonym exists in `/input/` text but was not captured by automatic acronym mining, document the exact source file and pointer in `evidence` or re-run with refined criteria; **never** downgrade an unverified gap to `SUPPORTED`.
2. **Synthetic / Conflicting Input Fixture Check**:
   - Inspect `status.conflicts` and `evidence.chains[*].external_opinions`. When input fixtures provide conflicting reviewer opinions (e.g., R1 `include` vs R2 `exclude`), verify the record stays in `borderline` (`external_reviewer_conflict->escalate_human`) with both raw opinions preserved—never silently pick one opinion as ground truth.
3. **Stopping Verdict Calibration**:
   - Confirm `status.stopping_verdict.decision` aligns strictly with evidence:
     * `STOP_SUFFICIENT_CONVERGED`: All sub-questions `SUPPORTED`, zero `source_load_errors`, zero unresolved conflicts, Round 2 marginal gain = 0.
     * `STOP_DIMINISHING_RETURNS`: Round 2 marginal gain = 0, no `UNKNOWN` gaps or load errors, only `PARTIAL` items bounded by finite snapshot.
     * `STOP_WITH_GAPS_ESCALATE_HUMAN`: Any `UNKNOWN` sub-question, malformed/failed source snapshot, or escalated reviewer/identity conflict exists.
     * `STOP_FINITE_SNAPSHOT_COMPLETE`: Expansion added hits and all local snapshot records have been exhausted cleanly.
4. **Output Contract Alignment**:
   - Verify `/app/output/result.json` populates every field requested by `/input/case.json`'s `output_contract` and preserves exact `case_id`. Check `result.json:contract_coverage` to confirm no required field fell back unexpectedly to `not_produced`.

### Step 4: Grounding & Final Audit (`M5` + `M6`)
```bash
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require question_search_plan.json --require subquestion_evidence_matrix.csv --require ranked_candidates.csv --require report.md
```
If `gap_gate.py` or `final_audit.py` exits non-zero, resolve the flagged grounding mismatch or missing artifact before finishing.

## 3. Exception Handling & Degraded Modes

| Failure / Edge Case | Required Pipeline & Agent Behavior |
| --- | --- |
| **Empty or zero-hit sub-question** | Mark sub-question `evidence_state: "UNKNOWN"`, list exact unmatched terms in `term_diagnosis`, trigger `STOP_WITH_GAPS_ESCALATE_HUMAN`. Never fabricate papers. |
| **Malformed JSON / unreadable file** | Log file path and exception in `status.source_load_errors` and `status.gaps` (`SOURCE_SNAPSHOT_LOAD_FAILED`). Keep valid `[]` distinct as a zero-error empty response. |
| **Missing abstract or publication year** | Protect record from silent exclusion (`insufficient_abstract_needs_review` / `MISSING:abstract` or `MISSING:year`) and route to `borderline`. |
| **Same title with distinct formal DOIs** | Block automatic title merge; keep both works separate and emit pair into `status.ambiguous_identity_candidates` for human arbitration. |
| **Conflicting reviewer opinions in input** | Preserve all raw opinions in `evidence`, tag `CONFLICT:needs-human-arbitration`, hold in `borderline`. |
| **No PDF extraction library installed** | Emit explicit warning in `run_manifest.json` and `status.warnings`; complete metadata-level retrieval honestly without claiming full-text verification. |

## 4. References & Examples

- Detailed decomposition rules, 5-state rubric, and stopping decision matrix: [references/question_decomposition_protocol.md](references/question_decomposition_protocol.md)
- Output contract schema and artifact inventory: [references/output_contract_spec.md](references/output_contract_spec.md)
- Illustrative input/output examples (marked as examples, not formal case answers): [examples/sample_input_case.json](examples/sample_input_case.json), [examples/sample_output_schema_example.json](examples/sample_output_schema_example.json)
