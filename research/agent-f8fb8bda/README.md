# Deep Research Agent (`deep-research-orchestrator`)

Offline-capable multi-source deep research orchestration Skill built for verifiable scientific inquiry.

## What It Solves
- Replaces shallow keyword rewrites with a 6-axis dependency-ordered sub-question DAG (`research_object`, `mechanism_or_variable`, `method_axis`, `comparison_or_baseline`, `time_or_scope_window`, `evidence_requirement`).
- Enforces a strict 6-tier `material_access` hierarchy (`fulltext_pdf`, `fulltext_xml`, `fulltext_local_text`, `abstract_only`, `discovery_only`, `blocked_or_unavailable`) so search snippets and paywalled/CAPTCHA-blocked sources can never impersonate full-text evidence.
- Preserves sub-question-scoped opposing and null findings side by side with exact `#page=N` / `#sec=...` locators.
- Persists resumable checkpoints (`/app/work/checkpoint_state.json`) and step-level `tool_call_log` records for independent re-execution.

## Package Layout
- `SKILL.md` — Primary operational guide and runtime workflow.
- `scripts/deep_research_orchestrator.py` — Main deterministic multi-source orchestrator and strict report verifier.
- `scripts/corpus_loader.py` — Offline parser for OpenAlex, Crossref (list + single work), EuropePMC JSON/JATS XML, arXiv Atom XML, PDFs (`pdftotext`/`pypdf`/`pdfplumber`), BibTeX, CSV, and Markdown.
- `scripts/prisma_flow_auditor.py` — Multi-source deduplication with preprint-to-VoR lineage preservation.
- `scripts/evidence_state_guard.py` — 5-state evidence grading, 3-way epistemic tags, overclaim blocker, and pure-stdlib TF-IDF primitives.
- `scripts/{preflight_probe,budget_guard,result_guard,block_indexer,gap_gate,final_audit,audit_chain}.py` — Offline sandbox runtime reliability, block indexing, and provenance guards.
- `references/` — Output contract and epistemic boundary protocol.
- `examples/minimal_example.md` — Minimal illustrative invocation and output schema.

## Dependencies & Offline Compatibility
- Python 3.10+ standard library only (zero external pip dependencies required; optional `pdftotext` or `pypdf`/`pdfplumber` used when available for PDF text extraction).
- Zero external network calls; zero prohibited libraries (`fitz`/`pymupdf`/`cairosvg`).

## Quick Invocation
```bash
python3 scripts/deep_research_orchestrator.py --input /input --output /app/output --work /app/work
python3 scripts/deep_research_orchestrator.py --input /input --output /app/output --work /app/work --resume
python3 scripts/deep_research_orchestrator.py --input /input --verify-report /app/output/deep_research_brief.md --out-json /app/work/citation_audit.json --strict
```
