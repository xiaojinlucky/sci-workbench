#!/usr/bin/env python3
"""Zero-dependency native document text extractor for PDF, DOCX, and plain text/Markdown.

Supports:
  - .pdf: pdftotext -> pypdf -> pdfplumber -> pure-stdlib zlib FlateDecode BT/ET stream fallback
    (preserves physical page numbering including blank/scanned pages)
  - .docx: pure-stdlib zipfile + xml.etree.ElementTree (paragraphs, tables -> Markdown,
    footnoteReference -> [^id] + footnotes, and OOXML w:instrText / w:fldChar / w:fldSimple
    live citation field detection for Zotero/CSL, Mendeley, EndNote, Citavi, and Word REF)
  - .md / .txt / .csv / .json: safe multi-encoding reader (UTF-8-sig, UTF-8, GB18030, Latin-1)

Returns structured text + page/line offset anchors and honest citation-field degradation metadata
for downstream alignment and provenance mapping.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W_PREFIX = f"{{{WORD_NS['w']}}}"
PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
PDF_BT_ET_RE = re.compile(rb"BT\b(.*?)\bET\b", re.S)
PDF_TJ_STR_RE = re.compile(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", re.S)
PDF_TJ_ARR_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.S)
PDF_PAREN_RE = re.compile(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", re.S)
PDF_HEX_RE = re.compile(rb"<([0-9A-Fa-f\s]{2,})>")
DOI_INLINE_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]*[A-Za-z0-9)]")


def _unescape_pdf_literal(raw: bytes) -> str:
    out = bytearray()
    i = 0
    n = len(raw)
    esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
    while i < n:
        ch = raw[i]
        if ch == 92 and i + 1 < n:  # backslash
            nxt = raw[i + 1]
            if nxt in esc:
                out.append(esc[nxt])
                i += 2
                continue
            if 48 <= nxt <= 55:  # octal
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
    """Pure Python stdlib PDF text extractor using zlib decompression + BT/ET operator scan."""
    data = pdf_path.read_bytes()
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
                for tm in PDF_TJ_STR_RE.finditer(seg):
                    s = _unescape_pdf_literal(tm.group(1))
                    if s:
                        parts.append(s)
                for am in PDF_TJ_ARR_RE.finditer(seg):
                    arr = am.group(1)
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
                if not parts:
                    for hm in PDF_HEX_RE.finditer(seg):
                        s = _decode_pdf_hex(hm.group(1))
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
    """Extract per-page text from PDF while preserving physical page numbering (including empty pages)."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            proc = subprocess.run(
                [pdftotext, "-enc", "UTF-8", str(pdf_path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw_pages = proc.stdout.split("\x0c")
                # pdftotext emits a trailing form-feed after the final page; drop only that single terminator
                if len(raw_pages) > 1 and proc.stdout.endswith("\x0c") and raw_pages[-1] == "":
                    raw_pages = raw_pages[:-1]
                pages = [p.strip() for p in raw_pages]
                if any(pages):
                    return pages, "pdftotext"
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


def _classify_live_field(instr: str, display_text: str, para_index: int, field_index: int) -> dict[str, Any] | None:
    s = " ".join(instr.split()).strip()
    if not s:
        return None
    up = s.upper()
    field_type = ""
    if "ZOTERO_ITEM" in up or ("CSL_CITATION" in up and "ZOTERO" in up):
        field_type = "zotero_csl"
    elif "MENDELEY" in up or "CSL_CITATION" in up:
        field_type = "mendeley_csl"
    elif "EN.CITE" in up or "ENDNOTE" in up:
        field_type = "endnote"
    elif "CITAVI" in up:
        field_type = "citavi"
    elif re.match(r"^(?:REF|PAGEREF|NOTEREF|CITATION)\b", up):
        field_type = "word_ref"
    else:
        return None

    citation_keys: list[str] = []
    dois: list[str] = []
    item_ids: list[str] = []
    json_start = s.find("{")
    if json_start != -1:
        raw_json = s[json_start:]
        try:
            payload = json.loads(raw_json)
            if isinstance(payload, dict):
                cid = payload.get("citationID")
                if cid:
                    citation_keys.append(str(cid))
                for item in payload.get("citationItems", []):
                    if not isinstance(item, dict):
                        continue
                    if item.get("id") is not None:
                        item_ids.append(str(item["id"]))
                    idata = item.get("itemData", {}) if isinstance(item.get("itemData"), dict) else {}
                    if idata.get("citation-key"):
                        citation_keys.append(str(idata["citation-key"]))
                    elif idata.get("id") is not None:
                        item_ids.append(str(idata["id"]))
                    if idata.get("DOI"):
                        dois.append(str(idata["DOI"]).strip().lower())
        except Exception:
            pass
    for dm in DOI_INLINE_RE.findall(s):
        dlow = dm.lower().rstrip(".,;)")
        if dlow not in dois:
            dois.append(dlow)

    return {
        "field_index": field_index,
        "paragraph_index": para_index,
        "field_type": field_type,
        "instruction_preview": s[:240],
        "display_text": display_text.strip(),
        "citation_keys": sorted(set(citation_keys)),
        "item_ids": sorted(set(item_ids)),
        "dois": sorted(set(dois)),
        "preserved_in_markdown": False,
    }


def _docx_para_text_and_fields(
    elem: ET.Element, para_index: int = 0, field_counter: list[int] | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Extract paragraph text, footnoteReference anchors [^id], and OOXML live citation fields."""
    parts: list[str] = []
    live_fields: list[dict[str, Any]] = []
    if field_counter is None:
        field_counter = [0]

    # Stack for nested complex fields (w:fldChar begin -> separate -> end)
    field_stack: list[dict[str, Any]] = []

    def _walk(node: ET.Element) -> None:
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "fldSimple":
            instr = node.attrib.get(f"{W_PREFIX}instr", "")
            sub_parts: list[str] = []
            for ch in node:
                sub_txt, sub_fields = _docx_para_text_and_fields(ch, para_index, field_counter)
                if sub_txt:
                    sub_parts.append(sub_txt)
                live_fields.extend(sub_fields)
            disp = "".join(sub_parts)
            parts.append(disp)
            field_counter[0] += 1
            rec = _classify_live_field(instr, disp, para_index, field_counter[0])
            if rec:
                live_fields.append(rec)
            return
        if tag == "fldChar":
            ftype = node.attrib.get(f"{W_PREFIX}fldCharType", "").lower()
            if ftype == "begin":
                field_stack.append({"instr": [], "display": [], "phase": "instr"})
            elif ftype == "separate" and field_stack:
                field_stack[-1]["phase"] = "display"
            elif ftype == "end" and field_stack:
                done = field_stack.pop()
                instr_str = "".join(done["instr"])
                disp_str = "".join(done["display"])
                field_counter[0] += 1
                rec = _classify_live_field(instr_str, disp_str, para_index, field_counter[0])
                if rec:
                    live_fields.append(rec)
            return
        if tag == "instrText":
            if node.text:
                if field_stack:
                    field_stack[-1]["instr"].append(node.text)
                else:
                    field_counter[0] += 1
                    rec = _classify_live_field(node.text, "", para_index, field_counter[0])
                    if rec:
                        live_fields.append(rec)
            return
        if tag == "t" and node.text:
            parts.append(node.text)
            if field_stack and field_stack[-1]["phase"] == "display":
                field_stack[-1]["display"].append(node.text)
            return
        if tag == "tab":
            parts.append("\t")
            return
        if tag in ("br", "cr"):
            parts.append("\n")
            return
        if tag == "footnoteReference":
            fid = node.attrib.get(f"{W_PREFIX}id", "").strip()
            if fid and fid not in ("-1", "0"):
                parts.append(f"[^{fid}]")
            return
        for child in node:
            _walk(child)

    _walk(elem)
    # Flush any unclosed field instruction
    while field_stack:
        done = field_stack.pop()
        instr_str = "".join(done["instr"])
        disp_str = "".join(done["display"])
        field_counter[0] += 1
        rec = _classify_live_field(instr_str, disp_str, para_index, field_counter[0])
        if rec:
            live_fields.append(rec)

    return "".join(parts).strip(), live_fields


def _docx_para_text(elem: ET.Element) -> str:
    txt, _ = _docx_para_text_and_fields(elem)
    return txt


def extract_docx_text(docx_path: Path) -> tuple[str, dict[str, Any]]:
    """Pure-stdlib OOXML DOCX extractor preserving headings, tables, footnotes, and live citation field audit."""
    blocks: list[str] = []
    live_citation_fields: list[dict[str, Any]] = []
    field_counter = [0]
    table_count = 0
    footnote_count = 0
    para_counter = 0

    try:
        zf_ctx = zipfile.ZipFile(docx_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        return "", {
            "engine": "stdlib_ooxml",
            "error": f"Failed to open DOCX archive: {exc}",
            "live_citation_fields": [],
            "live_citation_fields_count": 0,
            "citation_fields_preserved": False,
        }

    with zf_ctx as zf:
        if "word/document.xml" not in zf.namelist():
            return "", {
                "engine": "stdlib_ooxml",
                "error": "missing word/document.xml",
                "live_citation_fields": [],
                "live_citation_fields_count": 0,
                "citation_fields_preserved": False,
            }
        try:
            root = ET.fromstring(zf.read("word/document.xml"))
        except ET.ParseError as exc:
            return "", {
                "engine": "stdlib_ooxml",
                "error": f"Malformed word/document.xml: {exc}",
                "live_citation_fields": [],
                "live_citation_fields_count": 0,
                "citation_fields_preserved": False,
            }

        body = root.find("w:body", WORD_NS)
        if body is not None:
            for child in body:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    para_counter += 1
                    txt, p_fields = _docx_para_text_and_fields(child, para_counter, field_counter)
                    live_citation_fields.extend(p_fields)
                    if not txt:
                        continue
                    pstyle = child.find("w:pPr/w:pStyle", WORD_NS)
                    style_val = pstyle.attrib.get(f"{W_PREFIX}val", "") if pstyle is not None else ""
                    m_hd = re.match(r"Heading(\d)", style_val, re.I)
                    if m_hd:
                        lvl = min(6, max(1, int(m_hd.group(1))))
                        blocks.append(f"{'#' * lvl} {txt}")
                    else:
                        blocks.append(txt)
                elif tag == "tbl":
                    rows: list[list[str]] = []
                    for tr in child.findall("w:tr", WORD_NS):
                        cells = []
                        for tc in tr.findall("w:tc", WORD_NS):
                            cell_parts: list[str] = []
                            for p in tc.findall("w:p", WORD_NS):
                                para_counter += 1
                                pt, pf = _docx_para_text_and_fields(p, para_counter, field_counter)
                                live_citation_fields.extend(pf)
                                if pt:
                                    cell_parts.append(pt)
                            cell_txt = " ".join(cell_parts).strip()
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
                fn_lines: list[str] = []
                for fn in fn_root.findall("w:footnote", WORD_NS):
                    fid = fn.attrib.get(f"{W_PREFIX}id", "")
                    if fid in ("-1", "0"):
                        continue
                    fn_parts: list[str] = []
                    for p in fn.findall("w:p", WORD_NS):
                        pt, pf = _docx_para_text_and_fields(p, para_counter, field_counter)
                        live_citation_fields.extend(pf)
                        if pt:
                            fn_parts.append(pt)
                    ftxt = " ".join(fn_parts).strip()
                    if ftxt:
                        footnote_count += 1
                        fn_lines.append(f"[^{fid}]: {ftxt}")
                if fn_lines:
                    blocks.append("\n".join(fn_lines))
            except ET.ParseError:
                pass

    degradation_notice = ""
    if live_citation_fields:
        ftypes = sorted({f["field_type"] for f in live_citation_fields})
        degradation_notice = (
            f"Detected {len(live_citation_fields)} OOXML live citation field(s) ({', '.join(ftypes)}). "
            "Exporting DOCX to Markdown plain text strips live Word/Zotero/CSL field codes "
            "(citation_fields_preserved=false); keep original DOCX field bindings for final manuscript re-linking."
        )

    return "\n\n".join(blocks), {
        "engine": "stdlib_ooxml",
        "tables_extracted": table_count,
        "footnotes_extracted": footnote_count,
        "live_citation_fields": live_citation_fields,
        "live_citation_fields_count": len(live_citation_fields),
        "citation_fields_preserved": False,
        "degradation_notice": degradation_notice,
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
    # Heal hyphenated line breaks typical in two-column scholarly PDFs
    cleaned = re.sub(r"([A-Za-z])-\r?\n([a-z])", r"\1\2", page_text)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    return cleaned.strip()


def extract_document(path: Path, fmt: str = "auto") -> dict[str, Any]:
    """Extract normalized text plus page/line provenance metadata from PDF, DOCX, or text/Markdown."""
    if not path.is_file():
        return {
            "source_path": str(path),
            "format": "missing",
            "engine": "none",
            "encoding": "utf-8",
            "pages_count": 0,
            "page_offsets": [],
            "live_citation_fields": [],
            "live_citation_fields_count": 0,
            "citation_fields_preserved": False,
            "text": "",
            "warning": f"Source file does not exist: {path}",
        }

    ext = path.suffix.lower()
    detected = fmt.lower() if fmt and fmt.lower() != "auto" else (
        "pdf" if ext == ".pdf" else ("docx" if ext == ".docx" else "text")
    )
    if detected == "pdf":
        pages, engine = extract_pdf_pages(path)
        page_offsets: list[dict[str, Any]] = []
        empty_pages: list[int] = []
        assembled_lines: list[str] = []
        cur_line = 1
        for pno, pg in enumerate(pages, 1):
            cpg = _clean_pdf_page_text(pg)
            if not cpg:
                empty_pages.append(pno)
                page_offsets.append({"page": pno, "start_line": cur_line, "end_line": cur_line, "chars": 0, "empty": True})
                continue
            pg_lines = cpg.splitlines()
            start_ln = cur_line
            assembled_lines.extend(pg_lines)
            end_ln = start_ln + len(pg_lines) - 1
            page_offsets.append({"page": pno, "start_line": start_ln, "end_line": end_ln, "chars": len(cpg), "empty": False})
            assembled_lines.append("")
            cur_line = end_ln + 2
        text = "\n".join(assembled_lines).strip()
        warn = ""
        if not text:
            warn = "No extractable text layer (likely scanned PDF); OCR required."
        elif empty_pages:
            warn = f"Blank or image-only PDF page(s) detected at physical page(s) {empty_pages}; verify OCR coverage."
        return {
            "source_path": str(path),
            "format": "pdf",
            "engine": engine,
            "encoding": "utf-8",
            "pages_count": len(pages),
            "empty_pages": empty_pages,
            "page_offsets": page_offsets,
            "live_citation_fields": [],
            "live_citation_fields_count": 0,
            "citation_fields_preserved": False,
            "text": text,
            "warning": warn,
        }
    if detected == "docx":
        text, meta = extract_docx_text(path)
        warn = meta.get("error", "") or meta.get("degradation_notice", "")
        return {
            "source_path": str(path),
            "format": "docx",
            "engine": meta.get("engine", "stdlib_ooxml"),
            "encoding": "utf-8",
            "pages_count": 1,
            "page_offsets": [{"page": 1, "start_line": 1, "end_line": max(1, len(text.splitlines())), "chars": len(text)}],
            "tables_extracted": meta.get("tables_extracted", 0),
            "footnotes_extracted": meta.get("footnotes_extracted", 0),
            "live_citation_fields": meta.get("live_citation_fields", []),
            "live_citation_fields_count": meta.get("live_citation_fields_count", 0),
            "citation_fields_preserved": False,
            "degradation_notice": meta.get("degradation_notice", ""),
            "text": text,
            "warning": warn,
        }
    text, enc = read_text_multi_encoding(path)
    lines = text.splitlines()
    return {
        "source_path": str(path),
        "format": "text",
        "engine": f"text_{enc}",
        "encoding": enc,
        "pages_count": 1,
        "page_offsets": [{"page": 1, "start_line": 1, "end_line": max(1, len(lines)), "chars": len(text)}],
        "live_citation_fields": [],
        "live_citation_fields_count": 0,
        "citation_fields_preserved": False,
        "text": text,
        "warning": "",
    }
