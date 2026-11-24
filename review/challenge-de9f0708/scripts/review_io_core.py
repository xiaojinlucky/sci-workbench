#!/usr/bin/env python3
"""Zero-dependency offline document, section, paragraph, table, and image-fingerprint IO core for academic review skills.

Design principles:
- Works in pure Python 3.10+ stdlib (offline Linux containers with --network none).
- Opportunistically leverages optional packages (pdfplumber, pypdf, python-docx, PIL) or CLI (pdftotext) when available,
  falling back cleanly to stdlib ZIP/XML DOCX parsing and honest gap reporting without fabricating missing content.
- Handles wrapped PDF paragraphs and cross-line hyphenation while preserving exact line_start..line_end provenance.
- Uses true 9x8 horizontal + 8x9 vertical gradient dHash (128-bit HV-dHash) with texture-variance/background gating
  so blank or white-background statistical plots do not trigger false flip matches.
- Never imports prohibited desktop/native modules (fitz, pymupdf, cairosvg, win32com, pywin32).
"""

from __future__ import annotations

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
    ("abstract", ("abstract", "摘要", "内容提要")),
    ("introduction", ("introduction", "background", "motivation", "引言", "背景", "绪论")),
    ("related_work", ("related work", "literature review", "相关工作", "文献综述")),
    ("methods", ("methods", "methodology", "materials and methods", "experimental", "study design", "patients and methods", "statistical analysis", "方法", "材料与方法", "实验设计", "研究方法", "资料与方法")),
    ("results", ("results", "findings", "experimental results", "outcomes", "结果", "实验结果", "研究结果")),
    ("discussion", ("discussion", "limitations", "讨论", "分析与讨论", "局限性")),
    ("conclusion", ("conclusion", "conclusions", "concluding remarks", "summary", "结论", "总结")),
    ("references", ("references", "bibliography", "works cited", "参考文献")),
    ("declarations", ("acknowledgments", "acknowledgements", "funding", "conflict of interest", "competing interests", "ethics", "data availability", "code availability", "author contributions", "致谢", "基金", "利益冲突", "伦理声明", "数据可用性")),
    ("appendix", ("appendix", "supplementary", "supplemental", "附录", "补充材料")),
]

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TEX_HEADING_RE = re.compile(r"^\\(?:sub)*section\*?\{(.+?)\}")
PLAIN_NUM_HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+){0,2}|[IVX]{1,4})[\.\s、]+|[一二三四五六七八九十]+[、.\s]+)?([A-Za-z][A-Za-z0-9 &/\-]{2,64}|[\u4e00-\u9fff]{2,18})\s*:?\s*$"
)
KNOWN_SINGLE_HEADINGS = {
    kw for _, kws in SECTION_ROLES for kw in kws
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
TEXT_DOC_EXTS = {".md", ".txt", ".tex", ".pdf", ".docx"}

LIST_OR_CAPTION_START_RE = re.compile(
    r"^(?:[-*•]\s+|\d+[.)]\s+|\[\d+\]\s+|(?:Fig(?:ure)?\.?|Table|Supplementary\s+(?:Figure|Table)|图|表)\s*\d+\s*[:.：\-]|\[Page\s+\d+\]|\[Table\s+\d+\])",
    re.I,
)


def detect_section_role(heading: str) -> str:
    """Map a heading title to a canonical academic section role using exact/prefix priority before substring match.

    Prevents misclassifications such as:
    - '3.5 Summary of Experimental Results' -> 'results' (not 'abstract')
    - '4.2 Discussion of Statistical Methods' -> 'discussion' (not 'methods')
    """
    cleaned = re.sub(r"^(?:\d+(?:\.\d+){0,2}|[IVX]{1,4}|[一二三四五六七八九十]+)[\.\s、:：\-]+", "", heading.strip())
    low = cleaned.lower().strip(" :：.-")
    if not low:
        return "other"

    if ("result" in low and "discussion" in low) or ("结果" in low and "讨论" in low):
        return "results_discussion"

    # Special guard: standalone "Summary" at top of a manuscript is handled separately; "Summary of Results" -> results
    if low.startswith("summary of ") and any(w in low for w in ("result", "finding", "outcome", "experiment")):
        return "results"
    if low == "summary" or low == "executive summary":
        return "abstract"

    # Pass 1: exact match
    for role, kws in SECTION_ROLES:
        for kw in kws:
            if low == kw:
                return role

    # Pass 2: prefix match (longest keyword first so 'materials and methods' beats 'methods',
    # and 'discussion of statistical methods' matches 'discussion' before 'methods')
    prefix_candidates: list[tuple[int, str]] = []
    for role, kws in SECTION_ROLES:
        for kw in kws:
            if low.startswith(kw + " ") or low.startswith(kw + ":") or low.startswith(kw + "-") or low.startswith(kw + "："):
                prefix_candidates.append((len(kw), role))
    if prefix_candidates:
        prefix_candidates.sort(key=lambda x: -x[0])
        return prefix_candidates[0][1]

    # Pass 3: earliest positional match in heading (so the head noun wins over trailing modifiers)
    pos_candidates: list[tuple[int, int, str]] = []
    for role, kws in SECTION_ROLES:
        for kw in kws:
            idx = low.find(kw)
            if idx >= 0:
                # Word boundary check for ASCII keywords
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
                cells = []
                for tc in tr.findall("./w:tc", ns):
                    ct = "".join(t.text or "" for t in tc.findall(".//w:t", ns)).strip()
                    cells.append(ct.replace("\n", " "))
                if any(cells):
                    parts.append(" | ".join(cells))
    return "\n".join(parts), "stdlib-ooxml"


def extract_pdf_text_cascade(fp: Path, rel: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str]:
    """Extract PDF page-by-page using pdfplumber -> pypdf -> pdftotext CLI (reading-order flow), recording low-text pages."""
    gaps: list[dict[str, Any]] = []
    pages_meta: list[dict[str, Any]] = []

    try:
        import pdfplumber  # type: ignore
        pages_out: list[str] = []
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
        pass

    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(fp))
        pages_out = []
        for pno, page in enumerate(reader.pages, 1):
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
        return "\n\n".join(pages_out), pages_meta, gaps, "pypdf"
    except Exception:
        pass

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            # Avoid -layout so multi-column PDFs extract in reading order rather than gluing columns side-by-side.
            proc = subprocess.run(
                [pdftotext, str(fp), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
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

    gaps.append({
        "code": "PDF_EXTRACTOR_UNAVAILABLE",
        "severity": "error",
        "source": rel,
        "message": "No PDF text extractor (pdfplumber/pypdf/pdftotext) available in sandbox; PDF content remains unverified.",
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


def split_sections(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a document into canonical academic sections supporting Markdown, LaTeX, and plain/PDF headings."""
    sections: list[dict[str, Any]] = []
    cur_title = "(preamble)"
    cur_role = "preamble"
    cur_start = 1
    buf: list[tuple[int, str]] = []

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
        if m_md or m_tex or plain_h:
            flush(lno - 1)
            cur_title = (m_md.group(2) if m_md else (m_tex.group(1) if m_tex else plain_h or "")).strip()
            cur_role = detect_section_role(cur_title)
            cur_start = lno
            buf = []
        else:
            buf.append((lno, raw))
    flush(len(doc["lines"]))
    return sections


def merge_paragraph_lines(sec: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge PDF hard-wrapped lines and cross-line hyphenations into logical paragraphs with exact line spans.

    Each returned paragraph dict has:
    - text: merged paragraph string
    - line_start: first source line number
    - line_end: last source line number
    - char_spans: list of (char_start, char_end, source_line_no) for exact sentence-to-line provenance
    """
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
                # Rejoin cross-line English hyphenation like "observa-\ntional" -> "observational"
                if (
                    merged.endswith("-")
                    and len(merged) >= 2
                    and merged[-2].isalpha()
                    and piece[0].islower()
                ):
                    merged = merged[:-1]
                    start_c = len(merged)
                    merged += piece
                    spans.append((start_c, len(merged), lno))
                else:
                    # No extra space between adjacent CJK characters
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
        # Markdown tables or pipe-delimited table rows stay line-separated
        if "|" in st and (st.startswith("|") or st.count("|") >= 2):
            flush()
            buf_lines = [(lno, st)]
            flush()
            continue
        # Start a new paragraph on explicit bullet, numbered list, citation entry, or figure/table caption
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
        # Preserve original character offset mapping approximately by splitting with regex iterator
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


def _decode_png_grayscale_matrix(raw: bytes) -> tuple[int, int, list[list[int]]] | None:
    """Pure-stdlib decoder for non-interlaced 8-bit/16-bit PNG including Gray, RGB, RGBA, GrayA, and Indexed Palette (color_type=3)."""
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
        channels = 1
        bytes_per_sample = 1
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
                b = prev[i]
                cur[i] = (cur[i] + ((a + b) >> 1)) & 0xFF
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
            row_out = []
            for p_idx in cur:
                r, g, b = palette[p_idx] if p_idx < len(palette) else (255, 255, 255)
                row_out.append(int(0.299 * r + 0.587 * g + 0.114 * b))
            rows_gray.append(row_out)
        elif color_type == 0:
            rows_gray.append([cur[i] for i in range(0, stride, bpp)])
        elif color_type == 4:
            rows_gray.append([cur[i] for i in range(0, stride, bpp)])
        else:
            step = bpp
            g_off = bytes_per_sample
            b_off = 2 * bytes_per_sample
            rows_gray.append([
                int(0.299 * cur[i] + 0.587 * cur[i + g_off] + 0.114 * cur[i + b_off])
                for i in range(0, stride, step)
            ])
    return width, height, rows_gray


def _downsample_grid_rect(matrix: list[list[int]], out_w: int, out_h: int) -> list[list[float]]:
    h = len(matrix)
    w = len(matrix[0]) if h else 0
    out: list[list[float]] = []
    for gy in range(out_h):
        y0 = (gy * h) // out_h
        y1 = max(y0 + 1, ((gy + 1) * h) // out_h)
        row: list[float] = []
        for gx in range(out_w):
            x0 = (gx * w) // out_w
            x1 = max(x0 + 1, ((gx + 1) * w) // out_w)
            total = 0.0
            cnt = 0
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
    h = len(mat)
    w = len(mat[0]) if h else 0
    if mode == "rotated_90":
        return [[mat[h - 1 - c][r] for c in range(h)] for r in range(w)]
    if mode == "rotated_270":
        return [[mat[c][w - 1 - r] for c in range(h)] for r in range(w)]
    if mode == "rotated_90_hflip":
        r90 = [[mat[h - 1 - c][r] for c in range(h)] for r in range(w)]
        return [list(reversed(r)) for r in r90]
    if mode == "rotated_270_hflip":
        r270 = [[mat[c][w - 1 - r] for c in range(h)] for r in range(w)]
        return [list(reversed(r)) for r in r270]
    return mat


def _compute_hv_dhash_and_stats(mat: list[list[int]]) -> dict[str, Any]:
    """Compute true 128-bit HV-dHash (9x8 horizontal gradient + 8x9 vertical gradient) plus texture metrics."""
    # Downsample to 32x32 first for fast variance/background & centroid stats
    g32 = _downsample_grid_rect(mat, 32, 32)
    vals = [v for r in g32 for v in r]
    n = max(len(vals), 1)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)

    # Background dominance: fraction of cells within +-3 of the mode/dominant extreme (e.g., white canvas)
    white_frac = sum(1 for v in vals if v >= 248.0) / n
    black_frac = sum(1 for v in vals if v <= 7.0) / n
    bg_frac = max(white_frac, black_frac)

    # True 9x8 horizontal gradient dHash (64 bits)
    gh = _downsample_grid_rect(mat, 9, 8)
    h_bits = 0
    h_active = 0
    for y in range(8):
        for x in range(8):
            diff = gh[y][x + 1] - gh[y][x]
            if abs(diff) > 1.5:
                h_active += 1
            h_bits = (h_bits << 1) | (1 if diff > 1.5 else 0)

    # True 8x9 vertical gradient dHash (64 bits)
    gv = _downsample_grid_rect(mat, 8, 9)
    v_bits = 0
    v_active = 0
    for y in range(8):
        for x in range(8):
            diff = gv[y + 1][x] - gv[y][x]
            if abs(diff) > 1.5:
                v_active += 1
            v_bits = (v_bits << 1) | (1 if diff > 1.5 else 0)

    # Normalized intensity profile on 16x16 for correlation check (eliminates false flips on white-canvas charts)
    g16 = _downsample_grid_rect(mat, 16, 16)
    vec16 = [v for r in g16 for v in r]

    return {
        "hv_bits": (h_bits << 64) | v_bits,
        "std": round(std, 3),
        "bg_frac": round(bg_frac, 3),
        "active_gradients": h_active + v_active,
        "vec16": vec16,
    }


def _pearson_corr(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 1e-9 or db <= 1e-9:
        return 0.0
    return num / (da * db)


def detect_image_duplications(images: list[Path], root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Detect exact byte duplicates and flipped/rotated perceptual duplicates across image files.

    Uses true 128-bit HV-dHash + 16x16 Pearson intensity correlation (>= 0.96) + minimum texture/gradient gating
    to prevent false positives on sparse white-background charts while catching flipped/rotated microscopy panels.
    """
    records: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    has_pil = False
    try:
        from PIL import Image  # type: ignore
        has_pil = True
    except Exception:
        Image = None  # type: ignore

    transforms = (
        "identical_perceptual",
        "horizontal_flip",
        "vertical_flip",
        "rotated_180",
        "rotated_90",
        "rotated_270",
        "rotated_90_hflip",
        "rotated_270_hflip",
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
                    small = im.convert("L").resize((64, 64))
                    px = list(small.getdata())
                    mat = [px[i * 64:(i + 1) * 64] for i in range(64)]
            except Exception as exc:
                gaps.append({"code": "IMAGE_DECODE_ERROR", "severity": "warning", "source": rel, "message": str(exc)})
        elif fp.suffix.lower() == ".png":
            decoded = _decode_png_grayscale_matrix(raw)
            if decoded:
                w, h, mat = decoded
                dims = (w, h)
            else:
                gaps.append({
                    "code": "IMAGE_PIXEL_UNVERIFIED",
                    "severity": "info",
                    "source": rel,
                    "message": "PNG variant not decoded by stdlib decoder and PIL unavailable; only exact byte-hash checked.",
                })
        else:
            gaps.append({
                "code": "IMAGE_PIXEL_UNVERIFIED",
                "severity": "info",
                "source": rel,
                "message": f"PIL unavailable for {fp.suffix} perceptual check; only exact byte-hash checked.",
            })

        rec: dict[str, Any] = {
            "file": rel,
            "sha256": sha,
            "bytes": len(raw),
            "dims": dims,
            "base": None,
            "transforms": {},
        }
        if mat is not None:
            base = _compute_hv_dhash_and_stats(mat)
            rec["base"] = base
            # Only index perceptual transforms if image has sufficient texture (not blank/pure-canvas)
            if base["std"] >= 5.0 and base["active_gradients"] >= 12:
                rec["transforms"] = {
                    t: _compute_hv_dhash_and_stats(_transform_matrix(mat, t))
                    for t in transforms
                }
        records.append(rec)

    matches: list[dict[str, Any]] = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            if a["sha256"] == b["sha256"]:
                matches.append({
                    "file_a": a["file"],
                    "file_b": b["file"],
                    "match_type": "exact_byte_duplicate",
                    "hamming_distance": 0,
                    "hash_bits": 128,
                    "intensity_corr": 1.0,
                    "dims_a": a["dims"],
                    "dims_b": b["dims"],
                    "note": "Exact SHA-256 duplicate image file; verify whether intentional reuse or unintended panel duplication.",
                })
                continue
            if a["base"] is not None and a["transforms"] and b["transforms"]:
                a_base = a["base"]
                best_type = None
                best_dist = 129
                best_corr = -1.0
                for tname, tinfo in b["transforms"].items():
                    dist = (a_base["hv_bits"] ^ tinfo["hv_bits"]).bit_count()
                    corr = _pearson_corr(a_base["vec16"], tinfo["vec16"])
                    if dist < best_dist or (dist == best_dist and corr > best_corr):
                        best_dist = dist
                        best_type = tname
                        best_corr = corr
                # Dual gate: low 128-bit HV-dHash Hamming distance AND very high 16x16 pixel correlation (>=0.96)
                # Sparse white-background charts (bg_frac >= 0.70) require an even tighter gate (dist <= 6 and corr >= 0.985)
                sparse_bg = (a_base["bg_frac"] >= 0.70) or (b["base"]["bg_frac"] >= 0.70)
                max_dist = 6 if sparse_bg else 12
                min_corr = 0.985 if sparse_bg else 0.96
                if best_type is not None and best_dist <= max_dist and best_corr >= min_corr:
                    matches.append({
                        "file_a": a["file"],
                        "file_b": b["file"],
                        "match_type": best_type,
                        "hamming_distance": best_dist,
                        "hash_bits": 128,
                        "intensity_corr": round(best_corr, 4),
                        "dims_a": a["dims"],
                        "dims_b": b["dims"],
                        "note": (
                            f"Perceptual 128-bit HV-dHash match ({best_type}, Hamming={best_dist}/128, "
                            f"r={best_corr:.4f}) between {a['file']} and {b['file']}. "
                            "Anomaly requires human/original-data investigation; similarity alone does not establish intentional misconduct."
                        ),
                    })
    return matches, gaps


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
