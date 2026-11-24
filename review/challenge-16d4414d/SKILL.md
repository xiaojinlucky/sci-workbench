---
name: logical-argument-audit
description: Source-bound scientific logic, argument-chain, causal-boundary, cross-section consistency, and multi-round revision-closure auditor. Separates author claims from cited prior views and controls/hypotheses, enforces five-gate admissibility and ACL H1-H17 fairness, and outputs line-anchored revision plans.
---

# 论文逻辑与论证审查 (challenge-16d4414d)

Source-bound scientific logic and argument-chain auditor designed for offline sandbox execution. Reconstructs explicit `Claim -> Evidence -> Warrant -> Boundary` chains from the actual supplied manuscript (including wrapped multi-line PDF paragraphs), separates author claims from cited prior literature and genuine QC-control/hypothesis/limitation clauses (without suppressing comparative author claims such as `Compared with the control group, ...`), detects six concrete logical breakpoints (including polarity-aware B3 direction/significance checks and multi-round revision closure mapping), and enforces the five-gate admissibility rule plus an executable ACL H1-H17 fairness firewall (`--audit-findings`).

## Scope & Non-Goals
- **In scope**: Claim extraction and speaker-identity separation, argument-chain reconstruction (`Claim -> Evidence -> Warrant -> Boundary`), observational-to-causal overclaim detection, Introduction-promise vs Methods/Results delivery checks, Abstract/Results/Conclusion numerical and effect-direction conflict checks, scale/sample over-extrapolation checks, scope/limitation checks, and multi-round revision closure mapping (`prior comment -> author rebuttal -> v1/v2 diff verification` with scope isolation of new issues).
- **Out of scope**: Language polishing/copyediting, plagiarism or misconduct adjudication, bibliography formatting rules, raw-data statistical recomputation, and journal submission strategy.

## Core Protocols & Guardrails
1. **Three-Way Sentence Identity Separation (Zero False Contradictions)**: Before raising any overclaim or contradiction, classify every sentence into:
   - `author_claim`: Author's own substantive assertion.
   - `cited_prior_view`: Attribution to prior literature (`\cite`, `[n]`, `(Author, Year)`, "prior studies reported", "前人研究认为"). Never flag as author self-contradiction.
   - `explicit_control_or_hypothesis`: Negative/positive control, baseline, ablation, explicit hypothesis, negated causal disclaimer ("cannot prove causality"), or author-disclosed limitation. Never flag as an unintended defect.
2. **Five-Gate Finding Admissibility** (see `references/evidence_and_fairness_protocol.md`): Every finding must satisfy (1) Material Impact, (2) Discrete & Actionable Fix, (3) Source-Bound Provenance (file, section, page/line, verbatim quote `<= 200` chars or `[MISSING: ...]`), (4) Demonstrable Failure Mode, and (5) No Speculative Padding.
3. **Six Logical Breakpoints**:
   - **B1 (`causal_overreach`)**: Strong causal verbs in an observational/correlational design without interventional or causal-identification basis (strictly excluding cited views, controls, hypotheses, and hedged/negated statements).
   - **B2 (`intro_delivery_fracture`)**: Research question/objective explicitly promised in the Introduction not delivered in Methods/Results.
   - **B3 (`cross_section_conflict`)**: Numerical/percentage discrepancy or effect-direction reversal between Abstract, Results, and Conclusion (with rounding tolerance to prevent trivial rounding noise).
   - **B4 (`scale_over_extrapolation`)**: Preclinical/cell-line/animal/single-center findings extrapolated directly into universal clinical efficacy claims.
   - **B5 (`missing_limitations`)**: Absent scope boundaries or limitation disclosure in Discussion/Conclusion—emitted strictly as `suggestion` (`P3`), never as a rejection blocker.
   - **B6 (`revision_closure`)**: When prior reviewer comments, author rebuttal, and manuscript `v1`/`v2` are supplied, map every prior comment to the author's claimed edit and actual `v1 -> v2` diff evidence (`closed`, `partially_closed`, `unclosed_claimed_fixed`, `unclosed_unaddressed`, `declined_with_rationale`), and isolate newly noticed methodological issues outside prior comments so editors can judge scope.
4. **ACL H1-H17 Fairness Firewall**: Never escalate H1-H17 improper critiques (novelty without citation, "not SOTA", null/negative result, method simplicity, narrow domain, unrequested extra experiments, or disclosed limitations) into `critical`/`major` findings. Downgrade or drop with explicit `firewall_check`.
5. **Honest Zero-Defect & Gap Reporting**: If no critical/major defects exist, report `verdict: "no_major_defects_detected"` with honest coverage bounds rather than fabricating issues. If PDF pages have low extractable text (`PDF_LOW_TEXT_PAGE`) or extractors are missing, log structured `gaps` and never invent missing text.

## Dual-Compatible Output Contract (`/app/output/result.json`)
To satisfy both `result_guard.py` / `final_audit.py` runtime checks and domain audit semantics:
- Top-level `status`: MUST be `"ok"` (no blocking gaps) or `"partial_with_gaps"` (gaps or unverified inputs present).
- Top-level `audit_status`: `"completed"` | `"partial"` | `"indeterminate"` | `"failed"`.
- Top-level `verdict`: `"no_major_defects_detected"` | `"defects_detected"` | `"needs_human_review"` | `"incomplete_inputs"`.
- Required metadata: `case_id`, `sources`, `uncertainty`, `artifacts`, `gaps`, `findings`, `argument_graph`, `metrics`, `metric_provenance` (plus `revision_closure` when multi-round files are present).
See `references/output_contract_spec.md` for full field definitions.

## Execution Workflow

### Step 1: Preflight Probe, Budget Init, Skeleton Result & Full Indexing
Run inside `/app/work` (do not skip; guarantees early honest state under budget caps):
```bash
python /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id "challenge-16d4414d"
python /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

### Step 2: Run Deterministic Argument-Chain & Revision-Closure Engine
```bash
python /skills/weekly-skill/scripts/argument_chain_auditor.py \
  --input /input \
  --out-json /app/output/logic_audit.json \
  --out-md /app/output/logic_review_report.md \
  --out-result-json /app/output/result.json
```
Inspect `/app/output/logic_audit.json` and `/app/output/logic_review_report.md`. Verify:
- `sentence_identity_breakdown` confirms cited views and genuine QC controls/hypotheses were separated while comparative author results (`compared with the control/placebo group`) were preserved.
- `argument_graph` captures core claims with `evidence_anchor`, `warrant_risk`, and `boundary_condition`.
- `revision_closure` (if triggered by `v1/v2`, `round1/round2`, `rev1/rev2`, `draft1/draft2`, or `revised` drafts) reconciles every prior comment against `v1 -> v2` diff lines.

### Step 3: Deep Semantic Verification Against Source & References
Read the relevant sections in `/input` directly (never rely solely on heuristic output) and consult:
- `references/evidence_and_fairness_protocol.md` (five-gate admissibility + ACL H1-H17 firewall)
- `references/argument_logic_anchors.md` (six breakpoints + severity calibration + false-positive traps)
- `references/revision_closure_protocol.md` (multi-round rebuttal verification & new-issue scope isolation)
- `references/output_contract_spec.md` (JSON schema and `status`/`audit_status` mapping)

Verify any `needs_human_review` cues (such as B2 lexical promise-delivery cues that may use domain synonyms, or unmatched summary percentages), refine wording of actionable revisions to exact sentence-level edits, and drop any candidate that fails any of the five gates or violates ACL H1-H17.

### Step 4: Enforce ACL H1-H17 Gate, Finalize `result.json`, Grounding & Deliverable Audit
After adding or refining any semantic findings in `/app/output/result.json`, run the executable ACL H1-H17 firewall gate (`--audit-findings`) so any improper H1-H17 critique is automatically downgraded to `suggestion` (`DOWNGRADED_BY_ACL_H<n>`), then finalize and run M5/M6 gates:
```bash
python /skills/weekly-skill/scripts/argument_chain_auditor.py --audit-findings /app/output/result.json --out-json /app/output/result.json
python /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --finalize
python /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json --require logic_audit.json --require logic_review_report.md
```
If `gap_gate.py` flags any ungrounded DOI/arXiv or missing metric provenance, resolve it before exit. If genuine input gaps exist (e.g., scanned PDF pages or truncated sections), preserve them in `gaps` so `result.json` honestly seals with `"status": "partial_with_gaps"` and `"audit_status": "partial"`.

## Offline Extraction & Six-Facet Companion Guarantees
- **4-Tier PDF Cascade**: `review_io_core.py` cascades `pypdf` (two-column reading order) → `pdfplumber` → `pdftotext` → pure-stdlib FlateDecode `BT..ET` fallback (`PDF_STDLIB_FALLBACK_USED`).
- **Real-Journal Precision, B7 Interpretive Tension & Non-Bundling Rule**: Disambiguates clinical/epidemiological noun phrases (e.g., `causes of death`) from causal overclaim verbs, separates `regulatory_guidance` from `research_manuscript`, automatically flags **B7 Interpretive Tension** (95% CI vs p-value mathematical contradictions & AAPC trend-vs-endpoint divergence) as individual line-anchored findings (never bundled into a single suggestion or buried in `uncertainty`), and emits `six_facet_coverage` + `semantic_review_checklist` in both `logic_audit.json` and `result.json`. Step 3 MUST preserve all deterministic `file:L<start>-L<end>` anchors and explicitly ADD substantiated semantic findings rather than only dropping weak candidates.
