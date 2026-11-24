# Auditable Systematic Literature Review (`systematic-review-pipeline`)

Offline-ready 5-stage systematic review Skill implementing SHA-256 frozen PICO/PECO protocols, strict Stage-1/Stage-2 screening separation, PRISMA 2020 Item 16a conservation accounting, versioned 5-domain methodological quality appraisal, dual-reviewer disagreement flagging, and opposing-evidence preservation.

## Package Contents
- `SKILL.md` — End-to-end 5-stage systematic review workflow and invariants.
- `scripts/systematic_review_runner.py` — Deterministic 5-stage engine emitting `systematic_review_bundle.json`, `systematic_review_report.md`, and `result.json`.
- `scripts/{corpus_loader,prisma_flow_auditor,evidence_state_guard}.py` — Multi-source offline ingestor, preprint-to-VoR deduplicator, PRISMA conservation verifier, and 5-state guard.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit,audit_chain}.py` — Offline sandbox runtime reliability, block indexing, and provenance guards.
- `references/` — Output contract and appraisal rubric.
- `examples/minimal_example.md` — Minimal illustrative walkthrough.

## Dependencies
- Pure Python 3.10+ standard library (uses local `pdftotext`/`pypdf`/`pdfplumber` if installed). Zero external network calls; zero prohibited libraries.
