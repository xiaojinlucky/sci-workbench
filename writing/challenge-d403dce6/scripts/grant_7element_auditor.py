#!/usr/bin/env python3
"""Offline 7-element grant proposal auditor, /input corpus fact indexer, and workorder verifier.

Pure Python 3.12 stdlib (zero third-party deps, zero network).
Reads .md/.txt/.json/.csv, native OOXML .docx, and .pdf (pdftotext / pypdf / stdlib zlib fallback).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

SEVEN_ELEMENTS: list[tuple[str, tuple[str, ...]]] = [
    ("background", ("立项依据", "研究背景", "rationale", "significance", "background")),
    ("question", ("科学问题", "研究问题", "关键科学问题", "scientific question", "research question", "hypothesis")),
    ("goals", ("研究目标", "总体目标", "objectives", "goals", "aims")),
    ("content", ("研究内容", "工作包", "research content", "work packages")),
    ("innovation", ("创新点", "特色与创新", "创新之处", "innovation", "novelty")),
    ("route", ("技术路线", "研究方案", "实施路径", "technical roadmap", "technical route", "methodology")),
    ("foundation", ("研究基础", "工作基础", "可行性", "foundation", "preliminary", "feasibility")),
]

SEMANTIC_CUES: dict[str, tuple[str, ...]] = {
    "background": ("现状", "瓶颈", "流行病学", "临床需求", "背景", "挑战", "趋势", "文献报道", "incidence", "rationale", "limitation"),
    "question": ("科学问题", "关键问题", "核心假设", "待解问题", "机制不明", "可检验假设", "hypothesis", "scientific question", "causal"),
    "goals": ("研究目标", "目标", "G1", "G2", "G3", "考核指标", "objective", "aim", "goal"),
    "content": ("研究内容", "工作包", "WP1", "WP2", "WP3", "拟开展", "实验设计", "work package", "cohort"),
    "innovation": ("创新点", "区别于", "相较于", "差异化", "差异在于", "特色", "突破", "推进体现", "unlike", "novelty", "distinct from"),
    "route": ("技术路线", "阶段", "里程碑", "MS1", "MS2", "M12", "输入输出", "风险", "备选方案", "串行", "roadmap", "milestone"),
    "foundation": ("研究基础", "可行性", "前期", "团队", "设备", "平台", "EVIDENCE_GAP", "伦理", "自有基础", "preliminary", "feasibility"),
}

NEG_BEFORE_RE = re.compile(
    r"(?:并非|不是|不是真实|未曾|未能|尚未|尚无|未提供|未获得|未获批|未具备|不得|严禁|禁止|不能将|不将|切勿|区别于|而非|不属于|非货币|非已获|非真实|切忌|避免将|不可将|"
    r"not\s+(?:a|an|the|yet|supplied|provided|owned|funded|real|completed|performed)|never\s+|neither\s+|does\s+not\s+|without\s+|rather\s+than\s+|distinct\s+from\s+|must\s+not\s+|do\s+not\s+)",
    re.I,
)
NEG_AFTER_RE = re.compile(
    r"(?:并非(?:申请人|本团队|本课题组|真实|已完成)|尚未(?:完成|获得|提供|获批|具备)|未(?:完成|提供|获得|获批)|属于外部|归属外部|由外部作者|"
    r"not\s+(?:yet\s+)?(?:completed|funded|owned|supplied|performed)|remains?\s+(?:proposed|planned|unverified))",
    re.I,
)
CONTRAST_RE = re.compile(r"(?:但是|然而|但|不过|相反|whereas|however|but\s+)", re.I)
QUOTE_META_RE = re.compile(r"(?:例如|示例|反例|误写|违规|禁止|不得|切勿|避免|禁用|不应|原话|引述|example|forbid|avoid|prohibit|do\s+not\s+write|invalid)", re.I)
SENT_SPLIT_RE = re.compile(r"[。；;！？!\n]+")
GAP_TAG_RE = re.compile(r"\[EVIDENCE_GAP[^\]]*\]|\[待申请人补充[^\]]*\]|\[待确认[^\]]*\]|\[\[EVIDENCE:[^\]]*\]\]", re.I)

FIRST_PERSON_PRIOR_RE = re.compile(
    r"(?:本课题组|本团队|申请人|本实验室|我们)(?:前期|此前|目前|已经|现)?[^。；;\n]{0,22}?"
    r"(?:已(?:经)?(?:完成|获得|发表|建立|证实|验证|掌握|研发|开发|获批|授权|通过|购置|具备)|前期成果表明|预实验证实)|"
    r"\bour\s+(?:group|lab|team|applicant)\s+(?:has\s+)?(?:previously\s+)?(?:demonstrated|published|obtained|completed|established|secured|patented)\b",
    re.I,
)
PROPOSED_AS_COMPLETED_RE = re.compile(
    r"(?:(?:G[1-9]|WP[1-9]|目标\s*[1-9]|工作包\s*[1-9]|拟开展(?:工作|研究|方案)|本项目(?:方案|计划|指标))[^。；;\n]{0,20}?"
    r"(?:已完成|已实现|已验证|已取得|已获得|确保提升|保证实现|保证达到|必将实现|必定达成))|"
    r"(?:规划单位[^。；;\n]{0,18}?(?:万元|经费已到位|已获批资金|资助金额|实际经费))|(?:(?:经费|资金)已(?:获批|到位|落实))",
    re.I,
)
FUNDER_POLICY_FAB_RE = re.compile(
    r"(?:(?:国家自然科学基金|国自然|NSFC|NIH|NSF|科技部|基金委|省自然|资助机构)(?:指南|政策|规定|通知|要求)(?:明确|规定|要求|资助|限制|支持))|"
    r"(?:(?:已|将)?获(?:现实)?资助(?:机构)?(?:批准|拨款|立项))|(?:现实资助机构(?:政策|指南)规定)",
    re.I,
)
RESOURCE_FAB_RE = re.compile(
    r"(?:(?:本实验室|本单位|本团队|课题组)[^。；;\n]{0,16}?(?:已具备|已购置|已配备|现有一台|拥有多台|已建成完整)|"
    r"(?:已通过|已获得)(?:伦理审查|伦理审批|伦理批件|动物伦理|IRB\s*批准|安全审批))",
    re.I,
)
PRIORITY_HYPE_RE = re.compile(r"国际领先|国内首创|填补(?:了)?(?:国内|国际)?空白|首次提出|首次揭示|前所未有|颠覆性突破|世界领先|world-first|unprecedented|groundbreaking", re.I)
POLICY_SLOGAN_RE = re.compile(r"赋能|抓手|卡点|打通最后一公里|重大战略意义|开启新时代|开启新的篇章|重磅|炸裂|令人振奋|意义深远|堪称|无疑", re.I)
AI_CLICHE_RE = re.compile(r"综上所述|总的来说|由此可见|不难发现|众所周知|值得注意的是|深刻揭示了|显著提升了|为未来研究奠定(?:了)?(?:坚实)?基础|提供(?:了)?新思路|提供(?:了)?新的可能", re.I)
DEFENSIVE_OPEN_RE = re.compile(r"^(?:遗憾的是|受限于|尽管本研究存在|虽然本方案|本文仅|本研究仅|无法证明|不能证明|Unfortunately,\s|Although\s+limited)", re.I)
OUTLINE_STUB_RE = re.compile(r"(?:写作建议|此处可写|建议补充|待撰写|\bTODO\b|\bTBD\b|\bFIXME\b|lorem\s+ipsum|\[insert\s+[^\]]+\])", re.I)

EV_STATUS_LINE_RE = re.compile(r"^\s*(?:(?:applicant(?:'s)?(?:\s+preliminary)?\s+evidence|(?:preliminary\s+)?evidence)\s+(?:status|availability)|status|申请人(?:前期)?(?:研究)?基础(?:证据)?状态|前期证据状态|材料状态)\s*[:：=]\s*(.*?)\s*$", re.I)
EV_STATUS_JSON_RE = re.compile(r'"[^"\r\n]*evidence[^"\r\n]*status"\s*:\s*"([^"]+)"', re.I)
UNAVAIL_RE = re.compile(r"^(?:unknown|unavailable|pending|tbd|not\s+(?:provided|supplied|available|confirmed)|absent|missing|n/?a|none|未知|待核实|待确认|待补充|未提供|尚未提供|缺失|不详|暂无|未确认|未提交|无)(?:\b|[\s.,;:：，；。()（）\[\]—-]|$)", re.I)

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
DRAFT_SIZE_RE = re.compile(r"(\d{3,5})\s*[–—\-~至到]\s*(\d{3,5})\s*(?:个)?(?:汉字|字|中文字符)")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
PATENT_RE = re.compile(r"\b(?:CN|US|EP|JP|KR|WO)\s*\d{6,12}\s*[A-Z]\d?\b")
ARXIV_RE = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\b", re.I)
FACT_NUM_RE = re.compile(r"(?:\bn\s*=\s*\d[\d,]*|\d+(?:[.,]\d+)?\s*%|\bp\s*[<=>]\s*0?\.\d+|95%\s*CI\s*[\d.\-–~]+|\d+(?:[.~–\-]\d+)?\s*(?:mol/L|mA/cm2|rpm|℃|°C|MPa|kPa|mg/L|mm|cm|h|V|/10,?000|万|例|项|个月|年))", re.I)
ANCHOR_TOK_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|(?:CN|US|EP|JP|KR|WO)\s*\d{6,12}\s*[A-Z]\d?|\d+(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:mol/L|mA/cm2|rpm|℃|°C|MPa|V|h|/10,?000)|[A-Za-z][A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{3,8}", re.I)
PLANNED_REF_RE = re.compile(r"(?:planned|proposed|workorder|evidence_gap|gap|pending|tbd|拟开展|本项目计划|待确认|待申请人|工作单|任务单|计划书)", re.I)
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".html", ".xml"}


def _count_han(t: str) -> int:
    return len(HAN_RE.findall(t))


def _count_en(t: str) -> int:
    return len(WORD_RE.findall(t))


def _eff_units(han: int, en: int) -> int:
    return han + round(en * 1.6)


def _is_finite(v: Any) -> bool:
    if isinstance(v, bool) or v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


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
                    txt = rh[2:].decode("utf-16-be", errors="ignore") if rh.startswith(b"\xfe\xff") else (rh.decode("utf-16-be", errors="ignore") if len(rh) >= 2 and rh[0] == 0 else rh.decode("latin-1", errors="ignore"))
                    if txt.strip():
                        parts.append(txt)
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
            if "heading1" in sv or sv in {"1", "标题 1", "heading 1"}:
                lines.append(f"# {para}")
            elif "heading2" in sv or sv in {"2", "标题 2", "heading 2"}:
                lines.append(f"## {para}")
            elif "heading3" in sv or sv in {"3", "标题 3", "heading 3"}:
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


def _is_negated_or_attributed(line: str, start: int, end: int | None = None) -> bool:
    mend = end if end is not None else start
    lb = 0
    for m in SENT_SPLIT_RE.finditer(line, 0, start):
        lb = m.end()
    mr = SENT_SPLIT_RE.search(line, mend)
    rb = mr.start() if mr else len(line)
    sent, rs, re_ = line[lb:rb], start - lb, mend - lb
    for lq, rq in (("“", "”"), ("「", "」"), ("『", "』"), ('"', '"'), ("‘", "’")):
        li, ri = sent.rfind(lq, 0, rs), sent.find(rq, re_)
        if li != -1 and ri != -1:
            outside = sent[:li] + " " + sent[ri + 1 :]
            if QUOTE_META_RE.search(outside) or NEG_BEFORE_RE.search(outside):
                return True
    before = sent[max(0, rs - 80) : rs]
    cm = list(CONTRAST_RE.finditer(before))
    if cm:
        before = before[cm[-1].end() :]
    if NEG_BEFORE_RE.search(before):
        return True
    after = sent[re_ : min(len(sent), re_ + 45)]
    ca = CONTRAST_RE.search(after)
    if ca:
        after = after[: ca.start()]
    return bool(NEG_AFTER_RE.search(after))


def _declares_unavail(text: str) -> bool:
    clean = text.replace("\ufeff", "")
    for m in EV_STATUS_JSON_RE.finditer(clean):
        if UNAVAIL_RE.match(m.group(1).strip().strip("[](){}*` ")):
            return True
    first = True
    for ln in clean.splitlines():
        st = ln.strip()
        if not st:
            continue
        sm = EV_STATUS_LINE_RE.match(st)
        val = (sm.group(1) if sm else (st if first else "")).strip().strip("[](){}*` ")
        if val and UNAVAIL_RE.match(val):
            return True
        first = False
    return False


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _extract_workorder(case_cfg: dict[str, Any], wo_cfg: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(case_cfg.get("organizer_workorder"), dict):
        merged.update(case_cfg["organizer_workorder"])
    if isinstance(wo_cfg.get("organizer_workorder"), dict):
        merged.update(wo_cfg["organizer_workorder"])
    elif wo_cfg:
        merged.update(wo_cfg)
    return merged


def _expand_workorder_reqs(wo: dict[str, Any], case_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    reqs: list[dict[str, Any]] = []
    for idx, r in enumerate(wo.get("rfp_requirements") or case_cfg.get("rfp_requirements") or [], 1):
        if isinstance(r, dict):
            reqs.append({"requirement_id": str(r.get("id") or r.get("requirement_id") or f"RFP{idx}"), "source": "rfp_requirements", "text": str(r.get("text") or r.get("requirement") or r.get("title") or ""), "keywords": list(r.get("keywords") or [])})
        else:
            reqs.append({"requirement_id": f"RFP{idx}", "source": "rfp_requirements", "text": str(r), "keywords": []})
    for key, rid, src in (("sections", "WO-SECTIONS", "workorder.sections"), ("draft_size", "WO-DRAFT-SIZE", "workorder.draft_size"), ("consistency", "WO-CONSISTENCY", "workorder.consistency")):
        if wo.get(key):
            reqs.append({"requirement_id": rid, "source": src, "text": str(wo[key]), "keywords": []})
    if isinstance(wo.get("required_supporting_files"), list) and wo["required_supporting_files"]:
        reqs.append({"requirement_id": "WO-SUPPORTING-6", "source": "workorder.required_supporting_files", "text": "; ".join(str(x) for x in wo["required_supporting_files"][:6]), "keywords": []})
    lims = wo.get("scientific_limits")
    if isinstance(lims, list):
        for i, lim in enumerate(lims, 1):
            reqs.append({"requirement_id": f"WO-LIMIT-{i}", "source": "workorder.scientific_limits", "text": str(lim), "keywords": []})
    elif isinstance(lims, str) and lims.strip():
        reqs.append({"requirement_id": "WO-LIMIT-1", "source": "workorder.scientific_limits", "text": lims.strip(), "keywords": []})
    return reqs


def extract_corpus_index(input_dir: Path, case_json_path: Path | None = None, workorder_path: Path | None = None) -> dict[str, Any]:
    """Automatically index /input (PDF/DOCX/MD/TXT/JSON/CSV) into facts, identifiers, and boundaries."""
    c_path = case_json_path if (case_json_path and case_json_path.is_file()) else (input_dir / "case.json" if (input_dir / "case.json").is_file() else None)
    w_path = workorder_path if (workorder_path and workorder_path.is_file()) else (input_dir / "organizer-workorder.json" if (input_dir / "organizer-workorder.json").is_file() else None)
    case_cfg, wo_cfg = _load_json(c_path), _load_json(w_path)
    wo = _extract_workorder(case_cfg, wo_cfg)

    files_meta, numeric_facts, author_hints, cand_ev = [], [], [], []
    dois, patents, arxivs = set(), set(), set()
    scan = [input_dir] if input_dir.is_file() else sorted(p for p in input_dir.rglob("*") if p.is_file())
    for fp in scan:
        ext = fp.suffix.lower()
        if ext not in TEXT_EXTS and ext not in {".pdf", ".docx"}:
            continue
        rel = fp.name if input_dir.is_file() else fp.relative_to(input_dir).as_posix()
        txt, extractor = _read_doc(fp)
        lines = txt.splitlines()
        files_meta.append({"file": rel, "ext": ext, "bytes": fp.stat().st_size, "sha256_prefix": hashlib.sha256(fp.read_bytes()).hexdigest()[:16], "char_count": len(txt), "line_count": len(lines), "extractor": extractor})
        dois.update(DOI_RE.findall(txt))
        patents.update(re.sub(r"\s+", "", p) for p in PATENT_RE.findall(txt))
        arxivs.update(ARXIV_RE.findall(txt))
        if fp.name.lower() in {"case.json", "organizer-workorder.json"}:
            continue
        page = 0
        for lno, ln in enumerate(lines, 1):
            st = ln.strip()
            if not st:
                continue
            pm = re.match(r"^##\s*\[Page\s+(\d+)\]", st, re.I)
            if pm:
                page = int(pm.group(1)); continue
            if re.search(r"(?:发明人|专利权人|Corresponding author|Authors?:|University|Hospital|Department of)", st, re.I) and len(author_hints) < 12:
                author_hints.append({"file": rel, "line": lno, "page": page or None, "excerpt": st[:160]})
            nums = FACT_NUM_RE.findall(st)
            if nums and len(st) >= 18 and len(numeric_facts) < 500:
                ref = f"{rel}#L{lno}" + (f" (Page {page})" if page else "")
                numeric_facts.append({"fact_id": f"F{len(numeric_facts)+1:02d}", "source_ref": ref, "numbers": nums[:6], "excerpt": st[:220]})
                if len(cand_ev) < 120:
                    cand_ev.append({"claim_id": f"CLM{len(cand_ev)+1}", "claim": st[:160], "section": "TBD", "source_ref": ref, "claim_type": "external_literature"})

    sb = wo.get("source_basis") if isinstance(wo.get("source_basis"), dict) else {}
    missing_b = [k for cond, k in (
        (sb.get("applicant_owned_preliminary_results_supplied") is False, "applicant_owned_preliminary_results_missing"),
        (sb.get("actual_funder_notice_supplied") is False or str(wo.get("authority") or "").lower() == "organizer-workorder-not-funder-policy", "actual_funder_notice_missing"),
        (sb.get("resource_availability_evidence_supplied") is False, "resource_availability_evidence_missing"),
    ) if cond]
    return {
        "schema_version": "grant-corpus-index/v1",
        "input_dir": str(input_dir),
        "case_id": str(case_cfg.get("case_id") or wo.get("id") or "unknown"),
        "files": files_meta,
        "identifiers": {"dois": sorted(dois), "patents": sorted(patents), "arxivs": sorted(arxivs)},
        "workorder_summary": {
            "authority": wo.get("authority"), "actual_topic": wo.get("actual_topic"),
            "planning_months": wo.get("planning_months"), "planning_unit_cap": wo.get("planning_unit_cap"),
            "draft_size": wo.get("draft_size"), "external_authors": list(sb.get("external_authors") or []),
            "missing_evidence_boundaries": missing_b, "expanded_requirements": _expand_workorder_reqs(wo, case_cfg),
        },
        "author_and_affiliation_hints": author_hints,
        "numeric_facts": numeric_facts,
        "candidate_evidence_map": cand_ev,
    }


def _collect_corpus_entries(corpus_dir: Path | None, exclude: set[Path]) -> list[dict[str, Any]]:
    if not corpus_dir or not corpus_dir.exists():
        return []
    excl = {p.resolve() for p in exclude if p}
    entries: list[dict[str, Any]] = []
    files = [corpus_dir] if corpus_dir.is_file() else sorted(p for p in corpus_dir.rglob("*") if p.is_file())
    for fp in files:
        if fp.resolve() in excl or any(k in fp.name.lower() for k in ("audit", "contract_helper", "grant_chapter", "grant_plan", "crosswalk")):
            continue
        if fp.suffix.lower() not in TEXT_EXTS and fp.suffix.lower() not in {".pdf", ".docx"}:
            continue
        txt, extractor = _read_doc(fp)
        if not txt.strip():
            continue
        rel = fp.name if corpus_dir.is_file() else fp.relative_to(corpus_dir).as_posix()
        low = txt.lower()
        sha_p = hashlib.sha256(fp.read_bytes()).hexdigest()[:16]
        lines = txt.splitlines()
        page_map: dict[int, int] = {}
        cur_page = 1
        for i_ln, raw_ln in enumerate(lines, 1):
            pm = re.match(r"^##\s*\[Page\s+(\d+)\]", raw_ln.strip(), re.I)
            if pm:
                cur_page = int(pm.group(1))
            page_map[i_ln] = cur_page
        entries.append({
            "file": rel,
            "name_l": fp.name.lower(),
            "stem_l": fp.stem.lower(),
            "sha256_prefix": sha_p,
            "extractor": extractor,
            "lines": lines,
            "page_map": page_map,
            "full_text": txt,
            "full_l": low,
            "cmp_l": re.sub(r"\s+", "", low),
        })
    return entries


_UNIT_NORM_MAP = {
    "μg/kg": "ug/kg", "µg/kg": "ug/kg", "ug/kg": "ug/kg", "mcg/kg": "ug/kg",
    "mg/kg": "mg/kg", "g/kg": "g/kg",
    "μg/l": "ug/l", "µg/l": "ug/l", "ug/l": "ug/l", "mg/l": "mg/l",
    "μg/ml": "ug/ml", "µg/ml": "ug/ml", "ug/ml": "ug/ml", "mg/ml": "mg/ml",
    "mmol/l": "mmol/l", "mol/l": "mol/l", "μmol/l": "umol/l", "µmol/l": "umol/l", "umol/l": "umol/l",
    "ma/cm2": "ma/cm2", "ma/cm²": "ma/cm2", "a/cm2": "a/cm2",
    "°c": "c", "℃": "c", "mpa": "mpa", "kpa": "kpa", "pa": "pa",
    "mv": "mv", "v": "v", "nm": "nm", "μm": "um", "µm": "um", "um": "um", "mm": "mm", "cm": "cm",
    "mg": "mg", "ug": "ug", "μg": "ug", "µg": "ug", "kg": "kg", "ml": "ml", "μl": "ul", "µl": "ul", "ul": "ul",
    "%": "%", "/10,000": "/10000", "/10000": "/10000", "万": "万", "rpm": "rpm", "h": "h", "min": "min",
}

_UNIT_PAT = (
    r"(?:μg/kg|µg/kg|ug/kg|mcg/kg|mg/kg|g/kg|μg/mL|µg/mL|ug/mL|mg/mL|μg/L|µg/L|ug/L|mg/L|"
    r"mmol/L|mol/L|μmol/L|µmol/L|umol/L|mA/cm2|mA/cm²|A/cm2|°C|℃|MPa|kPa|Pa|mV|nm|μm|µm|um|mm|cm|"
    r"mg|μg|µg|ug|kg|mL|μL|µL|uL|rpm|/10,?000|%|(?<![A-Za-z])V(?![A-Za-z])|(?<![A-Za-z])h(?![A-Za-z]))"
)
_NUM_UNIT_RE = re.compile(rf"(?<![A-Za-z0-9_.#])(\d+(?:,\d{{3}})*(?:\.\d+)?)\s*({_UNIT_PAT})?", re.I)
_STRUCT_STRIP_RE = re.compile(
    r"(?:#L\d+(?:[-–]L?\d+)?|\bPage\s*\d+\b|\bLine\s*\d+\b|\b(?:CLM|WP|MS|RFP|RG|HC|RQ|Fig|Table|Tab)\s*\.?\s*\d+\b|\b[GOMSP]\d+\b|第\s*\d+\s*[页行节章条]|95%\s*CI)",
    re.I,
)
_PVAL_RE = re.compile(r"\bp\s*([<=>≤≥])\s*(0?\.\d+)\b", re.I)
_PLAN_FACT_CUE_RE = re.compile(
    r"(?:10\.\d{4,9}/\S+|(?:CN|US|EP|JP|KR|WO)\s*\d{6,12}\s*[A-Z]\d?|"
    r"\bn\s*=\s*\d[\d,]*|\bp\s*[<=>≤≥]\s*0?\.\d+|95%\s*CI\s*[\d.\-–~]+|"
    r"\d+(?:\.\d+)?\s*(?:%|mg/kg|ug/kg|μg/kg|µg/kg|mol/L|mmol/L|mg/L|mA/cm2|/10,?000)|"
    r"(?:前期(?:预实验|实验|队列|研究|数据|结果)|既往(?:研究|文献|报道)|文献(?:报道|表明|显示)|已测得|实测)[^。；;，,]{0,24}\d)",
    re.I,
)
_FUTURE_PLAN_CLAUSE_RE = re.compile(
    r"^(?:本项目|本研究|后续|未来|拟|计划|将|WP\d|G\d|目标\s*\d|planned|proposed|we\s+will|to\s+be)",
    re.I,
)

_CONTRAST_ENTITY_GROUPS: list[list[tuple[str, re.Pattern[str]]]] = [
    [
        ("female_girls", re.compile(r"(?:女性|女童|女孩|\bgirls?\b|\bfemales?\b)", re.I)),
        ("male_boys", re.compile(r"(?:男性|男童|男孩|\bboys?\b|\bmales?\b)", re.I)),
    ],
    [
        ("pediatric", re.compile(r"(?:儿童|儿科|青少年|学龄前|0[–—-]19|0[–—-]9|0[–—-]4|\bchildren\b|\bpediatric\b|\badolescents?\b|\bpreschoolers?\b)", re.I)),
        ("adult_elderly", re.compile(r"(?:成人|成年人|老年人|老年患者|\badults?\b|\belderly\b|\bgeriatric\b)", re.I)),
    ],
    [
        ("human_clinical", re.compile(r"(?:患者|病人|临床队列|人群|病例|\bpatients?\b|\binhabitants?\b|\bindividuals?\b|\bcases?\b|\bhumans?\b)", re.I)),
        ("animal_model", re.compile(r"(?:小鼠|大鼠|家兔|动物模型|\bmice\b|\bmouse\b|\brats?\b|\bmurine\b|\bporcine\b)", re.I)),
    ],
    [
        ("medicines", re.compile(r"(?:药物|药品|医药|\bmedicines?\b|\bdrugs?\b|\bpharmaceuticals?\b)", re.I)),
        ("pesticides", re.compile(r"(?:农药|杀虫剂|除草剂|\bpesticides?\b|\binsecticides?\b|\bherbicides?\b)", re.I)),
    ],
    [
        ("vanadium", re.compile(r"(?:全钒|钒液流|钒离子|硫酸氧钒|\bvanadium\b)", re.I)),
        ("lithium", re.compile(r"(?:锂离子|锂电池|\blithium\b)", re.I)),
    ],
]

_DIR_UP_RE = re.compile(r"(?:显著上升|呈上升|上升|升高|增加|增长|高于|递增|正相关|阳性关联|显著提升|\bincreas(?:e|ed|ing)\b|\brise\b|\bhigher\b|\bpositive\s+association\b|\bupward\b)", re.I)
_DIR_DOWN_RE = re.compile(r"(?:显著下降|呈下降|下降|降低|减少|低于|递减|负相关|显著降低|\bdecreas(?:e|ed|ing)\b|\bdeclin(?:e|ed|ing)\b|\blower\b|\bnegative\s+association\b|\bdownward\b)", re.I)
_DIR_FLAT_RE = re.compile(r"(?:平稳|趋于平稳|保持稳定|无显著变化|无统计学差异|无关联|无相关性|未见关联|\bstationary\b|\bno\s+significant\s+(?:change|difference)\b|\bnot\s+associated\b|\bno\s+association\b|\bunchanged\b)", re.I)

_BILINGUAL_CONCEPT_PAIRS: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
    (re.compile(r"中毒|外源性中毒", re.I), re.compile(r"intoxicat|poisoning", re.I)),
    (re.compile(r"女性|女童", re.I), re.compile(r"\bgirls?\b|\bfemales?\b|feminino", re.I)),
    (re.compile(r"男性|男童", re.I), re.compile(r"\bboys?\b|\bmales?\b|masculino", re.I)),
    (re.compile(r"药物|药品", re.I), re.compile(r"\bmedicines?\b|\bdrugs?\b|medicamentos", re.I)),
    (re.compile(r"意外误服|意外", re.I), re.compile(r"\baccidental\b|acidental", re.I)),
    (re.compile(r"发病率|发生率", re.I), re.compile(r"\bincidence\b|incidência", re.I)),
    (re.compile(r"全巴西|巴西", re.I), re.compile(r"\bbrazil\b|\bbrasil\b", re.I)),
    (re.compile(r"骨坏死|缺血性坏死", re.I), re.compile(r"osteonecrosis|avascular\s+necrosis|\bavn\b", re.I)),
    (re.compile(r"镰状细胞", re.I), re.compile(r"sickle\s+cell|\bscd\b", re.I)),
    (re.compile(r"谷氨酸|缬氨酸", re.I), re.compile(r"glutamic|valine|\bhbs\b", re.I)),
    (re.compile(r"系统综述|纳入", re.I), re.compile(r"systematic|prisma|prospero|studies|patients", re.I)),
]


def _canon_num(raw: str) -> str:
    s = raw.replace(",", "").strip()
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _extract_num_units(text: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    cleaned = _STRUCT_STRIP_RE.sub(" ", text)
    # Remove DOI / patent / year-range noise before extracting numeric facts
    cleaned = DOI_RE.sub(" ", cleaned)
    cleaned = PATENT_RE.sub(" ", cleaned)
    pvals = [(m.group(1).replace("≤", "<=").replace("≥", ">="), _canon_num(m.group(2))) for m in _PVAL_RE.finditer(cleaned)]
    cleaned = _PVAL_RE.sub(" ", cleaned)
    pairs: list[tuple[str, str]] = []
    for m in _NUM_UNIT_RE.finditer(cleaned):
        raw_n, raw_u = m.group(1), (m.group(2) or "").strip()
        cn = _canon_num(raw_n)
        nu = _UNIT_NORM_MAP.get(raw_u.lower(), raw_u.lower()) if raw_u else ""
        # Skip bare single-digit integers without unit (e.g. "1" in "1/6" handled or trivial enumerations)
        if not nu and len(cn) <= 1:
            continue
        # Skip standalone years 1900..2099 only if no unit
        if not nu and re.fullmatch(r"(?:19|20)\d{2}", cn):
            continue
        pairs.append((cn, nu))
    return pairs, pvals


def _check_numeric_unit_consistency(claim: str, window_text: str) -> tuple[bool, list[str]]:
    c_pairs, c_pvals = _extract_num_units(claim)
    if not c_pairs and not c_pvals:
        return True, []
    w_pairs, w_pvals = _extract_num_units(window_text)
    # Also normalize Portuguese comma-decimals (e.g. 53,1% -> 53.1%) and dot-thousands (5.539 -> 5539) in window
    w_alt = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1\2", window_text)
    w_alt = re.sub(r"(\d),(\d{1,2})(?!\d)", r"\1.\2", w_alt)
    w_pairs_alt, w_pvals_alt = _extract_num_units(w_alt)
    all_w_pairs = w_pairs + w_pairs_alt
    all_w_nums = {n for n, _ in all_w_pairs}
    all_w_pvals = set(w_pvals + w_pvals_alt)

    mismatches: list[str] = []
    for op, pv in c_pvals:
        if (op, pv) not in all_w_pvals:
            mismatches.append(f"pvalue_mismatch:p{op}{pv}")
    for cn, nu in c_pairs:
        if nu:
            # Strict: both number AND unit must match in the source window!
            if (cn, nu) not in all_w_pairs:
                # Check if same number appears with a DIFFERENT unit (e.g. mg/kg vs ug/kg)
                other_units = sorted({wu for wn, wu in all_w_pairs if wn == cn and wu and wu != nu})
                if other_units:
                    mismatches.append(f"unit_mismatch:{cn} {nu} vs source {other_units}")
                elif cn in all_w_nums and nu in {"%", "/10000"}:
                    # e.g. "25%" vs "25.0%" handled by _canon_num; if unit missing in source, reject
                    mismatches.append(f"unit_not_in_source:{cn}{nu}")
                else:
                    mismatches.append(f"number_unit_not_found:{cn}{nu}")
        else:
            if cn not in all_w_nums:
                mismatches.append(f"number_not_found:{cn}")
    return len(mismatches) == 0, mismatches


def _detect_polarity(text: str) -> set[str]:
    pols: set[str] = set()
    if _DIR_FLAT_RE.search(text):
        pols.add("flat")
    for m in _DIR_UP_RE.finditer(text):
        if not _is_negated_or_attributed(text, m.start(), m.end()):
            pols.add("up")
            break
    for m in _DIR_DOWN_RE.finditer(text):
        if not _is_negated_or_attributed(text, m.start(), m.end()):
            pols.add("down")
            break
    return pols


def _check_claim_support_semantics(claim: str, window_text: str, anchor_hits: list[str], ref_ids: list[str]) -> tuple[bool, str]:
    # 1. Contrasting entity / subject substitution check
    for grp in _CONTRAST_ENTITY_GROUPS:
        c_hit = [lbl for lbl, pat in grp if pat.search(claim)]
        w_hit = [lbl for lbl, pat in grp if pat.search(window_text)]
        if c_hit and w_hit and not (set(c_hit) & set(w_hit)):
            return False, f"SUBJECT_OR_ENTITY_MISMATCH: claim={c_hit} vs source={w_hit}"

    # 2. Polarity / direction reversal check
    c_pol = _detect_polarity(claim)
    w_pol = _detect_polarity(window_text)
    if c_pol and w_pol:
        # Reject if claim asserts any directional polarity absent from the cited source window
        if (c_pol - w_pol):
            return False, f"POLARITY_OR_DIRECTION_REVERSAL: claim={sorted(c_pol)} vs source={sorted(w_pol)}"

    # 3. Minimum substantive lexical/bilingual overlap check
    biling_hits = sum(1 for zh_p, en_p in _BILINGUAL_CONCEPT_PAIRS if zh_p.search(claim) and en_p.search(window_text))
    if len(anchor_hits) == 0 and biling_hits == 0 and not ref_ids:
        return False, "INSUFFICIENT_ANCHOR_OVERLAP"
    return True, "supported"


def _has_unverified_fact_in_plan(claim: str, corpus: list[dict[str, Any]]) -> list[str]:
    """Detect concrete external/prior empirical statistics mixed into a plan/gap-tagged claim."""
    bad_clauses: list[str] = []
    clauses = [c.strip() for c in re.split(r"[，,。；;！!？?\n]+", claim) if c.strip()]
    for cl in clauses:
        if not _PLAN_FACT_CUE_RE.search(cl):
            continue
        # Pure future target threshold without prior-work/literature/measured cue is exempt ONLY if future-framed AND has no prior/literature claim
        is_prior_or_ext = bool(re.search(r"(?:前期|预实验|此前|既往|文献|报道|实测|已测得|已验证|队列显示|结果显示|10\.\d{4,9}/|(?:CN|US|EP|WO)\d{6,12}|n\s*=\s*\d|p\s*[<=>≤≥]\s*0?\.\d|95%\s*CI)", cl, re.I))
        if not is_prior_or_ext and _FUTURE_PLAN_CLAUSE_RE.search(cl):
            continue
        # Check if this clause's numbers/IDs are actually grounded in corpus
        grounded = False
        for ent in corpus:
            ok_num, _ = _check_numeric_unit_consistency(cl, ent["full_text"])
            ids = DOI_RE.findall(cl) + [re.sub(r"\s+", "", p) for p in PATENT_RE.findall(cl)]
            ids_ok = all(re.sub(r"\s+", "", r.lower()) in ent["cmp_l"] for r in ids)
            if ok_num and ids_ok and (ids or any(n in ent["full_text"] for n, _ in _extract_num_units(cl)[0])):
                grounded = True
                break
        if not grounded:
            bad_clauses.append(cl[:140])
    return bad_clauses


def _verify_evidence_map(ev_map: list[Any], corpus: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vers, ungrounded = [], []
    stop_w = {"page", "line", "table", "figure", "section", "results", "methods", "abstract", "introduction", "discussion", "paper", "source"}
    for idx, row in enumerate(ev_map, 1):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("claim_id") or f"CLM{idx}")
        claim = str(row.get("claim") or "").strip()
        sec = str(row.get("section") or row.get("paragraph") or "").strip()
        sref = str(row.get("source_ref") or "").strip()
        ctype = str(row.get("claim_type") or "").strip().lower()
        supplied_verbatim = str(row.get("verbatim_excerpt") or "").strip()
        if not (claim and sec and sref):
            continue
        if ctype in {"planned", "proposed", "gap", "evidence_gap", "workorder"} or PLANNED_REF_RE.search(sref):
            bad_facts = _has_unverified_fact_in_plan(claim, corpus)
            if bad_facts:
                rec = {
                    "claim_id": cid,
                    "source_ref": sref,
                    "status": "unverified_fact_in_plan_tag",
                    "error_code": "UNVERIFIED_FACT_IN_PLAN_TAG",
                    "locator_valid": False,
                    "verbatim_match": False,
                    "numeric_unit_consistent": False,
                    "claim_supported": False,
                    "unverified_embedded_clauses": bad_facts,
                    "matched_file": None,
                    "matched_line": None,
                    "verbatim_excerpt": "",
                }
                vers.append(rec)
                ungrounded.append(rec)
                continue
            vers.append({
                "claim_id": cid,
                "source_ref": sref,
                "status": "planned_or_gap_boundary",
                "locator_valid": True,
                "verbatim_match": None,
                "numeric_unit_consistent": True,
                "claim_supported": True,
                "matched_file": None,
                "matched_line": None,
                "verbatim_excerpt": "",
            })
            continue
        if not corpus:
            rec = {
                "claim_id": cid,
                "source_ref": sref,
                "status": "ungrounded",
                "error_code": "CORPUS_NOT_SUPPLIED",
                "locator_valid": False,
                "verbatim_match": False,
                "numeric_unit_consistent": False,
                "claim_supported": False,
                "matched_file": None,
                "matched_line": None,
                "verbatim_excerpt": "",
            }
            vers.append(rec)
            ungrounded.append(rec)
            continue

        sl = sref.lower()
        ref_files = re.findall(r"[\w.-]+\.(?:pdf|md|docx|txt|csv|json)\b", sref, re.I)
        missing_files = [rf for rf in ref_files if not any(rf.lower() == e["name_l"] or rf.lower() in e["file"].lower() for e in corpus)]
        named_entries = sorted(
            [e for e in corpus if e["name_l"] in sl or e["stem_l"] in sl],
            key=lambda e: (0 if e["name_l"] in sl else 1, -len(e["name_l"])),
        )
        # If a file is explicitly named, restrict search strictly to that file (no cross-file rescue!)
        search_pool = named_entries if named_entries else corpus

        ref_ids = DOI_RE.findall(sref + " " + claim) + [re.sub(r"\s+", "", p) for p in PATENT_RE.findall(sref + " " + claim)]
        bad_ids = [r for r in ref_ids if not any(re.sub(r"\s+", "", r.lower()) in e["cmp_l"] for e in search_pool)]

        lp_nums = set(re.findall(r"(?:#L|Line\s*|Page\s*|P|表|图|S|WP|G|M)\s*(\d+)", sref, re.I))
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
        char_span: list[int] | None = None

        if not missing_files:
            if exp_lm and named_entries:
                tln = int(exp_lm.group(1))
                tln_end = int(exp_lm.group(2)) if exp_lm.group(2) else tln
                ent = named_entries[0]
                if 1 <= tln <= len(ent["lines"]):
                    locator_valid = True
                    matched_ent = ent
                    lo = max(1, tln - 2)
                    hi = min(len(ent["lines"]), max(tln_end, tln) + 5)
                    win_lines = [ent["lines"][i - 1].strip() for i in range(lo, hi + 1) if ent["lines"][i - 1].strip()]
                    window_text = " ".join(win_lines)
                    matched_line = tln
                    matched_page = ent["page_map"].get(tln)
                    best_excerpt = window_text[:220]
                    w_l, w_c = window_text.lower(), re.sub(r"\s+", "", window_text.lower())
                    best_hits = [a for a in anchors if a.lower() in w_l or re.sub(r"\s+", "", a.lower()) in w_c]
                    pos = ent["full_text"].find(ent["lines"][tln - 1].strip())
                    if pos >= 0:
                        char_span = [pos, pos + len(window_text)]
            else:
                # Search best matching window in search_pool (never grant free +2.0 score for line existence!)
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
                            pos = ent["full_text"].find(st)
                            if pos >= 0:
                                char_span = [pos, pos + len(wtxt)]

        if bad_ids:
            locator_valid = False

        # 2. Verbatim match check
        if locator_valid and window_text:
            if supplied_verbatim:
                sv_norm = re.sub(r"\s+", " ", supplied_verbatim).strip().lower()
                wt_norm = re.sub(r"\s+", " ", window_text).strip().lower()
                verbatim_match = (len(sv_norm) >= 8) and (sv_norm in wt_norm or sv_norm[:40] in wt_norm)
            else:
                # Auto-extracted verbatim window must share real anchors or bilingual terms with claim
                biling_ok = any(zh_p.search(claim) and en_p.search(window_text) for zh_p, en_p in _BILINGUAL_CONCEPT_PAIRS)
                verbatim_match = bool(best_hits or biling_ok)
        else:
            verbatim_match = False

        # 3. Numeric & unit consistency check
        if locator_valid and window_text and not bad_ids:
            numeric_unit_consistent, num_mismatches = _check_numeric_unit_consistency(claim, window_text)
        else:
            numeric_unit_consistent, num_mismatches = False, (["ungrounded_identifier"] if bad_ids else ["no_valid_locator"])

        # 4. Claim support (entity/subject + polarity/direction) check
        if locator_valid and window_text and not bad_ids:
            claim_supported, support_reason = _check_claim_support_semantics(claim, window_text, best_hits, ref_ids)
        else:
            claim_supported, support_reason = False, "NO_VALID_LOCATOR"

        ok = bool(locator_valid and verbatim_match and numeric_unit_consistent and claim_supported)
        status = "verified" if ok else ("locator_only_unverified" if locator_valid else "ungrounded")
        rec: dict[str, Any] = {
            "claim_id": cid,
            "source_ref": sref,
            "status": status,
            "locator_valid": locator_valid,
            "verbatim_match": verbatim_match,
            "numeric_unit_consistent": numeric_unit_consistent,
            "claim_supported": claim_supported,
            "support_reason": support_reason,
            "matched_file": matched_ent["file"] if matched_ent else None,
            "file_sha256_prefix": matched_ent["sha256_prefix"] if matched_ent else None,
            "matched_page": matched_page,
            "matched_line": matched_line,
            "char_span": char_span,
            "matched_anchors": best_hits[:8],
            "verbatim_excerpt": best_excerpt,
        }
        if num_mismatches:
            rec["numeric_unit_mismatches"] = num_mismatches
        if bad_ids:
            rec["ungrounded_identifiers"] = bad_ids
        if missing_files:
            rec["missing_referenced_files"] = missing_files
        vers.append(rec)
        if not ok:
            ungrounded.append(rec)
    return vers, ungrounded


def _parse_sections(lines: list[str]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], str]:
    raw_secs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    md_h = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
    nested_h = re.compile(r"^(?:([一二三四五六七八九十]+、\s*.{2,42})|([（(][一二三四五六七八九十\d]+[）)]\s*.{2,42})|(\d+(?:\.\d+){0,2}[\.、\s]\s*[^\s。；]{2,40})|\*\*([一二三四五六七八九十\d（(][^\n]{2,42}?)\*\*)\s*$")

    for lno, ln in enumerate(lines, 1):
        st = ln.strip()
        if not st or st.startswith("|"):
            if cur is not None:
                cur["lines"].append((lno, ln))
            continue
        mm = md_h.match(st)
        mp = None if mm else nested_h.match(st)
        if mp:
            ct = (mp.group(1) or mp.group(2) or mp.group(3) or mp.group(4) or "").strip()
            if len(ct) > 42 or ct.endswith(("。", ".", "；", ";", "，", ",")) or re.match(r"^(?:19|20)\d{2}\b", ct):
                mp = None
            elif not any(any(kw in ct.lower() for kw in kws) for _, kws in SEVEN_ELEMENTS) and not re.match(r"^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]|\d+\.\d+)", ct):
                mp = None
        if mm or mp:
            lvl = len(mm.group(1)) if mm else 2
            title = mm.group(2).strip() if mm else (mp.group(1) or mp.group(2) or mp.group(3) or mp.group(4) or st).strip()  # type: ignore[union-attr]
            if lvl == 1 and not any(any(kw in title.lower() for kw in kws) for _, kws in SEVEN_ELEMENTS):
                continue
            if cur is not None:
                raw_secs.append(cur)
            cur = {"title": title, "start_line": lno, "level": lvl, "lines": []}
        elif cur is not None:
            cur["lines"].append((lno, ln))
    if cur is not None:
        raw_secs.append(cur)

    def _entry(title: str, start_line: int, blines: list[str]) -> dict[str, Any]:
        bt = "\n".join(blines).strip()
        prose = [l.strip() for l in blines if l.strip() and not l.strip().startswith(("|", "```"))]
        tbl = [" ".join(c.strip() for c in l.strip().strip("|").split("|")) for l in blines if l.strip().startswith("|") and not all(re.fullmatch(r":?-{2,}:?", c.strip() or "---") for c in l.strip().strip("|").split("|"))]
        full = "\n".join(prose + tbl)
        return {"heading": title, "start_line": start_line, "han_chars": _count_han("\n".join(prose)), "total_han_with_tables": _count_han(full), "en_words": _count_en(full), "body_text": bt, "prose_lines": prose, "first_prose_line": prose[0] if prose else "", "has_stub_marker": bool(OUTLINE_STUB_RE.search(bt))}

    found: dict[str, dict[str, Any]] = {}
    for sec in raw_secs:
        ent = _entry(sec["title"], sec["start_line"], [l for _, l in sec["lines"]])
        lt, op120 = sec["title"].lower(), ent["body_text"][:120].lower()
        bk, bw = None, 0
        for key, kws in SEVEN_ELEMENTS:
            if key in found:
                continue
            w = (3 if any(k in lt for k in kws) else 0) + (1 if any(k in op120 for k in kws) else 0)
            if w > bw:
                bk, bw = key, w
        if bk and bw >= 1:
            found[bk] = ent

    mode = "headings" if raw_secs else "paragraph_semantic"
    if len(found) < len(SEVEN_ELEMENTS):
        paras: list[tuple[int, list[str]]] = []
        ps, pl = 1, []
        for lno, ln in enumerate(lines, 1):
            st = ln.strip()
            if not st or st.startswith(("|", "```")):
                if pl:
                    paras.append((ps, pl)); pl = []
            else:
                if not pl:
                    ps = lno
                pl.append(ln)
        if pl:
            paras.append((ps, pl))
        used_p, mapped_starts, added = set(), {v["start_line"] for v in found.values()}, False
        cand_pairs: list[tuple[int, str, int]] = []
        for key, kws in SEVEN_ELEMENTS:
            if key in found:
                continue
            cues = SEMANTIC_CUES.get(key, ())
            for idx, (p_start, p_lines) in enumerate(paras):
                if p_start in mapped_starts:
                    continue
                pt = "\n".join(p_lines)
                if len(pt.strip()) < 25:
                    continue
                plw = pt.lower()
                sc = sum(3 for k in kws if k in plw) + sum(1 for c in cues if c.lower() in plw)
                if sc >= 2:
                    cand_pairs.append((sc, key, idx))
        cand_pairs.sort(key=lambda x: (-x[0], x[2]))
        for _, key, idx in cand_pairs:
            if key in found or idx in used_p:
                continue
            used_p.add(idx)
            found[key] = _entry(f"[semantic:{key}]", paras[idx][0], paras[idx][1])
            added = True
        if added and raw_secs:
            mode = "headings+semantic_fallback"
    return found, raw_secs, mode


def _parse_md_tables(text: str) -> list[list[list[str]]]:
    tables, cur = [], []
    for ln in text.splitlines():
        st = ln.strip()
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 3:
            cells = [c.strip() for c in st.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                cur.append(cells)
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    return tables


def _fallback_parse_md(draft_text: str, crosswalk_text: str) -> tuple[dict[str, Any], list[str]]:
    tables = _parse_md_tables(draft_text + "\n\n" + crosswalk_text)
    warns: list[str] = []
    parsed: dict[str, Any] = {"goals": [], "work_packages": [], "milestones": [], "evidence_map": [], "risks": [], "human_confirmations": []}
    for tbl in tables:
        hl = [h.lower() for h in tbl[0]]
        hdr = " ".join(hl)
        rows = tbl[1:]
        col = lambda *ks: next((ci for ci, h in enumerate(hl) if any(k in h for k in ks)), None)
        if ("论断" in hdr or "claim" in hdr) and ("来源" in hdr or "source" in hdr or "引文" in hdr):
            cc, cs, cr = col("论断", "claim") or 0, col("段落", "章节", "section", "paragraph"), col("来源", "source", "引文")
            for idx, r in enumerate(rows, 1):
                if len(r) >= 3 and any(r):
                    parsed["evidence_map"].append({"claim_id": f"MD-CLM{idx}", "claim": r[cc] if cc < len(r) else r[0], "section": r[cs] if (cs is not None and cs < len(r)) else r[1], "source_ref": r[cr] if (cr is not None and cr < len(r)) else r[2]})
        elif ("目标" in hdr or "g1" in hdr or "goal" in hdr) and ("工作包" in hdr or "wp" in hdr) and ("里程碑" in hdr or "milestone" in hdr):
            cg, cw, cm, cu, ck = col("目标", "goal", "g1"), col("工作包", "wp"), col("里程碑", "milestone", "节点", "月份"), col("规划单位", "unit", "工作量"), col("指标", "metric", "考核")
            for idx, r in enumerate(rows, 1):
                if any(t in (r[0] if r else "").lower() for t in ("合计", "总计", "汇总", "total", "sum")):
                    um = re.search(r"\b(\d+(?:\.\d+)?)\b", r[cu] if (cu is not None and cu < len(r)) else " ".join(r[1:]))
                    if um:
                        parsed["planning_unit_total_declared"] = float(um.group(1))
                    continue
                gt = r[cg] if (cg is not None and cg < len(r)) else r[0]
                wt = r[cw] if (cw is not None and cw < len(r)) else (r[1] if len(r) > 1 else "")
                mt = r[cm] if (cm is not None and cm < len(r)) else " ".join(r)
                ut = r[cu] if (cu is not None and cu < len(r)) else ""
                gm, wm, mm = re.search(r"\b(G\d+)\b", gt, re.I), re.search(r"\b(WP\d+)\b", wt, re.I), re.search(r"(?:\bM|第\s*)(\d{1,2})\s*(?:个?月)?\b", mt, re.I)
                um = re.search(r"\b(\d+(?:\.\d+)?)\b", ut) if ut else re.search(r"(\d+(?:\.\d+)?)\s*(?:规划单位|units?)\b", " ".join(r), re.I)
                gid, wid = gm.group(1).upper() if gm else f"G{idx}", wm.group(1).upper() if wm else f"WP{idx}"
                mv, uv = (int(mm.group(1)) if mm else None), (float(um.group(1)) if um else None)
                if mv is None:
                    warns.append(f"Fallback matrix row #{idx} ({gid}/{wid}) lacked anchored month in milestone column")
                parsed["goals"].append({"goal_id": gid, "wp_id": wid, "metric": r[ck] if (ck is not None and ck < len(r)) else (r[-1] if r else "")})
                wpe: dict[str, Any] = {"wp_id": wid, "goal_id": gid}
                if uv is not None:
                    wpe["planning_units"] = uv
                if mv is not None:
                    wpe["end_month"] = mv
                parsed["work_packages"].append(wpe)
                if mv is not None:
                    parsed["milestones"].append({"milestone_id": f"MS{idx}", "goal_id": gid, "wp_id": wid, "month": mv})
        elif ("规划单位" in hdr or "planning_unit" in hdr or "分项" in hdr) and ("合计" not in hdr or len(rows) >= 2):
            cw, cu = col("工作包", "wp", "分项", "任务") or 0, col("规划单位", "unit", "数量", "分值")
            for idx, r in enumerate(rows, 1):
                rt = " ".join(r)
                if any(t in rt.lower() for t in ("合计", "总计", "total")):
                    if cu is not None and cu < len(r):
                        um = re.search(r"\b(\d+(?:\.\d+)?)\b", r[cu])
                        if um:
                            parsed["planning_unit_total_declared"] = float(um.group(1))
                    continue
                um = re.search(r"\b(\d+(?:\.\d+)?)\b", r[cu] if (cu is not None and cu < len(r)) else r[-1])
                if um:
                    wm = re.search(r"\b(WP\d+)\b", r[cw] if cw < len(r) else rt, re.I)
                    wid, val = (wm.group(1).upper() if wm else f"WP{idx}"), float(um.group(1))
                    ex = next((w for w in parsed["work_packages"] if w.get("wp_id") == wid), None)
                    if ex is not None:
                        ex["planning_units"] = val
                    else:
                        parsed["work_packages"].append({"wp_id": wid, "planning_units": val})
        elif ("风险" in hdr or "risk" in hdr) and ("触发" in hdr or "trigger" in hdr or "备选" in hdr or "fallback" in hdr):
            ct, cf = col("触发", "trigger") or 1, col("备选", "fallback", "应对")
            for idx, r in enumerate(rows, 1):
                if len(r) >= 3:
                    parsed["risks"].append({"risk_id": f"MD-R{idx}", "description": r[0], "trigger_signal": r[ct] if ct < len(r) else r[1], "fallback_plan": r[cf] if (cf is not None and cf < len(r)) else r[-1]})
        elif "人工确认" in hdr or "确认项" in hdr or "human confirm" in hdr:
            for idx, r in enumerate(rows, 1):
                if any(r):
                    parsed["human_confirmations"].append({"confirm_id": f"MD-HC{idx}", "item": " ".join(r)})
    return parsed, warns


def _check_ext_author_appropriation(lines: list[str], ext_authors: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hits, warns = [], []
    toks = []
    for a in ext_authors:
        s = str(a).strip()
        if s:
            toks.append(s)
            ps = [p for p in re.split(r"[\s,]+", s) if len(p) >= 3]
            if ps:
                toks.append(ps[-1])
    utoks = sorted(set(toks), key=len, reverse=True)
    if not utoks:
        return hits, warns
    auth_re = re.compile("|".join(re.escape(t) for t in utoks), re.I)
    own_re = re.compile(r"(?:本课题组|本团队|申请人|我们)(?:前期|此前)?(?:与|联合|主导|共同|已)?[^。；;\n]{0,20}?(?:发表|完成|提出|建立|报道|研究|工作)|(?:our\s+(?:group|team|lab)\s+(?:with|and)\s+)", re.I)
    contrast_re = re.compile(r"(?:区别于|相较于|在.{0,18}基础上|参考|基于.{0,12}报道|unlike|building\s+on|extending)", re.I)
    for lno, ln in enumerate(lines, 1):
        cln = GAP_TAG_RE.sub("", ln)
        if not auth_re.search(cln):
            continue
        same = False
        for sent in (s for s in SENT_SPLIT_RE.split(cln) if s.strip()):
            ma, mo = auth_re.search(sent), own_re.search(sent)
            if ma and mo and not _is_negated_or_attributed(sent, mo.start(), mo.end()) and not contrast_re.search(sent[: mo.start()]):
                same = True
                hits.append({"line": lno, "author_matched": ma.group(0), "excerpt": sent.strip()[:180], "issue": "External author tied to first-person applicant ownership in same sentence."})
        if not same:
            mo = own_re.search(cln)
            if mo and not _is_negated_or_attributed(cln, mo.start(), mo.end()):
                warns.append({"line": lno, "excerpt": ln.strip()[:160], "issue": "External author and first-person phrase in adjacent sentences; verify boundary."})
    return hits, warns


def audit_grant_draft(
    draft_path: Path,
    *,
    plan_json_path: Path | None = None,
    case_json_path: Path | None = None,
    workorder_path: Path | None = None,
    applicant_evidence_path: Path | None = None,
    crosswalk_path: Path | None = None,
    corpus_dir: Path | None = None,
    strict_style: bool = False,
) -> dict[str, Any]:
    text, draft_ext = _read_doc(draft_path)
    lines = text.splitlines()
    crosswalk_text = _read_doc(crosswalk_path)[0] if (crosswalk_path and crosswalk_path.is_file()) else ""

    eff_corpus = corpus_dir or (case_json_path.parent if (case_json_path and case_json_path.is_file()) else None)
    eff_wo = workorder_path
    if (not eff_wo or not eff_wo.is_file()) and eff_corpus and eff_corpus.is_dir() and (eff_corpus / "organizer-workorder.json").is_file():
        eff_wo = eff_corpus / "organizer-workorder.json"

    case_cfg, wo_cfg = _load_json(case_json_path), _load_json(eff_wo)
    wo = _extract_workorder(case_cfg, wo_cfg)
    case_id = str(case_cfg.get("case_id") or wo.get("id") or "unknown")
    sb = wo.get("source_basis") if isinstance(wo.get("source_basis"), dict) else {}
    ext_authors = list(sb.get("external_authors") or [])
    p_months = int(wo["planning_months"]) if _is_finite(wo.get("planning_months")) and int(wo["planning_months"]) > 0 else None
    p_cap = float(wo["planning_unit_cap"]) if _is_finite(wo.get("planning_unit_cap")) and float(wo["planning_unit_cap"]) > 0 else None
    dm = DRAFT_SIZE_RE.search(str(wo.get("draft_size") or ""))
    size_range = [int(dm.group(1)), int(dm.group(2))] if dm else None

    # 1. Seven elements & bilingual thickness
    found, _, parse_mode = _parse_sections(lines)
    missing = [k for k, _ in SEVEN_ELEMENTS if k not in found]
    thin, stubs, seven_han = [], [], 0
    for k, _ in SEVEN_ELEMENTS:
        info = found.get(k)
        if not info:
            continue
        seven_han += info["han_chars"]
        eu = _eff_units(info["total_han_with_tables"], info["en_words"])
        if eu < 65:
            thin.append({"element": k, "heading": info["heading"], "han_chars": info["han_chars"], "en_words": info["en_words"], "effective_units": eu})
        if info["has_stub_marker"]:
            stubs.append(k)
    size_ok = True if size_range is None else (size_range[0] <= seven_han <= size_range[1])

    # 2. Evidence boundaries
    app_sup, fun_sup, res_sup = sb.get("applicant_owned_preliminary_results_supplied"), sb.get("actual_funder_notice_supplied"), sb.get("resource_availability_evidence_supplied")
    has_app = bool(app_sup) if isinstance(app_sup, bool) else False
    if applicant_evidence_path and applicant_evidence_path.is_file():
        blob = _read_doc(applicant_evidence_path)[0].strip()
        has_app = (len(blob) > 20 and not _declares_unavail(blob)) if app_sup is not False else False
    if eff_wo and eff_wo.is_file():
        wraw = eff_wo.read_text(encoding="utf-8", errors="replace")
        if re.search(r"applicant.*missing|真实申请人证据.*缺失", wraw, re.I) or _declares_unavail(wraw):
            has_app = False
    not_funder = str(wo.get("authority") or "").strip().lower() == "organizer-workorder-not-funder-policy" or fun_sup is False

    # 3. Anti-fabrication gates (sentence-level negation + quote attribution)
    fab_app, prop_done, fab_fun, fab_res = [], [], [], []
    for lno, ln in enumerate(lines, 1):
        cln = GAP_TAG_RE.sub("", ln)
        for active, pat, bucket in (
            (not has_app, FIRST_PERSON_PRIOR_RE, fab_app),
            (True, PROPOSED_AS_COMPLETED_RE, prop_done),
            (not_funder, FUNDER_POLICY_FAB_RE, fab_fun),
            (res_sup is False, RESOURCE_FAB_RE, fab_res),
        ):
            if not active:
                continue
            for m in pat.finditer(cln):
                if not _is_negated_or_attributed(cln, m.start(), m.end()):
                    bucket.append({"line": lno, "match": m.group(0), "excerpt": ln.strip()[:180]})
                    break
    ext_appr, attr_warns = _check_ext_author_appropriation(lines, ext_authors) if not has_app else ([], [])
    gap_tags = GAP_TAG_RE.findall(text + "\n" + crosswalk_text)

    # 4. Plan / matrix / arithmetic / evidence back-check
    plan_file = _load_json(plan_json_path)
    plan, fb_warns = (plan_file, []) if plan_file else _fallback_parse_md(text, crosswalk_text)
    goals = plan.get("goals") if isinstance(plan.get("goals"), list) else []
    wps = plan.get("work_packages") if isinstance(plan.get("work_packages"), list) else []
    milestones = plan.get("milestones") if isinstance(plan.get("milestones"), list) else []
    ev_map = plan.get("evidence_map") if isinstance(plan.get("evidence_map"), list) else []
    risks = plan.get("risks") if isinstance(plan.get("risks"), list) else []
    hcs = plan.get("human_confirmations") if isinstance(plan.get("human_confirmations"), list) else []

    errors: list[str] = []
    if missing:
        errors.append(f"Missing required grant element(s): {missing}")
    if thin:
        errors.append(f"Substantively thin / outline-only element(s): {[e['element'] for e in thin]}")
    if stubs:
        errors.append(f"Template/stub marker(s) found inside element(s): {stubs}")
    if not size_ok and size_range is not None:
        errors.append(f"Seven-element Chinese character count {seven_han} outside workorder draft_size range {size_range}")
    for cnt, msg in (
        (len(fab_app), f"Detected {len(fab_app)} ungrounded first-person applicant prior-work claim(s)"),
        (len(ext_appr), f"Detected {len(ext_appr)} appropriation(s) of external authors' work"),
        (len(prop_done), f"Detected {len(prop_done)} proposed work/unit/metric claim(s) written as completed/funded/guaranteed"),
        (len(fab_fun), f"Detected {len(fab_fun)} fabricated real funding-agency policy/funding claim(s)"),
        (len(fab_res), f"Detected {len(fab_res)} ungrounded equipment/platform/ethics availability claim(s)"),
    ):
        if cnt:
            errors.append(msg)
    if (not has_app or not_funder or res_sup is False) and not gap_tags:
        errors.append("Missing applicant/funder/resource evidence in workorder but draft contains zero explicit [EVIDENCE_GAP] markers")

    cons_txt = str(wo.get("consistency") or "")
    req_g1_g3, req_1to1 = bool(re.search(r"G1.*G2.*G3|3个.*目标", cons_txt)), bool(re.search(r"1[:：]1|一一对应", cons_txt))
    ng, nw, nm = [], [], []
    for i, g in enumerate(goals, 1):
        if isinstance(g, dict):
            ng.append(g)
        elif isinstance(g, str) and g.strip():
            gm = re.match(r"^\s*(G\d+|O\d+|Aim\s*\d+|目标\s*\d+)\b", g.strip(), re.I)
            ng.append({"goal_id": gm.group(1).upper().replace(" ", "") if gm else f"G{i}", "description": g.strip(), "metric": ""})
        else:
            errors.append(f"Goal entry #{i} has unsupported type {type(g).__name__}")
    for i, w in enumerate(wps, 1):
        if isinstance(w, dict):
            nw.append(w)
        elif isinstance(w, str) and w.strip():
            wm = re.match(r"^\s*(WP\d+|T\d+|Task\s*\d+)\b", w.strip(), re.I)
            nw.append({"wp_id": wm.group(1).upper().replace(" ", "") if wm else f"WP{i}", "title": w.strip()})
        else:
            errors.append(f"Work package entry #{i} has unsupported type {type(w).__name__}")
    for i, m in enumerate(milestones, 1):
        if isinstance(m, dict):
            nm.append(m)
        elif isinstance(m, str) and m.strip():
            mi = re.match(r"^\s*(MS?\d+)\b", m.strip(), re.I)
            mo = re.search(r"\bM(\d{1,2})\b|第\s*(\d{1,2})\s*个?月", m.strip(), re.I)
            nm.append({"milestone_id": mi.group(1).upper() if mi else f"MS{i}", "month": int(mo.group(1) or mo.group(2)) if mo else None, "description": m.strip()})
        else:
            errors.append(f"Milestone entry #{i} has unsupported type {type(m).__name__}")

    g_ids = [str(g.get("goal_id", "")).strip().upper() for g in ng if g.get("goal_id")] or sorted(set(f"G{m}" for t in re.findall(r"\bG([1-9])\b|(?:目标|Objective|Aim)\s*([1-9])", text, re.I) for m in t if m))
    w_ids = [str(w.get("wp_id", "")).strip().upper() for w in nw if w.get("wp_id")] or sorted(set(f"WP{m}" for t in re.findall(r"\bWP([1-9])\b|(?:工作包|任务|Task)\s*([1-9])", text, re.I) for m in t if m))
    g_set = set(g_ids)
    wg_ids = {str(w.get("goal_id", "")).strip().upper() for w in nw if w.get("goal_id")}
    mg_ids = {str(m.get("goal_id", "")).strip().upper() for m in nm if m.get("goal_id")}

    parity_ok = True
    if req_g1_g3:
        for rg in ("G1", "G2", "G3"):
            if rg not in g_set:
                errors.append(f"Workorder requires bounded goal {rg} but it is missing from goals"); parity_ok = False
    if not g_ids:
        errors.append("No structured goals (e.g., G1/G2/G3) found"); parity_ok = False
    if not w_ids:
        errors.append("No structured work packages (e.g., WP1/WP2/WP3) found"); parity_ok = False
    if g_set and w_ids and len(set(w_ids)) < len(g_set):
        errors.append(f"Insufficient work packages for goals: goals={g_ids} vs work_packages={w_ids}"); parity_ok = False
    elif req_1to1 and g_set and w_ids and not wg_ids and len(set(w_ids)) != len(g_set):
        errors.append(f"Strict 1:1 goal-to-WP parity requested: goals={g_ids} vs work_packages={w_ids}"); parity_ok = False
    if wg_ids and g_set and (wg_ids - g_set):
        errors.append(f"Work package(s) reference undeclared goal_id(s): {sorted(wg_ids - g_set)}"); parity_ok = False
    for g in ng:
        gid = str(g.get("goal_id", "")).strip().upper() or "G?"
        if not str(g.get("metric") or "").strip():
            errors.append(f"Goal {gid} missing verifiable future metric"); parity_ok = False
        if wg_ids and gid not in wg_ids:
            errors.append(f"Goal {gid} has no mapped work package"); parity_ok = False
        if mg_ids and gid not in mg_ids:
            errors.append(f"Goal {gid} has no mapped milestone"); parity_ok = False

    u_sum, u_valid, max_m = 0.0, 0, 0
    for wp in nw:
        wid, pu = str(wp.get("wp_id") or "WP?"), wp.get("planning_units")
        if pu is not None:
            if not _is_finite(pu) or float(pu) <= 0:
                errors.append(f"Work package {wid} has invalid planning_units: {pu!r}")
            else:
                u_sum += float(pu); u_valid += 1
        elif plan_file:
            errors.append(f"Work package {wid} missing planning_units allocation")
        sm, em = wp.get("start_month"), wp.get("end_month")
        if sm is not None and em is not None:
            if not _is_finite(sm) or not _is_finite(em):
                errors.append(f"Work package {wid} has non-numeric month span [{sm}, {em}]")
            else:
                smi, emi = int(sm), int(em); max_m = max(max_m, emi)
                if smi < 1 or emi < smi:
                    errors.append(f"Work package {wid} has invalid month span [{smi}, {emi}]")
                if p_months is not None and emi > p_months:
                    errors.append(f"Work package {wid} end_month M{emi} exceeds planning_months {p_months}")
    for ms in nm:
        mid, mv = str(ms.get("milestone_id") or "MS?"), ms.get("month")
        if mv is None or not re.fullmatch(r"[+-]?\d+", str(mv).strip()):
            errors.append(f"Milestone {mid} missing valid integer month: {mv!r}"); continue
        mi = int(str(mv).strip()); max_m = max(max_m, mi)
        if mi < 1 or (p_months is not None and mi > p_months):
            errors.append(f"Milestone {mid} at M{mi} exceeds planning_months [1, {p_months}]")

    u_sum = round(u_sum, 4)
    decl_tot = plan.get("planning_unit_total_declared")
    if decl_tot is not None:
        if not _is_finite(decl_tot):
            errors.append(f"Declared planning_unit_total_declared is non-finite: {decl_tot!r}")
        elif abs(float(decl_tot) - u_sum) > 1e-4:
            errors.append(f"Planning-unit sum mismatch: sum(work_packages)={u_sum} != declared_total={decl_tot}")
    if p_cap is not None:
        if u_valid == 0:
            errors.append("Workorder requires planning_unit breakdown but zero valid allocations found")
        elif u_sum > p_cap + 1e-6:
            errors.append(f"Planning-unit sum {u_sum} exceeds planning_unit_cap {p_cap}")

    valid_ev = sum(1 for r in ev_map if isinstance(r, dict) and str(r.get("claim") or "").strip() and str(r.get("section") or r.get("paragraph") or "").strip() and str(r.get("source_ref") or "").strip())
    if valid_ev < 3:
        errors.append(f"3-tier claim-paragraph-source evidence map has only {valid_ev} valid entries (minimum 3 required)")

    corpus_entries = _collect_corpus_entries(eff_corpus, {p for p in (draft_path, plan_json_path, crosswalk_path) if p})
    ev_vers, ungrounded_ev = _verify_evidence_map(ev_map, corpus_entries)
    if ungrounded_ev:
        tag_list = [f"{u['claim_id']}({u.get('error_code') or u['status']})" for u in ungrounded_ev]
        errors.append(f"Detected {len(ungrounded_ev)} unverified/ungrounded evidence_map entry/entries: {tag_list}")
    # Also scan prose sentences tagged with [EVIDENCE_GAP] or 拟开展/plan for unverified prior/external empirical facts
    plan_tag_prose_leaks = []
    for lno, ln in enumerate(lines, 1):
        for sent in (s.strip() for s in SENT_SPLIT_RE.split(ln) if s.strip()):
            if not (GAP_TAG_RE.search(sent) or re.search(r"(?:\bplan(?:ned)?\b|拟开展|计划阶段)", sent, re.I)):
                continue
            cleaned_s = GAP_TAG_RE.sub("", sent)
            m_prior = re.search(r"(?:前期(?:预实验|实验|队列|数据)|既往文献|文献报道|已测得|实测)", cleaned_s, re.I)
            if not m_prior or _is_negated_or_attributed(cleaned_s, m_prior.start(), m_prior.end()):
                continue
            leaks = _has_unverified_fact_in_plan(cleaned_s, corpus_entries)
            if leaks:
                plan_tag_prose_leaks.append({"line": lno, "clauses": leaks})
    if plan_tag_prose_leaks:
        errors.append(f"UNVERIFIED_FACT_IN_PLAN_TAG in draft prose at line(s) {[x['line'] for x in plan_tag_prose_leaks]}")

    valid_risks = 0
    for r in risks:
        if not isinstance(r, dict):
            continue
        if not str(r.get("trigger_signal") or "").strip() or not str(r.get("fallback_plan") or "").strip():
            errors.append(f"Risk {r.get('risk_id') or 'R?'} missing trigger_signal or fallback_plan")
        else:
            valid_risks += 1
    if valid_risks < 3:
        errors.append(f"Insufficient valid risks with trigger_signal and fallback_plan: {valid_risks} < 3")

    hc_txts = [" ".join(str(hc.get(k, "")) for k in ("category", "item", "reason")) if isinstance(hc, dict) else str(hc) for hc in hcs]
    hc_j = "\n".join(hc_txts)
    if len(hc_txts) < 2:
        errors.append(f"Insufficient human confirmation items: {len(hc_txts)} < 2")
    cov_app_hc = bool(re.search(r"申请人|前期(?:基础|数据|成果|证据)|设备|资源|applicant|preliminary", hc_j, re.I))
    cov_fun_hc = bool(re.search(r"资助|指南|政策|预算|机构|funder|agency|notice|policy", hc_j, re.I))
    if not has_app and not cov_app_hc:
        errors.append("Human confirmations must explicitly include real applicant evidence/resources confirmation")
    if not_funder and not cov_fun_hc:
        errors.append("Human confirmations must explicitly include real funding-agency guidelines/policy confirmation")

    # 5. Expanded Workorder / RFP coverage (real anchor + substantive response) & Collaborator conflicts
    def _resolve_draft_anchor(loc: str) -> tuple[bool, str, str]:
        """Resolve a location anchor like 'grant_chapter.md#研究目标' or '#L42' to actual prose in draft."""
        if not loc or "#" not in loc:
            return False, "", "MISSING_SECTION_OR_LINE_ANCHOR"
        anchor = loc.split("#", 1)[1].strip()
        if not anchor:
            return False, "", "EMPTY_ANCHOR"
        lm = re.fullmatch(r"L(\d+)(?:[-–]L?(\d+))?", anchor, re.I)
        if lm:
            s_l = int(lm.group(1))
            e_l = int(lm.group(2)) if lm.group(2) else min(len(lines), s_l + 6)
            if 1 <= s_l <= len(lines):
                seg = "\n".join(lines[s_l - 1 : min(len(lines), max(e_l, s_l + 2))]).strip()
                return bool(seg), seg, "line_anchor"
            return False, "", f"LINE_OUT_OF_RANGE:{s_l}"
        al = anchor.lower()
        for k, info in found.items():
            if al == k or al in info["heading"].lower() or info["heading"].lower() in al:
                return True, info["body_text"], f"section:{info['heading']}#L{info['start_line']}"
        for lno, ln in enumerate(lines, 1):
            if al in ln.lower() and (ln.strip().startswith("#") or len(ln.strip()) <= 48):
                seg = "\n".join(lines[lno - 1 : min(len(lines), lno + 8)]).strip()
                return True, seg, f"heading_match#L{lno}"
        return False, "", f"ANCHOR_NOT_FOUND_IN_DRAFT:{anchor}"

    _RFP_GENERIC_SHALLOW_RE = re.compile(
        r"(?:待定|待补充|后续考虑|将在后续|拟考虑|暂未明确|TODO|TBD|will\s+consider\s+later|to\s+be\s+determined)",
        re.I,
    )
    _RFP_TOPIC_SUBSTANCE: list[tuple[re.Pattern[str], re.Pattern[str], str]] = [
        (re.compile(r"受众|audience|读者|目标群体", re.I), re.compile(r"(?:基层|医生|临床|医护|家庭|家长|监管|评审|工程师|药师|患者|儿童|青少年|政策制定|医院|社区|研究人员|clinicians?|families|parents|policymakers?|hospitals?|researchers?|engineers?)", re.I), "audience_specificity"),
        (re.compile(r"影响|impact|转化|推广", re.I), re.compile(r"(?:降低|减少|提升|改善|转化|应用|工具包|规程|手册|指南|成本|效率|健康教育|防控|预警|reduce|improve|deploy|guideline|toolkit|protocol)", re.I), "impact_specificity"),
        (re.compile(r"分层|stratif|性别|年龄", re.I), re.compile(r"(?:0[–—-]4|0[–—-]9|10[–—-]19|女性|男性|性别|年龄|分层|亚组|sex|age|stratif|subgroup)", re.I), "stratification_specificity"),
        (re.compile(r"外部文献|申请人证据|区分|boundary", re.I), re.compile(r"(?:EVIDENCE_GAP|外部作者|文献报道|尚未提供|缺失|拟开展|external|gap)", re.I), "provenance_boundary"),
    ]

    def _check_rfp_substantive(req_text: str, kws: list[str], seg: str) -> tuple[bool, str]:
        if not seg or _eff_units(_count_han(seg), _count_en(seg)) < 35:
            return False, "ANCHOR_TEXT_TOO_SHORT"
        req_terms = [str(k).strip() for k in kws if str(k).strip()]
        if not req_terms:
            req_terms = [t for t in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z]{4,}", req_text) if t not in {"明确", "涵盖", "提供", "要求", "说明", "包含", "内容", "回答"}][:6]
        # Find sentences in anchored section that touch the requirement
        sents = [s.strip() for s in SENT_SPLIT_RE.split(seg) if s.strip()]
        matched_sents = [s for s in sents if any(t.lower() in s.lower() for t in req_terms)] if req_terms else sents
        if not matched_sents:
            return False, "NO_TARGETED_RESPONSE_IN_ANCHOR"
        joined = " ".join(matched_sents)
        # Strip bare keywords to verify actual substantive answer beyond echoing the keyword
        stripped = joined
        for t in req_terms:
            stripped = re.sub(re.escape(t), "", stripped, flags=re.I)
        if _RFP_GENERIC_SHALLOW_RE.search(joined) and _eff_units(_count_han(stripped), _count_en(stripped)) < 40:
            return False, "SHALLOW_OR_DEFERRED_RFP_ANSWER"
        if _eff_units(_count_han(stripped), _count_en(stripped)) < 22:
            return False, "KEYWORD_ONLY_WITHOUT_SUBSTANTIVE_ANSWER"
        for topic_re, detail_re, code in _RFP_TOPIC_SUBSTANCE:
            if topic_re.search(req_text + " " + " ".join(req_terms)):
                if not detail_re.search(joined):
                    return False, f"MISSING_SUBSTANTIVE_DETAIL:{code}"
        return True, "substantively_addressed"

    rfp_cov = plan.get("rfp_coverage") if isinstance(plan.get("rfp_coverage"), list) else []
    rfp_by_id = {str(x.get("requirement_id") or "").strip(): x for x in rfp_cov if isinstance(x, dict) and x.get("requirement_id")}
    rfp_omissions, wo_cov_map = [], []
    for req in _expand_workorder_reqs(wo, case_cfg):
        rid, src, kws = req["requirement_id"], req["source"], req.get("keywords") or []
        cov = rfp_by_id.get(rid)
        auto_ok, auto_loc, check_detail = False, "", ""
        if src == "workorder.sections":
            auto_ok, auto_loc, check_detail = (not missing and not thin), "grant_chapter.md#seven_elements", "all_7_elements_substantive"
        elif src == "workorder.draft_size":
            auto_ok, auto_loc, check_detail = size_ok, f"grant_chapter.md#han_chars={seven_han}", f"han_chars={seven_han}"
        elif src == "workorder.consistency":
            auto_ok, auto_loc, check_detail = (parity_ok and (p_cap is None or u_sum <= p_cap + 1e-6)), "grant_plan.json#goals_wp_milestones", "matrix_and_units_ok"
        elif src == "workorder.required_supporting_files":
            auto_ok, auto_loc, check_detail = (valid_ev >= 3 and valid_risks >= 3 and len(hc_txts) >= 2), "supporting_deliverables#1..6", "6_supporting_items_checked"
        elif src == "workorder.scientific_limits":
            # Verify each scientific limit item individually against its specific rule
            lt = req["text"]
            lim_ok = True
            lim_rules = []
            if re.search(r"外部作者|前期工作|自有|applicant|external", lt, re.I):
                lim_ok = lim_ok and (not fab_app) and (not ext_appr)
                lim_rules.append("external_vs_applicant_gate")
            if re.search(r"资助|政策|经费|预算|funder|agency|funding", lt, re.I):
                lim_ok = lim_ok and (not fab_fun) and (not prop_done)
                lim_rules.append("funder_policy_and_unit_gate")
            if re.search(r"设备|伦理|资源|resource|ethics", lt, re.I):
                lim_ok = lim_ok and (not fab_res)
                lim_rules.append("resource_ethics_gate")
            if not lim_rules:
                lim_ok = (not fab_app and not ext_appr and not prop_done and not fab_fun and not ungrounded_ev)
                lim_rules.append("general_provenance_gate")
            lim_ok = lim_ok and (not ungrounded_ev)
            auto_ok, auto_loc, check_detail = lim_ok, f"scientific_limit_gate:{'+'.join(lim_rules)}", "+".join(lim_rules)
        else:
            # Strict RFP check: MUST bind to a real anchor in grant_chapter.md AND substantively answer the requirement!
            if cov and str(cov.get("status", "")).lower() in {"covered", "addressed", "needs_human_confirmation"}:
                loc_str = str(cov.get("location") or "").strip()
                anc_ok, seg_txt, anc_info = _resolve_draft_anchor(loc_str)
                if not anc_ok:
                    auto_ok, auto_loc, check_detail = False, loc_str, anc_info
                else:
                    sub_ok, sub_reason = _check_rfp_substantive(req["text"], kws, seg_txt)
                    auto_ok, auto_loc, check_detail = sub_ok, anc_info, sub_reason
            else:
                # Fallback: search actual draft sections for a substantive answer (never pass on single keyword!)
                best_sec_ok = False
                for k_sec, info_sec in found.items():
                    sub_ok, sub_reason = _check_rfp_substantive(req["text"], kws, info_sec["body_text"])
                    if sub_ok:
                        best_sec_ok = True
                        auto_ok, auto_loc, check_detail = True, f"section:{info_sec['heading']}#L{info_sec['start_line']}", sub_reason
                        break
                if not best_sec_ok:
                    auto_ok, auto_loc, check_detail = False, "", "RFP_UNCOVERED_OR_KEYWORD_ONLY"
        covered = auto_ok
        wo_cov_map.append({"requirement_id": rid, "source": src, "covered": covered, "location": auto_loc, "verification": check_detail})
        if not covered and src in {"rfp_requirements", "workorder.scientific_limits"}:
            rfp_omissions.append({"requirement_id": rid, "requirement": req["text"], "reason": check_detail})
    if rfp_omissions:
        errors.append(f"Missed or non-substantively answered {len(rfp_omissions)} required RFP/limit item(s): {[o['requirement_id'] + ':' + o['reason'] for o in rfp_omissions]}")

    collabs = case_cfg.get("collaborator_comments") or wo.get("collaborator_comments") or plan.get("collaborator_comments") or []
    c_decs = plan.get("conflict_decisions") if isinstance(plan.get("conflict_decisions"), list) else []
    unres_conf = []
    if isinstance(collabs, list) and len(collabs) >= 2:
        c_groups = sorted({str(c.get("conflict_group") or "default_conflict") for c in collabs if isinstance(c, dict) and (c.get("conflicts_with") or c.get("conflict_group"))})
        if c_groups:
            valid_decs = [d for d in c_decs if isinstance(d, dict) and str(d.get("chosen_option") or "").strip() and str(d.get("rationale") or "").strip()]
            dec_groups = {str(d.get("conflict_group") or "default_conflict") for d in valid_decs}
            missing_groups = [g for g in c_groups if g not in dec_groups] if len(c_groups) > 1 else ([] if valid_decs else c_groups)
            if missing_groups:
                unres_conf.append({"issue": f"Conflicting collaborator groups missing decision log: {missing_groups}"})
                errors.append(f"Conflicting collaborator feedback missing conflict_decisions for group(s): {missing_groups}")

    # 6. Style scan
    style_warns = []
    for lno, ln in enumerate(lines, 1):
        for kind, pat in (("priority_hype", PRIORITY_HYPE_RE), ("policy_slogan", POLICY_SLOGAN_RE), ("ai_cliche", AI_CLICHE_RE)):
            m = pat.search(ln)
            if m and not _is_negated_or_attributed(ln, m.start(), m.end()):
                style_warns.append({"kind": kind, "line": lno, "term": m.group(0), "excerpt": ln.strip()[:140]})
    for k in ("background", "goals", "innovation"):
        info = found.get(k)
        if info and DEFENSIVE_OPEN_RE.search(info["first_prose_line"]):
            style_warns.append({"kind": "defensive_opening", "line": info["start_line"], "term": info["first_prose_line"][:36], "excerpt": info["first_prose_line"][:140]})
    if strict_style and style_warns:
        errors.append(f"Strict style mode failed with {len(style_warns)} hype/cliché/defensive warning(s)")

    sup_status = {
        "1_editable_seven_element_draft": not missing and not thin and not stubs and size_ok,
        "2_three_tier_evidence_map": valid_ev >= 3 and not ungrounded_ev,
        "3_goal_wp_milestone_metric_matrix": parity_ok and bool(g_ids) and bool(w_ids),
        "4_planning_unit_breakdown": (p_cap is None or (u_valid > 0 and u_sum <= p_cap + 1e-6)) and (p_months is None or max_m <= p_months),
        "5_risks_and_fallbacks_ge3": valid_risks >= 3,
        "6_human_confirmations_ge2": len(hc_txts) >= 2 and (has_app or cov_app_hc) and (not not_funder or cov_fun_hc),
    }
    honest_gaps = [g for cond, g in ((not has_app, "applicant_owned_preliminary_results_missing"), (not_funder, "actual_funder_notice_missing"), (res_sup is False, "resource_availability_evidence_missing")) if cond]

    ev_by_id = {v["claim_id"]: v for v in ev_vers}
    enriched_ev = []
    for idx, r in enumerate(ev_map, 1):
        if isinstance(r, dict):
            item = dict(r)
            v = ev_by_id.get(str(r.get("claim_id") or f"CLM{idx}"))
            if v and v.get("verbatim_excerpt"):
                item.update({"verified_file": v["matched_file"], "verified_line": v["matched_line"], "verbatim_excerpt": v["verbatim_excerpt"]})
            enriched_ev.append(item)
    preserved = [x for x in (
        f"published_paper={sb.get('published_paper')}" if sb.get("published_paper") else None,
        f"external_authors={ext_authors}" if ext_authors else None,
        f"planning_months={p_months}" if p_months is not None else None,
        f"planning_unit_cap={p_cap}" if p_cap is not None else None,
        f"authority={wo.get('authority')}" if wo.get("authority") else None,
    ) if x]
    contract_helper = {
        "case_id": case_id,
        "recommended_status": "partial_with_gaps" if honest_gaps else "ok",
        "deliverable": {"primary_draft": draft_path.name, "supporting_files": [p.name for p in (crosswalk_path, plan_json_path) if p and p.is_file()]},
        "preserved_facts": preserved,
        "evidence_map": enriched_ev,
        "change_log": {"human_confirmations": hcs, "rfp_coverage": rfp_cov, "conflict_decisions": c_decs, "unresolved_gaps": honest_gaps},
    }

    return {
        "passed": len(errors) == 0,
        "case_id": case_id,
        "draft_extractor": draft_ext,
        "parse_mode": parse_mode,
        "seven_elements": {
            "found_elements": {k: {"heading": v["heading"], "start_line": v["start_line"], "han_chars": v["han_chars"], "en_words": v["en_words"], "effective_units": _eff_units(v["total_han_with_tables"], v["en_words"])} for k, v in found.items()},
            "missing_elements": missing, "thin_elements": thin, "stub_elements": stubs,
            "seven_section_han_chars": seven_han, "total_doc_han_chars": _count_han(text),
            "draft_size_range": size_range, "draft_size_ok": size_ok,
        },
        "provenance_and_boundary": {
            "applicant_evidence_present": has_app, "actual_funder_notice_supplied": not not_funder,
            "resource_availability_evidence_supplied": res_sup, "external_authors_checked": ext_authors,
            "explicit_evidence_gap_count": len(gap_tags), "fabricated_applicant_claims": fab_app,
            "external_author_appropriations": ext_appr, "attribution_warnings": attr_warns,
            "proposed_as_completed_claims": prop_done, "funder_policy_fabrications": fab_fun, "resource_fabrications": fab_res,
        },
        "evidence_map_verification": {
            "corpus_files_indexed": [e["file"] for e in corpus_entries],
            "checked_count": len(ev_vers), "verified_count": sum(1 for v in ev_vers if v["status"] == "verified"),
            "planned_or_gap_count": sum(1 for v in ev_vers if v["status"] == "planned_or_gap_boundary"),
            "ungrounded_count": len(ungrounded_ev), "details": ev_vers,
        },
        "plan_consistency": {
            "goal_ids": g_ids, "wp_ids": w_ids, "parity_ok": parity_ok, "planning_months": p_months,
            "max_milestone_month": max_m, "planning_unit_cap": p_cap, "planning_unit_sum": u_sum,
            "planning_unit_total_declared": decl_tot, "fallback_parse_warnings": fb_warns,
        },
        "supporting_files_check": {
            "all_six_satisfied": all(sup_status.values()), "details": sup_status,
            "evidence_map_valid_count": valid_ev, "risks_valid_count": valid_risks,
            "human_confirmations_count": len(hc_txts), "covers_applicant_confirmation": cov_app_hc, "covers_funder_confirmation": cov_fun_hc,
        },
        "rfp_and_conflicts": {"workorder_coverage_map": wo_cov_map, "rfp_omissions": rfp_omissions, "unresolved_conflicts": unres_conf, "conflict_decisions_count": len(c_decs)},
        "style_warnings": style_warns, "errors": errors, "output_contract_preview": contract_helper,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit 7-element grant draft (.md/.docx/.pdf), extract /input fact index, and verify source_ref grounding.")
    ap.add_argument("--draft", default="", help="Grant proposal chapter draft (.md, .docx, .pdf, or .txt).")
    ap.add_argument("--plan-json", default="", help="Optional structured grant_plan.json.")
    ap.add_argument("--case-json", default="", help="Optional /input/case.json.")
    ap.add_argument("--workorder", default="", help="Optional /input/organizer-workorder.json (auto-probed if omitted).")
    ap.add_argument("--applicant-evidence", default="", help="Optional applicant preliminary evidence file.")
    ap.add_argument("--crosswalk", default="", help="Optional standalone element_crosswalk.md.")
    ap.add_argument("--corpus-dir", "--input-dir", dest="corpus_dir", default="", help="/input directory for corpus indexing and evidence_map source_ref verification.")
    ap.add_argument("--extract-corpus-index", default="", help="Extract structured /input fact & boundary index JSON (runs standalone without --draft).")
    ap.add_argument("--strict-style", action="store_true", help="Treat style warnings as blocking errors.")
    ap.add_argument("--emit-contract-helper", default="", help="Optional JSON path for 5-field output_contract helper.")
    ap.add_argument("--out-json", default="", help="Output audit JSON path (required when --draft is provided).")
    args = ap.parse_args(argv)

    if args.extract_corpus_index:
        in_dir = Path(args.corpus_dir) if args.corpus_dir else (Path(args.case_json).parent if args.case_json else None)
        if not in_dir or not in_dir.exists():
            ap.error("--extract-corpus-index requires --input-dir / --corpus-dir (or --case-json)")
        idx = extract_corpus_index(in_dir, case_json_path=Path(args.case_json) if args.case_json else None, workorder_path=Path(args.workorder) if args.workorder else None)
        ip = Path(args.extract_corpus_index)
        ip.parent.mkdir(parents=True, exist_ok=True)
        ip.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.draft:
            print(json.dumps({"mode": "extract_corpus_index", "files_indexed": len(idx["files"]), "numeric_facts": len(idx["numeric_facts"]), "candidate_evidence_map": len(idx["candidate_evidence_map"]), "out": str(ip)}, ensure_ascii=False, indent=2))
            return 0

    if not args.draft or not args.out_json:
        ap.error("--draft and --out-json are required unless running standalone --extract-corpus-index")

    rep = audit_grant_draft(
        Path(args.draft),
        plan_json_path=Path(args.plan_json) if args.plan_json else None,
        case_json_path=Path(args.case_json) if args.case_json else None,
        workorder_path=Path(args.workorder) if args.workorder else None,
        applicant_evidence_path=Path(args.applicant_evidence) if args.applicant_evidence else None,
        crosswalk_path=Path(args.crosswalk) if args.crosswalk else None,
        corpus_dir=Path(args.corpus_dir) if args.corpus_dir else None,
        strict_style=args.strict_style,
    )
    op = Path(args.out_json); op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.emit_contract_helper:
        hp = Path(args.emit_contract_helper); hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(rep["output_contract_preview"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": rep["passed"], "parse_mode": rep["parse_mode"],
        "seven_section_han_chars": rep["seven_elements"]["seven_section_han_chars"],
        "all_six_supporting_ok": rep["supporting_files_check"]["all_six_satisfied"],
        "evidence_verified": rep["evidence_map_verification"]["verified_count"],
        "evidence_ungrounded": rep["evidence_map_verification"]["ungrounded_count"],
        "planning_unit_sum": rep["plan_consistency"]["planning_unit_sum"],
        "error_count": len(rep["errors"]), "style_warning_count": len(rep["style_warnings"]), "errors": rep["errors"],
    }, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
