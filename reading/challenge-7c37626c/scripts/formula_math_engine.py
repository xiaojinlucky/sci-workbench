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
    check_dimensional_balance,
    check_equivalence,
    extract_symbols_from_latex,
    inspect_formula_noise,
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
    r"(?:in\s+units?\s+of|in|units?[:\s]+|\()\s*([A-Za-z\u00b5\u03bc\u03a9\u00c5%]+(?:\s*[*/^·⋅]\s*[A-Za-z0-9\u00b5\u03bc\u03a9\u00c5\-\+\^\(\)\{\}]+)*)\s*(?:\)|[,.;]|$)"
)
RANGE_HINT_RE = re.compile(
    r"((?:[0-9.]+\s*(?:<=|<|≤|to|–|-|~)\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:<=|<|≤)\s*[0-9.]+)|"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s*(?:>=|>|<=|<|≥|≤|=)\s*[-+]?[0-9]+(?:\.[0-9]+)?))"
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


def _extract_unit_and_range_hints(snippet: str, sym: str) -> tuple[str | None, str | None]:
    # Search in a window around the symbol occurrence
    pos = snippet.find(sym)
    window = snippet[max(0, pos - 20):pos + 120] if pos >= 0 else snippet[:140]
    unit_hint = None
    m_u = UNIT_HINT_RE.search(window)
    if m_u:
        cand = m_u.group(1).strip(" ,.;:")
        # Avoid false-positive common English words
        if cand.lower() not in {"the", "a", "an", "and", "or", "is", "are", "was", "to", "of", "for", "with", "that", "this", "which"}:
            unit_hint = cand
    range_hint = None
    m_r = RANGE_HINT_RE.search(window)
    if m_r:
        range_hint = m_r.group(1).strip()
    return unit_hint, range_hint


def find_symbol_definition(sym: str, paras: list[dict[str, str]]) -> dict[str, Any]:
    """Three-tier symbol provenance lookup: source_defined -> context_inferred -> undefined_in_supplied_text."""
    esc = re.escape(sym)
    explicit_pat = re.compile(
        rf"(?:where|here|let|denote[sd]?|represents?|defined\s+as|stands\s+for|其中|表示|定义为|记为)\s+[^\n.;]{{0,70}}\b{esc}\b"
        rf"|\b{esc}\b\s*(?:=|is\s+the|is\s+a|denotes|represents|stands\s+for|表示|代表|定义为|为)\s*([^\n.;]{{3,110}})",
        re.I,
    )
    for p in paras:
        if explicit_pat.search(p["text"]):
            snippet = " ".join(p["text"].split())[:220]
            unit_hint, range_hint = _extract_unit_and_range_hints(snippet, sym)
            return {
                "symbol": sym,
                "status": "source_defined",
                "paragraph_id": p["paragraph_id"],
                "unit_hint": unit_hint,
                "range_hint": range_hint,
                "definition_quote": snippet,
            }

    mention_pat = re.compile(rf"\b{esc}\b")
    for p in paras:
        if mention_pat.search(p["text"]):
            snippet = " ".join(p["text"].split())[:220]
            unit_hint, range_hint = _extract_unit_and_range_hints(snippet, sym)
            return {
                "symbol": sym,
                "status": "context_inferred",
                "paragraph_id": p["paragraph_id"],
                "unit_hint": unit_hint,
                "range_hint": range_hint,
                "definition_quote": snippet,
            }

    return {
        "symbol": sym,
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

    # Derivation & Numeric Checks
    drv = bundle.get("derivation_checks") or []
    if drv:
        lines.append("## Derivation Checks")
        lines.append("")
        for item in drv:
            lines.append(
                f"- `{item.get('step_id')}`: method=`{item.get('method')}`, equivalent=`{item.get('equivalent')}`, "
                f"assumptions=`{item.get('assumptions') or 'none_declared'}`"
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

    # 4. Variable provenance table (3 tiers)
    all_symbols = sorted({s for f in parsed_formulas for s in (f.get("symbols") or [])})
    variable_table = [find_symbol_definition(sym, paras) for sym in all_symbols]
    undefined_vars = [v["symbol"] for v in variable_table if v["status"] == "undefined_in_supplied_text"]

    # 5. Derivation equivalence checks (with assumption/boundary retention + OCR noise gate)
    budget_guard.tick_phase(budget_file, "verify_derivations_and_dimensions")
    derivation_checks: list[dict[str, Any]] = []
    for idx, drv in enumerate(case_data.get("derivation_checks") or [], start=1):
        if isinstance(drv, dict) and drv.get("lhs") and drv.get("rhs"):
            step_id = str(drv.get("step_id", f"S{idx}"))
            ref_fid = str(drv.get("formula_id") or "")
            if ref_fid and ref_fid in halted_formula_ids:
                derivation_checks.append({
                    "step_id": step_id,
                    "formula_id": ref_fid,
                    "method": "halted_ocr_noise",
                    "equivalent": None,
                    "assumptions": drv.get("assumptions") or [],
                    "boundary_conditions": drv.get("boundary_conditions") or [],
                    "reason": f"Skipped derivation check because source formula {ref_fid} failed OCR noise gate.",
                })
                continue
            chk = check_equivalence(str(drv["lhs"]), str(drv["rhs"]), test_env=drv.get("test_env"))
            chk["step_id"] = step_id
            if ref_fid:
                chk["formula_id"] = ref_fid
            chk["assumptions"] = drv.get("assumptions") or []
            chk["boundary_conditions"] = drv.get("boundary_conditions") or []
            if chk.get("method") == "halted_ocr_noise":
                gaps.append({
                    "code": "DERIVATION_HALTED_BY_OCR_NOISE",
                    "severity": "warning",
                    "source": step_id,
                    "message": f"Derivation step {step_id} halted due to noisy/corrupted formula tokens.",
                })
            derivation_checks.append(chk)

    # 6. General Composite SI Dimensional Checks
    dimensional_checks: list[dict[str, Any]] = []
    for idx, dchk in enumerate(case_data.get("dimensional_checks") or [], start=1):
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

    # 7. Numeric substitution checks (strict no-zero-fill + OCR noise guard)
    numeric_checks: list[dict[str, Any]] = []
    for idx, nchk in enumerate(case_data.get("numeric_checks") or [], start=1):
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
        "derivation_checks": derivation_checks,
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

    append_audit_entry(
        audit_file,
        phase="verify_math",
        action="formula_report_written",
        actor="formula_math_engine.py",
        output={
            "formula_count": len(parsed_formulas),
            "var_count": len(variable_table),
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
        "undefined_variable_count": len(undefined_vars),
        "ocr_halted_formula_count": len(halted_formula_ids),
        "derivation_check_count": len(derivation_checks),
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
        "undefined_variable_count": "formula_verification_report.json:variable_table",
        "ocr_halted_formula_count": "formula_verification_report.json:formulas",
        "derivation_check_count": "formula_verification_report.json:derivation_checks",
        "dimensional_check_count": "formula_verification_report.json:dimensional_checks",
        "dimensional_mismatch_count": "formula_verification_report.json:dimensional_checks",
        "dimensional_oom_offset_count": "formula_verification_report.json:dimensional_checks",
        "numeric_check_count": "formula_verification_report.json:numeric_checks",
        "markdown_validation_fail_count": "formula_verification_report.json:markdown_validation",
    }

    patch = {
        "artifacts": ["result.json", "formula_verification_report.json", "formula_notes.md"],
        "formula_coverage_status": formula_coverage_status,
        "xml_sources_used": xml_sources_used,
        "formulas": parsed_formulas,
        "variable_table": variable_table,
        "derivation_checks": derivation_checks,
        "dimensional_checks": dimensional_checks,
        "numeric_checks": numeric_checks,
        "markdown_validation": md_validation,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "findings": [
            f"Extracted {len(parsed_formulas)} unique formula node(s) from {len(xml_sources_used)} XML source(s) without double-counting; "
            f"source_defined_vars={metrics['source_defined_variable_count']}/{len(variable_table)}; "
            f"ocr_halted={len(halted_formula_ids)}; formula_coverage_status={formula_coverage_status}."
        ],
        "gaps": gaps,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harbor 7c37626c MathML/LaTeX Formula & Composite SI Verifier.")
    parser.add_argument("--input", default="", help="Input directory (auto-detects /input or /app/input).")
    parser.add_argument("--output", default="", help="Output directory (auto-detects /app/output or /output).")
    parser.add_argument("--work", default="", help="Work directory (auto-detects /app/work or cwd).")
    args = parser.parse_args(argv)

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
