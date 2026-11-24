# Research Frontiers & Temporal Trend Evolution

Offline-ready temporal bibliometric and research-frontier analysis skill supporting OpenAlex, Crossref, EuropePMC JSON/XML, arXiv Atom XML, PDFs, BibTeX, CSV, and Markdown corpora.

## Package Contents
- `SKILL.md` — Operational workflow and five temporal/evolution invariants.
- `scripts/trend_evolution_analyzer.py` — Main deterministic analyzer with preprint-to-VoR deduplication, dual date-basis sensitivity, source-bias-free stratified topic evolution (`EMERGING_FRONTIER`, `CONVERGING_MERGING`, `SPLITTING_SPECIALIZING`), domain-agnostic 4-role representative selection, and read-only `--verify-report` auditor.
- `scripts/{corpus_loader,prisma_flow_auditor,evidence_state_guard}.py` — Shared offline multi-source parsing and lineage core.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit,audit_chain}.py` — Offline runtime reliability and provenance guards.
- `references/` & `examples/minimal_example.md` — Protocol references and minimal walkthrough.

## Dependencies
- Pure Python 3.10+ standard library (uses local `pdftotext`/`pypdf`/`pdfplumber` if available for PDFs). Zero network calls; zero prohibited dependencies.
