# LaTeX Diagnostics, Magic Comments, and Bibliography Engine Matrix

## 1. Engine & Bibliography Routing Matrix

| Preamble Signal | Required TeX Engine | Required Bib Tool | Common Failure Mode |
| --- | --- | --- | --- |
| Standard ASCII / UTF-8 (`inputenc`, `amsmath`, `booktabs`, `natbib`) | `pdflatex` (via `latexmk -pdf`) | `bibtex` (if `\bibliography{}`) | Unescaped `_`, `%`, `&`, `#`; missing `.cls`/`.sty` |
| `\usepackage{fontspec}`, `xeCJK`, `ctex`, `\documentclass{ctexart}` | `xelatex` or `lualatex` | `biber` or `bibtex` | `% !TeX program = pdflatex` magic comment silently overrides engine and crashes |
| `\usepackage{biblatex}` / `\addbibresource{...}` (default backend) | `pdflatex` / `xelatex` / `lualatex` | `biber` | Running `bibtex` instead of `biber`, or `.bcf` version mismatch between `biblatex` and `biber` |
| `\usepackage[backend=bibtex]{biblatex}` | `pdflatex` / `xelatex` / `lualatex` | `bibtex` | Mixing `\addbibresource` with legacy `\bibliography{}` |
| Large-collaboration `.bib` entries (>100 authors) | Any | `biber` preferred | `bibtex` buffer/capacity panic (`Sorry---you've exceeded BibTeX's buffer size`); truncate with `and others` if `bibtex` is mandated |

## 2. Root-Cause vs Cascading Error Triage

Always resolve errors in strict causal order; fixing item 1 often eliminates 10+ downstream warnings:

1. **Directive & Toolchain Conflicts (`TEX_DIRECTIVE_CONFLICT`, `TEX_BIB_BACKEND_CONFLICT`)**:
   - Remove or update stale `% !TeX program = pdflatex` when `fontspec`/`xeCJK`/`ctex` is loaded.
   - Align `% !BIB program` with `biblatex` (`biber`) vs `natbib`/`\bibliography` (`bibtex`).
2. **Missing Files & Packages (`TEX_DEPENDENCY_MISSING`)**:
   - Missing local `.tex` or figure: check relative path inside `editable/` (case-sensitive on Linux!).
   - Missing journal `.cls`/`.sty`: verify whether it is supplied in `/input`. In an offline sandbox, never attempt network installs; if genuinely absent from both `/input` and TeX Live, record `TEX_DEPENDENCY_MISSING` honestly.
3. **Bibliography Backend Failures (`TEX_BIBLATEX_BIBER_MISMATCH`, `TEX_BIBTEX_CAPACITY_OVERFLOW`, `TEX_TOOLCHAIN_ERROR`)**:
   - Inspect `.blg` section in `build_log.txt`.
   - Never rely on a subsequent `pdflatex` rc=0 pass to declare success when `bibtex`/`biber` exited non-zero.
4. **Syntax & Math Delimiter Errors (`undefined_control_sequence`, `math_or_env_mismatch`)**:
   - Check the exact `file:line` in `diagnostics`. Common causes: unescaped underscore `_` in text/DOIs (wrap with `\url{...}` or `\_`), unmatched `$` or `\left`/`\right`, or typo in `\begin{...}`/`\end{...}`.
5. **Cross-Reference & Citation Resolution (`TEX_REFERENCES_UNRESOLVED`, `TEX_MISSING_BIB_KEY`, `TEX_DUPLICATE_LABEL`)**:
   - Multi-pass convergence requires `tex -> bib -> tex -> tex` (handled automatically by `latexmk` or explicit pass sequence).
   - If a key is absent from all `.bib` files, report `TEX_MISSING_BIB_KEY` rather than inventing fake bibliographic metadata.

## 3. Reproducible Build Metadata Checklist

Every build record in `result.json` captures:
- Exact engine binary name and version (`compiler_version`)
- Ordered `step_results` (exact command, exit code, wall-clock seconds)
- Input-to-editable SHA-256 manifest (`source_manifest`)
- Compiled PDF SHA-256 (`pdf_sha256`) and Poppler page count
