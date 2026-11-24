# Grounded Literature Review Generation (`grounded-literature-review`)

Offline-ready Skill for producing empirically clustered, citation-legend-backed literature syntheses with strict separation of full-text consensus, abstract-only suggestive evidence, conflicting findings, and prospective protocols.

## Package Contents
- `SKILL.md` — Complete operational workflow and epistemic hedging contract.
- `scripts/review_synthesis_builder.py` — Pre-writing screening, TF-IDF empirical clustering, horizontal method-operation matrix, three-column synthesis generator, and strict `--verify-draft` auditor.
- `scripts/{corpus_loader,prisma_flow_auditor,evidence_state_guard}.py` — Multi-source offline parser (OpenAlex, Crossref, EuropePMC JSON/JATS XML, arXiv Atom XML, PDF, BibTeX, CSV, Markdown notes) and 5-state/PRISMA guards.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit,audit_chain}.py` — Offline sandbox runtime reliability, block indexing, and provenance modules.
- `references/` — Output contract and synthesis protocol.
- `examples/minimal_example.md` — Minimal illustrative example.

## Dependencies
- Pure Python 3.10+ standard library (uses local `pdftotext`/`pypdf`/`pdfplumber` if available for PDF text extraction). Zero network calls; zero prohibited dependencies.
