# -*- coding: utf-8 -*-
"""Document text extraction with page / section / paragraph structure.

Backends (first available wins): pdfplumber -> pypdf -> pdftotext CLI for PDF; python-docx for
DOCX (fallback: raw XML zip parse); html.parser for HTML; plain read for txt/md. Every block
keeps a locator (page, paragraph index, char offsets) so downstream evidence can point back.
"""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

WS = re.compile(r"[ \t]+")
MULTINL = re.compile(r"\n{3,}")

HEADING_RE = re.compile(
    r"^\s*(?:(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,2})|[IVX]{1,4})[.)]?\s+)?(?P<t>[A-Z][^\n]{2,90})\s*$"
)
SECTION_KEYWORDS = [
    "abstract", "summary", "highlights", "keywords", "introduction", "background", "related work", "related work",
    "methods", "materials and methods", "methodology", "experimental", "experiments", "setup", "results",
    "results and discussion", "discussion", "conclusions", "conclusion", "limitations", "acknowledgments",
    "acknowledgements", "author contributions", "funding", "conflicts of interest", "declaration",
    "references", "bibliography", "supplementary", "appendix", "data availability",
]


@dataclass
class Block:
    page: int
    kind: str            # heading | paragraph | caption | reference | table | list
    text: str
    start: int = 0       # char offset in doc text
    end: int = 0
    section: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Doc:
    path: str
    rel: str
    kind: str
    sha256: str = ""
    size: int = 0
    pages: List[Dict[str, Any]] = field(default_factory=list)   # [{page, text}]
    blocks: List[Block] = field(default_factory=list)
    text: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    language: str = ""
    lang_conf: float = 0.0
    warnings: List[str] = field(default_factory=list)
    annots: List[Dict[str, Any]] = field(default_factory=list)

    def sections(self) -> List[Dict[str, Any]]:
        out = []
        cur = {"heading": "(front matter)", "page": 1, "blocks": []}
        for b in self.blocks:
            if b.kind == "heading":
                if cur["blocks"]:
                    out.append(cur)
                cur = {"heading": b.text, "page": b.page, "blocks": []}
            else:
                cur["blocks"].append(b)
        if cur["blocks"]:
            out.append(cur)
        return [{"heading": s["heading"], "page": s["page"], "n_blocks": len(s["blocks"]), "text": "\n".join(b.text for b in s["blocks"])} for s in out]

    def paragraphs(self) -> List[Dict[str, Any]]:
        return [{"page": b.page, "section": b.section, "text": b.text, "start": b.start, "end": b.end} for b in self.blocks if b.kind in ("paragraph", "caption", "list")]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["blocks"] = [asdict(b) for b in self.blocks]
        return d


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------
def _classify_blocks(pages_text: List[str]) -> (List[Block], str):
    """Turn per-page text into heading/paragraph blocks with section labels."""
    blocks: List[Block] = []
    full = []
    pos = 0
    section = "(front matter)"
    in_refs = False
    for pno, ptxt in enumerate(pages_text, start=1):
        norm = ptxt.replace("\r\n", "\n").replace("\r", "\n")
        full.append(norm)
        # split into lines, merge lines into paragraphs (heuristic: blank line or heading line)
        paras = []
        buf = []
        for line in norm.split("\n"):
            s = line.strip()
            if not s:
                if buf:
                    paras.append(" ".join(buf)); buf = []
                continue
            if len(s) <= 90 and (HEADING_RE.match(s) or s.lower().rstrip(":") in SECTION_KEYWORDS or re.match(r"^(figure|fig\.|table)\s+\d+", s.lower())):
                if buf:
                    paras.append(" ".join(buf)); buf = []
                paras.append(("H", s))
                continue
            buf.append(s)
        if buf:
            paras.append(" ".join(buf))
        for item in paras:
            if isinstance(item, tuple):
                text = item[1]
                kind = "heading"
                low = text.lower().rstrip(".:").strip()
                if low.startswith("reference") or low.startswith("bibliography"):
                    in_refs = True
                elif low in ("appendix", "supplementary") or low.startswith("appendix"):
                    in_refs = False
                if not in_refs and low not in ("abstract", "summary", "keywords"):
                    section = re.sub(r"^\d+(\.\d+)*[.)]?\s*", "", text).strip()
                b = Block(page=pno, kind=kind, text=text, start=pos, end=pos + len(text), section=section if not in_refs else "References")
                pos += len(text) + 1
                blocks.append(b)
            else:
                text = item
                if not text:
                    continue
                kind = "paragraph"
                if re.match(r"^(figure|fig\.|table|scheme)\s*\d+", text, re.I) and len(text) < 400:
                    kind = "caption"
                elif in_refs or (re.match(r"^\[?\d+[\].]\s", text) and re.search(r"\(?(19|20)\d{2}\)?", text) and len(text) < 600 and ("doi" in text.lower() or "pp" in text.lower() or "vol" in text.lower() or "journal" in text.lower() or re.search(r"\d+[-–]\d+", text))):
                    kind = "reference"
                elif in_refs:
                    kind = "reference"
                b = Block(page=pno, kind=kind, text=text, start=pos, end=pos + len(text), section="References" if in_refs else section)
                pos += len(text) + 1
                blocks.append(b)
    return blocks, "\n".join(full)


def extract_pdf(path: str, warnings: List[str]) -> (List[str], Dict[str, Any], List[Dict[str, Any]]):
    pages_text: List[str] = []
    meta: Dict[str, Any] = {}
    annots: List[Dict[str, Any]] = []
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            meta = {k: str(v) for k, v in (pdf.metadata or {}).items()}
            for p in pdf.pages:
                try:
                    pages_text.append(p.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                except Exception:
                    pages_text.append("")
                try:
                    for a in (getattr(p, "annots", None) or []):
                        annots.append(_ann_from_pdfplumber(p.page_number, a))
                except Exception:
                    pass
        return pages_text, meta, annots
    except ImportError:
        pass
    except Exception as e:
        warnings.append(f"pdfplumber failed: {type(e).__name__}: {e}")
    try:
        from pypdf import PdfReader  # type: ignore
        rd = PdfReader(path)
        try:
            meta = {k: str(v) for k, v in (rd.metadata or {}).items()}
        except Exception:
            meta = {}
        for p in rd.pages:
            try:
                pages_text.append(p.extract_text() or "")
            except Exception:
                pages_text.append("")
            try:
                for a in (p.get("/Annots") or []):
                    obj = a.get_object() if hasattr(a, "get_object") else a
                    annots.append(_ann_from_pypdf(len(pages_text), obj))
            except Exception:
                pass
        return pages_text, meta, annots
    except ImportError:
        warnings.append("No PDF backend available (pdfplumber/pdf_backend/pypdf missing)")
    except Exception as e:
        warnings.append(f"pypdf failed: {type(e).__name__}: {e}")
    return pages_text, meta, annots


def _rgb_name(c) -> str:
    try:
        if isinstance(c, (list, tuple)) and len(c) >= 3:
            r, g, b = [float(x) for x in c[:3]]
        elif isinstance(c, dict):
            r, g, b = float(c.get("r", 0)), float(c.get("g", 0)), float(c.get("b", 0))
        elif isinstance(c, (int, float)):
            r = g = b = float(c)
        else:
            return "unknown"
        mx, mn = max(r, g, b), min(r, g, b)
        if mx < 0.2:
            return "black"
        if mx - mn < 0.2:
            return "gray" if mx < 0.8 else "white"
        if r >= g and r >= b:
            return "yellow" if g > 0.6 else "red" if b < 0.5 else "magenta"
        if g >= r and g >= b:
            return "green" if b < 0.6 or r > 0.3 else "cyan"
        return "blue"
    except Exception:
        return "unknown"


def _ann_from_pdfplumber(page_no: int, a: Any) -> Dict[str, Any]:
    data = a.get("data", {}) if isinstance(a, dict) else getattr(a, "data", {}) if a is not None else {}
    rect = a.get("rect") if isinstance(a, dict) else getattr(a, "rect", None)
    bbox = [round(float(v), 2) for v in rect] if rect else None
    return {"page": page_no, "subtype": str(a.get("uri") and "Link" or data.get("Subtype", "") or "").strip("/"), "contents": str(data.get("Contents", "") or ""), "author": str(data.get("T", "") or ""), "color": _rgb_name(data.get("C")), "rect": bbox, "quadpoints": data.get("QuadPoints") or data.get("Quadpoints")}



def _ann_from_pypdf(page_no: int, obj: Any) -> Dict[str, Any]:
    try:
        rect = [float(v) for v in obj.get("/Rect", [])]
    except Exception:
        rect = None
    try:
        c = obj.get("/C")
        color = _rgb_name([float(x) for x in c]) if c is not None else "unknown"
    except Exception:
        color = "unknown"
    qp = None
    try:
        q = obj.get("/QuadPoints")
        qp = [round(float(v), 2) for v in q] if q else None
    except Exception:
        qp = None
    return {"page": page_no, "subtype": str(obj.get("/Subtype", "")).strip("/"), "contents": str(obj.get("/Contents", "") or ""), "author": str(obj.get("/T", "") or ""), "color": color, "rect": rect, "quadpoints": qp, "modified": str(obj.get("/M", "") or "")}


# ----------------------------------------------------------------------------
class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks: List[Dict[str, Any]] = []
        self.cur = []
        self.tag = None
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "li", "tr", "br", "section", "article", "td", "th", "dd", "dt", "blockquote"):
            self._flush()
            self.tag = tag
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "li", "tr", "section", "article", "td", "th", "dd", "dt", "blockquote"):
            self._flush()
        if tag == "title":
            self._in_title = False

    def _flush(self):
        text = WS.sub(" ", "".join(self.cur)).strip()
        self.cur = []
        if text:
            kind = "heading" if self.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "title") else "paragraph"
            self.blocks.append({"kind": kind, "text": text, "tag": self.tag})

    def handle_data(self, data):
        if self.skip:
            return
        if self._in_title:
            self.title += data
        self.cur.append(data)


def extract_html(path: str) -> (List[str], Dict[str, Any]):
    raw = open(path, "rb").read()
    text = None
    m = re.search(rb"charset=[\"']?([A-Za-z0-9_\-]+)", raw[:4000])
    encs = [m.group(1).decode("ascii", "ignore")] if m else []
    encs += ["utf-8", "cp1252", "latin-1"]
    for e in encs:
        try:
            text = raw.decode(e)
            break
        except Exception:
            continue
    text = text or raw.decode("utf-8", "replace")
    p = _HTMLText()
    try:
        p.feed(text)
    except Exception:
        pass
    p._flush()
    # Google Patents / PubMed / publisher pages: keep title and abstract blocks
    meta = {"html_title": WS.sub(" ", p.title).strip()}
    blocks = [f"[{b['kind']}:{b['tag']}] {b['text']}" for b in p.blocks]
    return ["\n".join(blocks)], meta


def extract_docx(path: str, warnings: List[str]) -> (List[str], Dict[str, Any]):
    meta: Dict[str, Any] = {}
    try:
        import docx  # type: ignore
        d = docx.Document(path)
        cp = d.core_properties
        meta = {"title": cp.title or "", "author": cp.author or "", "created": str(cp.created or ""), "modified": str(cp.modified or ""), "subject": cp.subject or "", "keywords": cp.keywords or ""}
        lines = []
        for para in d.paragraphs:
            t = para.text.strip()
            if not t:
                continue
            style = (para.style.name if para.style is not None else "") or ""
            if style.lower().startswith("heading") or style.lower() == "title":
                lines.append(f"[heading:{style}] {t}")
            else:
                lines.append(t)
        # tables
        for ti, tbl in enumerate(d.tables):
            lines.append(f"[table:{ti+1}]")
            for row in tbl.rows:
                lines.append(" | ".join(c.text.strip() for c in row.cells))
        return ["\n".join(lines)], meta
    except ImportError:
        pass
    except Exception as e:
        warnings.append(f"python-docx failed: {e}")
    # fallback: raw document.xml
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
            paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
            lines = [WS.sub(" ", re.sub(r"<[^>]+>", "", p)).strip() for p in paras]
            core = z.read("docProps/core.xml").decode("utf-8", "replace") if "docProps/core.xml" in z.namelist() else ""
            meta = {"title": (re.search(r"<dc:title>(.*?)</dc:title>", core, re.S) or [None, ""])[1], "author": (re.search(r"<dc:creator>(.*?)</dc:creator>", core, re.S) or [None, ""])[1]}
            return ["\n".join(l for l in lines if l)], meta
    except Exception as e:
        warnings.append(f"docx fallback failed: {e}")
        return [], {}


def extract_plain(path: str) -> (List[str], Dict[str, Any]):
    raw = open(path, "rb").read()
    text = None
    for e in ("utf-8", "utf-8-sig", "gb18030", "cp1252", "latin-1"):
        try:
            text = raw.decode(e)
            break
        except Exception:
            continue
    text = text or raw.decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # markdown headings -> "[heading] x" markers for the block classifier
    lines = []
    for ln in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lines.append(f"[heading] {m.group(2).strip()}")
        else:
            lines.append(ln)
    return ["\n".join(lines)], {}


def extract_doc(path: str, rel: str = "") -> Doc:
    warnings: List[str] = []
    ext = os.path.splitext(path)[1].lower()
    kind = {".pdf": "pdf", ".docx": "docx", ".doc": "doc", ".html": "html", ".htm": "html", ".txt": "txt", ".md": "md", ".xml": "xml", ".rtf": "rtf"}.get(ext, ext.lstrip(".") or "bin")
    doc = Doc(path=path, rel=rel or os.path.basename(path), kind=kind, size=os.path.getsize(path), sha256=sha256_file(path), warnings=warnings)
    pages_text: List[str] = []
    meta: Dict[str, Any] = {}
    annots: List[Dict[str, Any]] = []
    if kind == "pdf":
        pages_text, meta, annots = extract_pdf(path, warnings)
    elif kind in ("docx",):
        pages_text, meta = extract_docx(path, warnings)
    elif kind in ("html", "htm"):
        pages_text, meta = extract_html(path)
    elif kind in ("txt", "md", "xml", "rtf", "csv", "json", "yaml", "yml", "bib", "ris", "nbib", "tsv"):
        if kind == "xml":
            # strip tags for reading
            raw = open(path, "rb").read().decode("utf-8", "replace")
            raw = re.sub(r"<[^>]+>", " ", raw)
            pages_text = [MULTINL.sub("\n\n", raw)]
        else:
            pages_text, meta = extract_plain(path)
    else:
        try:
            pages_text, meta = extract_plain(path)
        except Exception as e:
            warnings.append(f"cannot read: {e}")
    if not pages_text or all(not (p or "").strip() for p in pages_text):
        if kind == "pdf":
            warnings.append("PDF yielded no text layer (scanned/image PDF?) - OCR not available offline; content marked unreadable")
    pages = [{"page": i + 1, "chars": len(t or ""), "text": t or ""} for i, t in enumerate(pages_text)]
    doc.pages = pages
    doc.meta = meta
    doc.annots = annots
    blocks, full = _classify_blocks(pages_text)
    doc.blocks = blocks
    doc.text = full
    try:
        from . import lang as _lang  # type: ignore
        sample = " ".join((b.text for b in blocks if b.kind != "reference"))[:6000] or full[:6000]
        l, c, _ = _lang.detect_language(sample)
        doc.language, doc.lang_conf = l, c
    except Exception:
        try:
            import lang as _lang  # type: ignore
            sample = " ".join((b.text for b in blocks if b.kind != "reference"))[:6000] or full[:6000]
            l, c, _ = _lang.detect_language(sample)
            doc.language, doc.lang_conf = l, c
        except Exception:
            doc.language = "unknown"
    return doc
