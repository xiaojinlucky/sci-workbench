"""M4: Token-budget-aware multi-format scientific document, PDF, XLSX, DOCX, table, and citation block indexer."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TEX_SEC_RE = re.compile(r"^\\(?:sub)*section\*?\{(.+?)\}")
CAPTION_RE = re.compile(r"^\s*(?:Figure|Fig\.?|Table|Supplementary\s+(?:Figure|Table)|图|表)\s*\d+[:.\s-].*", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\b", re.I)
TEXT_EXTS = {".md", ".txt", ".tex", ".rst", ".log", ".yaml", ".yml", ".m", ".py", ".r"}
TABULAR_EXTS = {".csv", ".tsv"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _est_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _index_text_lines(lines: list[str], rel: str, max_block_chars: int = 3200, *, page_no: int | None = None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    title = f"page_{page_no}" if page_no is not None else "(preamble)"
    start = 1
    buf: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal buf
        joined = "\n".join(buf).strip()
        if not joined:
            buf = []
            return
        chunks = [joined[i : i + max_block_chars] for i in range(0, len(joined), max_block_chars)] or [""]
        for idx, chunk in enumerate(chunks):
            captions = [ln.strip()[:160] for ln in chunk.splitlines() if CAPTION_RE.match(ln)]
            dois = sorted(set(DOI_RE.findall(chunk)))[:8]
            arxivs = sorted(set(ARXIV_RE.findall(chunk)))[:8]
            heading_tag = title if len(chunks) == 1 else f"{title} [part {idx + 1}/{len(chunks)}]"
            blk: dict[str, Any] = {
                "file": rel,
                "kind": "pdf_page" if page_no is not None else "text_section",
                "title": heading_tag,
                "start_line": start,
                "end_line": end_line,
                "source_ref": f"{rel}#page={page_no}" if page_no is not None else f"{rel}#L{start}-L{max(start, end_line)}",
                "char_len": len(chunk),
                "est_tokens": _est_tokens(chunk),
                "captions": captions[:5],
                "identifiers": dois + arxivs,
                "preview": chunk[:260].replace("\n", " "),
            }
            if page_no is not None:
                blk["page"] = page_no
            blocks.append(blk)
        buf = []

    for lineno, raw in enumerate(lines, start=1):
        m_md = HEADING_RE.match(raw)
        m_tex = TEX_SEC_RE.match(raw.strip())
        if m_md or m_tex:
            flush(lineno - 1)
            title = (m_md.group(2) if m_md else m_tex.group(1)).strip()
            start = lineno
            buf.append(raw)
        else:
            buf.append(raw)
    flush(len(lines))
    return blocks


def _index_tabular(path: Path, rel: str) -> list[dict[str, Any]]:
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not raw:
        return [{"file": rel, "kind": "table", "rows": 0, "columns": [], "est_tokens": 5}]
    rows = list(csv.reader(raw, delimiter=delim))
    header = rows[0] if rows else []
    data_rows = rows[1:]
    null_counts = {h: 0 for h in header}
    for row in data_rows:
        for i, h in enumerate(header):
            if i >= len(row) or str(row[i]).strip() in ("", "NA", "null", "None", "nan"):
                null_counts[h] += 1
    summary = json.dumps({"cols": header, "rows": len(data_rows)}, ensure_ascii=False)
    return [{
        "file": rel,
        "kind": "table",
        "title": f"table:{path.name}",
        "source_ref": f"{rel}#L1-L{len(raw)}",
        "start_line": 1,
        "end_line": len(raw),
        "rows": len(data_rows),
        "columns": header[:60],
        "null_counts": {k: v for k, v in null_counts.items() if v > 0},
        "sample_rows": data_rows[:3],
        "est_tokens": _est_tokens(summary),
        "identifiers": [],
        "preview": f"rows={len(data_rows)} cols={header[:10]}",
    }]


def _col_letters_to_idx(ref: str) -> int:
    col = "".join(ch for ch in ref if ch.isalpha()).upper()
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _index_xlsx(path: Path, rel: str) -> list[dict[str, Any]]:
    """Pure-stdlib multi-sheet XLSX indexer via zipfile + XML (zero third-party deps)."""
    ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    ns_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    blocks: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sst.findall(f"{ns_main}si"):
                    shared.append("".join(t.text or "" for t in si.iter(f"{ns_main}t")))
            rels: dict[str, str] = {}
            if "xl/_rels/workbook.xml.rels" in zf.namelist():
                for r in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
                    rid, tgt = r.attrib.get("Id", ""), r.attrib.get("Target", "")
                    if rid and tgt:
                        rels[rid] = tgt if tgt.startswith("xl/") else f"xl/{tgt.lstrip('/')}"
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            for idx, sh in enumerate(wb_root.findall(f".//{ns_main}sheet"), start=1):
                sname = sh.attrib.get("name", f"Sheet{idx}")
                tgt = rels.get(sh.attrib.get(f"{ns_rel}id", ""), f"xl/worksheets/sheet{idx}.xml")
                if tgt not in zf.namelist():
                    continue
                s_root = ET.fromstring(zf.read(tgt))
                rows: list[list[str]] = []
                for row_el in s_root.findall(f".//{ns_main}sheetData/{ns_main}row"):
                    rmap: dict[int, str] = {}
                    max_c = -1
                    for c_el in row_el.findall(f"{ns_main}c"):
                        ci = _col_letters_to_idx(c_el.attrib.get("r", "")) if c_el.attrib.get("r") else max_c + 1
                        max_c = max(max_c, ci)
                        ct = c_el.attrib.get("t", "")
                        if ct == "inlineStr":
                            val = "".join(t.text or "" for t in c_el.iter(f"{ns_main}t"))
                        else:
                            v_el = c_el.find(f"{ns_main}v")
                            raw = v_el.text if v_el is not None else ""
                            val = shared[int(raw)] if ct == "s" and raw.isdigit() and int(raw) < len(shared) else (raw or "")
                        rmap[ci] = val.strip()
                    if max_c >= 0:
                        row_list = [rmap.get(i, "") for i in range(max_c + 1)]
                        if any(row_list):
                            rows.append(row_list)
                header = rows[0] if rows else []
                data_rows = rows[1:] if len(rows) > 1 else []
                summary = json.dumps({"sheet": sname, "cols": header[:30], "rows": len(data_rows)}, ensure_ascii=False)
                blocks.append({
                    "file": rel,
                    "kind": "xlsx_sheet",
                    "sheet_name": sname,
                    "title": f"xlsx:{path.name}!{sname}",
                    "source_ref": f"{rel}#{sname}!R1-R{len(rows)}",
                    "rows": len(data_rows),
                    "columns": header[:60],
                    "sample_rows": data_rows[:3],
                    "est_tokens": _est_tokens(summary),
                    "identifiers": [],
                    "preview": f"sheet={sname} rows={len(data_rows)} cols={header[:10]}",
                })
    except Exception as exc:
        return [{"file": rel, "kind": "unparsed_source", "status": "UNPARSED_SOURCE", "manual_transcription_needed": True, "error": str(exc), "est_tokens": 20}]
    return blocks


def _index_docx(path: Path, rel: str) -> list[dict[str, Any]]:
    """Pure-stdlib DOCX paragraph & table text indexer via zipfile + word/document.xml."""
    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return [{"file": rel, "kind": "unparsed_source", "status": "UNPARSED_SOURCE", "manual_transcription_needed": True, "error": "missing word/document.xml", "est_tokens": 20}]
            root = ET.fromstring(zf.read("word/document.xml"))
            lines: list[str] = []
            for p in root.iter(f"{ns_w}p"):
                txt = "".join(t.text or "" for t in p.iter(f"{ns_w}t")).strip()
                if txt:
                    lines.append(txt)
        if not lines:
            return [{"file": rel, "kind": "unparsed_source", "status": "UNPARSED_SOURCE", "manual_transcription_needed": True, "error": "empty_docx_text", "est_tokens": 20}]
        return _index_text_lines(lines, rel)
    except Exception as exc:
        return [{"file": rel, "kind": "unparsed_source", "status": "UNPARSED_SOURCE", "manual_transcription_needed": True, "error": str(exc), "est_tokens": 20}]


def _extract_pdf_pages_offline(path: Path) -> tuple[str, list[str]]:
    """3-tier offline PDF text extraction: pypdf -> pdftotext -> pure-stdlib zlib FlateDecode stream scan."""
    # Tier 1: pypdf / PyPDF2 (pure Python, zero banned native deps)
    for mod_name in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(mod_name)
            reader = mod.PdfReader(str(path))
            pages = [(pg.extract_text() or "").strip() for pg in reader.pages]
            if sum(len(p) for p in pages) >= 40:
                return mod_name, pages
        except Exception:
            pass
    # Tier 2: pdftotext CLI if present
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            cp = subprocess.run([pdftotext, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
            if cp.returncode == 0 and len(cp.stdout.strip()) >= 40:
                pages = [p.strip() for p in cp.stdout.split("\f") if p.strip()]
                return "pdftotext", pages
        except Exception:
            pass
    # Tier 3: pure-stdlib FlateDecode stream + literal string scan
    try:
        raw = path.read_bytes()
        chunks: list[str] = []
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, flags=re.S):
            blob = m.group(1)
            decoded = None
            for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
                try:
                    decoded = zlib.decompress(blob, wbits)
                    break
                except Exception:
                    continue
            text_src = (decoded or blob).decode("latin-1", errors="ignore")
            lits = re.findall(r"\(([^()\\]{2,180})\)\s*Tj|\[([^\]]{2,400})\]\s*TJ", text_src)
            for a, b in lits:
                s = a or " ".join(re.findall(r"\(([^()\\]{1,120})\)", b))
                s = re.sub(r"[^\x20-\x7e\u4e00-\u9fff]+", " ", s).strip()
                if len(s) >= 2:
                    chunks.append(s)
        joined = "\n".join(chunks).strip()
        if len(joined) >= 60:
            return "stdlib_pdf_stream", [joined[i : i + 3500] for i in range(0, len(joined), 3500)]
    except Exception:
        pass
    return "unparsed", []


def _index_pdf(path: Path, rel: str) -> list[dict[str, Any]]:
    backend, pages = _extract_pdf_pages_offline(path)
    if not pages:
        return [{
            "file": rel,
            "kind": "unparsed_source",
            "status": "UNPARSED_SOURCE",
            "manual_transcription_needed": True,
            "title": f"unparsed_pdf:{path.name}",
            "source_ref": f"{rel}#page=1",
            "est_tokens": 25,
            "identifiers": [],
            "preview": "PDF has no extractable text layer offline (scanned/custom-encoded); requires manual transcription rather than assuming null.",
        }]
    blocks: list[dict[str, Any]] = []
    for pno, ptxt in enumerate(pages, start=1):
        p_blocks = _index_text_lines(ptxt.splitlines(), rel, page_no=pno)
        for b in p_blocks:
            b["pdf_backend"] = backend
        blocks.extend(p_blocks)
    return blocks


def _index_json(path: Path, rel: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        obj = json.loads(text)
    except Exception as exc:
        return [{"file": rel, "kind": "json_invalid", "error": str(exc), "est_tokens": 20}]
    if isinstance(obj, dict):
        keys = list(obj.keys())[:40]
        preview = f"object keys={keys[:12]}"
    elif isinstance(obj, list):
        keys = []
        preview = f"array len={len(obj)}"
    else:
        keys = []
        preview = f"scalar type={type(obj).__name__}"
    return [{
        "file": rel,
        "kind": "json_summary",
        "title": f"json:{path.name}",
        "source_ref": f"{rel}#L1-L{len(text.splitlines())}",
        "start_line": 1,
        "end_line": len(text.splitlines()),
        "top_keys": keys,
        "bytes": len(text.encode("utf-8")),
        "est_tokens": _est_tokens(preview),
        "identifiers": sorted(set(DOI_RE.findall(text) + ARXIV_RE.findall(text)))[:10],
        "preview": preview,
    }]


def index_path(target: str | Path, *, max_files: int = 100) -> dict[str, Any]:
    """Build a compact structured block index for a file or directory across text, PDF, XLSX, DOCX, XML, JSON, CSV/TSV."""
    root = Path(target)
    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    blocks: list[dict[str, Any]] = []
    skipped: list[str] = []
    unparsed: list[str] = []
    for fpath in paths[:max_files]:
        rel = fpath.name if root.is_file() else fpath.relative_to(root).as_posix()
        ext = fpath.suffix.lower()
        if ext in TEXT_EXTS:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            blocks.extend(_index_text_lines(lines, rel))
        elif ext in {".html", ".htm", ".xml"}:
            parser = _TextExtractor()
            parser.feed(fpath.read_text(encoding="utf-8", errors="replace"))
            blocks.extend(_index_text_lines(parser.parts, rel))
        elif ext in TABULAR_EXTS:
            blocks.extend(_index_tabular(fpath, rel))
        elif ext == ".xlsx":
            blks = _index_xlsx(fpath, rel)
            blocks.extend(blks)
            if any(b.get("kind") == "unparsed_source" for b in blks):
                unparsed.append(rel)
        elif ext == ".docx":
            blks = _index_docx(fpath, rel)
            blocks.extend(blks)
            if any(b.get("kind") == "unparsed_source" for b in blks):
                unparsed.append(rel)
        elif ext == ".pdf":
            blks = _index_pdf(fpath, rel)
            blocks.extend(blks)
            if any(b.get("kind") == "unparsed_source" for b in blks):
                unparsed.append(rel)
        elif ext == ".json":
            blocks.extend(_index_json(fpath, rel))
        else:
            skipped.append(rel)
    return {
        "root": str(root),
        "total_blocks": len(blocks),
        "total_est_tokens": sum(int(b.get("est_tokens", 0)) for b in blocks),
        "unparsed_sources": unparsed[:40],
        "skipped_files": skipped[:40],
        "blocks": blocks,
    }


def query_blocks(index: dict[str, Any], keywords: list[str], *, max_tokens: int = 2400) -> list[dict[str, Any]]:
    """Select top keyword-matching blocks while staying inside max_tokens budget."""
    terms = [k.lower().strip() for k in keywords if k.strip()]
    scored: list[tuple[int, dict[str, Any]]] = []
    for blk in index.get("blocks", []):
        hay = " ".join([
            str(blk.get("title", "")),
            str(blk.get("preview", "")),
            " ".join(blk.get("captions", [])),
            " ".join(blk.get("columns", [])),
        ]).lower()
        hits = sum(hay.count(t) for t in terms) if terms else 1
        if hits > 0:
            scored.append((hits, blk))
    scored.sort(key=lambda pair: (-pair[0], int(pair[1].get("est_tokens", 1))))

    picked: list[dict[str, Any]] = []
    budget = 0
    for _, blk in scored:
        toks = int(blk.get("est_tokens", 1))
        if picked and budget + toks > max_tokens:
            continue
        picked.append(blk)
        budget += toks
    return picked


def slice_lines(path: str | Path, start_line: int, end_line: int, *, max_chars: int = 8000) -> str:
    """Read an exact inclusive line slice capped to max_chars."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    lo = max(1, int(start_line))
    hi = min(len(lines), int(end_line))
    snippet = "\n".join(lines[lo - 1 : hi])
    return snippet[:max_chars]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Token-budget-aware multi-format document/PDF/XLSX/DOCX/table block indexer.")
    parser.add_argument("--scan", default="", help="File or directory to index.")
    parser.add_argument("--query", default="", help="Comma-separated keywords to filter blocks.")
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    args = parser.parse_args(argv)

    if not args.scan:
        parser.print_help()
        return 2
    idx = index_path(args.scan)
    out_obj: Any = idx
    if args.query:
        kws = [k for k in args.query.split(",") if k.strip()]
        out_obj = {"query": kws, "matched": query_blocks(idx, kws, max_tokens=args.max_tokens)}
    encoded = json.dumps(out_obj, ensure_ascii=False, indent=2)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
