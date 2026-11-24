#!/usr/bin/env python3
"""Structured Paper Conclusion & Data Extraction Engine (challenge-105b70ee).

Implements Organizer Revision v2 & Wenyon Output Contract:
- Reads `/input/target-fields.json` (or `case.json.target_fields`) plus paper paragraphs,
  verbatim tables (JSON/CSV/Markdown with cell-level coordinates), captions, and PDFs.
- Emits ONE structured record per requested `field_id` preserving:
  `scalar`, `units`, `statistic`, `experimental_condition`, `comparator`,
  `paragraph_id`, `quote`, `epistemic_class`, and 4-state `status` in
  `{reported, not_reported, source_conflict, extraction_failed}`.
- Sentence-level condition-value binding filters out years, citation brackets, figure/table
  numbers, and p-values from usurping primary scalars; preserves missing cells as null
  (NEVER 0) and missing units as null (never borrowed from neighbor sentences).
- Distinguishes true cross-source numeric conflicts (e.g., prose vs. table for the same
  condition -> `source_conflict` with both anchors) from condition-stratified measurements.
- Strict `--validate-fields` verifier enforces exact quote match AND numeric-core presence
  inside the cited quote (preventing silent rounding or unit conversion).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from citation_anchors import extract_emlsi_fingerprint, verify_exact_quote
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import budget_guard
    import final_audit
    import gap_gate
    import preflight_probe
    import result_guard
    from audit_chain import append_audit_entry, init_audit_log
    from citation_anchors import extract_emlsi_fingerprint, verify_exact_quote


VALID_FIELD_STATUSES = {"reported", "not_reported", "source_conflict", "extraction_failed"}
VALID_EPISTEMIC = {"reported_result", "conditional_limitation", "speculative", "not_reported", "unavailable"}
FIELD_OUTPUT_KEYS = (
    "field_id", "scalar", "units", "statistic", "experimental_condition",
    "comparator", "paragraph_id", "quote", "status",
)

MISSING_PLACEHOLDERS = {"", "na", "n/a", "nd", "n.d.", "n/r", "nr", "null", "none", "-", "--", "—", "–", "not reported", "unreported"}

UNIT_PATTERN = (
    r"(?:"
    r"mA\s*cm\s*(?:\^?-?2|⁻²)|mA/cm2|A\s*g\s*(?:\^?-?1|⁻¹)|A/g|mAh\s*g\s*(?:\^?-?1|⁻¹)|mAh/g|"
    r"mmol\s*g\s*(?:\^?-?1|⁻¹)\s*h\s*(?:\^?-?1|⁻¹)|µmol\s*g\s*(?:\^?-?1|⁻¹)\s*h\s*(?:\^?-?1|⁻¹)|"
    r"mV\s*dec\s*(?:\^?-?1|⁻¹)|mV/dec|mg\s*L\s*(?:\^?-?1|⁻¹)|mg/L|µg/mL|ug/mL|mg/mL|mmol/L|mol/L|"
    r"mg/g|mg/kg|wt%|at%|vol%|%|µM|uM|mM|nM|pM|mV|V|kV|nm|µm|um|mm|cm|m|g|mg|µg|ug|kg|"
    r"h|hr|hrs|min|s|ms|µs|ns|K|°C|Pa|kPa|MPa|GPa|Hz|kHz|MHz|GHz|ppm|ppb|eV|keV|MeV|"
    r"W/m2|mW/cm2|J/g|kJ/mol|kcal/mol|fold|x|days|weeks|months|years"
    r")"
)

MEASUREMENT_RE = re.compile(
    rf"(?P<prefix>[<>≤≥~≈]\s*)?"
    rf"(?P<num>[-+]?\d+(?:,\d{{3}})*(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s*(?:±|\+/-|to|–|-)\s*\d+(?:\.\d+)?)?)"
    rf"(?:\s*(?P<unit>{UNIT_PATTERN}))?(?![A-Za-z0-9_])"
)

STAT_RE = re.compile(
    r"(?:mean\s*(?:±|\+/-)\s*SD|mean\s*(?:±|\+/-)\s*SEM|median\s*(?:\(|\[)?IQR|"
    r"p\s*[<=>≤≥]\s*0?\.\d+(?:[eE][-+]?\d+)?|95%\s*CI\s*(?:\[|\()[^\]\);]+(?:\]|\))|"
    r"\bn\s*=\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\s*[-–]\s*\d+)?|R\^?2\s*=\s*0?\.\d+|ANOVA|t-test|Mann-Whitney|Wilcoxon|SD\s*=\s*[\d.]+|SEM\s*=\s*[\d.]+)",
    re.I,
)

SPECULATIVE_RE = re.compile(
    r"\b(?:may|might|could|possibly|potentially|suggests?\s+that|hypothesi[sz]e|likely|appear(?:s|ed)?\s+to|plausible)\b|"
    r"(?:可能|或许|提示|推测|有望|尚待验证)",
    re.I,
)
LIMITATION_RE = re.compile(
    r"\b(?:limited\s+to|restricted\s+to|only\s+(?:under|when|for|at)|however|although|caveat|limitation|failed\s+to|did\s+not\s+generalize)\b|"
    r"(?:仅限于|局限于|受限于|然而|尽管如此|局限性)",
    re.I,
)
CONDITION_CUES_RE = re.compile(
    r"(?:\b(?:at|under|in|after|for|with|using)\s+[^,.;]{3,55}|\bpH\s*[:=]?\s*\d+(?:\.\d+)?|"
    r"\b\d+(?:\.\d+)?\s*(?:°C|K|MPa|kPa|bar|atm|mM|µM|M|mg/L|h|hr|min|days|rpm)\b|"
    r"\bn\s*=\s*(?:\d{1,3}(?:,\d{3})+|\d+))",
    re.I,
)
COMPARATOR_RE = re.compile(
    r"(?:\b(?:compared\s+(?:with|to)|versus|vs\.?|relative\s+to|over|against|outperform(?:ed|ing)?)\s+[^,.;]{2,50})",
    re.I,
)
HEADER_UNIT_RE = re.compile(r"\(([^()]{1,28})\)\s*$")


def _pdfplumber_column_aware_text(page: Any) -> str:
    """Extract pdfplumber text with automatic two-column vertical split to prevent cross-column bleed."""
    try:
        w, h = float(page.width), float(page.height)
        words = page.extract_words() or []
        mid = w / 2.0
        body_words = [wd for wd in words if wd["top"] > h * 0.10 and wd["bottom"] < h * 0.90]
        if len(body_words) >= 40:
            left_w = sum(1 for wd in body_words if wd["x1"] <= mid + 4.0)
            right_w = sum(1 for wd in body_words if wd["x0"] >= mid - 4.0)
            cross_w = sum(1 for wd in body_words if wd["x0"] < mid - 4.0 and wd["x1"] > mid + 4.0)
            if left_w >= 20 and right_w >= 20 and (cross_w / max(1, len(body_words))) < 0.08:
                left_txt = page.crop((0, 0, mid, h)).extract_text() or ""
                right_txt = page.crop((mid, 0, w, h)).extract_text() or ""
                return (left_txt.strip() + "\n\n" + right_txt.strip()).strip()
    except Exception:
        pass
    return page.extract_text() or ""


def _pdf_pages_text(pdf_path: Path) -> list[tuple[int, str]]:
    """Extract per-page reading-order text (pdftotext reading-flow -> pypdf -> column-aware pdfplumber)."""
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
            )
            if proc.returncode == 0:
                pages = proc.stdout.split("\f")
                if pages and not pages[-1].strip():
                    pages.pop()
                if pages:
                    return [(i, t) for i, t in enumerate(pages, 1)]
        except Exception:
            pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return [(i, p.extract_text() or "") for i, p in enumerate(reader.pages, 1)]
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            return [(i, _pdfplumber_column_aware_text(p)) for i, p in enumerate(doc.pages, 1)]
    except Exception:
        pass
    return []


def _header_unit(header: str) -> str | None:
    m = HEADER_UNIT_RE.search(header.strip())
    return m.group(1).strip() if m else None


def _table_rows_to_cell_blocks(
    table_id: str,
    source_file: str,
    headers: list[str],
    rows: list[list[Any]],
    caption: str = "",
) -> list[dict[str, Any]]:
    """Expand a 2D table into cell-anchored blocks preserving row_label, col_header, and header_unit."""
    blocks: list[dict[str, Any]] = []
    if not headers:
        return blocks
    # Also add one full table text block for exact table-level lookup
    full_lines = [f"{table_id} {caption}".strip(), " | ".join(str(h) for h in headers)]
    for r in rows:
        full_lines.append(" | ".join(str(c) for c in r))
    blocks.append({
        "paragraph_id": table_id,
        "section": "Tables",
        "source_file": source_file,
        "source_kind": "table_full",
        "table_id": table_id,
        "text": "\n".join(full_lines),
    })

    for ridx, row in enumerate(rows, 1):
        row_label = str(row[0]).strip() if row else f"row_{ridx}"
        for cidx, col_h in enumerate(headers):
            if cidx == 0 and len(headers) > 1:
                continue
            raw_cell = str(row[cidx]).strip() if cidx < len(row) else ""
            is_missing = raw_cell.lower() in MISSING_PLACEHOLDERS
            h_unit = _header_unit(str(col_h))
            cell_pid = f"{table_id}:R{ridx}:C{cidx+1}"
            cell_text = f"{caption} | {row_label} | {col_h}: {raw_cell}".strip(" |")
            blocks.append({
                "paragraph_id": cell_pid,
                "parent_table_id": table_id,
                "section": "Tables",
                "source_file": source_file,
                "source_kind": "table_cell",
                "table_id": table_id,
                "row_index": ridx,
                "row_label": row_label,
                "col_header": str(col_h),
                "header_unit": h_unit,
                "raw_cell": raw_cell,
                "is_missing_placeholder": is_missing,
                "text": cell_text,
            })
    return blocks


def _extract_md_tables_as_blocks(text: str, source_file: str, base_id: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    i = 0
    t_idx = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:-]+\|", lines[i + 1]):
            cap = lines[i - 1].strip() if i > 0 and re.match(r"^(?:Table|表)", lines[i - 1].strip(), re.I) else ""
            headers = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t_idx += 1
            tid = f"{base_id}_T{t_idx:02d}"
            blocks.extend(_table_rows_to_cell_blocks(tid, source_file, headers, rows, caption=cap))
        else:
            i += 1
    return blocks


def collect_source_blocks(input_dir: Path, case_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect grounded prose paragraphs, captions, and cell-level table blocks."""
    blocks: list[dict[str, Any]] = []

    def add_paragraphs_from_obj(obj: dict[str, Any], src_name: str) -> None:
        for idx, p in enumerate(obj.get("paragraphs") or [], 1):
            if isinstance(p, dict):
                pid = str(p.get("paragraph_id") or p.get("id") or f"{src_name}:P{idx:03d}")
                txt = str(p.get("text") or p.get("content") or "").strip()
                sec = str(p.get("section") or "Body")
            else:
                pid = f"{src_name}:P{idx:03d}"
                txt = str(p).strip()
                sec = "Body"
            if txt:
                blocks.append({
                    "paragraph_id": pid,
                    "section": sec,
                    "source_file": src_name,
                    "source_kind": "prose",
                    "text": txt,
                })
                if "|" in txt:
                    blocks.extend(_extract_md_tables_as_blocks(txt, src_name, pid))

        for tidx, t in enumerate(obj.get("tables") or [], 1):
            if isinstance(t, dict):
                tid = str(t.get("paragraph_id") or t.get("table_id") or t.get("id") or f"T{tidx:02d}")
                cap = str(t.get("caption") or t.get("title") or "")
                cols = t.get("columns") or t.get("headers")
                rows = t.get("rows")
                if isinstance(cols, list) and isinstance(rows, list) and cols:
                    norm_rows: list[list[Any]] = []
                    for r in rows:
                        if isinstance(r, list):
                            norm_rows.append(r)
                        elif isinstance(r, dict):
                            norm_rows.append([r.get(c, "") for c in cols])
                    blocks.extend(_table_rows_to_cell_blocks(tid, src_name, [str(c) for c in cols], norm_rows, caption=cap))
                else:
                    raw_t = str(t.get("text") or t.get("content") or json.dumps(t, ensure_ascii=False))
                    blocks.append({
                        "paragraph_id": tid,
                        "section": "Tables",
                        "source_file": src_name,
                        "source_kind": "table_full",
                        "table_id": tid,
                        "text": raw_t,
                    })

    add_paragraphs_from_obj(case_data, "case.json")

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if fp.name in ("case.json", "target-fields.json", "task.md"):
            continue
        rel = fp.relative_to(input_dir).as_posix()
        ext = fp.suffix.lower()
        if ext == ".csv":
            try:
                rows = list(csv.reader(fp.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
                if rows:
                    blocks.extend(_table_rows_to_cell_blocks(fp.stem, rel, rows[0], rows[1:], caption=fp.stem))
            except Exception:
                pass
        elif ext == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8-sig"))
                if isinstance(obj, dict):
                    if "paragraphs" in obj or "tables" in obj:
                        add_paragraphs_from_obj(obj, rel)
                    elif "columns" in obj and "rows" in obj and isinstance(obj["columns"], list) and isinstance(obj["rows"], list):
                        blocks.extend(_table_rows_to_cell_blocks(fp.stem, rel, [str(c) for c in obj["columns"]], obj["rows"], caption=str(obj.get("caption") or fp.stem)))
            except Exception:
                pass
        elif ext == ".pdf":
            for pno, ptxt in _pdf_pages_text(fp):
                for bno, chunk in enumerate(re.split(r"\n\s*\n", ptxt), 1):
                    cleaned = " ".join(chunk.split())
                    if cleaned:
                        blocks.append({
                            "paragraph_id": f"{fp.stem}:p{pno}:B{bno:03d}",
                            "section": f"Page {pno}",
                            "source_file": rel,
                            "page": pno,
                            "source_kind": "pdf_prose",
                            "text": cleaned,
                        })
        elif ext in {".md", ".txt", ".tex", ".xml", ".html"}:
            raw = fp.read_text(encoding="utf-8-sig", errors="replace")
            blocks.extend(_extract_md_tables_as_blocks(raw, rel, fp.stem))
            for bno, chunk in enumerate(re.split(r"\n\s*\n", raw), 1):
                cleaned = " ".join(chunk.split())
                if cleaned and not cleaned.startswith("|"):
                    blocks.append({
                        "paragraph_id": f"{fp.stem}:B{bno:03d}",
                        "section": fp.suffix.lstrip("."),
                        "source_file": rel,
                        "source_kind": "prose",
                        "text": cleaned,
                    })

    return blocks


def load_target_fields(input_dir: Path, case_data: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    tf_path = input_dir / "target-fields.json"
    found_tf = tf_path.is_file()
    raw_fields: Any = []
    if found_tf:
        loaded = json.loads(tf_path.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, list):
            raw_fields = loaded
        elif isinstance(loaded, dict):
            raw_fields = loaded.get("fields") or loaded.get("target_fields") or loaded.get("items") or []
    else:
        raw_fields = case_data.get("target_fields") or case_data.get("fields") or []

    out: list[dict[str, Any]] = []
    for idx, f in enumerate(raw_fields or [], 1):
        if isinstance(f, dict):
            fid = str(f.get("field_id") or f.get("id") or f.get("name") or f"F{idx:02d}")
            out.append({
                "field_id": fid,
                "label": str(f.get("label") or f.get("name") or fid),
                "description": str(f.get("description") or f.get("query") or ""),
                "expected_unit": str(f.get("expected_unit") or f.get("units") or ""),
                "target_condition": str(f.get("condition") or f.get("experimental_condition") or ""),
                "keywords": [str(k) for k in (f.get("keywords") or [])],
            })
        else:
            out.append({
                "field_id": str(f), "label": str(f), "description": "",
                "expected_unit": "", "target_condition": "", "keywords": [],
            })
    return out, found_tf


def _tokens(text: str) -> list[str]:
    low = text.lower()
    toks = re.findall(r"[a-z0-9][a-z0-9._/%-]{1,}", low)
    for span in re.findall(r"[\u4e00-\u9fff]{2,}", low):
        toks.extend(span[i : i + 2] for i in range(len(span) - 1))
    return toks


def _is_non_measurement_number(sentence: str, start: int, end: int, raw_num: str, unit: str | None) -> bool:
    """Filter out years, citation brackets [12], Fig./Table numbers, p-values, and n= counts."""
    before = sentence[max(0, start - 20) : start]
    after = sentence[end : min(len(sentence), end + 8)]
    if re.search(r"\[\s*(?:\d+\s*[-–,]\s*)*$", before) and re.match(r"^\s*(?:[-–,]\s*\d+\s*)*\]", after):
        return True
    if re.search(r"\b(?:fig(?:ure)?\.?|table|section|eq(?:uation)?\.?|scheme|ref\.?|图|表)\s*[sS]?$", before, re.I):
        return True
    if re.search(r"\bp\s*[<=>≤≥]\s*$", before, re.I):
        return True
    if re.search(r"\bn\s*=\s*$", before, re.I):
        return True
    if not unit and re.fullmatch(r"(?:19\d{2}|20[0-3]\d)", raw_num):
        if re.search(r"(?:in|\(|et\s+al\.?,|©)\s*$", before, re.I):
            return True
    return False


def _extract_best_sentence_measurement(
    block: dict[str, Any],
    q_toks: set[str],
    expected_unit: str,
) -> dict[str, Any]:
    """Bind scalar, unit, statistic, condition, comparator, and epistemic class within the best sentence."""
    # Direct table cell fast path (exact row/column coordinate binding)
    if block.get("source_kind") == "table_cell":
        raw_cell = str(block.get("raw_cell") or "").strip()
        h_unit = block.get("header_unit")
        if block.get("is_missing_placeholder"):
            return {
                "scalar": None,
                "units": h_unit,
                "statistic": None,
                "experimental_condition": block.get("row_label"),
                "comparator": None,
                "quote": block["text"][:220],
                "epistemic_class": "not_reported",
                "is_missing_cell": True,
                "unit_status": "missing_value_cell",
            }
        m = MEASUREMENT_RE.search(raw_cell)
        scalar = None
        unit = h_unit
        if m:
            pref = (m.group("prefix") or "").strip()
            scalar = f"{pref}{m.group('num').strip()}" if pref else m.group("num").strip()
            if m.group("unit"):
                unit = m.group("unit").strip()
        elif raw_cell:
            scalar = raw_cell
        return {
            "scalar": scalar,
            "units": unit,
            "statistic": "; ".join(STAT_RE.findall(raw_cell)) or None,
            "experimental_condition": block.get("row_label"),
            "comparator": None,
            "quote": block["text"][:220],
            "epistemic_class": "reported_result",
            "is_missing_cell": False,
            "unit_status": "reported" if unit else "unit_missing_in_source",
        }

    # Prose sentence-level binding
    text = block["text"]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", text) if s.strip()] or [text]
    best_sent = sentences[0]
    best_score = -1.0
    for s in sentences:
        stoks = set(_tokens(s))
        ov = len(stoks & q_toks)
        has_meas = any(
            not _is_non_measurement_number(s, m.start(), m.end(), m.group("num"), m.group("unit"))
            for m in MEASUREMENT_RE.finditer(s)
        )
        sc = ov + (1.5 if has_meas else 0.0)
        if expected_unit and expected_unit.lower() in s.lower():
            sc += 1.2
        if sc > best_score:
            best_score = sc
            best_sent = s

    candidates_m: list[tuple[float, re.Match[str]]] = []
    for m in MEASUREMENT_RE.finditer(best_sent):
        if _is_non_measurement_number(best_sent, m.start(), m.end(), m.group("num"), m.group("unit")):
            continue
        u = (m.group("unit") or "").strip()
        score_m = 0.0
        if expected_unit and u.lower() == expected_unit.lower():
            score_m += 4.0
        elif u:
            score_m += 2.0
        if "±" in m.group("num") or "+/-" in m.group("num"):
            score_m += 1.0
        # Proximity to query terms inside sentence
        ctx_window = best_sent[max(0, m.start() - 60) : min(len(best_sent), m.end() + 40)]
        score_m += 0.6 * len(set(_tokens(ctx_window)) & q_toks)
        candidates_m.append((score_m, m))

    candidates_m.sort(key=lambda x: -x[0])
    scalar = None
    unit = None
    if candidates_m:
        picked = candidates_m[0][1]
        pref = (picked.group("prefix") or "").strip()
        scalar = f"{pref}{picked.group('num').strip()}" if pref else picked.group("num").strip()
        unit = (picked.group("unit") or "").strip() or None

    stats = STAT_RE.findall(best_sent)
    conds = list(dict.fromkeys(m.group(0).strip() for m in CONDITION_CUES_RE.finditer(best_sent)))[:4]
    comps = list(dict.fromkeys(m.group(0).strip() for m in COMPARATOR_RE.finditer(best_sent)))[:3]
    if not conds:
        fp = extract_emlsi_fingerprint(best_sent)
        conds = fp.get("S_scope_conditions") or []

    if SPECULATIVE_RE.search(best_sent):
        epistemic = "speculative"
    elif LIMITATION_RE.search(best_sent):
        epistemic = "conditional_limitation"
    else:
        epistemic = "reported_result"

    return {
        "scalar": scalar,
        "units": unit,
        "statistic": "; ".join(dict.fromkeys(stats)) if stats else None,
        "experimental_condition": "; ".join(conds) if conds else None,
        "comparator": "; ".join(comps) if comps else None,
        "quote": best_sent[:260],
        "epistemic_class": epistemic,
        "is_missing_cell": False,
        "unit_status": "reported" if unit else ("unit_missing_in_source" if scalar is not None else "no_scalar"),
    }


def _numeric_core(scalar: Any) -> str | None:
    if scalar is None:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(scalar).replace(",", ""))
    return m.group(0) if m else None


def _conditions_distinct(c1: str | None, c2: str | None) -> bool:
    """Detect whether two candidate passages/rows clearly describe different groups or conditions."""
    if not c1 or not c2:
        return False
    s1 = c1.strip().lower()
    s2 = c2.strip().lower()
    if s1 == s2:
        return False
    # Check distinct temperatures, pH values, or concentrations
    t1 = set(re.findall(r"\b\d+(?:\.\d+)?\s*(?:°C|K|pH|mM|µM)\b", c1, re.I))
    t2 = set(re.findall(r"\b\d+(?:\.\d+)?\s*(?:°C|K|pH|mM|µM)\b", c2, re.I))
    if t1 and t2 and t1 != t2:
        return True
    # Distinct short table row labels (e.g., "Cat-B" vs "Bare Cu") represent different groups
    if len(s1) <= 32 and len(s2) <= 32 and not (s1 in s2 or s2 in s1):
        return True
    return False


def extract_field_record(field_spec: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    fid = field_spec["field_id"]
    query = " ".join([
        fid.replace("_", " "),
        field_spec["label"],
        field_spec["description"],
        field_spec["target_condition"],
        " ".join(field_spec["keywords"]),
    ]).strip()
    q_toks = set(_tokens(query))

    if not blocks:
        return {
            "field_id": fid,
            "scalar": None,
            "units": None,
            "statistic": None,
            "experimental_condition": None,
            "comparator": None,
            "paragraph_id": None,
            "quote": "",
            "status": "extraction_failed",
            "epistemic_class": "unavailable",
            "reason": "No readable source blocks available.",
        }

    # BM25-like IDF scoring across prose and table_cell blocks (skip table_full when cells exist to avoid double counting)
    searchable = [b for b in blocks if b.get("source_kind") != "table_full"] or blocks
    doc_cnts = [Counter(_tokens(b["text"])) for b in searchable]
    n_docs = len(searchable)
    df: Counter[str] = Counter()
    for cnt in doc_cnts:
        for t in cnt:
            df[t] += 1

    hits: list[tuple[float, int, dict[str, Any]]] = []
    min_ov = 2 if len(q_toks) >= 4 else 1
    for b, cnt in zip(searchable, doc_cnts):
        ov = q_toks & set(cnt.keys())
        if len(ov) < min_ov:
            continue
        sc = sum(math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5)) for t in ov)
        # Boost exact table cell header match
        if b.get("source_kind") == "table_cell":
            col_ov = set(_tokens(str(b.get("col_header") or ""))) & q_toks
            row_ov = set(_tokens(str(b.get("row_label") or ""))) & q_toks
            if col_ov:
                sc += 1.5 * len(col_ov)
            if row_ov:
                sc += 1.5 * len(row_ov)
        norm_sc = round(sc / max(1.0, math.sqrt(len(q_toks))), 4)
        hits.append((norm_sc, len(ov), b))

    hits.sort(key=lambda x: (-x[0], -x[1]))
    if not hits or hits[0][0] < 0.45:
        return {
            "field_id": fid,
            "scalar": None,
            "units": None,
            "statistic": None,
            "experimental_condition": None,
            "comparator": None,
            "paragraph_id": None,
            "quote": "",
            "status": "not_reported",
            "epistemic_class": "not_reported",
            "reason": "No source passage matched requested field above relevance threshold; absent field kept null (never 0).",
        }

    candidates: list[dict[str, Any]] = []
    for sc, _ovc, blk in hits[:5]:
        ext = _extract_best_sentence_measurement(blk, q_toks, field_spec["expected_unit"])
        candidates.append({
            "paragraph_id": blk["paragraph_id"],
            "source_file": blk.get("source_file"),
            "source_kind": blk.get("source_kind"),
            "score": sc,
            **ext,
        })

    # If top hit is an explicit missing placeholder cell (NA / ND / —), return not_reported with anchor
    best = candidates[0]
    if best.get("is_missing_cell"):
        return {
            "field_id": fid,
            "scalar": None,
            "units": best["units"],
            "statistic": None,
            "experimental_condition": best["experimental_condition"],
            "comparator": None,
            "paragraph_id": best["paragraph_id"],
            "quote": best["quote"],
            "status": "not_reported",
            "epistemic_class": "not_reported",
            "reason": "Matched source table cell explicitly records missing/not-reported placeholder; preserved as null (never 0).",
        }

    # Distinguish true same-condition cross-source conflict vs condition-stratified values
    top_core = _numeric_core(best["scalar"])
    true_conflicts: list[dict[str, Any]] = []
    stratified_others: list[dict[str, Any]] = []
    for cand in candidates[1:]:
        if cand["score"] < best["score"] * 0.65:
            continue
        c_core = _numeric_core(cand["scalar"])
        if top_core is not None and c_core is not None and c_core != top_core:
            if _conditions_distinct(best.get("experimental_condition"), cand.get("experimental_condition")):
                stratified_others.append(cand)
            else:
                true_conflicts.append(cand)

    if true_conflicts:
        conflict_pack = [best] + true_conflicts
        return {
            "field_id": fid,
            "scalar": best["scalar"],
            "units": best["units"],
            "statistic": best["statistic"],
            "experimental_condition": best["experimental_condition"],
            "comparator": best["comparator"],
            "paragraph_id": best["paragraph_id"],
            "quote": best["quote"],
            "status": "source_conflict",
            "epistemic_class": best["epistemic_class"],
            "unit_status": best["unit_status"],
            "conflicting_sources": [
                {
                    "paragraph_id": c["paragraph_id"],
                    "source_kind": c["source_kind"],
                    "scalar": c["scalar"],
                    "units": c["units"],
                    "quote": c["quote"],
                }
                for c in conflict_pack
            ],
            "reason": "Conflicting numeric values found across source passages/tables without distinguishing experimental conditions; both locations preserved.",
        }

    rec: dict[str, Any] = {
        "field_id": fid,
        "scalar": best["scalar"],
        "units": best["units"],
        "statistic": best["statistic"],
        "experimental_condition": best["experimental_condition"],
        "comparator": best["comparator"],
        "paragraph_id": best["paragraph_id"],
        "quote": best["quote"],
        "status": "reported" if best["scalar"] is not None else "not_reported",
        "epistemic_class": best["epistemic_class"] if best["scalar"] is not None else "not_reported",
        "unit_status": best["unit_status"],
    }
    if stratified_others:
        rec["condition_stratified_candidates"] = [
            {
                "paragraph_id": c["paragraph_id"],
                "scalar": c["scalar"],
                "units": c["units"],
                "experimental_condition": c["experimental_condition"],
            }
            for c in stratified_others[:3]
        ]
    return rec


def validate_fields_payload(
    fields_out: list[dict[str, Any]],
    required_ids: list[str],
    block_lookup: dict[str, str],
) -> dict[str, Any]:
    """Strict structural, verbatim-quote, numeric-fidelity, and zero-default-zero validation."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(fields_out, list):
        return {"ok": False, "errors": ["fields payload must be a JSON array"], "warnings": []}
    if any(not isinstance(f, dict) for f in fields_out):
        return {"ok": False, "errors": ["every fields entry must be a JSON object"], "warnings": []}

    emitted_ids = [str(f.get("field_id")) for f in fields_out]
    dups = sorted({fid for fid in emitted_ids if emitted_ids.count(fid) > 1})
    if dups:
        errors.append(f"duplicate field_ids: {dups}")
    missing = [rid for rid in required_ids if rid not in emitted_ids]
    if missing:
        errors.append(f"missing requested field_ids: {missing}")
    extra = sorted(set(emitted_ids) - set(required_ids))
    if extra:
        errors.append(f"unexpected field_ids: {extra}")

    for rec in fields_out:
        fid = rec.get("field_id")
        miss_k = [k for k in FIELD_OUTPUT_KEYS if k not in rec]
        if miss_k:
            errors.append(f"field {fid}: missing required keys {miss_k}")
        st = rec.get("status")
        if st not in VALID_FIELD_STATUSES:
            errors.append(f"field {fid}: invalid status {st!r}")
        if st in ("not_reported", "extraction_failed") and rec.get("scalar") is not None:
            errors.append(f"field {fid}: absent/failed field MUST have scalar=null (never 0 or guessed value)")
        pid = rec.get("paragraph_id")
        quote = str(rec.get("quote") or "")

        if st == "reported":
            if rec.get("scalar") is None:
                errors.append(f"field {fid}: reported status requires non-null scalar")
            if not pid or pid not in block_lookup:
                errors.append(f"field {fid}: reported status requires valid paragraph_id in corpus (got {pid!r})")
            else:
                if not verify_exact_quote(quote, block_lookup[pid]):
                    errors.append(f"field {fid}: quote does not match exact source text at {pid}")
                # Check numeric core appears verbatim in quote (prevents silent rounding or fabrication)
                nc = _numeric_core(rec.get("scalar"))
                if nc and nc not in quote.replace(",", ""):
                    errors.append(f"field {fid}: scalar numeric core {nc!r} not found verbatim inside quote at {pid}")
            if not rec.get("units"):
                warnings.append(f"field {fid}: units is null; confirm unit is genuinely absent in source")

        if st == "source_conflict":
            cs = rec.get("conflicting_sources") or []
            if not isinstance(cs, list) or len(cs) < 2:
                errors.append(f"field {fid}: source_conflict requires >=2 entries in conflicting_sources[]")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def _render_report_md(case_id: str, contract_status: str, fields_out: list[dict[str, Any]], uncertainties: list[str]) -> str:
    lines = [
        f"# Structured Literature Data & Conclusion Extraction (`{case_id}`)",
        "",
        f"- **Contract Status**: `{contract_status}` | **Total Target Fields**: `{len(fields_out)}`",
        "",
        "| field_id | status | scalar | units | statistic | condition | comparator | epistemic | location |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in fields_out:
        lines.append(
            f"| `{f.get('field_id')}` | `{f.get('status')}` | `{f.get('scalar')}` | `{f.get('units')}` | "
            f"{f.get('statistic') or '-'} | {f.get('experimental_condition') or '-'} | "
            f"{f.get('comparator') or '-'} | `{f.get('epistemic_class', '-')}` | `{f.get('paragraph_id') or '-'}` |"
        )
    lines.extend(["", "## Verbatim Evidence Quotes & Conflicts", ""])
    for f in fields_out:
        lines.append(f"### `{f.get('field_id')}` (`{f.get('status')}`)")
        if f.get("quote"):
            lines.append(f"- Anchor `{f.get('paragraph_id')}`: \"{f.get('quote')}\"")
        if f.get("conflicting_sources"):
            lines.append("- Conflicting sources preserved:")
            for cs in f["conflicting_sources"]:
                lines.append(f"  - `{cs.get('paragraph_id')}` ({cs.get('source_kind')}): scalar=`{cs.get('scalar')}` units=`{cs.get('units')}` — \"{cs.get('quote')}\"")
        if f.get("reason"):
            lines.append(f"- Note: {f.get('reason')}")
        lines.append("")
    if uncertainties:
        lines.extend(["## Uncertainties & Boundaries", ""])
        for u in uncertainties:
            lines.append(f"- {u}")
        lines.append("")
    return "\n".join(lines)


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    work_dir: Path,
    validate_fields_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    preflight_probe.probe_environment(input_dir=input_dir, output_dir=output_dir, work_dir=work_dir)
    budget_guard.init_budget(work_dir / ".budget_state.json")
    audit_file = work_dir / "audit_log.json"
    init_audit_log(audit_file, run_label="105b70ee-field-extractor")

    case_path = input_dir / "case.json"
    case_data: dict[str, Any] = {}
    input_errors: list[str] = []
    if case_path.is_file():
        try:
            case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            input_errors.append(f"Malformed case.json: {exc}")
    case_id = str(case_data.get("case_id") or "case-105b70ee-default")

    blocks = collect_source_blocks(input_dir, case_data)
    block_lookup = {str(b["paragraph_id"]): str(b["text"]) for b in blocks}
    block_source_lookup = {str(b["paragraph_id"]): str(b.get("source_file", "corpus")) for b in blocks}

    try:
        target_fields, found_tf = load_target_fields(input_dir, case_data)
    except Exception as exc:
        target_fields, found_tf = [], False
        input_errors.append(f"Malformed target-fields.json: {exc}")

    required_ids = [f["field_id"] for f in target_fields]
    exit_code = 0
    validation_report: dict[str, Any] | None = None

    if validate_fields_path is not None:
        if not validate_fields_path.is_file():
            input_errors.append(f"Requested --validate-fields draft not found: {validate_fields_path}")
            fields_out: list[dict[str, Any]] = []
            exit_code = 1
        else:
            try:
                loaded_draft = json.loads(validate_fields_path.read_text(encoding="utf-8-sig"))
                fields_out = loaded_draft.get("fields", loaded_draft) if isinstance(loaded_draft, dict) else loaded_draft
                validation_report = validate_fields_payload(fields_out, required_ids, block_lookup)
                if not validation_report["ok"]:
                    input_errors.extend(validation_report["errors"])
                    exit_code = 1
            except Exception as exc:
                input_errors.append(f"Malformed --validate-fields draft JSON: {exc}")
                fields_out = []
                exit_code = 1
    else:
        fields_out = [extract_field_record(fspec, blocks) for fspec in target_fields]

    # Build grounded claims only from verified reported or source_conflict records
    claims: list[dict[str, Any]] = []
    uncertainties: list[str] = list(input_errors)
    reported_cnt = 0
    not_reported_cnt = 0
    conflict_cnt = 0
    failed_cnt = 0

    for rec in fields_out:
        st = rec.get("status")
        fid = rec.get("field_id")
        pid = rec.get("paragraph_id")
        if st == "reported":
            reported_cnt += 1
            claims.append({
                "kind": rec.get("epistemic_class") or "reported_result",
                "evidence": rec.get("quote", ""),
                "source_id": block_source_lookup.get(str(pid), "corpus"),
                "location": str(pid),
                "field_id": fid,
                "scalar": rec.get("scalar"),
                "units": rec.get("units"),
            })
            if not rec.get("units"):
                uncertainties.append(f"Field `{fid}` reported without explicit unit in source at `{pid}`.")
        elif st == "source_conflict":
            conflict_cnt += 1
            uncertainties.append(f"Field `{fid}` has conflicting source values across passages/tables; preserved all anchors.")
            for cs in rec.get("conflicting_sources") or []:
                claims.append({
                    "kind": "source_conflict_evidence",
                    "evidence": cs.get("quote", ""),
                    "source_id": block_source_lookup.get(str(cs.get("paragraph_id")), "corpus"),
                    "location": str(cs.get("paragraph_id")),
                    "field_id": fid,
                    "scalar": cs.get("scalar"),
                    "units": cs.get("units"),
                })
        elif st == "not_reported":
            not_reported_cnt += 1
            uncertainties.append(f"Field `{fid}` not reported in supplied materials (scalar kept null, never 0).")
        else:
            failed_cnt += 1
            uncertainties.append(f"Field `{fid}` extraction failed: {rec.get('reason', 'unknown error')}.")

    if input_errors:
        contract_status = "input_error"
    elif not target_fields or reported_cnt == 0 and conflict_cnt == 0:
        contract_status = "insufficient_evidence"
    elif not_reported_cnt == 0 and conflict_cnt == 0 and failed_cnt == 0:
        contract_status = "answered"
    else:
        contract_status = "partial"

    harbor_status = "ok" if (contract_status == "answered" and not input_errors) else "partial_with_gaps"

    (output_dir / "extracted_fields.json").write_text(
        json.dumps({"case_id": case_id, "contract_status": contract_status, "fields": fields_out}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "extracted_fields_report.md").write_text(
        _render_report_md(case_id, contract_status, fields_out, uncertainties), encoding="utf-8"
    )

    sources_seen = sorted({str(b.get("source_file")) for b in blocks} | ({"target-fields.json"} if found_tf else set()) | ({"case.json"} if case_path.is_file() else set()))

    result_payload: dict[str, Any] = {
        "case_id": case_id,
        "status": harbor_status,
        "contract_status": contract_status,
        "sources": sources_seen,
        "artifacts": ["result.json", "extracted_fields.json", "extracted_fields_report.md"],
        "result": {
            "target_fields_count": len(target_fields),
            "fields": fields_out,
        },
        "claims": claims,
        "uncertainties": uncertainties,
        "uncertainty": "; ".join(uncertainties) if uncertainties else "All requested fields grounded to exact verbatim source quotes.",
        "metrics": {
            "target_fields_count": len(target_fields),
            "reported_fields_count": reported_cnt,
            "not_reported_fields_count": not_reported_cnt,
            "source_conflict_fields_count": conflict_cnt,
            "extraction_failed_fields_count": failed_cnt,
            "source_blocks_indexed": len(blocks),
        },
        "metric_provenance": {
            "target_fields_count": "target_fields_spec_loader",
            "reported_fields_count": "sentence_and_table_cell_binder",
            "not_reported_fields_count": "zero_default_zero_gate",
            "source_conflict_fields_count": "same_condition_cross_source_conflict_detector",
            "extraction_failed_fields_count": "field_extraction_engine",
            "source_blocks_indexed": "prose_and_table_cell_indexer",
        },
        "findings": [
            f"Processed {len(target_fields)} target field(s) across {len(blocks)} indexed prose/table-cell blocks.",
            f"Field states: reported={reported_cnt}, not_reported={not_reported_cnt}, source_conflict={conflict_cnt}, extraction_failed={failed_cnt}.",
        ],
    }
    if validation_report is not None:
        result_payload["validation"] = validation_report

    (output_dir / "result.json").write_text(
        json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    append_audit_entry(
        audit_file,
        phase="extract",
        action="field_extraction_engine",
        actor="field_extraction_engine.py",
        output=result_payload["metrics"],
        decision=contract_status,
        record_id=case_id,
    )
    return result_payload, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Literature Conclusion & Data Extraction Engine (challenge-105b70ee)")
    parser.add_argument("--input", default="/input", help="Input directory")
    parser.add_argument("--output", default="/app/output", help="Output directory")
    parser.add_argument("--work", default="/app/work", help="Working directory")
    parser.add_argument("--validate-fields", default="", help="Draft JSON of extracted fields to strictly validate and promote")
    args = parser.parse_args()

    paths = preflight_probe.resolve_default_paths(input_dir=args.input, output_dir=args.output, work_dir=args.work)
    val_p = Path(args.validate_fields) if args.validate_fields else None
    _, rc = run_pipeline(paths["input_dir"], paths["output_dir"], paths["work_dir"], validate_fields_path=val_p)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
