---
name: academic-latex-writing-compiler
description: Write, adapt, diagnose, and reproducibly compile scientific LaTeX manuscripts in offline Linux environments. Preserves immutable originals, enforces true multi-step compiler/bibliography exit codes, resolves magic-comment and BibLaTeX/Biber conflicts, audits semantic cross-references and two-column layout/widows, and inspects real rendered PDF pages.
---

# Academic LaTeX Writing, Diagnostics, and Reproducible Compilation

Use this skill whenever a task requires authoring, repairing, adapting, or compiling a scientific `.tex` manuscript (equations, tables, floats, citations, journal templates, multi-pass builds) and delivering verifiable editable source plus real PDF evidence.

## Hard Invariants

1. **Original input immutability**: Treat `/input` as read-only. Stage all working edits inside `/app/output/editable/` and always pass `--original /input` on repair passes so every changed file carries before/after SHA-256 hashes and a unified diff in `change_log`.
2. **Two-phase atomic `result.json`, budget-aware compilation, and deterministic safe auto-repair**: Immediately write a valid minimal `result.json` skeleton (with `case_id` and all required `output_contract` fields) before compiling so a hard 900s sandbox timeout never leaves a missing or truncated result. Integrate with `/app/work/.budget_state.json` (`TEX_BUDGET_EXHAUSTED` early cutoff if remaining seconds `< timeout + 25`), enforce a tight per-tool timeout (default `60s`), and cap repair iterations at **at most 3 rounds**. Use `--auto-repair` to apply strictly meaning-preserving deterministic fixes in `/app/output/editable/` (resolving conflicting `% !TeX program = pdflatex` when `fontspec`/`xeCJK`/`ctex` is present, injecting `\widowpenalty=10000`/`\clubpenalty=10000`/`\displaywidowpenalty=10000` in two-column layouts, adding missing `booktabs`/`graphicx`/`tabularx` imports, and upgrading fixed `\scalebox` around tabular to column-aware `\resizebox{\linewidth}{!}`), logging every rule and unified diff in `change_log`.
3. **True exit codes, honest Markdown-to-LaTeX scaffold (zero fabrication), smart entry selection, and stable PDF output**: Compile only inside a fresh `/app/work/latex-build-*` copy without pre-existing entry PDFs so stale artifacts never mask a broken build. Never let a later `pdflatex` pass hide an earlier non-zero `bibtex`/`biber` exit code. When `/input` provides only Markdown/text manuscript drafts (never falling back to `task.md`/`prompt.md`/`rubric.md`, which trigger `TEX_MISSING_MANUSCRIPT_SOURCE`), synthesize a compilable LaTeX scaffold under `/app/output/editable/main.tex` supporting `--template article|ieeetran|acmart|ctexart`, converting Markdown pipe tables to `booktabs`/`tabularx`/`longtable`, images to `\includegraphics[width=0.95\linewidth]`, lists, inline math/formatting, and `xeCJK`/`fontspec` (`xelatex`) for CJK. Never fabricate `Draft Author` or a placeholder title: when author/title metadata is missing from `case.json` and the draft header, leave `\author{}` blank and emit explicit `TEX_MISSING_AUTHOR_METADATA` / `TEX_MISSING_TITLE_METADATA` gaps. After `--auto-repair`, automatically re-hash `editable/` and re-run static bib/ref audits so resolved conflicts clear cleanly. Whenever a valid PDF is produced, emit BOTH a stable `main.pdf` (`<entry_stem>.pdf`) and hashed `compiled-<hash>.pdf` copy.
4. **Root-cause diagnosis before editing**: Separate primary causes (`missing_package_or_cls`, `journal_class_missing`, `package_option_clash`, `cjk_font_or_engine_mismatch`, `biblatex_biber_version_mismatch`, `magic_comment_conflict`, `undefined_control_sequence`, `math_or_env_mismatch`, `bibtex_buffer_or_author_overflow`) from cascading TeX errors. Consult `references/latex-diagnostics-and-bib-matrix.md` before patching.
5. **Cross-reference and bibliography fidelity**: Verify static `\label`/`\ref` (including `\cref`, `\Cref`, `\cpageref`, `\vref`, `\autoref`, `\nameref`, `\eqref`, `\subref`, and comma-separated multi-refs) and `.bib` key closure (flagging `TEX_MISSING_BIB_FILE` when citations exist without `.bib` entries), flag prose-vs-prefix type mismatches (e.g., `Section \ref{fig:...}`), check self-section anomalies, and validate any author-declared `/app/work/reference_expectations.json` targets literally without fabricating author intent.
6. **Layout and real PDF inspection**: Never treat exit code 0 alone as visual pass. Inspect `overfull_hboxes`, `float_too_large`, two-column widow/orphan settings (`\widowpenalty=10000`, `\clubpenalty=10000`), `pdfinfo`/`pdftotext` (`??` marker scan), and rendered `previews/page-*.png` images.
7. **Item-level `preserved_facts` provenance and complete output contract**: Extract item-level facts (claims, numbers, table cells, citations) with exact `source_file:source_line` anchors and read them back against final `editable/*.tex` (`tex_file:tex_line`, `tex_verified`) and compiled PDF text (`pdf_verified`), flagging `TEX_FACT_PRESERVATION_DRIFT` on any loss instead of using hash/count placeholders. Write direct, clear academic prose (no defensive filler or AI boilerplate) and output all required `output_contract` keys (`case_id`, `deliverable`, `preserved_facts`, `evidence_map`, `change_log`) plus `sources`, `uncertainty`, and `gaps`.

## Execution Workflow

### Step 1: Probe environment and inventory inputs

Run the standard preflight and budget/indexing probes to discover installed TeX engines (`latexmk`, `pdflatex`, `xelatex`, `lualatex`, `bibtex`, `biber`, `tectonic`), PDF tools (`pdfinfo`, `pdftotext`, `pdffonts`, `pdftoppm`), and all input files:

```bash
python3 /skills/weekly-skill/scripts/preflight_probe.py --out /app/work/env.json
python3 /skills/weekly-skill/scripts/budget_guard.py --init
python3 /skills/weekly-skill/scripts/block_indexer.py --scan /input --out /app/work/input_blocks.json
```

Read `/input/case.json` and `/input/task.md` first. Identify:
- Target template/document class, required TeX engine (if specified), and entry `.tex` file.
- Whether the task supplies an existing `.tex` tree to repair/adapt or prose/tables (`.md`/`.txt`) to scaffold into LaTeX.
- Whether the author explicitly states intended targets for specific `\ref` calls.

Completion criterion: Entry file (or prose draft source), required engine/template, and source constraints are confirmed from actual files (never guessed from filenames or domain labels).

### Step 2: Author or declare reference expectations (only if explicitly specified)

- **If drafting from supplied prose/data**: Write clean modular `.tex` files under `/app/output/editable/` (or let `latex_builder_engine.py` auto-scaffold `editable/main.tex` from `/input/*.md` and then refine it) preserving all figures, tables, equations, and `.bib` entries from `/input`. Keep relative paths portable, use `booktabs` (`\toprule`, `\midrule`, `\bottomrule`) for three-line tables, wrap wide tables via `tabularx`, and set `\widowpenalty=10000` and `\clubpenalty=10000` for two-column templates.
- **If the prompt explicitly names an intended `\ref` target**: Write `/app/work/reference_expectations.json` as a JSON list of objects:
  `[{"source": "sections/results.tex", "line": 2, "occurrence": 1, "expected_label": "sec:method"}]`.
  Only record explicitly stated targets; never invent expectations from speculation.

### Step 3: Execute baseline compilation and static/layout audit

Run `latex_builder_engine.py` against `/input` (or against `/app/output/editable` with `--original /input` when drafting new TeX):

```bash
python3 /skills/weekly-skill/scripts/latex_builder_engine.py \
  --input /input \
  --output /app/output \
  --work /app/work
```

Add optional flags when applicable:
- `--entry <rel/path.tex>` when multiple `\documentclass` files exist or task specifies an entry.
- `--engine <latexmk|pdflatex|xelatex|lualatex|tectonic>` when the template mandates an engine.
- `--reference-expectations /app/work/reference_expectations.json` when author-declared targets exist.
- `--timeout <1-300>` (default `60s` per tool invocation).

Note on exit codes: `latex_builder_engine.py` atomically writes a valid `result.json` skeleton before compiling, updates `/app/output/result.json`, `/app/output/build_log.txt`, and `/app/output/editable/` BEFORE exiting, and returns **non-zero** whenever a build step fails or a blocking gap (`TEX_BUILD_FAILED`, `TEX_DEPENDENCY_MISSING`, `TEX_BIBLATEX_BIBER_MISMATCH`, `TEX_MISSING_BIB_KEY`, `TEX_REFERENCE_TARGET_MISMATCH`, `TEX_REF_TYPE_MISMATCH`, `TEX_DIRECTIVE_CONFLICT`) is present.

### Step 4: Diagnose root causes and repair in `/app/output/editable/` (max 3 rounds)

Read `/app/output/result.json` (`gaps`, `latex_execution.compilation.diagnostics`, `bib_and_ref_audit`, `layout_audit`, `pdf_inspection`) and the tail of `/app/output/build_log.txt`. Consult `references/latex-diagnostics-and-bib-matrix.md` and `references/semantic-ref-and-layout-audit.md`:

1. Fix **root-cause fatal errors** first (directive conflicts, missing packages/macros, unescaped `_/%/&/#`, math delimiter mismatches, BibLaTeX/Biber conflicts, BibTeX author-list overflow).
2. Fix **citation and cross-reference defects** (`TEX_MISSING_BIB_KEY`, `TEX_DUPLICATE_LABEL`, `TEX_REF_TYPE_MISMATCH`, `TEX_REFERENCE_TARGET_MISMATCH`, and review `semantic_ref_warnings`). Do not invent nonexistent bibliographic metadata; if a cited paper is genuinely missing from `/input`, keep the gap visible and request the missing `.bib` entry.
3. Fix **layout and clipping defects**: resolve severe `Overfull \hbox (>=6pt)`, oversized figures (`width=\linewidth` or `width=0.95\columnwidth` in two-column layouts), `Float too large`, and widow/orphan headings.
4. Re-compile from `/app/output/editable` while keeping `--original /input` for diff and hash tracking:

```bash
python3 /skills/weekly-skill/scripts/latex_builder_engine.py \
  --input /app/output/editable \
  --original /input \
  --output /app/output \
  --work /app/work
```

Completion criterion: All repairable root causes are fixed in `/app/output/editable/` within 3 rounds, compilation converges with exit code 0 (or every remaining environmental gap is documented honestly), and before/after diffs are recorded in `change_log`.

### Step 5: Inspect real PDF pages and rendered previews

When compilation produces `main.pdf` and `compiled-<hash>.pdf`:
1. Check `pdf_inspection` in `result.json`: confirm `pages >= 1`, `unresolved_markers_in_pdf == []`, and check `non_embedded_fonts` and `widow_orphan_warnings`.
2. Inspect rendered `previews/page-*.png` (generated via `pdftoppm`) to verify equations, tables, figure bounds, citation numbers, and two-column balance.
3. If visual clipping, missing CJK/Unicode glyphs, or broken floats appear, return to Step 4 and re-compile from the same final source so `main.pdf`, `compiled-<hash>.pdf`, and previews match the delivered `.tex` files byte-for-byte.

### Step 6: Enrich contract fields and run final verification

If the case requires narrative explanations, preserved-fact annotations, or task-specific `output_contract` fields beyond what `latex_builder_engine.py` populated, patch `/app/output/result.json` cleanly via `result_guard.py` (preserving `status: "partial_with_gaps"` whenever genuine gaps remain) and run the final offline audit:

```bash
python3 /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 /skills/weekly-skill/scripts/final_audit.py --dir /app/output --require result.json --require build_log.txt
```

## Offline Degradation & Failure Handling

- **Missing TeX engine / class / font / offline Tectonic cache**: Never fabricate a PDF or silence the error. Retain complete `/app/output/editable/` sources, write exact missing files/engines into `gaps` (`TEX_COMPILER_MISSING`, `TEX_DEPENDENCY_MISSING`, `TEX_CACHE_NOT_READY`), and keep `status: "partial_with_gaps"` or `"failed"`.
- **Missing `pdftoppm` / `pdfinfo`**: Record `pdftoppm_available: false` in `pdf_inspection` and report visual inspection as unverified in `uncertainty`; never claim visual checks passed without real rendered pages.
- **Security**: Always compile with `-no-shell-escape` (or Tectonic `--untrusted --only-cached`). Never attempt network package downloads in an offline sandbox.
