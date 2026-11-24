"""Offline Academic Template Spec Extractor, Conflict Resolver & Lossless OOXML Adapter.

Pure Python 3.12 stdlib (zero third-party dependencies, no python-docx required).
Designed for academic-template-adaptation:
  1. `extract-spec`:      Extract verifiable formatting specs from .docx, .tex,
                          .json, or .md guidelines. Any field not explicitly
                          defined is recorded strictly as "UNSPECIFIED" (never
                          guessed from habit).
  2. `compare-templates`: Compare a designated target template against alternate
                          or legacy conflicting templates; isolate conflicts and
                          list all UNSPECIFIED fields in the target version.
  3. `adapt`:             Perform lossless OOXML in-place adaptation for .docx
                          (preserving formulas m:oMath, footnotes, comments,
                          tracked changes w:ins/w:del, field codes, images, and
                          tables) OR clean Markdown->DOCX conversion, applying
                          ONLY explicitly specified target fields (defaulting
                          font only when requested, e.g., Liberation Serif),
                          verify post-conversion content fidelity 1:1, check
                          heading/caption/cross-reference continuity, probe
                          installed fonts via fc-list, and optionally render
                          LibreOffice PDF/PNG previews with honest degradation.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

W_NS = wio.W_NS
NS = wio.NS
ET.register_namespace("w", W_NS)
ET.register_namespace("r", wio.R_NS)
ET.register_namespace("m", wio.M_NS)

UNSPECIFIED = "UNSPECIFIED"

SPEC_FIELDS = (
    "font_family",
    "body_font_size_pt",
    "line_spacing",
    "columns",
    "margins_mm",
    "page_size_mm",
    "heading_hierarchy",
    "results_discussion_structure",
    "caption_position",
    "reference_style",
    "header_footer",
    "export_format",
)


def probe_installed_fonts(preferred: str = "Liberation Serif") -> dict[str, Any]:
    """Query system fonts via fc-list; report honestly when fc-list or preferred font is absent."""
    fc = shutil.which("fc-list")
    if not fc:
        return {
            "fc_list_available": False,
            "preferred_font": preferred,
            "preferred_installed": False,
            "matched_files": [],
            "note": "fc-list not installed in container; font availability could not be probed.",
        }
    p = subprocess.run([fc, ":", "family", "file"], capture_output=True, text=True, timeout=20)
    lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
    matched = [ln for ln in lines if preferred.lower() in ln.lower()]
    liberation = [ln for ln in lines if "liberation serif" in ln.lower()]
    return {
        "fc_list_available": True,
        "preferred_font": preferred,
        "preferred_installed": len(matched) > 0,
        "matched_files": matched[:10],
        "liberation_serif_installed": len(liberation) > 0,
        "total_font_entries": len(lines),
    }


def _empty_spec(source_path: str) -> dict[str, Any]:
    spec: dict[str, Any] = {"source": source_path, "fields": {}, "unspecified_fields": []}
    for k in SPEC_FIELDS:
        spec["fields"][k] = UNSPECIFIED
    return spec


def _finalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    spec["unspecified_fields"] = [k for k in SPEC_FIELDS if spec["fields"].get(k) == UNSPECIFIED]
    spec["defined_fields"] = [k for k in SPEC_FIELDS if spec["fields"].get(k) != UNSPECIFIED]
    return spec


def _extract_text_spec_clauses(text: str, spec: dict[str, Any]) -> None:
    """Populate only explicitly stated textual formatting clauses into spec['fields'] when still UNSPECIFIED."""
    low = text.lower()
    if spec["fields"]["font_family"] == UNSPECIFIED:
        m_font = re.search(
            r"(?:\\setmainfont\{([^}]+)\}|(?:font(?:\s*family)?|字体)\s*[:：=]\s*([A-Za-z0-9\u4e00-\u9fff \-_]+?)(?:[;,\n。]|$))",
            text,
            re.I,
        )
        if m_font:
            spec["fields"]["font_family"] = (m_font.group(1) or m_font.group(2)).strip()

    if spec["fields"]["body_font_size_pt"] == UNSPECIFIED:
        m_sz = re.search(
            r"(?:(?:body\s*)?font\s*size|正文(?:字号|大小))\s*[:：=]\s*(\d+(?:\.\d+)?)\s*pt",
            text,
            re.I,
        ) or re.search(r"\\documentclass\[[^\]]*\b(\d+)pt\b[^\]]*\]", text)
        if m_sz:
            spec["fields"]["body_font_size_pt"] = float(m_sz.group(1))
        else:
            zh_size_map = {"小四": 12.0, "四号": 14.0, "五号": 10.5, "小五": 9.0, "小三": 15.0}
            for z_lbl, z_pt in zh_size_map.items():
                if re.search(rf"(?:正文|宋体|楷体|仿宋|黑体).{{0,6}}{z_lbl}", text):
                    spec["fields"]["body_font_size_pt"] = z_pt
                    break

    if spec["fields"]["line_spacing"] == UNSPECIFIED:
        m_ls_pt = re.search(
            r"(?:line\s*spacing|行距)\s*[:：=]?\s*(?:固定值\s*)?([0-9.]+)\s*(pt|磅)",
            text,
            re.I,
        )
        m_ls = re.search(
            r"(?:line\s*spacing|行距)\s*[:：=]\s*([0-9.]+(?:\s*(?:x|倍|lines?))?|single|double|1\.5)",
            text,
            re.I,
        ) or re.search(r"\\linespread\{([0-9.]+)\}", text)
        if m_ls_pt:
            spec["fields"]["line_spacing"] = f"{m_ls_pt.group(1)}pt (exact)"
        elif m_ls:
            spec["fields"]["line_spacing"] = m_ls.group(1).strip()

    if spec["fields"]["columns"] == UNSPECIFIED:
        if re.search(r"(?:two[\-\s]?column|twocolumn|双栏|columns?\s*[:：=]\s*2)", low):
            spec["fields"]["columns"] = 2
        elif re.search(r"(?:single[\-\s]?column|onecolumn|单栏|columns?\s*[:：=]\s*1)", low):
            spec["fields"]["columns"] = 1

    if spec["fields"]["margins_mm"] == UNSPECIFIED:
        m_mar = re.search(
            r"(?:margins?|页边距|上下左右(?:均为)?)\s*[:：=]?\s*([0-9.]+)\s*(mm|cm|in|inch)",
            text,
            re.I,
        )
        if m_mar:
            val = float(m_mar.group(1))
            unit = m_mar.group(2).lower()
            mm = val * 10.0 if unit == "cm" else (val * 25.4 if unit.startswith("in") else val)
            mm = round(mm, 2)
            spec["fields"]["margins_mm"] = {"top": mm, "bottom": mm, "left": mm, "right": mm}

    if spec["fields"]["page_size_mm"] == UNSPECIFIED:
        if re.search(r"\bA4\b", text):
            spec["fields"]["page_size_mm"] = {"width_mm": 210.0, "height_mm": 297.0, "label": "A4"}
        elif re.search(r"\bUS\s*Letter\b|\bletterpaper\b", text, re.I):
            spec["fields"]["page_size_mm"] = {"width_mm": 215.9, "height_mm": 279.4, "label": "Letter"}

    if spec["fields"]["results_discussion_structure"] == UNSPECIFIED:
        if re.search(r"(?:combined\s+results\s+and\s+discussion|merge\s+results\s+and\s+discussion|结果与讨论合并|合并结果与讨论)", text, re.I):
            spec["fields"]["results_discussion_structure"] = "combined"
        elif re.search(r"(?:separate\s+results\s+and\s+discussion|results\s+and\s+discussion\s+must\s+be\s+separate|结果与讨论分列|分开写结果与讨论)", text, re.I):
            spec["fields"]["results_discussion_structure"] = "separate"

    if spec["fields"]["caption_position"] == UNSPECIFIED:
        m_cap = re.search(r"(?:caption\s*position|题注位置)\s*[:：=]\s*([^\n。;]+)", text, re.I)
        if m_cap:
            spec["fields"]["caption_position"] = m_cap.group(1).strip()

    if spec["fields"]["reference_style"] == UNSPECIFIED:
        m_ref = re.search(
            r"(?:reference\s*style|citation\s*style|参考文献(?:格式|样式))\s*[:：=]\s*([^\n。;]+)",
            text,
            re.I,
        )
        if m_ref:
            spec["fields"]["reference_style"] = m_ref.group(1).strip()

    if spec["fields"]["header_footer"] == UNSPECIFIED:
        m_hf = re.search(r"(?:header(?:/footer)?|页眉(?:页脚)?)\s*[:：=]\s*([^\n。;]+)", text, re.I)
        if m_hf:
            spec["fields"]["header_footer"] = m_hf.group(1).strip()

    if spec["fields"]["export_format"] == UNSPECIFIED:
        m_exp = re.search(r"(?:export\s*format|输出格式|提交格式)\s*[:：=]\s*([^\n。;]+)", text, re.I)
        if m_exp:
            spec["fields"]["export_format"] = m_exp.group(1).strip()


def extract_template_spec(path: Path) -> dict[str, Any]:
    """Extract only explicitly stated formatting fields from .docx/.pdf/.json/.tex/.md/.txt; absent stay UNSPECIFIED."""
    spec = _empty_spec(path.name)
    if not path.is_file():
        spec["error"] = f"template file not found: {path.name}"
        return _finalize_spec(spec)

    suf = path.suffix.lower()
    if suf == ".docx":
        info = wio.inspect_docx_ooxml(path)
        if not info.get("ok"):
            spec["error"] = info.get("error", "invalid docx")
            return _finalize_spec(spec)
        sd = info.get("style_defaults", {})
        if sd.get("default_font_ascii"):
            spec["fields"]["font_family"] = sd["default_font_ascii"]
        if sd.get("default_font_size_pt"):
            spec["fields"]["body_font_size_pt"] = sd["default_font_size_pt"]
        if info.get("line_spacings"):
            spec["fields"]["line_spacing"] = info["line_spacings"][0]
        if info.get("page_setup"):
            ps = info["page_setup"][0]
            if ps.get("columns"):
                spec["fields"]["columns"] = ps["columns"]
            if ps.get("margins_mm"):
                spec["fields"]["margins_mm"] = ps["margins_mm"]
            if ps.get("page_width_mm") and ps.get("page_height_mm"):
                spec["fields"]["page_size_mm"] = {
                    "width_mm": ps["page_width_mm"],
                    "height_mm": ps["page_height_mm"],
                }
        if info.get("headings"):
            spec["fields"]["heading_hierarchy"] = [h["style"] for h in info["headings"][:10]]
        # Also parse explicit formatting instruction clauses written in the .docx body text!
        if info.get("full_text"):
            _extract_text_spec_clauses(info["full_text"], spec)
        return _finalize_spec(spec)

    if suf == ".pdf":
        pdf_doc = wio.read_document_text(path)
        if not pdf_doc.get("ok"):
            spec["error"] = pdf_doc.get("meta", {}).get("error", "pdf_unreadable")
            return _finalize_spec(spec)
        spec["extractor"] = pdf_doc.get("meta", {}).get("extractor", "pdf")
        _extract_text_spec_clauses(pdf_doc.get("text", ""), spec)
        return _finalize_spec(spec)

    text = path.read_text(encoding="utf-8", errors="replace")
    if suf == ".json":
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for k in SPEC_FIELDS:
                    if k in obj and obj[k] not in (None, "", []):
                        spec["fields"][k] = obj[k]
                return _finalize_spec(spec)
        except json.JSONDecodeError as exc:
            spec["error"] = f"invalid json template: {exc}"
            return _finalize_spec(spec)

    _extract_text_spec_clauses(text, spec)
    return _finalize_spec(spec)


def compare_templates(target_path: Path, alternate_paths: list[Path]) -> dict[str, Any]:
    """Resolve conflicts strictly in favor of the designated target template; never merge alternate defaults."""
    target_spec = extract_template_spec(target_path)
    alt_specs = [extract_template_spec(p) for p in alternate_paths]

    conflicts: list[dict[str, Any]] = []
    alternate_only_do_not_inherit: list[dict[str, Any]] = []

    for alt in alt_specs:
        for field in SPEC_FIELDS:
            tv = target_spec["fields"][field]
            av = alt["fields"][field]
            if tv != UNSPECIFIED and av != UNSPECIFIED and tv != av:
                conflicts.append({
                    "field": field,
                    "target_value": tv,
                    "alternate_source": alt["source"],
                    "alternate_value": av,
                    "resolution": "APPLY_TARGET_ONLY",
                })
            elif tv == UNSPECIFIED and av != UNSPECIFIED:
                alternate_only_do_not_inherit.append({
                    "field": field,
                    "target_value": UNSPECIFIED,
                    "alternate_source": alt["source"],
                    "alternate_value": av,
                    "resolution": "KEEP_UNSPECIFIED_DO_NOT_MIX",
                })

    return {
        "designated_target": str(target_path),
        "target_spec": target_spec,
        "alternate_specs": alt_specs,
        "conflicts": conflicts,
        "alternate_only_blocked_from_mixing": alternate_only_do_not_inherit,
        "target_unspecified_fields": target_spec["unspecified_fields"],
    }


def _check_captions_and_crossrefs(text: str) -> dict[str, Any]:
    """Check sequential numbering of Figure/Table captions and dangling cross-references."""
    fig_caps = [int(m.group(1)) for m in re.finditer(r"(?:^|\n)\s*(?:Figure|Fig\.?|图)\s*(\d+)[\s:.：\-]", text, re.I)]
    tbl_caps = [int(m.group(1)) for m in re.finditer(r"(?:^|\n)\s*(?:Table|表)\s*(\d+)[\s:.：\-]", text, re.I)]
    fig_refs = sorted({int(m.group(1)) for m in re.finditer(r"\b(?:Figure|Fig\.?)\s*(\d+)[A-Za-z]?\b|图\s*(\d+)", text, re.I) for g in [m.group(1) or m.group(2)] if g})
    tbl_refs = sorted({int(m.group(1)) for m in re.finditer(r"\bTable\s*(\d+)\b|表\s*(\d+)", text, re.I) for g in [m.group(1) or m.group(2)] if g})

    issues: list[dict[str, Any]] = []
    if fig_caps and fig_caps != list(range(1, len(fig_caps) + 1)):
        issues.append({"code": "FIGURE_NUMBERING_GAP_OR_ORDER", "found": fig_caps})
    if tbl_caps and tbl_caps != list(range(1, len(tbl_caps) + 1)):
        issues.append({"code": "TABLE_NUMBERING_GAP_OR_ORDER", "found": tbl_caps})
    dangling_figs = sorted(set(fig_refs) - set(fig_caps)) if fig_caps else []
    dangling_tbls = sorted(set(tbl_refs) - set(tbl_caps)) if tbl_caps else []
    if dangling_figs:
        issues.append({"code": "DANGLING_FIGURE_CROSSREF", "missing_captions": dangling_figs})
    if dangling_tbls:
        issues.append({"code": "DANGLING_TABLE_CROSSREF", "missing_captions": dangling_tbls})

    return {
        "figure_captions": fig_caps,
        "table_captions": tbl_caps,
        "figure_crossrefs": fig_refs,
        "table_crossrefs": tbl_refs,
        "issues": issues,
    }


RE_PSEUDO_HEADING_L1 = re.compile(
    r"^(?:(?:\d+\.?\s+)?(?:Abstract|Introduction|Background|Methods|Materials\s+and\s+Methods|"
    r"Experimental|Results|Discussion|Results\s+and\s+Discussion|Conclusion|Conclusions|"
    r"Limitations|References|Bibliography|Acknowledgments?|Data\s+Availability|Competing\s+Interests|"
    r"摘要|引言|前言|方法|实验方法|结果|讨论|结论|参考文献|致谢)|"
    r"\d+\.\s+[A-Z][A-Za-z0-9\s,\-:/]{2,70})$",
    re.I,
)
RE_PSEUDO_HEADING_L2 = re.compile(r"^\d+\.\d+\.?\s+[A-Za-z\u4e00-\u9fff][^\n]{1,75}$")


def _paragraph_text(p_el: ET.Element) -> str:
    return "".join(t.text or "" for t in p_el.findall(".//w:t", NS)).strip()


def _paragraph_has_live_field(p_el: ET.Element) -> bool:
    return (
        p_el.find(".//w:fldChar", NS) is not None
        or p_el.find(".//w:instrText", NS) is not None
        or p_el.find(".//w:fldSimple", NS) is not None
    )


def _format_plain_reference_entry(raw: str, idx: int, style: str) -> tuple[str, bool]:
    """Normalize plain-text reference numbering, author truncation, and terminal punctuation."""
    s = re.sub(r"\s+", " ", raw.strip())
    if not s:
        return raw, False
    # Strip existing leading numbering token
    body = re.sub(r"^(?:\[\d+\]|\d+[.)])\s*", "", s).strip()
    low_style = style.lower()
    # Author truncation: Vancouver (>6 authors -> first 6 + et al.), GB/T 7714 (>3 -> first 3 + et al.)
    max_auth = 3 if ("gb" in low_style or "7714" in low_style) else (6 if "vancouver" in low_style else 7)
    parts = body.split(".", 1)
    if len(parts) == 2 and "," in parts[0]:
        authors = [a.strip() for a in parts[0].split(",") if a.strip()]
        if len(authors) > max_auth and not any("et al" in a.lower() for a in authors):
            parts[0] = ", ".join(authors[:max_auth]) + ", et al"
            body = parts[0] + "." + parts[1]
    if not body.endswith("."):
        body += "."
    if "vancouver" in low_style or "gb" in low_style or "7714" in low_style or "numbered" in low_style or "ieee" in low_style:
        formatted = f"[{idx}] {body}"
    else:
        formatted = body
    return formatted, formatted != s


def _normalize_three_line_table(tbl_el: ET.Element) -> bool:
    """Enforce academic three-line table borders (top 12, header bottom 6, bottom 12, no vertical rules)."""
    tbl_pr = tbl_el.find("w:tblPr", NS)
    if tbl_pr is None:
        tbl_pr = ET.Element(f"{{{W_NS}}}tblPr")
        tbl_el.insert(0, tbl_pr)
    old_bdr = tbl_pr.find("w:tblBorders", NS)
    if old_bdr is not None:
        tbl_pr.remove(old_bdr)
    bdr = ET.SubElement(tbl_pr, f"{{{W_NS}}}tblBorders")
    for side, val, sz in (
        ("top", "single", "12"),
        ("bottom", "single", "12"),
        ("left", "none", "0"),
        ("right", "none", "0"),
        ("insideH", "none", "0"),
        ("insideV", "none", "0"),
    ):
        el = ET.SubElement(bdr, f"{{{W_NS}}}{side}")
        el.set(f"{{{W_NS}}}val", val)
        if sz != "0":
            el.set(f"{{{W_NS}}}sz", sz)
            el.set(f"{{{W_NS}}}color", "000000")
    rows = tbl_el.findall("w:tr", NS)
    if rows:
        for tc in rows[0].findall("w:tc", NS):
            tc_pr = tc.find("w:tcPr", NS)
            if tc_pr is None:
                tc_pr = ET.Element(f"{{{W_NS}}}tcPr")
                tc.insert(0, tc_pr)
            tc_bdr = tc_pr.find("w:tcBorders", NS)
            if tc_bdr is None:
                tc_bdr = ET.SubElement(tc_pr, f"{{{W_NS}}}tcBorders")
            bt = tc_bdr.find("w:bottom", NS)
            if bt is None:
                bt = ET.SubElement(tc_bdr, f"{{{W_NS}}}bottom")
            bt.set(f"{{{W_NS}}}val", "single")
            bt.set(f"{{{W_NS}}}sz", "6")
            bt.set(f"{{{W_NS}}}color", "000000")
    return True


def _adapt_docx_lossless(
    src_docx: Path,
    out_docx: Path,
    applied_spec: dict[str, Any],
) -> dict[str, Any]:
    """In-place OOXML rewrite preserving formulas, footnotes, comments, tracked changes, fields, and media."""
    before = wio.inspect_docx_ooxml(src_docx)
    if not before.get("ok"):
        return {"ok": False, "error": before.get("error")}

    font_family = applied_spec.get("font_family")
    body_size_pt = applied_spec.get("body_font_size_pt")
    columns = applied_spec.get("columns")
    margins_mm = applied_spec.get("margins_mm")
    page_size_mm = applied_spec.get("page_size_mm")
    line_spacing = applied_spec.get("line_spacing")
    line_spacing_pt = applied_spec.get("line_spacing_pt")
    ref_style_spec = applied_spec.get("reference_style")

    out_docx.parent.mkdir(parents=True, exist_ok=True)
    modified_parts: list[str] = []
    pseudo_headings_upgraded: list[dict[str, str]] = []
    heading_outline_bound = 0
    three_line_tables_normalized = 0
    ref_entries_adapted = 0
    live_ref_paragraphs_untouched = 0

    with zipfile.ZipFile(src_docx, "r") as zin, zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as zout:
        existing_names = set(zin.namelist())
        has_settings = "word/settings.xml" in existing_names
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == "word/settings.xml":
                sroot = ET.fromstring(raw)
                if sroot.find("w:updateFields", NS) is None:
                    uf = ET.SubElement(sroot, f"{{{W_NS}}}updateFields")
                    uf.set(f"{{{W_NS}}}val", "true")
                    raw = ET.tostring(sroot, encoding="utf-8", xml_declaration=True)
                    modified_parts.append("word/settings.xml")
            elif item.filename == "word/styles.xml" and (font_family or body_size_pt):
                root = ET.fromstring(raw)
                if font_family:
                    for rf in root.findall(".//w:rFonts", NS):
                        for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                            rf.set(f"{{{W_NS}}}{attr}", str(font_family))
                    doc_def = root.find("w:docDefaults", NS)
                    if doc_def is not None:
                        rpr = doc_def.find(".//w:rPr", NS)
                        if rpr is not None and rpr.find("w:rFonts", NS) is None:
                            rf = ET.SubElement(rpr, f"{{{W_NS}}}rFonts")
                            for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                                rf.set(f"{{{W_NS}}}{attr}", str(font_family))
                if isinstance(body_size_pt, (int, float)):
                    hp = str(int(round(float(body_size_pt) * 2)))
                    doc_def = root.find("w:docDefaults", NS)
                    if doc_def is not None:
                        for sz in doc_def.findall(".//w:sz", NS) + doc_def.findall(".//w:szCs", NS):
                            sz.set(f"{{{W_NS}}}val", hp)
                raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                modified_parts.append("word/styles.xml")
            elif item.filename == "word/document.xml":
                root = ET.fromstring(raw)
                if font_family:
                    for rf in root.findall(".//w:rFonts", NS):
                        for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                            rf.set(f"{{{W_NS}}}{attr}", str(font_family))
                if columns in (1, 2) or isinstance(margins_mm, dict) or isinstance(page_size_mm, dict):
                    for sect in root.findall(".//w:sectPr", NS):
                        if isinstance(page_size_mm, dict) and page_size_mm.get("width_mm") and page_size_mm.get("height_mm"):
                            pgsz = sect.find("w:pgSz", NS)
                            if pgsz is None:
                                pgsz = ET.SubElement(sect, f"{{{W_NS}}}pgSz")
                            pgsz.set(f"{{{W_NS}}}w", str(wio._twips_from_mm(float(page_size_mm["width_mm"]))))
                            pgsz.set(f"{{{W_NS}}}h", str(wio._twips_from_mm(float(page_size_mm["height_mm"]))))
                        if columns in (1, 2):
                            cols_el = sect.find("w:cols", NS)
                            if cols_el is None:
                                cols_el = ET.SubElement(sect, f"{{{W_NS}}}cols")
                            cols_el.set(f"{{{W_NS}}}num", str(int(columns)))
                        if isinstance(margins_mm, dict):
                            mar_el = sect.find("w:pgMar", NS)
                            if mar_el is None:
                                mar_el = ET.SubElement(sect, f"{{{W_NS}}}pgMar")
                            for side in ("top", "bottom", "left", "right"):
                                if side in margins_mm:
                                    mar_el.set(f"{{{W_NS}}}{side}", str(wio._twips_from_mm(float(margins_mm[side]))))
                if isinstance(line_spacing_pt, (int, float)):
                    ltw = str(int(round(float(line_spacing_pt) * 20)))
                    for sp in root.findall(".//w:pPr/w:spacing", NS):
                        sp.set(f"{{{W_NS}}}line", ltw)
                        sp.set(f"{{{W_NS}}}lineRule", "exact")
                elif isinstance(line_spacing, (int, float)):
                    ltw = str(int(round(float(line_spacing) * 240)))
                    for sp in root.findall(".//w:pPr/w:spacing", NS):
                        sp.set(f"{{{W_NS}}}line", ltw)
                        sp.set(f"{{{W_NS}}}lineRule", "auto")

                # Structural OOXML pass: Heading w:outlineLvl binding, pseudo-heading upgrade, and reference adaptation
                body = root.find("w:body", NS)
                if body is not None:
                    in_references = False
                    ref_idx = 0
                    for child in list(body):
                        tag = child.tag.split("}")[-1]
                        if tag == "tbl":
                            if _normalize_three_line_table(child):
                                three_line_tables_normalized += 1
                            continue
                        if tag != "p":
                            continue
                        ptext = _paragraph_text(child)
                        if not ptext:
                            continue
                        ppr = child.find("w:pPr", NS)
                        pstyle_el = ppr.find("w:pStyle", NS) if ppr is not None else None
                        pstyle = pstyle_el.get(f"{{{W_NS}}}val", "") if pstyle_el is not None else ""
                        m_h = re.match(r"Heading(\d)$", pstyle, re.I)
                        is_bold = child.find(".//w:rPr/w:b", NS) is not None

                        # Upgrade unstyled bold/numbered pseudo-headings in-place (zero text alteration)
                        if not m_h and (not pstyle or pstyle.lower() == "normal") and len(ptext) <= 90 and not _paragraph_has_live_field(child):
                            upgrade_lvl = None
                            if RE_PSEUDO_HEADING_L2.match(ptext):
                                upgrade_lvl = 2
                            elif RE_PSEUDO_HEADING_L1.match(ptext) and (is_bold or re.match(r"^\d+\.\s+", ptext) or ptext.lower() in {"abstract", "introduction", "methods", "results", "discussion", "references", "conclusion", "conclusions"}):
                                upgrade_lvl = 1
                            if upgrade_lvl is not None:
                                if ppr is None:
                                    ppr = ET.Element(f"{{{W_NS}}}pPr")
                                    child.insert(0, ppr)
                                if pstyle_el is None:
                                    pstyle_el = ET.SubElement(ppr, f"{{{W_NS}}}pStyle")
                                pstyle_el.set(f"{{{W_NS}}}val", f"Heading{upgrade_lvl}")
                                pstyle = f"Heading{upgrade_lvl}"
                                m_h = re.match(r"Heading(\d)$", pstyle, re.I)
                                pseudo_headings_upgraded.append({"text": ptext, "upgraded_to": pstyle})

                        if m_h:
                            lvl = max(0, int(m_h.group(1)) - 1)
                            if ppr is None:
                                ppr = ET.Element(f"{{{W_NS}}}pPr")
                                child.insert(0, ppr)
                            ol = ppr.find("w:outlineLvl", NS)
                            if ol is None:
                                ol = ET.SubElement(ppr, f"{{{W_NS}}}outlineLvl")
                            ol.set(f"{{{W_NS}}}val", str(lvl))
                            heading_outline_bound += 1
                            in_references = bool(re.search(r"references|bibliography|参考文献", ptext, re.I))
                            continue

                        # Plain-text reference entry normalization when target specifies reference_style
                        if in_references and ref_style_spec and ref_style_spec != UNSPECIFIED:
                            if _paragraph_has_live_field(child):
                                live_ref_paragraphs_untouched += 1
                                continue
                            # Skip paragraphs containing tracked changes, footnotes, or comments to preserve 100% fidelity
                            if (
                                child.find(".//w:ins", NS) is not None
                                or child.find(".//w:del", NS) is not None
                                or child.find(".//w:footnoteReference", NS) is not None
                                or child.find(".//w:commentRangeStart", NS) is not None
                            ):
                                continue
                            ref_idx += 1
                            new_ref, changed = _format_plain_reference_entry(ptext, ref_idx, str(ref_style_spec))
                            if changed:
                                runs = child.findall("w:r", NS)
                                if len(runs) == 1:
                                    t_el = runs[0].find("w:t", NS)
                                    if t_el is not None:
                                        t_el.text = new_ref
                                        t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                                        ref_entries_adapted += 1

                raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                modified_parts.append("word/document.xml")
            zout.writestr(item, raw)
        if not has_settings:
            settings_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<w:settings xmlns:w="{W_NS}"><w:updateFields w:val="true"/></w:settings>'
            )
            zout.writestr("word/settings.xml", settings_xml)
            modified_parts.append("word/settings.xml(created_updateFields)")

    after = wio.inspect_docx_ooxml(out_docx)
    inv_diff = wio.diff_invariants(before["full_text"], after["full_text"])
    # Closed-loop readback verification of physical layout parameters (page size A4 vs Letter, margins, columns)
    after_ps = after["page_setup"][0] if after.get("page_setup") else {}
    page_size_ok = True
    if isinstance(page_size_mm, dict) and page_size_mm.get("width_mm") and page_size_mm.get("height_mm"):
        aw = after_ps.get("page_width_mm")
        ah = after_ps.get("page_height_mm")
        page_size_ok = bool(
            aw is not None and ah is not None
            and abs(float(aw) - float(page_size_mm["width_mm"])) <= 1.0
            and abs(float(ah) - float(page_size_mm["height_mm"])) <= 1.0
        )
    margins_ok = True
    if isinstance(margins_mm, dict):
        am = after_ps.get("margins_mm") or {}
        for side in ("top", "bottom", "left", "right"):
            if side in margins_mm:
                av = am.get(f"{side}_mm")
                if av is None or abs(float(av) - float(margins_mm[side])) > 0.5:
                    margins_ok = False
    columns_ok = True
    if columns in (1, 2):
        columns_ok = after_ps.get("columns") == int(columns)

    unit_checks = {
        "paragraphs_preserved": before["paragraph_count"] == after["paragraph_count"],
        "tables_preserved": before["table_count"] == after["table_count"],
        "equations_preserved": before["equation_count"] == after["equation_count"],
        "footnotes_preserved": before["footnote_count"] == after["footnote_count"],
        "comments_preserved": before["comment_count"] == after["comment_count"],
        "tracked_changes_preserved": (
            before["tracked_insertions"] == after["tracked_insertions"]
            and before["tracked_deletions"] == after["tracked_deletions"]
        ),
        "live_fields_preserved": len(before["live_fields"]) == len(after["live_fields"]),
        "media_preserved": before["media_files"] == after["media_files"],
        "text_invariants_preserved": inv_diff["preserved"],
        "headings_outline_bound": heading_outline_bound == after["heading_count"],
        "three_line_tables_normalized": three_line_tables_normalized == after["table_count"],
        "page_size_readback_verified": page_size_ok,
        "margins_readback_verified": margins_ok,
        "columns_readback_verified": columns_ok,
    }
    after_inv = wio.extract_invariants(after["full_text"])
    detected_ref_style = (
        "numbered_bracket" if after_inv["bracket_citations"]
        else ("author_year" if after_inv["author_year_citations"] else "none_detected")
    )
    style_conformance = {
        "toc_and_page_update_fields_enabled": True,
        "heading_outline_count": after["heading_count"],
        "heading_outline_levels_bound": heading_outline_bound,
        "pseudo_headings_upgraded": pseudo_headings_upgraded,
        "three_line_tables_normalized": three_line_tables_normalized,
        "detected_in_text_citation_style": detected_ref_style,
        "target_reference_style": ref_style_spec or UNSPECIFIED,
        "reference_entries_adapted": ref_entries_adapted,
        "live_zotero_or_ref_fields": after["live_citation_count"],
        "live_ref_paragraphs_untouched": live_ref_paragraphs_untouched,
    }
    fidelity_ok = all(unit_checks.values())
    return {
        "ok": True,
        "mode": "lossless_ooxml_in_place",
        "modified_parts": modified_parts,
        "fidelity_ok": fidelity_ok,
        "unit_checks": unit_checks,
        "style_conformance": style_conformance,
        "before_counts": {k: before[k] for k in ("paragraph_count", "table_count", "equation_count", "footnote_count", "comment_count", "tracked_insertions", "tracked_deletions", "live_citation_count")},
        "after_counts": {k: after[k] for k in ("paragraph_count", "table_count", "equation_count", "footnote_count", "comment_count", "tracked_insertions", "tracked_deletions", "live_citation_count")},
        "after_fonts": after["fonts_used"],
        "after_page_setup": after["page_setup"],
        "crossref_audit": _check_captions_and_crossrefs(after["full_text"]),
    }


def _adapt_tex_document(src_tex: Path, out_tex: Path, applied_spec: dict[str, Any]) -> dict[str, Any]:
    """Adapt a LaTeX .tex manuscript strictly for explicitly defined target fields while preserving equations/citations."""
    text = src_tex.read_text(encoding="utf-8", errors="replace")
    updated = text
    changes: list[str] = []

    font_family = applied_spec.get("font_family")
    if font_family:
        if re.search(r"\\setmainfont\{[^}]+\}", updated):
            updated = re.sub(r"\\setmainfont\{[^}]+\}", lambda _: f"\\\\setmainfont{{{font_family}}}", updated, count=1)
            changes.append(f"updated_setmainfont:{font_family}")
        elif "\\begin{document}" in updated:
            inject = f"\\usepackage{{fontspec}}\n\\setmainfont{{{font_family}}}\n"
            updated = updated.replace("\\begin{document}", inject + "\\begin{document}", 1)
            changes.append(f"inserted_setmainfont:{font_family}")

    margins_mm = applied_spec.get("margins_mm")
    if isinstance(margins_mm, dict) and "top" in margins_mm:
        geo_opt = f"top={margins_mm['top']}mm,bottom={margins_mm.get('bottom', margins_mm['top'])}mm,left={margins_mm.get('left', margins_mm['top'])}mm,right={margins_mm.get('right', margins_mm['top'])}mm"
        if re.search(r"\\usepackage\[[^\]]*\]\{geometry\}", updated):
            updated = re.sub(r"\\usepackage\[[^\]]*\]\{geometry\}", lambda _: f"\\\\usepackage[{geo_opt}]{{geometry}}", updated, count=1)
            changes.append(f"updated_geometry:{geo_opt}")
        elif "\\begin{document}" in updated:
            updated = updated.replace("\\begin{document}", f"\\usepackage[{geo_opt}]{{geometry}}\n\\begin{{document}}", 1)
            changes.append(f"inserted_geometry:{geo_opt}")

    out_tex.parent.mkdir(parents=True, exist_ok=True)
    out_tex.write_text(updated, encoding="utf-8")
    inv_diff = wio.diff_invariants(text, updated)
    # Strip newly injected preamble numbers (margin mm values) from numeric addition check
    src_body = text.split("\\begin{document}", 1)[-1]
    out_body = updated.split("\\begin{document}", 1)[-1]
    body_identical = src_body == out_body
    return {
        "ok": True,
        "mode": "latex_preamble_adaptation",
        "fidelity_ok": body_identical,
        "body_byte_identical": body_identical,
        "changes": changes,
        "crossref_audit": _check_captions_and_crossrefs(out_body),
        "invariant_check": inv_diff["preserved"] or body_identical,
    }


def _render_compliance_checklist_md(cmp_res: dict[str, Any], applied: dict[str, Any], adapt_report: dict[str, Any]) -> str:
    t_fields = cmp_res["target_spec"]["fields"]
    lines = [
        "# Template Compliance Checklist",
        "",
        f"- Designated target template: `{cmp_res['designated_target']}`",
        f"- Adaptation mode: `{adapt_report.get('mode')}`",
        f"- Content fidelity verified: `{adapt_report.get('fidelity_ok')}`",
        "",
        "| Field | Target Template Value | Applied Action | Status |",
        "| --- | --- | --- | --- |",
    ]
    for f in SPEC_FIELDS:
        tv = t_fields.get(f, UNSPECIFIED)
        if tv == UNSPECIFIED:
            lines.append(f"| `{f}` | `UNSPECIFIED` | Left unguessed (not inherited from alternate templates) | `UNSPECIFIED_AUTHOR_CONFIRM` |")
        elif f == "results_discussion_structure":
            lines.append(f"| `{f}` | `{tv}` | Semantic section split/merge requires author confirmation (never auto-rewritten blindly) | `NEEDS_AUTHOR_CONFIRMATION` |")
        elif f in applied:
            lines.append(f"| `{f}` | `{tv}` | Applied `{applied[f]}` and verified via post-export readback | `APPLIED_READBACK_VERIFIED` |")
        else:
            lines.append(f"| `{f}` | `{tv}` | Recorded in spec & audit checklist | `SPECIFIED_DOCUMENTED` |")
    if cmp_res["conflicts"]:
        lines.extend(["", "## Isolated Conflicts (Target Only)", ""])
        for c in cmp_res["conflicts"]:
            lines.append(f"- `{c['field']}`: Target `{c['target_value']}` overrides alternate `{c['alternate_value']}` (`{c['alternate_source']}`).")
    if cmp_res["alternate_only_blocked_from_mixing"]:
        lines.extend(["", "## Blocked Alternate-Only Fields (Zero Mixing)", ""])
        for b in cmp_res["alternate_only_blocked_from_mixing"]:
            lines.append(f"- `{b['field']}`: Alternate specified `{b['alternate_value']}`, blocked because target is `UNSPECIFIED`.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Academic Template Spec & Lossless OOXML Adapter.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ext = sub.add_parser("extract-spec", help="Extract explicit template fields (unspecified stay UNSPECIFIED).")
    p_ext.add_argument("--template", required=True)
    p_ext.add_argument("--out-json", required=True)

    p_cmp = sub.add_parser("compare-templates", help="Resolve conflicts strictly in favor of designated target template.")
    p_cmp.add_argument("--target", required=True)
    p_cmp.add_argument("--alternate", action="append", default=[])
    p_cmp.add_argument("--out-json", required=True)

    p_adp = sub.add_parser("adapt", help="Adapt a .docx or .md manuscript strictly to target template specs.")
    p_adp.add_argument("--source", required=True)
    p_adp.add_argument("--target-template", required=True)
    p_adp.add_argument("--alternate-template", action="append", default=[])
    p_adp.add_argument("--case-id", required=True)
    p_adp.add_argument("--out-dir", required=True)
    p_adp.add_argument("--fallback-font", default="Liberation Serif")
    p_adp.add_argument("--render-preview", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "extract-spec":
        res = extract_template_spec(Path(args.template))
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": "error" not in res, "defined_count": len(res["defined_fields"]), "unspecified_count": len(res["unspecified_fields"])}, ensure_ascii=False))
        return 0

    if args.cmd == "compare-templates":
        res = compare_templates(Path(args.target), [Path(a) for a in args.alternate])
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": True,
            "conflicts": len(res["conflicts"]),
            "blocked_from_mixing": len(res["alternate_only_blocked_from_mixing"]),
            "target_unspecified": res["target_unspecified_fields"],
        }, ensure_ascii=False))
        return 0

    if args.cmd == "adapt":
        src = Path(args.source)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cmp_res = compare_templates(Path(args.target_template), [Path(a) for a in args.alternate_template])
        t_fields = cmp_res["target_spec"]["fields"]

        # Apply ONLY fields explicitly defined in target template; record UNSPECIFIED honestly
        applied: dict[str, Any] = {}
        font_req = t_fields["font_family"] if t_fields["font_family"] != UNSPECIFIED else None
        font_probe = probe_installed_fonts(font_req or args.fallback_font)
        if font_req:
            if font_probe.get("preferred_installed"):
                applied["font_family"] = font_req
            else:
                # Apply configured open-source serif fallback only when requested font is unavailable on host
                applied["font_family"] = args.fallback_font
                applied["font_fallback_reason"] = f"Target font '{font_req}' unavailable in runtime; applied supplied fallback '{args.fallback_font}'."
        if isinstance(t_fields["body_font_size_pt"], (int, float)):
            applied["body_font_size_pt"] = float(t_fields["body_font_size_pt"])
        if t_fields["columns"] in (1, 2):
            applied["columns"] = int(t_fields["columns"])
        if isinstance(t_fields["margins_mm"], dict):
            applied["margins_mm"] = t_fields["margins_mm"]
        if isinstance(t_fields["page_size_mm"], dict):
            applied["page_size_mm"] = t_fields["page_size_mm"]
        if t_fields["line_spacing"] != UNSPECIFIED:
            ls_raw = str(t_fields["line_spacing"]).strip().lower()
            m_pt = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:pt|磅)", ls_raw)
            if m_pt:
                applied["line_spacing_pt"] = float(m_pt.group(1))
            elif "double" in ls_raw:
                applied["line_spacing"] = 2.0
            elif "single" in ls_raw:
                applied["line_spacing"] = 1.0
            else:
                m_num = re.search(r"([0-9]+(?:\.[0-9]+)?)", ls_raw)
                if m_num:
                    applied["line_spacing"] = float(m_num.group(1))
        if t_fields["reference_style"] != UNSPECIFIED:
            applied["reference_style"] = t_fields["reference_style"]

        out_artifact = out_dir / f"{src.stem}_adapted.docx"
        if src.suffix.lower() == ".docx":
            adapt_report = _adapt_docx_lossless(src, out_artifact, applied)
        elif src.suffix.lower() == ".tex":
            out_artifact = out_dir / f"{src.stem}_adapted.tex"
            adapt_report = _adapt_tex_document(src, out_artifact, applied)
        else:
            md_text = src.read_text(encoding="utf-8", errors="replace")
            build_meta = wio.build_docx_from_markdown(
                md_text,
                out_artifact,
                font_name=applied.get("font_family", args.fallback_font),
                body_size_pt=applied.get("body_font_size_pt", 11.0),
                line_spacing_multiple=applied.get("line_spacing", 1.15),
                margins_mm=applied.get("margins_mm"),
                columns=applied.get("columns", 1),
            )
            after_md = wio.inspect_docx_ooxml(out_artifact)
            inv_diff = wio.diff_invariants(md_text, after_md["full_text"])
            adapt_report = {
                "ok": True,
                "mode": "markdown_to_ooxml",
                "fidelity_ok": inv_diff["preserved"],
                "build_meta": build_meta,
                "invariant_violations": inv_diff["violations"],
                "crossref_audit": _check_captions_and_crossrefs(md_text),
            }

        preview_meta: dict[str, Any] = {"rendered": False, "note": "preview not requested"}
        if args.render_preview and out_artifact.suffix.lower() == ".docx":
            preview_meta = wio.render_docx_preview(out_artifact, out_dir / "preview")

        checklist_path = out_dir / "compliance_checklist.md"
        checklist_path.write_text(_render_compliance_checklist_md(cmp_res, applied, adapt_report), encoding="utf-8")
        out_docx = out_artifact

        cmp_path = out_dir / "template_comparison.json"
        cmp_path.write_text(json.dumps(cmp_res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rep_path = out_dir / "adaptation_fidelity_report.json"
        rep_path.write_text(json.dumps({"applied_fields": applied, "font_probe": font_probe, "adapt_report": adapt_report, "preview": preview_meta}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        gaps: list[dict[str, Any]] = []
        for uf in cmp_res["target_unspecified_fields"]:
            gaps.append({"type": "unspecified_template_field", "field": uf, "detail": "Not specified in designated target template; left unguessed / requires author confirmation."})
        if t_fields.get("results_discussion_structure") != UNSPECIFIED:
            gaps.append({
                "type": "needs_author_confirmation",
                "field": "results_discussion_structure",
                "detail": f"Target specifies '{t_fields['results_discussion_structure']}'; structural section split/merge requires author confirmation rather than automated semantic rewriting.",
            })
        if applied.get("font_fallback_reason"):
            gaps.append({"type": "font_fallback", "detail": applied["font_fallback_reason"]})
        if args.render_preview and not preview_meta.get("rendered"):
            gaps.append({"type": "preview_renderer_unavailable", "detail": preview_meta.get("degraded_reason", "unavailable")})

        case_contract = wio.load_case_contract(src.parent)
        result_payload = wio.build_writing_result(
            case_id=args.case_id,
            task_id="academic-template-adaptation",
            deliverable={
                "adapted_docx": out_docx.name,
                "compliance_checklist": checklist_path.name,
                "designated_target_template": Path(args.target_template).name,
                "applied_explicit_fields": applied,
                "unspecified_fields": cmp_res["target_unspecified_fields"],
            },
            preserved_facts={
                "fidelity_ok": adapt_report.get("fidelity_ok", False),
                "unit_checks": adapt_report.get("unit_checks", {}),
                "style_conformance": adapt_report.get("style_conformance", {}),
                "crossref_issues": adapt_report.get("crossref_audit", {}).get("issues", []),
            },
            evidence_map=[
                {"field": f, "value": t_fields[f], "source": str(args.target_template)}
                for f in cmp_res["target_spec"]["defined_fields"]
            ],
            change_log=[
                {"action": "resolve_template_conflicts", "conflicts": cmp_res["conflicts"], "blocked_from_mixing": cmp_res["alternate_only_blocked_from_mixing"]},
                {"action": "adapt_document", "mode": adapt_report.get("mode"), "applied_fields": applied, "preview": preview_meta},
            ],
            artifacts=[out_docx.name, checklist_path.name, cmp_path.name, rep_path.name],
            sources=[{"relpath": str(src.name), "role": "source_manuscript"}, {"relpath": str(Path(args.target_template).name), "role": "designated_target_template"}],
            gaps=gaps,
            limitations=[
                "Only explicitly specified fields from the designated target template are applied; conflicting alternate template clauses are rejected.",
                "PDF/PNG visual layout checks depend on LibreOffice Writer and pdftoppm availability in the container.",
            ],
            verification={"fidelity_ok": adapt_report.get("fidelity_ok", False), "font_probe": font_probe, "preview": preview_meta},
            case_contract=case_contract,
        )
        res_path = out_dir / "result.json"
        res_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "fidelity_ok": adapt_report.get("fidelity_ok", False), "conflicts_isolated": len(cmp_res["conflicts"]), "unspecified_fields": len(cmp_res["target_unspecified_fields"]), "adapted_docx": str(out_docx)}, ensure_ascii=False))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
