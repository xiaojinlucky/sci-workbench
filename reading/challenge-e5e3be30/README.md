# Scientific Claim Evidence Tracing & Fact Verifier (`challenge-e5e3be30`)

An offline, zero-dependency Python 3.12 skill for compound scientific claim decomposition, strict source/DOI-bound evidence tracing, four-state entailment auditing, and citation integrity checking under Harbor Organizer Revision v2.

## Capabilities
1. **Atomic Compound Claim Decomposition (`scripts/claim_verifier.py`)**:
   - Treats `/input/claims-to-verify.json` strictly as untrusted audit questions.
   - Splits English and Chinese compound claims into atomic sub-claims (`Cxx.y`) typed across `{fact, comparison, causation, scope, quantitative}` with 5-dimensional `E/M/L/S/I` fingerprints (Entity, Magnitude/Unit/Stat, Logic/Negation, Scope/Condition, Identifier/Comparator).
2. **Strict Source/DOI Locking (Zero Cross-Paper Borrowing)**:
   - Reads DOI metadata exclusively from structured fields and explicit document header lines (never bibliography references).
   - Binds `cited_ref` strictly to matching documents; refuses to borrow passages from unrelated papers when a cited DOI is absent or unsupported.
3. **Four Independent Failure Diagnostics**:
   - `doi_exists_but_unsupported`: Cited DOI/file exists in corpus, yet full text does not support the claim (defeats the "topic-related / DOI exists => supported" trap).
   - `wrong_page_or_anchor_mismatch`: Cited page/paragraph does not hold the evidence, while matching evidence is recovered at a different anchor within the **same** document.
   - `fulltext_unavailable_or_figure_caption_missing`: Abstract-only text, missing PDF text layer, or image-only figure lacking caption text sets `localization_insufficient: true` and forces `insufficient_evidence` (never `contradicted`).
   - `inline_vs_structured_citation_drift`: Detects mismatches between inline citation markers (`[1],[2]`) in text and structured citation arrays.
4. **Extrapolation vs. Contradiction & Secondary/Conflict Auditing**:
   - Untested population/dose/mechanism extrapolations are classified `insufficient_evidence`, never `contradicted`.
   - Flags indirect/secondary citations in Introduction/Background (`secondary_citation_warning`).
   - Detects abstract-vs-body/table conflicts within the same study and preserves both verbatim anchored quotes.
5. **Automatic Numeric, Polarity & Extrapolation Entailment Engine**:
   - Uses reading-flow `pdftotext -enc UTF-8` (zero two-column interleaving) and preserves thousands-separated counts (`n=1,010`, `n=5,539`).
   - Detects quantitative contradictions (e.g., claimed `35%` vs measured `18.2%` on the matching target -> `contradicted`), statistical/polarity negations (`no significant difference`, `failed to improve`, `stationary`), and scope extrapolations (`all/universal/adults/clinical cure` vs pediatric `0-19`/observational/`in vitro` -> `insufficient_evidence`).
6. **Strict Semantic Validator (`--validate-verifications`)**:
   - Verifies `paragraph_id` ownership, verbatim `quote` substring match, inline-vs-structured citation reconciliation, blocks `supported` verdicts containing unresolved numeric/polarity contradictions, and blocks `contradicted` verdicts on untested extrapolation or localization gaps.

## Directory Layout
- `SKILL.md`: Primary verification protocol for the agent.
- `references/output_contract_spec.md`: Complete JSON and Markdown deliverable contract.
- `references/atomic_claim_and_four_state_rubric.md`: Atomic claim taxonomy, 4-state verdict matrix, 4 diagnostic flags, and misjudgment counterexamples.
- `references/citation_integrity_and_conflict_protocol.md`: Citation Legend, inline-vs-structured drift audit, wrong-page recovery, secondary citation, and abstract-body conflict rules.
- `scripts/claim_verifier.py`: End-to-end claim decomposer, source-locked retriever, diagnostic engine, Markdown renderer, and `--validate-verifications` gate.
- `scripts/citation_anchors.py`: Citation extraction, inline-vs-structured drift auditor, E/M/L/S/I fingerprinter, secondary citation & localization gap detectors, and verbatim quote verifier.
- `scripts/preflight_probe.py`, `result_guard.py`, `budget_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py`, `audit_chain.py`: Offline Harbor runtime guardrails.
- `examples/`: Minimal synthetic claims input and illustrative output schema.

## Quick Start (Offline)
```bash
# 1. Automated decomposition, retrieval & diagnostic audit
python3 scripts/claim_verifier.py --input /input --output /app/output --work /app/work

# 2. Validate agent-refined verification draft and promote to /app/output
python3 scripts/claim_verifier.py \
  --input /input --output /app/output --work /app/work \
  --validate-verifications /app/work/verifications_draft.json

# 3. Grounding and final deliverable audit
python3 scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 scripts/final_audit.py --dir /app/output --result-file result.json --require verification_cards.json --require verification_report.md
```

## Runtime Dependencies & Limits
- Python >= 3.10 standard library only (zero third-party Python packages required).
- PDF extraction uses system `pdftotext` (Poppler); image-only PDFs or figures without caption text are reported honestly via `fulltext_unavailable_or_figure_caption_missing` (`insufficient_evidence`).
- Automated candidate retrieval is lexical/structural; final nuanced entailment should be refined by the host agent and verified through `--validate-verifications`.
