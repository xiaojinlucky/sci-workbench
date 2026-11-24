# Academic LaTeX Writing, Diagnostics & Reproducible Compiler Skill

Offline-ready scientific LaTeX authoring, root-cause diagnostics, semantic cross-reference verification, layout auditing, and reproducible multi-pass PDF compilation skill.

## Core Capabilities

1. **Immutable Original Protection & Surgical Diffing**: Copies input sources into `/app/output/editable/`, compiles inside an isolated scratch build directory (preventing stale PDF false positives), and records before/after SHA-256 hashes plus unified diffs in `result.json`.
2. **True Multi-Step Exit Codes & Root-Cause Diagnostics**: Preserves per-step exit codes across `latexmk`, `pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`, and `tectonic`. Detects `% !TeX program` magic-comment conflicts, BibLaTeX/Biber `.bcf` version mismatches, and BibTeX author-list buffer overflows.
3. **Static & Semantic Cross-Reference / Bibliography Auditing**: Checks duplicate `\label` definitions, missing `.bib` keys, prose-vs-label prefix mismatches (`Section \ref{fig:...}`), self-section anomalies, and author-declared `reference_expectations.json` targets.
4. **Two-Column Widow/Orphan, Overfull Hbox & Real PDF Page Inspection**: Parses TeX logs for `Overfull \hbox`/`Float too large`, checks two-column `\widowpenalty`/`\clubpenalty`, scans compiled PDF text for literal `??` markers and sparse trailing widow pages, and renders PNG page previews via `pdftoppm`.

## Package Layout

- `SKILL.md`: Operational workflow and hard invariants for the Agent runtime.
- `scripts/latex_builder_engine.py`: Pure Python 3.12 stdlib offline build, diagnostic, audit, and PDF inspection engine.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py`: Standard Harbor runtime guards.
- `references/latex-diagnostics-and-bib-matrix.md`: Root-cause error taxonomy and multi-pass engine routing matrix.
- `references/semantic-ref-and-layout-audit.md`: Cross-reference auditing, two-column typography, and visual inspection rules.
- `references/output_contract_spec.md`: Complete `result.json` output contract schema.
- `examples/minimal_project_bundle.json`: Self-contained synthetic example bundle stored as UTF-8 JSON (zero static `.tex`/`.bib` files inside the Skill package).
- `examples/sample_output_schema_example.json`: Illustrative output contract schema example.

## Minimal Invocation

```bash
python3 scripts/latex_builder_engine.py \
  --input /input \
  --output /app/output \
  --work /app/work
```

## Runtime Dependencies & Limits

- Python 3.12 standard library only (zero third-party Python dependencies).
- Uses preinstalled TeX engines (`latexmk`, `pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`, `tectonic`) and Poppler CLI (`pdfinfo`, `pdftotext`, `pdffonts`, `pdftoppm`) when available; degrades explicitly and honestly when absent.
- Security: always runs with `-no-shell-escape` / `--untrusted --only-cached` and refuses symlinks or path traversal.
