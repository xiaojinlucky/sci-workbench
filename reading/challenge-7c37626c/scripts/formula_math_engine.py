"""Formula Parsing, MathML/XML Deduplication, Composite SI Dimensional & Numeric Verifier (challenge-7c37626c).

Pure Python 3.12 standard library baseline (optional defusedxml / sympy enhancement).
Produces:
- /app/output/result.json
- /app/output/formula_verification_report.json
- /app/output/formula_notes.md (validated via math_markdown_validator.py)

Key invariants:
- Parses JATS publisher-source.xml & source-formulas.xml without double-counting <alternatives><mml:math> + <tex-math>.
- Extracts prose paragraphs from publisher-source.xml (<p>, <abstract>, <caption>) as well as case.json / MD / TXT / PDF
  so symbol definitions trace back to exact paragraph IDs.
- Intercepts Pix2Text #142 class OCR/transcription noise (unexpected CJK, literal tabs, mojibake, unbalanced braces)
  and halts downstream derivation/computation on affected formulas instead of propagating corrupt tokens.
- Uses general composite SI dimensional algebra (7-dim vector + SI scale factor + order-of-magnitude gap detection).
- Preserves C02-style missing-formula snapshots honestly as formula_coverage_status="insufficient_evidence" or "partial",
  never back-inferring formulas or defaulting unknown variables to 0.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import budget_guard
import final_audit
import gap_gate
import preflight_probe
import result_guard
from audit_chain import append_audit_entry, init_audit_log
from math_ast_guard import (
    MATHEMATICAL_CONSTANTS,
    audit_derivation_chain,
    build_equation_dependency_dag,
    build_symbol_variants,
    check_dimensional_balance,
    check_equivalence,
    evaluate_domain_predicate,
    extract_derivation_assumptions_and_boundaries,
    extract_symbols_from_latex,
    inspect_formula_noise,
    parse_unit_expression,
    parse_xml_formulas,
    safe_eval_ast,
    safe_parse_xml_string,
)
from math_markdown_validator import validate_formula_markdown

LATEX_DISPLAY_RE = re.compile(
    r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\begin\{equation\*?\}(.+?)\\end\{equation\*?\}",
    re.S,
)
UNIT_HINT_RE = re.compile(
    r"(?:\bin\s+units?\s+of|\bin\b|\bunits?[:\s]+|\(|=\s*[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s+)\s*"
    r"([A-Za-z\u00b5\u03bc\u03a9\u00c5%]+(?:\s*[*/^·⋅]\s*(?:\([A-Za-z0-9\u00b5\u03bc\u03a9\u00c5\-\+\^*·⋅/]+\)|[A-Za-z0-9\u00b5\u03bc\u03a9\u00c5\-\+\^]+))*)"
    r"(?=\s*(?:\)|[,.;:]|\(|\b[a-z]+\b|$))"
)
RANGE_HINT_RE = re.compile(
    r"((?:[-+]?[0-9.]+\s*(?:<=|<|≤|to|–|-|~)\s*[A-Za-z_\\α-ωΑ-Ω][A-Za-z0-9_]*\s*(?:<=|<|≤)\s*[-+]?[0-9.]+)|"
    r"(?:[A-Za-z_\\α-ωΑ-Ω][A-Za-z0-9_]*\s*(?:>=|>|<=|<|≥|≤|=)\s*[-+]?[0-9]+(?:\.[0-9]+)?))"
)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _pdf_text(pdf_path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            return "\n\n".join(p.extract_text() or "" for p in doc.pages)
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return "\n\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:
        pass
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, str(pdf_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
    return ""


def _extract_xml_paragraphs(xml_path: Path, relpath: str) -> list[dict[str, str]]:
    """Extract prose paragraphs (<p>, <abstract>, <caption>) from JATS/publisher XML for symbol tracing."""
    paras: list[dict[str, str]] = []
    try:
        raw = xml_path.read_text(encoding="utf-8", errors="replace")
        root = safe_parse_xml_string(raw)
    except Exception:
        return paras

    idx = 0
    for elem in root.iter():
        tag = _strip_ns(elem.tag).lower()
        if tag == "p":
            txt = " ".join("".join(elem.itertext()).split()).strip()
            if txt:
                idx += 1
                pid = elem.attrib.get("id") or f"p{idx:03d}"
                paras.append({"paragraph_id": f"{relpath}:{pid}", "text": txt})
    return paras


def collect_text_paragraphs(input_dir: Path, case_data: dict[str, Any]) -> list[dict[str, str]]:
    """Collect grounded prose blocks from case.json, publisher XML, and MD/TXT/TEX/PDF files."""
    paras: list[dict[str, str]] = []
    for idx, p in enumerate(case_data.get("paragraphs") or [], start=1):
        if isinstance(p, dict):
            pid = str(p.get("paragraph_id") or p.get("id") or f"P{idx:03d}")
            txt = str(p.get("text") or p.get("content") or "").strip()
        else:
            pid = f"P{idx:03d}"
            txt = str(p).strip()
        if txt:
            paras.append({"paragraph_id": pid, "text": txt})

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if fp.name in ("case.json", "task.md"):
            continue
        ext = fp.suffix.lower()
        rel = fp.relative_to(input_dir).as_posix()
        if ext == ".xml":
            # Extract prose paragraphs from publisher-source.xml (or other full-text XML)
            if "source-formulas" not in fp.name.lower():
                paras.extend(_extract_xml_paragraphs(fp, rel))
            continue
        raw = ""
        if ext == ".pdf":
            raw = _pdf_text(fp)
        elif ext in {".md", ".txt", ".tex"}:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        if raw.strip():
            for bno, chunk in enumerate(re.split(r"\n\s*\n", raw), start=1):
                if chunk.strip():
                    paras.append({"paragraph_id": f"{rel}:B{bno:03d}", "text": chunk.strip()})
    return paras


_UNIT_FALSE_WORDS = {
    "the", "a", "an", "and", "or", "is", "are", "was", "to", "of", "for", "with",
    "that", "this", "which", "terms", "acid", "alkaline", "steady", "state",
    "unit", "units", "per", "from", "into", "on", "at", "by", "as", "be",
}


_UNICODE_SUBSCRIPT_CLASS = "\u2080-\u209c\u1d62-\u1d6a"
_AMBIGUOUS_SINGLE_LETTER_WORDS = {"a", "I"}


def _symbol_token_pattern(variant: str) -> str:
    esc = re.escape(variant)
    # Non-ASCII / backslash tokens cannot rely solely on \b at start
    left = (
        rf"(?<![A-Za-z0-9_\\{_UNICODE_SUBSCRIPT_CLASS}])"
        if variant[0] in "\\" or not variant[0].isalnum()
        else rf"(?<![A-Za-z0-9_{_UNICODE_SUBSCRIPT_CLASS}])"
    )
    # Prevent base symbol E from matching E_a, E_{a}, or Eₐ
    right = rf"(?![A-Za-z0-9_{_UNICODE_SUBSCRIPT_CLASS}]|\s*_\s*\{{?[A-Za-z0-9])"
    # Prevent single-letter symbols (e.g., J, m, s, K, V, E) from matching inside composite unit ratios like J/(mol*K) or m/s
    if len(variant) == 1 and variant.isalpha():
        right = rf"(?![A-Za-z0-9_/{_UNICODE_SUBSCRIPT_CLASS}]|\s*_\s*\{{?[A-Za-z0-9]|\s*/\s*(?:\(?mol|\(?s\b|\(?m\b|\(?cm|\(?L\b|\(?K\b|\(?g\b))"
        left = rf"(?<![A-Za-z0-9_/{_UNICODE_SUBSCRIPT_CLASS}])"
    return f"{left}{esc}{right}"


def _extract_unit_and_range_hints(text: str, match_start: int, match_end: int) -> tuple[str | None, str | None]:
    """Extract SI-validated unit_hint and range_hint from the local clause around the matched symbol."""
    # Bound window to the local clause after the symbol occurrence so adjacent variables don't bleed units
    tail = text[match_start:min(len(text), match_end + 110)]
    clause_break = re.search(
        r"(?:,\s*(?:and\s+)?(?:where\s+)?[A-Za-z_\\α-ωΑ-Ω][A-Za-z0-9_]*\s+(?:is|denotes|represents|=)|"
        r"\s+(?:and|with)\s+[A-Za-z][A-Za-z\- ]{1,24}\s+[A-Za-z_\\α-ωΑ-Ω][A-Za-z0-9_]*\b|"
        r";\s*|\.\s+)",
        tail[max(1, match_end - match_start):],
    )
    local_clause = tail[:(max(1, match_end - match_start) + clause_break.start())] if clause_break else tail

    unit_hint = None
    for m_u in UNIT_HINT_RE.finditer(local_clause):
        cand = m_u.group(1).strip(" ,.;:")
        if cand.startswith("(") and cand.endswith(")") and cand.count("(") == cand.count(")"):
            cand = cand[1:-1].strip()
        if not cand or cand.lower() in _UNIT_FALSE_WORDS or len(cand) > 28:
            continue
        # Validate candidate via our composite SI parser (also allow mV/dec)
        parsed_u = parse_unit_expression(cand)
        if parsed_u.get("ok"):
            if len(cand) == 1 and cand.islower() and "in " not in m_u.group(0).lower():
                continue
            unit_hint = cand
            break
        if re.match(r"^[mMkK]?V/dec$", cand):
            unit_hint = cand
            break

    range_hint = None
    m_r = RANGE_HINT_RE.search(local_clause)
    if m_r:
        range_hint = m_r.group(1).strip()
    return unit_hint, range_hint


def find_symbol_definition(sym: str, paras: list[dict[str, str]]) -> dict[str, Any]:
    """Case-sensitive symbol provenance lookup with Greek Unicode<->LaTeX, subscript isolation (E_a vs E), and article ('a') guard."""
    variants = build_symbol_variants(sym)
    alt_pat = "|".join(_symbol_token_pattern(v) for v in variants)
    # Keep symbol token strictly case-sensitive even when prose verbs are matched case-insensitively
    sym_group = f"(?-i:{alt_pat})"

    direct_def_pat = re.compile(
        rf"{sym_group}\s*(?:=|is\s+the|is\s+a|denotes|represents|stands\s+for|defined\s+as|表示|代表|定义为|为)\s*([^\n.;]{{3,115}})"
        rf"|(?:[A-Za-z][A-Za-z\- ]{{2,35}})\s+\({sym_group}\)\s*(?:is\s+defined|in\s+[A-Za-z\u00b5\u03bc%/^0-9\-]+|,|;|\.)"
        rf"|(?:intercept|slope|constant|parameter|coefficient|variable|exponent)\s+{sym_group}\b",
        re.I,
    )
    where_def_pat = re.compile(
        rf"(?:where|here|let|denote[sd]?|represents?|defined\s+(?:as|in\s+terms\s+of)|stands\s+for|with|其中|表示|定义为|记为)\s+[^\n.;]{{0,75}}{sym_group}"
        + (r"(?=\s*(?:=|and\s+[A-Za-z_\\]|is\s+the|denotes|represents|[,;)]))"),
        re.I,
    ) if sym in _AMBIGUOUS_SINGLE_LETTER_WORDS else re.compile(
        rf"(?:where|here|let|denote[sd]?|represents?|defined\s+(?:as|in\s+terms\s+of)|stands\s+for|with|其中|表示|定义为|记为)\s+[^\n.;]{{0,75}}{sym_group}",
        re.I,
    )
    sym_only_pat = re.compile(sym_group)

    def _is_article_false_positive(txt: str, start: int, end: int) -> bool:
        if sym != "a":
            return False
        after = txt[end:end + 25]
        before = txt[max(0, start - 20):start]
        # Reject English indefinite article 'a <noun>' unless directly participating in math syntax
        if re.match(r"^\s*(?:=|is\s+the\b|denotes\b|represents\b|stands\s+for\b|and\s+b\b|[,;)])", after, re.I):
            return False
        if re.search(r"(?:intercept|constant|parameter|coefficient|variable|slope|\()\s*$", before, re.I):
            return False
        return True

    # Pass 1: direct subject definition ("a is the ...", "R is the ...", "E_a denotes ...")
    for p in paras:
        clean_txt = " ".join(p["text"].split())
        for dm in direct_def_pat.finditer(clean_txt):
            sm = sym_only_pat.search(clean_txt, dm.start(), dm.end())
            if sm and not _is_article_false_positive(clean_txt, sm.start(), sm.end()):
                unit_hint, range_hint = _extract_unit_and_range_hints(clean_txt, sm.start(), sm.end())
                start = max(0, sm.start() - 55)
                snippet = clean_txt[start:start + 220].strip()
                return {
                    "symbol": sym,
                    "matched_token": sm.group(0),
                    "status": "source_defined",
                    "paragraph_id": p["paragraph_id"],
                    "unit_hint": unit_hint,
                    "range_hint": range_hint,
                    "definition_quote": snippet,
                }

    # Pass 2: clause-level explicit introduction ("where ... sym", guarding English article 'a')
    for p in paras:
        clean_txt = " ".join(p["text"].split())
        for wm in where_def_pat.finditer(clean_txt):
            sm = sym_only_pat.search(clean_txt, wm.start(), wm.end())
            if sm and not _is_article_false_positive(clean_txt, sm.start(), sm.end()):
                unit_hint, range_hint = _extract_unit_and_range_hints(clean_txt, sm.start(), sm.end())
                start = max(0, sm.start() - 55)
                snippet = clean_txt[start:start + 220].strip()
                return {
                    "symbol": sym,
                    "matched_token": sm.group(0),
                    "status": "source_defined",
                    "paragraph_id": p["paragraph_id"],
                    "unit_hint": unit_hint,
                    "range_hint": range_hint,
                    "definition_quote": snippet,
                }

    # Pass 3: bare symbol mention (strictly skipped for English article/pronoun tokens like 'a' and 'I')
    if sym not in _AMBIGUOUS_SINGLE_LETTER_WORDS:
        for p in paras:
            clean_txt = " ".join(p["text"].split())
            sm = sym_only_pat.search(clean_txt)
            if sm:
                unit_hint, range_hint = _extract_unit_and_range_hints(clean_txt, sm.start(), sm.end())
                start = max(0, sm.start() - 55)
                snippet = clean_txt[start:start + 220].strip()
                return {
                    "symbol": sym,
                    "matched_token": sm.group(0),
                    "status": "context_inferred",
                    "paragraph_id": p["paragraph_id"],
                    "unit_hint": unit_hint,
                    "range_hint": range_hint,
                    "definition_quote": snippet,
                }

    if sym in MATHEMATICAL_CONSTANTS:
        return {
            "symbol": sym,
            "matched_token": sym,
            "status": "mathematical_constant",
            "paragraph_id": None,
            "unit_hint": "1",
            "range_hint": "constant",
            "definition_quote": f"Universal mathematical constant ({sym}); not a paper-specific free parameter.",
        }

    return {
        "symbol": sym,
        "matched_token": None,
        "status": "undefined_in_supplied_text",
        "paragraph_id": None,
        "unit_hint": None,
        "range_hint": None,
        "definition_quote": "",
    }


def render_formula_notes_md(bundle: dict[str, Any], gaps: list[dict[str, Any]]) -> str:
    """Generate portable, anchor-indexed formula_notes.md guaranteed to pass math_markdown_validator."""
    lines: list[str] = [
        f"# Formula Verification Notes (`{bundle['case_id']}`)",
        "",
        f"- **Formula Coverage Status**: `{bundle['formula_coverage_status']}`",
        f"- **XML Sources Used**: {', '.join(f'`{s}`' for s in bundle['xml_sources_used']) if bundle['xml_sources_used'] else 'none'}",
        f"- **Catalog Boundary**: {bundle['catalog_note']}",
        "",
    ]

    formulas: list[dict[str, Any]] = bundle.get("formulas") or []
    if len(formulas) >= 3:
        lines.append("## Equation Index")
        lines.append("")
        for idx, f in enumerate(formulas, start=1):
            eid = f"E{idx:03d}"
            label_part = f" · {f['label']}" if f.get("label") else ""
            status_part = f" [{f.get('verification_status', 'parsed')}]"
            lines.append(f"- [{eid}{label_part}](#{eid}) — `{f.get('formula_id')}` (`{f.get('source_file')}`, `{f.get('representation_source')}`){status_part}")
        lines.append("")

    lines.append("## Extracted Formulas")
    lines.append("")
    if not formulas:
        lines.append("No XML MathML, `<tex-math>`, or display LaTeX formula representations were found in the supplied input snapshot.")
        lines.append("Per the missing-source contract, formulas were **not** back-inferred and unknown parameters were **not** set to zero.")
        lines.append("")
    else:
        for idx, f in enumerate(formulas, start=1):
            eid = f"E{idx:03d}"
            label_part = f" · {f['label']}" if f.get("label") else ""
            lines.append(f'<a id="{eid}"></a>')
            lines.append(
                f"**Source:** `{f.get('source_file', 'input')}` · `{f.get('formula_id')}`{label_part} · "
                f"tag=`{f.get('xml_tag')}` · representation=`{f.get('representation_source')}` · "
                f"status=`{f.get('verification_status', 'parsed')}`"
            )
            lines.append("")
            noise = f.get("ocr_noise") or {}
            if noise.get("severity") == "halt_for_verification":
                lines.append("> **OCR/Transcription Noise Alert (Halted for Visual Verification)**:")
                for iss in noise.get("issues") or []:
                    lines.append(f"> - `{iss.get('code')}`: {iss.get('message')}")
                lines.append("")
                lines.append("```text")
                lines.append(f.get("latex", "").replace("\t", "<TAB>"))
                lines.append("```")
                lines.append("")
            else:
                lines.append("$$")
                lines.append(f.get("latex", "").strip())
                lines.append("$$")
                lines.append("")
            syms = f.get("symbols") or []
            if syms:
                formatted_syms = ", ".join(f"`{s}`" for s in syms)
                lines.append(f"Extracted symbols: {formatted_syms}")
                lines.append("")

    # Variable Table
    lines.append("## Variable Provenance Table")
    lines.append("")
    vtable = bundle.get("variable_table") or []
    if not vtable:
        lines.append("No variables extracted from formula representations.")
        lines.append("")
    else:
        lines.append("| Symbol | Provenance Tier | Paragraph Anchor | Unit Hint | Range Hint |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in vtable:
            lines.append(
                f"| `{row['symbol']}` | `{row['status']}` | `{row.get('paragraph_id') or 'none'}` | "
                f"`{row.get('unit_hint') or 'unspecified'}` | `{row.get('range_hint') or 'unspecified'}` |"
            )
        lines.append("")

    # Composite SI Dimensional Checks
    dchecks = bundle.get("dimensional_checks") or []
    if dchecks:
        lines.append("## Composite SI Dimensional Checks")
        lines.append("")
        lines.append("| Check ID | LHS Unit | RHS Unit | Consistent | Scale Ratio | OOM Gap | Note |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for dc in dchecks:
            lines.append(
                f"| `{dc.get('check_id')}` | `{dc.get('lhs_unit')}` | `{dc.get('rhs_unit')}` | "
                f"`{dc.get('consistent')}` | `{dc.get('scale_ratio')}` | `{dc.get('order_of_magnitude_gap')}` | "
                f"{dc.get('warning') or 'exact SI match'} |"
            )
        lines.append("")

    # Cross-Equation Dependency Graph (DAG)
    dag = bundle.get("equation_dependency_dag") or {}
    dag_edges = dag.get("edges") or []
    if dag.get("node_count", 0) > 0:
        lines.append("## Cross-Equation Dependency Graph (DAG)")
        lines.append("")
        topo = " -> ".join(f"`{x}`" for x in (dag.get("topological_order") or []))
        lines.append(
            f"- **Nodes**: `{dag.get('node_count')}` | **Directed Edges**: `{dag.get('edge_count')}` | "
            f"**Acyclic**: `{not dag.get('has_cycle')}` | **Topological Order**: {topo or 'none'}"
        )
        lines.append("")
        if dag_edges:
            lines.append("| From Equation | To Equation | Relation Type | Shared Symbols | Source Anchor |")
            lines.append("| --- | --- | --- | --- | --- |")
            for e in dag_edges:
                ss = ", ".join(e.get("shared_symbols") or []) or "-"
                lines.append(
                    f"| `{e['from_formula_id']}` | `{e['to_formula_id']}` | `{e['relation_type']}` | `{ss}` | `{e['source_anchor']}` |"
                )
            lines.append("")

    # Step-by-Step Derivation, Assumptions & Boundary Conditions Audit Table
    dchains = bundle.get("derivation_chains") or []
    drv = bundle.get("derivation_checks") or []
    if dchains or drv:
        lines.append("## Derivation Steps, Assumptions & Boundary Conditions Audit")
        lines.append("")
        lines.append("| Step ID | From -> To | Operation | Assumptions / Approximations / Boundaries | Numeric Check (Before / After / RelErr) | Domain & Units | Verdict |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        rendered_step_ids: set[str] = set()
        for ch in dchains:
            for st in ch.get("steps") or []:
                rendered_step_ids.add(str(st.get("step_id") or ""))
                prem_parts = (st.get("assumptions") or []) + (st.get("approximations") or []) + (st.get("boundary_conditions") or [])
                prem_txt = "; ".join(str(x) for x in prem_parts) if prem_parts else "none_declared"
                edge_txt = f"{st.get('from_formula_id') or '-'} -> {st.get('to_formula_id') or '-'}"
                num_txt = f"{st.get('numeric_before')} / {st.get('numeric_after')} / {st.get('rel_error')}"
                dom_ok = len(st.get("domain_violations") or []) == 0
                dim_chk = st.get("dimensional_check") or {}
                dim_txt = f"dim={dim_chk.get('consistent')}" if dim_chk else "dim=n/a"
                dom_txt = f"domain={'ok' if dom_ok else 'VIOLATED'}, {dim_txt}"
                lines.append(
                    f"| `{st.get('step_id')}` | `{edge_txt}` | `{st.get('operation')}` | "
                    f"`{prem_txt[:110]}` | `{num_txt}` | `{dom_txt}` | `{st.get('step_verdict')}` |"
                )
        for item in drv:
            if str(item.get("step_id") or "") in rendered_step_ids:
                continue
            prem_parts = (item.get("assumptions") or []) + (item.get("approximations") or []) + (item.get("boundary_conditions") or [])
            prem_txt = "; ".join(str(x) for x in prem_parts) if prem_parts else "none_declared"
            edge_txt = f"{item.get('formula_id') or '-'} -> {item.get('to_formula_id') or '-'}"
            num_txt = f"{item.get('numeric_v1')} / {item.get('numeric_v2')} / {item.get('rel_error')}"
            dom_ok = len(item.get("domain_violations") or []) == 0
            dim_chk = item.get("dimensional_check") or {}
            dim_txt = f"dim={dim_chk.get('consistent')}" if dim_chk else "dim=n/a"
            dom_txt = f"domain={'ok' if dom_ok else 'VIOLATED'}, {dim_txt}"
            lines.append(
                f"| `{item.get('step_id')}` | `{edge_txt}` | `{item.get('operation') or item.get('method')}` | "
                f"`{prem_txt[:110]}` | `{num_txt}` | `{dom_txt}` | `{item.get('step_verdict') or item.get('equivalent')}` |"
            )
        lines.append("")

    # Extracted Prose Assumptions & Boundary Conditions
    prose_prems = bundle.get("prose_assumptions_and_boundaries") or []
    if prose_prems:
        lines.append("## Extracted Prose Assumptions, Approximations & Boundary Conditions")
        lines.append("")
        lines.append("| ID | Category | Linked Equations | Paragraph Anchor | Extracted Premise |")
        lines.append("| --- | --- | --- | --- | --- |")
        for pr in prose_prems[:25]:
            leq = ", ".join(pr.get("linked_equations") or []) or "general"
            safe_txt = str(pr.get("text") or "").replace("|", "/").replace("$", "")
            lines.append(
                f"| `{pr['condition_id']}` | `{pr['category']}` | `{leq}` | `{pr['paragraph_id']}` | `{safe_txt[:110]}` |"
            )
        lines.append("")

    nchk = bundle.get("numeric_checks") or []
    if nchk:
        lines.append("## Numeric Substitution Checks")
        lines.append("")
        for item in nchk:
            lines.append(
                f"- `{item.get('check_id')}`: status=`{item.get('status')}`, "
                f"computed=`{item.get('computed')}`, expected=`{item.get('expected')}`, "
                f"within_tolerance=`{item.get('within_tolerance')}`"
            )
        lines.append("")

    if gaps:
        lines.append("## Recorded Gaps and Limits")
        lines.append("")
        for g in gaps:
            lines.append(f"- `{g.get('code')}` ({g.get('severity', 'warning')}): {g.get('message')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_file = work_dir / ".budget_state.json"
    budget_guard.init_budget(budget_file)
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="7c37626c-formula-verifier")

    case_path = input_dir / "case.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace")) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "case-unknown")

    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="challenge-7c37626c")

    budget_guard.tick_phase(budget_file, "discover_formula_sources")

    # 1. Discover XML formula catalogs: prefer source-formulas.xml; fallback to publisher-source.xml if unreadable or empty
    xml_candidates = sorted(p for p in input_dir.rglob("*.xml") if p.is_file())
    source_formulas_xml = [p for p in xml_candidates if "source-formulas" in p.name.lower()]
    publisher_xml = [p for p in xml_candidates if "publisher-source" in p.name.lower()]
    other_xml = [p for p in xml_candidates if p not in source_formulas_xml and p not in publisher_xml]

    parsed_formulas: list[dict[str, Any]] = []
    xml_sources_used: list[str] = []
    gaps: list[dict[str, Any]] = []
    xml_groups = (
        [g for g in (source_formulas_xml, publisher_xml, other_xml) if g]
        if (source_formulas_xml or publisher_xml)
        else ([xml_candidates] if xml_candidates else [])
    )
    for sources in xml_groups:
        for xp in sources:
            relpath = xp.relative_to(input_dir).as_posix()
            try:
                formulas = [f for f in parse_xml_formulas(str(xp), source_file=relpath) if f.get("latex")]
            except Exception as exc:
                gaps.append({
                    "code": "FORMULA_SOURCE_UNREADABLE",
                    "severity": "warning",
                    "source": relpath,
                    "message": f"Source formula XML could not be read ({type(exc).__name__}: {exc}).",
                })
                continue
            if formulas:
                xml_sources_used.append(relpath)
                parsed_formulas.extend(formulas)
        if parsed_formulas:
            break

    # 2. Collect grounded text paragraphs (including JATS <p> from publisher-source.xml!)
    paras = collect_text_paragraphs(input_dir, case_data)

    # 3. Extract formulas from case.json or Markdown/TeX display blocks if XML yielded none
    if not parsed_formulas:
        for idx, eq in enumerate(case_data.get("formulas") or [], start=1):
            if isinstance(eq, dict):
                fid = str(eq.get("formula_id") or eq.get("id") or f"eq_{idx}")
                latex = str(eq.get("latex") or eq.get("expression") or "").strip()
                label = eq.get("label")
            else:
                fid = f"eq_{idx}"
                latex = str(eq).strip()
                label = None
            if latex:
                noise = inspect_formula_noise(latex)
                parsed_formulas.append({
                    "formula_id": fid,
                    "source_file": "case.json",
                    "xml_tag": "json_formula",
                    "label": label,
                    "representation_source": "inline_latex",
                    "has_mathml": False,
                    "has_tex_math": False,
                    "latex": latex,
                    "mathml_latex": None,
                    "tex_math": None,
                    "representation_symbol_divergence": False,
                    "symbols": extract_symbols_from_latex(latex),
                    "ocr_noise": noise,
                    "verification_status": "halted_for_visual_check" if noise["severity"] == "halt_for_verification" else "parsed",
                    "raw_text": latex[:400],
                })

    if not parsed_formulas:
        for p in paras:
            for m in LATEX_DISPLAY_RE.finditer(p["text"]):
                body = next((g for g in m.groups() if g), "").strip()
                if body:
                    noise = inspect_formula_noise(body)
                    parsed_formulas.append({
                        "formula_id": f"{p['paragraph_id']}_eq_{len(parsed_formulas)+1}",
                        "source_file": p["paragraph_id"].split(":", 1)[0],
                        "xml_tag": "inline_latex",
                        "label": None,
                        "representation_source": "inline_latex",
                        "has_mathml": False,
                        "has_tex_math": False,
                        "latex": body,
                        "mathml_latex": None,
                        "tex_math": None,
                        "representation_symbol_divergence": False,
                        "symbols": extract_symbols_from_latex(body),
                        "ocr_noise": noise,
                        "verification_status": "halted_for_visual_check" if noise["severity"] == "halt_for_verification" else "parsed",
                        "raw_text": body[:400],
                    })

    # Flag OCR noise and MathML/TeX divergences in gaps
    halted_formula_ids: set[str] = set()
    for f in parsed_formulas:
        noise = f.get("ocr_noise") or {}
        if noise.get("severity") == "halt_for_verification":
            halted_formula_ids.add(str(f["formula_id"]))
            issue_codes = ", ".join(x["code"] for x in noise.get("issues", []))
            gaps.append({
                "code": "OCR_NOISE_DETECTED",
                "severity": "warning",
                "source": f"{f.get('source_file')}:{f.get('formula_id')}",
                "message": f"Formula {f.get('formula_id')} halted for visual verification due to OCR/transcription noise ({issue_codes}).",
                "fallback": "halt_derivation_and_verify_source",
            })
        if f.get("representation_symbol_divergence"):
            gaps.append({
                "code": "MATHML_TEX_SYMBOL_DIVERGENCE",
                "severity": "warning",
                "source": f"{f.get('source_file')}:{f.get('formula_id')}",
                "message": f"MathML and <tex-math> representations for {f.get('formula_id')} yielded differing symbol sets; inspect source XML.",
            })

    # 4. Variable provenance table (exclude pure OCR-corrupted garbage tokens that only appear in halted formulas unless all formulas halted)
    clean_formulas = [f for f in parsed_formulas if str(f.get("formula_id")) not in halted_formula_ids]
    # Keep halted-formula symbols only if they have explicit prose definitions or if no clean formulas exist
    clean_symbols = {s for f in clean_formulas for s in (f.get("symbols") or [])}
    halted_only_symbols = {s for f in parsed_formulas if str(f.get("formula_id")) in halted_formula_ids for s in (f.get("symbols") or [])} - clean_symbols
    variable_table: list[dict[str, Any]] = []
    for sym in sorted(clean_symbols):
        variable_table.append(find_symbol_definition(sym, paras))
    for sym in sorted(halted_only_symbols):
        entry = find_symbol_definition(sym, paras)
        if not clean_formulas or entry["status"] in ("source_defined", "mathematical_constant"):
            variable_table.append(entry)
    variable_table.sort(key=lambda r: r["symbol"])
    undefined_vars = [v["symbol"] for v in variable_table if v["status"] == "undefined_in_supplied_text"]

    # 5. Step-by-step derivation checks, multi-step chains, prose assumptions & equation dependency DAG
    budget_guard.tick_phase(budget_file, "verify_derivations_and_dimensions")
    prose_premises = extract_derivation_assumptions_and_boundaries(paras, parsed_formulas)

    derivation_checks: list[dict[str, Any]] = []
    for idx, drv in enumerate(case_data.get("derivation_checks") or [], start=1):
        if isinstance(drv, dict) and drv.get("lhs") and drv.get("rhs"):
            step_id = str(drv.get("step_id", f"S{idx}"))
            ref_fid = str(drv.get("formula_id") or "")
            to_fid = str(drv.get("to_formula_id") or "")
            assumptions = [str(a) for a in (drv.get("assumptions") or [])]
            approximations = [str(a) for a in (drv.get("approximations") or [])]
            boundaries = [str(b) for b in (drv.get("boundary_conditions") or [])]
            domain_constraints = [str(d) for d in (drv.get("domain_constraints") or [])]
            if ref_fid and ref_fid in halted_formula_ids:
                derivation_checks.append({
                    "step_id": step_id,
                    "formula_id": ref_fid,
                    "to_formula_id": to_fid or None,
                    "operation": drv.get("operation") or "algebraic_step",
                    "method": "halted_ocr_noise",
                    "equivalent": None,
                    "assumptions": assumptions,
                    "approximations": approximations,
                    "boundary_conditions": boundaries,
                    "domain_violations": [],
                    "step_verdict": "halted_ocr_noise",
                    "reason": f"Skipped derivation check because source formula {ref_fid} failed OCR noise gate.",
                })
                continue
            chk = check_equivalence(str(drv["lhs"]), str(drv["rhs"]), test_env=drv.get("test_env"))
            chk["step_id"] = step_id
            if ref_fid:
                chk["formula_id"] = ref_fid
            if to_fid:
                chk["to_formula_id"] = to_fid
            chk["operation"] = drv.get("operation") or "algebraic_step"
            chk["assumptions"] = assumptions
            chk["approximations"] = approximations
            chk["boundary_conditions"] = boundaries
            dom_evals = [
                evaluate_domain_predicate(p, drv.get("test_env"))
                for p in (domain_constraints + assumptions + boundaries)
            ]
            dom_viols = [d for d in dom_evals if d.get("status") == "violated"]
            chk["domain_checks"] = [d for d in dom_evals if d["status"] in ("satisfied", "violated")]
            chk["domain_violations"] = dom_viols
            dim_c = None
            if drv.get("unit_lhs") and drv.get("unit_rhs"):
                dim_c = check_dimensional_balance(str(drv["unit_lhs"]), str(drv["unit_rhs"]))
                chk["dimensional_check"] = dim_c
            if chk.get("method") == "halted_ocr_noise":
                chk["step_verdict"] = "halted_ocr_noise"
                gaps.append({
                    "code": "DERIVATION_HALTED_BY_OCR_NOISE",
                    "severity": "warning",
                    "source": step_id,
                    "message": f"Derivation step {step_id} halted due to noisy/corrupted formula tokens.",
                })
            elif dom_viols:
                chk["step_verdict"] = "domain_violation"
                gaps.append({
                    "code": "DERIVATION_DOMAIN_VIOLATION",
                    "severity": "warning",
                    "source": step_id,
                    "message": f"Derivation step {step_id} violated declared domain constraint(s): {[d['predicate'] for d in dom_viols]}.",
                })
            elif dim_c and dim_c.get("consistent") is False:
                chk["step_verdict"] = "dimensional_mismatch"
            elif chk.get("equivalent") is True:
                chk["step_verdict"] = "verified"
            elif approximations and isinstance(chk.get("rel_error"), (int, float)) and chk["rel_error"] <= float(drv.get("approx_rel_tol", 0.05)):
                chk["step_verdict"] = "verified_under_approximation"
            elif chk.get("equivalent") is False:
                chk["step_verdict"] = "failed_equivalence"
            else:
                chk["step_verdict"] = "conditional_on_stated_premise" if (assumptions or approximations or boundaries) else "not_verifiable_from_supplied_materials"
            derivation_checks.append(chk)

    derivation_chains: list[dict[str, Any]] = []
    for ch_spec in (case_data.get("derivation_chains") or []):
        if isinstance(ch_spec, dict) and ch_spec.get("steps"):
            audited_chain = audit_derivation_chain(ch_spec, halted_formula_ids=halted_formula_ids)
            if audited_chain["chain_status"] == "failed":
                gaps.append({
                    "code": "DERIVATION_CHAIN_FAILURE",
                    "severity": "warning",
                    "source": audited_chain["chain_id"],
                    "message": f"Derivation chain {audited_chain['chain_id']} has one or more failed equivalence, domain, or dimensional steps.",
                })
            derivation_chains.append(audited_chain)

    # Synchronize derivation_checks with audited derivation_chains so result.json never has empty derivation_checks when chains exist
    existing_step_ids = {str(dc.get("step_id") or "") for dc in derivation_checks}
    for ch in derivation_chains:
        cid = ch["chain_id"]
        for st in ch.get("steps") or []:
            sid = str(st.get("step_id") or "")
            if sid and sid in existing_step_ids:
                continue
            existing_step_ids.add(sid)
            derivation_checks.append({
                "step_id": sid,
                "chain_id": cid,
                "formula_id": st.get("from_formula_id"),
                "to_formula_id": st.get("to_formula_id"),
                "operation": st.get("operation"),
                "expr1": st.get("expr_before"),
                "expr2": st.get("expr_after"),
                "method": st.get("method"),
                "equivalent": st.get("equivalent"),
                "numeric_v1": st.get("numeric_before"),
                "numeric_v2": st.get("numeric_after"),
                "rel_error": st.get("rel_error"),
                "assumptions": st.get("assumptions") or [],
                "approximations": st.get("approximations") or [],
                "boundary_conditions": st.get("boundary_conditions") or [],
                "domain_checks": st.get("domain_checks") or [],
                "domain_violations": st.get("domain_violations") or [],
                "dimensional_check": st.get("dimensional_check"),
                "step_verdict": st.get("step_verdict"),
                "synced_from_chain": True,
            })

    equation_dependency_dag = build_equation_dependency_dag(
        parsed_formulas,
        paras,
        derivation_chains=derivation_chains,
        derivation_checks=derivation_checks,
    )

    # 5b. Auto-Candidate Generation Layer (when hidden platform cases omit case.json derivation/numeric/unit specs)
    #     Parses task.md directives + prose numeric assignments so "推得顺/算得回" works autonomously without manual JSON!
    task_md_path = input_dir / "task.md"
    task_md_text = task_md_path.read_text(encoding="utf-8", errors="replace") if task_md_path.is_file() else ""
    task_eq_targets = sorted(set(re.findall(r"(?:Eqs?\.?|Equations?|公式|式)\s*\(?\s*([0-9]+[A-Za-z]?)\s*\)?", task_md_text, re.I)))

    # Auto-extract grounded numeric parameter values from paper prose (e.g., "z = 4", "T = 298.15 K", "R = 8.314")
    prose_numeric_env: dict[str, float] = {}
    for vrow in variable_table:
        sym = vrow["symbol"]
        if vrow["status"] == "undefined_in_supplied_text":
            continue
        variants = build_symbol_variants(sym)
        alt_v = "|".join(re.escape(v) for v in variants)
        num_pat = re.compile(rf"(?<![A-Za-z0-9_])(?:{alt_v})\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\b")
        for p in paras:
            m = num_pat.search(p["text"])
            if m:
                try:
                    prose_numeric_env[sym] = float(m.group(1))
                    break
                except ValueError:
                    pass

    if not derivation_chains and not derivation_checks and equation_dependency_dag.get("edges"):
        # Synthesize grounded candidate derivation steps directly from the cross-equation dependency DAG + prose premises
        for e_idx, edge in enumerate(equation_dependency_dag["edges"][:12], start=1):
            src_f = edge["from_formula_id"]
            dst_f = edge["to_formula_id"]
            linked_prems = [
                pr["text"] for pr in prose_premises
                if src_f in (pr.get("linked_equations") or []) or dst_f in (pr.get("linked_equations") or [])
            ]
            derivation_checks.append({
                "step_id": f"AUTO_D{e_idx}",
                "formula_id": src_f,
                "to_formula_id": dst_f,
                "operation": f"auto_discovered_{edge['relation_type']}",
                "method": "auto_dag_premise_audit",
                "equivalent": None,
                "assumptions": linked_prems[:6],
                "approximations": [],
                "boundary_conditions": [],
                "domain_violations": [],
                "source_anchor": edge["source_anchor"],
                "step_verdict": "conditional_on_stated_premise" if linked_prems else "not_verifiable_from_supplied_materials",
            })

    # 6. General Composite SI Dimensional Checks (with auto-synthesis from SI-validated unit_hints when case.json omits them)
    dimensional_checks: list[dict[str, Any]] = []
    raw_dim_specs = list(case_data.get("dimensional_checks") or [])
    if not raw_dim_specs and parsed_formulas:
        unit_by_sym = {v["symbol"]: v["unit_hint"] for v in variable_table if v.get("unit_hint")}
        for idx, f in enumerate(parsed_formulas, start=1):
            if str(f.get("formula_id")) in halted_formula_ids or "=" not in str(f.get("latex") or ""):
                continue
            lhs_part, rhs_part = str(f["latex"]).split("=", 1)
            lhs_syms = extract_symbols_from_latex(lhs_part)
            rhs_syms = extract_symbols_from_latex(rhs_part)
            if len(lhs_syms) == 1 and lhs_syms[0] in unit_by_sym:
                rhs_units = [unit_by_sym[s] for s in rhs_syms if s in unit_by_sym]
                if len(rhs_units) == 1:
                    raw_dim_specs.append({
                        "check_id": f"AUTO_DIM_{idx}",
                        "lhs_unit": unit_by_sym[lhs_syms[0]],
                        "rhs_unit": rhs_units[0],
                    })
    for idx, dchk in enumerate(raw_dim_specs, start=1):
        if isinstance(dchk, dict) and dchk.get("lhs_unit") and dchk.get("rhs_unit"):
            dc = check_dimensional_balance(str(dchk["lhs_unit"]), str(dchk["rhs_unit"]))
            dc["check_id"] = str(dchk.get("check_id", f"D{idx}"))
            if dc.get("consistent") is False:
                gaps.append({
                    "code": "DIMENSIONAL_MISMATCH",
                    "severity": "warning",
                    "source": dc["check_id"],
                    "message": f"Dimensional mismatch in {dc['check_id']}: `{dc['lhs_unit']}` vs `{dc['rhs_unit']}`; axis delta `{dc.get('dimension_delta')}`.",
                })
            elif dc.get("consistent") is True and dc.get("scale_exact_match") is False:
                gaps.append({
                    "code": "UNIT_ORDER_OF_MAGNITUDE_OFFSET",
                    "severity": "warning",
                    "source": dc["check_id"],
                    "message": f"Scale offset in {dc['check_id']}: `{dc['lhs_unit']}` vs `{dc['rhs_unit']}`; scale ratio `{dc.get('scale_ratio')}`, log10 gap `{dc.get('order_of_magnitude_gap')}`.",
                })
            dimensional_checks.append(dc)

    # 7. Numeric substitution checks (strict no-zero-fill + OCR noise guard + auto-template generation when case.json omits them)
    numeric_checks: list[dict[str, Any]] = []
    raw_num_specs = list(case_data.get("numeric_checks") or [])
    if not raw_num_specs and parsed_formulas:
        for idx, f in enumerate(parsed_formulas[:8], start=1):
            fid = str(f.get("formula_id") or f"eq_{idx}")
            if fid in halted_formula_ids:
                continue
            lat = str(f.get("latex") or "")
            if "=" not in lat:
                continue
            rhs_expr = lat.split("=", 1)[1].strip()
            rhs_syms = [s for s in extract_symbols_from_latex(rhs_expr) if s not in MATHEMATICAL_CONSTANTS]
            if not rhs_syms:
                continue
            raw_num_specs.append({
                "check_id": f"AUTO_NUM_{fid}",
                "expression": rhs_expr,
                "required_vars": rhs_syms,
                "env": {s: prose_numeric_env[s] for s in rhs_syms if s in prose_numeric_env},
                "expected": None,
                "auto_generated": True,
            })
    for idx, nchk in enumerate(raw_num_specs, start=1):
        if isinstance(nchk, dict) and nchk.get("expression"):
            cid = str(nchk.get("check_id", f"N{idx}"))
            expr = str(nchk["expression"])
            noise = inspect_formula_noise(expr)
            if noise["severity"] == "halt_for_verification":
                numeric_checks.append({
                    "check_id": cid,
                    "status": "halted_for_visual_check",
                    "computed": None,
                    "expected": nchk.get("expected"),
                    "within_tolerance": None,
                    "reason": "Halted numeric evaluation due to OCR/transcription noise in expression.",
                    "noise_issues": noise["issues"],
                })
                gaps.append({
                    "code": "NUMERIC_CHECK_HALTED_BY_OCR_NOISE",
                    "severity": "warning",
                    "source": cid,
                    "message": f"Numeric check {cid} halted because expression triggered OCR noise detector.",
                })
                continue

            env = nchk.get("env") or {}
            req_vars = nchk.get("required_vars") or []
            missing_vars = [v for v in req_vars if v not in env or env[v] is None]
            if missing_vars:
                numeric_checks.append({
                    "check_id": cid,
                    "status": "insufficient_evidence",
                    "missing_variables": missing_vars,
                    "computed": None,
                    "expected": nchk.get("expected"),
                    "within_tolerance": None,
                    "reason": "Missing parameter value(s); unknown inputs are NOT defaulted to 0.",
                })
            else:
                try:
                    val = safe_eval_ast(expr, {k: float(v) for k, v in env.items()})
                    expected = nchk.get("expected")
                    within = abs(val - float(expected)) <= float(nchk.get("tol", 1e-6)) if expected is not None else None
                    numeric_checks.append({
                        "check_id": cid,
                        "status": "verified",
                        "computed": round(val, 8),
                        "expected": expected,
                        "within_tolerance": within,
                    })
                except Exception as exc:
                    numeric_checks.append({
                        "check_id": cid,
                        "status": "evaluation_error",
                        "error": str(exc),
                        "computed": None,
                        "expected": nchk.get("expected"),
                        "within_tolerance": None,
                    })

    # 8. Honest C02 Missing-Formula Coverage Status
    if not parsed_formulas:
        formula_coverage_status = "insufficient_evidence"
        gaps.append({
            "code": "MISSING_FORMULA_REPRESENTATION",
            "severity": "warning",
            "source": "formula_math_engine",
            "message": "No XML MathML, <tex-math>, or LaTeX formula nodes found in input snapshot (C02 missing branch). Only explicit metrics/conditions can be checked; back-inference and zero-filling are blocked.",
            "fallback": "report_partial_or_insufficient_evidence",
        })
    elif (
        any(n.get("status") in {"insufficient_evidence", "halted_for_visual_check"} for n in numeric_checks)
        or bool(halted_formula_ids)
        or any(g.get("code") == "FORMULA_SOURCE_UNREADABLE" for g in gaps)
    ):
        formula_coverage_status = "partial"
        if any(n.get("status") == "insufficient_evidence" for n in numeric_checks):
            gaps.append({
                "code": "MISSING_VARIABLE_VALUES_FOR_RECOMPUTATION",
                "severity": "warning",
                "source": "numeric_checks",
                "message": "One or more required parameter values for numeric recomputation are absent; retained as null without zero-filling.",
            })
    else:
        formula_coverage_status = "partial" if gaps else "complete"

    bundle: dict[str, Any] = {
        "case_id": case_id,
        "xml_sources_used": xml_sources_used,
        "catalog_note": "Extracted formula catalog nodes preserve source MathML/TeX structure and IDs without double-counting; catalog nodes alone do not constitute a complete derivation.",
        "formula_coverage_status": formula_coverage_status,
        "formulas": parsed_formulas,
        "variable_table": variable_table,
        "equation_dependency_dag": equation_dependency_dag,
        "derivation_chains": derivation_chains,
        "derivation_checks": derivation_checks,
        "prose_assumptions_and_boundaries": prose_premises,
        "dimensional_checks": dimensional_checks,
        "numeric_checks": numeric_checks,
    }

    # 9. Render and self-validate formula_notes.md
    notes_md = render_formula_notes_md(bundle, gaps)
    notes_path = output_dir / "formula_notes.md"
    result_guard.atomic_write_text(notes_path, notes_md)
    md_validation = validate_formula_markdown(notes_md, strict=False)
    bundle["markdown_validation"] = md_validation
    if not md_validation["ready"]:
        gaps.append({
            "code": "FORMULA_MARKDOWN_VALIDATION_FAILED",
            "severity": "warning",
            "source": "math_markdown_validator",
            "message": f"formula_notes.md had {md_validation['summary']['fail']} math rendering issue(s).",
        })

    bundle_path = output_dir / "formula_verification_report.json"
    result_guard.atomic_write_json(bundle_path, bundle)

    chain_total_steps = sum(ch["step_count"] for ch in derivation_chains)
    chain_verified_steps = sum(ch["verified_step_count"] for ch in derivation_chains)
    append_audit_entry(
        audit_file,
        phase="verify_math",
        action="formula_report_written",
        actor="formula_math_engine.py",
        output={
            "formula_count": len(parsed_formulas),
            "var_count": len(variable_table),
            "dag_edge_count": equation_dependency_dag["edge_count"],
            "derivation_chain_steps": chain_total_steps,
            "prose_premise_count": len(prose_premises),
            "halted_ocr_count": len(halted_formula_ids),
            "coverage_status": formula_coverage_status,
            "markdown_ready": md_validation["ready"],
        },
        record_id=case_id,
    )

    metrics = {
        "formula_count": len(parsed_formulas),
        "variable_count": len(variable_table),
        "source_defined_variable_count": sum(1 for v in variable_table if v["status"] == "source_defined"),
        "context_inferred_variable_count": sum(1 for v in variable_table if v["status"] == "context_inferred"),
        "mathematical_constant_count": sum(1 for v in variable_table if v["status"] == "mathematical_constant"),
        "undefined_variable_count": len(undefined_vars),
        "ocr_halted_formula_count": len(halted_formula_ids),
        "dag_edge_count": equation_dependency_dag["edge_count"],
        "derivation_chain_count": len(derivation_chains),
        "derivation_chain_step_count": chain_total_steps,
        "derivation_chain_verified_step_count": chain_verified_steps,
        "derivation_check_count": len(derivation_checks),
        "prose_premise_count": len(prose_premises),
        "dimensional_check_count": len(dimensional_checks),
        "dimensional_mismatch_count": sum(1 for d in dimensional_checks if d.get("consistent") is False),
        "dimensional_oom_offset_count": sum(1 for d in dimensional_checks if d.get("consistent") is True and d.get("scale_exact_match") is False),
        "numeric_check_count": len(numeric_checks),
        "markdown_validation_fail_count": md_validation["summary"]["fail"],
    }
    metric_provenance = {
        "formula_count": "formula_verification_report.json:formulas",
        "variable_count": "formula_verification_report.json:variable_table",
        "source_defined_variable_count": "formula_verification_report.json:variable_table",
        "context_inferred_variable_count": "formula_verification_report.json:variable_table",
        "mathematical_constant_count": "formula_verification_report.json:variable_table",
        "undefined_variable_count": "formula_verification_report.json:variable_table",
        "ocr_halted_formula_count": "formula_verification_report.json:formulas",
        "dag_edge_count": "formula_verification_report.json:equation_dependency_dag",
        "derivation_chain_count": "formula_verification_report.json:derivation_chains",
        "derivation_chain_step_count": "formula_verification_report.json:derivation_chains",
        "derivation_chain_verified_step_count": "formula_verification_report.json:derivation_chains",
        "derivation_check_count": "formula_verification_report.json:derivation_checks",
        "prose_premise_count": "formula_verification_report.json:prose_assumptions_and_boundaries",
        "dimensional_check_count": "formula_verification_report.json:dimensional_checks",
        "dimensional_mismatch_count": "formula_verification_report.json:dimensional_checks",
        "dimensional_oom_offset_count": "formula_verification_report.json:dimensional_checks",
        "numeric_check_count": "formula_verification_report.json:numeric_checks",
        "markdown_validation_fail_count": "formula_verification_report.json:markdown_validation",
    }

    patch: dict[str, Any] = {
        "artifacts": ["result.json", "formula_verification_report.json", "formula_notes.md"],
        "formula_coverage_status": formula_coverage_status,
        "xml_sources_used": xml_sources_used,
        "formulas": parsed_formulas,
        "variable_table": variable_table,
        "equation_dependency_dag": equation_dependency_dag,
        "derivation_chains": derivation_chains,
        "derivation_checks": derivation_checks,
        "prose_assumptions_and_boundaries": prose_premises,
        "dimensional_checks": dimensional_checks,
        "numeric_checks": numeric_checks,
        "markdown_validation": md_validation,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": [
            f"Extracted {len(parsed_formulas)} unique formula node(s) from {len(xml_sources_used)} XML source(s) without double-counting; "
            f"source_defined_vars={metrics['source_defined_variable_count']}/{len(variable_table)} (undefined={len(undefined_vars)}); "
            f"dag_edges={equation_dependency_dag['edge_count']}; derivation_steps_verified={chain_verified_steps}/{chain_total_steps}; "
            f"prose_premises={len(prose_premises)}; ocr_halted={len(halted_formula_ids)}; formula_coverage_status={formula_coverage_status}."
        ],
        "gaps": gaps,
    }

    # Dynamic output_contract field absorption (preserve all case.json required keys without fabricating missing values)
    raw_contract = case_data.get("output_contract")
    contract_fields: list[str] = []
    if isinstance(raw_contract, list):
        contract_fields = [str(k) for k in raw_contract if k]
    elif isinstance(raw_contract, dict):
        if isinstance(raw_contract.get("required_fields"), list):
            contract_fields = [str(k) for k in raw_contract["required_fields"] if k]
        elif isinstance(raw_contract.get("fields"), list):
            contract_fields = [str(k) for k in raw_contract["fields"] if k]
        else:
            contract_fields = [str(k) for k in raw_contract.keys() if k]
    if contract_fields:
        alias_map = {
            "sources": xml_sources_used or sorted({p["paragraph_id"].split(":", 1)[0] for p in paras}),
            "uncertainty": gaps,
            "assumptions": prose_premises,
            "derivation_steps": derivation_chains or derivation_checks,
            "variables": variable_table,
        }
        unpop: list[str] = []
        for cf in contract_fields:
            if cf in patch or cf in ("case_id", "status", "task_id", "schema_version"):
                continue
            if cf in alias_map:
                patch[cf] = alias_map[cf]
            else:
                patch[cf] = None
                unpop.append(cf)
        patch["output_contract_compliance"] = {
            "declared_fields": contract_fields,
            "unpopulated_fields": unpop,
            "coverage_ratio": round((len(contract_fields) - len(unpop)) / max(1, len(contract_fields)), 4),
        }

    res = result_guard.update_result(result_path, patch, finalize=True)
    grounding = gap_gate.check_grounding(res, [input_dir])
    if not grounding["ok"]:
        res = result_guard.update_result(result_path, {"gaps": gaps + grounding["gaps"]}, finalize=True)

    budget_guard.tick_phase(budget_file, "final_audit")
    audit = final_audit.audit_deliverables(
        output_dir,
        result_file="result.json",
        required_files=["formula_verification_report.json", "formula_notes.md"],
    )
    if not audit["passed"]:
        raise RuntimeError(f"final_audit failed: {audit['blocking_failures']}")
    return res


def run_symbol_guard_self_tests() -> dict[str, Any]:
    """Verify find_symbol_definition guards against E_a vs E confusion and English article 'a' false positives."""
    sample_paras = [
        {"paragraph_id": "p1", "text": "In a kinetic study, the activation energy E_a = 42.5 kJ/mol is measured under steady state."},
        {"paragraph_id": "p2", "text": "The Tafel equation uses a as a constant; specifically, a is the Tafel intercept in V and b is the slope in mV/dec."},
    ]
    r_E = find_symbol_definition("E", sample_paras[:1])
    r_Ea = find_symbol_definition("E_a", sample_paras[:1])
    r_a_article_only = find_symbol_definition("a", sample_paras[:1])
    r_a_defined = find_symbol_definition("a", sample_paras)
    checks = [
        {"test": "E_does_not_match_Ea", "passed": r_E["status"] == "undefined_in_supplied_text", "detail": r_E},
        {"test": "Ea_matches_explicit_def", "passed": r_Ea["status"] == "source_defined" and r_Ea["unit_hint"] == "kJ/mol", "detail": r_Ea},
        {"test": "article_a_not_extracted_as_symbol", "passed": r_a_article_only["status"] == "undefined_in_supplied_text", "detail": r_a_article_only},
        {"test": "math_intercept_a_extracted_correctly", "passed": r_a_defined["status"] == "source_defined" and r_a_defined["paragraph_id"] == "p2", "detail": r_a_defined},
    ]
    return {"ok": all(c["passed"] for c in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 7c37626c MathML/LaTeX Formula & Composite SI Verifier.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    parser.add_argument("--self-test", action="store_true", help="Run built-in math_ast_guard + symbol provenance self-tests.")
    args = parser.parse_args(argv)

    if args.self_test:
        from math_ast_guard import run_self_tests as _ast_tests
        t1 = _ast_tests()
        t2 = run_symbol_guard_self_tests()
        ok = bool(t1["ok"] and t2["ok"])
        print(json.dumps({"ok": ok, "ast_guard_tests": t1, "symbol_guard_tests": t2}, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    paths = preflight_probe.resolve_default_paths(
        input_dir=args.input or None,
        output_dir=args.output or None,
        work_dir=args.work or None,
    )
    res = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"])
    print(json.dumps({"ok": True, "case_id": res["case_id"], "status": res["status"], "formula_coverage_status": res["formula_coverage_status"], "metrics": res["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
