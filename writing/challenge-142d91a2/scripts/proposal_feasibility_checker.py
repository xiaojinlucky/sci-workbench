#!/usr/bin/env python3
"""Offline research proposal 7-element, convergence, DAG/CPM, budget, resource-gap, and reconciliation auditor.

Pure Python 3.12 standard library (zero external dependencies, zero network calls).
Uses python-docx when preinstalled; otherwise exports valid OOXML .docx via stdlib zipfile plus standalone HTML.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import math
import re
import zipfile
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

REQUIRED_PROPOSAL_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("background_gap", ("研究背景", "现状与缺口", "背景与缺口", "background", "gap")),
    ("research_questions", ("科学问题", "研究问题", "research question")),
    ("objectives", ("研究目标", "目标设定", "objectives", "aims")),
    ("technical_route", ("技术路线", "研究方案", "technical route", "methodology")),
    ("milestones_schedule", ("阶段计划", "进度安排", "里程碑", "milestones", "timeline", "schedule")),
    ("risks_fallbacks", ("风险", "备选方案", "risk", "contingency")),
    ("expected_outcomes", ("预期成果", "考核指标", "引用溯源", "deliverables", "outcomes")),
]

REQUIRED_RISK_TYPES = ("data", "method", "schedule", "ethics_compliance")

KNOWN_RE = re.compile(r"已知|已有研究|前人|文献表明|既往报道|reported|prior\s+stud|existing\s+work|known", re.I)
UNKNOWN_RE = re.compile(r"未知|尚未明确|分歧|矛盾|缺口|瓶颈|局限|未验证|unknown|gap|contradict|unresolved|remains\s+unclear", re.I)
VERIFY_RE = re.compile(r"待验证|拟验证|本研究拟|本课题拟|聚焦于|收敛为|检验假设|to\s+verify|this\s+proposal|we\s+will\s+test|converge", re.I)

HOLLOW_GAP_RE = re.compile(
    r"尚无(?:任何)?研究|无人研究|目前尚属空白|填补(?:了)?(?:国内|国际)?空白|国际领先|国内首创|首次提出|no\s+prior\s+research|world-first|unprecedented",
    re.I,
)
OVERBROAD_SCOPE_RE = re.compile(
    r"解释所有影响因素|解决全部(?:科学)?问题|涵盖所有可能因素|全面彻底解决所有|explain\s+all\s+(?:possible\s+)?factors|solve\s+all\s+problems",
    re.I,
)
AI_CLICHE_RE = re.compile(
    r"综上所述|总的来说|由此可见|不难发现|众所周知|值得注意的是|显著赋能|深刻揭示了|为未来研究奠定(?:了)?(?:坚实)?基础|提供(?:了)?新思路",
    re.I,
)
DEFENSIVE_OPENING_RE = re.compile(
    r"^(?:遗憾的是|受限于|尽管本研究存在|虽然本方案|本文仅|本研究仅|无法证明|Unfortunately,\s)",
    re.I,
)
STUB_MARKER_RE = re.compile(r"\b(?:TODO|TBD|FIXME)\b|待补充|待撰写|lorem\s+ipsum|\[insert\s+[^\]]+\]", re.I)
GAP_TAG_RE = re.compile(r"\[EVIDENCE_GAP[^\]]*\]|\[待确认[^\]]*\]|\[待补充[^\]]*\]|\[\[EVIDENCE:[^\]]*\]\]", re.I)
NEGATION_PREFIX_RE = re.compile(
    r"(?:并非|不是|避免|拒绝|不得|严禁|禁止|不宣称|不声称|而非|取代|not\s+|never\s+|avoid\s+|rather\s+than\s+)$",
    re.I,
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")


def _is_finite_number(val: Any) -> bool:
    if isinstance(val, bool) or val is None:
        return False
    try:
        f = float(val)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _is_negated(line: str, pos: int) -> bool:
    prefix = line[max(0, pos - 18):pos]
    return bool(NEGATION_PREFIX_RE.search(prefix.strip()))


def _parse_sections(text: str) -> dict[str, dict[str, Any]]:
    lines = text.splitlines()
    raw_secs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    heading_re = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
    for lno, ln in enumerate(lines, 1):
        m = heading_re.match(ln.strip())
        if m:
            lvl = len(m.group(1))
            title = m.group(2).strip()
            if lvl == 1 and not any(any(k in title.lower() for k in kws) for _, kws in REQUIRED_PROPOSAL_SECTIONS):
                continue
            if cur is not None:
                raw_secs.append(cur)
            cur = {"title": title, "start_line": lno, "lines": []}
        elif cur is not None:
            cur["lines"].append((lno, ln))
    if cur is not None:
        raw_secs.append(cur)

    found: dict[str, dict[str, Any]] = {}
    for sec in raw_secs:
        low = sec["title"].lower()
        body_lines = [ln for _, ln in sec["lines"]]
        body_text = "\n".join(body_lines).strip()
        prose_lines = [
            ln.strip()
            for ln in body_lines
            if ln.strip() and not ln.strip().startswith("|") and not ln.strip().startswith("```")
        ]
        table_lines = [
            " ".join(c.strip() for c in ln.strip().strip("|").split("|"))
            for ln in body_lines
            if ln.strip().startswith("|")
            and not all(re.fullmatch(r":?-{2,}:?", c.strip() or "---") for c in ln.strip().strip("|").split("|"))
        ]
        full_content = "\n".join(prose_lines + table_lines)
        for key, kws in REQUIRED_PROPOSAL_SECTIONS:
            if any(k in low for k in kws) and key not in found:
                found[key] = {
                    "heading": sec["title"],
                    "start_line": sec["start_line"],
                    "han_chars": len(HAN_RE.findall(full_content)),
                    "en_words": len(WORD_RE.findall(full_content)),
                    "body_text": body_text,
                    "first_prose_line": prose_lines[0] if prose_lines else "",
                    "has_stub": bool(STUB_MARKER_RE.search(body_text)),
                }
                break
    return found


def _compute_cpm_and_parallelism(
    steps: list[dict[str, Any]],
    step_map: dict[str, dict[str, Any]],
    total_months: int,
    topo_order: list[str],
) -> dict[str, Any]:
    """Compute Critical Path Method (ES/EF/LS/LF/slack) and monthly parallel workload."""
    if not topo_order:
        return {
            "critical_path_steps": [],
            "critical_path_months": 0,
            "step_schedule": {},
            "peak_parallel_steps": 0,
            "peak_parallel_month": None,
            "peak_monthly_workload": 0.0,
            "monthly_workload": {},
            "parallelizable_pairs": [],
        }

    dur: dict[str, int] = {}
    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    succ: dict[str, list[str]] = {sid: [] for sid in topo_order}

    for sid in topo_order:
        s = step_map[sid]
        sm = int(s["start_month"])
        em = int(s["end_month"])
        d = max(1, em - sm + 1)
        dur[sid] = d
        deps = [dep for dep in s.get("depends_on", []) if dep in step_map]
        for dep in deps:
            succ[dep].append(sid)
        if not deps:
            es[sid] = 0
        else:
            starts = []
            for dep in deps:
                dep_end = int(step_map[dep]["end_month"])
                overlap_adj = 1 if sm == dep_end else 0
                starts.append(max(0, ef[dep] - overlap_adj))
            es[sid] = max(starts)
        ef[sid] = es[sid] + d

    cpm_len = max(ef.values()) if ef else 0
    lf: dict[str, int] = {}
    ls: dict[str, int] = {}
    for sid in reversed(topo_order):
        if not succ[sid]:
            lf[sid] = cpm_len
        else:
            ends = []
            s_end = int(step_map[sid]["end_month"])
            for nxt in succ[sid]:
                nxt_start = int(step_map[nxt]["start_month"])
                overlap_adj = 1 if nxt_start == s_end else 0
                ends.append(ls[nxt] + overlap_adj)
            lf[sid] = min(ends)
        ls[sid] = lf[sid] - dur[sid]

    slack = {sid: ls[sid] - es[sid] for sid in topo_order}
    crit_steps = [sid for sid in topo_order if slack[sid] == 0]

    horizon = max(total_months, max((int(s["end_month"]) for s in steps if _is_finite_number(s.get("end_month"))), default=0))
    monthly_active: dict[int, list[str]] = {m: [] for m in range(1, horizon + 1)}
    monthly_workload: dict[str, dict[str, Any]] = {}
    peak_steps = 0
    peak_month: int | None = None
    peak_workload = 0.0

    for m in range(1, horizon + 1):
        active_ids: list[str] = []
        wl = 0.0
        for s in steps:
            sid = str(s.get("step_id", ""))
            sm = int(s["start_month"])
            em = int(s["end_month"])
            if sm <= m <= em:
                active_ids.append(sid)
                eff = s.get("effort_units", s.get("fte", 1.0))
                wl += float(eff) if _is_finite_number(eff) and float(eff) > 0 else 1.0
        monthly_active[m] = active_ids
        wl = round(wl, 3)
        monthly_workload[f"M{m}"] = {"steps": active_ids, "count": len(active_ids), "workload": wl}
        if len(active_ids) > peak_steps or (len(active_ids) == peak_steps and wl > peak_workload):
            peak_steps = len(active_ids)
            peak_month = m
        if wl > peak_workload:
            peak_workload = wl

    parallel_pairs: set[tuple[str, str]] = set()
    for active in monthly_active.values():
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a, b = sorted((active[i], active[j]))
                parallel_pairs.add((a, b))

    return {
        "critical_path_steps": crit_steps,
        "critical_path_months": cpm_len,
        "step_schedule": {
            sid: {"duration": dur[sid], "ES": es[sid], "EF": ef[sid], "LS": ls[sid], "LF": lf[sid], "slack": slack[sid]}
            for sid in topo_order
        },
        "peak_parallel_steps": peak_steps,
        "peak_parallel_month": peak_month,
        "peak_monthly_workload": round(peak_workload, 3),
        "monthly_workload": monthly_workload,
        "parallelizable_pairs": [list(p) for p in sorted(parallel_pairs)],
    }


def _reconcile_prose_and_json(
    text: str,
    plan: dict[str, Any],
    total_months: int,
    budget_sum: float,
    budget_limit: float | None,
) -> list[dict[str, Any]]:
    """Verify proposal.md numeric/ID declarations match proposal_plan.json."""
    mismatches: list[dict[str, Any]] = []

    # 1. Total duration check in prose
    if total_months > 0:
        dur_pat = re.compile(
            r"(?:总周期|研究周期|计划周期|实施周期|总时长|项目周期)\s*[:：为约共]?\s*(\d{1,3})\s*个?月|"
            r"\b(\d{1,3})-month\s+(?:timeline|schedule|duration|project)\b",
            re.I,
        )
        for m in dur_pat.finditer(text):
            val = int(m.group(1) or m.group(2))
            if val != total_months:
                mismatches.append({
                    "field": "total_months",
                    "prose_value": val,
                    "json_value": total_months,
                    "excerpt": m.group(0),
                })

    # 2. Budget total / limit check in prose
    bud_total_pat = re.compile(
        r"(?:预算合计|经费合计|总预算|总经费|预算总计)\s*[:：为约]?\s*(?:人民币|RMB|¥|\$)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(万)?",
        re.I,
    )
    for m in bud_total_pat.finditer(text):
        raw_num = float(m.group(1).replace(",", ""))
        val = raw_num * 10000.0 if m.group(2) else raw_num
        if budget_sum > 0 and abs(val - budget_sum) > max(1.0, budget_sum * 0.01):
            # Also tolerate if unit scale matches raw_num directly (e.g. JSON stores 万元)
            if abs(raw_num - budget_sum) > max(0.05, budget_sum * 0.01):
                mismatches.append({
                    "field": "budget_sum",
                    "prose_value": m.group(0),
                    "json_value": budget_sum,
                    "excerpt": m.group(0),
                })

    if budget_limit is not None and budget_limit > 0:
        bud_cap_pat = re.compile(
            r"(?:预算上限|经费上限|资助上限)\s*[:：为约]?\s*(?:人民币|RMB|¥|\$)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(万)?",
            re.I,
        )
        for m in bud_cap_pat.finditer(text):
            raw_num = float(m.group(1).replace(",", ""))
            val = raw_num * 10000.0 if m.group(2) else raw_num
            if abs(val - budget_limit) > max(1.0, budget_limit * 0.01) and abs(raw_num - budget_limit) > max(0.05, budget_limit * 0.01):
                mismatches.append({
                    "field": "budget_limit",
                    "prose_value": m.group(0),
                    "json_value": budget_limit,
                    "excerpt": m.group(0),
                })

    # 3. Objective IDs consistency
    json_obj_ids = [str(o.get("objective_id", "")).strip().upper() for o in plan.get("objectives", []) if isinstance(o, dict) and o.get("objective_id")]
    if json_obj_ids:
        missing_in_prose = [oid for oid in json_obj_ids if not re.search(rf"\b{re.escape(oid)}\b", text, re.I)]
        if missing_in_prose:
            mismatches.append({
                "field": "objective_ids",
                "prose_value": "missing_ids",
                "json_value": json_obj_ids,
                "missing_in_prose": missing_in_prose,
            })

    # 4. Milestone month bounds mentioned in prose (e.g., M15 or 第15个月 when total_months=12)
    if total_months > 0:
        for m in re.finditer(r"(?:里程碑|节点|Milestone)\s*[A-Za-z0-9_-]*\s*[:：（(]?\s*(?:M|第\s*)(\d{1,3})\s*(?:个?月)?", text, re.I):
            mval = int(m.group(1))
            if mval > total_months:
                mismatches.append({
                    "field": "milestone_month_in_prose",
                    "prose_value": mval,
                    "json_value": f"<= {total_months}",
                    "excerpt": m.group(0),
                })

    return mismatches


def _collect_corpus_text(corpus_path: Path | None, exclude_paths: set[Path] | None = None) -> str:
    if not corpus_path or not corpus_path.exists():
        return ""
    exts = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".tex", ".html"}
    excluded = {p.resolve() for p in (exclude_paths or set()) if p}
    chunks: list[str] = []
    files = [corpus_path] if corpus_path.is_file() else sorted(p for p in corpus_path.rglob("*") if p.is_file())
    for fp in files:
        if fp.resolve() in excluded:
            continue
        if "audit" in fp.name.lower() or "contract_helper" in fp.name.lower():
            continue
        if fp.suffix.lower() in exts:
            chunks.append(fp.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks).lower()


def _export_stdlib_ooxml_docx(markdown_text: str, out_path: Path) -> None:
    """Create a valid, Word-compatible OOXML .docx using only Python 3.12 stdlib."""
    body_xml: list[str] = []
    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        st = lines[i].strip()
        if not st:
            i += 1
            continue
        # Handle markdown table block
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
            tbl_rows: list[list[str]] = []
            while i < len(lines):
                row_s = lines[i].strip()
                if not (row_s.startswith("|") and row_s.endswith("|")):
                    break
                cells = [c.strip() for c in row_s.strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                    tbl_rows.append(cells)
                i += 1
            if tbl_rows:
                rows_xml: list[str] = []
                for r_idx, row in enumerate(tbl_rows):
                    cells_xml: list[str] = []
                    for cell in row:
                        rpr = "<w:rPr><w:b/></w:rPr>" if r_idx == 0 else ""
                        cells_xml.append(
                            "<w:tc><w:p><w:r>" + rpr + f"<w:t xml:space=\"preserve\">{xml_escape(cell)}</w:t></w:r></w:p></w:tc>"
                        )
                    rows_xml.append("<w:tr>" + "".join(cells_xml) + "</w:tr>")
                body_xml.append(
                    "<w:tbl><w:tblPr><w:tblBorders>"
                    "<w:top w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"BFBFBF\"/>"
                    "<w:bottom w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"BFBFBF\"/>"
                    "<w:insideH w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9D9D9\"/>"
                    "</w:tblBorders></w:tblPr>" + "".join(rows_xml) + "</w:tbl>"
                )
            continue

        style_id = ""
        text_val = st
        if st.startswith("### "):
            style_id = "Heading3"
            text_val = st[4:].strip()
        elif st.startswith("## "):
            style_id = "Heading2"
            text_val = st[3:].strip()
        elif st.startswith("# "):
            style_id = "Heading1"
            text_val = st[2:].strip()
        elif st.startswith(("- ", "* ")):
            text_val = "• " + st[2:].strip()

        text_val = re.sub(r"\*\*(.+?)\*\*", r"\1", text_val)
        ppr = f"<w:pPr><w:pStyle w:val=\"{style_id}\"/></w:pPr>" if style_id else ""
        body_xml.append(
            f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{xml_escape(text_val)}</w:t></w:r></w:p>"
        )
        i += 1

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body_xml)
        + "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
        "</w:styles>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/document.xml", document_xml)


def _export_standalone_html(markdown_text: str, out_path: Path, summary: dict[str, Any]) -> None:
    """Write a zero-CDN, clean offline HTML preview for human inspection."""
    parts: list[str] = []
    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        st = lines[i].strip()
        if not st:
            i += 1
            continue
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                th = "".join(f"<th>{html.escape(c)}</th>" for c in rows[0])
                tb = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                parts.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            continue
        if st.startswith("### "):
            parts.append(f"<h3>{html.escape(st[4:].strip())}</h3>")
        elif st.startswith("## "):
            parts.append(f"<h2>{html.escape(st[3:].strip())}</h2>")
        elif st.startswith("# "):
            parts.append(f"<h1>{html.escape(st[2:].strip())}</h1>")
        elif st.startswith(("- ", "* ")):
            parts.append(f"<p class='li'>• {html.escape(st[2:].strip())}</p>")
        else:
            parts.append(f"<p>{html.escape(st)}</p>")
        i += 1

    badge = (
        f"<div class='meta'><strong>Audit Status:</strong> {'PASSED' if summary['passed'] else 'NEEDS REVISION'} | "
        f"<strong>Total Months:</strong> {summary['total_months']} | "
        f"<strong>CPM:</strong> {summary['cpm_months']}m ({' -> '.join(summary['cpm_steps'])}) | "
        f"<strong>Peak Parallel Workload:</strong> {summary['peak_workload']} | "
        f"<strong>Budget Sum:</strong> {summary['budget_sum']}</div>"
    )
    doc = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/>"
        "<title>Research Proposal Preview</title>"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:920px;margin:24px auto;padding:0 20px;line-height:1.65;color:#1f2937}"
        "h1,h2,h3{color:#0f172a;margin-top:1.3em}table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}"
        "th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left}th{background:#f1f5f9}"
        ".meta{background:#f8fafc;border:1px solid #cbd5e1;padding:10px 14px;border-radius:6px;font-size:13px;margin-bottom:18px}"
        ".li{margin:4px 0 4px 12px}</style></head><body>"
        + badge
        + "\n".join(parts)
        + "</body></html>\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")


def audit_proposal_plan(
    proposal_md: Path,
    plan_json_path: Path,
    *,
    case_json_path: Path | None = None,
    corpus_path: Path | None = None,
    export_docx_path: Path | None = None,
    export_html_path: Path | None = None,
    strict_style: bool = False,
) -> dict[str, Any]:
    text = proposal_md.read_text(encoding="utf-8", errors="replace")
    plan = json.loads(plan_json_path.read_text(encoding="utf-8"))
    case_cfg = json.loads(case_json_path.read_text(encoding="utf-8")) if case_json_path and case_json_path.is_file() else {}
    case_id = str(case_cfg.get("case_id") or plan.get("case_id") or "unknown")

    errors: list[str] = []
    style_warnings: list[dict[str, Any]] = []

    # 1. Seven elements thickness & 已知—未知—待验证 structure
    found_secs = _parse_sections(text)
    missing_secs = [k for k, _ in REQUIRED_PROPOSAL_SECTIONS if k not in found_secs]
    thin_secs: list[dict[str, Any]] = []
    stub_secs: list[str] = []
    for key, _ in REQUIRED_PROPOSAL_SECTIONS:
        info = found_secs.get(key)
        if not info:
            continue
        effective_units = info["han_chars"] + info["en_words"]
        if effective_units < 60:
            thin_secs.append({"section": key, "heading": info["heading"], "han_chars": info["han_chars"], "en_words": info["en_words"], "effective_units": effective_units})
        if info["has_stub"]:
            stub_secs.append(key)

    if missing_secs:
        errors.append(f"Missing proposal markdown section(s): {missing_secs}")
    if thin_secs:
        errors.append(f"Substantively thin section(s): {[s['section'] for s in thin_secs]}")
    if stub_secs:
        errors.append(f"Unfinished stub marker(s) in section(s): {stub_secs}")

    bg_text = found_secs.get("background_gap", {}).get("body_text", "")
    has_known = bool(KNOWN_RE.search(bg_text))
    has_unknown = bool(UNKNOWN_RE.search(bg_text))
    has_verify = bool(VERIFY_RE.search(bg_text))
    kuv_ok = has_known and has_unknown and has_verify
    if bg_text and not kuv_ok:
        errors.append(
            f"Background section missing '已知—未知—待验证' triad (known={has_known}, unknown={has_unknown}, verify={has_verify})"
        )

    # Check hollow gap & overbroad scope claims
    hollow_gap_hits: list[dict[str, Any]] = []
    overbroad_hits: list[dict[str, Any]] = []
    for lno, ln in enumerate(text.splitlines(), 1):
        m_hol = HOLLOW_GAP_RE.search(ln)
        if m_hol and not _is_negated(ln, m_hol.start()):
            hollow_gap_hits.append({"line": lno, "term": m_hol.group(0), "excerpt": ln.strip()[:140]})
        m_ov = OVERBROAD_SCOPE_RE.search(ln)
        if m_ov and not _is_negated(ln, m_ov.start()):
            overbroad_hits.append({"line": lno, "term": m_ov.group(0), "excerpt": ln.strip()[:140]})
        m_ai = AI_CLICHE_RE.search(ln)
        if m_ai and not _is_negated(ln, m_ai.start()):
            style_warnings.append({"kind": "ai_cliche", "line": lno, "term": m_ai.group(0), "excerpt": ln.strip()[:140]})
    for sec_k in ("background_gap", "objectives", "expected_outcomes"):
        info = found_secs.get(sec_k)
        if info and DEFENSIVE_OPENING_RE.search(info["first_prose_line"]):
            style_warnings.append({"kind": "defensive_opening", "line": info["start_line"], "term": info["first_prose_line"][:36], "excerpt": info["first_prose_line"][:140]})

    if hollow_gap_hits:
        errors.append(f"Hollow unverified priority/no-prior-work claim(s) detected: {[h['term'] for h in hollow_gap_hits[:4]]}")
    if overbroad_hits:
        errors.append(f"Unconverged overbroad scope phrasing detected: {[h['term'] for h in overbroad_hits[:4]]}")
    if strict_style and style_warnings:
        errors.append(f"Strict style mode failed with {len(style_warnings)} warning(s)")

    # 2. Research questions (1..3) & quantified objectives (metric, sample, control, checkpoint)
    rqs = plan.get("research_questions") if isinstance(plan.get("research_questions"), list) else []
    objectives = plan.get("objectives") if isinstance(plan.get("objectives"), list) else []
    steps = plan.get("route_steps") if isinstance(plan.get("route_steps"), list) else []
    milestones = plan.get("milestones") if isinstance(plan.get("milestones"), list) else []
    budget_items = plan.get("budget_items") if isinstance(plan.get("budget_items"), list) else []
    risks = plan.get("risks") if isinstance(plan.get("risks"), list) else []
    resource_gaps = plan.get("resource_gaps") if isinstance(plan.get("resource_gaps"), list) else []
    claim_citations = plan.get("claim_citations") if isinstance(plan.get("claim_citations"), list) else []

    if rqs:
        if not (1 <= len(rqs) <= 3):
            errors.append(f"Research questions must converge to 1–3 items, got {len(rqs)}")
        for rq in rqs:
            if isinstance(rq, dict) and not str(rq.get("falsifiable_criterion") or rq.get("criterion") or "").strip():
                errors.append(f"Research question {rq.get('rq_id', '?')} missing falsifiable_criterion")
    else:
        errors.append("Plan JSON missing 1–3 structured research_questions")

    if not objectives:
        errors.append("No quantified research objectives defined in plan JSON")

    # 3. Timeline & Budget limits (with anti-fabrication guard)
    plan_months = int(plan["total_months"]) if _is_finite_number(plan.get("total_months")) else 0
    case_months = int(case_cfg["total_months"]) if _is_finite_number(case_cfg.get("total_months")) else 0
    total_months = min(plan_months, case_months) if plan_months > 0 and case_months > 0 else (case_months or plan_months)
    if total_months <= 0:
        errors.append("total_months must be a positive integer")
    if case_months > 0 and plan_months > case_months:
        errors.append(f"Plan duration {plan_months} exceeds case total_months {case_months}")

    case_has_budget_key = "budget_limit" in case_cfg or "budget_limit_supplied" in case_cfg
    case_budget_raw = case_cfg.get("budget_limit")
    case_budget_explicitly_absent = (
        case_cfg.get("budget_limit_supplied") is False
        or (case_has_budget_key and (case_budget_raw is None or str(case_budget_raw).strip().lower() in {"", "none", "null", "not_provided", "unknown", "未提供"}))
    )
    case_budget = float(case_budget_raw) if _is_finite_number(case_budget_raw) and float(case_budget_raw) > 0 else None

    plan_budget_raw = plan.get("budget_limit")
    plan_budget = float(plan_budget_raw) if _is_finite_number(plan_budget_raw) and float(plan_budget_raw) > 0 else None
    budget_limit_fabricated = False
    if case_budget_explicitly_absent and plan_budget is not None:
        src_tag = str(plan.get("budget_limit_source") or "").lower()
        if not any(tok in src_tag for tok in ("pending", "gap", "estimated", "not_supplied")):
            budget_limit_fabricated = True
            errors.append(
                f"Case did not supply budget_limit, yet plan fabricated numeric budget_limit={plan_budget} without pending/estimated source declaration"
            )
    if case_budget is not None and plan_budget is not None and plan_budget > case_budget + 1e-6:
        errors.append(f"Plan budget_limit {plan_budget} exceeds case budget_limit {case_budget}")
    effective_budget_limit = case_budget if case_budget is not None else plan_budget

    # Objective -> Route -> Milestone closure + falsifiability fields (defensive against str/non-dict items)
    step_ids: dict[str, dict[str, Any]] = {}
    valid_steps: list[dict[str, Any]] = []
    for idx_s, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            errors.append(f"Route step #{idx_s} is {type(s).__name__}; expected object with step_id/start_month/end_month")
            continue
        valid_steps.append(s)
        if s.get("step_id"):
            step_ids[str(s["step_id"])] = s
    if len(step_ids) != len(steps):
        errors.append("Each technical route step must have a unique non-empty step_id")

    ms_by_obj = {str(m.get("objective_id")) for m in milestones if isinstance(m, dict) and m.get("objective_id")}
    step_by_obj = {str(s.get("objective_id")) for s in valid_steps if s.get("objective_id")}

    for idx_o, obj in enumerate(objectives, 1):
        if isinstance(obj, str):
            errors.append(f"Objective #{idx_o} ({obj[:40]!r}) is a plain string; expected object with objective_id, metric, target_value, sample_or_unit, control_or_baseline")
            continue
        if not isinstance(obj, dict):
            errors.append(f"Objective #{idx_o} has unsupported type {type(obj).__name__}")
            continue
        oid = str(obj.get("objective_id") or f"O{idx_o}")
        if not str(obj.get("metric") or "").strip() or not str(obj.get("target_value") or "").strip():
            errors.append(f"Objective {oid} missing quantified metric or target_value")
        if not str(obj.get("sample_or_unit") or obj.get("sample") or "").strip():
            errors.append(f"Objective {oid} missing sample_or_unit (inference unit / sample size)")
        if not str(obj.get("control_or_baseline") or obj.get("control") or "").strip():
            errors.append(f"Objective {oid} missing control_or_baseline")
        if oid not in step_by_obj:
            errors.append(f"Objective {oid} has no mapped technical route step")
        if oid not in ms_by_obj:
            errors.append(f"Objective {oid} has no mapped schedule milestone")

    # 4. DAG topology, ordering, CPM, and parallel workload
    step_end: dict[str, int] = {}
    valid_spans = True
    for s in valid_steps:
        sid = str(s.get("step_id") or "S?")
        sm_raw = s.get("start_month")
        em_raw = s.get("end_month")
        if not _is_finite_number(sm_raw) or not _is_finite_number(em_raw):
            errors.append(f"Route step {sid} has non-numeric month span [{sm_raw}, {em_raw}]")
            valid_spans = False
            continue
        sm, em = int(sm_raw), int(em_raw)
        if sm < 1 or em < sm:
            errors.append(f"Route step {sid} has invalid month span [{sm}, {em}]")
            valid_spans = False
        if total_months > 0 and em > total_months:
            errors.append(f"Route step {sid} exceeds total_months ({em} > {total_months})")
        step_end[sid] = em

    for s in valid_steps:
        sid = str(s.get("step_id") or "S?")
        if not _is_finite_number(s.get("start_month")):
            continue
        sm = int(s["start_month"])
        deps_raw = s.get("depends_on", [])
        if not isinstance(deps_raw, list):
            errors.append(f"Route step {sid} depends_on must be a list, got {type(deps_raw).__name__}")
            continue
        for dep in deps_raw:
            dep_s = str(dep)
            if dep_s not in step_ids:
                errors.append(f"Route step {sid} depends on unknown step {dep_s}")
            elif dep_s in step_end and sm < step_end[dep_s]:
                errors.append(f"Route step {sid} starts at M{sm} before dependency {dep_s} ends at M{step_end[dep_s]}")

    dependency_graph = {
        sid: [str(d) for d in (step.get("depends_on") if isinstance(step.get("depends_on"), list) else []) if str(d) in step_ids]
        for sid, step in step_ids.items()
    }
    topo_order: list[str] = []
    dag_acyclic = True
    try:
        ts = TopologicalSorter(dependency_graph)
        topo_order = list(ts.static_order())
    except CycleError as exc:
        dag_acyclic = False
        cycle = " -> ".join(str(x) for x in exc.args[1])
        errors.append(f"Technical route has a dependency cycle: {cycle}")

    cpm_report: dict[str, Any] = {
        "critical_path_steps": [],
        "critical_path_months": 0,
        "peak_parallel_steps": 0,
        "peak_parallel_month": None,
        "peak_monthly_workload": 0.0,
        "parallelizable_pairs": [],
    }
    if dag_acyclic and valid_spans and len(step_ids) == len(valid_steps) and valid_steps:
        cpm_report = _compute_cpm_and_parallelism(valid_steps, step_ids, total_months, topo_order)
        if total_months > 0 and cpm_report["critical_path_months"] > total_months:
            errors.append(
                f"Critical path duration {cpm_report['critical_path_months']}m exceeds total_months {total_months}m ({cpm_report['critical_path_steps']})"
            )
        max_wl_cap = case_cfg.get("max_parallel_workload") or plan.get("max_parallel_workload")
        if _is_finite_number(max_wl_cap) and float(max_wl_cap) > 0:
            if cpm_report["peak_monthly_workload"] > float(max_wl_cap) + 1e-6:
                errors.append(
                    f"Peak monthly workload {cpm_report['peak_monthly_workload']} at M{cpm_report['peak_parallel_month']} exceeds cap {max_wl_cap}"
                )

    for idx_m, ms in enumerate(milestones, 1):
        if not isinstance(ms, dict):
            errors.append(f"Milestone #{idx_m} is {type(ms).__name__}; expected object with milestone_id and integer month")
            continue
        mid = str(ms.get("milestone_id") or f"M{idx_m}")
        mval = ms.get("month")
        if mval is None or not re.fullmatch(r"[+-]?\d+", str(mval).strip()):
            errors.append(f"Milestone {mid} has a non-integer month: {mval!r}")
            continue
        mi = int(str(mval).strip())
        if mi < 1 or (total_months > 0 and mi > total_months):
            errors.append(f"Milestone {mid} at M{mi} is outside [1, {total_months}]")

    # 5. Strict Budget Arithmetic (blocks NaN, negative, zero-padding, overrun)
    budget_sum = 0.0
    for idx, b in enumerate(budget_items, 1):
        if not isinstance(b, dict):
            errors.append(f"Budget item #{idx} is not an object")
            continue
        amt = b.get("amount")
        label = str(b.get("item") or f"item#{idx}")
        if not _is_finite_number(amt):
            errors.append(f"Budget item '{label}' has non-finite/invalid amount: {amt!r}")
            continue
        famt = float(amt)
        if famt < 0:
            errors.append(f"Budget item '{label}' has negative amount: {famt}")
        elif famt == 0:
            errors.append(f"Budget item '{label}' has zero-value padding amount: 0")
        else:
            budget_sum += famt
    budget_sum = round(budget_sum, 4)

    declared_budget_total = plan.get("budget_total_declared")
    if declared_budget_total is not None:
        if not _is_finite_number(declared_budget_total):
            errors.append(f"budget_total_declared is non-finite: {declared_budget_total!r}")
        elif abs(float(declared_budget_total) - budget_sum) > 1e-3:
            errors.append(f"Budget items sum {budget_sum} != budget_total_declared {declared_budget_total}")
    if effective_budget_limit is not None and budget_sum > effective_budget_limit + 1e-6:
        errors.append(f"Budget sum {budget_sum} exceeds budget_limit {effective_budget_limit}")

    # 6. 4-Category Risks & Unavailable Resource/Sample Gate
    covered_risks: set[str] = set()
    for r in risks:
        if not isinstance(r, dict):
            continue
        rtype = str(r.get("category") or "").strip().lower()
        covered_risks.add(rtype)
        rid = str(r.get("risk_id") or rtype or "R?")
        if not str(r.get("trigger_signal") or "").strip() or not str(r.get("fallback_plan") or "").strip():
            errors.append(f"Risk {rid} missing trigger_signal or fallback_plan")
        if not str(r.get("decision_checkpoint") or "").strip():
            errors.append(f"Risk {rid} missing decision_checkpoint")
    missing_risk_cats = [rt for rt in REQUIRED_RISK_TYPES if rt not in covered_risks]
    if missing_risk_cats:
        errors.append(f"Missing required risk categories: {missing_risk_cats}")

    unavailable_resources: list[str] = []
    for k in ("unavailable_resources", "missing_samples", "unavailable_samples"):
        val = case_cfg.get(k)
        if isinstance(val, list):
            unavailable_resources.extend(str(x).strip() for x in val if str(x).strip())
        elif isinstance(val, str) and val.strip():
            unavailable_resources.append(val.strip())
    case_blob = json.dumps(case_cfg, ensure_ascii=False)
    if not unavailable_resources and re.search(r"样本不可得|关键样本缺失|设备不可得|unavailable\s+(?:sample|resource|cohort)", case_blob, re.I):
        unavailable_resources.append("declared_unavailable_sample_or_resource_in_case")

    resource_gaps_linked_ok = True
    if unavailable_resources:
        valid_rg = [
            rg for rg in resource_gaps
            if isinstance(rg, dict)
            and str(rg.get("resource") or "").strip()
            and str(rg.get("trigger_signal") or "").strip()
            and str(rg.get("decision_checkpoint") or "").strip()
            and str(rg.get("fallback_plan") or "").strip()
        ]
        if len(valid_rg) < len(unavailable_resources):
            resource_gaps_linked_ok = False
            errors.append(
                f"Unavailable sample/resource(s) {unavailable_resources} require linked resource_gaps entries "
                f"(resource -> trigger_signal -> decision_checkpoint -> fallback_plan); found {len(valid_rg)}"
            )
        if not GAP_TAG_RE.search(text):
            resource_gaps_linked_ok = False
            errors.append("Unavailable sample/resource detected in case, but proposal.md contains zero explicit [EVIDENCE_GAP] markers")

    # 7. Prose <-> JSON Reconciliation & Claim-Citation Grounding
    reconciliation_mismatches = _reconcile_prose_and_json(text, plan, total_months, budget_sum, effective_budget_limit)
    for mm in reconciliation_mismatches:
        errors.append(f"Prose vs JSON mismatch on {mm['field']}: prose={mm['prose_value']} vs json={mm['json_value']}")

    valid_citations = [
        c for c in claim_citations
        if isinstance(c, dict) and str(c.get("claim") or "").strip() and str(c.get("source_ref") or "").strip()
    ]
    if len(valid_citations) < 3:
        errors.append(f"Claim-citation mapping has only {len(valid_citations)} valid entries (minimum 3 required)")

    effective_corpus = corpus_path if corpus_path else (case_json_path.parent if case_json_path and case_json_path.is_file() else None)
    corpus_blob = _collect_corpus_text(effective_corpus, exclude_paths={proposal_md, plan_json_path})
    ungrounded_dois: list[str] = []
    if corpus_blob:
        found_dois = sorted(set(DOI_RE.findall(text + "\n" + json.dumps(claim_citations, ensure_ascii=False))))
        for doi in found_dois:
            if doi.lower() not in corpus_blob:
                ungrounded_dois.append(doi)
        if ungrounded_dois:
            errors.append(f"Ungrounded DOI(s) not present in /input corpus: {ungrounded_dois}")

    # 8. Zero-Crash Editable Export (.docx + .html)
    py_docx_installed = importlib.util.find_spec("docx") is not None
    docx_exported = False
    docx_exporter = "none"
    if export_docx_path:
        if py_docx_installed:
            import docx  # type: ignore

            doc = docx.Document()
            for ln in text.splitlines():
                st = ln.strip()
                if not st:
                    continue
                if st.startswith("# "):
                    doc.add_heading(st[2:].strip(), level=1)
                elif st.startswith("## "):
                    doc.add_heading(st[3:].strip(), level=2)
                elif st.startswith("### "):
                    doc.add_heading(st[4:].strip(), level=3)
                else:
                    doc.add_paragraph(st)
            export_docx_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(export_docx_path))
            docx_exported = True
            docx_exporter = "python-docx"
        else:
            _export_stdlib_ooxml_docx(text, export_docx_path)
            docx_exported = True
            docx_exporter = "stdlib_ooxml"

    html_exported = False
    if export_html_path:
        _export_standalone_html(
            text,
            export_html_path,
            {
                "passed": len(errors) == 0,
                "total_months": total_months,
                "cpm_months": cpm_report["critical_path_months"],
                "cpm_steps": cpm_report["critical_path_steps"],
                "peak_workload": cpm_report["peak_monthly_workload"],
                "budget_sum": budget_sum,
            },
        )
        html_exported = True

    honest_gaps: list[str] = []
    if unavailable_resources:
        honest_gaps.append(f"unavailable_resources={unavailable_resources}")
    if case_budget_explicitly_absent:
        honest_gaps.append("budget_limit_not_supplied_in_input")

    contract_helper = {
        "case_id": case_id,
        "recommended_status": "partial_with_gaps" if honest_gaps else "ok",
        "deliverable": {
            "proposal_md": proposal_md.name,
            "proposal_plan_json": plan_json_path.name,
            "docx": export_docx_path.name if (export_docx_path and docx_exported) else None,
            "docx_exporter": docx_exporter,
            "html_preview": export_html_path.name if (export_html_path and html_exported) else None,
        },
        "preserved_facts": [
            f"total_months={total_months}",
            f"budget_limit={effective_budget_limit}",
            f"budget_sum={budget_sum}",
            f"critical_path_months={cpm_report['critical_path_months']}",
        ],
        "evidence_map": valid_citations,
        "change_log": {
            "resource_gaps": resource_gaps,
            "human_confirmations": plan.get("human_confirmations", []),
            "unresolved_gaps": honest_gaps,
        },
    }

    return {
        "passed": len(errors) == 0,
        "case_id": case_id,
        "seven_elements": {
            "found_sections": {k: {"heading": v["heading"], "start_line": v["start_line"], "han_chars": v["han_chars"], "en_words": v["en_words"]} for k, v in found_secs.items()},
            "missing_sections": missing_secs,
            "thin_sections": thin_secs,
            "stub_sections": stub_secs,
            "known_unknown_verify_ok": kuv_ok,
            "research_questions_count": len(rqs),
            "objectives_count": len(objectives),
            "hollow_gap_hits": hollow_gap_hits,
            "overbroad_scope_hits": overbroad_hits,
        },
        "schedule_and_cpm": {
            "total_months": total_months,
            "route_steps_count": len(steps),
            "dag_acyclic": dag_acyclic,
            **cpm_report,
        },
        "budget": {
            "budget_limit": effective_budget_limit,
            "budget_sum": budget_sum,
            "budget_total_declared": declared_budget_total,
            "budget_limit_fabricated": budget_limit_fabricated,
        },
        "risks_and_resource_gaps": {
            "missing_risk_categories": missing_risk_cats,
            "risks_count": len(risks),
            "unavailable_resources_detected": unavailable_resources,
            "resource_gaps_count": len(resource_gaps),
            "resource_gaps_linked_ok": resource_gaps_linked_ok,
        },
        "prose_json_reconciliation": {
            "mismatches": reconciliation_mismatches,
            "valid_claim_citations_count": len(valid_citations),
            "ungrounded_dois": ungrounded_dois,
        },
        "exports": {
            "python_docx_installed": py_docx_installed,
            "docx_exported": docx_exported,
            "docx_exporter": docx_exporter,
            "html_exported": html_exported,
        },
        "style_warnings": style_warnings,
        "errors": errors,
        "output_contract_preview": contract_helper,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate research proposal 7-element convergence, DAG/CPM, budget, 4-category risks, prose-JSON reconciliation, and editable export."
    )
    parser.add_argument("--proposal", required=True, help="Proposal markdown file.")
    parser.add_argument("--plan-json", required=True, help="Structured proposal_plan.json.")
    parser.add_argument("--case-json", default="", help="Optional /input/case.json with constraints.")
    parser.add_argument("--corpus", default="", help="Optional /input directory for DOI/source grounding.")
    parser.add_argument("--export-docx", default="", help="Optional editable .docx export path.")
    parser.add_argument("--export-html", default="", help="Optional standalone .html preview path.")
    parser.add_argument("--strict-style", action="store_true", help="Treat style warnings as blocking errors.")
    parser.add_argument("--emit-contract-helper", default="", help="Optional JSON path for 5-field output_contract helper.")
    parser.add_argument("--out-json", required=True, help="Output feasibility audit JSON.")
    args = parser.parse_args(argv)

    rep = audit_proposal_plan(
        Path(args.proposal),
        Path(args.plan_json),
        case_json_path=Path(args.case_json) if args.case_json else None,
        corpus_path=Path(args.corpus) if args.corpus else None,
        export_docx_path=Path(args.export_docx) if args.export_docx else None,
        export_html_path=Path(args.export_html) if args.export_html else None,
        strict_style=args.strict_style,
    )

    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.emit_contract_helper:
        hp = Path(args.emit_contract_helper)
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(rep["output_contract_preview"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "passed": rep["passed"],
        "cpm_months": rep["schedule_and_cpm"]["critical_path_months"],
        "peak_parallel_steps": rep["schedule_and_cpm"]["peak_parallel_steps"],
        "budget_sum": rep["budget"]["budget_sum"],
        "docx_exported": rep["exports"]["docx_exported"],
        "docx_exporter": rep["exports"]["docx_exporter"],
        "error_count": len(rep["errors"]),
        "errors": rep["errors"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
