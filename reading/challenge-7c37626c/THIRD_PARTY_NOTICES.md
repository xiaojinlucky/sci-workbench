# Third-Party Notices

1. **yy / scholar-kit** (MIT License, Copyright (c) 2026 YY Ahn)
   - Design reference for `scripts/math_ast_guard.py`: SymPy reserved single-letter symbol isolation (`E, I, S, N, O`) and safe verification workflows, extended here with JATS/MathML deduplication, a general composite SI dimensional algebra parser, OCR noise interception, and pure-stdlib AST evaluation.

2. **AngelChen-HC / systematic-review-skill** (Apache License 2.0)
   - Adapted in `scripts/audit_chain.py`: SHA-256 canonical JSON hash-chained audit logging (`srlib/audit.py`), stripped to pure Python 3.12 stdlib for offline execution.

3. **Light0305 / Light-Science (`light-review` concepts only)** (MIT License, Copyright (c) 2026 Light0305)
   - Alignment on tracking derivation quantifiers, parameter assumptions, boundary conditions, and honest `insufficient_evidence` reporting on missing derivations.

4. **Portable Markdown Math Validation Patterns**
   - `scripts/math_markdown_validator.py` adapts standard GFM/MathJax delimiter-balance, bare-LaTeX, pseudo-math `(I_0)`, tab-corruption, and `E001...` anchor traceability rules for standalone offline use.

5. **Bundled Harbor Guardrails (`preflight_probe`, `result_guard`, `budget_guard`, `block_indexer`, `gap_gate`, `final_audit`)**
   - Adapted from permissive MIT/Apache-2.0 Harbor runtime guardrail baselines.
