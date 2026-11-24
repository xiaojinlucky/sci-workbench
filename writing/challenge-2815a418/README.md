# Peer-Review Rebuttal and Controlled Baseline Revision Skill (`rebuttal-revision-closure`)

Offline, deterministic peer-review rebuttal and manuscript revision closure workflow supporting raw `.pdf`, `.docx`, `.md`, and `.txt` inputs, parent+sub-item comment extraction, triple-bound edit auditing, drift-proof re-anchoring, multi-round prior-commitment continuity, and 5-field `result.json` sealing.

## Purpose and Scope

1. **Zero-dependency native document extraction & `/input` bootstrapping (`scripts/doc_extract.py` + `--extract-review-items`)**: Automatically discovers manuscript and reviewer-comment files under `/input/`, decodes `.pdf` (`pdftotext` -> `pypdf` -> `pdfplumber` -> pure-stdlib `zlib` fallback), `.docx` (pure-stdlib OOXML paragraphs, tables, footnotes), and multi-encoding text, and emits `review_items.json` preserving both parent items and nested sub-items across standard, non-standard (`Reviewer #2`, `Q1`/`W1`, `问题一`, numbered lists), and unnumbered prose (`AUTO-1..N`) formats with a `comment_parse_underflow` self-check.
2. **Triple-bound edit verification + substantive token quota + `CLARIFY_EXISTING` grounding**: Requires every edit-claiming response to simultaneously bind a verbatim revised quote, a valid `[§Section Lstart-Lend]` anchor containing that quote, and `>= 3` non-stopword diff tokens inside that span—while requiring `CLARIFY_EXISTING` and `DISAGREE_WITH_EVIDENCE` responses to quote or anchor real existing manuscript text.
3. **Drift-proof anchoring (`--extract-anchors` + `--reanchor`) & multi-round continuity (`--previous-ledger`)**: Generates `[§Section Lstart-Lend]` tags with `quote_sha1` hashes, automatically recomputes drifted line numbers after late manuscript edits, and verifies prior-round verified edits remain intact in subsequent rounds (`prior_commitment_unmet`).
4. **Baseline DOI & statistical preservation + substantive 3-element missing-evidence closure**: Blocks silently dropped baseline DOIs or unexplained statistical token removals and enforces substantive 3-part missing-evidence entries (`>= 80` chars with concrete section/data specificity).
5. **Four-tier tone, reviewer isolation, anti-fabrication & 5-field contract**: Audits `ACCEPT`/`CLARIFY`/`POLITE_DISAGREE`/`APPEAL`, blocks cross-reviewer leaks, overpromises, rebukes, and empty-thanks replies, and exports `rebuttal_ledger.json`, `rebuttal_closure_report.md`, and a 5-field `contract_patch.json`.

## Directory Layout

- `SKILL.md`: Agent execution protocol, hard invariants, and completion criteria.
- `scripts/doc_extract.py`: Zero-dependency PDF/DOCX/text extractor with page/line offset provenance.
- `scripts/rebuttal_closure_checker.py`: Review-item extractor, anchor extractor, re-anchorer, and deterministic closure auditor.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Common Harbor M1-M6 runtime guards.
- `references/{disposition-and-evidence,tone-and-isolation,output-contract-and-qa}.md`: Methodological rules, tone templates, and 5-field schema.
- `examples/synthetic_example.md`: Self-contained minimal end-to-end walkthrough (marked explicitly as illustrative example).

## Quick Start CLI Usage

```bash
# 1. Auto-extract baseline & review items from /input (PDF/DOCX/MD/TXT)
python3 scripts/rebuttal_closure_checker.py \
  --extract-review-items --from-input /input --work-dir /app/work --out-json /app/work/review_items.json

# 2. Extract exact [§Section Lstart-Lend] + quote_sha1 anchors from baseline->revised diff
python3 scripts/rebuttal_closure_checker.py \
  --extract-anchors --baseline /app/work/baseline_extracted.md --revised /app/output/manuscript-revised.md \
  --out-json /app/work/extracted_anchors.json

# 3. Auto-reanchor drifted line numbers if revised manuscript is edited later
python3 scripts/rebuttal_closure_checker.py \
  --reanchor --revised /app/output/manuscript-revised.md --response /app/output/response-letter.md \
  --out-response /app/output/response-letter.md --out-json /app/work/reanchor_report.json

# 4. Run full triple-bound & multi-round closure audit
python3 scripts/rebuttal_closure_checker.py \
  --baseline /app/work/baseline_extracted.md --comments /app/work/comments_extracted.md \
  --revised /app/output/manuscript-revised.md --response /app/output/response-letter.md \
  --missing-evidence /app/output/missing_evidence_list.md \
  --previous-ledger /app/work/round1_rebuttal_ledger.json \
  --case-id "<CASE_ID>" --out-json /app/output/rebuttal_ledger.json \
  --out-report /app/output/rebuttal_closure_report.md --out-contract-patch /app/work/contract_patch.json
```

## Dependencies and Limitations

- Python 3.12 standard library only (zero third-party dependencies, zero network access; optional acceleration if `pdftotext`, `pypdf`, or `pdfplumber` is preinstalled).
- Operates strictly within controlled baseline scope; never fabricates external experiments, cohorts, or editor decisions. Offline comparison benchmarks in companion folders reflect local self-tests on open-access papers, not official hidden platform whiteboards.
