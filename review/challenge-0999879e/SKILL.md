---
name: manuscript-quality-audit
description: Full-manuscript six-facet quality, cross-modal figure/table integrity, and publishability auditor with defect-first triage, observational causal-overclaim detection, 128-bit HV-dHash image duplication screening, and ACL H1-H17 fairness guardrails.
---

# Manuscript Quality & Publishability Audit (`challenge-0999879e`)

Deliver a source-bound, line-anchored, actionable audit dossier across all six canonical academic review facets (`fact_verification`, `citation_support`, `method_design`, `statistical_reporting`, `argument_chain`, `academic_norms`), separating verifiable errors from reporting omissions, unverified boundaries, and optional suggestions.

## Scope & Non-Goals
- **In scope**: Overall manuscript quality, IMRaD structural completeness, Abstract-vs-Results numerical and sample-size reconciliation, figure/table caption linkage and panel-sequence integrity, exact/flipped/rotated image duplication screening, multi-system bibliography and retraction checks (numeric `[n]`, LaTeX `\cite{}`, Author-Year), citation-marker vs source-support boundaries, method-design vs statistical-reporting separation, and publication-norm disclosures.
- **Out of scope**: Prose rewriting/polishing, translation, external web plagiarism screening, or journal typesetting.

## Core Audit Principles (Non-Negotiable)
1. **Full Coverage & Wrapped-PDF Resilience**: Parse every readable page and section (`coverage_ratio`), merging PDF hard-wrapped paragraphs and cross-line hyphenations while retaining exact `line_start..line_end` anchors. If any PDF page has `< 30` extracted characters (`PDF_LOW_TEXT_PAGE`) or an extractor is unavailable, record a structured gap in `gaps` and keep `audit_status = "partial"`.
2. **Defect-First Five-Gate Admissibility**: Report only findings that (a) materially impact correctness, validity, reproducibility, or compliance, (b) are discrete and actionable with a verification method, (c) anchor to exact `file`, `section`, `page` (if PDF), `line_start-line_end`, and verbatim quote `<= 200` chars (or explicit `[MISSING: ...]`), (d) demonstrate a concrete failure mode, and (e) avoid speculative padding. When no substantive defects exist, report `verdict: "no_major_defects_detected"` honestly.
3. **Four-Way Taxonomy + Design vs Reporting Separation**:
   - Classify every item into `factual_error`, `insufficient_evidence`, `norm_violation`, or `suggestion`, plus `blocking_tier` (`blocking` for `P0/P1` vs `optional` for `P2/P3`).
   - Tag `defect_distinction` explicitly as `method_flaw` (broken causal/comparative logic or pseudoreplication `df > N`), `under_reported` (missing randomization/blinding, effect size/CI, multiplicity adjustment, or error-bar definition), or `needs_reconciliation` (Abstract-vs-Results numerical conflict beyond `±0.5 pp`, subgroup counts != total `N`, figure/table mismatch, or image duplication anomaly).
4. **Scoped Observational Classification & Citation-Marker Rule**: Determine observational vs interventional study design strictly from Abstract and Methods non-cited, non-future-work sentences (so Discussion calls for "future RCTs" never bypass observational checks). In an observational manuscript, a strong causal claim carrying a citation marker (`[1]`, `\cite{}`, `(Author, Year)`) MUST retain the exact line span and marker as `needs_human_review`, keeping `audit_status = "partial"` and `passed = false` until the source passage and identification assumptions are verified.
5. **Cross-Modal Figure & Image Integrity**: Check figure/table references vs captions, contiguous panel letters (`A, B, C...`), error-bar metric definitions (`SD`/`SEM`/`95% CI`), and 128-bit HV-dHash + intensity-correlation image duplication/flipping (with white-background chart protection). Report perceptual matches neutrally as `needs_human_review` (`needs_investigation`)—never infer intentional misconduct from similarity alone.
6. **Fairness Firewall (ACL H1-H17) & CNS Calibration**: Do not escalate stylistic preferences, "not SOTA", simple methods, null findings, disclosed limitations, or speculative extra experiments into `critical`/`major` defects.

## Deterministic Offline Execution Workflow

Work inside `/app/work`; write final deliverables into `/app/output/`. Do not fabricate missing source content or software state.

### Step 1: Preflight Probe, Budget/Result Init & Full Input Indexing
```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/budget_guard.py --state /app/work/budget.json --init
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --init --case-id "<CASE_ID>" --task-id challenge-0999879e
python3 -B /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```
Read `/input/case.json` and `/input/task.md` first to extract `case_id`, required check scope, and any case-specific constraints.

### Step 2: Run the Six-Facet Cross-Modal Audit Engine
```bash
python3 -B /skills/weekly-skill/scripts/manuscript_quality_auditor.py \
  --input /input \
  --out-json /app/output/quality_audit.json \
  --out-md /app/output/quality_review_report.md \
  --out-result-json /app/output/result.json
```
Inspect `/app/output/quality_audit.json` immediately. The engine runs offline in pure Python 3 stdlib (and automatically uses `pdfplumber`/`pypdf`/`python-docx`/`PIL` when present).

### Step 3: Deep Semantic Verification & False-Positive Scrubbing
Automated rules provide high-precision anchors, NOT the whole review. Read `references/evidence_and_fairness_protocol.md`, `references/six_facet_audit_checklist.md`, and `references/output_contract_spec.md`, then manually inspect the full indexed manuscript to:
1. **Verify every automated finding against surrounding context**: Drop any false positive where a causal phrase quotes prior work, states a hypothesis/control, or is immediately qualified; reconcile any `needs_reconciliation` item if an exact explanation appears in a table or Methods paragraph.
2. **Audit deep semantic defects beyond deterministic rules**:
   - Does the Conclusion answer the exact Research Question posed in the Introduction without silent goal-shifting?
   - Do tables, figures, and prose agree on units, denominators, statistical test names, and direction of effect?
   - Is the statistical inference unit valid (independent subjects vs technical replicates/cells)?
   - Are selection/exclusion funnels transparent without unexplained drop in sample count?
   - When cited source passages are provided alongside the manuscript, inspect them directly to settle `needs_human_review` citation items.
3. **Apply the ACL H1-H17 Firewall & Priority Ordering**: Order final findings by `P0 (critical) -> P1 (major) -> P2 (minor) -> P3 (suggestion)` with concrete sentence-level `actionable_revision`, `verification_method`, and `revision_cost`.

### Step 4: Merge Final Semantic Findings, Validate, Grounding Gate & Deliverable Audit
If you add or refine semantic findings after Step 2, update `/app/output/quality_audit.json`, `/app/output/quality_review_report.md`, and `/app/output/result.json` via `result_guard.py --path /app/output/result.json --patch-json '<PATCH_JSON>' --finalize` (preserving `status: "ok"|"partial_with_gaps"`, `audit_status`, `metrics`, and `metric_provenance`), then run all mandatory verification gates:
```bash
python3 -B /skills/weekly-skill/scripts/result_guard.py --path /app/output/result.json --validate
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json --require quality_audit.json --require quality_review_report.md
```
Never ship if `result_guard.py --validate`, `gap_gate.py`, or `final_audit.py` fails.

## Failure Modes & Honest Degradation
- **Scanned / Low-Text PDF**: Report `PDF_LOW_TEXT_PAGE` with exact page numbers, lower `coverage_ratio`, set `audit_status = "partial"` and `result.json` `status = "partial_with_gaps"`.
- **Missing Cited Source Texts**: Keep citation-support items as `needs_human_review` / `unverified`; never assume a matching citation marker proves semantic support or implies fabrication.
- **Image Format Without Pixel Decoder**: If `PIL` is absent and images are non-PNG (e.g., TIFF/JPEG), report exact SHA-256 checks performed and log `IMAGE_PIXEL_UNVERIFIED` in `gaps` rather than claiming perceptual checks passed.
- **Zero-Dependency PDF Fallback & Real-Journal Precision**: `review_io_core.py` cascades `pypdf` (column-ordered) → `pdfplumber` → `pdftotext` → pure-stdlib FlateDecode `BT..ET` stream decoding (`PDF_STDLIB_FALLBACK_USED`), disambiguates epidemiological nouns (e.g., `causes of death`) from causal overclaim verbs, strictly separates uppercase total `N=` from lowercase subgroup `n=`, runs dual-scale (linear + `log(1+r/100)`) conditional `95% CI` vs `p`-value screening, cross-language numeric precision alignment (`50.9` vs `50,99`), and `AAPC`/endpoint tension checks (`needs_human_review`), and exports `citation_claim_mapping` + `semantic_review_checklist` in both `quality_audit.json` and `result.json`. During Step 3, verify estimation scale, model link/df, and endpoint assumptions; you may calibrate severity with explicit rationale while preserving exact `file:L<start>-L<end>` anchors and full 1:1 finding expansion.
