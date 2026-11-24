#!/usr/bin/env python3
"""Zero-dependency native document text extractor for PDF, DOCX, and plain text/Markdown.

Supports:
  - .pdf: pdftotext -> pypdf -> pdfplumber -> pure-stdlib zlib FlateDecode BT/ET stream fallback
          (preserves physical page indices including blank pages; flags low-confidence fallback)
  - .docx: pure-stdlib zipfile + xml.etree.ElementTree (headings, numbered lists via w:numPr,
           tables -> Markdown, inline footnote references [^id], and footnotes.xml)
  - .md / .txt / .csv / .json: safe multi-encoding reader (UTF-8-sig, UTF-8, GB18030, Latin-1)

Returns structured text + physical page/line offset anchors for downstream alignment and provenance mapping.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
PDF_BT_ET_RE = re.compile(rb"BT\b(.*?)\bET\b", re.S)
PDF_TEXT_OP_RE = re.compile(
    rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj|\[(.*?)\]\s*TJ|<([0-9A-Fa-f\s]{2,})>\s*Tj",
    re.S,
)
PDF_PAREN_RE = re.compile(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", re.S)
PDF_HEX_RE = re.compile(rb"<([0-9A-Fa-f\s]{2,})>")


def _unescape_pdf_literal(raw: bytes) -> str:
    out = bytearray()
    i = 0
    n = len(raw)
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
    while i < n:
        ch = raw[i]
        if ch == 92 and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in esc:
                out.append(esc[nxt])
                i += 2
                continue
            if 48 <= nxt <= 55:
                oct_digits = bytes([nxt])
                j = i + 2
                while j < min(n, i + 4) and 48 <= raw[j] <= 55:
                    oct_digits += bytes([raw[j]])
                    j += 1
                out.append(int(oct_digits, 8) & 0xFF)
                i = j
                continue
            if nxt in (10, 13):
                i += 2 + (1 if nxt == 13 and i + 2 < n and raw[i + 2] == 10 else 0)
                continue
            out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    if out.startswith(b"\xfe\xff"):
        return out[2:].decode("utf-16-be", errors="ignore")
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("latin-1", errors="ignore")


def _decode_pdf_hex(hex_bytes: bytes) -> str:
    cleaned = re.sub(rb"\s+", b"", hex_bytes)
    if len(cleaned) % 2 == 1:
        cleaned += b"0"
    try:
        raw = bytes.fromhex(cleaned.decode("ascii"))
    except ValueError:
        return ""
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="ignore")
    if len(raw) >= 2 and raw[0] == 0:
        return raw.decode("utf-16-be", errors="ignore")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="ignore")


def _extract_pdf_stdlib_zlib(pdf_path: Path) -> list[str]:
    """Low-confidence pure-stdlib PDF stream fallback preserving operator order."""
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return []
    pages: list[str] = []
    for sm in PDF_STREAM_RE.finditer(data):
        raw_stream = sm.group(1)
        chunks = [raw_stream]
        try:
            chunks.insert(0, zlib.decompress(raw_stream))
        except zlib.error:
            try:
                chunks.insert(0, zlib.decompress(raw_stream.strip()))
            except zlib.error:
                pass
        extracted_lines: list[str] = []
        for blob in chunks:
            if b"BT" not in blob:
                continue
            for bm in PDF_BT_ET_RE.finditer(blob):
                seg = bm.group(1)
                parts: list[str] = []
                for om in PDF_TEXT_OP_RE.finditer(seg):
                    if om.group(1) is not None:
                        s = _unescape_pdf_literal(om.group(1))
                        if s:
                            parts.append(s)
                    elif om.group(2) is not None:
                        arr = om.group(2)
                        arr_parts: list[str] = []
                        for pm in PDF_PAREN_RE.finditer(arr):
                            s = _unescape_pdf_literal(pm.group(1))
                            if s:
                                arr_parts.append(s)
                        if not arr_parts:
                            for hm in PDF_HEX_RE.finditer(arr):
                                s = _decode_pdf_hex(hm.group(1))
                                if s:
                                    arr_parts.append(s)
                        if arr_parts:
                            parts.append("".join(arr_parts))
                    elif om.group(3) is not None:
                        s = _decode_pdf_hex(om.group(3))
                        if s:
                            parts.append(s)
                joined = " ".join(p.strip() for p in parts if p.strip())
                if joined:
                    extracted_lines.append(joined)
            if extracted_lines:
                break
        if extracted_lines:
            text_page = "\n".join(extracted_lines)
            if sum(1 for c in text_page if c.isprintable()) / max(1, len(text_page)) >= 0.75:
                pages.append(text_page)
    return pages


def extract_pdf_pages(pdf_path: Path) -> tuple[list[str], str]:
    """Extract per-page text from PDF preserving physical page indices (including blank pages)."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run(
                [pdftotext, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_pages = [p.strip() for p in proc.stdout.split("\x0c")]
                # pdftotext emits a trailing form-feed after the last page
                if len(raw_pages) > 1 and raw_pages[-1] == "":
                    raw_pages.pop()
                if any(raw_pages):
                    return raw_pages, "pdftotext"
        except Exception:
            pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        pages = [(pg.extract_text() or "").strip() for pg in reader.pages]
        if any(pages):
            return pages, "pypdf"
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            pages = [(pg.extract_text() or "").strip() for pg in doc.pages]
        if any(pages):
            return pages, "pdfplumber"
    except Exception:
        pass
    pages = _extract_pdf_stdlib_zlib(pdf_path)
    return pages, "stdlib_zlib" if pages else "pdf_empty_or_scanned"


def _docx_para_text(elem: ET.Element) -> str:
    parts: list[str] = []
    w_id_attr = f"{{{WORD_NS['w']}}}id"
    for node in elem.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
        elif tag == "footnoteReference":
            fid = node.attrib.get(w_id_attr, "")
            if fid and fid not in ("-1", "0"):
                parts.append(f"[^{fid}]")
    return "".join(parts).strip()


def extract_docx_text(docx_path: Path) -> tuple[str, dict[str, Any]]:
    """Pure-stdlib OOXML DOCX extractor preserving headings, numbered lists, Markdown tables, and footnotes."""
    blocks: list[str] = []
    table_count = 0
    footnote_count = 0
    list_counters: dict[tuple[str, str], int] = {}
    w_val_attr = f"{{{WORD_NS['w']}}}val"
    try:
        zf_ctx = zipfile.ZipFile(docx_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        return "", {"engine": "stdlib_ooxml", "error": f"Failed to open DOCX archive: {exc}"}

    with zf_ctx as zf:
        if "word/document.xml" not in zf.namelist():
            return "", {"engine": "stdlib_ooxml", "error": "missing word/document.xml"}
        try:
            root = ET.fromstring(zf.read("word/document.xml"))
        except ET.ParseError as exc:
            return "", {"engine": "stdlib_ooxml", "error": f"Corrupt word/document.xml: {exc}"}

        body = root.find("w:body", WORD_NS)
        if body is not None:
            for child in body:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    txt = _docx_para_text(child)
                    if not txt:
                        continue
                    pstyle = child.find("w:pPr/w:pStyle", WORD_NS)
                    style_val = pstyle.attrib.get(w_val_attr, "") if pstyle is not None else ""
                    m_hd = re.match(r"Heading(\d)", style_val, re.I)
                    if m_hd:
                        lvl = min(6, max(1, int(m_hd.group(1))))
                        blocks.append(f"{'#' * lvl} {txt}")
                        continue
                    num_pr = child.find("w:pPr/w:numPr", WORD_NS)
                    if num_pr is not None:
                        ilvl_el = num_pr.find("w:ilvl", WORD_NS)
                        numid_el = num_pr.find("w:numId", WORD_NS)
                        ilvl = ilvl_el.attrib.get(w_val_attr, "0") if ilvl_el is not None else "0"
                        numid = numid_el.attrib.get(w_val_attr, "0") if numid_el is not None else "0"
                        if numid != "0":
                            key = (numid, ilvl)
                            list_counters[key] = list_counters.get(key, 0) + 1
                            indent = "  " * min(3, int(ilvl) if ilvl.isdigit() else 0)
                            if not re.match(r"^(?:\d+\.|[-*]|\([a-z0-9]+\))\s", txt, re.I):
                                txt = f"{indent}{list_counters[key]}. {txt}"
                    blocks.append(txt)
                elif tag == "tbl":
                    rows: list[list[str]] = []
                    for tr in child.findall("w:tr", WORD_NS):
                        cells = []
                        for tc in tr.findall("w:tc", WORD_NS):
                            cell_txt = " ".join(_docx_para_text(p) for p in tc.findall("w:p", WORD_NS)).strip()
                            cells.append(cell_txt.replace("|", "\\|"))
                        if any(cells):
                            rows.append(cells)
                    if rows:
                        table_count += 1
                        ncols = max(len(r) for r in rows)
                        norm_rows = [r + [""] * (ncols - len(r)) for r in rows]
                        md_tbl = [
                            "| " + " | ".join(norm_rows[0]) + " |",
                            "| " + " | ".join(["---"] * ncols) + " |",
                        ]
                        for r in norm_rows[1:]:
                            md_tbl.append("| " + " | ".join(r) + " |")
                        blocks.append("\n".join(md_tbl))
        if "word/footnotes.xml" in zf.namelist():
            try:
                fn_root = ET.fromstring(zf.read("word/footnotes.xml"))
            except ET.ParseError:
                fn_root = None
            if fn_root is not None:
                fn_lines: list[str] = []
                for fn in fn_root.findall("w:footnote", WORD_NS):
                    fid = fn.attrib.get(f"{{{WORD_NS['w']}}}id", "")
                    if fid in ("-1", "0"):
                        continue
                    ftxt = " ".join(_docx_para_text(p) for p in fn.findall("w:p", WORD_NS)).strip()
                    if ftxt:
                        footnote_count += 1
                        fn_lines.append(f"[^{fid}]: {ftxt}")
                if fn_lines:
                    blocks.append("\n".join(fn_lines))
    return "\n\n".join(blocks), {
        "engine": "stdlib_ooxml",
        "tables_extracted": table_count,
        "footnotes_extracted": footnote_count,
    }


def read_text_multi_encoding(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _clean_pdf_page_text(page_text: str) -> str:
    cleaned = re.sub(r"([A-Za-z])-\r?\n([a-z])", r"\1\2", page_text)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    return cleaned.strip()


def extract_document(path: Path, fmt: str = "auto") -> dict[str, Any]:
    """Extract normalized text plus physical page/line provenance metadata from PDF, DOCX, or text/Markdown."""
    ext = path.suffix.lower()
    detected = fmt.lower() if fmt and fmt.lower() != "auto" else (
        "pdf" if ext == ".pdf" else ("docx" if ext == ".docx" else "text")
    )
    if detected == "pdf":
        pages, engine = extract_pdf_pages(path)
        page_offsets: list[dict[str, Any]] = []
        blank_pages: list[int] = []
        assembled_lines: list[str] = []
        cur_line = 1
        for pno, pg in enumerate(pages, 1):
            cpg = _clean_pdf_page_text(pg)
            if not cpg:
                blank_pages.append(pno)
                page_offsets.append({"page": pno, "start_line": None, "end_line": None, "chars": 0, "blank": True})
                continue
            pg_lines = cpg.splitlines()
            start_ln = cur_line
            assembled_lines.extend(pg_lines)
            end_ln = start_ln + len(pg_lines) - 1
            page_offsets.append({"page": pno, "start_line": start_ln, "end_line": end_ln, "chars": len(cpg), "blank": False})
            assembled_lines.append("")
            cur_line = end_ln + 2
        text = "\n".join(assembled_lines).strip()
        low_conf = engine in ("stdlib_zlib", "pdf_empty_or_scanned")
        warning = ""
        if not text:
            warning = "No extractable text layer (likely scanned PDF); OCR required; mark partial_with_gaps."
        elif engine == "stdlib_zlib":
            warning = "Low-confidence pure-stdlib stream fallback used without ToUnicode font mapping; verify extracted text before finalizing."
        elif blank_pages:
            warning = f"Physical page(s) {blank_pages} had empty text layers (possible blank or scanned pages)."
        return {
            "source_path": str(path),
            "format": "pdf",
            "engine": engine,
            "encoding": "utf-8",
            "pages_count": len(pages),
            "blank_pages": blank_pages,
            "low_confidence_fallback": low_conf,
            "extraction_status": "empty_or_scanned" if not text else ("low_confidence" if low_conf else "ok"),
            "page_offsets": page_offsets,
            "text": text,
            "warning": warning,
        }
    if detected == "docx":
        text, meta = extract_docx_text(path)
        err = meta.get("error", "")
        return {
            "source_path": str(path),
            "format": "docx",
            "engine": meta.get("engine", "stdlib_ooxml"),
            "encoding": "utf-8",
            "pages_count": 1,
            "blank_pages": [],
            "low_confidence_fallback": bool(err),
            "extraction_status": "error" if err else "ok",
            "page_offsets": [{"page": 1, "start_line": 1, "end_line": max(1, len(text.splitlines())), "chars": len(text), "blank": not bool(text)}],
            "tables_extracted": meta.get("tables_extracted", 0),
            "footnotes_extracted": meta.get("footnotes_extracted", 0),
            "text": text,
            "warning": err,
        }
    try:
        text, enc = read_text_multi_encoding(path)
    except OSError as exc:
        return {
            "source_path": str(path),
            "format": "text",
            "engine": "text_error",
            "encoding": "",
            "pages_count": 0,
            "blank_pages": [],
            "low_confidence_fallback": True,
            "extraction_status": "error",
            "page_offsets": [],
            "text": "",
            "warning": f"Failed to read file: {exc}",
        }
    lines = text.splitlines()
    return {
        "source_path": str(path),
        "format": "text",
        "engine": f"text_{enc}",
        "encoding": enc,
        "pages_count": 1,
        "blank_pages": [],
        "low_confidence_fallback": False,
        "extraction_status": "ok" if text.strip() else "empty",
        "page_offsets": [{"page": 1, "start_line": 1, "end_line": max(1, len(lines)), "chars": len(text), "blank": not bool(text.strip())}],
        "text": text,
        "warning": "",
    }
