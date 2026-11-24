"""Unified offline bibliographic, API-snapshot, HTML, XML, RIS, BibTeX, CSV & PDF loader.

Zero prohibited dependencies (no fitz/pymupdf/cairosvg/win32com) and zero external network calls.
Preserves raw source provenance, page/paragraph anchors, multilingual titles/abstracts,
OpenAlex inverted-index abstracts, SciELO JSON/HTML, Google Patents HTML, arXiv XML, RIS, and PDF pages.
"""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from evidence_state_guard import extract_identifiers, normalize_doi

BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,]+)\s*,(.+?)\n\s*\}", re.S)
BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|\"([^\"]*)\"|(\d+))")
HTML_TAG_RE = re.compile(r"<[^>]+>")
SECTION_HEADING_RE = re.compile(
    r"^(?:#{1,4}\s*|\d+(?:\.\d+)*\.?\s+)?"
    r"(abstract|summary|resumo|resumen|摘要|introduction|background|methods?|materials?\s+and\s+methods?|"
    r"methodology|experimental|results?|findings?|discussion|limitations?|conclusions?|references?|ethics|"
    r"funding|conflicts?\s+of\s+interest|data\s+availability|statistical\s+analysis)\b[:：]?\s*$",
    re.I,
)


def strip_html_tags(raw: str) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    cleaned = re.sub(r"<(br|p|div|li|tr|h[1-6]|section)[^>]*>", "\n", cleaned, flags=re.I)
    cleaned = HTML_TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [" ".join(ln.split()) for ln in cleaned.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def fold_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def reconstruct_openalex_abstract(inv_index: Any) -> str:
    if not isinstance(inv_index, dict) or not inv_index:
        return ""
    pos_map: dict[int, str] = {}
    for word, positions in inv_index.items():
        if isinstance(positions, list):
            for p in positions:
                if isinstance(p, int) and p >= 0:
                    pos_map[p] = str(word)
    if not pos_map:
        return ""
    return " ".join(pos_map[i] for i in sorted(pos_map))


def _count_glued_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{20,}", text or ""))


def read_pdf_pages_safe(pdf_path: Path) -> tuple[list[dict[str, Any]], str]:
    """Extract per-page text safely using non-banned tools only (pdfplumber / pypdf / pdftotext).

    Uses tuned word-spacing tolerance (x_tolerance=1.2) to prevent inter-word gluing in two-column
    or tight-kerning academic PDFs, falling back to pypdf/pdftotext when cleaner spacing is produced.
    """
    candidates: list[tuple[int, int, str, list[dict[str, Any]]]] = []
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            pgs: list[dict[str, Any]] = []
            for idx, page in enumerate(doc.pages, start=1):
                txt = (page.extract_text(x_tolerance=1.2, y_tolerance=3) or "").strip()
                if not txt:
                    txt = (page.extract_text() or "").strip()
                pgs.append({"page_number": idx, "text": txt, "char_count": len(txt)})
        total_chars = sum(p["char_count"] for p in pgs)
        glued = sum(_count_glued_words(p["text"]) for p in pgs)
        if total_chars > 0:
            candidates.append((glued, -total_chars, "pdfplumber", pgs))
    except Exception:
        pass

    if not candidates or candidates[0][0] > 3:
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(str(pdf_path))
            pgs = []
            for idx, page in enumerate(reader.pages, start=1):
                txt = (page.extract_text() or "").strip()
                pgs.append({"page_number": idx, "text": txt, "char_count": len(txt)})
            total_chars = sum(p["char_count"] for p in pgs)
            glued = sum(_count_glued_words(p["text"]) for p in pgs)
            if total_chars > 0:
                candidates.append((glued, -total_chars, "pypdf", pgs))
        except Exception:
            pass

    if not candidates:
        bin_p = shutil.which("pdftotext")
        if bin_p:
            proc = subprocess.run(
                [bin_p, str(pdf_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0 and proc.stdout:
                pgs = []
                raw_pages = proc.stdout.split("\x0c")
                for idx, chunk in enumerate(raw_pages, start=1):
                    txt = chunk.strip()
                    if idx == len(raw_pages) and not txt:
                        continue
                    pgs.append({"page_number": idx, "text": txt, "char_count": len(txt)})
                total_chars = sum(p["char_count"] for p in pgs)
                glued = sum(_count_glued_words(p["text"]) for p in pgs)
                if total_chars > 0:
                    candidates.append((glued, -total_chars, "pdftotext", pgs))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]))
        _, _, best_extractor, best_pages = candidates[0]
        return best_pages, best_extractor
    return [], "unavailable"


def read_pdf_text_safe(pdf_path: Path) -> str:
    pages, _ = read_pdf_pages_safe(pdf_path)
    return "\n\n".join(f"[Page {p['page_number']}]\n{p['text']}" for p in pages if p.get("text"))


def segment_text_to_paragraphs(
    text: str,
    *,
    record_id: str,
    page_number: int | None = None,
    default_section: str = "body",
) -> list[dict[str, Any]]:
    """Split raw text into section-annotated, paragraph-indexed blocks."""
    paragraphs: list[dict[str, Any]] = []
    cur_section = default_section
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    idx = 0
    for chunk in chunks:
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        first_line = lines[0]
        m_head = SECTION_HEADING_RE.match(first_line)
        if m_head:
            cur_section = m_head.group(1).lower()
            if len(lines) == 1:
                continue
            body_txt = " ".join(lines[1:]).strip()
        else:
            m_inline = re.match(
                r"^(abstract|resumo|resumen|摘要|methods?|results?|discussion|limitations?|conclusions?)\s*[:：]\s*(.+)$",
                first_line,
                re.I,
            )
            if m_inline:
                cur_section = m_inline.group(1).lower()
                body_txt = " ".join([m_inline.group(2)] + lines[1:]).strip()
            else:
                body_txt = " ".join(lines).strip()
        if not body_txt:
            continue
        idx += 1
        p_prefix = f"{record_id}-P{page_number:02d}-{idx:02d}" if page_number else f"{record_id}-P{idx:02d}"
        paragraphs.append({
            "paragraph_id": p_prefix,
            "section": cur_section,
            "page_number": page_number,
            "is_abstract": cur_section in {"abstract", "summary", "resumo", "resumen", "摘要"},
            "text": body_txt,
        })
    return paragraphs


def parse_bibtex_records(raw: str, source_file: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for m in BIB_ENTRY_RE.finditer(raw):
        etype, citekey, body = m.group(1), m.group(2).strip(), m.group(3)
        fields: dict[str, str] = {}
        for fm in BIB_FIELD_RE.finditer(body):
            k = fm.group(1).lower()
            v = next((g for g in fm.groups()[1:] if g is not None), "")
            fields[k] = " ".join(v.split())
        authors = [a.strip() for a in fields.get("author", "").split(" and ") if a.strip()]
        yr = int(fields["year"]) if fields.get("year", "").isdigit() else None
        records.append({
            "record_id": citekey,
            "type": etype.lower(),
            "title": fields.get("title", ""),
            "raw_title": fields.get("title", ""),
            "authors": authors,
            "year": yr,
            "doi": normalize_doi(fields.get("doi", "")),
            "venue": fields.get("journal") or fields.get("booktitle") or "",
            "abstract": fields.get("abstract", ""),
            "language": fields.get("language", ""),
            "source": "bibtex",
            "source_file": source_file,
        })
    return records


def parse_ris_records(raw: str, source_file: str) -> list[dict[str, Any]]:
    """Parse standard RIS bibliographic exports (PubMed/Embase/Scopus/WoS/EndNote)."""
    records: list[dict[str, Any]] = []
    cur: dict[str, Any] = {"authors": [], "keywords": []}
    idx = 0
    for line in raw.splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s{1,2}-\s*(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"type": val.lower(), "authors": [], "keywords": []}
        elif tag in ("TI", "T1"):
            cur["title"] = (cur.get("title", "") + " " + val).strip()
        elif tag in ("AU", "A1"):
            cur["authors"].append(val)
        elif tag in ("PY", "Y1"):
            ym = re.search(r"(1[89]\d{2}|20[0-3]\d)", val)
            if ym:
                cur["year"] = int(ym.group(1))
        elif tag == "DO":
            cur["doi"] = normalize_doi(val)
        elif tag in ("AB", "N2"):
            cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
        elif tag in ("JO", "JF", "T2"):
            cur.setdefault("venue", val)
        elif tag == "LA":
            cur["language"] = val.lower()
        elif tag == "AN":
            cur["accession_number"] = val
        elif tag == "KW":
            cur["keywords"].append(val)
        elif tag == "ER":
            if cur.get("title") or cur.get("doi"):
                idx += 1
                cur["record_id"] = cur.get("accession_number") or f"{Path(source_file).stem}_RIS_{idx:04d}"
                cur["raw_title"] = cur.get("title", "")
                cur["source"] = "ris"
                cur["source_file"] = source_file
                records.append(normalize_record_dict(cur, default_id=cur["record_id"], source_file=source_file, default_source="ris"))
            cur = {"authors": [], "keywords": []}
    return records


def parse_arxiv_xml(raw: str, source_file: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return records
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    for idx, entry in enumerate(root.findall("a:entry", ns), start=1):
        eid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        arxiv_m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", eid)
        arxiv_id = arxiv_m.group(1) if arxiv_m else ""
        title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
        published = (entry.findtext("a:published", default="", namespaces=ns) or "").strip()
        yr = int(published[:4]) if published[:4].isdigit() else None
        authors = [
            " ".join((a.findtext("a:name", default="", namespaces=ns) or "").split())
            for a in entry.findall("a:author", ns)
        ]
        doi_el = entry.findtext("arxiv:doi", default="", namespaces=ns)
        doi = normalize_doi(doi_el) if doi_el else (f"10.48550/arxiv.{arxiv_id.split('v')[0]}" if arxiv_id else "")
        records.append(normalize_record_dict({
            "record_id": f"arxiv:{arxiv_id}" if arxiv_id else f"arxiv_{idx:04d}",
            "title": title,
            "abstract": summary,
            "authors": [a for a in authors if a],
            "year": yr,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "venue": "arXiv",
            "type": "preprint",
            "source": "arxiv",
        }, default_id=f"arxiv_{idx:04d}", source_file=source_file, default_source="arxiv"))
    return records


def parse_scielo_article_json(obj: dict[str, Any], source_file: str) -> list[dict[str, Any]]:
    """Parse SciELO ArticleMeta JSON preserving multilingual titles, abstracts, language, and DOI."""
    art = obj.get("article") if isinstance(obj.get("article"), dict) else obj
    if not isinstance(art, dict) or not any(k in art for k in ("v12", "v83", "v237", "v40")):
        return []
    pid = str(obj.get("code") or art.get("v880", [{}])[0].get("_", "") or Path(source_file).stem)
    doi_raw = obj.get("doi") or ""
    if not doi_raw and isinstance(art.get("v237"), list) and art["v237"]:
        doi_raw = str(art["v237"][0].get("_", ""))
    doi = normalize_doi(doi_raw)

    body_lang = ""
    if isinstance(art.get("v40"), list) and art["v40"]:
        body_lang = str(art["v40"][0].get("_", "")).strip().lower()

    titles_by_lang: dict[str, str] = {}
    for titem in (art.get("v12") or []):
        if isinstance(titem, dict) and titem.get("_"):
            lg = str(titem.get("l") or body_lang or "unknown").lower()
            titles_by_lang[lg] = html.unescape(" ".join(str(titem["_"]).split()))

    abstracts_by_lang: dict[str, str] = {}
    for aitem in (art.get("v83") or []):
        if isinstance(aitem, dict) and (aitem.get("a") or aitem.get("_")):
            lg = str(aitem.get("l") or "unknown").lower()
            abstracts_by_lang[lg] = html.unescape(" ".join(str(aitem.get("a") or aitem.get("_")).split()))

    authors: list[str] = []
    for au in (art.get("v10") or []):
        if isinstance(au, dict):
            s = str(au.get("s") or "").strip()
            n = str(au.get("n") or "").strip()
            if s or n:
                authors.append(f"{n} {s}".strip())

    yr = obj.get("publication_year")
    if not yr and isinstance(art.get("v65"), list) and art["v65"]:
        y_str = str(art["v65"][0].get("_", ""))[:4]
        if y_str.isdigit():
            yr = int(y_str)
    elif isinstance(yr, str) and yr[:4].isdigit():
        yr = int(yr[:4])

    primary_title = titles_by_lang.get(body_lang) or next(iter(titles_by_lang.values()), "")
    primary_abs = abstracts_by_lang.get(body_lang) or next(iter(abstracts_by_lang.values()), "")

    return [normalize_record_dict({
        "record_id": pid,
        "work_id": pid,
        "title": primary_title,
        "raw_title": primary_title,
        "titles_by_language": titles_by_lang,
        "abstract": primary_abs,
        "abstracts_by_language": abstracts_by_lang,
        "authors": authors,
        "year": yr,
        "doi": doi,
        "body_language": body_lang or "unknown",
        "source": "scielo_meta",
        "view_type": "metadata_multilingual_record",
    }, default_id=pid, source_file=source_file, default_source="scielo_meta")]


def parse_html_document(raw_html: str, source_file: str, stem: str) -> dict[str, Any]:
    """Parse Google Patents HTML or SciELO/Publisher HTML without external dependencies."""
    metas = re.findall(r'<meta\s+[^>]*name=["\']([^"\']+)["\'][^>]*content=["\']([^"\']*)["\']', raw_html, re.I)
    metas += re.findall(r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']', raw_html, re.I)
    meta_map: dict[str, list[str]] = {}
    for pair in metas:
        # Handle both attribute orders by checking if first looks like meta name
        k, v = pair[0], pair[1]
        if not re.match(r"^(DC\.|citation_|scheme|description|keywords|language)", k, re.I) and re.match(
            r"^(DC\.|citation_|scheme|description|keywords|language)", v, re.I
        ):
            k, v = v, k
        meta_map.setdefault(k, []).append(html.unescape(v.strip()))

    is_patent = (
        "patent" in " ".join(meta_map.get("DC.type", [])).lower()
        or "citation_patent_number" in meta_map
        or "citation_patent_publication_number" in meta_map
        or "citation_patent_application_number" in meta_map
    )

    if is_patent:
        pub_no = (
            (meta_map.get("citation_patent_publication_number") or [None])[0]
            or (meta_map.get("citation_patent_number") or [None])[0]
            or stem
        )
        pub_no = pub_no.replace(":", "")
        app_no = ((meta_map.get("citation_patent_application_number") or [""])[0]).replace(":", "")
        title = " ".join(((meta_map.get("DC.title") or [""])[0]).split())
        abstract = " ".join(((meta_map.get("DC.description") or [""])[0]).split())
        dates = meta_map.get("DC.date", [])
        creators = meta_map.get("DC.contributor", [])
        assignees = re.findall(r'itemprop="assigneeOriginal"[^>]*>(.*?)</dd>', raw_html, re.S | re.I)
        assignees = [strip_html_tags(a) for a in assignees if strip_html_tags(a)]
        inventors = re.findall(r'itemprop="inventor"[^>]*>(.*?)</dd>', raw_html, re.S | re.I)
        inventors = [strip_html_tags(i) for i in inventors if strip_html_tags(i)]

        claims_m = re.search(r'<section[^>]*itemprop="claims"[^>]*>(.*?)</section>', raw_html, re.S | re.I)
        claims_text = strip_html_tags(claims_m.group(1)) if claims_m else ""
        ipc_cpc = sorted(set(re.findall(r"\b([A-HY]\d{2}[A-Z]\s*\d{1,4}/\d{2,4})\b", raw_html)))

        # Priority applications & family rows from Google Patents HTML structure
        priorities: list[str] = []
        for pm in re.finditer(r'itemprop="priorityApplications"[^>]*>(.*?)</tr>', raw_html, re.S | re.I):
            app_hit = re.search(r'itemprop="applicationNumber"[^>]*>([^<]+)<', pm.group(1), re.I)
            if app_hit:
                priorities.append(app_hit.group(1).strip())
        if not priorities:
            for pm in re.finditer(r'<tr[^>]*itemprop="application"[^>]*>(.*?)</tr>', raw_html, re.S | re.I):
                row_txt = pm.group(1)
                if "priority" in row_txt.lower():
                    app_hit = re.search(r'itemprop="applicationNumber"[^>]*>([^<]+)<', row_txt, re.I)
                    if app_hit:
                        priorities.append(app_hit.group(1).strip())

        fam_id_m = re.search(r'itemprop="family"[^>]*>.*?<span[^>]*itemprop="id"[^>]*>([^<]+)<', raw_html, re.S | re.I)
        family_id = fam_id_m.group(1).strip() if fam_id_m else ""
        fam_members = sorted(set(re.findall(r'itemprop="publicationNumber"[^>]*>([A-Z]{2}\d+[A-Z]\d?)<', raw_html)))

        status_m = re.search(r'itemprop="ifiStatus"[^>]*>([^<]+)<', raw_html, re.I)
        legal_status = status_m.group(1).strip() if status_m else ""

        return normalize_record_dict({
            "record_id": pub_no or stem,
            "type": "patent",
            "kind": "patent",
            "title": title or stem,
            "raw_title": title or stem,
            "publication_number": pub_no,
            "application_number": app_no,
            "priority_numbers": sorted(set(priorities)),
            "family_id": family_id,
            "observed_family_members": fam_members,
            "assignee": assignees or creators[-1:],
            "inventors": inventors or creators,
            "filing_date": dates[0] if dates else None,
            "publication_date": dates[-1] if len(dates) > 1 else (dates[0] if dates else None),
            "ipc_cpc": ipc_cpc,
            "abstract": abstract,
            "claims_text": claims_text,
            "text": f"{abstract}\n\n{claims_text}".strip(),
            "legal_status": legal_status,
            "source": "google_patents_html",
        }, default_id=stem, source_file=source_file, default_source="google_patents_html")

    # Academic HTML (e.g. SciELO / JATS HTML)
    html_lang_m = re.search(r'<html[^>]+lang=["\']([a-zA-Z_-]+)["\']', raw_html, re.I)
    body_lang = (
        (meta_map.get("citation_language") or [None])[0]
        or (html_lang_m.group(1) if html_lang_m else "")
    ).split("-")[0].lower()
    if not body_lang:
        m_suf = re.search(r"_(pt|en|es|zh|fr|de|ja)$", stem, re.I)
        if m_suf:
            body_lang = m_suf.group(1).lower()

    title = (meta_map.get("citation_title") or [""])[0]
    if not title:
        t_m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.S | re.I)
        title = strip_html_tags(t_m.group(1)) if t_m else stem
    doi = normalize_doi((meta_map.get("citation_doi") or [""])[0])
    authors = meta_map.get("citation_author", [])
    pub_date = (meta_map.get("citation_publication_date") or [""])[0]
    yr = int(pub_date[:4]) if pub_date[:4].isdigit() else None
    journal = (meta_map.get("citation_journal_title") or [""])[0]

    # Extract article body text cleanly
    art_m = re.search(r"<article[^>]*>(.*?)</article>", raw_html, re.S | re.I)
    body_raw = art_m.group(1) if art_m else raw_html
    body_txt = strip_html_tags(body_raw)
    abs_m = re.search(r'(?:Abstract|Resumo|Resumen|摘要)\s*[:：]?\s*(.{80,2200}?)(?:\n\s*(?:Keywords|Palavras-chave|Introduction|Introdução|1\.)|\Z)', body_txt, re.S | re.I)
    abstract = " ".join(abs_m.group(1).split()) if abs_m else ""
    paragraphs = segment_text_to_paragraphs(body_txt, record_id=stem)

    return normalize_record_dict({
        "record_id": stem,
        "title": title,
        "raw_title": title,
        "authors": authors,
        "year": yr,
        "doi": doi,
        "venue": journal,
        "body_language": body_lang or "unknown",
        "abstract": abstract,
        "text": body_txt,
        "paragraphs": paragraphs,
        "source": "html_article",
    }, default_id=stem, source_file=source_file, default_source="html_article")


def normalize_record_dict(
    raw: dict[str, Any],
    *,
    default_id: str,
    source_file: str,
    default_source: str = "local_json",
) -> dict[str, Any]:
    title = raw.get("title") or raw.get("articleTitle") or raw.get("display_name") or ""
    if isinstance(title, list):
        title = title[0] if title else ""
    title = html.unescape(strip_html_tags(str(title))) if "<" in str(title) or "&" in str(title) else str(title)

    authors_raw = raw.get("authors") or raw.get("author") or raw.get("authorships") or raw.get("authorString") or []
    authors: list[str] = []
    if isinstance(authors_raw, list):
        for a in authors_raw:
            if isinstance(a, dict):
                if isinstance(a.get("author"), dict) and a["author"].get("display_name"):
                    authors.append(str(a["author"]["display_name"]))
                else:
                    name = a.get("name") or f"{a.get('given', '')} {a.get('family', '')}".strip() or str(a)
                    authors.append(name)
            else:
                authors.append(str(a))
    elif isinstance(authors_raw, str):
        authors = [x.strip() for x in re.split(r"[;,]", authors_raw) if x.strip()]

    year_val = raw.get("year") or raw.get("pubYear") or raw.get("publication_year")
    if year_val is None:
        for dk in ("issued", "published-print", "published-online", "created"):
            dobj = raw.get(dk)
            if isinstance(dobj, dict) and isinstance(dobj.get("date-parts"), list) and dobj["date-parts"] and dobj["date-parts"][0]:
                year_val = dobj["date-parts"][0][0]
                break
        if year_val is None and isinstance(raw.get("publication_date"), str) and raw["publication_date"][:4].isdigit():
            year_val = int(raw["publication_date"][:4])
    if isinstance(year_val, str):
        ym = re.search(r"(1[89]\d{2}|20[0-3]\d)", year_val)
        year_val = int(ym.group(1)) if ym else None
    elif not isinstance(year_val, int):
        year_val = None

    doi = normalize_doi(str(raw.get("doi") or raw.get("DOI") or ""))
    doi_from_text = False
    text_blob = str(raw.get("text") or raw.get("fulltext") or raw.get("body") or "")
    abstract = str(raw.get("abstract") or raw.get("abstractText") or raw.get("summary") or raw.get("snippet") or "")
    if not abstract and isinstance(raw.get("abstract_inverted_index"), dict):
        abstract = reconstruct_openalex_abstract(raw["abstract_inverted_index"])
    if "<" in abstract or "&" in abstract:
        abstract = html.unescape(strip_html_tags(abstract))

    if not doi:
        ids = extract_identifiers(f"{title} {abstract} {text_blob[:4000]}")
        if ids["doi"]:
            doi = ids["doi"][0]
            doi_from_text = True

    venue = raw.get("venue") or raw.get("journal") or raw.get("journalTitle") or raw.get("container-title") or ""
    if isinstance(venue, list):
        venue = venue[0] if venue else ""
    if not venue and isinstance(raw.get("primary_location"), dict):
        src_obj = raw["primary_location"].get("source") or {}
        if isinstance(src_obj, dict):
            venue = src_obj.get("display_name") or ""

    rid = str(raw.get("record_id") or raw.get("paper_id") or raw.get("publication_number") or raw.get("id") or default_id)
    rec = dict(raw)
    rec.update({
        "record_id": rid,
        "title": title.strip(),
        "raw_title": str(raw.get("raw_title") or title).strip(),
        "authors": authors,
        "year": year_val,
        "doi": doi,
        "doi_from_text": bool(raw.get("doi_from_text", doi_from_text)),
        "arxiv_id": str(raw.get("arxiv_id") or raw.get("arXiv") or ""),
        "pmid": str(raw.get("pmid") or raw.get("PMID") or ""),
        "pmcid": str(raw.get("pmcid") or raw.get("PMCID") or ""),
        "venue": str(venue).strip(),
        "abstract": abstract.strip(),
        "text": text_blob.strip(),
        "source": str(raw.get("source") or raw.get("database") or default_source),
        "source_file": source_file,
    })
    return rec


def load_input_bundle(input_dir: Path) -> dict[str, Any]:
    """Load case.json, task.md, and all finite local bibliographic/document artifacts."""
    case_path = input_dir / "case.json"
    case_data: dict[str, Any] = {}
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))
    task_md = ""
    if (input_dir / "task.md").is_file():
        task_md = (input_dir / "task.md").read_text(encoding="utf-8", errors="replace")

    records: list[dict[str, Any]] = []
    reference_set: list[dict[str, Any]] = []
    supplemental_hits: list[dict[str, Any]] = []
    queries: list[Any] = list(case_data.get("queries") or case_data.get("questions") or [])
    raw_texts: list[dict[str, Any]] = []
    ingest_diagnostics: list[dict[str, Any]] = []

    def _collect_list(items: Any, target: list[dict[str, Any]], src: str, src_name: str) -> None:
        if isinstance(items, list):
            for idx, it in enumerate(items, start=1):
                if isinstance(it, dict):
                    target.append(normalize_record_dict(it, default_id=f"{src}_{idx:04d}", source_file=src, default_source=src_name))
                elif isinstance(it, str) and it.strip():
                    target.append(normalize_record_dict({"title": it.strip()}, default_id=f"{src}_{idx:04d}", source_file=src, default_source=src_name))

    case_record_count = 0
    for key in ("records", "papers", "documents", "candidates", "results", "sources", "patents", "views", "previous_snapshot", "current_snapshot"):
        val = case_data.get(key)
        if not isinstance(val, list) or not val:
            continue
        # Guard: 'sources' may be a list of database names (strings) rather than bibliographic records
        if key == "sources" and all(isinstance(x, str) for x in val):
            continue
        before_k = len(records)
        _collect_list(val, records, f"case.json:{key}", str(case_data.get("source") or key))
        added = len(records) - before_k
        case_record_count += added
        if added > 0:
            ingest_diagnostics.append({"file": f"case.json:{key}", "format": "case_json", "count": added})
    if "reference_set" in case_data:
        before_ref = len(reference_set)
        _collect_list(case_data["reference_set"], reference_set, "case.json:reference_set", "reference_set")
        ingest_diagnostics.append({"file": "case.json:reference_set", "format": "case_json_ref", "count": len(reference_set) - before_ref})
    if "supplemental_hits" in case_data:
        before_supp = len(supplemental_hits)
        _collect_list(case_data["supplemental_hits"], supplemental_hits, "case.json:supplemental_hits", "supplemental")
        ingest_diagnostics.append({"file": "case.json:supplemental_hits", "format": "case_json_supp", "count": len(supplemental_hits) - before_supp})

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if fp.name in ("case.json", "task.md"):
            continue
        rel = fp.relative_to(input_dir).as_posix()
        ext = fp.suffix.lower()
        stem_low = fp.stem.lower()
        is_ref_file = "reference" in stem_low or "gold_seed" in stem_low or "seed_set" in stem_low
        is_supp_file = "supplemental" in stem_low or "supp_hit" in stem_low
        target = reference_set if is_ref_file else (supplemental_hits if is_supp_file else records)

        if ext == ".bib":
            parsed = parse_bibtex_records(fp.read_text(encoding="utf-8", errors="replace"), rel)
            target.extend(parsed)
            ingest_diagnostics.append({"file": rel, "format": "bibtex", "count": len(parsed)})
        elif ext == ".ris":
            parsed = parse_ris_records(fp.read_text(encoding="utf-8", errors="replace"), rel)
            target.extend(parsed)
            ingest_diagnostics.append({"file": rel, "format": "ris", "count": len(parsed)})
        elif ext == ".xml":
            parsed = parse_arxiv_xml(fp.read_text(encoding="utf-8", errors="replace"), rel)
            target.extend(parsed)
            ingest_diagnostics.append({"file": rel, "format": "xml", "count": len(parsed)})
        elif ext in (".html", ".htm"):
            raw_html = fp.read_text(encoding="utf-8", errors="replace")
            rec = parse_html_document(raw_html, rel, fp.stem)
            target.append(rec)
            ingest_diagnostics.append({"file": rel, "format": "html", "count": 1, "detected_source": rec.get("source")})
        elif ext in (".csv", ".tsv"):
            delim = "\t" if ext == ".tsv" else ","
            cnt = 0
            with fp.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                for idx, row in enumerate(csv.DictReader(fh, delimiter=delim), start=1):
                    target.append(normalize_record_dict(dict(row), default_id=f"{fp.stem}_{idx:04d}", source_file=rel, default_source=fp.stem))
                    cnt += 1
            ingest_diagnostics.append({"file": rel, "format": ext.lstrip("."), "count": cnt})
        elif ext == ".pdf":
            pages, extractor = read_pdf_pages_safe(fp)
            total_chars = sum(p["char_count"] for p in pages)
            if not pages or total_chars == 0:
                tl_status = "empty_or_scanned"
            elif total_chars < max(200, len(pages) * 80):
                tl_status = "sparse_text"
            else:
                tl_status = "full_text"
            txt = "\n\n".join(f"[Page {p['page_number']}]\n{p['text']}" for p in pages if p["text"])
            paras: list[dict[str, Any]] = []
            for p in pages:
                paras.extend(segment_text_to_paragraphs(p["text"], record_id=fp.stem, page_number=p["page_number"]))
            raw_texts.append({"file": rel, "text": txt, "page_count": len(pages), "text_layer_status": tl_status, "extractor": extractor})
            ingest_diagnostics.append({"file": rel, "format": "pdf", "pages": len(pages), "chars": total_chars, "text_layer_status": tl_status, "extractor": extractor})
            pdf_title = "unknown"
            if pages and pages[0].get("text"):
                first_lines = [ln.strip() for ln in pages[0]["text"].splitlines() if len(ln.strip()) >= 12]
                for cand_ln in first_lines[:4]:
                    if not re.match(r"^(?:arXiv:|https?://|doi:|page\s+\d|preprint\b|submitted\s+to\b|\d{4}\b)", cand_ln, re.I):
                        pdf_title = cand_ln[:220]
                        break
            records.append(normalize_record_dict({
                "title": pdf_title,
                "title_provenance": "pdf_first_page_heading" if pdf_title != "unknown" else "unknown_no_reliable_pdf_title",
                "text": txt,
                "paragraphs": paras,
                "page_count": len(pages),
                "text_layer_status": tl_status,
                "pdf_extractor": extractor,
                "fulltext_available": tl_status == "full_text",
                "ids": extract_identifiers(txt[:8000]),
            }, default_id=fp.stem, source_file=rel, default_source="pdf"))
        elif ext in (".md", ".txt"):
            txt = fp.read_text(encoding="utf-8", errors="replace")
            raw_texts.append({"file": rel, "text": txt})
            paras = segment_text_to_paragraphs(txt, record_id=fp.stem)
            if not any(fp.stem.lower().startswith(p) for p in ("readme", "notes", "prompt")):
                records.append(normalize_record_dict({
                    "title": fp.stem,
                    "text": txt,
                    "paragraphs": paras,
                    "fulltext_available": len(txt.strip()) >= 300,
                }, default_id=fp.stem, source_file=rel, default_source="text_doc"))
            ingest_diagnostics.append({"file": rel, "format": ext.lstrip("."), "chars": len(txt)})
        elif ext == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
                before = len(target)
                if isinstance(obj, list):
                    _collect_list(obj, target, rel, fp.stem)
                elif isinstance(obj, dict):
                    scielo_recs = parse_scielo_article_json(obj, rel)
                    if scielo_recs:
                        target.extend(scielo_recs)
                    elif isinstance(obj.get("message"), dict) and isinstance(obj["message"].get("items"), list):
                        _collect_list(obj["message"]["items"], target, rel, "crossref")
                    elif isinstance(obj.get("resultList"), dict) and isinstance(obj["resultList"].get("result"), list):
                        _collect_list(obj["resultList"]["result"], target, rel, "europepmc")
                    elif isinstance(obj.get("results"), list):
                        src_name = "openalex" if "openalex" in stem_low else fp.stem
                        _collect_list(obj["results"], target, rel, src_name)
                    elif isinstance(obj.get("records"), list):
                        _collect_list(obj["records"], target, rel, fp.stem)
                    elif isinstance(obj.get("papers"), list):
                        _collect_list(obj["papers"], target, rel, fp.stem)
                    elif isinstance(obj.get("patents"), list):
                        _collect_list(obj["patents"], target, rel, "patents")
                    elif obj.get("title") or obj.get("text") or obj.get("doi") or obj.get("publication_number"):
                        target.append(normalize_record_dict(obj, default_id=fp.stem, source_file=rel, default_source=fp.stem))
                ingest_diagnostics.append({"file": rel, "format": "json", "count": len(target) - before})
            except Exception as exc:
                ingest_diagnostics.append({"file": rel, "format": "json", "error": str(exc)})

    return {
        "case_id": str(case_data.get("case_id") or "case-unknown"),
        "case_data": case_data,
        "task_md": task_md,
        "records": records,
        "reference_set": reference_set,
        "supplemental_hits": supplemental_hits,
        "queries": queries,
        "raw_texts": raw_texts,
        "ingest_diagnostics": ingest_diagnostics,
    }
