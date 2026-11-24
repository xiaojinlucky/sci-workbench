# Standalone TikZ & OpenTikZ `edit_contract` Guide

1. **Standalone Preamble**:
   - Use `\documentclass[border=10pt]{standalone}` with explicit `\usepackage{tikz}` and `\usetikzlibrary{positioning, arrows.meta, calc, fit}`.
   - Define colorblind-safe Okabe-Ito / Cell palette entries once in preamble via `\definecolor{<name>}{HTML}{<HEX>}` — never hardcode hex colors inside `\begin{tikzpicture}...\end{tikzpicture}`.
2. **Parametric `\def` Macros & Semantic Node IDs**:
   - Expose spacing (`\def\StageGap{...}`), box dimensions (`\def\BoxW{...}`, `\def\BoxH{...}`), and line widths at the top of the `tikzpicture` so source-preserving edits modify macros cleanly without re-authoring coordinates.
   - Use semantic node names (`inlet`, `reactor`, `detector`, `outlet`), never generic `(a)`, `(b)`.
3. **`.meta.json` `edit_contract` Validation**:
   - `validate_tikz_contract.py` verifies `documentclass{standalone}`, presence of every `edit_contract.parameters[].name` `\def` in `.tex`, presence of every `edit_contract.styles[]` `/.style=` in `.tex`, and zero inline hex codes inside the `tikzpicture` body.
