#!/usr/bin/env python3
"""Zero-dependency offline document, section, paragraph, six-facet companion, and image-fingerprint IO core.

Design principles:
- Pure Python 3.10+ stdlib capable (offline Linux containers with --network none), including:
  * 4-tier PDF extraction cascade: pdfplumber -> pypdf -> pdftotext CLI -> pure-stdlib FlateDecode/BT-ET fallback
  * OOXML .docx paragraph+table parsing via zipfile+XML (or python-docx when available)
  * 8/16-bit and palette PNG decoding with 128-bit HV-dHash + 16x16 Pearson correlation (white-canvas chart protected)
- Handles wrapped PDF paragraphs, cross-line hyphenation, singular/bilingual headings (METHOD / ABSTRACT RESUMO),
  superscript numeric medical citations, and noun-vs-verb causal disambiguation ('causes of death' vs 'X causes Y').
- Provides shared deterministic six-facet companion auditing and structured semantic_review_checklist generation.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

SECTION_ROLES: list[tuple[str, tuple[str, ...]]] = [
    ("abstract", ("abstract", "resumo", "摘要", "内容提要")),
    ("introduction", ("introduction", "background", "motivation", "introdução", "引言", "背景", "绪论")),
    ("related_work", ("related work", "literature review", "相关工作", "文献综述")),
    ("methods", (
        "materials and methods", "patients and methods", "study design", "statistical analysis",
        "methodology", "methods", "method", "experimental", "métodos", "método",
        "方法", "材料与方法", "实验设计", "研究方法", "资料与方法",
    )),
    ("results", ("experimental results", "results", "result", "findings", "outcomes", "resultados", "结果", "实验结果", "研究结果")),
    ("discussion", ("discussion", "limitations", "discussão", "讨论", "分析与讨论", "局限性")),
    ("conclusion", ("concluding remarks", "conclusions", "conclusion", "conclusão", "summary", "结论", "总结")),
    ("references", ("references", "reference", "bibliography", "works cited", "referências", "参考文献")),
    ("declarations", (
        "acknowledgments", "acknowledgements", "funding", "conflict of interest", "competing interests",
        "ethics", "data availability", "code availability", "author contributions",
        "致谢", "基金", "利益冲突", "伦理声明", "数据可用性",
    )),
    ("appendix", ("appendix", "supplementary", "supplemental", "附录", "补充材料")),
]

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TEX_HEADING_RE = re.compile(r"^\\(?:sub)*section\*?\{(.+?)\}")
PLAIN_NUM_HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+){0,2}|[IVX]{1,4})[\.\s、]+|[一二三四五六七八九十]+[、.\s]+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 &/\-]{2,64}|[\u4e00-\u9fff]{2,18})\s*:?\s*$"
)
KNOWN_SINGLE_HEADINGS = {kw for _, kws in SECTION_ROLES for kw in kws}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
TEXT_DOC_EXTS = {".md", ".txt", ".tex", ".pdf", ".docx"}

LIST_OR_CAPTION_START_RE = re.compile(
    r"^(?:[-*•]\s+|\d+[.)]\s+|\[\d+\]\s+|(?:Fig(?:ure)?\.?|Table|Supplementary\s+(?:Figure|Table)|图|表)\s*\d+\s*[:.：\-]|\[Page\s+\d+\]|\[Table\s+\d+\])",
    re.I,
)

# High-precision causal verb vs epidemiology/clinical noun ("main causes of death") disambiguation
CAUSAL_VERB_CANDIDATE_RE = re.compile(
    r"\b(?:proves?|proved|causes?|caused|directly\s+(?:causes?|prevents?|cures?|reduces?|increases?|improves?|leads?\s+to)|guarantees?|guaranteed|establishes?\s+caus\w*|conclusively\s+demonstrates?|universally\s+(?:prevents?|cures?|causes?))\b|"
    r"证明了|必然导致|直接导致|完全证明|确立了因果|普遍成立",
    re.I,
)
CAUSE_NOUN_PREFIX_RE = re.compile(
    r"\b(?:the|a|an|this|that|these|those|main|major|minor|leading|primary|secondary|common|underlying|external|internal|"
    r"multiple|other|various|several|potential|possible|probable|known|unknown|specific|specific\s+underlying|root|exact|"
    r"direct|indirect|preventable|avoidable|all|most|frequent|principal|top|two|three|four|five|its|their|our|his|her|by|for|from|of|with|without|as)\s+$",
    re.I,
)
CAUSE_NOUN_SUFFIX_RE = re.compile(r"^\s+(?:of|for|in|among|are|were|include|included|including|remained|remain)\b", re.I)

PERCENT_RE = re.compile(r"(?<![\d.,])([+−-]?\d+(?:\.\d+)?)\s*[%％]")
STAT_CONST_PCT_AFTER_RE = re.compile(
    r"^\s*(?:CI|CIs|IC|confidence|credible|intervalo|power|significance|alpha|level|tolerance|bootstrap|prediction)\b",
    re.I,
)
STAT_CONST_PCT_BEFORE_RE = re.compile(
    r"\b(?:IC\s*|CI\s*|alpha\s*=\s*|level\s+of\s+|power\s+of\s+|at\s+the\s+|two-sided\s+|one-sided\s+)$",
    re.I,
)

# Superscript numeric citation detection (e.g., medical journals like SciELO / Vancouver superscript style: "children.1,2" or "rate.15")
SUPERSCRIPT_CITE_RE = re.compile(r"[a-zA-Z\)\]][.,;:](?:\s?)(\d{1,2}(?:[-–,]\d{1,2}){0,4})(?=\s|$|[A-Z])")
BRACKET_NUM_CITE_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
TEX_CITE_RE = re.compile(r"\\cite[pt]?\*?\{([^}]+)\}")
AY_CITE_RE = re.compile(r"\(([A-Z][A-Za-z'-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'-]+|\s+et\s+al\.)?),?\s*((?:19|20)\d{2}[a-z]?)\)")
BIB_NUM_ENTRY_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")
RETRACTION_RE = re.compile(r"\b(?:retracted|retraction|withdrawn|erratum|expression\s+of\s+concern)\b|撤稿|撤回|勘误", re.I)
PREPRINT_RE = re.compile(r"\b(?:arXiv|bioRxiv|medRxiv|chemRxiv|SSRN|preprint)\b", re.I)


def is_true_causal_overclaim(sentence: str) -> bool:
    """Return True only when a sentence contains a genuine causal assertion verb, excluding clinical nouns like 'causes of death'."""
    for m in CAUSAL_VERB_CANDIDATE_RE.finditer(sentence):
        tok = m.group(0).lower().strip()
        if tok in ("cause", "causes"):
            before = sentence[max(0, m.start() - 32):m.start()]
            after = sentence[m.end():min(len(sentence), m.end() + 28)]
            if CAUSE_NOUN_PREFIX_RE.search(before) or CAUSE_NOUN_SUFFIX_RE.match(after):
                continue
        return True
    return False


def extract_outcome_percents(text: str) -> list[tuple[str, Decimal]]:
    """Extract empirical percentages while filtering statistical constants (95% CI / IC95% / 80% power / 5% alpha)."""
    out: list[tuple[str, Decimal]] = []
    for m in PERCENT_RE.finditer(text):
        raw_num = m.group(1).replace("−", "-")
        val = Decimal(raw_num)
        after = text[m.end():m.end() + 36]
        before = text[max(0, m.start() - 28):m.start()]
        if STAT_CONST_PCT_AFTER_RE.match(after):
            continue
        if val in (Decimal("95"), Decimal("99"), Decimal("90"), Decimal("80"), Decimal("5"), Decimal("1"), Decimal("10")):
            if STAT_CONST_PCT_BEFORE_RE.search(before):
                continue
            # Also ignore standalone '95%' when CI/confidence/IC appears within +-40 chars
            win = text[max(0, m.start() - 40):min(len(text), m.end() + 40)]
            if val in (Decimal("95"), Decimal("99"), Decimal("90")) and re.search(r"\b(?:CI|IC|confidence|interval|intervalo)\b", win, re.I):
                continue
        out.append((m.group(0), val))
    return out


def detect_section_role(heading: str) -> str:
    """Map a heading title to a canonical academic section role with exact/prefix priority and bilingual support."""
    cleaned = re.sub(r"^(?:\d+(?:\.\d+){0,2}|[IVX]{1,4}|[一二三四五六七八九十]+)[\.\s、:：\-]+", "", heading.strip())
    low = cleaned.lower().strip(" :：.-")
    if not low:
        return "other"

    if ("result" in low and "discussion" in low) or ("结果" in low and "讨论" in low):
        return "results_discussion"
    if low.startswith("summary of ") and any(w in low for w in ("result", "finding", "outcome", "experiment")):
        return "results"
    if low in ("summary", "executive summary"):
        return "abstract"

    low_compact = re.sub(r"\s+", "", low)
    for role, kws in SECTION_ROLES:
        for kw in kws:
            if low == kw or (len(low) <= 22 and low_compact == kw.replace(" ", "")):
                return role

    prefix_candidates: list[tuple[int, str]] = []
    for role, kws in SECTION_ROLES:
        for kw in kws:
            if low.startswith(kw + " ") or low.startswith(kw + ":") or low.startswith(kw + "-") or low.startswith(kw + "："):
                prefix_candidates.append((len(kw), role))
    if prefix_candidates:
        prefix_candidates.sort(key=lambda x: -x[0])
        return prefix_candidates[0][1]

    pos_candidates: list[tuple[int, int, str]] = []
    for role, kws in SECTION_ROLES:
        for kw in kws:
            idx = low.find(kw)
            if idx >= 0:
                if kw.isascii():
                    before_ok = (idx == 0) or (not low[idx - 1].isalnum())
                    after_idx = idx + len(kw)
                    after_ok = (after_idx == len(low)) or (not low[after_idx].isalnum())
                    if not (before_ok and after_ok):
                        continue
                pos_candidates.append((idx, -len(kw), role))
    if pos_candidates:
        pos_candidates.sort()
        return pos_candidates[0][2]

    return "other"


def _is_plain_heading(line: str) -> str | None:
    st = line.strip()
    if not st or len(st) > 90 or st.startswith("[Page ") or (st.endswith(".") and not re.match(r"^\d+\.\s+\S+$", st)):
        return None
    if MD_HEADING_RE.match(st) or TEX_HEADING_RE.match(st):
        return None
    if any(ch in st for ch in (",", ";", "，", "；", "=", "%")):
        return None
    m = PLAIN_NUM_HEADING_RE.match(st)
    if not m:
        return None
    candidate = m.group(1).strip(" :：")
    low = candidate.lower()
    if low in KNOWN_SINGLE_HEADINGS:
        return st
    if any(low == kw or low.startswith(kw + " ") for kw in KNOWN_SINGLE_HEADINGS):
        return st
    if re.match(r"^\d+(?:\.\d+)?\.?\s+[A-Z][A-Z\s&/\-]{3,45}$", st):
        return st
    return None


def extract_docx_text_stdlib(fp: Path) -> tuple[str, str]:
    """Extract paragraph and table text from .docx using python-docx if available, else pure stdlib zipfile+XML."""
    try:
        import docx  # type: ignore
        doc = docx.Document(str(fp))
        parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for ti, tbl in enumerate(doc.tables, 1):
            parts.append(f"[Table {ti}]")
            for row in tbl.rows:
                parts.append(" | ".join(c.text.strip().replace("\n", " ") for c in row.cells))
        return "\n".join(parts), "python-docx"
    except Exception:
        pass

    with zipfile.ZipFile(fp, "r") as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts: list[str] = []
    body = root.find("w:body", ns)
    if body is None:
        return "", "stdlib-xml-empty"
    tbl_idx = 0
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            txt = "".join(t.text or "" for t in child.findall(".//w:t", ns)).strip()
            if txt:
                parts.append(txt)
        elif tag == "tbl":
            tbl_idx += 1
            parts.append(f"[Table {tbl_idx}]")
            for tr in child.findall(".//w:tr", ns):
                cells = ["".join(t.text or "" for t in tc.findall(".//w:t", ns)).strip().replace("\n", " ") for tc in tr.findall("./w:tc", ns)]
                if any(cells):
                    parts.append(" | ".join(cells))
    return "\n".join(parts), "stdlib-ooxml"


def _unescape_pdf_literal(raw_bytes: bytes) -> str:
    """Decode a PDF literal string (...) handling escapes and UTF-16BE BOM."""
    out = bytearray()
    i = 0
    n = len(raw_bytes)
    while i < n:
        b = raw_bytes[i]
        if b == 92 and i + 1 < n:  # backslash
            nxt = raw_bytes[i + 1]
            esc_map = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
            if nxt in esc_map:
                out.append(esc_map[nxt])
                i += 2
            elif 48 <= nxt <= 55:  # octal
                j = i + 1
                oct_digits = []
                while j < n and len(oct_digits) < 3 and 48 <= raw_bytes[j] <= 55:
                    oct_digits.append(chr(raw_bytes[j]))
                    j += 1
                out.append(int("".join(oct_digits), 8) & 0xFF)
                i = j
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(b)
            i += 1
    if out.startswith(b"\xfe\xff"):
        return out[2:].decode("utf-16-be", errors="ignore")
    return out.decode("latin-1", errors="ignore")


def _extract_pdf_text_pure_stdlib(fp: Path, rel: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Tier-4 zero-dependency pure-stdlib PDF text extractor for uncompressed/FlateDecode BT..ET content streams."""
    raw = fp.read_bytes()
    gaps: list[dict[str, Any]] = []
    streams: list[bytes] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        blob = m.group(1)
        try:
            dec = zlib.decompress(blob)
            if b"BT" in dec and (b"Tj" in dec or b"TJ" in dec):
                streams.append(dec)
        except Exception:
            if b"BT" in blob and (b"Tj" in blob or b"TJ" in blob):
                streams.append(blob)

    pages_out: list[str] = []
    pages_meta: list[dict[str, Any]] = []
    for pno, strm in enumerate(streams, 1):
        chunks: list[str] = []
        for bt in re.finditer(rb"BT(.*?)ET", strm, re.S):
            blk = bt.group(1)
            for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)\s*Tj|\[(.*?)\]\s*TJ", blk, re.S):
                if tm.group(1) is None:
                    lit = tm.group(0)
                    lit = lit[1:lit.rfind(b")")]
                    s = _unescape_pdf_literal(lit)
                    if s:
                        chunks.append(s)
                else:
                    arr_parts = []
                    for sm in re.finditer(rb"\((?:\\.|[^\\()])*\)", tm.group(1)):
                        arr_parts.append(_unescape_pdf_literal(sm.group(0)[1:-1]))
                    if arr_parts:
                        chunks.append("".join(arr_parts))
        pt = re.sub(r"[ \t]+", " ", "\n".join(c.strip() for c in chunks if c.strip())).strip()
        # Keep only printable ASCII/Latin/CJK text density
        printable_ratio = (sum(1 for ch in pt if ch.isprintable() or ch in "\n\t") / max(len(pt), 1)) if pt else 0.0
        if printable_ratio < 0.75:
            pt = ""
        low_text = len(pt) < 30
        pages_meta.append({"page": pno, "chars": len(pt), "low_text": low_text})
        if pt:
            pages_out.append(f"[Page {pno}]\n{pt}")

    joined = "\n\n".join(pages_out)
    if joined:
        gaps.append({
            "code": "PDF_STDLIB_FALLBACK_USED",
            "severity": "info",
            "source": rel,
            "message": f"Extracted {len(joined)} chars across {len(pages_out)} stream(s) using pure-stdlib FlateDecode fallback (pdfplumber/pypdf/pdftotext unavailable); custom font CMap glyphs may require verification.",
        })
    return joined, pages_meta, gaps


def extract_pdf_text_cascade(fp: Path, rel: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str]:
    """Extract PDF page-by-page using pypdf (column-ordered) -> pdfplumber -> pdftotext CLI -> pure-stdlib FlateDecode fallback."""
    gaps: list[dict[str, Any]] = []
    pages_meta: list[dict[str, Any]] = []

    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(fp))
        pages_out: list[str] = []
        total_chars = 0
        for pno, page in enumerate(reader.pages, 1):
            pt = (page.extract_text() or "").strip()
            total_chars += len(pt)
            low_text = len(pt) < 30
            pages_meta.append({"page": pno, "chars": len(pt), "low_text": low_text})
            if low_text:
                gaps.append({
                    "code": "PDF_LOW_TEXT_PAGE",
                    "severity": "warning",
                    "source": f"{rel}#page={pno}",
                    "message": f"Page {pno} extracted only {len(pt)} chars (<30); possible scanned page requiring OCR.",
                })
            pages_out.append(f"[Page {pno}]\n{pt}")
        if total_chars >= 30 or not reader.pages:
            return "\n\n".join(pages_out), pages_meta, gaps, "pypdf"
        pages_meta.clear()
        gaps.clear()
    except Exception:
        pages_meta.clear()
        gaps.clear()

    try:
        import pdfplumber  # type: ignore
        pages_out = []
        with pdfplumber.open(str(fp)) as pdf:
            for pno, page in enumerate(pdf.pages, 1):
                pt = (page.extract_text() or "").strip()
                low_text = len(pt) < 30
                pages_meta.append({"page": pno, "chars": len(pt), "low_text": low_text})
                if low_text:
                    gaps.append({
                        "code": "PDF_LOW_TEXT_PAGE",
                        "severity": "warning",
                        "source": f"{rel}#page={pno}",
                        "message": f"Page {pno} extracted only {len(pt)} chars (<30); possible scanned page requiring OCR.",
                    })
                pages_out.append(f"[Page {pno}]\n{pt}")
        return "\n\n".join(pages_out), pages_meta, gaps, "pdfplumber"
    except Exception:
        pages_meta.clear()
        gaps.clear()

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run([pdftotext, str(fp), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if proc.returncode == 0:
                raw_pages = proc.stdout.split("\x0c")
                if raw_pages and not raw_pages[-1].strip():
                    raw_pages = raw_pages[:-1]
                pages_out = []
                for pno, pt_raw in enumerate(raw_pages, 1):
                    pt = pt_raw.strip()
                    low_text = len(pt) < 30
                    pages_meta.append({"page": pno, "chars": len(pt), "low_text": low_text})
                    if low_text:
                        gaps.append({
                            "code": "PDF_LOW_TEXT_PAGE",
                            "severity": "warning",
                            "source": f"{rel}#page={pno}",
                            "message": f"Page {pno} extracted only {len(pt)} chars (<30); possible scanned page requiring OCR.",
                        })
                    pages_out.append(f"[Page {pno}]\n{pt}")
                return "\n\n".join(pages_out), pages_meta, gaps, "pdftotext"
        except Exception as exc:
            gaps.append({"code": "PDF_CLI_ERROR", "severity": "error", "source": rel, "message": str(exc)})

    # Tier 4: Pure Python stdlib FlateDecode BT..ET fallback
    try:
        std_txt, std_meta, std_gaps = _extract_pdf_text_pure_stdlib(fp, rel)
        if std_txt and len(std_txt) >= 30:
            gaps.extend(std_gaps)
            return std_txt, std_meta, gaps, "stdlib-pdf-fallback"
    except Exception:
        pass

    gaps.append({
        "code": "PDF_EXTRACTOR_UNAVAILABLE",
        "severity": "error",
        "source": rel,
        "message": "No external PDF extractor available and stdlib stream fallback yielded insufficient text (likely scanned or custom-CMap PDF); content remains unverified.",
    })
    return "", [], gaps, "unavailable"


def load_documents(
    target: Path,
    *,
    extra_exts: set[str] | None = None,
    ignore_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    """Load readable documents from a file or directory and also return discovered image paths."""
    allowed = set(TEXT_DOC_EXTS) | (extra_exts or set())
    ignored = {"task.md", "readme.md"} | (ignore_names or set())
    if target.is_file():
        all_files = [target]
    elif target.is_dir():
        all_files = sorted(p for p in target.rglob("*") if p.is_file())
    else:
        return [], [{"code": "INPUT_NOT_FOUND", "severity": "error", "source": str(target), "message": f"Input path not found: {target}"}], []

    docs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    images: list[Path] = []

    for fp in all_files:
        suf = fp.suffix.lower()
        if suf in IMAGE_EXTS:
            images.append(fp)
            continue
        if suf not in allowed or fp.name.lower() in ignored:
            continue
        rel = fp.name if target.is_file() else fp.relative_to(target).as_posix()
        sha = hashlib.sha256(fp.read_bytes()).hexdigest()
        pages_meta: list[dict[str, Any]] = []
        extractor = "utf8-text"
        text = ""
        if suf == ".pdf":
            text, pages_meta, pdf_gaps, extractor = extract_pdf_text_cascade(fp, rel)
            gaps.extend(pdf_gaps)
            if extractor == "unavailable":
                continue
        elif suf == ".docx":
            try:
                text, extractor = extract_docx_text_stdlib(fp)
            except Exception as exc:
                gaps.append({"code": "DOCX_PARSE_ERROR", "severity": "error", "source": rel, "message": str(exc)})
                continue
        else:
            text = fp.read_text(encoding="utf-8", errors="replace")

        lines = text.splitlines()
        page_map: dict[int, int] = {}
        cur_page = 1 if pages_meta else 0
        for lno, raw in enumerate(lines, 1):
            mp = re.match(r"^\[Page\s+(\d+)\]$", raw.strip())
            if mp:
                cur_page = int(mp.group(1))
            if cur_page:
                page_map[lno] = cur_page

        docs.append({
            "file": rel,
            "path": str(fp),
            "ext": suf,
            "sha256": sha,
            "extractor": extractor,
            "text": text,
            "lines": lines,
            "line_count": len(lines),
            "pages": pages_meta,
            "page_map": page_map,
        })
    return docs, gaps, images


def detect_document_role(doc: dict[str, Any]) -> str:
    """Distinguish empirical research manuscripts from regulatory guidance or editorial fixtures."""
    head = doc.get("text", "")[:2600]
    if re.search(
        r"\b(?:guidance\s+for\s+industry|regulatory\s+guidance|food\s+and\s+drug\s+administration|"
        r"u\.s\.\s+department\s+of\s+health\s+and\s+human\s+services|editorial\s+validation\s+fixture)\b",
        head,
        re.I,
    ):
        return "regulatory_guidance"
    return "research_manuscript"


def split_sections(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a document into canonical academic sections supporting Markdown, LaTeX, plain/PDF headings, and multi-page References resumption."""
    sections: list[dict[str, Any]] = []
    cur_title = "(preamble)"
    cur_role = "preamble"
    cur_start = 1
    buf: list[tuple[int, str]] = []
    seen_references = False

    def flush(end_ln: int) -> None:
        if not buf:
            return
        txt = "\n".join(x for _, x in buf).strip()
        if not txt and cur_title == "(preamble)":
            return
        sections.append({
            "file": doc["file"],
            "title": cur_title,
            "role": cur_role,
            "start_line": cur_start,
            "end_line": end_ln,
            "lines": list(buf),
            "text": txt,
        })

    for lno, raw in enumerate(doc["lines"], 1):
        st = raw.strip()
        m_md = MD_HEADING_RE.match(st)
        m_tex = TEX_HEADING_RE.match(st)
        plain_h = _is_plain_heading(st) if not (m_md or m_tex) else None
        # Resume References when a numbered bibliography continues after an intervening sidebar heading (e.g., Funding / Conflict of interests)
        if (
            seen_references
            and cur_role != "references"
            and not (m_md or m_tex or plain_h)
            and re.match(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+[A-Z\u4e00-\u9fff]", st)
        ):
            flush(lno - 1)
            cur_title = "References (continued)"
            cur_role = "references"
            cur_start = lno
            buf = [(lno, raw)]
            continue
        if m_md or m_tex or plain_h:
            flush(lno - 1)
            cur_title = (m_md.group(2) if m_md else (m_tex.group(1) if m_tex else plain_h or "")).strip()
            cur_role = detect_section_role(cur_title)
            if cur_role == "references":
                seen_references = True
            cur_start = lno
            buf = []
        else:
            buf.append((lno, raw))
    flush(len(doc["lines"]))
    return sections


def merge_paragraph_lines(sec: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge PDF hard-wrapped lines and cross-line hyphenations into logical paragraphs with exact line spans."""
    paragraphs: list[dict[str, Any]] = []
    buf_lines: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal buf_lines
        if not buf_lines:
            return
        merged = ""
        spans: list[tuple[int, int, int]] = []
        for idx, (lno, seg) in enumerate(buf_lines):
            piece = seg.strip()
            if not piece:
                continue
            if idx > 0 and merged:
                if merged.endswith("-") and len(merged) >= 2 and merged[-2].isalpha() and piece[0].islower():
                    merged = merged[:-1]
                    start_c = len(merged)
                    merged += piece
                    spans.append((start_c, len(merged), lno))
                else:
                    need_space = not (ord(merged[-1]) > 0x2E80 and ord(piece[0]) > 0x2E80)
                    if need_space:
                        merged += " "
                    start_c = len(merged)
                    merged += piece
                    spans.append((start_c, len(merged), lno))
            else:
                start_c = len(merged)
                merged += piece
                spans.append((start_c, len(merged), lno))
        if merged:
            paragraphs.append({
                "text": merged,
                "line_start": buf_lines[0][0],
                "line_end": buf_lines[-1][0],
                "char_spans": spans,
            })
        buf_lines = []

    for lno, raw in sec["lines"]:
        st = raw.strip()
        if not st or st.startswith("[Page "):
            flush()
            continue
        if "|" in st and (st.startswith("|") or st.count("|") >= 2):
            flush()
            buf_lines = [(lno, st)]
            flush()
            continue
        if buf_lines and LIST_OR_CAPTION_START_RE.match(st):
            flush()
        buf_lines.append((lno, st))
    flush()
    return paragraphs


def _span_to_lines(char_start: int, char_end: int, char_spans: list[tuple[int, int, int]], fallback: int) -> tuple[int, int]:
    hit_lines = [lno for cs, ce, lno in char_spans if ce > char_start and cs < char_end]
    if not hit_lines:
        return fallback, fallback
    return min(hit_lines), max(hit_lines)


def split_sentences(sec: dict[str, Any]) -> list[dict[str, Any]]:
    """Split section into paragraph-merged, line-anchored sentences preserving line_start..line_end across PDF wraps."""
    sents: list[dict[str, Any]] = []
    abbr_re = re.compile(r"\b(Fig|Figs|Eq|Ref|Refs|No|vs|al|Dr|Prof|Table|Tab|Suppl|Sec|Vol|e\.g|i\.e|et\s+al)\.", re.I)
    for para in merge_paragraph_lines(sec):
        text = para["text"]
        spans = para["char_spans"]
        protected = abbr_re.sub(lambda m: m.group(0).replace(".", "<DOT>"), text)
        cursor = 0
        raw_parts: list[tuple[int, int, str]] = []
        for m in re.finditer(r"(?<=[.!?。！？])\s+", protected):
            chunk = protected[cursor:m.start()].replace("<DOT>", ".").strip()
            if chunk:
                orig_start = text.find(chunk[:24], max(0, cursor - 16))
                if orig_start < 0:
                    orig_start = cursor
                raw_parts.append((orig_start, orig_start + len(chunk), chunk))
            cursor = m.end()
        tail = protected[cursor:].replace("<DOT>", ".").strip()
        if tail:
            orig_start = text.find(tail[:24], max(0, cursor - 16))
            if orig_start < 0:
                orig_start = cursor
            raw_parts.append((orig_start, orig_start + len(tail), tail))

        for cs, ce, st in raw_parts:
            if len(st) >= 12:
                ls, le = _span_to_lines(cs, ce, spans, para["line_start"])
                sents.append({"line": ls, "line_start": ls, "line_end": le, "text": st})
    return sents


def detect_superscript_numeric_style(body_text: str, numeric_ref_count: int) -> tuple[bool, set[str]]:
    """Detect medical Vancouver/SciELO superscript numeric citations when bracketed [n] citations are absent."""
    if numeric_ref_count < 1:
        return False, set()
    bracket_hits = len(BRACKET_NUM_CITE_RE.findall(body_text))
    if bracket_hits >= 2:
        return False, set()
    found_keys: set[str] = set()
    for m in SUPERSCRIPT_CITE_RE.finditer(body_text):
        spec = m.group(1)
        for part in spec.split(","):
            part = part.strip()
            rm = re.match(r"^(\d+)[-–](\d+)$", part)
            if rm:
                lo, hi = int(rm.group(1)), int(rm.group(2))
                if 1 <= lo <= hi <= max(numeric_ref_count + 5, 50) and (hi - lo) <= 15:
                    found_keys.update(str(k) for k in range(lo, hi + 1))
            elif part.isdigit() and 1 <= int(part) <= max(numeric_ref_count + 5, 50):
                found_keys.add(part)
    return len(found_keys) >= 3, found_keys


def extract_superscript_citations(sentence: str, max_ref_num: int) -> list[dict[str, Any]]:
    """Extract Vancouver/medical superscript numeric citation keys from a single sentence (excluding decimals/years)."""
    out: list[dict[str, Any]] = []
    cap = max(max_ref_num + 5, 40)
    for m in SUPERSCRIPT_CITE_RE.finditer(sentence):
        spec = m.group(1)
        for part in spec.split(","):
            part = part.strip()
            rm = re.match(r"^(\d+)[-–](\d+)$", part)
            if rm:
                lo, hi = int(rm.group(1)), int(rm.group(2))
                if 1 <= lo <= hi <= cap and (hi - lo) <= 15:
                    for k in range(lo, hi + 1):
                        out.append({"key": str(k), "raw": m.group(0), "style": "superscript_numeric"})
            elif part.isdigit() and 1 <= int(part) <= cap:
                out.append({"key": part, "raw": m.group(0), "style": "superscript_numeric"})
    return out


CI_VS_P_RE = re.compile(
    r"(?:(?P<label>AAPC|APC|OR|HR|RR|IRR|PR|β|beta|MD|SMD|RD)\s*[:=]?\s*)?"
    r"(?P<est>[+-−]?\d+(?:\.\d+)?)\s*%?\*?"
    r"\s*[;,(]?\s*(?:95\s*%\s*(?:CI|IC)|IC\s*95\s*%)\s*[:=]?\s*\[?\s*"
    r"(?P<lo>[+-−]?\d+(?:\.\d+)?)\s*(?:[-–—~]|to)\s*(?P<hi>[+-−]?\d+(?:\.\d+)?)\s*\]?"
    r"\s*[;,)]?\s*p\s*(?P<op>[<=>≤≥])\s*(?P<pval>0?\.\d+)",
    re.I,
)


def _locate_match_line(sec: dict[str, Any], ls: int, le: int, needle_a: str, needle_b: str) -> tuple[int, int]:
    """Locate the exact line span inside sec['lines'] containing needle_a/needle_b so multi-stat paragraphs get unique line anchors."""
    sub = [(ln, txt) for ln, txt in sec["lines"] if ls <= ln <= le]
    hits = [ln for ln, txt in sub if needle_a in txt and needle_b in txt]
    if hits:
        return hits[0], hits[0]
    hits_a = [ln for ln, txt in sub if needle_a in txt]
    hits_b = [ln for ln, txt in sub if needle_b in txt]
    if hits_a and hits_b:
        return min(hits_a[0], hits_b[0]), max(hits_a[0], hits_b[0])
    if hits_a:
        return hits_a[0], min(le, hits_a[0] + 1)
    return ls, le


def audit_ci_vs_p_consistency(doc: dict[str, Any], secs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure-stdlib dual-scale (linear + log) conditional check of reported 95% CI vs p-value consistency.

    Computes BOTH linear Wald and log-scale (log(OR/HR/RR) or log(1 + r/100) for APC/AAPC/percent-change)
    approximations, explicitly documents scale assumptions, and marks items `needs_human_review` whenever exact
    model link/degrees-of-freedom are not supplied.
    """
    out: list[dict[str, Any]] = []
    page_map = doc.get("page_map", {})
    seen_keys: set[tuple[str, str, str, str]] = set()
    ratio_labels = {"OR", "HR", "RR", "IRR", "PR"}

    def _to_f(s: str) -> float:
        return float(s.replace("−", "-"))

    for sec in secs:
        if sec["role"] == "references":
            continue
        for sent in split_sentences(sec):
            st = sent["text"]
            for m in CI_VS_P_RE.finditer(st):
                label = (m.group("label") or "").upper()
                before_ctx = st[max(0, m.start() - 48):m.start()]
                if not label and re.search(r"\b(?:annual\s+percent\s+change|percent\s+change|AAPC|APC)\b", before_ctx, re.I):
                    label = "AAPC" if re.search(r"average|AAPC", before_ctx, re.I) else "APC"
                try:
                    est = _to_f(m.group("est"))
                    lo = _to_f(m.group("lo"))
                    hi = _to_f(m.group("hi"))
                    prep = _to_f(m.group("pval"))
                except ValueError:
                    continue
                op = m.group("op")
                if not (lo < hi) or not (0.0 <= prep <= 1.0):
                    continue
                is_ratio = label in ratio_labels
                null_val = 1.0 if is_ratio else 0.0
                if not (lo - 1e-6 <= est <= hi + 1e-6):
                    continue
                sig_key = (m.group("est"), m.group("lo"), m.group("hi"), m.group("pval"))
                if sig_key in seen_keys:
                    continue

                # 1. Linear Wald scale: SE = (hi - lo) / (2 * 1.959964)
                se_lin = (hi - lo) / 3.919928
                z_lin = (abs(est - null_val) / se_lin) if se_lin > 1e-9 else None
                p_lin = math.erfc(z_lin / math.sqrt(2.0)) if z_lin is not None else None

                # 2. Log scale for ratio measures (OR/HR/RR) or percent-change measures (APC/AAPC: log(1 + r/100))
                z_log: float | None = None
                p_log: float | None = None
                if is_ratio and lo > 0 and hi > 0 and est > 0:
                    se_log = (math.log(hi) - math.log(lo)) / 3.919928
                    if se_log > 1e-9:
                        z_log = abs(math.log(est)) / se_log
                        p_log = math.erfc(z_log / math.sqrt(2.0))
                elif label in {"APC", "AAPC"} and lo > -99.0 and hi > -99.0 and est > -99.0:
                    se_log = (math.log(1.0 + hi / 100.0) - math.log(1.0 + lo / 100.0)) / 3.919928
                    if se_log > 1e-9:
                        z_log = abs(math.log(1.0 + est / 100.0)) / se_log
                        p_log = math.erfc(z_log / math.sqrt(2.0))

                z_vals = [z for z in (z_lin, z_log) if z is not None]
                if not z_vals:
                    continue
                z_max = max(z_vals)
                p_min = math.erfc(z_max / math.sqrt(2.0))
                both_scales_tested = (p_lin is not None and p_log is not None)
                ci_crosses_null = (lo <= null_val <= hi)
                scale_desc = (
                    f"linear Wald p≈{p_lin:.4f} (|z|={z_lin:.2f}); log(1+r/100) scale p≈{p_log:.4f} (|z|={z_log:.2f})"
                    if both_scales_tested and not is_ratio
                    else (
                        f"linear p≈{p_lin:.4f} (|z|={z_lin:.2f}); log-ratio scale p≈{p_log:.4f} (|z|={z_log:.2f})"
                        if both_scales_tested
                        else f"linear Wald scale p≈{p_lin:.4f} (|z|={z_lin:.2f})"
                    )
                )
                contradiction = ""
                severity = "major"
                if op in ("<", "≤") and prep <= 0.001 and p_min >= 0.01:
                    both_violated = (p_lin is None or p_lin >= 0.01) and (p_log is None or p_log >= 0.01)
                    severity = "major" if both_violated else "minor"
                    contradiction = (
                        f"Dual-scale CI-vs-p tension for {label or 'estimate'}={est:g} (95% CI {lo:g}–{hi:g}): "
                        f"{scale_desc} (conservative lower bound p>={p_min:.4f}), substantially higher than reported p{op}{prep:g}. "
                        "Assumption: standard normal/Wald interval; exact model link, Joinpoint permutation/t-dist df, or transcription requires author verification."
                    )
                elif op in ("<", "≤") and prep <= 0.05 and ci_crosses_null and p_min >= 0.08:
                    contradiction = (
                        f"95% CI ({lo:g}–{hi:g}) includes the null ({null_val:g}) [{scale_desc}], "
                        f"in tension with significant p{op}{prep:g}; verify interval construction and exact p-value."
                    )
                elif op in (">", "≥") and prep >= 0.10 and not ci_crosses_null and p_min <= 0.02:
                    contradiction = (
                        f"95% CI ({lo:g}–{hi:g}) excludes the null ({null_val:g}) [{scale_desc}], "
                        f"in tension with non-significant p{op}{prep:g}; verify interval construction and exact p-value."
                    )
                if contradiction:
                    seen_keys.add(sig_key)
                    ls, le = _locate_match_line(sec, sent["line_start"], sent["line_end"], m.group("est"), m.group("lo"))
                    loc: dict[str, Any] = {"file": doc["file"], "section": sec["title"], "line_start": ls, "line_end": le}
                    if page_map.get(ls):
                        loc["page"] = page_map[ls]
                    # Center verbatim quote around the exact statistical match (so multi-stat sentences have unique quotes)
                    q_start = max(0, m.start() - 60)
                    q_end = min(len(st), m.end() + 60)
                    local_quote = st[q_start:q_end].strip()
                    out.append({
                        "facet": "statistical_reporting",
                        "subtype": "ci_vs_p_inconsistency",
                        "finding_type": "insufficient_evidence",
                        "severity": severity,
                        "location": loc,
                        "verbatim_quote": local_quote[:200],
                        "evidence_basis": contradiction,
                        "actionable_revision": (
                            "Verify the exact confidence interval construction (linear vs log/Joinpoint t-distribution), degrees of freedom, "
                            "and exact p-value against raw model output; clarify the estimation scale or correct any transcription discrepancy."
                        ),
                        "metrics": {
                            "label": label or "estimate",
                            "estimate": est,
                            "ci_lo": lo,
                            "ci_hi": hi,
                            "p_reported": prep,
                            "z_linear": round(z_lin, 3) if z_lin is not None else None,
                            "p_linear": round(p_lin, 5) if p_lin is not None else None,
                            "z_log": round(z_log, 3) if z_log is not None else None,
                            "p_log": round(p_log, 5) if p_log is not None else None,
                            "z_max": round(z_max, 3),
                            "p_implied_min": round(p_min, 5),
                            "both_scales_checked": both_scales_tested,
                            "scale_assumption": "dual_scale_linear_and_log1p_wald_approx (exact model link/df unverified)",
                        },
                        "status": "needs_human_review",
                    })
    return out


def audit_trend_vs_endpoint_tension(doc: dict[str, Any], secs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect interpretive tension when a large reported AAPC/APC over >=5 years diverges from start/end rates (always needs_human_review)."""
    out: list[dict[str, Any]] = []
    page_map = doc.get("page_map", {})
    row_re = re.compile(
        r"\b(?P<r1>\d+(?:\.\d+)?)\s+(?P<r2>\d+(?:\.\d+)?)\s+(?P<y1>(?:19|20)\d{2})\s*[-–]\s*(?P<y2>(?:19|20)\d{2})\s+"
        r"(?P<aapc>[+-−]?\d+(?:\.\d+)?)\s*%\*?",
        re.I,
    )
    seen: set[tuple[str, str, str]] = set()
    for sec in secs:
        if sec["role"] == "references":
            continue
        for lno, raw in sec["lines"]:
            for m in row_re.finditer(raw):
                try:
                    r1 = float(m.group("r1"))
                    r2 = float(m.group("r2"))
                    y1 = int(m.group("y1"))
                    y2 = int(m.group("y2"))
                    aapc = float(m.group("aapc").replace("−", "-"))
                except ValueError:
                    continue
                span = y2 - y1
                if span < 5 or r1 <= 0 or abs(aapc) < 5.0:
                    continue
                endpoint_rel_pct = ((r2 - r1) / r1) * 100.0
                implied_cum_pct = (((1.0 + aapc / 100.0) ** span) - 1.0) * 100.0
                if abs(implied_cum_pct) >= 30.0 and (abs(endpoint_rel_pct) < 5.0 or (aapc * endpoint_rel_pct < 0)):
                    sig = (m.group("r1"), m.group("r2"), m.group("aapc"))
                    if sig in seen:
                        continue
                    seen.add(sig)
                    loc: dict[str, Any] = {"file": doc["file"], "section": sec["title"], "line_start": lno, "line_end": lno}
                    if page_map.get(lno):
                        loc["page"] = page_map[lno]
                    out.append({
                        "facet": "argument_chain",
                        "subtype": "trend_vs_endpoint_tension",
                        "finding_type": "insufficient_evidence",
                        "severity": "minor",
                        "location": loc,
                        "verbatim_quote": raw.strip()[:200],
                        "evidence_basis": (
                            f"Interpretive tension between fitted trend and observed endpoints (requires annual series verification): "
                            f"AAPC={aapc:+.1f}% over {y1}-{y2} ({span} yr) would imply {implied_cum_pct:+.1f}% under constant compounding, "
                            f"whereas observed start/end rates ({r1:g} -> {r2:g}) change by {endpoint_rel_pct:+.2f}%. "
                            "Note: non-monotonic/segmented Joinpoint trajectories or endpoint volatility can legitimately produce such divergence."
                        ),
                        "actionable_revision": (
                            "Provide the full annual rate series, segment inflection years, and fitted Joinpoint model parameters so readers "
                            "can reconcile the summary AAPC with the observed start-to-end endpoint rates."
                        ),
                        "status": "needs_human_review",
                    })
    return out


def audit_bilingual_numeric_alignment(doc: dict[str, Any], secs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect cross-language decimal/precision discrepancies (e.g., EN '50.9' vs PT/EU comma '50,99' for the same paired anchor)
    and check integer count-to-percentage closure (e.g., n=2,944 / 5,539 = 53.15% vs reported 53.1%)."""
    out: list[dict[str, Any]] = []
    page_map = doc.get("page_map", {})
    text = doc.get("text", "")
    lines = doc.get("lines", [])

    # 1. Paired rate/endpoint bilingual precision discrepancy (e.g., "55.69/10,000 ... to 50.9" vs "55,69/10.000 ... a 50,99")
    pair_re = re.compile(
        r"(?P<v1>\d{1,4}[.,]\d{1,2})\s*(?:/\s*10[.,]?000|/100[.,]?000|%|\b)\s*(?:inhabitants?|habitantes)?\s*"
        r"(?:to|a|para|→|->|-|–)\s*(?P<v2>\d{1,4}[.,]\d{1,2})",
        re.I,
    )
    paired_hits: list[tuple[int, str, float, str, float, str]] = []
    for lno, raw in enumerate(lines, 1):
        for m in pair_re.finditer(raw):
            r1s, r2s = m.group("v1"), m.group("v2")
            try:
                f1 = float(r1s.replace(",", "."))
                f2 = float(r2s.replace(",", "."))
            except ValueError:
                continue
            paired_hits.append((lno, r1s, f1, r2s, f2, raw.strip()))

    seen_biling: set[tuple[str, str]] = set()
    for i in range(len(paired_hits)):
        l1, s1a, f1a, s1b, f1b, txt1 = paired_hits[i]
        for j in range(i + 1, len(paired_hits)):
            l2, s2a, f2a, s2b, f2b, txt2 = paired_hits[j]
            if abs(f1a - f2a) < 1e-6 and s1b.replace(",", ".") != s2b.replace(",", "."):
                diff = abs(f1b - f2b)
                if 0.01 <= diff <= 0.25:
                    sig = tuple(sorted([s1b, s2b]))
                    if sig in seen_biling:
                        continue
                    seen_biling.add(sig)
                    loc: dict[str, Any] = {"file": doc["file"], "section": "Cross-Language / Cross-Section", "line_start": l1, "line_end": l2}
                    if page_map.get(l1):
                        loc["page"] = page_map[l1]
                    out.append({
                        "facet": "fact_verification",
                        "subtype": "bilingual_numeric_precision_mismatch",
                        "finding_type": "factual_error",
                        "severity": "minor",
                        "location": loc,
                        "verbatim_quote": f"L{l1}: {txt1[:90]} | L{l2}: {txt2[:90]}",
                        "evidence_basis": (
                            f"Cross-language/cross-section numeric precision mismatch for paired anchor {f1a:g}: "
                            f"endpoint reported as '{s1b}' at L{l1} vs '{s2b}' at L{l2} (diff={diff:.2f})."
                        ),
                        "actionable_revision": (
                            f"Harmonize the decimal precision of '{s1b}' (L{l1}) and '{s2b}' (L{l2}) across English/non-English abstracts, results prose, and tables."
                        ),
                        "status": "confirmed",
                    })

    # 2. Same-paragraph count-to-percentage closure check (strictly scoped to sections declaring total N; handles EN & EU comma/dot decimals)
    pct_n_re = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*%\s*[\(;]\s*n\s*=\s*(\d{1,3}(?:[.,]\d{3})+|\d+)", re.I)
    for sec in secs:
        if sec["role"] not in ("abstract", "results"):
            continue
        for para in merge_paragraph_lines(sec):
            ptxt = para["text"]
            tot_m = re.search(r"\b(\d{1,3}(?:[.,]\d{3})+)\s+(?:cases?|patients?|participants?|records?|casos|pacientes)\b", ptxt, re.I)
            if not tot_m:
                continue
            total_n = int(re.sub(r"[.,]", "", tot_m.group(1)))
            if total_n < 100:
                continue
            for pm in pct_n_re.finditer(ptxt):
                rep_p = float(pm.group(1).replace(",", "."))
                k = int(re.sub(r"[.,]", "", pm.group(2)))
                if 0 < k <= total_n:
                    calc_p = (k / total_n) * 100.0
                    diff = abs(calc_p - rep_p)
                    # Only flag modest arithmetic discrepancies (0.3..5.0 pp); large gaps indicate a nested subgroup denominator
                    if 0.30 <= diff <= 5.0:
                        ls, le = para["line_start"], para["line_end"]
                        loc = {"file": doc["file"], "section": sec["title"], "line_start": ls, "line_end": le}
                        if page_map.get(ls):
                            loc["page"] = page_map[ls]
                        out.append({
                            "facet": "fact_verification",
                            "subtype": "count_percentage_closure_mismatch",
                            "finding_type": "factual_error",
                            "severity": "minor",
                            "location": loc,
                            "verbatim_quote": ptxt[max(0, pm.start() - 40):min(len(ptxt), pm.end() + 40)].strip()[:200],
                            "evidence_basis": f"Same-paragraph arithmetic check: reported {rep_p}% (n={k}) vs declared total N={total_n} yields {calc_p:.2f}% (diff={diff:.2f} pp).",
                            "actionable_revision": f"Verify denominator for n={k} or reconcile reported percentage ({rep_p}%) against exact {calc_p:.2f}%.",
                            "status": "needs_human_review",
                        })
    return out


def audit_six_facet_companion(
    doc: dict[str, Any],
    secs: list[dict[str, Any]],
    *,
    skip_facets: set[str] | None = None,
    id_prefix: str = "AUX",
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic six-facet companion auditor + structured semantic_review_checklist for any review skill.

    Returns:
      - companion_findings: concrete line-anchored findings for facets not skipped
      - six_facet_coverage: coverage dict across all 6 canonical facets
      - semantic_checklist: structured Step-3 verification checklist preserved even under early budget cutoff
    """
    skip = skip_facets or set()
    findings: list[dict[str, Any]] = []
    fid = 0
    text = doc["text"]
    page_map = doc["page_map"]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for s in secs:
        by_role.setdefault(s["role"], []).append(s)

    def add(facet: str, ftype: str, sev: str, ls: int, le: int, sec_title: str, quote: str, basis: str, fix: str, status: str = "confirmed") -> None:
        nonlocal fid
        fid += 1
        loc: dict[str, Any] = {"file": doc["file"], "section": sec_title, "line_start": ls, "line_end": le}
        if page_map.get(ls):
            loc["page"] = page_map[ls]
        findings.append({
            "finding_id": f"{id_prefix}-{fid:03d}",
            "priority": {"critical": "P0", "major": "P1", "minor": "P2", "suggestion": "P3"}.get(sev, "P2"),
            "blocking_tier": "blocking" if sev in ("critical", "major") else "optional",
            "facet": facet,
            "finding_type": ftype,
            "severity": sev,
            "location": loc,
            "verbatim_quote": quote[:200],
            "evidence_basis": basis,
            "actionable_revision": fix,
            "status": status,
        })

    # 1. Citation support companion (multi-line ref parsing, retraction, dangling, superscript-aware)
    ref_entries: dict[str, tuple[int, int, str]] = {}
    if "citation_support" not in skip:
        cur_k = ""
        cur_ls = cur_le = 0
        buf: list[str] = []
        for rsec in by_role.get("references", []):
            for lno, raw in rsec["lines"]:
                st = raw.strip()
                if not st or st.startswith("[Page "):
                    continue
                m = BIB_NUM_ENTRY_RE.match(st)
                if m:
                    if cur_k and buf:
                        ref_entries[cur_k] = (cur_ls, cur_le, " ".join(buf))
                    cur_k = m.group(1) or m.group(2)
                    cur_ls = cur_le = lno
                    buf = [st]
                elif cur_k:
                    buf.append(st)
                    cur_le = lno
        if cur_k and buf:
            ref_entries[cur_k] = (cur_ls, cur_le, " ".join(buf))

        for k, (ls, le, rtxt) in ref_entries.items():
            if RETRACTION_RE.search(rtxt):
                add(
                    "citation_support", "factual_error", "critical", ls, le, "References", rtxt,
                    f"Reference [{k}] carries an explicit retraction/withdrawal/erratum notice.",
                    f"Remove or qualify reliance on retracted/corrected reference [{k}].",
                )
        body_text = "\n".join(s["text"] for s in secs if s["role"] != "references")
        is_super, _ = detect_superscript_numeric_style(body_text, len(ref_entries))
        if ref_entries and not is_super:
            for s in secs:
                if s["role"] == "references":
                    continue
                for sent in split_sentences(s):
                    for m in BRACKET_NUM_CITE_RE.finditer(sent["text"]):
                        ctx = sent["text"][max(0, m.start() - 28):min(len(sent["text"]), m.end() + 28)]
                        if re.search(r"\b(?:scale|score|likert|range|interval|grade|point)\b", ctx, re.I):
                            continue
                        for part in m.group(1).split(","):
                            part = part.strip()
                            if part.isdigit() and part not in ref_entries:
                                add(
                                    "citation_support", "factual_error", "major", sent["line_start"], sent["line_end"], s["title"],
                                    sent["text"], f"In-text citation [{part}] has no matching entry in References.",
                                    f"Supply reference entry [{part}] or reconcile citation numbering.",
                                )

    # 2. Fact verification companion (df > max_N and large Abstract-vs-Results % gap)
    if "fact_verification" not in skip:
        n_vals = [int(m.group(1)) for m in re.finditer(r"\b[Nn]\s*=\s*(\d{2,6})\b", text)]
        max_n = max(n_vals) if n_vals else None
        if max_n:
            for s in secs:
                if s["role"] == "references":
                    continue
                for sent in split_sentences(s):
                    for dm in re.finditer(r"\b(?:t|chi2|χ2)\s*\(\s*(\d+(?:\.\d+)?)\s*\)\s*=\s*-?\d+(?:\.\d+)?", sent["text"], re.I):
                        dfv = float(dm.group(1))
                        if dfv > max_n:
                            add(
                                "fact_verification", "factual_error", "major", sent["line_start"], sent["line_end"], s["title"],
                                sent["text"], f"Reported df={dfv:g} exceeds maximum declared cohort N={max_n}.",
                                "Reconcile statistical unit/df with cohort sample size N.",
                            )
        for bh in audit_bilingual_numeric_alignment(doc, secs):
            loc = bh["location"]
            add(
                "fact_verification", bh["finding_type"], bh["severity"],
                loc["line_start"], loc["line_end"], loc["section"],
                bh["verbatim_quote"], bh["evidence_basis"], bh["actionable_revision"],
                status=bh["status"],
            )

    # 3. Argument chain companion (Intro aim -> Conclusion alignment)
    intro_concl_info: dict[str, Any] = {"checked": False, "overlap_ratio": None}
    intro_secs = by_role.get("introduction", [])
    concl_secs = by_role.get("conclusion", []) + by_role.get("discussion", [])
    if intro_secs and concl_secs:
        aim_kws: set[str] = set()
        stop = {"this", "that", "with", "from", "were", "have", "been", "study", "paper", "results", "patients", "method", "methods", "whether", "evaluate", "investigate", "examine"}
        for isec in intro_secs:
            for sent in split_sentences(isec):
                if re.search(
                    r"\b(?:we\s+(?:aim|aimed|investigate|evaluate|examine|test|assess|study)|"
                    r"(?:this|the\s+present)\s+(?:study|paper|work|article)\s+(?:aimed|aims|seeks|sought|evaluated|analyzed|described|investigated|examined)|"
                    r"(?:objective|aim|purpose|goal)\s+(?:of\s+this\s+\w+\s+)?was)\b|本研究旨在|本文探讨",
                    sent["text"],
                    re.I,
                ):
                    aim_kws.update(w for w in re.findall(r"[a-z]{4,}", sent["text"].lower()) if w not in stop)
        if len(aim_kws) >= 4:
            concl_blob = " ".join(s["text"].lower() for s in concl_secs)
            hits = sum(1 for w in aim_kws if w in concl_blob)
            ratio = round(hits / len(aim_kws), 3)
            intro_concl_info = {"checked": True, "aim_keyword_count": len(aim_kws), "matched_in_conclusion": hits, "overlap_ratio": ratio}
            if "argument_chain" not in skip and ratio < 0.25:
                add(
                    "argument_chain", "insufficient_evidence", "minor", intro_secs[0]["start_line"], intro_secs[0]["start_line"],
                    intro_secs[0]["title"], f"Intro objective terms ({sorted(aim_kws)[:8]}) overlap ratio in Discussion/Conclusion = {ratio}",
                    "Low lexical alignment between Introduction research objectives and Discussion/Conclusion; verify whether stated aims were directly answered.",
                    "Confirm that every research objective stated in the Introduction is explicitly resolved in the Conclusion.",
                    status="needs_human_review",
                )

    # 4. Method design & Statistical reporting & Academic norms lightweight checks when not skipped
    doc_role = detect_document_role(doc)
    if "method_design" not in skip and doc_role == "research_manuscript":
        is_obs = bool(re.search(r"\b(?:retrospective|observational|cross-sectional|ecological|case-control|cohort\s+study)\b|回顾性|观察性|横断面", text, re.I))
        is_rct = bool(re.search(r"\b(?:randomized\s+controlled|double-blind\s+trial|Mendelian\s+randomization)\b|随机对照", text, re.I))
        if is_obs and not is_rct:
            for s in secs:
                if s["role"] in ("abstract", "discussion", "conclusion", "results_discussion"):
                    for sent in split_sentences(s):
                        if is_true_causal_overclaim(sent["text"]):
                            add(
                                "method_design", "methodology_flaw", "major", sent["line_start"], sent["line_end"], s["title"],
                                sent["text"], "Observational/non-randomized design paired with strong causal claim language.",
                                "Downgrade causal wording to associational framing or document causal identification assumptions.",
                            )
                            break

    if "statistical_reporting" not in skip:
        for s in secs:
            if s["role"] == "references":
                continue
            for sent in split_sentences(s):
                if re.search(r"\bp\s*=\s*0\.000+(?!\d)", sent["text"], re.I):
                    add(
                        "statistical_reporting", "norm_violation", "minor", sent["line_start"], sent["line_end"], s["title"],
                        sent["text"], "Exact 'p = 0.000' reported instead of 'p < 0.001' or scientific notation.",
                        "Replace 'p = 0.000' with 'p < 0.001' or exact scientific notation.",
                    )
        for ci_hit in audit_ci_vs_p_consistency(doc, secs):
            loc = ci_hit["location"]
            add(
                "statistical_reporting", ci_hit["finding_type"], ci_hit["severity"],
                loc["line_start"], loc["line_end"], loc["section"],
                ci_hit["verbatim_quote"], ci_hit["evidence_basis"], ci_hit["actionable_revision"],
                status=ci_hit["status"],
            )

    if "academic_norms" not in skip and doc_role == "research_manuscript":
        has_ethics_ctx = bool(re.search(r"\b(?:patients?\s+(?:were\s+)?(?:enrolled|recruited|included)|clinical\s+trial|mice\s+were|informed\s+consent)\b", text, re.I))
        has_ethics_decl = bool(re.search(r"\b(?:IRB|ethics\s+committee|institutional\s+review|IACUC|Helsinki|informed\s+consent)\b|伦理", text, re.I))
        if has_ethics_ctx and not has_ethics_decl:
            add(
                "academic_norms", "norm_violation", "major", 1, 1, "Declarations",
                "[MISSING: ethics / IRB / IACUC disclosure]",
                "Human/animal enrollment signals present without ethics approval or informed consent disclosure.",
                "Add Ethics Approval and Informed Consent/IACUC statement.",
                status="needs_human_review",
            )

    facets = ("fact_verification", "citation_support", "method_design", "statistical_reporting", "argument_chain", "academic_norms")
    cov = {
        f: {
            "checked": True,
            "companion_findings": sum(1 for x in findings if x["facet"] == f),
        }
        for f in facets
    }
    checklist = [
        {
            "facet": "fact_verification",
            "automated_checks_ran": True,
            "semantic_follow_up": "Verify key numeric tables/figures against Abstract and Results prose.",
            "status": "checked_with_cues" if cov["fact_verification"]["companion_findings"] else "deterministic_clean_verify_tables",
        },
        {
            "facet": "citation_support",
            "automated_checks_ran": True,
            "references_parsed": len(ref_entries),
            "semantic_follow_up": "Inspect supplied source passages to grade claim support (supported/partial/contradicted/not_provided).",
            "status": "checked_with_cues" if cov["citation_support"]["companion_findings"] else "verify_source_semantics",
        },
        {
            "facet": "method_design",
            "automated_checks_ran": True,
            "semantic_follow_up": "Confirm study design, comparator, statistical unit (no pseudoreplication), and multiplicity plan.",
            "status": "checked_with_cues" if cov["method_design"]["companion_findings"] else "verify_design_assumptions",
        },
        {
            "facet": "statistical_reporting",
            "automated_checks_ran": True,
            "semantic_follow_up": "Verify effect sizes, 95% CIs, exact p-values, and error-bar definitions (SD/SEM/CI).",
            "status": "checked_with_cues" if cov["statistical_reporting"]["companion_findings"] else "verify_effect_sizes",
        },
        {
            "facet": "argument_chain",
            "automated_checks_ran": True,
            "intro_conclusion_alignment": intro_concl_info,
            "semantic_follow_up": "Verify warrant bridges from observations to conclusions and confirm bounded generalizability.",
            "status": "checked_with_cues" if cov["argument_chain"]["companion_findings"] else "verify_warrants_and_scope",
        },
        {
            "facet": "academic_norms",
            "automated_checks_ran": True,
            "semantic_follow_up": "Confirm ethics protocol IDs, COI, funding, data/code availability, and third-party asset permissions.",
            "status": "checked_with_cues" if cov["academic_norms"]["companion_findings"] else "verify_declarations",
        },
    ]
    return findings, cov, checklist


def _decode_png_grayscale_matrix(raw: bytes) -> tuple[int, int, list[list[int]]] | None:
    """Pure-stdlib decoder for non-interlaced 8/16-bit PNG including Gray, RGB, RGBA, GrayA, and Indexed Palette (color_type=3)."""
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    width = height = bit_depth = color_type = interlace = 0
    palette: list[tuple[int, int, int]] = []
    idat_chunks: list[bytes] = []
    while pos + 8 <= len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        ctype = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR" and len(data) >= 13:
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", data[:13])
        elif ctype == b"PLTE":
            for i in range(0, len(data) - 2, 3):
                palette.append((data[i], data[i + 1], data[i + 2]))
        elif ctype == b"IDAT":
            idat_chunks.append(data)
        elif ctype == b"IEND":
            break
    if interlace != 0 or width <= 0 or height <= 0 or width * height > 4_000_000:
        return None
    if color_type == 3:
        if bit_depth != 8 or not palette:
            return None
        channels, bytes_per_sample = 1, 1
    elif color_type in (0, 2, 4, 6) and bit_depth in (8, 16):
        channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
        bytes_per_sample = bit_depth // 8
    else:
        return None

    bpp = channels * bytes_per_sample
    try:
        inflated = zlib.decompress(b"".join(idat_chunks))
    except Exception:
        return None
    stride = width * bpp
    if len(inflated) < height * (stride + 1):
        return None

    prev = bytearray(stride)
    rows_gray: list[list[int]] = []
    idx = 0
    for _ in range(height):
        ftype = inflated[idx]
        idx += 1
        cur = bytearray(inflated[idx:idx + stride])
        idx += stride
        if ftype == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[i] = (cur[i] + pr) & 0xFF
        prev = cur

        if color_type == 3:
            rows_gray.append([int(0.299 * palette[p_idx][0] + 0.587 * palette[p_idx][1] + 0.114 * palette[p_idx][2]) if p_idx < len(palette) else 255 for p_idx in cur])
        elif color_type in (0, 4):
            rows_gray.append([cur[i] for i in range(0, stride, bpp)])
        else:
            g_off, b_off = bytes_per_sample, 2 * bytes_per_sample
            rows_gray.append([int(0.299 * cur[i] + 0.587 * cur[i + g_off] + 0.114 * cur[i + b_off]) for i in range(0, stride, bpp)])
    return width, height, rows_gray


def _downsample_grid_rect(matrix: list[list[int]], out_w: int, out_h: int) -> list[list[float]]:
    h = len(matrix)
    w = len(matrix[0]) if h else 0
    out: list[list[float]] = []
    for gy in range(out_h):
        y0, y1 = (gy * h) // out_h, max((gy * h) // out_h + 1, ((gy + 1) * h) // out_h)
        row: list[float] = []
        for gx in range(out_w):
            x0, x1 = (gx * w) // out_w, max((gx * w) // out_w + 1, ((gx + 1) * w) // out_w)
            total = cnt = 0
            for y in range(y0, min(y1, h)):
                for x in range(x0, min(x1, w)):
                    total += matrix[y][x]
                    cnt += 1
            row.append(total / max(cnt, 1))
        out.append(row)
    return out


def _transform_matrix(mat: list[list[int]], mode: str) -> list[list[int]]:
    if mode == "identical_perceptual":
        return mat
    if mode == "horizontal_flip":
        return [list(reversed(r)) for r in mat]
    if mode == "vertical_flip":
        return list(reversed(mat))
    if mode == "rotated_180":
        return [list(reversed(r)) for r in reversed(mat)]
    h, w = len(mat), len(mat[0]) if mat else 0
    if mode == "rotated_90":
        return [[mat[h - 1 - c][r] for c in range(h)] for r in range(w)]
    if mode == "rotated_270":
        return [[mat[c][w - 1 - r] for c in range(h)] for r in range(w)]
    if mode == "rotated_90_hflip":
        return [list(reversed(r)) for r in [[mat[h - 1 - c][r] for c in range(h)] for r in range(w)]]
    if mode == "rotated_270_hflip":
        return [list(reversed(r)) for r in [[mat[c][w - 1 - r] for c in range(h)] for r in range(w)]]
    return mat


def _compute_hv_dhash_and_stats(mat: list[list[int]]) -> dict[str, Any]:
    g32 = _downsample_grid_rect(mat, 32, 32)
    vals = [v for r in g32 for v in r]
    n = max(len(vals), 1)
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    bg_frac = max(sum(1 for v in vals if v >= 248.0) / n, sum(1 for v in vals if v <= 7.0) / n)

    gh = _downsample_grid_rect(mat, 9, 8)
    h_bits = h_active = 0
    for y in range(8):
        for x in range(8):
            diff = gh[y][x + 1] - gh[y][x]
            if abs(diff) > 1.5:
                h_active += 1
            h_bits = (h_bits << 1) | (1 if diff > 1.5 else 0)

    gv = _downsample_grid_rect(mat, 8, 9)
    v_bits = v_active = 0
    for y in range(8):
        for x in range(8):
            diff = gv[y + 1][x] - gv[y][x]
            if abs(diff) > 1.5:
                v_active += 1
            v_bits = (v_bits << 1) | (1 if diff > 1.5 else 0)

    g16 = _downsample_grid_rect(mat, 16, 16)
    return {
        "hv_bits": (h_bits << 64) | v_bits,
        "std": round(std, 3),
        "bg_frac": round(bg_frac, 3),
        "active_gradients": h_active + v_active,
        "vec16": [v for r in g16 for v in r],
    }


def _pearson_corr(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if (da > 1e-9 and db > 1e-9) else 0.0


def detect_image_duplications(images: list[Path], root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Detect exact byte duplicates and flipped/rotated perceptual duplicates across image files."""
    records: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    has_pil = False
    try:
        from PIL import Image  # type: ignore
        has_pil = True
    except Exception:
        Image = None  # type: ignore

    transforms = (
        "identical_perceptual", "horizontal_flip", "vertical_flip", "rotated_180",
        "rotated_90", "rotated_270", "rotated_90_hflip", "rotated_270_hflip",
    )

    for fp in images:
        rel = fp.name if fp == root else fp.relative_to(root).as_posix()
        raw = fp.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        mat: list[list[int]] | None = None
        dims: tuple[int, int] | None = None
        if has_pil:
            try:
                with Image.open(fp) as im:  # type: ignore
                    dims = im.size
                    px = list(im.convert("L").resize((64, 64)).getdata())
                    mat = [px[i * 64:(i + 1) * 64] for i in range(64)]
            except Exception as exc:
                gaps.append({"code": "IMAGE_DECODE_ERROR", "severity": "warning", "source": rel, "message": str(exc)})
        elif fp.suffix.lower() == ".png":
            decoded = _decode_png_grayscale_matrix(raw)
            if decoded:
                dims, mat = (decoded[0], decoded[1]), decoded[2]
            else:
                gaps.append({"code": "IMAGE_PIXEL_UNVERIFIED", "severity": "info", "source": rel, "message": "PNG variant not decoded by stdlib decoder and PIL unavailable; only exact byte-hash checked."})
        else:
            gaps.append({"code": "IMAGE_PIXEL_UNVERIFIED", "severity": "info", "source": rel, "message": f"PIL unavailable for {fp.suffix} perceptual check; only exact byte-hash checked."})

        rec: dict[str, Any] = {"file": rel, "sha256": sha, "bytes": len(raw), "dims": dims, "base": None, "transforms": {}}
        if mat is not None:
            base = _compute_hv_dhash_and_stats(mat)
            rec["base"] = base
            if base["std"] >= 5.0 and base["active_gradients"] >= 12:
                rec["transforms"] = {t: _compute_hv_dhash_and_stats(_transform_matrix(mat, t)) for t in transforms}
        records.append(rec)

    matches: list[dict[str, Any]] = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            if a["sha256"] == b["sha256"]:
                matches.append({
                    "file_a": a["file"], "file_b": b["file"], "match_type": "exact_byte_duplicate",
                    "hamming_distance": 0, "hash_bits": 128, "intensity_corr": 1.0, "dims_a": a["dims"], "dims_b": b["dims"],
                    "note": "Exact SHA-256 duplicate image file; verify whether intentional reuse or unintended panel duplication.",
                })
                continue
            if a["base"] is not None and a["transforms"] and b["transforms"]:
                a_base = a["base"]
                best_type, best_dist, best_corr = None, 129, -1.0
                for tname, tinfo in b["transforms"].items():
                    dist = (a_base["hv_bits"] ^ tinfo["hv_bits"]).bit_count()
                    corr = _pearson_corr(a_base["vec16"], tinfo["vec16"])
                    if dist < best_dist or (dist == best_dist and corr > best_corr):
                        best_dist, best_type, best_corr = dist, tname, corr
                sparse_bg = (a_base["bg_frac"] >= 0.70) or (b["base"]["bg_frac"] >= 0.70)
                if best_type is not None and best_dist <= (6 if sparse_bg else 12) and best_corr >= (0.985 if sparse_bg else 0.96):
                    matches.append({
                        "file_a": a["file"], "file_b": b["file"], "match_type": best_type,
                        "hamming_distance": best_dist, "hash_bits": 128, "intensity_corr": round(best_corr, 4),
                        "dims_a": a["dims"], "dims_b": b["dims"],
                        "note": (
                            f"Perceptual 128-bit HV-dHash match ({best_type}, Hamming={best_dist}/128, r={best_corr:.4f}) "
                            f"between {a['file']} and {b['file']}. Anomaly requires original-data investigation; similarity alone does not establish misconduct."
                        ),
                    })
    return matches, gaps


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
