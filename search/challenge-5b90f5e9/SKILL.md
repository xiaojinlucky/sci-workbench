---
name: systematic-screening-prisma
description: Execute auditable all-record systematic literature screening over finite snapshots: freeze versioned PICO/eligibility protocols with SHA-256 drift detection, deduplicate multi-source and preprint-to-VoR exports with identity-safe guards, assign clause-linked reason codes with exact sentence quotes, separate fulltext-unavailable and missing-metadata records into human arbitration queues, compute dual-reviewer agreement/disagreement taxonomy without averaging away conflicts, and enforce PRISMA 2020 accounting conservation.
---

# Systematic Literature Search & Screening (`challenge-5b90f5e9`)

## 1. Mission & Non-Negotiable Invariants

This skill enforces a reproducible, third-party-auditable systematic screening pipeline (**Protocol Freeze -> Identity-Safe Deduplication -> Clause-Bound All-Record Screening -> Dual-Reviewer Conflict Classification -> PRISMA 2020 Conservation Closure**) over finite local bibliographic snapshots.

### Six Epistemic Invariants
1. **Protocol Version Freeze & Drift Guard**: Hash the active eligibility protocol (`protocol_sha256`) before screening. If prior screening decisions or an earlier protocol version are present in `/input`, flag every affected record in `criteria_drift_audit` rather than mixing decisions from incompatible protocol versions.
2. **Identity-Safe Deduplication & Preprint-to-VoR Upgrade**: Merge exact DOI/PMID/arXiv duplicates and verified preprint-to-Version-of-Record pairs while preserving `alt_dois`, `found_in_sources`, and `deduplication_log`. Never auto-merge two records carrying distinct non-preprint DOIs; route ambiguous title collisions into `near_duplicate_review_queue` for human verification.
3. **Screening, Not Cherry-Picking (Clause + Exact Quote)**: Screen **100%** of canonical records against every frozen inclusion/exclusion clause. Never output generic "not relevant" exclusions. Every decision must carry a structured `reason_code`, `triggered_clause`, and a verbatim sentence `evidence_quote` anchored to the record's own title, abstract, or full text.
4. **Honest Routing of Missing Metadata & Unretrieved Full Text**:
   - A potentially eligible record missing a protocol-bounded field (such as publication year under an active `[year_min, year_max]` bound) or lacking an abstract must route to `arbitration_queue` (`BORDERLINE_MISSING_REQUIRED_METADATA` / `BORDERLINE_NEEDS_ARBITRATION`), never fall through silently into `include`.
   - A record passing title/abstract screening whose full text is marked unavailable (`fulltext_available: false` or `retrieval_status: not_retrieved`) must be counted under PRISMA `n_not_retrieved` (`FULLTEXT_NOT_RETRIEVED`), never misclassified as an eligibility exclusion.
5. **Preserve Dual-Reviewer Disagreements Without Averaging**: Classify reviewer conflicts (`include_vs_exclude`, `include_vs_borderline`, `reason_code_disagreement`), compute raw agreement and Cohen's Kappa, route all unresolved disagreements to `arbitration_queue`, and explicitly tag synthetic reviewer fixtures in inputs as `input_fixture_not_final_human_verdict`.
6. **PRISMA 2020 Arithmetic Conservation Closure**: All four PRISMA 2020 stage equations must close exactly (`prisma_flow.balanced == true`). Never publish screening counts when any equation fails.

## 2. Information Hierarchy & References

Consult on demand (paths relative to this `SKILL.md`):
- `references/output_contract_spec.md` — schema for `/app/output/result.json`, `prisma_screening_ledger.json`, and `prisma_screening_ledger.md`.
- `references/prisma_clauses_and_arbitration_protocol.md` — structured reason-code dictionary, PICO clause evaluation precedence, conflict taxonomy, and protocol-drift remediation procedure.
- `references/search_methodology_protocol.md` — screening-not-cherry-picking principle and finite-snapshot honesty rules.

## 3. Step-by-Step Execution Workflow

Run all commands from the directory containing this `SKILL.md` (or resolve `scripts/` relative to it).

### Step 1: Preflight Environment & Budget Initialization
```bash
python -B scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python -B scripts/budget_guard.py --state /app/work/.budget_state.json --init
```
**Completion criterion**: `/app/work/env.json` confirms readable `/input` snapshots (JSON, Crossref/EuropePMC/OpenAlex, RIS, BibTeX, CSV, XML, PDF) and writable `/app/output`.

### Step 2: Execute Deduplication, Clause Screening & PRISMA Closure Engine
```bash
python -B scripts/systematic_screener.py --input /input --output /app/output --work /app/work
```
What this engine deterministically performs:
1. **Protocol Freezing & Drift Check**: Normalizes eligibility rules (`required_terms`, `pico_clauses`, `exclude_terms`, `exclude_designs`, `year_min/year_max`, `allowed_languages`, `require_fulltext`), hashes the frozen protocol (`protocol_sha256`), and audits any supplied `prior_decisions` against current decisions for protocol drift.
2. **Identity-Safe Deduplication**: Deduplicates all identified records (`n_identified`), logs every merge in `deduplication_log` with `match_method`, upgrades preprints to published VoR retaining `alt_dois`, and separates blocked/ambiguous pairs into `near_duplicate_review_queue`.
3. **All-Record Clause Evaluation**: Screens every canonical record (`n_screened`) in deterministic priority order:
   - Reviewer conflict check (never averages away conflict)
   - Explicit exclusion checks that hold even when optional fields are missing (language mismatch, excluded study design, explicit exclusion keyword, out-of-window known year)
   - Missing abstract / missing required protocol-bounded year check (`BORDERLINE_NEEDS_ARBITRATION` / `BORDERLINE_MISSING_REQUIRED_METADATA`)
   - PICO / required-term inclusion clause check (full match -> `INC_MATCH`, partial match -> `BORDERLINE_PARTIAL_CLAUSE_MATCH`, zero match -> `EXC_WRONG_POPULATION_OR_OBJECT`)
   - Full-text retrieval boundary (`FULLTEXT_NOT_RETRIEVED` when full-text assessment is required but unavailable)
4. **Dual-Reviewer Agreement & Conflict Ledger**: Computes observed agreement, Cohen's Kappa (when >=2 dual-rated records exist), classifies conflict types, preserves synthetic-fixture provenance labels, and constructs `arbitration_queue`.
5. **PRISMA 2020 4-Stage Accounting & Dual Reports**: Verifies:
   - `n_identified - n_dedup - n_automation_excl - n_awaiting_screen == n_screened`
   - `n_screened - n_screen_excl == n_sought`
   - `n_sought - n_not_retrieved == n_assessed`
   - `n_assessed - n_elig_excl - n_awaiting_elig == n_included`
   Writes `/app/output/prisma_screening_ledger.json`, `/app/output/prisma_screening_ledger.md` (with ASCII PRISMA flow summary), and `/app/output/result.json`.

**Completion criterion**: `prisma_flow.balanced` is `true`, every canonical record has a decision + reason_code + verbatim quote, and all 3 deliverables exist.

### Step 3: Human Arbitration & Fixture Provenance Review (Host Agent)
Inspect `/app/output/prisma_screening_ledger.json` and `/app/output/prisma_screening_ledger.md`:
1. Confirm every item in `arbitration_queue` clearly states **why** human sign-off is mandatory (reviewer disagreement, missing abstract, missing protocol-bounded year, or partial clause match).
2. Confirm synthetic duplicate exports or synthetic reviewer opinions from `/input` are explicitly flagged as input fixtures rather than fabricated human reviews.
3. Confirm every excluded record quotes verbatim text from its own title/abstract and contains zero generic "irrelevant" labels.

### Step 4: Grounding Verification & Final Deliverable Gate
```bash
python -B scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python -B scripts/final_audit.py --dir /app/output --result-file result.json --require prisma_screening_ledger.json prisma_screening_ledger.md
```
**Completion criterion**: `final_audit.py` exits `0` with `passed: true`.

## 4. Failure Modes & Edge-Case Matrix

| Edge Case | Required Behavior |
| --- | --- |
| Potentially eligible record with `year: null` under active `year_min`/`year_max` | Route to `arbitration_queue` (`BORDERLINE_MISSING_REQUIRED_METADATA`, `missing_critical_fields: ["year"]`); never auto-include. |
| Record with `year: null` when protocol has NO date bound | Do NOT trigger missing-year arbitration; screen on remaining clauses. |
| Clearly excluded study (e.g., editorial) also missing year | Exclude cleanly (`EXC_WRONG_DESIGN`) with verbatim quote; do not clog arbitration queue. |
| Title/abstract eligible record with unavailable full text | Count under `n_not_retrieved` (`FULLTEXT_NOT_RETRIEVED`), never under `n_screen_excl` or `n_elig_excl`. |
| Two records with identical/similar title but distinct non-preprint DOIs | Keep separate; log into `near_duplicate_review_queue` without silent merging. |
| Dual reviewers disagree (`include` vs `exclude`) | Preserve both opinions verbatim, classify conflict type, route to `arbitration_queue`; never average or pick one silently. |

## 5. Hard Boundaries
- Finite local snapshot screening only; never claim live database recall, global completeness, or fabricated human adjudication.
- Never publish unbalanced PRISMA flow counts (`prisma_flow.balanced == false`).
