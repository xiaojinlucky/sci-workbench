# Literature Reading Benchmark & Evaluation Harness (`benchmark-a1d81ca4`)

Offline Python 3.12 standard-library Skill for auditing, running, scoring, and versioning source-conditioned literature reading benchmarks (Organizer Revision 2 compliant).

## Package Layout
- `SKILL.md` — Execution contract, boundaries, and workflow.
- `references/`
  - `item_card_and_leakage_audit.md` — Item card schema and pre-run anti-leakage checks.
  - `scoring_012_and_failure_taxonomy.md` — Multi-dimensional 0/1/2 scoring and six-class failure taxonomy.
  - `version_snapshot_and_incremental_recompute.md` — Comparability-aware version diff and incremental recompute rules.
- `scripts/`
  - `item_bank_auditor.py` — Standalone item-card completeness, corpus-anchor, duplicate-ID, and prompt-leakage auditor.
  - `benchmark_runner.py` — Deterministic 0/1/2 scorer, multi-run stability & budget auditor, regression item drafter, version diff engine, and Markdown report generator.
  - `audit_chain.py`, `citation_anchors.py` — SHA-256 hash-chained audit ledger and strict verbatim quote verifier.
  - `preflight_probe.py`, `budget_guard.py`, `result_guard.py`, `block_indexer.py`, `gap_gate.py`, `final_audit.py` — Harbor runtime guardrails.
- `examples/`
  - `case.json` — Minimal illustrative 4-item `benchmark-DRAFT` example (not a formal evaluation case).
  - `sample_output_schema.json` — Illustrative output contract schema.

## Quick Offline Run
```bash
python3 -B scripts/item_bank_auditor.py --input examples --out /tmp/item_audit.json
python3 -B scripts/benchmark_runner.py --input examples --output /tmp/bench_out --work /tmp/bench_work
```

## Key Capabilities
- **Direct `.md`/`.txt`/`.json` Corpus Ingestion**: Automatically indexes `<a id="p...">`, `[P001]`/`[T01]`, markdown pipe tables, and paragraph blocks without manual reformatting.
- **Per-Criterion 0/1/2 Breakdown**: Emits `criterion_breakdown` (`value_unit_ok`, `keyword_ok`, `anchor_id_ok`, `verbatim_quote_ok`, `citation_parity_ok`, `abstention_ok`) for transparent error attribution.
- **Auto-vs-Human Calibration Engine**: Computes `exact_agreement_rate`, `within_1_agreement_rate`, `cohens_kappa`, `quadratic_weighted_kappa`, `confusion_matrix`, and `per_item_disagreements[]` when `human_gold_scores` are provided; otherwise emits a stratified `human_calibration_queue[]` without fabricating agreement.
