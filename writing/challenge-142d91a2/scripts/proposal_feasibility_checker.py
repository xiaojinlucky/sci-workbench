#!/usr/bin/env python3
"""Offline research proposal extractor, 7-element convergence, DAG/CPM, budget, source-grounding, and export engine.

Pure Python 3.12 stdlib (zero external deps, zero network).
Reads .md/.txt/.json/.csv, native OOXML .docx, and .pdf (pdftotext / pypdf / stdlib zlib fallback).
Exports editable .docx (python-docx or stdlib OOXML) and standalone offline HTML preview.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

REQUIRED_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("background_gap", ("研究背景", "现状与缺口", "背景与缺口", "立项依据", "background", "gap")),
    ("research_questions", ("科学问题", "研究问题", "关键问题", "research question", "hypothesis")),
    ("objectives", ("研究目标", "目标设定", "研究内容与目标", "objectives", "aims")),
    ("technical_route", ("技术路线", "研究方案", "研究内容", "可行性分析", "technical route", "methodology")),
    ("milestones_schedule", ("阶段计划", "进度安排", "里程碑", "研究进度", "milestones", "timeline", "schedule")),
    ("risks_fallbacks", ("风险", "备选方案", "应急预案", "risk", "contingency")),
    ("expected_outcomes", ("预期成果", "考核指标", "引用溯源", "deliverables", "outcomes")),
]

SEMANTIC_CUES: dict[str, tuple[str, ...]] = {
    "background_gap": ("已知", "未知", "待验证", "缺口", "断点", "瓶颈", "现状", "背景", "文献", "公开资料", "既有", "known", "unknown", "gap", "background"),
    "research_questions": ("RQ1", "RQ2", "研究问题", "科学问题", "可证伪", "可证伪标准", "假设", "falsifiable", "hypothesis"),
    "objectives": ("O1", "O2", "O3", "目标 O1", "量化考核目标", "研究目标", "考核指标", "对照基线", "样本单元", "样本量", "检验节点", "objective", "baseline", "metric"),
    "technical_route": ("S1", "S2", "S3", "技术路线", "实验方案", "前置依赖", "输入输出", "依赖", "DAG", "route", "workflow", "method"),
    "milestones_schedule": ("M1", "M4", "M12", "里程碑", "阶段计划", "关键路径", "CPM", "milestone", "schedule"),
    "risks_fallbacks": ("触发信号", "备选方案", "决策时点", "伦理", "data", "method", "schedule", "ethics_compliance"),
    "expected_outcomes": ("预期交付", "预期成果", "交付物", "Fig.", "引用", "溯源", "来源定位", "DOI", "deliverable", "citation"),
}

REQUIRED_RISK_TYPES = ("data", "method", "schedule", "ethics_compliance")
KNOWN_RE = re.compile(r"已知|已有研究|前人|文献表明|既往报道|现有研究表明|reported|prior\s+stud|existing\s+work|known|established", re.I)
UNKNOWN_RE = re.compile(r"未知|尚未明确|分歧|矛盾|缺口|瓶颈|局限|未验证|证据不足|unknown|gap|contradict|unresolved|remains\s+unclear|insufficient", re.I)
VERIFY_RE = re.compile(r"待验证|拟验证|本研究拟|本课题拟|聚焦于|收敛为|检验假设|拟通过|to\s+verify|this\s+proposal|we\s+will\s+test|converge", re.I)

HOLLOW_GAP_RE = re.compile(r"尚无(?:任何)?研究|无人研究|目前尚属空白|填补(?:了)?(?:国内|国际)?空白|国际领先|国内首创|首次提出|no\s+prior\s+research|world-first|unprecedented", re.I)
OVERBROAD_RE = re.compile(r"解释所有影响因素|解决全部(?:科学)?问题|涵盖所有可能因素|全面彻底解决所有|explain\s+all\s+(?:possible\s+)?factors|solve\s+all\s+problems", re.I)
AI_CLICHE_RE = re.compile(r"综上所述|总的来说|由此可见|不难发现|众所周知|值得注意的是|显著赋能|深刻揭示了|为未来研究奠定(?:了)?(?:坚实)?基础|提供(?:了)?新思路", re.I)
DEFENSIVE_OPEN_RE = re.compile(r"^(?:遗憾的是|受限于|尽管本研究存在|虽然本方案|本文仅|本研究仅|无法证明|Unfortunately,\s)", re.I)
STUB_RE = re.compile(r"\b(?:TODO|TBD|FIXME)\b|待补充|待撰写|lorem\s+ipsum|\[insert\s+[^\]]+\]", re.I)
GAP_TAG_RE = re.compile(r"\[EVIDENCE_GAP[^\]]*\]|\[待确认[^\]]*\]|\[待补充[^\]]*\]|\[\[EVIDENCE:[^\]]*\]\]", re.I)
NEG_BEFORE_RE = re.compile(r"(?:并非|不是|避免|拒绝|不得|严禁|禁止|不宣称|不声称|而非|取代|not\s+|never\s+|avoid\s+|rather\s+than\s+|must\s+not\s+)", re.I)
NEG_AFTER_RE = re.compile(r"(?:属于空泛|不可取|应避免|并非真实|not\s+claimed|is\s+avoided)", re.I)
CONTRAST_RE = re.compile(r"(?:但是|然而|但|不过|however|but\s+)", re.I)
SENT_SPLIT_RE = re.compile(r"[。；;！？!\n]+")

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
PATENT_RE = re.compile(r"\b(?:CN|US|EP|JP|KR|WO)\s*\d{6,12}\s*[A-Z]\d?\b")
FACT_NUM_RE = re.compile(r"(?:\bn\s*=\s*\d[\d,]*|\d+(?:[.,]\d+)?\s*%|\bp\s*[<=>]\s*0?\.\d+|95%\s*CI\s*[\d.\-–~]+|\d+(?:[.~–\-]\d+)?\s*(?:mol/L|mA/cm2|rpm|℃|°C|MPa|mm|h|V|例|篇|项|个月|年))", re.I)
ANCHOR_TOK_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|(?:CN|US|EP|JP|KR|WO)\s*\d{6,12}\s*[A-Z]\d?|\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?%|[A-Za-z][A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{3,8}", re.I)
PLANNED_REF_RE = re.compile(r"(?:planned|proposed|case\.json|evidence_gap|gap|pending|tbd|拟开展|待确认|任务单|计划书)", re.I)
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".html", ".xml"}
CN_NUM_MAP = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _is_finite(val: Any) -> bool:
    if isinstance(val, bool) or val is None:
        return False
    try:
        return math.isfinite(float(val))
    except (TypeError, ValueError):
        return False


def _eff_units(han: int, en: int) -> int:
    return han + round(en * 1.6)


def _decode_pdf_lit(raw: bytes) -> str:
    out, i, n = bytearray(), 0, len(raw)
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < n:
        if raw[i] == 92 and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in esc:
                out.append(esc[nxt]); i += 2
            elif 48 <= nxt <= 55:
                m = re.match(rb"[0-7]{1,3}", raw[i + 1 : i + 4])
                out.append(int(m.group(0), 8) & 0xFF if m else nxt)
                i += 1 + (len(m.group(0)) if m else 1)
            else:
                out.append(nxt); i += 2
        else:
            out.append(raw[i]); i += 1
    for enc in ("utf-8", "latin-1"):
        try:
            return out.decode(enc)
        except UnicodeDecodeError:
            pass
    return out.decode("latin-1", errors="ignore")


def _extract_pdf_zlib(pdf_path: Path) -> str:
    data = pdf_path.read_bytes()
    chunks: list[str] = []

    def _scan(buf: bytes) -> None:
        for bt in re.findall(rb"BT[\s\S]*?ET", buf):
            parts: list[str] = []
            for tok in re.finditer(rb"\((?:\\.|[^\\()])*\)|<([0-9A-Fa-f\s]{4,})>", bt):
                if tok.group(1) is not None:
                    hx = re.sub(rb"\s+", b"", tok.group(1))
                    if len(hx) % 2:
                        hx += b"0"
                    try:
                        rh = bytes.fromhex(hx.decode("ascii"))
                    except ValueError:
                        continue
                    t = rh[2:].decode("utf-16-be", errors="ignore") if rh.startswith(b"\xfe\xff") else (rh.decode("utf-16-be", errors="ignore") if len(rh) >= 2 and rh[0] == 0 else rh.decode("latin-1", errors="ignore"))
                    if t.strip():
                        parts.append(t)
                else:
                    parts.append(_decode_pdf_lit(tok.group(0)[1:-1]))
            j = re.sub(r"[ \t]+", " ", "".join(parts)).strip()
            if len(j) >= 2 and (not chunks or chunks[-1] != j):
                chunks.append(j)

    _scan(data)
    for m in re.finditer(rb"stream\r?\n([\s\S]*?)\r?\nendstream", data):
        for wb in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                _scan(zlib.decompress(m.group(1), wb))
                break
            except Exception:
                continue
    return "\n".join(chunks)


def _extract_docx_ooxml(docx_path: Path) -> str:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(docx_path, "r") as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find("w:body", ns)
    if body is None:
        return ""
    lines: list[str] = []
    for ch in list(body):
        tag = ch.tag.split("}")[-1]
        if tag == "p":
            se = ch.find("w:pPr/w:pStyle", ns)
            sv = (se.attrib.get(f"{{{ns['w']}}}val", "") if se is not None else "").lower()
            para = "".join(t.text or "" for t in ch.findall(".//w:t", ns)).strip()
            if not para:
                continue
            if "heading1" in sv or sv in {"1", "标题 1"}:
                lines.append(f"# {para}")
            elif "heading2" in sv or sv in {"2", "标题 2"}:
                lines.append(f"## {para}")
            elif "heading3" in sv or sv in {"3", "标题 3"}:
                lines.append(f"### {para}")
            else:
                lines.append(para)
        elif tag == "tbl":
            rows = []
            for tr in ch.findall("w:tr", ns):
                rc = [" ".join("".join(t.text or "" for t in p.findall(".//w:t", ns)).strip() for p in tc.findall(".//w:p", ns)).strip().replace("|", "/") for tc in tr.findall("w:tc", ns)]
                if any(rc):
                    rows.append(rc)
            if rows:
                nc = max(len(r) for r in rows)
                for ri, r in enumerate(rows):
                    lines.append("| " + " | ".join(r + [""] * (nc - len(r))) + " |")
                    if ri == 0:
                        lines.append("| " + " | ".join(["---"] * nc) + " |")
    return "\n".join(lines)


def _read_doc(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext == ".docx":
        try:
            return _extract_docx_ooxml(path), "stdlib_ooxml"
        except Exception:
            return "", "docx_error"
    if ext == ".pdf":
        bin_p = shutil.which("pdftotext")
        if bin_p:
            try:
                cp = subprocess.run([bin_p, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
                if cp.returncode == 0 and cp.stdout.strip():
                    return cp.stdout, "pdftotext"
            except Exception:
                pass
        for mod in ("pdfplumber", "pypdf"):
            try:
                if mod == "pdfplumber":
                    import pdfplumber  # type: ignore
                    with pdfplumber.open(str(path)) as d:
                        t = "\n".join((pg.extract_text() or "") for pg in d.pages)
                else:
                    from pypdf import PdfReader  # type: ignore
                    t = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
                if t.strip():
                    return t, mod
            except Exception:
                continue
        return _extract_pdf_zlib(path), "stdlib_pdf_zlib"
    return path.read_text(encoding="utf-8", errors="replace"), "utf8_text"


def _safe_load_json(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if not path or not path.is_file():
        return {}, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}, f"{path.name}: expected JSON object, got {type(raw).__name__}"
        return raw, None
    except Exception as exc:
        return {}, f"{path.name}: JSON parse error ({exc})"


def _is_negated(line: str, pos: int, end: int | None = None) -> bool:
    mend = end if end is not None else pos
    lb = 0
    for m in SENT_SPLIT_RE.finditer(line, 0, pos):
        lb = m.end()
    mr = SENT_SPLIT_RE.search(line, mend)
    sent = line[lb : (mr.start() if mr else len(line))]
    rs, re_ = pos - lb, mend - lb
    before = sent[max(0, rs - 70) : rs]
    cm = list(CONTRAST_RE.finditer(before))
    if cm:
        before = before[cm[-1].end() :]
    if NEG_BEFORE_RE.search(before):
        return True
    after = sent[re_ : min(len(sent), re_ + 40)]
    return bool(NEG_AFTER_RE.search(after))


def extract_plan_seed(input_dir: Path, case_json_path: Path | None = None) -> dict[str, Any]:
    """Scan /input (PDF/DOCX/MD/TXT/JSON/CSV) and generate a grounded constraints & proposal_plan seed."""
    c_path = case_json_path if (case_json_path and case_json_path.is_file()) else (input_dir / "case.json" if (input_dir / "case.json").is_file() else None)
    case_cfg, case_err = _safe_load_json(c_path)
    raw_tm = case_cfg.get("total_months")
    if _is_finite(raw_tm) and int(raw_tm) > 0:
        total_months: int | None = int(raw_tm)
        total_months_source = "supplied_in_case_json"
        needs_human_confirmation = False
    else:
        total_months = None
        total_months_source = "missing_in_input"
        needs_human_confirmation = True

    cb_raw = case_cfg.get("budget_limit")
    budget_limit = float(cb_raw) if _is_finite(cb_raw) and float(cb_raw) > 0 else None
    pw_raw = case_cfg.get("max_parallel_workload")
    max_parallel_workload = float(pw_raw) if _is_finite(pw_raw) and float(pw_raw) > 0 else None
    unavail: list[str] = []
    for k in ("unavailable_resources", "missing_samples", "unavailable_samples"):
        v = case_cfg.get(k)
        if isinstance(v, list):
            unavail.extend(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, str) and v.strip():
            unavail.append(v.strip())

    files_meta, numeric_facts, seed_citations, unparsed_pdfs, gap_cues = [], [], [], [], []
    dois, patents = set(), set()
    scan = [input_dir] if input_dir.is_file() else sorted(p for p in input_dir.rglob("*") if p.is_file())
    for fp in scan:
        ext = fp.suffix.lower()
        if ext not in TEXT_EXTS and ext not in {".pdf", ".docx"}:
            continue
        rel = fp.name if input_dir.is_file() else fp.relative_to(input_dir).as_posix()
        txt, extractor = _read_doc(fp)
        lines = txt.splitlines()
        if ext == ".pdf" and len(txt.strip()) < 20:
            unparsed_pdfs.append(rel)
        files_meta.append({"file": rel, "ext": ext, "bytes": fp.stat().st_size, "sha256_prefix": hashlib.sha256(fp.read_bytes()).hexdigest()[:16], "char_count": len(txt), "line_count": len(lines), "extractor": extractor})
        dois.update(DOI_RE.findall(txt))
        patents.update(re.sub(r"\s+", "", p) for p in PATENT_RE.findall(txt))
        if fp.name.lower() == "case.json":
            continue
        page = 0
        for lno, ln in enumerate(lines, 1):
            st = ln.strip()
            if not st:
                continue
            pm = re.match(r"^##\s*\[Page\s+(\d+)\]", st, re.I)
            if pm:
                page = int(pm.group(1)); continue
            ref = f"{rel}#L{lno}" + (f" (Page {page})" if page else "")
            if UNKNOWN_RE.search(st) and len(st) >= 20 and len(gap_cues) < 8:
                gap_cues.append({"source_anchor": ref, "excerpt": st[:180]})
            nums = FACT_NUM_RE.findall(st)
            if nums and len(st) >= 18 and len(numeric_facts) < 200:
                numeric_facts.append({"fact_id": f"F{len(numeric_facts)+1:02d}", "source_ref": ref, "numbers": nums[:6], "excerpt": st[:200]})
                if len(seed_citations) < 20:
                    seed_citations.append({"claim_id": f"C{len(seed_citations)+1}", "claim_category": "external_fact", "claim": st[:150], "section": "研究背景与现状缺口", "source_ref": ref})

    if total_months is not None:
        m1, m2, m3 = max(1, total_months // 3), max(2, (2 * total_months) // 3), total_months
        cp_label = f"M{m1}"
    else:
        m1 = m2 = m3 = None
        cp_label = "[EVIDENCE_GAP: confirm total_months]"

    rg_seed = [{"gap_id": f"RG{i}", "resource": res, "scope_adjustment": "Narrow scope to available data/equipment", "trigger_signal": f"{res} unavailable by {cp_label}", "decision_checkpoint": cp_label, "fallback_plan": f"Execute verified proxy protocol and mark {res} as [EVIDENCE_GAP]"} for i, res in enumerate(unavail, 1)]
    hc_seed = []
    if total_months is None:
        hc_seed.append({"confirm_id": f"HC{len(hc_seed)+1}", "item": "Confirm total project duration total_months with advisor/funder (missing_in_input)"})
    if budget_limit is None:
        hc_seed.append({"confirm_id": f"HC{len(hc_seed)+1}", "item": "Confirm actual project budget ceiling with advisor/funder (not supplied in /input)"})
    if unavail:
        hc_seed.append({"confirm_id": f"HC{len(hc_seed)+1}", "item": f"Confirm access timeline or replacement scope for unavailable resource(s): {unavail}"})
    if unparsed_pdfs:
        hc_seed.append({"confirm_id": f"HC{len(hc_seed)+1}", "item": f"Manually verify scanned/unparsed PDF(s): {unparsed_pdfs}"})

    # Pre-populate grounded constraints and candidate RQs/objectives from extracted facts & gaps
    cand_rqs = []
    for idx_g, g in enumerate(gap_cues[:2], 1):
        cand_rqs.append({
            "rq_id": f"RQ{idx_g}",
            "question": f"Refine falsifiable question grounded on {g['source_anchor']}: {g['excerpt'][:90]}",
            "falsifiable_criterion": "Quantify measurable rejection threshold from /input baseline",
            "source_anchor": g["source_anchor"],
        })
    if not cand_rqs:
        src_anc = numeric_facts[0]["source_ref"] if numeric_facts else "input_corpus"
        cand_rqs.append({"rq_id": "RQ1", "question": f"Converged question anchored to {src_anc}", "falsifiable_criterion": "Quantified rejection threshold", "source_anchor": src_anc})

    cand_objs = []
    for idx_f, nf in enumerate(numeric_facts[:2], 1):
        cand_objs.append({
            "objective_id": f"O{idx_f}",
            "rq_id": cand_rqs[min(idx_f - 1, len(cand_rqs) - 1)]["rq_id"],
            "title": f"Grounded objective derived from {nf['source_ref']}",
            "source_anchor": nf["source_ref"],
            "grounded_baseline_excerpt": nf["excerpt"][:120],
            "metric": f"Quantitative metric tied to {nf['numbers'][0] if nf['numbers'] else 'baseline'}",
            "target_value": "Specify verifiable threshold or [EVIDENCE_GAP]",
            "sample_or_unit": "Specify available sample/dataset from /input",
            "control_or_baseline": f"Baseline from {nf['source_ref']}",
            "checkpoint_month": m1 if idx_f == 1 else m2,
            "planned_figure": f"Fig. {idx_f}",
        })
    if not cand_objs:
        cand_objs.append({"objective_id": "O1", "rq_id": "RQ1", "metric": "Quantified metric", "target_value": "Threshold", "sample_or_unit": "Available unit", "control_or_baseline": "Baseline", "checkpoint_month": m1, "planned_figure": "Fig. 1"})

    grounded_constraints = {
        "total_months": {"value": total_months, "source": total_months_source, "needs_human_confirmation": needs_human_confirmation},
        "budget_limit": {"value": budget_limit, "source": "supplied_in_case_json" if budget_limit is not None else "not_supplied_in_input"},
        "max_parallel_workload": {"value": max_parallel_workload, "source": "supplied_in_case_json" if max_parallel_workload is not None else "missing_in_input"},
        "unavailable_resources": unavail,
        "extracted_gap_anchors": gap_cues,
    }

    return {
        "schema_version": "proposal-plan-seed/v2",
        "input_dir": str(input_dir),
        "case_parse_error": case_err,
        "files_indexed": files_meta,
        "unparsed_pdfs": unparsed_pdfs,
        "identifiers": {"dois": sorted(dois), "patents": sorted(patents)},
        "grounded_constraints": grounded_constraints,
        "numeric_facts": numeric_facts,
        "plan_seed": {
            "case_id": str(case_cfg.get("case_id") or "unknown"),
            "total_months": total_months,
            "total_months_source": total_months_source,
            "needs_human_confirmation": needs_human_confirmation,
            "budget_limit": budget_limit,
            "budget_limit_source": "supplied_in_case_json" if budget_limit is not None else "not_supplied_in_input",
            "max_parallel_workload": max_parallel_workload,
            "research_questions": cand_rqs,
            "objectives": cand_objs,
            "route_steps": [
                {"step_id": "S1", "objective_id": cand_objs[0]["objective_id"], "start_month": 1 if total_months else None, "end_month": m1, "depends_on": [], "effort_units": 1.0, "pending_duration_confirmation": total_months is None},
                {"step_id": "S2", "objective_id": cand_objs[0]["objective_id"], "start_month": m1, "end_month": m2, "depends_on": ["S1"], "effort_units": 1.0, "pending_duration_confirmation": total_months is None},
                {"step_id": "S3", "objective_id": cand_objs[-1]["objective_id"], "start_month": m2, "end_month": m3, "depends_on": ["S2"], "effort_units": 1.0, "pending_duration_confirmation": total_months is None},
            ],
            "milestones": [{"milestone_id": "M1", "objective_id": cand_objs[0]["objective_id"], "month": m1}, {"milestone_id": "M2", "objective_id": cand_objs[0]["objective_id"], "month": m2}, {"milestone_id": "M3", "objective_id": cand_objs[-1]["objective_id"], "month": m3}],
            "resource_gaps": rg_seed,
            "risks": [{"risk_id": f"R{i}", "category": cat, "description": f"Specify {cat} risk grounded on /input", "trigger_signal": f"Observable trigger at {cp_label}", "decision_checkpoint": cp_label, "fallback_plan": "Concrete fallback action"} for i, cat in enumerate(REQUIRED_RISK_TYPES, 1)],
            "claim_citations": seed_citations,
            "human_confirmations": hc_seed,
        },
    }


def _parse_sections(text: str) -> tuple[dict[str, dict[str, Any]], str]:
    lines = text.splitlines()
    raw_secs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    md_h = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
    cn_h = re.compile(r"^(?:([一二三四五六七八九十]+、\s*.{2,42})|([（(][一二三四五六七八九十\d]+[）)]\s*.{2,42})|(\d+(?:\.\d+){0,2}[\.、\s]\s*[^\s。；]{2,40})|\*\*([一二三四五六七八九十\d（(][^\n]{2,42}?)\*\*)\s*$")

    for lno, ln in enumerate(lines, 1):
        st = ln.strip()
        if not st or st.startswith("|"):
            if cur is not None:
                cur["lines"].append((lno, ln))
            continue
        mm = md_h.match(st)
        mp = None if mm else cn_h.match(st)
        if mp:
            ct = (mp.group(1) or mp.group(2) or mp.group(3) or mp.group(4) or "").strip()
            if len(ct) > 42 or ct.endswith(("。", ".", "；", ";", "，", ",")) or re.match(r"^(?:19|20)\d{2}\b", ct):
                mp = None
            elif not any(any(k in ct.lower() for k in kws) for _, kws in REQUIRED_SECTIONS) and not re.match(r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]|\d+\.\d+)", ct):
                mp = None
        if mm or mp:
            lvl = len(mm.group(1)) if mm else 2
            title = mm.group(2).strip() if mm else (mp.group(1) or mp.group(2) or mp.group(3) or mp.group(4) or st).strip()  # type: ignore[union-attr]
            if lvl == 1 and not any(any(k in title.lower() for k in kws) for _, kws in REQUIRED_SECTIONS):
                continue
            if cur is not None:
                raw_secs.append(cur)
            cur = {"title": title, "start_line": lno, "lines": []}
        elif cur is not None:
            cur["lines"].append((lno, ln))
    if cur is not None:
        raw_secs.append(cur)

    def _entry(title: str, start_line: int, blines: list[str]) -> dict[str, Any]:
        bt = "\n".join(blines).strip()
        prose = [l.strip() for l in blines if l.strip() and not l.strip().startswith(("|", "```"))]
        tbl = [" ".join(c.strip() for c in l.strip().strip("|").split("|")) for l in blines if l.strip().startswith("|") and not all(re.fullmatch(r":?-{2,}:?", c.strip() or "---") for c in l.strip().strip("|").split("|"))]
        full = "\n".join(prose + tbl)
        han, en = len(HAN_RE.findall(full)), len(WORD_RE.findall(full))
        return {"heading": title, "start_line": start_line, "han_chars": han, "en_words": en, "effective_units": _eff_units(han, en), "body_text": bt, "first_prose_line": prose[0] if prose else "", "has_stub": bool(STUB_RE.search(bt))}

    found: dict[str, dict[str, Any]] = {}
    for sec in raw_secs:
        ent = _entry(sec["title"], sec["start_line"], [l for _, l in sec["lines"]])
        lt, op120 = sec["title"].lower(), ent["body_text"][:120].lower()
        bk, bw = None, 0
        for key, kws in REQUIRED_SECTIONS:
            if key in found:
                continue
            w = (3 if any(k in lt for k in kws) else 0) + (1 if any(k in op120 for k in kws) else 0)
            if w > bw:
                bk, bw = key, w
        if bk and bw >= 1:
            found[bk] = ent

    mode = "headings" if raw_secs else "paragraph_semantic"
    if len(found) < len(REQUIRED_SECTIONS):
        paras: list[tuple[int, list[str]]] = []
        ps, pl = 1, []
        for lno, ln in enumerate(lines, 1):
            st = ln.strip()
            if not st or st.startswith("```"):
                if pl:
                    paras.append((ps, pl)); pl = []
            elif st.startswith("|"):
                # Attach table lines to the immediately preceding paragraph block
                if pl:
                    pl.append(ln)
                elif paras:
                    paras[-1][1].append(ln)
            else:
                if not pl:
                    ps = lno
                pl.append(ln)
        if pl:
            paras.append((ps, pl))
        # Merge adjacent background known/unknown/verify paragraphs if all belong to background
        used_p, mapped_starts, added = set(), {v["start_line"] for v in found.values()}, False
        cand_pairs: list[tuple[int, str, int]] = []
        for key, kws in REQUIRED_SECTIONS:
            if key in found:
                continue
            cues = SEMANTIC_CUES.get(key, ())
            for idx, (p_start, p_lines) in enumerate(paras):
                if p_start in mapped_starts:
                    continue
                pt = "\n".join(p_lines)
                if len(pt.strip()) < 20:
                    continue
                plw = pt.lower()
                sc = sum(3 for k in kws if k in plw) + sum(1 for c in cues if c.lower() in plw)
                if sc >= 2:
                    cand_pairs.append((sc, key, idx))
        cand_pairs.sort(key=lambda x: (-x[0], x[2]))
        assigned_idx: dict[str, int] = {}
        for _, key, idx in cand_pairs:
            if key in found or idx in used_p:
                continue
            used_p.add(idx); assigned_idx[key] = idx
            found[key] = _entry(f"[semantic:{key}]", paras[idx][0], paras[idx][1]); added = True
        # Expand background_gap with adjacent unassigned paragraphs containing 已知/未知/待验证
        if "background_gap" in assigned_idx:
            bi = assigned_idx["background_gap"]
            bg_lines = list(paras[bi][1])
            for ni in range(max(0, bi - 2), min(len(paras), bi + 3)):
                if ni == bi or ni in used_p:
                    continue
                nt = "\n".join(paras[ni][1])
                if KNOWN_RE.search(nt) or UNKNOWN_RE.search(nt) or VERIFY_RE.search(nt):
                    bg_lines.extend([""] + paras[ni][1])
                    used_p.add(ni)
            found["background_gap"] = _entry("[semantic:background_gap]", paras[bi][0], bg_lines)
        if added and raw_secs:
            mode = "headings+semantic_fallback"
    return found, mode


def _compute_cpm(steps: list[dict[str, Any]], step_map: dict[str, dict[str, Any]], total_months: int, topo: list[str]) -> dict[str, Any]:
    """Compute Critical Path Method anchored to declared start_month/end_month and monthly parallel workload."""
    if not topo:
        return {"critical_path_steps": [], "critical_path_months": 0, "step_schedule": {}, "peak_parallel_steps": 0, "peak_parallel_month": None, "peak_monthly_workload": 0.0, "monthly_workload": {}, "parallelizable_pairs": []}
    dur, es, ef = {}, {}, {}
    succ: dict[str, list[str]] = {sid: [] for sid in topo}
    for sid in topo:
        s = step_map[sid]
        sm, em = int(s["start_month"]), int(s["end_month"])
        dur[sid] = max(1, em - sm + 1)
        deps = [d for d in (s.get("depends_on") or []) if d in step_map]
        for d in deps:
            succ[d].append(sid)
        # Anchor ES to declared start_month - 1 and predecessor finish (respecting shared boundary month)
        declared_es = max(0, sm - 1)
        if not deps:
            es[sid] = declared_es
        else:
            dep_starts = [max(0, ef[d] - (1 if sm == int(step_map[d]["end_month"]) else 0)) for d in deps]
            es[sid] = max(declared_es, max(dep_starts))
        ef[sid] = es[sid] + dur[sid]

    cpm_len = max(ef.values()) if ef else 0
    lf, ls = {}, {}
    for sid in reversed(topo):
        if not succ[sid]:
            lf[sid] = cpm_len
        else:
            s_end = int(step_map[sid]["end_month"])
            lf[sid] = min(ls[nxt] + (1 if int(step_map[nxt]["start_month"]) == s_end else 0) for nxt in succ[sid])
        ls[sid] = lf[sid] - dur[sid]
    slack = {sid: ls[sid] - es[sid] for sid in topo}
    crit_steps = [sid for sid in topo if slack[sid] == 0]

    horizon = max(total_months, max((int(s["end_month"]) for s in steps if _is_finite(s.get("end_month"))), default=0))
    m_active: dict[int, list[str]] = {m: [] for m in range(1, horizon + 1)}
    m_wl: dict[str, dict[str, Any]] = {}
    pk_s, pk_m, pk_w = 0, None, 0.0
    for m in range(1, horizon + 1):
        act, wl = [], 0.0
        for s in steps:
            if int(s["start_month"]) <= m <= int(s["end_month"]):
                act.append(str(s.get("step_id", "")))
                eff = s.get("effort_units", s.get("fte", 1.0))
                wl += float(eff) if _is_finite(eff) and float(eff) > 0 else 1.0
        m_active[m] = act
        wl = round(wl, 3)
        m_wl[f"M{m}"] = {"steps": act, "count": len(act), "workload": wl}
        if len(act) > pk_s or (len(act) == pk_s and wl > pk_w):
            pk_s, pk_m = len(act), m
        if wl > pk_w:
            pk_w = wl
    pairs = {tuple(sorted((a[i], a[j]))) for a in m_active.values() for i in range(len(a)) for j in range(i + 1, len(a))}
    return {
        "critical_path_steps": crit_steps, "critical_path_months": cpm_len,
        "step_schedule": {sid: {"duration": dur[sid], "ES": es[sid], "EF": ef[sid], "LS": ls[sid], "LF": lf[sid], "slack": slack[sid]} for sid in topo},
        "peak_parallel_steps": pk_s, "peak_parallel_month": pk_m, "peak_monthly_workload": round(pk_w, 3),
        "monthly_workload": m_wl, "parallelizable_pairs": [list(p) for p in sorted(pairs)],
    }


def _reconcile_prose(
    text: str,
    plan: dict[str, Any],
    total_months: int | None,
    budget_sum: float,
    budget_limit: float | None,
    cpm: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mm: list[dict[str, Any]] = []
    tm = total_months or 0
    if tm > 0:
        for m in re.finditer(r"(?:总周期|研究周期|计划周期|实施周期|总时长|项目周期|为期)\s*[:：为约共]?\s*(\d{1,3})\s*个?月|\b(\d{1,3})-month\s+(?:timeline|schedule|duration|project)\b", text, re.I):
            val = int(m.group(1) or m.group(2))
            if val != tm:
                mm.append({"code": "PROSE_PLAN_MISMATCH", "field": "total_months", "prose_value": val, "json_value": tm, "excerpt": m.group(0)})
        for m in re.finditer(r"(?:总周期|研究周期|计划周期|实施周期|项目周期|为期)\s*[:：为约共]?\s*([一二两三四五六]|\d+(?:\.\d+)?)\s*年", text):
            raw_y = m.group(1)
            yrs = float(CN_NUM_MAP[raw_y]) if raw_y in CN_NUM_MAP else float(raw_y)
            val_m = round(yrs * 12)
            if val_m != tm:
                mm.append({"code": "PROSE_PLAN_MISMATCH", "field": "total_months_year_synonym", "prose_value": f"{raw_y}年({val_m}m)", "json_value": tm, "excerpt": m.group(0)})

    # Budget total & limit reconciliation
    for pat, field, target in (
        (r"(?:预算合计|经费合计|总预算|总经费|预算总计)\s*[:：为约]?\s*(?:人民币|RMB|¥|\$)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(万)?", "budget_sum", budget_sum),
        (r"(?:预算上限|经费上限|资助上限)\s*[:：为约]?\s*(?:人民币|RMB|¥|\$)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(万)?", "budget_limit", budget_limit or 0.0),
    ):
        if target <= 0:
            continue
        for m in re.finditer(pat, text, re.I):
            rn = float(m.group(1).replace(",", ""))
            val = rn * 10000.0 if m.group(2) else rn
            if abs(val - target) > max(1.0, target * 0.01) and abs(rn - target) > max(0.05, target * 0.01):
                mm.append({"code": "PROSE_PLAN_MISMATCH", "field": field, "prose_value": m.group(0), "json_value": target, "excerpt": m.group(0)})

    # Individual budget items presence & sum consistency check when budget_items are defined
    b_items = [b for b in (plan.get("budget_items") or []) if isinstance(b, dict) and _is_finite(b.get("amount"))]
    if b_items and budget_sum > 0:
        # Check if prose mentions budget_sum (either formatted with commas or plain)
        sum_int = str(int(round(budget_sum)))
        sum_comma = f"{int(round(budget_sum)):,}"
        if sum_int not in text and sum_comma not in text:
            mm.append({"code": "PROSE_PLAN_MISMATCH", "field": "budget_sum_missing_in_prose", "prose_value": "not_found", "json_value": budget_sum})

    # RQ IDs reconciliation
    rq_ids = [str(r.get("rq_id", "")).strip().upper() for r in plan.get("research_questions", []) if isinstance(r, dict) and r.get("rq_id")]
    if rq_ids:
        miss_rq = [rq for rq in rq_ids if not re.search(rf"\b{re.escape(rq)}\b", text, re.I)]
        if miss_rq:
            mm.append({"code": "PROSE_PLAN_MISMATCH", "field": "research_question_ids", "prose_value": "missing_in_prose", "json_value": rq_ids, "missing_in_prose": miss_rq})

    # Objective IDs reconciliation
    oids = [str(o.get("objective_id", "")).strip().upper() for o in plan.get("objectives", []) if isinstance(o, dict) and o.get("objective_id")]
    if oids:
        miss = [oid for oid in oids if not re.search(rf"\b{re.escape(oid)}\b", text, re.I)]
        if miss:
            mm.append({"code": "PROSE_PLAN_MISMATCH", "field": "objective_ids", "prose_value": "missing_in_prose", "json_value": oids, "missing_in_prose": miss})

    # Per-step CPM ES/EF/LS/LF reconciliation against proposal.md
    step_sched = (cpm or {}).get("step_schedule") if isinstance(cpm, dict) else None
    if isinstance(step_sched, dict) and step_sched:
        # Parse markdown tables that have ES, EF, LS, LF columns OR inline ES=x/EF=y/LS=z/LF=w
        prose_cpm: dict[str, dict[str, int]] = {}
        lines = text.splitlines()
        idx_l = 0
        while idx_l < len(lines):
            st = lines[idx_l].strip()
            if st.startswith("|") and st.endswith("|"):
                tbl_rows = []
                while idx_l < len(lines) and lines[idx_l].strip().startswith("|") and lines[idx_l].strip().endswith("|"):
                    cells = [c.strip() for c in lines[idx_l].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                        tbl_rows.append(cells)
                    idx_l += 1
                if len(tbl_rows) >= 2:
                    hdr = [h.upper() for h in tbl_rows[0]]
                    col_es = next((i for i, h in enumerate(hdr) if re.search(r"\bES\b", h)), None)
                    col_ef = next((i for i, h in enumerate(hdr) if re.search(r"\bEF\b", h)), None)
                    col_ls = next((i for i, h in enumerate(hdr) if re.search(r"\bLS\b", h)), None)
                    col_lf = next((i for i, h in enumerate(hdr) if re.search(r"\bLF\b", h)), None)
                    if all(c is not None for c in (col_es, col_ef, col_ls, col_lf)):
                        for r in tbl_rows[1:]:
                            row_j = " ".join(r)
                            sm = re.search(r"\b(S\d+)\b", row_j, re.I)
                            if sm:
                                sid = sm.group(1).upper()
                                try:
                                    def _gi(ci: int) -> int:
                                        m_num = re.search(r"\d+", r[ci])
                                        return int(m_num.group(0)) if m_num else -999
                                    prose_cpm[sid] = {
                                        "ES": _gi(col_es),  # type: ignore[arg-type]
                                        "EF": _gi(col_ef),  # type: ignore[arg-type]
                                        "LS": _gi(col_ls),  # type: ignore[arg-type]
                                        "LF": _gi(col_lf),  # type: ignore[arg-type]
                                    }
                                except Exception:
                                    pass
                continue
            # Inline pattern e.g. S1 ... ES=0, EF=3, LS=0, LF=3
            im = re.search(r"\b(S\d+)\b[^\n]{0,80}\bES\s*[:=]\s*(\d+)[^\n]{0,20}\bEF\s*[:=]\s*(\d+)[^\n]{0,20}\bLS\s*[:=]\s*(\d+)[^\n]{0,20}\bLF\s*[:=]\s*(\d+)", st, re.I)
            if im:
                prose_cpm[im.group(1).upper()] = {"ES": int(im.group(2)), "EF": int(im.group(3)), "LS": int(im.group(4)), "LF": int(im.group(5))}
            idx_l += 1

        for sid, exp in step_sched.items():
            sid_u = str(sid).upper()
            got = prose_cpm.get(sid_u)
            if got is None:
                mm.append({"code": "PROSE_PLAN_MISMATCH", "field": f"cpm_step_{sid_u}_missing_in_prose", "prose_value": "missing_ES_EF_LS_LF", "json_value": {k: exp[k] for k in ("ES", "EF", "LS", "LF")}})
            else:
                diffs = {k: {"prose": got[k], "json": exp[k]} for k in ("ES", "EF", "LS", "LF") if got.get(k) != exp.get(k)}
                if diffs:
                    mm.append({"code": "PROSE_PLAN_MISMATCH", "field": f"cpm_step_{sid_u}_values", "prose_value": got, "json_value": {k: exp[k] for k in ("ES", "EF", "LS", "LF")}, "diffs": diffs})

    if tm > 0:
        for m in re.finditer(r"(?:里程碑|节点|Milestone)\s*[A-Za-z0-9_-]*\s*[:：（(]?\s*(?:M|第\s*)(\d{1,3})\s*(?:个?月)?", text, re.I):
            mv = int(m.group(1))
            if mv > tm:
                mm.append({"code": "PROSE_PLAN_MISMATCH", "field": "milestone_month_in_prose", "prose_value": mv, "json_value": f"<= {tm}", "excerpt": m.group(0)})
    return mm


def _collect_corpus(corpus_path: Path | None, exclude: set[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    if not corpus_path or not corpus_path.exists():
        return [], []
    excl = {p.resolve() for p in exclude if p}
    entries, unparsed_pdfs = [], []
    files = [corpus_path] if corpus_path.is_file() else sorted(p for p in corpus_path.rglob("*") if p.is_file())
    for fp in files:
        if fp.resolve() in excl or any(k in fp.name.lower() for k in ("audit", "contract_helper", "proposal_plan", "proposal_preview")):
            continue
        ext = fp.suffix.lower()
        if ext not in TEXT_EXTS and ext not in {".pdf", ".docx"}:
            continue
        txt, extractor = _read_doc(fp)
        rel = fp.name if corpus_path.is_file() else fp.relative_to(corpus_path).as_posix()
        if ext == ".pdf" and len(txt.strip()) < 20:
            unparsed_pdfs.append(rel); continue
        if not txt.strip():
            continue
        low = txt.lower()
        sha_p = hashlib.sha256(fp.read_bytes()).hexdigest()[:16]
        lines = txt.splitlines()
        page_map: dict[int, int] = {}
        cur_p = 1
        for i_ln, raw_ln in enumerate(lines, 1):
            pm = re.match(r"^##\s*\[Page\s+(\d+)\]", raw_ln.strip(), re.I)
            if pm:
                cur_p = int(pm.group(1))
            page_map[i_ln] = cur_p
        entries.append({"file": rel, "name_l": fp.name.lower(), "stem_l": fp.stem.lower(), "sha256_prefix": sha_p, "extractor": extractor, "lines": lines, "page_map": page_map, "full_text": txt, "full_l": low, "cmp_l": re.sub(r"\s+", "", low)})
    return entries, unparsed_pdfs


_PROP_STRUCT_STRIP_RE = re.compile(r"(?:#L\d+(?:[-–]L?\d+)?|\bPage\s*\d+\b|\bLine\s*\d+\b|\b(?:RQ|CLM|RG|HC|Fig|Table|Tab)\s*\.?\s*\d+\b|\b[COSM]\d+\b|95%\s*CI)", re.I)
_PROP_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.#])(\d+(?:,\d{3})*(?:\.\d+)?)(%)?", re.I)


def _prop_canon_num(raw: str) -> str:
    s = raw.replace(",", "").strip()
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _prop_nums_consistent(claim: str, window_text: str) -> tuple[bool, list[str]]:
    c_clean = DOI_RE.sub(" ", PATENT_RE.sub(" ", _PROP_STRUCT_STRIP_RE.sub(" ", claim)))
    c_nums = [(_prop_canon_num(m.group(1)), m.group(2) or "") for m in _PROP_NUM_RE.finditer(c_clean) if (m.group(2) or len(_prop_canon_num(m.group(1))) >= 2) and not (not m.group(2) and re.fullmatch(r"(?:19|20)\d{2}", _prop_canon_num(m.group(1))))]
    if not c_nums:
        return True, []
    w_alt = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1\2", window_text)
    w_alt = re.sub(r"(\d),(\d{1,2})(?!\d)", r"\1.\2", w_alt)
    w_all = window_text + " " + w_alt
    w_pairs = {(_prop_canon_num(m.group(1)), m.group(2) or "") for m in _PROP_NUM_RE.finditer(w_all)}
    w_only = {n for n, _ in w_pairs}
    bad = []
    for n, pct in c_nums:
        if pct:
            if (n, "%") not in w_pairs:
                bad.append(f"{n}%")
        elif n not in w_only:
            bad.append(n)
    return len(bad) == 0, bad


def _verify_claim_citations(
    citations: list[Any],
    corpus: list[dict[str, Any]],
    unparsed_pdfs: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vers, ungrounded = [], []
    unparsed_set = [u.lower() for u in (unparsed_pdfs or [])]
    stop_w = {"page", "line", "table", "figure", "section", "results", "methods", "abstract", "introduction", "paper", "source"}
    for idx, row in enumerate(citations, 1):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("claim_id") or f"C{idx}")
        claim, sref = str(row.get("claim") or "").strip(), str(row.get("source_ref") or "").strip()
        raw_cat = str(row.get("claim_category") or row.get("claim_type") or "").strip().lower()
        if not (claim and sref):
            continue
        sl = sref.lower()

        # 1. Strict 3-way classification: external_fact vs plan_assumption vs pending_evidence
        if raw_cat in {"plan_assumption", "planned", "proposed"} or (not raw_cat and PLANNED_REF_RE.search(sref)):
            vers.append({
                "claim_id": cid, "claim_category": "plan_assumption", "source_ref": sref,
                "status": "plan_assumption", "counts_as_verified_citation": False,
                "matched_file": None, "matched_line": None, "verbatim_excerpt": "",
            })
            continue
        if raw_cat in {"pending_evidence", "gap", "evidence_gap"} or re.search(r"\[EVIDENCE_GAP|pending_evidence", sref, re.I):
            vers.append({
                "claim_id": cid, "claim_category": "pending_evidence", "source_ref": sref,
                "status": "pending_evidence", "counts_as_verified_citation": False,
                "matched_file": None, "matched_line": None, "verbatim_excerpt": "",
            })
            continue

        # 2. Scanned/unparsed PDF scoping: ONLY downgrade citations explicitly pointing to an unparsed PDF!
        ref_files = re.findall(r"[\w.-]+\.(?:pdf|md|docx|txt|csv|json)\b", sref, re.I)
        points_to_unparsed = bool(ref_files) and all(
            any(rf.lower() == u or rf.lower() in u or Path(u).name == rf.lower() for u in unparsed_set)
            for rf in ref_files
        )
        ref_ids = DOI_RE.findall(sref + " " + claim) + [re.sub(r"\s+", "", p) for p in PATENT_RE.findall(sref + " " + claim)]
        if points_to_unparsed and not ref_ids:
            vers.append({
                "claim_id": cid, "claim_category": "pending_evidence", "source_ref": sref,
                "status": "unverifiable_scanned_pdf", "counts_as_verified_citation": False,
                "matched_file": ref_files[0], "matched_line": None, "verbatim_excerpt": "",
            })
            continue

        if not corpus:
            rec = {
                "claim_id": cid, "claim_category": "external_fact", "source_ref": sref,
                "status": "ungrounded", "error_code": "CORPUS_NOT_SUPPLIED", "counts_as_verified_citation": False,
                "locator_valid": False, "verbatim_match": False, "numeric_consistent": False, "claim_supported": False,
                "matched_file": None, "matched_line": None, "verbatim_excerpt": "",
            }
            vers.append(rec); ungrounded.append(rec)
            continue

        # Strictly restrict search to explicitly named file(s); NEVER rescue wrong-file citations from another file!
        miss_files = [rf for rf in ref_files if not any(rf.lower() == e["name_l"] or rf.lower() in e["file"].lower() for e in corpus)]
        named_entries = sorted(
            [e for e in corpus if e["name_l"] in sl or e["stem_l"] in sl],
            key=lambda e: (0 if e["name_l"] in sl else 1, -len(e["name_l"])),
        )
        search_pool = named_entries if (ref_files or named_entries) else corpus

        bad_ids = [r for r in ref_ids if not any(re.sub(r"\s+", "", r.lower()) in e["cmp_l"] for e in search_pool)]
        lp_nums = set(re.findall(r"(?:#L|Line\s*|Page\s*|P|表|图|S|O|M)\s*(\d+)", sref, re.I))
        nums = [n for n in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?%?", claim) if (len(n) >= 2 or "%" in n) and n not in lp_nums]
        words = [t for t in ANCHOR_TOK_RE.findall(claim) if len(t) >= 3 and t.lower() not in stop_w]
        anchors: list[str] = []
        for t in ref_ids + nums + words:
            if t.lower() not in {a.lower() for a in anchors}:
                anchors.append(t)
        anchors = anchors[:16]

        exp_lm = re.search(r"#L(\d+)(?:[-–]L?(\d+))?", sref, re.I)
        exp_pm = re.search(r"Page\s*(\d+)", sref, re.I)

        locator_valid = False
        matched_ent: dict[str, Any] | None = None
        matched_line: int | None = None
        matched_page: int | None = None
        window_text = ""
        best_excerpt = ""
        best_hits: list[str] = []

        if not miss_files and not bad_ids:
            if exp_lm and named_entries:
                tln = int(exp_lm.group(1))
                tln_end = int(exp_lm.group(2)) if exp_lm.group(2) else tln
                ent = named_entries[0]
                if 1 <= tln <= len(ent["lines"]):
                    locator_valid = True
                    matched_ent = ent
                    lo = max(1, tln - 2)
                    hi = min(len(ent["lines"]), max(tln_end, tln) + 5)
                    window_text = " ".join(ent["lines"][i - 1].strip() for i in range(lo, hi + 1) if ent["lines"][i - 1].strip())
                    matched_line = tln
                    matched_page = ent["page_map"].get(tln)
                    best_excerpt = window_text[:220]
                    wl, wc = window_text.lower(), re.sub(r"\s+", "", window_text.lower())
                    best_hits = [a for a in anchors if a.lower() in wl or re.sub(r"\s+", "", a.lower()) in wc]
            else:
                best_sc = -1.0
                for ent in search_pool:
                    for lno, ln in enumerate(ent["lines"], 1):
                        st = ln.strip()
                        if len(st) < 4:
                            continue
                        if exp_pm and ent["page_map"].get(lno) != int(exp_pm.group(1)):
                            continue
                        hi = min(len(ent["lines"]), lno + 4)
                        wtxt = " ".join(ent["lines"][i - 1].strip() for i in range(lno, hi + 1) if ent["lines"][i - 1].strip())
                        wl, wc = wtxt.lower(), re.sub(r"\s+", "", wtxt.lower())
                        hits = [a for a in anchors if a.lower() in wl or re.sub(r"\s+", "", a.lower()) in wc]
                        if not hits:
                            continue
                        sc = sum(2.0 if (a in ref_ids or a in nums) else 1.0 for a in hits)
                        if sc > best_sc:
                            best_sc = sc
                            matched_ent = ent
                            matched_line = lno
                            matched_page = ent["page_map"].get(lno)
                            window_text = wtxt
                            best_excerpt = wtxt[:220]
                            best_hits = hits
                            locator_valid = True

        verbatim_match = bool(locator_valid and window_text and (len(best_hits) >= 1 or bool(ref_ids)))
        numeric_ok, num_bad = _prop_nums_consistent(claim, window_text) if (locator_valid and window_text) else (False, ["no_valid_locator"])
        claim_supported = bool(locator_valid and verbatim_match and numeric_ok and (len(best_hits) >= 2 or (len(best_hits) >= 1 and (bool(ref_ids) or bool(nums)))))
        ok = bool((not bad_ids) and (not miss_files) and locator_valid and verbatim_match and numeric_ok and claim_supported)
        status = "verified" if ok else ("locator_only_unverified" if locator_valid else "ungrounded")
        rec = {
            "claim_id": cid, "claim_category": "external_fact", "source_ref": sref,
            "status": status, "counts_as_verified_citation": ok,
            "locator_valid": locator_valid, "verbatim_match": verbatim_match,
            "numeric_consistent": numeric_ok, "claim_supported": claim_supported,
            "matched_file": matched_ent["file"] if matched_ent else None,
            "file_sha256_prefix": matched_ent["sha256_prefix"] if matched_ent else None,
            "matched_page": matched_page, "matched_line": matched_line,
            "matched_anchors": best_hits[:8], "verbatim_excerpt": best_excerpt,
        }
        if num_bad:
            rec["numeric_mismatches"] = num_bad
        if bad_ids:
            rec["ungrounded_identifiers"] = bad_ids
        if miss_files:
            rec["missing_referenced_files"] = miss_files
        vers.append(rec)
        if not ok:
            ungrounded.append(rec)
    return vers, ungrounded


def _export_stdlib_ooxml_docx(markdown_text: str, out_path: Path) -> None:
    body_xml: list[str] = []
    lines, i = markdown_text.splitlines(), 0
    while i < len(lines):
        st = lines[i].strip()
        if not st:
            i += 1; continue
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                rxml = ["".join("<w:tc><w:p><w:r>" + ("<w:rPr><w:b/></w:rPr>" if ri == 0 else "") + f'<w:t xml:space="preserve">{xml_escape(c)}</w:t></w:r></w:p></w:tc>' for c in r) for ri, r in enumerate(rows)]
                body_xml.append('<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/></w:tblBorders></w:tblPr>' + "".join(f"<w:tr>{x}</w:tr>" for x in rxml) + "</w:tbl>")
            continue
        sid, tv = "", st
        if st.startswith("### "):
            sid, tv = "Heading3", st[4:].strip()
        elif st.startswith("## "):
            sid, tv = "Heading2", st[3:].strip()
        elif st.startswith("# "):
            sid, tv = "Heading1", st[2:].strip()
        elif st.startswith(("- ", "* ")):
            tv = "• " + st[2:].strip()
        tv = re.sub(r"\*\*(.+?)\*\*", r"\1", tv)
        ppr = f'<w:pPr><w:pStyle w:val="{sid}"/></w:pPr>' if sid else ""
        body_xml.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{xml_escape(tv)}</w:t></w:r></w:p>')
        i += 1
    doc_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + "".join(body_xml) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
    styles_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style></w:styles>'
    ct_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    r_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    d_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct_xml); zf.writestr("_rels/.rels", r_rels); zf.writestr("word/_rels/document.xml.rels", d_rels); zf.writestr("word/styles.xml", styles_xml); zf.writestr("word/document.xml", doc_xml)


def _export_standalone_html(markdown_text: str, out_path: Path, summary: dict[str, Any]) -> None:
    parts, lines, i = [], markdown_text.splitlines(), 0
    while i < len(lines):
        st = lines[i].strip()
        if not st:
            i += 1; continue
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
            rows = []
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
    badge = f"<div class='meta'><strong>Status:</strong> {'PASSED' if summary['passed'] else 'NEEDS REVISION'} | <strong>Months:</strong> {summary['total_months']} | <strong>CPM:</strong> {summary['cpm_months']}m ({' -> '.join(summary['cpm_steps'])}) | <strong>Peak Workload:</strong> {summary['peak_workload']} | <strong>Budget Sum:</strong> {summary['budget_sum']}</div>"
    doc = "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/><title>Research Proposal Preview</title><style>body{font-family:system-ui,sans-serif;max-width:920px;margin:24px auto;padding:0 20px;line-height:1.65;color:#1f2937}h1,h2,h3{color:#0f172a;margin-top:1.3em}table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left}th{background:#f1f5f9}.meta{background:#f8fafc;border:1px solid #cbd5e1;padding:10px 14px;border-radius:6px;font-size:13px;margin-bottom:18px}.li{margin:4px 0 4px 12px}</style></head><body>" + badge + "\n".join(parts) + "</body></html>\n"
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
    text, prop_ext = _read_doc(proposal_md)
    plan, plan_err = _safe_load_json(plan_json_path)
    case_cfg, case_err = _safe_load_json(case_json_path)
    case_id = str(case_cfg.get("case_id") or plan.get("case_id") or "unknown")

    errors: list[str] = []
    style_warnings: list[dict[str, Any]] = []
    if plan_err:
        errors.append(f"Invalid plan JSON ({plan_err})")
    if case_err:
        errors.append(f"Invalid case JSON ({case_err})")

    # 1. Seven elements thickness & 已知—未知—待验证 structure
    found_secs, parse_mode = _parse_sections(text)
    missing_secs = [k for k, _ in REQUIRED_SECTIONS if k not in found_secs]
    thin_secs, stub_secs = [], []
    for key, _ in REQUIRED_SECTIONS:
        info = found_secs.get(key)
        if not info:
            continue
        if info["effective_units"] < 75:
            thin_secs.append({"section": key, "heading": info["heading"], "han_chars": info["han_chars"], "en_words": info["en_words"], "effective_units": info["effective_units"]})
        if info["has_stub"]:
            stub_secs.append(key)
    if missing_secs:
        errors.append(f"Missing proposal markdown section(s): {missing_secs}")
    if thin_secs:
        errors.append(f"Substantively thin section(s): {[s['section'] for s in thin_secs]}")
    if stub_secs:
        errors.append(f"Unfinished stub marker(s) in section(s): {stub_secs}")

    bg_text = found_secs.get("background_gap", {}).get("body_text", "")
    hk, hu, hv = bool(KNOWN_RE.search(bg_text)), bool(UNKNOWN_RE.search(bg_text)), bool(VERIFY_RE.search(bg_text))
    kuv_ok = hk and hu and hv
    if bg_text and not kuv_ok:
        errors.append(f"Background section missing '已知—未知—待验证' triad (known={hk}, unknown={hu}, verify={hv})")

    hollow_hits, overbroad_hits = [], []
    for lno, ln in enumerate(text.splitlines(), 1):
        for pat, bucket in ((HOLLOW_GAP_RE, hollow_hits), (OVERBROAD_RE, overbroad_hits)):
            m = pat.search(ln)
            if m and not _is_negated(ln, m.start(), m.end()):
                bucket.append({"line": lno, "term": m.group(0), "excerpt": ln.strip()[:140]})
        m_ai = AI_CLICHE_RE.search(ln)
        if m_ai and not _is_negated(ln, m_ai.start(), m_ai.end()):
            style_warnings.append({"kind": "ai_cliche", "line": lno, "term": m_ai.group(0), "excerpt": ln.strip()[:140]})
    for sk in ("background_gap", "objectives", "expected_outcomes"):
        info = found_secs.get(sk)
        if info and DEFENSIVE_OPEN_RE.search(info["first_prose_line"]):
            style_warnings.append({"kind": "defensive_opening", "line": info["start_line"], "term": info["first_prose_line"][:36], "excerpt": info["first_prose_line"][:140]})
    if hollow_hits:
        errors.append(f"Hollow unverified priority/no-prior-work claim(s) detected: {[h['term'] for h in hollow_hits[:4]]}")
    if overbroad_hits:
        errors.append(f"Unconverged overbroad scope phrasing detected: {[h['term'] for h in overbroad_hits[:4]]}")
    if strict_style and style_warnings:
        errors.append(f"Strict style mode failed with {len(style_warnings)} warning(s)")

    # 2. Questions & Objectives
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

    # 3. Timeline & Budget limits (anti-fabrication & MISSING_CONFIRMED_DURATION hard block)
    plan_m = int(plan["total_months"]) if _is_finite(plan.get("total_months")) and int(plan["total_months"]) > 0 else None
    case_m = int(case_cfg["total_months"]) if _is_finite(case_cfg.get("total_months")) and int(case_cfg["total_months"]) > 0 else None
    needs_hc_duration = bool(plan.get("needs_human_confirmation")) or str(plan.get("total_months_source") or "").lower() == "missing_in_input"
    total_months: int | None = (min(plan_m, case_m) if (plan_m and case_m) else (case_m or plan_m))
    cpm_blocked_reason: str | None = None
    if total_months is None or needs_hc_duration:
        cpm_blocked_reason = "MISSING_CONFIRMED_DURATION"
        errors.append("CPM_BLOCKED: MISSING_CONFIRMED_DURATION (total_months is null or needs_human_confirmation=true)")
    if case_m and plan_m and plan_m > case_m:
        errors.append(f"Plan duration {plan_m} exceeds case total_months {case_m}")

    cb_raw = case_cfg.get("budget_limit")
    cb_absent = case_cfg.get("budget_limit_supplied") is False or (("budget_limit" in case_cfg or "budget_limit_supplied" in case_cfg) and (cb_raw is None or str(cb_raw).strip().lower() in {"", "none", "null", "not_provided", "unknown", "未提供"}))
    case_b = float(cb_raw) if _is_finite(cb_raw) and float(cb_raw) > 0 else None
    pb_raw = plan.get("budget_limit")
    plan_b = float(pb_raw) if _is_finite(pb_raw) and float(pb_raw) > 0 else None
    b_fab = False
    if cb_absent and plan_b is not None:
        if not any(t in str(plan.get("budget_limit_source") or "").lower() for t in ("pending", "gap", "estimated", "not_supplied")):
            b_fab = True
            errors.append(f"Case did not supply budget_limit, yet plan fabricated numeric budget_limit={plan_b} without pending/estimated source declaration")
    if case_b is not None and plan_b is not None and plan_b > case_b + 1e-6:
        errors.append(f"Plan budget_limit {plan_b} exceeds case budget_limit {case_b}")
    eff_b_limit = case_b if case_b is not None else plan_b

    # Objective -> Route -> Milestone closure
    step_ids, valid_steps = {}, []
    for idx_s, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            errors.append(f"Route step #{idx_s} is {type(s).__name__}; expected object"); continue
        valid_steps.append(s)
        if s.get("step_id"):
            step_ids[str(s["step_id"])] = s
    if len(step_ids) != len(steps):
        errors.append("Each technical route step must have a unique non-empty step_id")

    ms_by_obj = {str(m.get("objective_id")) for m in milestones if isinstance(m, dict) and m.get("objective_id")}
    step_by_obj = {str(s.get("objective_id")) for s in valid_steps if s.get("objective_id")}
    for idx_o, obj in enumerate(objectives, 1):
        if not isinstance(obj, dict):
            errors.append(f"Objective #{idx_o} is {type(obj).__name__}; expected object"); continue
        oid = str(obj.get("objective_id") or f"O{idx_o}")
        if not str(obj.get("metric") or "").strip() or not str(obj.get("target_value") or "").strip():
            errors.append(f"Objective {oid} missing quantified metric or target_value")
        if not str(obj.get("sample_or_unit") or obj.get("sample") or "").strip():
            errors.append(f"Objective {oid} missing sample_or_unit")
        if not str(obj.get("control_or_baseline") or obj.get("control") or "").strip():
            errors.append(f"Objective {oid} missing control_or_baseline")
        if oid not in step_by_obj:
            errors.append(f"Objective {oid} has no mapped technical route step")
        if oid not in ms_by_obj:
            errors.append(f"Objective {oid} has no mapped schedule milestone")

    # 4. DAG, CPM, Parallelism
    step_end, valid_spans = {}, True
    for s in valid_steps:
        sid, smr, emr = str(s.get("step_id") or "S?"), s.get("start_month"), s.get("end_month")
        if not _is_finite(smr) or not _is_finite(emr):
            errors.append(f"Route step {sid} has non-numeric month span [{smr}, {emr}]"); valid_spans = False; continue
        sm, em = int(smr), int(emr)
        if sm < 1 or em < sm:
            errors.append(f"Route step {sid} has invalid month span [{sm}, {em}]"); valid_spans = False
        if total_months is not None and total_months > 0 and em > total_months:
            errors.append(f"Route step {sid} exceeds total_months ({em} > {total_months})")
        step_end[sid] = em
    for s in valid_steps:
        sid = str(s.get("step_id") or "S?")
        if not _is_finite(s.get("start_month")):
            continue
        sm, deps_raw = int(s["start_month"]), s.get("depends_on", [])
        if not isinstance(deps_raw, list):
            errors.append(f"Route step {sid} depends_on must be a list"); continue
        for dep in deps_raw:
            ds = str(dep)
            if ds not in step_ids:
                errors.append(f"Route step {sid} depends on unknown step {ds}")
            elif ds in step_end and sm < step_end[ds]:
                errors.append(f"Route step {sid} starts at M{sm} before dependency {ds} ends at M{step_end[ds]}")

    dep_graph = {sid: [str(d) for d in (st.get("depends_on") if isinstance(st.get("depends_on"), list) else []) if str(d) in step_ids] for sid, st in step_ids.items()}
    topo, dag_acyclic = [], True
    try:
        topo = list(TopologicalSorter(dep_graph).static_order())
    except CycleError as exc:
        dag_acyclic = False
        errors.append(f"Technical route has a dependency cycle: {' -> '.join(str(x) for x in exc.args[1])}")

    cpm: dict[str, Any] = {"critical_path_steps": [], "critical_path_months": 0, "step_schedule": {}, "peak_parallel_steps": 0, "peak_parallel_month": None, "peak_monthly_workload": 0.0, "parallelizable_pairs": []}
    if not cpm_blocked_reason and dag_acyclic and valid_spans and len(step_ids) == len(valid_steps) and valid_steps and total_months:
        cpm = _compute_cpm(valid_steps, step_ids, total_months, topo)
        if cpm["critical_path_months"] > total_months:
            errors.append(f"Critical path duration {cpm['critical_path_months']}m exceeds total_months {total_months}m ({cpm['critical_path_steps']})")
        wl_cap = case_cfg.get("max_parallel_workload") or plan.get("max_parallel_workload")
        if _is_finite(wl_cap) and float(wl_cap) > 0 and cpm["peak_monthly_workload"] > float(wl_cap) + 1e-6:
            errors.append(f"Peak monthly workload {cpm['peak_monthly_workload']} at M{cpm['peak_parallel_month']} exceeds cap {wl_cap}")

    for idx_m, ms in enumerate(milestones, 1):
        if not isinstance(ms, dict):
            errors.append(f"Milestone #{idx_m} is {type(ms).__name__}; expected object"); continue
        mid, mv = str(ms.get("milestone_id") or f"M{idx_m}"), ms.get("month")
        if mv is None:
            if not cpm_blocked_reason:
                errors.append(f"Milestone {mid} missing integer month")
            continue
        if not re.fullmatch(r"[+-]?\d+", str(mv).strip()):
            errors.append(f"Milestone {mid} has non-integer month: {mv!r}"); continue
        mi = int(str(mv).strip())
        if mi < 1 or (total_months is not None and total_months > 0 and mi > total_months):
            errors.append(f"Milestone {mid} at M{mi} is outside [1, {total_months}]")

    # 5. Budget arithmetic
    b_sum = 0.0
    for idx, b in enumerate(budget_items, 1):
        if not isinstance(b, dict):
            errors.append(f"Budget item #{idx} is not an object"); continue
        amt, lbl = b.get("amount"), str(b.get("item") or f"item#{idx}")
        if not _is_finite(amt):
            errors.append(f"Budget item '{lbl}' has non-finite/invalid amount: {amt!r}"); continue
        fa = float(amt)
        if fa < 0:
            errors.append(f"Budget item '{lbl}' has negative amount: {fa}")
        elif fa == 0:
            errors.append(f"Budget item '{lbl}' has zero-value padding amount: 0")
        else:
            b_sum += fa
    b_sum = round(b_sum, 4)
    decl_b = plan.get("budget_total_declared")
    if decl_b is not None:
        if not _is_finite(decl_b):
            errors.append(f"budget_total_declared is non-finite: {decl_b!r}")
        elif abs(float(decl_b) - b_sum) > 1e-3:
            errors.append(f"Budget items sum {b_sum} != budget_total_declared {decl_b}")
    if eff_b_limit is not None and b_sum > eff_b_limit + 1e-6:
        errors.append(f"Budget sum {b_sum} exceeds budget_limit {eff_b_limit}")

    # 6. 4-Category Risks & Unavailable Resources
    cov_risks = set()
    for r in risks:
        if not isinstance(r, dict):
            continue
        rt = str(r.get("category") or "").strip().lower()
        cov_risks.add(rt)
        rid = str(r.get("risk_id") or rt or "R?")
        if not str(r.get("trigger_signal") or "").strip() or not str(r.get("fallback_plan") or "").strip():
            errors.append(f"Risk {rid} missing trigger_signal or fallback_plan")
        if not str(r.get("decision_checkpoint") or "").strip():
            errors.append(f"Risk {rid} missing decision_checkpoint")
    miss_rc = [rt for rt in REQUIRED_RISK_TYPES if rt not in cov_risks]
    if miss_rc:
        errors.append(f"Missing required risk categories: {miss_rc}")

    unavail_res: list[str] = []
    for k in ("unavailable_resources", "missing_samples", "unavailable_samples"):
        v = case_cfg.get(k)
        if isinstance(v, list):
            unavail_res.extend(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, str) and v.strip():
            unavail_res.append(v.strip())
    if not unavail_res and re.search(r"样本不可得|关键样本缺失|设备不可得|unavailable\s+(?:sample|resource|cohort)", json.dumps(case_cfg, ensure_ascii=False), re.I):
        unavail_res.append("declared_unavailable_sample_or_resource_in_case")

    rg_ok = True
    if unavail_res:
        vrg = [rg for rg in resource_gaps if isinstance(rg, dict) and all(str(rg.get(k) or "").strip() for k in ("resource", "trigger_signal", "decision_checkpoint", "fallback_plan"))]
        if len(vrg) < len(unavail_res):
            rg_ok = False
            errors.append(f"Unavailable sample/resource(s) {unavail_res} require linked resource_gaps entries; found {len(vrg)}")
        if not GAP_TAG_RE.search(text):
            rg_ok = False
            errors.append("Unavailable sample/resource detected in case, but proposal.md contains zero explicit [EVIDENCE_GAP] markers")

    # 7. Prose <-> JSON Reconciliation + PDF/DOCX/Text Corpus & source_ref Grounding
    reconcile_mm = _reconcile_prose(text, plan, total_months, b_sum, eff_b_limit, cpm=cpm if not cpm_blocked_reason else None)
    for mm in reconcile_mm:
        errors.append(f"PROSE_PLAN_MISMATCH on {mm['field']}: prose={mm['prose_value']} vs json={mm['json_value']}")

    valid_cits = [c for c in claim_citations if isinstance(c, dict) and str(c.get("claim") or "").strip() and str(c.get("source_ref") or "").strip()]
    eff_corpus = corpus_path if corpus_path else (case_json_path.parent if case_json_path and case_json_path.is_file() else None)
    corpus_entries, unparsed_pdfs = _collect_corpus(eff_corpus, {proposal_md, plan_json_path})
    cit_vers, ungrounded_cits = _verify_claim_citations(claim_citations, corpus_entries, unparsed_pdfs=unparsed_pdfs)
    verified_citations_count = sum(1 for v in cit_vers if v.get("status") == "verified" and v.get("claim_category") == "external_fact")
    plan_assumption_count = sum(1 for v in cit_vers if v.get("claim_category") == "plan_assumption")
    pending_ev_count = sum(1 for v in cit_vers if v.get("claim_category") == "pending_evidence")

    if verified_citations_count < 3:
        errors.append(f"INSUFFICIENT_VERIFIED_EXTERNAL_CITATIONS: verified external_fact count={verified_citations_count} < 3 (plan_assumption={plan_assumption_count} do not satisfy citation floor)")
    if ungrounded_cits:
        errors.append(f"Detected {len(ungrounded_cits)} ungrounded/unverified external_fact claim_citations: {[u['claim_id'] + '(' + u['status'] + ')' for u in ungrounded_cits]}")

    corpus_cmp = "\n".join(e["cmp_l"] for e in corpus_entries)
    ungrounded_dois = []
    if corpus_cmp:
        for doi in sorted(set(DOI_RE.findall(text + "\n" + json.dumps(claim_citations, ensure_ascii=False)))):
            if doi.lower().replace(" ", "") not in corpus_cmp:
                ungrounded_dois.append(doi)
        if ungrounded_dois:
            errors.append(f"Ungrounded DOI(s) not present in parsed /input corpus: {ungrounded_dois}")

    # 8. Zero-Crash Editable Export (.docx with native tables + .html)
    py_docx = importlib.util.find_spec("docx") is not None
    docx_ok, docx_exp = False, "none"
    if export_docx_path:
        if py_docx:
            import docx  # type: ignore
            d = docx.Document()
            dlines, di = text.splitlines(), 0
            while di < len(dlines):
                st = dlines[di].strip()
                if not st:
                    di += 1; continue
                if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
                    trows = []
                    while di < len(dlines) and dlines[di].strip().startswith("|") and dlines[di].strip().endswith("|"):
                        cells = [c.strip() for c in dlines[di].strip().strip("|").split("|")]
                        if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                            trows.append(cells)
                        di += 1
                    if trows:
                        nc = max(len(r) for r in trows)
                        tbl = d.add_table(rows=len(trows), cols=nc)
                        for ri, r in enumerate(trows):
                            for ci, val in enumerate(r):
                                tbl.cell(ri, ci).text = val
                    continue
                if st.startswith("# "):
                    d.add_heading(st[2:].strip(), level=1)
                elif st.startswith("## "):
                    d.add_heading(st[3:].strip(), level=2)
                elif st.startswith("### "):
                    d.add_heading(st[4:].strip(), level=3)
                else:
                    d.add_paragraph(st)
                di += 1
            export_docx_path.parent.mkdir(parents=True, exist_ok=True); d.save(str(export_docx_path))
            docx_ok, docx_exp = True, "python-docx"
        else:
            _export_stdlib_ooxml_docx(text, export_docx_path); docx_ok, docx_exp = True, "stdlib_ooxml"

    html_ok = False
    if export_html_path:
        _export_standalone_html(text, export_html_path, {"passed": len(errors) == 0, "total_months": total_months, "cpm_months": cpm["critical_path_months"], "cpm_steps": cpm["critical_path_steps"], "peak_workload": cpm["peak_monthly_workload"], "budget_sum": b_sum})
        html_ok = True

    honest_gaps = [g for cond, g in ((bool(cpm_blocked_reason), f"cpm_blocked={cpm_blocked_reason}"), (bool(unavail_res), f"unavailable_resources={unavail_res}"), (cb_absent, "budget_limit_not_supplied_in_input"), (bool(unparsed_pdfs), f"unparsed_pdfs={unparsed_pdfs}")) if cond]
    cit_by_id = {v["claim_id"]: v for v in cit_vers}
    enriched_cits = []
    for idx, c in enumerate(valid_cits, 1):
        item = dict(c)
        v = cit_by_id.get(str(c.get("claim_id") or f"C{idx}"))
        if v and v.get("verbatim_excerpt"):
            item.update({"verified_file": v["matched_file"], "verified_line": v["matched_line"], "verbatim_excerpt": v["verbatim_excerpt"]})
        enriched_cits.append(item)

    overall_status = "partial_with_gaps" if (cpm_blocked_reason or honest_gaps) else ("ok" if len(errors) == 0 else "failed")
    contract_helper = {
        "case_id": case_id,
        "recommended_status": "partial_with_gaps" if honest_gaps else "ok",
        "deliverable": {"proposal_md": proposal_md.name, "proposal_plan_json": plan_json_path.name, "docx": export_docx_path.name if (export_docx_path and docx_ok) else None, "docx_exporter": docx_exp, "html_preview": export_html_path.name if (export_html_path and html_ok) else None},
        "preserved_facts": [f"total_months={total_months}", f"budget_limit={eff_b_limit}", f"budget_sum={b_sum}", f"critical_path_months={cpm['critical_path_months']}"],
        "evidence_map": enriched_cits,
        "change_log": {"resource_gaps": resource_gaps, "human_confirmations": plan.get("human_confirmations", []), "unresolved_gaps": honest_gaps},
    }
    return {
        "passed": len(errors) == 0,
        "status": overall_status,
        "cpm_blocked_reason": cpm_blocked_reason,
        "case_id": case_id, "proposal_extractor": prop_ext, "parse_mode": parse_mode,
        "seven_elements": {"found_sections": {k: {"heading": v["heading"], "start_line": v["start_line"], "han_chars": v["han_chars"], "en_words": v["en_words"], "effective_units": v["effective_units"]} for k, v in found_secs.items()}, "missing_sections": missing_secs, "thin_sections": thin_secs, "stub_sections": stub_secs, "known_unknown_verify_ok": kuv_ok, "research_questions_count": len(rqs), "objectives_count": len(objectives), "hollow_gap_hits": hollow_hits, "overbroad_scope_hits": overbroad_hits},
        "schedule_and_cpm": {"total_months": total_months, "cpm_blocked_reason": cpm_blocked_reason, "route_steps_count": len(steps), "dag_acyclic": dag_acyclic, **cpm},
        "budget": {"budget_limit": eff_b_limit, "budget_sum": b_sum, "budget_total_declared": decl_b, "budget_limit_fabricated": b_fab},
        "risks_and_resource_gaps": {"missing_risk_categories": miss_rc, "risks_count": len(risks), "unavailable_resources_detected": unavail_res, "resource_gaps_count": len(resource_gaps), "resource_gaps_linked_ok": rg_ok},
        "prose_json_reconciliation": {"mismatches": reconcile_mm, "valid_claim_citations_count": len(valid_cits), "ungrounded_dois": ungrounded_dois},
        "source_grounding_verification": {"corpus_files_indexed": [e["file"] for e in corpus_entries], "unparsed_pdfs": unparsed_pdfs, "checked_count": len(cit_vers), "verified_citations_count": verified_citations_count, "verified_count": verified_citations_count, "plan_assumption_count": plan_assumption_count, "pending_evidence_count": pending_ev_count, "ungrounded_count": len(ungrounded_cits), "details": cit_vers},
        "exports": {"python_docx_installed": py_docx, "docx_exported": docx_ok, "docx_exporter": docx_exp, "html_exported": html_ok},
        "style_warnings": style_warnings, "errors": errors, "output_contract_preview": contract_helper,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract /input proposal plan seed and validate 7-element convergence, DAG/CPM, budget, 4-category risks, source_ref grounding, and editable export.")
    ap.add_argument("--proposal", "--manuscript", dest="proposal", default="", help="Proposal draft file (.md, .docx, .pdf, or .txt).")
    ap.add_argument("--plan-json", default="", help="Structured proposal_plan.json.")
    ap.add_argument("--case-json", default="", help="Optional /input/case.json with constraints.")
    ap.add_argument("--corpus", "--input-dir", dest="corpus", default="", help="/input directory for PDF/DOCX/text indexing, DOI and source_ref grounding.")
    ap.add_argument("--extract-plan-seed", default="", help="Extract grounded constraints and proposal_plan seed JSON from /input (runs standalone without --proposal).")
    ap.add_argument("--export-docx", default="", help="Optional editable .docx export path.")
    ap.add_argument("--export-html", default="", help="Optional standalone .html preview path.")
    ap.add_argument("--strict-style", action="store_true", help="Treat style warnings as blocking errors.")
    ap.add_argument("--emit-contract-helper", default="", help="Optional JSON path for 5-field output_contract helper.")
    ap.add_argument("--out-json", default="", help="Output feasibility audit JSON.")
    args = ap.parse_args(argv)

    if args.extract_plan_seed:
        in_dir = Path(args.corpus) if args.corpus else (Path(args.case_json).parent if args.case_json else None)
        if not in_dir or not in_dir.exists():
            ap.error("--extract-plan-seed requires --input-dir / --corpus (or --case-json)")
        seed = extract_plan_seed(in_dir, case_json_path=Path(args.case_json) if args.case_json else None)
        sp = Path(args.extract_plan_seed); sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.proposal:
            print(json.dumps({"mode": "extract_plan_seed", "files_indexed": len(seed["files_indexed"]), "numeric_facts": len(seed["numeric_facts"]), "unparsed_pdfs": seed["unparsed_pdfs"], "out": str(sp)}, ensure_ascii=False, indent=2))
            return 0

    if not args.proposal or not args.plan_json or not args.out_json:
        ap.error("--proposal, --plan-json, and --out-json are required unless running standalone --extract-plan-seed")

    rep = audit_proposal_plan(
        Path(args.proposal), Path(args.plan_json),
        case_json_path=Path(args.case_json) if args.case_json else None,
        corpus_path=Path(args.corpus) if args.corpus else None,
        export_docx_path=Path(args.export_docx) if args.export_docx else None,
        export_html_path=Path(args.export_html) if args.export_html else None,
        strict_style=args.strict_style,
    )
    op = Path(args.out_json); op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.emit_contract_helper:
        hp = Path(args.emit_contract_helper); hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(rep["output_contract_preview"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": rep["passed"], "parse_mode": rep["parse_mode"],
        "cpm_months": rep["schedule_and_cpm"]["critical_path_months"],
        "peak_parallel_steps": rep["schedule_and_cpm"]["peak_parallel_steps"],
        "budget_sum": rep["budget"]["budget_sum"],
        "citations_verified": rep["source_grounding_verification"]["verified_count"],
        "citations_ungrounded": rep["source_grounding_verification"]["ungrounded_count"],
        "docx_exported": rep["exports"]["docx_exported"], "docx_exporter": rep["exports"]["docx_exporter"],
        "error_count": len(rep["errors"]), "errors": rep["errors"],
    }, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
