#!/usr/bin/env python3
"""Offline LaTeX manuscript builder, root-cause diagnostics, semantic ref/bib auditor,
two-column widow/orphan & overfull layout inspector, and real PDF page verifier."""

from __future__ import annotations

import argparse
import difflib
from hashlib import file_digest, sha256
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import preflight_probe

SOURCE_TYPES = {
    ".tex", ".bib", ".bst", ".sty", ".cls", ".bbl", ".def", ".cfg",
    ".clo", ".fd", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".csv",
    ".txt", ".dat", ".svg", ".tikz", ".pgf", ".bbx", ".cbx", ".dbx",
}
ENGINES = ("latexmk", "xelatex", "lualatex", "pdflatex", "tectonic")
BUILD_TIMEOUT = 60


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write JSON so a timeout or crash never leaves a truncated result.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _escape_latex_text(text: str) -> str:
    """Escape LaTeX special characters outside inline $...$ math spans."""
    parts = re.split(r"(\$[^$\n]+?\$)", text)
    out: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(part)
            continue
        escaped = (
            part.replace("\\", r"\textbackslash{}")
            .replace("&", r"\&")
            .replace("%", r"\%")
            .replace("#", r"\#")
            .replace("_", r"\_")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("~", r"\textasciitilde{}")
            .replace("^", r"\textasciicircum{}")
        )
        out.append(escaped)
    return "".join(out)


def _scaffold_tex_from_prose(input_dir: Path, emitted_dir: Path, case: dict[str, Any]) -> tuple[Path | None, list[str]]:
    """When /input contains only Markdown/text drafts (no .tex), synthesize a compilable LaTeX scaffold."""
    draft_files = sorted(
        p for p in input_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".md", ".txt"}
        and p.name.lower() not in {"readme.md", "task.md"}
        and not p.is_symlink()
        and p.resolve().is_relative_to(input_dir.resolve())
        and not any(part.startswith(".") for part in p.relative_to(input_dir).parts)
    )
    if not draft_files:
        # Fall back to task.md if it is the only prose input
        task_md = input_dir / "task.md"
        if task_md.is_file():
            draft_files = [task_md]
        else:
            return None, []

    title = str(case.get("title") or (case.get("source") or {}).get("title") or "Scientific Manuscript Draft")
    body_lines: list[str] = []
    used_sources: list[str] = []
    for df in draft_files:
        rel = df.relative_to(input_dir).as_posix()
        used_sources.append(rel)
        raw = df.read_text(encoding="utf-8", errors="replace")
        for ln in raw.splitlines():
            st = ln.strip()
            if not st:
                body_lines.append("")
            elif st.startswith("# "):
                if title == "Scientific Manuscript Draft":
                    title = st[2:].strip()
                else:
                    body_lines.append(f"\\section{{{_escape_latex_text(st[2:].strip())}}}")
            elif st.startswith("## "):
                body_lines.append(f"\\section{{{_escape_latex_text(st[3:].strip())}}}")
            elif st.startswith("### "):
                body_lines.append(f"\\subsection{{{_escape_latex_text(st[4:].strip())}}}")
            else:
                body_lines.append(_escape_latex_text(st))

    has_cjk = any(0x3400 <= ord(c) <= 0x9FFF for c in ("\n".join(body_lines) + title))
    preamble = [
        "\\documentclass[11pt]{article}",
        "\\usepackage[utf8]{inputenc}" if not has_cjk else "\\usepackage{xeCJK}",
        "\\usepackage{amsmath,booktabs}",
        "\\usepackage[margin=22mm]{geometry}",
        "\\widowpenalty=10000",
        "\\clubpenalty=10000",
        f"\\title{{{_escape_latex_text(title)}}}",
        "\\author{Draft Author}",
        "\\date{}",
        "\\begin{document}",
        "\\maketitle",
    ]
    if not any(ln.strip() for ln in body_lines):
        body_lines.append("Scaffold generated from supplied non-TeX input materials.")
    tex_content = "\n".join(preamble + [""] + body_lines + ["", "\\end{document}", ""])
    emitted_dir.mkdir(parents=True, exist_ok=True)
    scaffold_path = emitted_dir / "main.tex"
    scaffold_path.write_text(tex_content, encoding="utf-8")
    return scaffold_path, used_sources

# Log / static patterns
UNRESOLVED_RE = re.compile(
    r"undefined (?:references|citations)|(?:Citation|Reference)\s+[`']?[^'`\s]+['`]?\s+.*undefined|There were undefined (?:references|citations)",
    re.I,
)
MISSING_TEX_ASSET_RE = re.compile(r"(?:File|file)\s+[`'\u2018]([^'`\u2019\r\n]+)['`\u2019]\s+not found")
TEX_LOCATION_RE = re.compile(r"(?m)^(?:(?:error|warning):\s*)?([^\s:]+\.(?:tex|bib|sty|cls)):(\d+):\s*(.+)$")
OVERFULL_HBOX_RE = re.compile(
    r"Overfull \\hbox \(([0-9.]+)pt too wide\)(?: in paragraph at lines (\d+)--(\d+)| detected at line (\d+)|.*)"
)
OVERFULL_VBOX_RE = re.compile(r"Overfull \\vbox \(([0-9.]+)pt too high\)")
FLOAT_TOO_LARGE_RE = re.compile(r"Float too large for page by ([0-9.]+)pt")
BIBER_VERSION_MISMATCH_RE = re.compile(
    r"(?:Found biblatex control file version|biber.*version.*mismatch|Incompatible biblatex/biber|control file version \S+.*expected)",
    re.I,
)
BIBTEX_OVERFLOW_RE = re.compile(
    r"(?:exceeded BibTeX's|Sorry---you've exceeded|capacity exceeded.*bibtex|panicked at.*bibtex|buffer size exceeded)",
    re.I,
)
UNDEFINED_CS_RE = re.compile(r"Undefined control sequence\b")
FONT_MISSING_RE = re.compile(r"(?:Missing character:|Font .* not loadable|Package fontspec Error)", re.I)

REF_CALL_RE = re.compile(r"\\(?:(auto|c|C|eq|page)?ref\*?)\s*\{([^{}]+)\}")
CITE_CALL_RE = re.compile(r"\\(?:[a-zA-Z]*cite[a-zA-Z]*\*?)(?:\[[^\]]*\])*\s*\{([^{}]+)\}")
LABEL_DEF_RE = re.compile(r"\\label\s*\{([^{}]+)\}")
BIB_ENTRY_RE = re.compile(r"@\s*(?!comment\b|preamble\b|string\b)[a-zA-Z]+\s*\{\s*([^,\s{}]+)\s*,", re.I)
SECTION_CMD_RE = re.compile(r"\\(?:section|subsection|subsubsection|chapter)\*?\s*\{([^{}]+)\}")
MAGIC_PROGRAM_RE = re.compile(r"(?mi)^\s*%\s*!\s*TeX\s+program\s*=\s*([a-z0-9_-]+)")
MAGIC_BIB_RE = re.compile(r"(?mi)^\s*%\s*!\s*(?:BIB|TeX)\s+(?:TS-)?program\s*=\s*(biber|bibtex|bibtex8)", re.I)
TWOCOLUMN_RE = re.compile(r"\\documentclass(?:\[[^\]]*\b(?:twocolumn|sigconf|conference|journal)\b[^\]]*\])?\{(?:IEEEtran|acmart|revtex|aaai|icml|cvpr|article)\}|\\twocolumn\b", re.I)

# Prose keyword -> expected label prefix mapping for semantic ref type check
PROSE_TO_PREFIX = (
    (re.compile(r"\b(?:Section|Sec\.|Sect\.)\s*~?\s*$", re.I), {"sec", "sect", "section", "ch", "chap", "app", "appendix"}),
    (re.compile(r"\b(?:Figure|Fig\.)\s*~?\s*$", re.I), {"fig", "figure"}),
    (re.compile(r"\b(?:Table|Tab\.)\s*~?\s*$", re.I), {"tab", "table", "tbl"}),
    (re.compile(r"\b(?:Equation|Eq\.)\s*~?\s*\(?\s*$", re.I), {"eq", "eqn", "equation"}),
    (re.compile(r"\b(?:Algorithm|Alg\.)\s*~?\s*$", re.I), {"alg", "algo", "algorithm"}),
    (re.compile(r"\b(?:Appendix|App\.)\s*~?\s*$", re.I), {"app", "appendix", "sec"}),
)
KNOWN_TYPED_PREFIXES = {"sec", "sect", "section", "ch", "chap", "fig", "figure", "tab", "table", "tbl", "eq", "eqn", "equation", "alg", "algo", "algorithm", "app", "appendix"}


def strip_tex_comments(text: str) -> str:
    """Remove ordinary TeX comments while preserving escaped percent signs and line numbers."""
    visible_lines: list[str] = []
    for line in text.splitlines():
        cut = len(line)
        for idx, ch in enumerate(line):
            if ch != "%":
                continue
            bs = 0
            p = idx - 1
            while p >= 0 and line[p] == "\\":
                bs += 1
                p -= 1
            if bs % 2 == 0:
                cut = idx
                break
        visible_lines.append(line[:cut])
    return "\n".join(visible_lines)


def choose_entry(root: Path, sources: list[Path], requested: str | None) -> tuple[Path | None, str | None]:
    """Select a single compilable entry .tex file, preferring requested or main.tex."""
    documents: list[Path] = []
    for source in sources:
        if source.suffix.lower() != ".tex":
            continue
        text = source.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(?m)^[^%\n]*\\documentclass\b", text):
            documents.append(source)
    if requested:
        norm_req = requested.replace("\\", "/").lstrip("./")
        selected = next((p for p in documents if p.relative_to(root).as_posix() == norm_req), None)
        return selected, None if selected else f"Requested TeX entry is unavailable: {requested}"
    main = root / "main.tex"
    if main in documents:
        return main, None
    if len(documents) == 1:
        return documents[0], None
    if documents:
        return None, "Several compilable TeX files exist: " + ", ".join(
            sorted(path.relative_to(root).as_posix() for path in documents)
        )
    return None, r"No supplied .tex contains a \documentclass declaration."


def inspect_directives_and_packages(entry: Path, inventory: str) -> dict[str, Any]:
    """Detect TeX magic comments, unicode/CJK requirements, bib backend, and directive conflicts."""
    raw_entry = entry.read_text(encoding="utf-8", errors="ignore")
    magic_prog_m = MAGIC_PROGRAM_RE.search(raw_entry)
    magic_bib_m = MAGIC_BIB_RE.search(raw_entry)
    magic_program = magic_prog_m.group(1).lower() if magic_prog_m else None
    magic_bib = magic_bib_m.group(1).lower() if magic_bib_m else None

    unicode_needed = bool(
        re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(?:fontspec|xeCJK|luatexja)\b|\\usepackage(?:\[[^\]]*\])?\{ctex|\\documentclass(?:\[[^\]]*\])?\{ctex", inventory)
    )
    uses_biblatex = bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\bbiblatex\b|\\addbibresource\b", inventory))
    biblatex_bibtex_opt = bool(re.search(r"\\usepackage\[[^\]]*backend\s*=\s*bibtex[^\]]*\]\{biblatex\}", inventory))
    uses_bibtex_cmd = bool(re.search(r"\\bibliography\s*\{", inventory))

    expected_bib_backend: str | None = None
    if uses_biblatex and not biblatex_bibtex_opt:
        expected_bib_backend = "biber"
    elif uses_bibtex_cmd or biblatex_bibtex_opt:
        expected_bib_backend = "bibtex"

    conflicts: list[dict[str, Any]] = []
    if magic_program == "pdflatex" and unicode_needed:
        conflicts.append({
            "code": "TEX_DIRECTIVE_CONFLICT",
            "file": entry.name,
            "message": "Magic comment requests pdflatex, but source loads fontspec/xeCJK/ctex which requires xelatex or lualatex.",
        })
    if magic_bib and expected_bib_backend and magic_bib != expected_bib_backend:
        conflicts.append({
            "code": "TEX_BIB_BACKEND_CONFLICT",
            "file": entry.name,
            "message": f"Magic comment sets bibliography tool {magic_bib!r}, conflicting with preamble backend {expected_bib_backend!r}.",
        })
    if uses_biblatex and uses_bibtex_cmd:
        conflicts.append({
            "code": "TEX_BIB_BACKEND_CONFLICT",
            "file": entry.name,
            "message": "Project mixes BibLaTeX (\\usepackage{biblatex}/\\addbibresource) with legacy \\bibliography{...}.",
        })

    is_twocolumn = bool(TWOCOLUMN_RE.search(inventory))
    widow_penalty_m = re.search(r"\\widowpenalty\s*=?\s*(\d+)", inventory)
    club_penalty_m = re.search(r"\\clubpenalty\s*=?\s*(\d+)", inventory)

    return {
        "magic_program": magic_program,
        "magic_bib": magic_bib,
        "unicode_needed": unicode_needed,
        "uses_biblatex": uses_biblatex,
        "expected_bib_backend": expected_bib_backend,
        "is_twocolumn": is_twocolumn,
        "widow_penalty": int(widow_penalty_m.group(1)) if widow_penalty_m else None,
        "club_penalty": int(club_penalty_m.group(1)) if club_penalty_m else None,
        "conflicts": conflicts,
    }


def compiler_commands(
    entry: Path,
    meta: dict[str, Any],
    requested: str | None,
) -> tuple[str | None, list[list[str]], str | None]:
    """Select an installed TeX toolchain and return (engine_name, commands, note)."""
    available = {eng for eng in ENGINES if shutil.which(eng)}
    unicode_needed = meta["unicode_needed"]
    expected_bib = meta["expected_bib_backend"]

    if requested and requested not in available:
        return None, [], f"Requested engine {requested!r} is not installed (available: {sorted(available)})."

    # If magic comment conflicted with unicode_needed and user did not force --engine, avoid pdflatex trap.
    effective_req = requested
    note = None
    if effective_req == "pdflatex" and unicode_needed and not requested:
        effective_req = None
        note = "Overrode conflicting pdflatex magic directive because fontspec/ctex requires xelatex/lualatex."

    if ("latexmk" in available and not effective_req) or effective_req == "latexmk":
        if unicode_needed and shutil.which("xelatex"):
            mode = "-xelatex"
        elif unicode_needed and shutil.which("lualatex"):
            mode = "-lualatex"
        elif unicode_needed:
            return None, [], "Source requires xelatex/lualatex (fontspec/ctex), which is not installed."
        else:
            mode = "-pdf"
        cmd = [
            "latexmk", "-norc", mode, "-interaction=nonstopmode", "-halt-on-error",
            "-file-line-error", "-latexoption=-no-shell-escape",
        ]
        if expected_bib == "biber":
            cmd.append("-biber")
        cmd.append(entry.name)
        return "latexmk", [cmd], note

    order = ("xelatex", "lualatex", "pdflatex") if unicode_needed else ("pdflatex", "xelatex", "lualatex")
    for eng in order:
        if eng not in available or (effective_req and eng != effective_req):
            continue
        if unicode_needed and eng == "pdflatex":
            continue
        tex = [eng, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "-no-shell-escape", entry.name]
        bib_cmd: list[str] | None = None
        if expected_bib == "biber":
            if shutil.which("biber"):
                bib_cmd = ["biber", entry.stem]
            else:
                note = "Source requires biber, but biber binary is not installed in this environment."
        elif expected_bib == "bibtex":
            if shutil.which("bibtex"):
                bib_cmd = ["bibtex", entry.stem]
            else:
                note = "Source requires bibtex, but bibtex binary is not installed in this environment."
        cmds = [tex, bib_cmd, tex, tex] if bib_cmd else [tex, tex]
        return eng, cmds, note

    if "tectonic" in available and (not effective_req or effective_req == "tectonic"):
        return "tectonic", [["tectonic", "--untrusted", "--only-cached", "--keep-logs", entry.name]], note

    return None, [], "No compatible TeX engine found in PATH."


def classify_diagnostics(build_text: str) -> list[dict[str, Any]]:
    """Classify TeX/BibTeX/Biber logs into root-cause vs cascading structured diagnostics."""
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()

    def add(category: str, severity: str, file_name: str, line: int | None, message: str, hint: str, root_cause: bool) -> None:
        key = (category, file_name, line)
        if key in seen:
            return
        seen.add(key)
        items.append({
            "category": category,
            "severity": severity,
            "file": file_name,
            "line": line,
            "message": message[:240],
            "is_root_cause": root_cause,
            "remediation_hint": hint,
        })

    if BIBER_VERSION_MISMATCH_RE.search(build_text):
        add(
            "biblatex_biber_version_mismatch", "fatal_error", "build_log", None,
            "BibLaTeX control file (.bcf) version conflicts with installed Biber binary.",
            "Align BibLaTeX/Biber versions or switch to compatible natbib/bibtex backend if permitted by template.",
            True,
        )
    if BIBTEX_OVERFLOW_RE.search(build_text):
        add(
            "bibtex_buffer_or_author_overflow", "fatal_error", "references.bib", None,
            "BibTeX exceeded internal buffer/capacity (often caused by oversized author lists).",
            "Truncate excessive author lists with 'and others' per journal style or use biblatex+biber.",
            True,
        )
    for asset in sorted({m for m in MISSING_TEX_ASSET_RE.findall(build_text) if "#" not in m}):
        cat = "missing_package_or_cls" if asset.endswith((".sty", ".cls", ".def", ".clo")) else "missing_input_or_figure"
        add(
            cat, "fatal_error", asset, None,
            f"Required TeX asset not found: {asset}",
            "Verify relative path inside editable/ or check whether the journal .cls/.sty is available offline.",
            True,
        )
    for m in TEX_LOCATION_RE.finditer(build_text):
        fname, lno, msg = m.group(1).removeprefix("./"), int(m.group(2)), m.group(3).strip()
        if UNDEFINED_CS_RE.search(msg):
            add("undefined_control_sequence", "fatal_error", fname, lno, msg,
                "Check missing \\usepackage, typo in macro name, or unescaped special character (_, %, &, #).", True)
        elif FONT_MISSING_RE.search(msg):
            add("font_or_glyph_missing", "fatal_error", fname, lno, msg,
                "Switch to an installed system font or use xelatex/lualatex for CJK/Unicode glyphs.", True)
        elif "missing $" in msg.lower() or "extra }" in msg.lower() or "ended by \\end" in msg.lower():
            add("math_or_env_mismatch", "fatal_error", fname, lno, msg,
                "Check unescaped '_'/'^' outside math mode or mismatched \\begin{...}/\\end{...} delimiters.", True)
        else:
            is_root = len([x for x in items if x["is_root_cause"]]) == 0
            add("tex_compile_error", "fatal_error", fname, lno, msg,
                "Inspect source line and preceding macro/environment boundary.", is_root)

    for m in OVERFULL_HBOX_RE.finditer(build_text):
        pts = float(m.group(1))
        if pts >= 1.5:
            lno = int(m.group(2) or m.group(4)) if (m.group(2) or m.group(4)) else None
            add("overfull_hbox", "layout_warning", "tex_log", lno,
                f"Overfull \\hbox ({pts:.2f}pt too wide)",
                "Wrap long URLs/identifiers, adjust tabularx column widths, or cap figure width to \\linewidth.", False)
    for m in FLOAT_TOO_LARGE_RE.finditer(build_text):
        pts = float(m.group(1))
        add("float_too_large", "layout_warning", "tex_log", None,
            f"Float too large for page by {pts:.2f}pt",
            "Reduce figure height or split wide table with longtable/table*.", False)

    return items


def audit_static_bib_and_refs(input_root: Path, sources: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Static cross-file audit of labels, refs, bib keys, ref-type mismatches, and self-section warnings."""
    labels: dict[str, list[dict[str, Any]]] = {}
    bib_keys: set[str] = set()
    ref_calls: list[dict[str, Any]] = []
    cite_calls: list[dict[str, Any]] = []

    for src in sources:
        rel = src.relative_to(input_root).as_posix()
        ext = src.suffix.lower()
        if ext == ".bib":
            raw = src.read_text(encoding="utf-8", errors="replace")
            for m in BIB_ENTRY_RE.finditer(raw):
                bib_keys.add(m.group(1).strip())
        elif ext == ".tex":
            visible = strip_tex_comments(src.read_text(encoding="utf-8", errors="replace"))
            lines = visible.splitlines()
            current_section_title = ""
            current_section_labels: set[str] = set()
            for lno, line in enumerate(lines, 1):
                sm = SECTION_CMD_RE.search(line)
                if sm:
                    current_section_title = sm.group(1).strip()
                    current_section_labels = set()
                for lm in LABEL_DEF_RE.finditer(line):
                    lbl = lm.group(1).strip()
                    labels.setdefault(lbl, []).append({"file": rel, "line": lno, "section": current_section_title})
                    if lbl.split(":", 1)[0].lower() in {"sec", "sect", "section", "ch", "chap"}:
                        current_section_labels.add(lbl)
                for rm in REF_CALL_RE.finditer(line):
                    lbl = rm.group(2).strip()
                    prefix_ctx = line[: rm.start()]
                    ref_calls.append({
                        "file": rel,
                        "line": lno,
                        "label": lbl,
                        "prefix_context": prefix_ctx[-48:],
                        "full_line": line.strip()[:160],
                        "current_section": current_section_title,
                        "current_section_labels": sorted(current_section_labels),
                    })
                for cm in CITE_CALL_RE.finditer(line):
                    for key in [k.strip() for k in cm.group(1).split(",") if k.strip()]:
                        cite_calls.append({"file": rel, "line": lno, "key": key})

    duplicate_labels = {k: v for k, v in labels.items() if len(v) > 1}
    undefined_labels = [r for r in ref_calls if r["label"] not in labels]
    missing_bib_keys = [c for c in cite_calls if bib_keys and c["key"] not in bib_keys]

    ref_type_mismatches: list[dict[str, Any]] = []
    semantic_ref_warnings: list[dict[str, Any]] = []
    for r in ref_calls:
        lbl = r["label"]
        if ":" in lbl:
            lbl_prefix = lbl.split(":", 1)[0].lower()
            if lbl_prefix in KNOWN_TYPED_PREFIXES:
                for prose_re, allowed in PROSE_TO_PREFIX:
                    if prose_re.search(r["prefix_context"]) and lbl_prefix not in allowed:
                        ref_type_mismatches.append({
                            "file": r["file"],
                            "line": r["line"],
                            "label": lbl,
                            "context": r["prefix_context"].strip(),
                            "message": f"{r['file']}:{r['line']} prose cue mismatches label prefix {lbl_prefix!r} ({lbl}).",
                        })
                        break
        # Self-section reference warning when prose points to a different conceptual section (e.g. steps/method inside Results)
        if lbl in r["current_section_labels"]:
            low_line = r["full_line"].lower()
            cur_sec = r["current_section"].lower()
            if ("result" in cur_sec and any(w in low_line for w in ("method", "step", "procedure", "protocol", "setup", "algorithm"))) or (
                "method" in cur_sec and any(w in low_line for w in ("result", "finding", "discussion", "conclusion"))
            ):
                semantic_ref_warnings.append({
                    "file": r["file"],
                    "line": r["line"],
                    "label": lbl,
                    "current_section": r["current_section"],
                    "excerpt": r["full_line"],
                    "message": (
                        f"{r['file']}:{r['line']} references current section label {lbl!r} while prose mentions another section role; "
                        "verify intended target or declare reference_expectations.json."
                    ),
                })

    gaps: list[dict[str, Any]] = []
    if duplicate_labels:
        gaps.append({
            "code": "TEX_DUPLICATE_LABEL",
            "labels": sorted(duplicate_labels.keys()),
            "message": f"Duplicate \\label definitions detected: {sorted(duplicate_labels.keys())}",
        })
    if ref_type_mismatches:
        gaps.append({
            "code": "TEX_REF_TYPE_MISMATCH",
            "items": ref_type_mismatches,
            "message": f"{len(ref_type_mismatches)} cross-reference prose/label type mismatch(es) detected.",
        })
    if missing_bib_keys:
        uniq = sorted({c["key"] for c in missing_bib_keys})
        gaps.append({
            "code": "TEX_MISSING_BIB_KEY",
            "keys": uniq,
            "message": f"Citation keys not found in supplied .bib files: {uniq}",
        })

    audit = {
        "defined_labels_count": len(labels),
        "bib_keys_count": len(bib_keys),
        "ref_calls_count": len(ref_calls),
        "cite_calls_count": len(cite_calls),
        "duplicate_labels": duplicate_labels,
        "undefined_labels_static": undefined_labels,
        "missing_bib_keys": missing_bib_keys,
        "ref_type_mismatches": ref_type_mismatches,
        "semantic_ref_warnings": semantic_ref_warnings,
    }
    return audit, gaps


def validate_reference_expectations(
    input_root: Path,
    sources: list[Path],
    expectations: object,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Deterministically verify author-declared literal \\ref targets; never infer intent."""
    configured = expectations is not None and expectations != []
    report: dict[str, Any] = {"configured": configured, "checks": []}
    gaps: list[dict[str, Any]] = []
    if not configured:
        return report, gaps
    if not isinstance(expectations, list):
        msg = "reference_expectations must be a JSON list when supplied."
        gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "message": msg})
        report["checks"].append({"status": "invalid", "message": msg})
        return report, gaps

    source_by_rel = {p.relative_to(input_root).as_posix(): p for p in sources}
    label_locations: dict[str, list[str]] = {}
    for src in sources:
        if src.suffix.lower() != ".tex":
            continue
        vis = strip_tex_comments(src.read_text(encoding="utf-8", errors="replace"))
        for m in LABEL_DEF_RE.finditer(vis):
            label_locations.setdefault(m.group(1).strip(), []).append(src.relative_to(input_root).as_posix())

    for idx, item in enumerate(expectations, 1):
        check: dict[str, Any] = {"index": idx, "status": "invalid"}
        report["checks"].append(check)
        if not isinstance(item, dict):
            msg = f"reference_expectations[{idx - 1}] must be an object."
            check["message"] = msg
            gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "index": idx, "message": msg})
            continue
        rel_src = item.get("source")
        line_no = item.get("line")
        occ = item.get("occurrence", 1)
        exp_lbl = item.get("expected_label")
        if (
            not isinstance(rel_src, str)
            or "\\" in rel_src
            or PurePosixPath(rel_src).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(rel_src).parts)
            or not isinstance(line_no, int) or isinstance(line_no, bool) or line_no < 1
            or not isinstance(occ, int) or isinstance(occ, bool) or occ < 1
            or not isinstance(exp_lbl, str) or not exp_lbl.strip()
        ):
            msg = f"reference_expectations[{idx - 1}] needs safe relative source, positive int line/occurrence, and expected_label."
            check["message"] = msg
            gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "index": idx, "message": msg})
            continue

        exp_lbl = exp_lbl.strip()
        check.update({"source": rel_src, "line": line_no, "occurrence": occ, "expected_label": exp_lbl})
        src_path = source_by_rel.get(rel_src)
        if src_path is None:
            msg = f"Expected reference source not among supplied TeX files: {rel_src}"
            check["message"] = msg
            gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "index": idx, "message": msg})
            continue
        defs = label_locations.get(exp_lbl, [])
        if len(defs) != 1:
            msg = f"Expected label {exp_lbl!r} must be defined exactly once; found {len(defs)}."
            check["message"] = msg
            check["expected_label_definitions"] = defs
            gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "index": idx, "message": msg})
            continue

        src_lines = strip_tex_comments(src_path.read_text(encoding="utf-8", errors="replace")).splitlines()
        if line_no > len(src_lines):
            msg = f"Reference line {line_no} outside {rel_src} ({len(src_lines)} lines)."
            check["message"] = msg
            gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "index": idx, "message": msg})
            continue

        refs = [m.group(2).strip() for m in REF_CALL_RE.finditer(src_lines[line_no - 1])]
        if occ > len(refs):
            msg = f"No \\ref occurrence {occ} found on {rel_src}:{line_no}."
            check["message"] = msg
            gaps.append({"code": "TEX_REFERENCE_EXPECTATION_INVALID", "index": idx, "message": msg})
            continue

        actual_lbl = refs[occ - 1]
        check["actual_label"] = actual_lbl
        if actual_lbl != exp_lbl:
            check["status"] = "mismatch"
            msg = f"{rel_src}:{line_no} \\ref targets {actual_lbl!r}; author-declared target is {exp_lbl!r}."
            check["message"] = msg
            gaps.append({
                "code": "TEX_REFERENCE_TARGET_MISMATCH",
                "index": idx,
                "source": rel_src,
                "line": line_no,
                "actual_label": actual_lbl,
                "expected_label": exp_lbl,
                "message": msg,
            })
        else:
            check["status"] = "pass"
            check["target_definition"] = defs[0]
    return report, gaps


def compile_project(entry: Path, meta: dict[str, Any], requested: str | None, timeout: int) -> dict[str, Any]:
    """Run TeX build passes in an isolated temp directory, preserving true per-step exit codes."""
    engine, commands, route_note = compiler_commands(entry, meta, requested)
    output: list[str] = []
    if route_note:
        output.append(f"[router] {route_note}")
    step_results: list[dict[str, Any]] = []
    failed_commands: list[dict[str, Any]] = []
    timed_out = False
    version: str | None = None

    if engine:
        try:
            ident = subprocess.run(
                [engine, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
            )
            lines = (ident.stdout or ident.stderr).splitlines()
            version = lines[0] if lines else None
        except OSError:
            version = None

    last_process_text = ""
    for command in commands:
        t0 = time.monotonic()
        try:
            completed = subprocess.run(
                command, cwd=entry.parent, timeout=timeout, capture_output=True,
                text=True, encoding="utf-8", errors="replace", check=False,
            )
            dur = round(time.monotonic() - t0, 3)
            rc = completed.returncode
            last_process_text = completed.stdout + "\n" + completed.stderr
            output.extend(("$ " + " ".join(command), completed.stdout, completed.stderr))
            step_results.append({
                "tool": command[0],
                "command": " ".join(command),
                "returncode": rc,
                "duration_sec": dur,
            })
            if rc != 0:
                failed_commands.append({"tool": command[0], "returncode": rc})
                if command[0] not in {"bibtex", "biber"}:
                    break
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            dur = round(time.monotonic() - t0, 3)
            step_results.append({
                "tool": command[0],
                "command": " ".join(command),
                "returncode": 124,
                "duration_sec": dur,
                "timed_out": True,
            })
            failed_commands.append({"tool": command[0], "returncode": 124})
            output.append("Timed out: " + " ".join(command))
            for stream in (exc.stdout, exc.stderr):
                if isinstance(stream, bytes):
                    output.append(stream.decode("utf-8", errors="replace"))
                elif stream:
                    output.append(stream)
            break
        except OSError as exc:
            step_results.append({"tool": command[0], "command": " ".join(command), "returncode": 127, "error": str(exc)})
            failed_commands.append({"tool": command[0], "returncode": 127})
            output.append(str(exc))
            break

    tex_log = entry.with_suffix(".log")
    blg_log = entry.with_suffix(".blg")
    final_log = tex_log.read_text(encoding="utf-8", errors="replace") if tex_log.is_file() else ""
    if blg_log.is_file():
        output.extend(("\n--- Bibliography log (.blg) ---", blg_log.read_text(encoding="utf-8", errors="replace")))
    if final_log:
        output.extend(("\n--- Final TeX log (.log) ---", final_log))

    # True composite returncode: non-zero if ANY step failed or timed out
    if not step_results:
        true_rc = None
    elif failed_commands:
        true_rc = failed_commands[0]["returncode"]
    else:
        true_rc = 0

    pdf = entry.with_suffix(".pdf")
    valid_pdf = False
    if not timed_out and pdf.is_file():
        # Only mark pdf valid if final tex step succeeded (rc == 0) AND header starts with %PDF-
        tex_steps = [s for s in step_results if s["tool"] not in {"bibtex", "biber"}]
        if tex_steps and tex_steps[-1]["returncode"] == 0:
            with pdf.open("rb") as f:
                valid_pdf = f.read(5) == b"%PDF-"

    build_text = "\n".join(line for line in output if line)
    unresolved = list(dict.fromkeys(
        line.strip() for line in (final_log or last_process_text).splitlines() if UNRESOLVED_RE.search(line)
    ))
    overfull_hboxes = [
        {"excess_pt": float(m.group(1)), "line_start": int(m.group(2) or m.group(4)) if (m.group(2) or m.group(4)) else None}
        for m in OVERFULL_HBOX_RE.finditer(final_log or build_text)
    ]
    overfull_vboxes = [float(m.group(1)) for m in OVERFULL_VBOX_RE.finditer(final_log or build_text)]
    float_too_large = [float(m.group(1)) for m in FLOAT_TOO_LARGE_RE.finditer(final_log or build_text)]
    diagnostics = classify_diagnostics(build_text)

    return {
        "compiler_available": engine is not None,
        "compiler_used": engine,
        "compiler_requested": requested,
        "compiler_version": version,
        "route_note": route_note,
        "returncode": true_rc,
        "step_results": step_results,
        "failed_commands": failed_commands,
        "compiler_invocations": len(step_results),
        "timed_out": timed_out,
        "timeout_seconds_per_tool": timeout,
        "pdf_generated": valid_pdf,
        "unresolved_references": unresolved,
        "missing_inputs": sorted({name for name in MISSING_TEX_ASSET_RE.findall(build_text) if "#" not in name}),
        "offline_bundle_not_cached": engine == "tectonic" and "bundle isn't cached" in build_text,
        "biblatex_biber_mismatch": bool(BIBER_VERSION_MISMATCH_RE.search(build_text)),
        "bibtex_overflow": bool(BIBTEX_OVERFLOW_RE.search(build_text)),
        "overfull_hboxes": overfull_hboxes,
        "overfull_vboxes_pt": overfull_vboxes,
        "float_too_large_pt": float_too_large,
        "diagnostics": diagnostics,
        "log": build_text,
    }


def inspect_compiled_pdf(pdf_path: Path, output_dir: Path, meta: dict[str, Any], render_previews: bool = True) -> dict[str, Any]:
    """Inspect real compiled PDF via pdfinfo/pdftotext/pdffonts/pdftoppm for pages, ?? markers, and widows/orphans."""
    info: dict[str, Any] = {
        "pdf_file": pdf_path.name,
        "pdfinfo_available": bool(shutil.which("pdfinfo")),
        "pdftotext_available": bool(shutil.which("pdftotext")),
        "pdftoppm_available": bool(shutil.which("pdftoppm")),
        "pages": None,
        "page_size": None,
        "unresolved_markers_in_pdf": [],
        "widow_orphan_warnings": [],
        "rendered_previews": [],
        "fonts_checked": False,
        "non_embedded_fonts": [],
    }
    if info["pdfinfo_available"]:
        res = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if res.returncode == 0:
            for ln in res.stdout.splitlines():
                if ln.startswith("Pages:"):
                    try:
                        info["pages"] = int(ln.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif ln.startswith("Page size:"):
                    info["page_size"] = ln.split(":", 1)[1].strip()

    if shutil.which("pdffonts"):
        res = subprocess.run(["pdffonts", str(pdf_path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if res.returncode == 0:
            info["fonts_checked"] = True
            for ln in res.stdout.splitlines()[2:]:
                parts = ln.split()
                if len(parts) >= 5 and parts[-4].lower() == "no":
                    info["non_embedded_fonts"].append(parts[0])

    if info["pdftotext_available"]:
        res = subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        if res.returncode == 0:
            raw_pages = res.stdout.split("\f")
            # Drop trailing empty split after final form-feed
            if raw_pages and not raw_pages[-1].strip():
                raw_pages = raw_pages[:-1]
            info["extracted_page_count"] = len(raw_pages)
            per_page_lines: list[int] = []
            for idx, page_txt in enumerate(raw_pages, 1):
                non_empty = [ln.strip() for ln in page_txt.splitlines() if ln.strip()]
                per_page_lines.append(len(non_empty))
                for ln in non_empty:
                    if re.search(r"\?\?|\[(\?|citation)\]", ln):
                        info["unresolved_markers_in_pdf"].append({"page": idx, "excerpt": ln[:120]})
                # Orphan heading at bottom of page (last non-empty line looks like numbered section header)
                if len(non_empty) >= 4:
                    tail_candidate = non_empty[-1]
                    if tail_candidate.isdigit() and len(non_empty) >= 5:
                        tail_candidate = non_empty[-2]
                    if re.match(r"^(?:\d+(?:\.\d+)*)\s+[A-Z][\w\s-]{2,50}$", tail_candidate):
                        info["widow_orphan_warnings"].append({
                            "page": idx,
                            "type": "orphan_heading_bottom",
                            "excerpt": tail_candidate,
                        })
            # Trailing sparse widow page check (multi-page doc whose final page has <= 3 non-page-number lines)
            if len(raw_pages) >= 2:
                last_lines = [ln.strip() for ln in raw_pages[-1].splitlines() if ln.strip() and not ln.strip().isdigit()]
                if 0 < len(last_lines) <= 3:
                    info["widow_orphan_warnings"].append({
                        "page": len(raw_pages),
                        "type": "trailing_sparse_widow_page",
                        "line_count": len(last_lines),
                        "excerpt": " | ".join(last_lines)[:140],
                    })
            info["per_page_line_counts"] = per_page_lines

    if meta.get("is_twocolumn"):
        wp = meta.get("widow_penalty")
        cp = meta.get("club_penalty")
        if (wp is None or wp < 10000) or (cp is None or cp < 10000):
            info["widow_orphan_warnings"].append({
                "page": None,
                "type": "twocolumn_widow_club_penalty_unset",
                "message": "Two-column document does not set \\widowpenalty=10000 and \\clubpenalty=10000; inspect column breaks visually.",
            })

    if render_previews and info["pdftoppm_available"]:
        prev_dir = output_dir / "previews"
        prev_dir.mkdir(parents=True, exist_ok=True)
        # Render up to first 6 pages for practical inspection budget
        res = subprocess.run(
            ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "6", str(pdf_path), str(prev_dir / "page")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if res.returncode == 0:
            for png in sorted(prev_dir.glob("page-*.png")):
                info["rendered_previews"].append({
                    "path": png.relative_to(output_dir).as_posix(),
                    "bytes": png.stat().st_size,
                })

    return info


def build_unified_diffs(input_root: Path, original_root: Path | None, sources: list[Path]) -> list[dict[str, Any]]:
    """Compute human-auditable line diffs for text files changed against original_root."""
    if not original_root:
        return []
    changes: list[dict[str, Any]] = []
    for src in sources:
        if src.suffix.lower() not in {".tex", ".bib", ".sty", ".cls", ".txt"}:
            continue
        rel = src.relative_to(input_root)
        orig = original_root / rel
        if not orig.is_file():
            changes.append({"file": rel.as_posix(), "action": "added", "diff_lines": 0})
            continue
        old_txt = orig.read_text(encoding="utf-8", errors="replace").splitlines()
        new_txt = src.read_text(encoding="utf-8", errors="replace").splitlines()
        if old_txt != new_txt:
            diff = list(difflib.unified_diff(old_txt, new_txt, fromfile=f"original/{rel.as_posix()}", tofile=f"editable/{rel.as_posix()}", lineterm=""))
            changes.append({
                "file": rel.as_posix(),
                "action": "modified",
                "diff_lines": len(diff),
                "diff_preview": "\n".join(diff[:40]),
            })
    return changes


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    case_id: str,
    requested_entry: str | None,
    requested_engine: str | None,
    original_root: Path | None,
    timeout: int,
    reference_expectations_path: Path | None,
    dry_run_audit: bool = False,
    render_previews: bool = True,
) -> tuple[dict[str, Any], int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_file = input_dir / "case.json"
    if not case_file.is_file() and original_root:
        case_file = original_root / "case.json"
    case = json.loads(case_file.read_text(encoding="utf-8")) if case_file.is_file() else {}
    actual_case_id = case_id or case.get("case_id")
    if not actual_case_id:
        raise ValueError("case_id is required in case.json or --case-id")

    # Phase 1 atomic skeleton write so a hard 900s sandbox timeout never leaves a missing/corrupt result.json
    skeleton: dict[str, Any] = {
        "case_id": actual_case_id,
        "status": "in_progress",
        "deliverable": {"editable_project_dir": "editable", "entry_tex": None, "compiled_pdf": None, "stable_pdf": None},
        "preserved_facts": {"original_input_immutable": True},
        "evidence_map": [],
        "change_log": {"compared_against_original": original_root is not None, "modified_files": []},
        "sources": ["case.json"] if case_file.is_file() else [],
        "uncertainty": "Build initialized; awaiting compilation and verification.",
        "artifacts": ["result.json"],
        "gaps": [],
    }
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        exp_keys = contract.get("required") if isinstance(contract.get("required"), list) else list(contract.keys())
        for k in exp_keys:
            if isinstance(k, str) and k not in skeleton:
                skeleton[k] = None
    _atomic_write_json(output_dir / "result.json", skeleton)

    sources = sorted(
        p for p in input_dir.rglob("*")
        if p.is_file()
        and (p.suffix.lower() in SOURCE_TYPES or p.name == "Makefile")
        and not p.is_symlink()
        and p.resolve().is_relative_to(input_dir.resolve())
        and not any(part.startswith(".") for part in p.relative_to(input_dir).parts)
    )
    emitted = output_dir / "editable"
    entry, entry_error = choose_entry(input_dir, sources, requested_entry)
    scaffolded_from_prose = False
    scaffold_sources: list[str] = []
    compile_root = input_dir
    if entry is None and not requested_entry:
        scaff_entry, scaffold_sources = _scaffold_tex_from_prose(input_dir, emitted, case)
        if scaff_entry is not None:
            scaffolded_from_prose = True
            entry = scaff_entry
            entry_error = None
            compile_root = emitted
            sources = [scaff_entry] + [p for p in sources if p.suffix.lower() in {".bib", ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".csv", ".sty", ".cls"}]

    artifacts: list[str] = ["result.json"]
    manifest: list[dict[str, Any]] = []
    findings: list[str] = []
    gaps: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []

    # 1. Copy editable sources safely (never mutate /input) and hash every file
    for source in sources:
        rel = source.relative_to(compile_root) if source.resolve().is_relative_to(compile_root.resolve()) else source.relative_to(input_dir)
        dest = emitted / rel
        if source.resolve() != dest.resolve():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        with source.open("rb") as fh:
            digest = file_digest(fh, "sha256").hexdigest()
        row: dict[str, Any] = {
            "source": rel.as_posix(),
            "artifact": dest.relative_to(output_dir).as_posix(),
            "sha256": digest,
        }
        if original_root:
            prev = original_root / rel
            if prev.is_file() and prev.resolve().is_relative_to(original_root.resolve()):
                with prev.open("rb") as fh:
                    row["previous_sha256"] = file_digest(fh, "sha256").hexdigest()
            else:
                row["previous_sha256"] = None
            row["changed"] = row["previous_sha256"] != digest
        manifest.append(row)
        artifacts.append(row["artifact"])
    if scaffolded_from_prose:
        findings.append(f"No pre-existing .tex entry found; scaffolded compilable LaTeX entry editable/main.tex from prose sources: {scaffold_sources}.")

    # 2. Static Bib + Ref audit
    bib_ref_audit, bib_ref_gaps = audit_static_bib_and_refs(compile_root if scaffolded_from_prose else input_dir, [emitted / m["source"] for m in manifest] if scaffolded_from_prose else sources)
    gaps.extend(bib_ref_gaps)

    # 3. Author-declared reference_expectations check
    if reference_expectations_path:
        try:
            raw_exp = reference_expectations_path.read_bytes()
            exp_obj = json.loads(raw_exp.decode("utf-8"))
            manifest_art = output_dir / "reference_expectations.json"
            if reference_expectations_path.resolve() != manifest_art.resolve():
                manifest_art.write_bytes(raw_exp)
            artifacts.append(manifest_art.name)
            ref_validation, ref_gaps = validate_reference_expectations(input_dir, sources, exp_obj)
            ref_validation["manifest"] = manifest_art.name
            ref_validation["manifest_sha256"] = sha256(raw_exp).hexdigest()
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            msg = f"Could not read author-supplied reference expectations: {exc}"
            ref_validation = {"configured": True, "checks": [{"status": "invalid", "message": msg}]}
            ref_gaps = [{"code": "TEX_REFERENCE_EXPECTATION_INVALID", "message": msg}]
    else:
        ref_validation, ref_gaps = validate_reference_expectations(input_dir, sources, None)
    gaps.extend(ref_gaps)

    # 4. Directive inspection & isolated compilation
    compilation: dict[str, Any] | None = None
    pdf_inspection: dict[str, Any] | None = None
    directives_meta: dict[str, Any] = {}
    compiled_pdf_name: str | None = None

    if entry_error:
        gaps.append({"code": "TEX_ENTRY_UNRESOLVED", "message": entry_error})
    else:
        assert entry is not None
        inventory = "\n".join(
            strip_tex_comments(p.read_text(encoding="utf-8", errors="ignore"))
            for p in sources if p.suffix.lower() in {".tex", ".cls", ".sty"}
        )
        directives_meta = inspect_directives_and_packages(entry, inventory)
        for c in directives_meta["conflicts"]:
            gaps.append(c)

        rel_entry = entry.relative_to(compile_root) if entry.resolve().is_relative_to(compile_root.resolve()) else entry.relative_to(input_dir)
        findings.append(f"Entry: editable/{rel_entry.as_posix()}. Original /input files preserved intact.")
        evidence_map.append({
            "claim": f"Editable TeX entry staged at editable/{rel_entry.as_posix()}",
            "source": rel_entry.as_posix(),
            "sha256": next((m["sha256"] for m in manifest if m["source"] == rel_entry.as_posix()), None),
        })

        if not dry_run_audit:
            with tempfile.TemporaryDirectory(prefix="latex-build-", dir=work_dir) as build_td:
                build_root = Path(build_td)
                for mrow in manifest:
                    rel = Path(mrow["source"])
                    orig = emitted / rel
                    # Do NOT stage a pre-existing PDF matching entry.stem so stale PDFs can never mask build failure
                    if rel == rel_entry.with_suffix(".pdf"):
                        continue
                    staged = build_root / rel
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(orig, staged)

                staged_entry = build_root / rel_entry
                eff_engine = requested_engine or (
                    directives_meta["magic_program"]
                    if directives_meta["magic_program"] in ENGINES and not (
                        directives_meta["magic_program"] == "pdflatex" and directives_meta["unicode_needed"]
                    )
                    else None
                )
                compilation = compile_project(staged_entry, directives_meta, eff_engine, timeout)
                log_path = output_dir / "build_log.txt"
                log_path.write_text(compilation.pop("log") or "No compiler invocation was available.\n", encoding="utf-8")
                artifacts.append(log_path.name)

                stable_pdf_name: str | None = None
                if compilation["pdf_generated"]:
                    bundle_id = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
                    compiled_pdf_name = f"compiled-{bundle_id}.pdf"
                    stable_pdf_name = f"{rel_entry.stem}.pdf"
                    pdf_path = output_dir / compiled_pdf_name
                    stable_pdf_path = output_dir / stable_pdf_name
                    shutil.copyfile(staged_entry.with_suffix(".pdf"), pdf_path)
                    shutil.copyfile(staged_entry.with_suffix(".pdf"), stable_pdf_path)
                    with pdf_path.open("rb") as fh:
                        compilation["pdf_sha256"] = file_digest(fh, "sha256").hexdigest()
                    compilation["stable_pdf"] = stable_pdf_name
                    artifacts.extend([stable_pdf_name, compiled_pdf_name])
                    pdf_inspection = inspect_compiled_pdf(pdf_path, output_dir, directives_meta, render_previews=render_previews)
                    for prev in pdf_inspection.get("rendered_previews", []):
                        artifacts.append(prev["path"])
                    evidence_map.append({
                        "claim": f"Compiled valid PDF {stable_pdf_name} / {compiled_pdf_name} ({pdf_inspection.get('pages')} page(s))",
                        "source": "build_log.txt",
                        "sha256": compilation["pdf_sha256"],
                    })

                if not compilation["compiler_available"]:
                    msg = f"Requested TeX engine unavailable: {eff_engine}" if eff_engine else "No compatible TeX compiler available."
                    gaps.append({"code": "TEX_COMPILER_MISSING", "message": msg})
                elif compilation["timed_out"]:
                    gaps.append({"code": "TEX_BUILD_TIMEOUT", "message": f"Tool invocation exceeded {timeout}s."})
                elif compilation["offline_bundle_not_cached"]:
                    gaps.append({"code": "TEX_CACHE_NOT_READY", "message": "Offline Tectonic bundle cache unavailable."})
                elif compilation["biblatex_biber_mismatch"]:
                    gaps.append({"code": "TEX_BIBLATEX_BIBER_MISMATCH", "message": "BibLaTeX/Biber control file version mismatch."})
                elif compilation["bibtex_overflow"]:
                    gaps.append({"code": "TEX_BIBTEX_CAPACITY_OVERFLOW", "message": "BibTeX capacity/buffer exceeded (check large author lists)."})
                elif compilation["missing_inputs"]:
                    gaps.append({"code": "TEX_DEPENDENCY_MISSING", "files": compilation["missing_inputs"]})
                elif not compilation["pdf_generated"]:
                    gaps.append({"code": "TEX_BUILD_FAILED", "message": "Compilation failed to produce a valid PDF. Inspect build_log.txt."})
                elif compilation["failed_commands"]:
                    gaps.append({
                        "code": "TEX_TOOLCHAIN_ERROR",
                        "failed_commands": compilation["failed_commands"],
                        "message": "At least one build/bibliography step exited non-zero; inspect build_log.txt.",
                    })

                if compilation["unresolved_references"]:
                    gaps.append({
                        "code": "TEX_REFERENCES_UNRESOLVED",
                        "count": len(compilation["unresolved_references"]),
                        "message": "Final TeX log still contains undefined citations or references.",
                    })
                if pdf_inspection and pdf_inspection.get("unresolved_markers_in_pdf"):
                    gaps.append({
                        "code": "PDF_UNRESOLVED_MARKERS_RENDERED",
                        "items": pdf_inspection["unresolved_markers_in_pdf"][:10],
                        "message": "Rendered PDF text contains literal '??' or '[?]' markers.",
                    })

    # 5. Layout audit summary (overfull hbox/vbox + widow/orphan)
    severe_overfull = [
        h for h in (compilation.get("overfull_hboxes", []) if compilation else [])
        if h["excess_pt"] >= 6.0
    ]
    layout_audit = {
        "is_twocolumn": directives_meta.get("is_twocolumn", False),
        "widow_penalty": directives_meta.get("widow_penalty"),
        "club_penalty": directives_meta.get("club_penalty"),
        "overfull_hbox_count": len(compilation.get("overfull_hboxes", [])) if compilation else 0,
        "severe_overfull_hbox_ge_6pt": severe_overfull,
        "float_too_large_pt": compilation.get("float_too_large_pt", []) if compilation else [],
        "widow_orphan_warnings": pdf_inspection.get("widow_orphan_warnings", []) if pdf_inspection else [],
    }
    if severe_overfull:
        findings.append(f"Detected {len(severe_overfull)} severe Overfull \\hbox (>=6pt) warning(s); inspect rendered pages for clipping.")
    if layout_audit["widow_orphan_warnings"]:
        findings.append(f"Detected {len(layout_audit['widow_orphan_warnings'])} widow/orphan layout warning(s).")
    if bib_ref_audit["semantic_ref_warnings"]:
        findings.append(f"Detected {len(bib_ref_audit['semantic_ref_warnings'])} semantic cross-reference warning(s) for author review.")

    # 6. Build full Harbor output_contract payload (case_id, deliverable, preserved_facts, evidence_map, change_log)
    diffs = build_unified_diffs(compile_root if scaffolded_from_prose else input_dir, original_root, [emitted / m["source"] for m in manifest] if scaffolded_from_prose else sources)
    hard_build_codes = {
        "TEX_ENTRY_UNRESOLVED", "TEX_COMPILER_MISSING", "TEX_BUILD_TIMEOUT",
        "TEX_CACHE_NOT_READY", "TEX_BIBLATEX_BIBER_MISMATCH", "TEX_BIBTEX_CAPACITY_OVERFLOW",
        "TEX_DEPENDENCY_MISSING", "TEX_BUILD_FAILED", "TEX_TOOLCHAIN_ERROR",
    }
    has_hard_build_error = any(g.get("code") in hard_build_codes for g in gaps)
    status = "failed" if (has_hard_build_error and not (compilation and compilation.get("pdf_generated"))) else (
        "partial_with_gaps" if gaps else "ok"
    )
    entry_rel_str = (
        f"editable/{(entry.relative_to(compile_root) if entry.resolve().is_relative_to(compile_root.resolve()) else entry.relative_to(input_dir)).as_posix()}"
        if entry else None
    )
    stable_pdf_out = (compilation or {}).get("stable_pdf")

    payload: dict[str, Any] = {
        "case_id": actual_case_id,
        "status": status,
        "deliverable": {
            "editable_project_dir": "editable",
            "entry_tex": entry_rel_str,
            "stable_pdf": stable_pdf_out,
            "compiled_pdf": compiled_pdf_name,
            "build_log": "build_log.txt" if compilation is not None else None,
            "page_previews": [p["path"] for p in (pdf_inspection.get("rendered_previews", []) if pdf_inspection else [])],
        },
        "preserved_facts": {
            "original_input_immutable": True,
            "source_file_count": len(manifest),
            "defined_labels_count": bib_ref_audit["defined_labels_count"],
            "bib_keys_count": bib_ref_audit["bib_keys_count"],
            "source_hashes": {m["source"]: m["sha256"] for m in manifest},
        },
        "evidence_map": evidence_map,
        "change_log": {
            "compared_against_original": original_root is not None,
            "draft_scaffolded_from_markdown": scaffolded_from_prose,
            "scaffold_sources": scaffold_sources,
            "modified_files": diffs,
            "human_confirmations_required": [g["message"] for g in gaps if "message" in g],
            "semantic_ref_warnings": bib_ref_audit["semantic_ref_warnings"],
        },
        "sources": list(dict.fromkeys(
            (["case.json"] if case_file.is_file() else []) + scaffold_sources + [m["source"] for m in manifest]
        )),
        "uncertainty": (
            "All build steps, cross-reference checks, and PDF page inspections passed without blocking gaps."
            if not gaps else
            f"{len(gaps)} gap(s) recorded; inspect gaps, build_log.txt, and rendered page previews before submission."
        ),
        "artifacts": list(dict.fromkeys(artifacts)),
        "metrics": {
            "compiler_invocations": compilation["compiler_invocations"] if compilation else 0,
            "compiler_returncode": compilation["returncode"] if compilation else None,
            "pdf_pages": pdf_inspection.get("pages") if pdf_inspection else 0,
            "overfull_hbox_count": layout_audit["overfull_hbox_count"],
            "unresolved_reference_count": len(compilation["unresolved_references"]) if compilation else 0,
            "gap_count": len(gaps),
        },
        "metric_provenance": {
            "compiler_invocations": "actual compiler/bibliography process executions",
            "compiler_returncode": "first non-zero exit code across build steps, or 0 if all steps passed",
            "pdf_pages": "pdfinfo page count on freshly compiled PDF",
            "overfull_hbox_count": "Overfull \\hbox occurrences parsed from final TeX .log",
        },
        "findings": findings,
        "reference_validation": ref_validation,
        "bib_and_ref_audit": bib_ref_audit,
        "layout_audit": layout_audit,
        "pdf_inspection": pdf_inspection,
        "latex_execution": {
            "directives": directives_meta,
            "compilation": compilation,
            "source_manifest": manifest,
        },
        "gaps": gaps,
    }

    # Ensure every case-specific output_contract key exists at top level (prevent schema KeyErrors)
    if isinstance(contract, dict):
        expected_keys = contract.get("required") if isinstance(contract.get("required"), list) else list(contract.keys())
        missing = sorted(k for k in expected_keys if isinstance(k, str) and k not in payload)
        for k in missing:
            payload[k] = None
        if missing:
            payload["gaps"].append({"code": "OUTPUT_FIELDS_NEED_COMPLETION", "fields": missing, "message": f"Populated placeholder for case-specific fields: {missing}"})
            if payload["status"] == "ok":
                payload["status"] = "partial_with_gaps"

    _atomic_write_json(output_dir / "result.json", payload)

    # Determine true process exit code:
    # - Real compiler/toolchain failure -> non-zero (true compiler returncode or 1)
    # - Hard gaps (missing bib key, duplicate label, ref mismatch, directive conflict) -> 1
    # - Clean ok -> 0
    if has_hard_build_error:
        exit_code = int(compilation["returncode"]) if (compilation and isinstance(compilation.get("returncode"), int) and compilation["returncode"] != 0) else 1
    elif gaps:
        exit_code = 1
    else:
        exit_code = 0
    return payload, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="/input", help="Input TeX project directory")
    parser.add_argument("--output", default="/app/output", help="Output artifacts directory")
    parser.add_argument("--work", default="/app/work", help="Working scratch directory")
    parser.add_argument("--case-id", default="", help="Override case_id when absent in case.json")
    parser.add_argument("--entry", help="Relative TeX entry file (e.g. main.tex)")
    parser.add_argument("--engine", choices=ENGINES, help="Force TeX engine")
    parser.add_argument("--original", type=Path, help="Original immutable source tree for SHA-256 & unified diff comparison")
    parser.add_argument("--reference-expectations", type=Path, help="Author-declared JSON list of literal \\ref targets")
    parser.add_argument("--timeout", type=int, default=BUILD_TIMEOUT, help="Seconds per tool invocation (1-300)")
    parser.add_argument("--dry-run-audit", action="store_true", help="Run static source/bib/ref/directive audit without invoking TeX")
    parser.add_argument("--no-previews", action="store_true", help="Skip pdftoppm PNG page rendering")
    parser.add_argument("--allow-partial-exit0", action="store_true", help="Exit 0 even when partial_with_gaps are recorded (still writes full result.json)")
    args = parser.parse_args(argv)

    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")

    paths = preflight_probe.resolve_default_paths(input_dir=args.input, output_dir=args.output, work_dir=args.work)
    payload, rc = run_pipeline(
        paths["input_dir"],
        paths["output_dir"],
        paths["work_dir"],
        args.case_id,
        args.entry,
        args.engine,
        args.original,
        args.timeout,
        args.reference_expectations,
        dry_run_audit=args.dry_run_audit,
        render_previews=not args.no_previews,
    )
    summary = {
        "case_id": payload["case_id"],
        "status": payload["status"],
        "pdf_generated": bool((payload["latex_execution"].get("compilation") or {}).get("pdf_generated")),
        "compiler_returncode": payload["metrics"].get("compiler_returncode"),
        "pdf_pages": payload["metrics"].get("pdf_pages"),
        "gap_codes": [g.get("code") for g in payload["gaps"]],
        "exit_code": 0 if (args.allow_partial_exit0 and payload["status"] != "failed") else rc,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.allow_partial_exit0 and payload["status"] != "failed":
        return 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
