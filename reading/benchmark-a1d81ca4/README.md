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
python -B scripts/item_bank_auditor.py --input examples --out /tmp/item_audit.json
python -B scripts/benchmark_runner.py --input examples --output /tmp/bench_out --work /tmp/bench_work
```
