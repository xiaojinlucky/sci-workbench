# Research Topic Selection & Novelty Evaluation (`topic-novelty-feasibility`)

Offline-ready Skill for formulating falsifiable research questions, building per-paper prior-work comparison matrices, red-teaming 4-axis novelty claims against bounded local corpora, verifying 5-axis feasibility premises with strict `[GROUNDED]`/`[ASSUMPTION]`/`[BLOCKED_UNKNOWN]` provenance, and producing a 5-column Pre-Mortem risk matrix.

## Package Contents
- `SKILL.md` — Complete 5-pillar topic evaluation workflow and strict audit rules.
- `scripts/novelty_feasibility_evaluator.py` — Deterministic prior-work matrix, 4-axis novelty overlap red-teamer, source-verified feasibility auditor, and Pre-Mortem generator.
- `scripts/{corpus_loader,prisma_flow_auditor,evidence_state_guard}.py` — Shared offline multi-source parsing and similarity core.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit,audit_chain}.py` — Offline sandbox runtime reliability, block indexing, and provenance guards.
- `references/` & `examples/minimal_example.md` — Evaluation protocols and minimal example.

## Dependencies
- Pure Python 3.10+ standard library (uses local `pdftotext`/`pypdf`/`pdfplumber` if installed). Zero external network calls; zero prohibited dependencies.
