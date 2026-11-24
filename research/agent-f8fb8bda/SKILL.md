---
name: deep-research-orchestrator
description: Bounded multi-source deep research orchestrator that decomposes scientific questions into dependency-ordered sub-question DAGs, enforces 6-tier material-access boundaries (blocking discovery snippets and capping abstract-only claims), preserves sub-question-scoped opposing evidence with passage locators, logs access barriers without stalling, and audits final briefs against overclaims.
---

# Deep Research Agent (`deep-research-orchestrator`)

Turn a multi-step scientific research question and a finite local multi-source corpus (OpenAlex, Crossref, EuropePMC JSON/JATS XML, arXiv Atom XML, PDFs, BibTeX, CSV, Markdown) into an auditable, resumable, passage-grounded research synthesis without fabricated coverage or smoothed-over contradictions.

## Core Methodology (Four Non-Negotiable Principles)

1. **6-Axis Question Decomposition & Shortest Evidence Chain**
   - Never rewrite a prompt into flat keywords. Break every research question into typed sub-questions (`SQ-1..SQ-k`) specifying: `research_object`, `mechanism_or_variable`, `method_axis`, `comparison_or_baseline`, `time_or_scope_window`, and `evidence_requirement`.
   - Separate prerequisite baseline sub-questions (`wave_1_prerequisite_baseline`) from parallel comparison/conflict audits (`wave_2_parallel_*`) and final bounded synthesis (`wave_3_bounded_synthesis`).
   - Attach deduplicated retrieval queries and explicit stopping rules (`no_new_signal` across consecutive refinements or snapshot exhaustion).

2. **6-Tier Material-Access Ceiling & Anti-Snippet Guard**
   Every source is explicitly graded before any claim can cite it:
   | Tier | Meaning | Allowed Claim Strength |
   |---|---|---|
   | `fulltext_pdf` / `fulltext_xml` / `fulltext_local_text` | Verified page or section text inspected | Full `FACT_OBSERVED` / `SUPPORTED` / `REFUTED` with `#page=N` or `#sec=...` locator |
   | `abstract_only` | Only title + abstract available | Capped at `PARTIAL` / `INFERENCE_BOUNDED`; **never** proves unshown methods, controls, or subgroup statistics |
   | `discovery_only` | Search snippet or title/DOI metadata only | **Strictly forbidden** as synthesis evidence; retained for discovery provenance only |
   | `blocked_or_unavailable` | Paywall, HTTP 403/429, CAPTCHA, SSO, or empty scan PDF | Logged once in `access_log` & `gaps`, halted cleanly without bypass; **never** treated as "no studies exist" |

3. **Three-Way Epistemic Separation & Side-by-Side Conflict Preservation**
   - Separate every statement into `FACT_OBSERVED` (directly observed in inspected full text), `INFERENCE_BOUNDED` (cautious cross-paper synthesis or abstract-only observation), and `UNVERIFIED_OR_GAP` (missing full text, blocked source, or prospective proposal).
   - Never smooth over opposing or null findings with boilerplate ("remains controversial"). Preserve supportive and opposing/null passages side by side in `conflict_ledger` with exact locators, study populations, and method differences.
   - Separate prospective/planned protocols from completed empirical experiments.

4. **True Resumable Execution & Evidence-Grounded Gaps**
   - Persist `/app/work/checkpoint_state.json` (keyed by corpus `input_fingerprint`) plus per-stage snapshots (`stage1_ingest.json`, `stage2_dedup.json`, `stage3_dag.json`) and `tool_call_log` at every stage. Passing `--resume` reloads verified intermediate outputs and skips completed stages automatically without re-parsing unchanged corpora.
   - Distinguish three concrete gap types (`unresolved_evidence_conflict`, `access_or_retrieval_coverage_gap`, `bounded_subquestion_evidence_gap`) instead of generic closing caveats. Never claim global exhaustiveness ("no prior studies exist anywhere").

## Runtime Contract & Budget Discipline

- **Inputs**: `/input/case.json`, `/input/task.md`, and local multi-source literature files. Read real files directly; never guess content from filenames or folder labels.
- **Work & Outputs**: Intermediate state and stage checkpoints in `/app/work/`; final deliverables in `/app/output/`:
  - `/app/output/result.json` (compact summary contract referencing `deep_research_ledger.json`, containing `case_id`, `status`, `sources`, `sub_questions_dag`, `sources_matrix`, `evidence_matrix`, `conflict_ledger`, `research_gaps`, `access_log`, `tool_call_log`, `uncertainty`, `metrics`, `metric_provenance`, `gaps`)
  - `/app/output/deep_research_ledger.json` (full passage-level evidence ledger)
  - `/app/output/deep_research_brief.md`
- **Budget Guard**: Run the deterministic Python orchestrator first to produce a verified baseline deliverable early within bounded runtime budgets, then refine the Markdown brief and re-verify in read-only strict mode.

## Step-by-Step Execution Workflow

All script paths below are relative to the directory containing this `SKILL.md` (resolve `scripts/...` against the installed skill root).

### Step 1: Probe Environment & Initialize Budget Guard
```bash
python3 scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 scripts/budget_guard.py --state /app/work/.budget_state.json --init
```

### Step 2: Run Deterministic Multi-Source Deep Research Orchestrator (Resumable)
```bash
python3 scripts/deep_research_orchestrator.py \
  --input /input \
  --output /app/output \
  --work /app/work
```
To resume an interrupted run against the same corpus snapshot without repeating Stages 1–3:
```bash
python3 scripts/deep_research_orchestrator.py \
  --input /input \
  --output /app/output \
  --work /app/work \
  --resume
```
The orchestrator invokes `block_indexer.py` automatically to index large local files, ingests OpenAlex/Crossref/EuropePMC (including `PPR*` preprints and JATS XML)/arXiv/PDF/BibTeX/CSV/MD files, deduplicates preprint-to-VoR pairs without losing full-text passages, builds clause-aware 6-axis sub-question DAGs with stopword-filtered executable retrieval queries, maps verbatim passage locators while excluding PDF mastheads/author lines from polarity scoring, persists stage checkpoints, and emits `deep_research_ledger.json`, `deep_research_brief.md`, and `result.json`.

### Step 3: Domain Synthesis Refinement (Agent Reasoning Pass)
Before finalizing `/app/output/deep_research_brief.md`, inspect `deep_research_ledger.json` and refine the narrative synthesis using `references/evidence_and_epistemic_protocol.md`:
- Ensure every sub-question (`SQ-1..SQ-k`) is explicitly answered with inline `[SRC-xx]` + locator citations.
- Compare **actual functional operations and study conditions** across papers rather than listing tool or method names.
- Explicitly explain *why* conflicting studies diverge (e.g., model organism vs human cohort, substrate/electrolyte difference, abstract-only vs full-text reporting).
- Keep every prospective protocol clearly tagged as uncompleted/planned.
- Disclose any `API_PAGINATION_TRUNCATED`, `PDF_LOW_TEXT_PAGE`, `abstract_only`, or `blocked_or_unavailable` access barriers in the Uncertainty section.

### Step 4: Strict Read-Only Grounding, Citation & Boundary Audit
```bash
python3 scripts/deep_research_orchestrator.py \
  --input /input \
  --verify-report /app/output/deep_research_brief.md \
  --out-json /app/work/citation_audit.json \
  --strict
python3 scripts/gap_gate.py \
  --verify-json /app/output/result.json \
  --corpus /input \
  --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py \
  --dir /app/output \
  --result-file result.json \
  --require deep_research_ledger.json \
  --require deep_research_brief.md
```
`--verify-report --strict` runs strictly read-only (never overwrites `deep_research_brief.md` or `result.json`), validates **every** `[...]` citation token against grounded source/record IDs (catching fabricated IDs such as `[W99999]` or `[Smith2025]`), rejects `discovery_only`/`blocked_or_unavailable` citations in evidence prose while exempting explicit exclusion/access-barrier disclosures, and flags abstract over-promotion or global overclaims. Remove any flagged violations and re-run Step 4 until clean.

## Exception Handling

- **Empty or unreadable PDFs**: Recorded as `PDF_PARSE_EMPTY` or `PDF_LOW_TEXT_PAGE` in `gaps` and `access_log`; do not hallucinate page text.
- **Paywall / CAPTCHA / SSO / 403 / 429 barriers**: Halt that source cleanly, record once in `access_log`, keep all verified evidence from accessible sources, and mark affected sub-questions as partial. Never attempt to bypass access controls.
- **Truncated API snapshots** (OpenAlex `meta.count > loaded` or EuropePMC `hitCount > loaded`): Record `API_PAGINATION_TRUNCATED` and explicitly state the denominator ceiling.
- **Zero parseable sources**: Emit honest `status="partial_with_gaps"` / `EMPTY_CORPUS` gap rather than fabricated prose.

## References & Examples
- `references/output_contract_spec.md` — Required output fields and verification checklist.
- `references/evidence_and_epistemic_protocol.md` — 6-tier access rules, epistemic tagging, and conflict presentation standards.
- `examples/minimal_example.md` — Illustrative input/command/output walkthrough (clearly marked as example only).
