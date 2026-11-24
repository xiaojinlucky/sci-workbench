# Logical & Argument-Chain Audit Skill (`challenge-16d4414d`)

Zero-dependency Skill for source-bound scientific logic, argument-chain, cross-section consistency, and multi-round revision-closure auditing.

## Key Capabilities
- **Pure-Stdlib Offline Runtime**: Runs in `python:3.12-slim --network none` across Markdown, LaTeX, plain text, DOCX (stdlib OOXML fallback), and PDF (cascade extractor with honest low-text page gap reporting).
- **Three-Way Sentence Identity Separation**: Separates `author_claim`, `cited_prior_view`, and `explicit_control_or_hypothesis` (including negated causal disclaimers) before evaluating contradictions or overclaims.
- **Structured Argument Graph**: Builds line-anchored `Claim -> Evidence -> Warrant -> Boundary` records across Abstract, Introduction, Results, Discussion, and Conclusion.
- **Six Logical Breakpoints**:
  1. Observational/correlational causal overreach (`causal_overreach`)
  2. Introduction-promised objectives missing in Methods/Results (`intro_delivery_fracture`)
  3. Cross-section numerical discrepancies (`> 0.5pp`) and polarity-aware effect-direction / significance reversals (`cross_section_direction_reversal`, `cross_section_significance_reversal`, `cross_section_numeric_conflict`)
  4. Scale/sample over-extrapolation from preclinical/animal/single-center designs to universal clinical efficacy (`scale_over_extrapolation`)
  5. Missing limitations/boundaries (`missing_limitations`, strictly `suggestion` P3)
  6. Multi-round Revision Closure Mode (`prior_review -> author_response -> v1/v2` or `round1/round2`/`rev1/rev2`/`draft1/draft2` diff) with isolated reporting of newly noticed issues.
- **Executable ACL H1-H17 Fairness Firewall (`--audit-findings`) & Honest Zero-Defect Verdict**: Automatically downgrades improper H1-H17 critiques to `suggestion` (`DOWNGRADED_BY_ACL_H<n>`) and never fabricates issues on logically sound manuscripts (`verdict: no_major_defects_detected`).

## Quick Invocation
```bash
python scripts/argument_chain_auditor.py \
  --input /input \
  --out-json /app/output/logic_audit.json \
  --out-md /app/output/logic_review_report.md \
  --out-result-json /app/output/result.json
```

## Package Layout
- `SKILL.md`: Agent instructions, guardrails, and M1-M6 execution workflow.
- `scripts/argument_chain_auditor.py`: Deterministic logic, revision-closure, and ACL H1-H17 findings-gate engine.
- `scripts/review_io_core.py`: Zero-dependency document, section, wrapped-paragraph sentence, and image IO module.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: M1-M6 runtime guards.
- `references/`: Shared protocol (`evidence_and_fairness_protocol.md`), output contract (`output_contract_spec.md`), logic anchors (`argument_logic_anchors.md`), and revision closure rules (`revision_closure_protocol.md`).
- `examples/minimal_example.md`: Annotated synthetic walkthrough (not a formal case answer).

## Boundaries & Limitations
- Does not perform raw statistical recomputation, reference metadata web verification, or copyediting.
- Scanned PDFs lacking an embedded text layer or unavailable PDF extractors are flagged in `gaps` (`status: partial_with_gaps`) rather than guessed.
