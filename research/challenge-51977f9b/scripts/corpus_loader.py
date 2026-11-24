"""Unified offline multi-source research corpus loader (v3).

Zero prohibited dependencies and zero external network calls.
Key v3 fixes:
- Properly classifies EuropePMC PPR* preprints (`source == 'PPR'` / `record_id.startswith('PPR')`) as `preprint`
  without retaining `online_first_year` that could trigger silent promotion during deduplication.
- Strips HTML tags and unescapes HTML entities on BOTH `title` and `abstract`.
- Skips journal mastheads, running headers, volume/page banners, and arXiv stamps when inferring PDF titles,
  joining wrapped multi-line titles cleanly.
- Handles default XML namespaces (`xmlns=...`) in JATS XML articles so all metadata and sections parse reliably.
- Wires `block_indexer.scan_directory` to record structured block coverage of local files.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import block_indexer
from evidence_state_guard import (
    extract_identifiers,
    normalize_arxiv_id,
    normalize_doi,
    verify_quote_grounded,
)

BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,]+)\s*,(.+?)\n\s*\}", re.S)
BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|\"([^\"]*)\"|(\d+))")
NOTE_META_RE = re.compile(r"^\s*(record[-_ ]?id|title|doi|pdf(?:_file)?|theme)\s*:\s*(.*?)\s*$", re.I)
NOTE_CLAIM_HEADING_RE = re.compile(r"^\s*#{2,4}\s*claim\s*:\s*(.*?)\s*$", re.I)
NOTE_CLAIM_FIELD_RE = re.compile(r"^\s*(claim|stance|locator|quote)\s*:\s*(.*?)\s*$", re.I)
PAGE_LOCATOR_RE = re.compile(r"\b(?:page|p\.?)\s*(\d+)\b", re.I)
EXPLICIT_DATE_LINE_RE = re.compile(r"(?mi)^\s*(published|publication year|online first|preprint year|posted)\s*:\s*(\d{4})\b")
BLOCKED_ACCESS_STATUSES = {"blocked", "403", "429", "captcha", "sso", "paywall", "login_required", "unavailable", "timeout", "error"}
SEARCH_SNIPPET_TYPES = {"search_result", "search-result", "search_hit", "search-hit", "snippet", "discovery_metadata"}
PREPRINT_VENUES = ("arxiv", "biorxiv", "medrxiv", "chemrxiv", "techrxiv", "ssrn", "research square", "preprints.org", "preprint")
PDF_MASTHEAD_RE = re.compile(
    r"^(?:arxiv:|preprint|published|http|doi:|page\s+\d|vol\.|volume\s+\d|issn|received|accepted|copyright|©|"
    r"open\s+access|research\s+article|review\s+article|original\s+article|short\s+communication|letter|"
    r"briefings\s+in\b|advanced\s+functional\s+materials|nature\s+\w+|science\s+\w+|journal\s+of\b|"
    r"frontiers\s+in\b|plos\s+\w+|bmc\s+\w+|proceedings\s+of\b|ieee\s+transactions|acs\s+\w+|"
    r".*,\s*(?:19|20)\d{2},\s*(?:pp\.|vol\.|\d+[–-]\d+)|title\s*:\s*$)",
    re.I,
)


def _parse_year(val: Any) -> int | None:
    if isinstance(val, int) and 1800 <= val <= 2035:
        return val
    if isinstance(val, str):
        m = re.search(r"\b(1[89]\d{2}|20[0-3]\d)\b", val)
        if m:
            y = int(m.group(1))
            if 1800 <= y <= 2035:
                return y
    if isinstance(val, dict):
        dp = val.get("date-parts")
        if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0] and isinstance(dp[0][0], int):
            y = dp[0][0]
            if 1800 <= y <= 2035:
                return y
        dt_str = val.get("date-time")
        if isinstance(dt_str, str):
            return _parse_year(dt_str)
    return None


def reconstruct_openalex_abstract(inv_idx: Any) -> str:
    if not isinstance(inv_idx, dict) or not inv_idx:
        return ""
    pos_map: dict[int, str] = {}
    for word, positions in inv_idx.items():
        if isinstance(positions, list):
            for p in positions:
                if isinstance(p, int):
                    pos_map[p] = str(word)
    if not pos_map:
        return ""
    return " ".join(pos_map[i] for i in sorted(pos_map.keys()))


def _strip_xml_text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


def _strip_html(raw: str) -> str:
    # Unescape first so double-encoded entities like &lt;sub&gt;2&lt;/sub&gt; are cleanly stripped!
    unesc = html.unescape(raw or "")
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", unesc)
    cleaned = re.sub(r"(?s)<[^>]+>", "", cleaned)
    return " ".join(html.unescape(cleaned).split())


def _strip_xml_namespaces(root: ET.Element) -> None:
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def read_pdf_pages_safe(pdf_path: Path) -> list[str]:
    """Read PDF pages using pdfplumber -> pypdf -> pdftotext, zero prohibited dependencies."""
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as doc:
            return [(p.extract_text() or "") for p in doc.pages]
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        return [(p.extract_text() or "") for p in reader.pages]
    except Exception:
        pass
    bin_p = shutil.which("pdftotext")
    if bin_p:
        try:
            proc = subprocess.run(
                [bin_p, str(pdf_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if proc.returncode == 0:
                pages = proc.stdout.split("\f")
                if pages and not pages[-1].strip():
                    pages.pop()
                return pages
        except Exception:
            pass
    return []


def read_pdf_text_safe(pdf_path: Path) -> str:
    return "\n\n".join(read_pdf_pages_safe(pdf_path))


def _infer_date_basis(raw: dict[str, Any], *, default_source: str = "") -> tuple[int | None, str, dict[str, int | None]]:
    """Separate published, online_first, and preprint years rigorously (including EuropePMC PPR*)."""
    pub_y = (
        _parse_year(raw.get("published_year"))
        or _parse_year(raw.get("published-print"))
        or _parse_year(raw.get("published"))
    )
    online_y = (
        _parse_year(raw.get("online_first_year"))
        or _parse_year(raw.get("published-online"))
        or _parse_year(raw.get("electronicPublicationDate"))
    )
    preprint_y = _parse_year(raw.get("preprint_year")) or _parse_year(raw.get("posted"))
    issued_y = (
        _parse_year(raw.get("issued"))
        or _parse_year(raw.get("publication_year"))
        or _parse_year(raw.get("pubYear"))
        or _parse_year(raw.get("year"))
        or _parse_year(raw.get("Year"))
    )
    first_pub_y = _parse_year(raw.get("firstPublicationDate")) or _parse_year(raw.get("publication_date"))

    rtype = str(raw.get("type") or raw.get("pubType") or "").strip().lower()
    src_code = str(raw.get("source") or default_source or "").strip().upper()
    rec_id = str(raw.get("record_id") or raw.get("id") or "").strip().upper()
    venue = str(raw.get("venue") or raw.get("journal") or raw.get("journalTitle") or "").lower()
    if isinstance(raw.get("container-title"), list) and raw["container-title"]:
        venue = venue or str(raw["container-title"][0]).lower()
    doi = normalize_doi(str(raw.get("doi") or raw.get("DOI") or ""))

    is_pre = (
        rtype in {"preprint", "posted-content"}
        or "preprint" in rtype
        or src_code == "PPR"
        or rec_id.startswith("PPR")
        or doi.startswith(("10.48550/", "10.1101/", "10.21203/rs.", "10.26434/"))
        or any(pv in venue for pv in PREPRINT_VENUES)
        or (src_code == "ARXIV" and not pub_y)
    )

    declared_basis = str(raw.get("date_basis") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if is_pre:
        py = preprint_y or issued_y or first_pub_y or online_y or pub_y
        # Do NOT copy electronicPublicationDate into online_first_year for preprints!
        return py, ("preprint" if py else "unknown"), {"published_year": None, "online_first_year": None, "preprint_year": py}

    if declared_basis in {"published", "online_first", "preprint"} and (issued_y or pub_y or online_y or preprint_y):
        chosen = {"published": pub_y or issued_y, "online_first": online_y or issued_y, "preprint": preprint_y or issued_y}[declared_basis]
        if chosen:
            return chosen, declared_basis, {
                "published_year": pub_y or (chosen if declared_basis == "published" else None),
                "online_first_year": online_y or (chosen if declared_basis == "online_first" else None),
                "preprint_year": preprint_y or (chosen if declared_basis == "preprint" else None),
            }

    if pub_y:
        return pub_y, "published", {"published_year": pub_y, "online_first_year": online_y, "preprint_year": preprint_y}
    if online_y:
        return online_y, "online_first", {"published_year": None, "online_first_year": online_y, "preprint_year": preprint_y}
    if issued_y:
        return issued_y, "published", {"published_year": issued_y, "online_first_year": online_y, "preprint_year": preprint_y}
    if first_pub_y:
        return first_pub_y, "online_first", {"published_year": None, "online_first_year": first_pub_y, "preprint_year": preprint_y}
    return None, "unknown", {"published_year": None, "online_first_year": None, "preprint_year": preprint_y}


def _infer_material_access(raw: dict[str, Any], *, text_blob: str, abstract: str, default_access: str = "") -> str:
    declared_access = str(raw.get("access_status") or raw.get("material_access") or "").strip().lower()
    if declared_access in BLOCKED_ACCESS_STATUSES or str(raw.get("http_status") or "") in {"403", "429", "401"}:
        return "blocked_or_unavailable"
    if declared_access in {"fulltext_pdf", "fulltext_xml", "fulltext_local_text", "captured_text", "secondary_note", "abstract_only", "discovery_only", "blocked_or_unavailable"}:
        return declared_access
    if default_access:
        return default_access

    declared_type = str(raw.get("record_type") or raw.get("material_type") or raw.get("type") or "").strip().lower()
    has_full = bool(text_blob.strip()) and not (declared_type in SEARCH_SNIPPET_TYPES)
    has_locator = any(isinstance(raw.get(k), str) and raw[k].strip() for k in ("url", "href", "link"))
    has_explicit_full = any(isinstance(raw.get(k), str) and raw[k].strip() for k in ("raw_content", "full_text", "fulltext", "article_text", "paper_text", "text"))
    body_only_snippet = has_locator and isinstance(raw.get("body"), str) and bool(raw["body"].strip()) and not has_explicit_full
    if declared_type in SEARCH_SNIPPET_TYPES or body_only_snippet or (raw.get("snippet") and not has_explicit_full and not abstract.strip()):
        return "discovery_only"
    if has_full:
        return "fulltext_local_text"
    if abstract.strip():
        return "abstract_only"
    return "discovery_only"


def _normalize_source_label(raw_src: str, default_source: str) -> str:
    s = (raw_src or default_source or "local_json").strip()
    low = s.lower()
    if low == "ppr":
        return "europepmc_preprint"
    if low in {"med", "pmc", "cba", "agr", "ctx", "hir", "eth"}:
        return "europepmc"
    if low == "crossref":
        return "crossref"
    return low


def normalize_record_dict(
    raw: dict[str, Any],
    *,
    default_id: str,
    source_file: str,
    default_source: str = "local_json",
    default_access: str = "",
) -> dict[str, Any]:
    title = raw.get("title") or raw.get("articleTitle") or raw.get("display_name") or ""
    if isinstance(title, list):
        title = title[0] if title else ""
    title_str = _strip_html(str(title))

    authors: list[str] = []
    if isinstance(raw.get("authorships"), list):
        for a in raw["authorships"]:
            if isinstance(a, dict):
                nm = (a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
                if nm:
                    authors.append(str(nm))
    elif isinstance(raw.get("authorList"), dict) and isinstance(raw["authorList"].get("author"), list):
        for a in raw["authorList"]["author"]:
            if isinstance(a, dict):
                nm = a.get("fullName") or f"{a.get('firstName', '')} {a.get('lastName', '')}".strip()
                if nm:
                    authors.append(nm)
    elif isinstance(raw.get("authors") or raw.get("author"), list):
        for a in (raw.get("authors") or raw.get("author")):
            if isinstance(a, dict):
                nm = a.get("name") or f"{a.get('given', '')} {a.get('family', '')}".strip() or str(a)
                if nm:
                    authors.append(nm)
            elif a:
                authors.append(str(a))
    elif isinstance(raw.get("authorString") or raw.get("authors") or raw.get("author"), str):
        astr = str(raw.get("authorString") or raw.get("authors") or raw.get("author"))
        authors = [x.strip() for x in re.split(r"[;,]", astr) if x.strip()]

    abstract = str(raw.get("abstract") or raw.get("abstractText") or raw.get("summary") or "")
    if not abstract.strip() and isinstance(raw.get("abstract_inverted_index"), dict):
        abstract = reconstruct_openalex_abstract(raw["abstract_inverted_index"])
    abstract = _strip_html(abstract)

    has_locator = any(isinstance(raw.get(k), str) and raw[k].strip() for k in ("url", "href", "link"))
    explicit_ft = raw.get("raw_content") or raw.get("full_text") or raw.get("fulltext") or raw.get("article_text") or raw.get("paper_text") or raw.get("text")
    text_blob = str(explicit_ft or ("" if has_locator else (raw.get("body") or "")))

    doi = normalize_doi(str(raw.get("doi") or raw.get("DOI") or ""))
    arxiv_id = normalize_arxiv_id(str(raw.get("arxiv_id") or raw.get("arXiv") or ""))
    pmid = str(raw.get("pmid") or raw.get("PMID") or "").strip()
    pmcid = str(raw.get("pmcid") or raw.get("PMCID") or "").strip().upper()
    # Explicit metadata dict fields (e.g. OpenAlex ids / Semantic Scholar externalIds) only
    if isinstance(raw.get("ids"), dict):
        if not doi and raw["ids"].get("doi"):
            doi = normalize_doi(str(raw["ids"]["doi"]))
        if not pmid and raw["ids"].get("pmid"):
            pmid = re.sub(r"\D+", "", str(raw["ids"]["pmid"]))
        if not pmcid and raw["ids"].get("pmcid"):
            pmcid = str(raw["ids"]["pmcid"]).strip().upper()
    # Front-matter header ONLY (strictly before Abstract/Introduction/References) for raw local PDF/text
    header_zone = ""
    if default_source in {"local_pdf", "local_text"} and text_blob:
        cut_m = re.search(r"\b(?:abstract|introduction|background|references|bibliography)\b", text_blob[:2200], re.I)
        header_zone = text_blob[: cut_m.start() if cut_m else 900]
    if not doi or not arxiv_id or not pmcid:
        header_ids = extract_identifiers(f"{raw.get('url') or ''} {title_str} {header_zone}")
        if not doi and header_ids["doi"]:
            doi = header_ids["doi"][0]
        if not arxiv_id and header_ids["arxiv"]:
            arxiv_id = header_ids["arxiv"][0]
        if not pmcid and header_ids["pmc"]:
            pmcid = header_ids["pmc"][0]
    # Body/abstract IDs are NEVER promoted to the current paper's identity; track them separately as cited_candidate_ids!
    body_ids = extract_identifiers(f"{abstract} {text_blob[:12000]}")
    cited_candidate_ids = {
        "doi": [x for x in body_ids["doi"] if x != doi],
        "arxiv": [x for x in body_ids["arxiv"] if x != arxiv_id],
        "pmid": [x for x in body_ids["pmid"] if x != pmid],
        "pmc": [x for x in body_ids["pmc"] if x != pmcid],
    }

    venue_raw = raw.get("venue") or raw.get("journal") or raw.get("journalTitle") or raw.get("container-title") or ""
    if not venue_raw and isinstance(raw.get("primary_location"), dict):
        src_obj = raw["primary_location"].get("source") or {}
        if isinstance(src_obj, dict):
            venue_raw = src_obj.get("display_name") or ""
    if isinstance(venue_raw, list):
        venue_raw = venue_raw[0] if venue_raw else ""

    year_val, date_basis, date_parts = _infer_date_basis(raw, default_source=default_source)
    citations_raw = raw.get("citations") or raw.get("cited_by_count") or raw.get("citedByCount") or raw.get("is-referenced-by-count") or 0
    try:
        citations = int(citations_raw)
    except Exception:
        citations = 0

    material_access = _infer_material_access(raw, text_blob=text_blob, abstract=abstract, default_access=default_access)

    concepts: list[str] = []
    for cfield in ("concepts", "topics", "keywords", "subject"):
        val = raw.get(cfield)
        if isinstance(val, list):
            for it in val[:10]:
                if isinstance(it, dict) and it.get("display_name"):
                    concepts.append(str(it["display_name"]))
                elif isinstance(it, str) and it.strip():
                    concepts.append(it.strip())

    raw_src = str(raw.get("source") or raw.get("database") or default_source)
    norm_source = _normalize_source_label(raw_src, default_source)

    rec = {
        "record_id": str(raw.get("record_id") or raw.get("paper_id") or raw.get("work_id") or raw.get("id") or default_id),
        "title": title_str,
        "authors": authors,
        "year": year_val,
        "date_basis": date_basis,
        "published_year": date_parts["published_year"],
        "online_first_year": date_parts["online_first_year"],
        "preprint_year": date_parts["preprint_year"],
        "doi": doi,
        "arxiv_id": arxiv_id,
        "pmid": pmid,
        "pmcid": pmcid,
        "cited_candidate_ids": cited_candidate_ids,
        "identity_source": "explicit_metadata_or_header_only",
        "type": str(raw.get("type") or raw.get("pubType") or ("preprint" if date_basis == "preprint" else "")),
        "venue": _strip_html(str(venue_raw)),
        "citations": citations,
        "concepts": sorted(set(concepts)),
        "abstract": abstract.strip(),
        "text": text_blob.strip(),
        "material_access": material_access,
        "access_reason": str(raw.get("access_reason") or raw.get("error") or ""),
        "source": norm_source,
        "source_file": source_file,
        "language": str(raw.get("language") or "en").lower(),
        "theme": str(raw.get("theme") or raw.get("topic") or "").strip(),
        "pdf_file": str(raw.get("pdf_file") or "").strip() or None,
        "note_file": str(raw.get("note_file") or "").strip() or None,
        "claim_spans": raw.get("claim_spans") or [],
        "passages": raw.get("passages") or [],
    }
    if not rec["passages"]:
        if rec["text"] and material_access != "discovery_only":
            rec["passages"] = [{"locator": f"{source_file}#body", "text": rec["text"][:16000]}]
        elif rec["abstract"]:
            rec["passages"] = [{"locator": f"{source_file}#abstract", "text": rec["abstract"]}]
    return rec


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
        records.append(normalize_record_dict(
            {
                "record_id": citekey,
                "type": etype.lower(),
                "title": fields.get("title", ""),
                "authors": authors,
                "year": yr,
                "date_basis": "published" if yr else "unknown",
                "doi": fields.get("doi", ""),
                "venue": fields.get("journal") or fields.get("booktitle") or "",
                "abstract": fields.get("abstract", ""),
            },
            default_id=citekey,
            source_file=source_file,
            default_source="bibtex",
        ))
    return records


def parse_arxiv_atom_xml(raw: str, source_file: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    for idx, entry in enumerate(root.findall("atom:entry", ns), 1):
        eid = _strip_xml_text(entry.find("atom:id", ns))
        arxiv_id = normalize_arxiv_id(eid)
        title = _strip_xml_text(entry.find("atom:title", ns))
        summary = _strip_xml_text(entry.find("atom:summary", ns))
        published = _strip_xml_text(entry.find("atom:published", ns))
        authors = [_strip_xml_text(a.find("atom:name", ns)) for a in entry.findall("atom:author", ns)]
        authors = [a for a in authors if a]
        doi = normalize_doi(_strip_xml_text(entry.find("arxiv:doi", ns)))
        cats = [c.attrib.get("term", "") for c in entry.findall("atom:category", ns) if c.attrib.get("term")]
        py = _parse_year(published)
        records.append(normalize_record_dict(
            {
                "record_id": f"arxiv:{arxiv_id}" if arxiv_id else f"{Path(source_file).stem}_{idx:03d}",
                "title": title,
                "authors": authors,
                "preprint_year": py,
                "year": py,
                "date_basis": "preprint" if py else "unknown",
                "doi": doi,
                "arxiv_id": arxiv_id,
                "type": "preprint",
                "venue": "arXiv",
                "abstract": summary,
                "concepts": cats,
            },
            default_id=f"arxiv_{idx:03d}",
            source_file=source_file,
            default_source="arxiv",
            default_access="abstract_only" if summary else "discovery_only",
        ))
    return records


def parse_jats_xml_article(raw: str, source_file: str) -> dict[str, Any] | None:
    """Parse JATS XML fulltext (namespace-safe) into section-indexed passages."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    _strip_xml_namespaces(root)
    if root.tag.lower() != "article":
        return None

    title = _strip_xml_text(root.find(".//article-meta/title-group/article-title"))
    abstract = _strip_xml_text(root.find(".//article-meta/abstract"))
    doi = ""
    pmid = ""
    pmcid = ""
    for aid in root.findall(".//article-meta/article-id"):
        id_type = (aid.attrib.get("pub-id-type") or "").lower()
        val = _strip_xml_text(aid)
        if id_type == "doi" and val:
            doi = normalize_doi(val)
        elif id_type == "pmid" and val:
            pmid = val
        elif id_type in {"pmc", "pmcid"} and val:
            pmcid = val.upper() if val.upper().startswith("PMC") else f"PMC{val}"

    authors: list[str] = []
    for contrib in root.findall(".//article-meta/contrib-group/contrib[@contrib-type='author']"):
        sn = _strip_xml_text(contrib.find(".//surname"))
        gn = _strip_xml_text(contrib.find(".//given-names"))
        nm = f"{gn} {sn}".strip()
        if nm:
            authors.append(nm)

    pub_y = None
    online_y = None
    for pd in root.findall(".//article-meta/pub-date"):
        ptype = (pd.attrib.get("pub-type") or pd.attrib.get("date-type") or "").lower()
        yr = _parse_year(_strip_xml_text(pd.find("year")))
        if not yr:
            continue
        if ptype in {"ppub", "collection", "pub"} and not pub_y:
            pub_y = yr
        elif ptype in {"epub", "electronic"} and not online_y:
            online_y = yr
        elif not pub_y and not online_y:
            pub_y = yr

    journal = _strip_xml_text(root.find(".//journal-meta/journal-title-group/journal-title")) or _strip_xml_text(root.find(".//journal-meta/journal-id"))

    passages: list[dict[str, str]] = []
    body_chunks: list[str] = []
    if abstract:
        passages.append({"locator": f"{source_file}#abstract", "text": abstract})
    body = root.find(".//body")
    if body is not None:
        secs = body.findall("./sec")
        if secs:
            for s_idx, sec in enumerate(secs, 1):
                stitle = _strip_xml_text(sec.find("title")) or f"Section_{s_idx}"
                stext = _strip_xml_text(sec)
                if stext:
                    passages.append({"locator": f"{source_file}#sec={stitle[:40]}", "text": stext[:8000]})
                    body_chunks.append(f"[{stitle}]\n{stext}")
        else:
            btxt = _strip_xml_text(body)
            if btxt:
                passages.append({"locator": f"{source_file}#body", "text": btxt[:16000]})
                body_chunks.append(btxt)

    full_text = "\n\n".join(body_chunks)
    rec_id = pmcid or pmid or (f"doi:{doi}" if doi else Path(source_file).stem)
    return normalize_record_dict(
        {
            "record_id": rec_id,
            "title": title or Path(source_file).stem,
            "authors": authors,
            "published_year": pub_y,
            "online_first_year": online_y,
            "year": pub_y or online_y,
            "date_basis": "published" if pub_y else ("online_first" if online_y else "unknown"),
            "doi": doi,
            "pmid": pmid,
            "pmcid": pmcid,
            "type": root.attrib.get("article-type", "journal-article"),
            "venue": journal,
            "abstract": abstract,
            "text": full_text,
            "passages": passages,
        },
        default_id=Path(source_file).stem,
        source_file=source_file,
        default_source="europepmc_xml",
        default_access="fulltext_xml" if full_text.strip() else ("abstract_only" if abstract.strip() else "discovery_only"),
    )


def parse_markdown_review_note(raw: str, source_file: str) -> dict[str, Any] | None:
    """Read explicitly marked literature notes with claim spans and PDF locators."""
    lines = raw.splitlines()
    metadata: dict[str, str] = {}
    claim_spans: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    active_start = 0
    saw_claim_heading = False

    def finish_claim(end_line: int) -> None:
        nonlocal active
        if active is not None:
            active["note_span"] = {"start_line": active_start, "end_line": max(active_start, end_line)}
            claim_spans.append(active)
            active = None

    for line_no, line in enumerate(lines, start=1):
        heading = NOTE_CLAIM_HEADING_RE.match(line)
        if heading:
            finish_claim(line_no - 1)
            active = {"claim_key": heading.group(1).strip()}
            active_start = line_no
            saw_claim_heading = True
            continue
        if active is not None:
            field = NOTE_CLAIM_FIELD_RE.match(line)
            if field:
                active[field.group(1).lower().replace(" ", "_")] = field.group(2).strip().strip('"')
            continue
        if not saw_claim_heading:
            meta = NOTE_META_RE.match(line)
            if meta:
                key = re.sub(r"[-_ ]", "", meta.group(1).lower())
                if key == "recordid":
                    key = "record_id"
                elif key in {"pdf", "pdffile"}:
                    key = "pdf_file"
                metadata[key] = meta.group(2).strip()

    finish_claim(len(lines))
    if not claim_spans or not (metadata.get("record_id") or metadata.get("doi") or metadata.get("pdf_file")):
        return None

    title = metadata.get("title") or next(
        (line.lstrip("# ").strip() for line in lines if line.startswith("# ")),
        Path(source_file).stem,
    )
    rec = normalize_record_dict(
        {
            "record_id": metadata.get("record_id") or Path(source_file).stem,
            "title": title,
            "doi": metadata.get("doi", ""),
            "theme": metadata.get("theme", ""),
            "pdf_file": metadata.get("pdf_file", ""),
            "note_file": source_file,
            "claim_spans": claim_spans,
            "text": raw,
            "access_status": "secondary_note",
        },
        default_id=Path(source_file).stem,
        source_file=source_file,
        default_source="markdown_note",
        default_access="secondary_note",
    )
    rec["secondary_note_requires_primary_verification"] = True
    rec["primary_verified_excerpts"] = []
    return rec


def _extract_pdf_header_metadata(pages: list[str], rel: str, stem: str) -> dict[str, Any]:
    """Infer clean PDF title by skipping journal mastheads/running headers and joining wrapped title lines."""
    page1 = pages[0] if pages else ""
    ids = extract_identifiers(f"{rel} {page1[:4000]}")
    arxiv_id = normalize_arxiv_id(stem) or (ids["arxiv"][0] if ids["arxiv"] else "")
    doi = ids["doi"][0] if ids["doi"] else ""

    raw_lines = [ln.strip() for ln in page1.splitlines()[:28] if ln.strip()]
    title_parts: list[str] = []
    for ln in raw_lines:
        clean_ln = re.sub(r"^title\s*:\s*", "", ln, flags=re.I).strip()
        if len(clean_ln) < 12 or PDF_MASTHEAD_RE.match(clean_ln):
            if title_parts:
                break
            continue
        if re.search(r"@|department\s+of|university|institute|abstract\b", clean_ln, re.I):
            if title_parts:
                break
            continue
        # Stop if line looks like comma-separated author list after title started
        if title_parts and (clean_ln.count(",") >= 2 or re.search(r"\d\s*,", clean_ln)):
            break
        title_parts.append(clean_ln)
        if len(" ".join(title_parts)) >= 95 or clean_ln.endswith((".", "?", "!")) or len(title_parts) >= 2:
            break

    title = " ".join(title_parts)[:220] if title_parts else stem
    # Normalize spaced-out PDF small-caps like "U SING INFRARED"
    title = re.sub(r"\b([A-Z])\s+([A-Z]{3,})\b", r"\1\2", title)

    preprint_year = None
    pub_year = None
    date_basis = "unknown"
    if arxiv_id and re.match(r"^\d{4}\.", arxiv_id):
        yy = int(arxiv_id[:2])
        preprint_year = 2000 + yy if yy <= 35 else 1900 + yy
        date_basis = "preprint"
    else:
        m = EXPLICIT_DATE_LINE_RE.search(page1[:2000])
        if m:
            y = int(m.group(2))
            lbl = m.group(1).lower()
            if "preprint" in lbl or "posted" in lbl:
                preprint_year, date_basis = y, "preprint"
            elif "online" in lbl:
                pub_year, date_basis = y, "online_first"
            else:
                pub_year, date_basis = y, "published"

    abstract = ""
    am = re.search(
        r"(?is)\babstract\b[:.\s\n]*(.{80,1800}?)(?:\n\s*(?:1\.?\s+introduction|keywords|index\s+terms|\n\n))",
        page1 + "\n" + (pages[1] if len(pages) > 1 else ""),
    )
    if am:
        abstract = " ".join(am.group(1).split())

    return {
        "record_id": f"arxiv:{arxiv_id}" if arxiv_id else stem,
        "title": title,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "year": pub_year or preprint_year,
        "published_year": pub_year if date_basis == "published" else None,
        "online_first_year": pub_year if date_basis == "online_first" else None,
        "preprint_year": preprint_year,
        "date_basis": date_basis,
        "type": "preprint" if arxiv_id and not pub_year else "pdf_document",
        "venue": "arXiv" if arxiv_id else "",
        "abstract": abstract,
    }


def load_input_bundle(input_dir: Path, *, work_dir: Path | None = None) -> dict[str, Any]:
    """Load case.json, task.md, block index, and all finite local multi-source literature artifacts."""
    case_path = input_dir / "case.json"
    case_data: dict[str, Any] = {}
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))
    task_md = ""
    if (input_dir / "task.md").is_file():
        task_md = (input_dir / "task.md").read_text(encoding="utf-8", errors="replace")

    # Actively wire block_indexer for structured local document/table block coverage
    block_index_summary: dict[str, Any] = {}
    try:
        idx_obj = block_indexer.index_path(input_dir)
        blocks_list = idx_obj.get("blocks", [])
        indexed_files = {str(b.get("file") or "") for b in blocks_list if b.get("file")}
        block_index_summary = {
            "files_indexed": len(indexed_files),
            "blocks_indexed": int(idx_obj.get("total_blocks", len(blocks_list))),
            "total_est_tokens": int(idx_obj.get("total_est_tokens", 0)),
        }
        if work_dir is not None:
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "input_blocks.json").write_text(json.dumps(idx_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        block_index_summary = {"files_indexed": 0, "blocks_indexed": 0, "total_est_tokens": 0}

    records: list[dict[str, Any]] = []
    reference_set: list[dict[str, Any]] = []
    queries: list[Any] = list(case_data.get("queries") or case_data.get("questions") or [])
    raw_texts: list[dict[str, str]] = []
    ingest_gaps: list[dict[str, Any]] = []
    access_log: list[dict[str, Any]] = []
    source_inventory: list[dict[str, Any]] = []
    pagination_warnings: list[dict[str, Any]] = []
    pdf_text_by_file: dict[str, str] = {}
    pdf_pages_by_file: dict[str, list[str]] = {}

    def _collect_list(items: Any, target: list[dict[str, Any]], src: str, src_name: str) -> None:
        if isinstance(items, list):
            for idx, it in enumerate(items, start=1):
                if isinstance(it, dict):
                    rec = normalize_record_dict(it, default_id=f"{src}_{idx:04d}", source_file=src, default_source=src_name)
                    target.append(rec)
                    if rec["material_access"] == "blocked_or_unavailable":
                        access_log.append({
                            "source_file": src,
                            "record_id": rec["record_id"],
                            "status": "blocked_or_unavailable",
                            "reason": rec.get("access_reason") or "Access barrier declared in record metadata",
                            "action": "recorded_and_stopped_without_bypass",
                        })
                elif isinstance(it, str) and it.strip():
                    target.append(normalize_record_dict({"title": it.strip()}, default_id=f"{src}_{idx:04d}", source_file=src, default_source=src_name))

    for key in ("records", "papers", "documents", "candidates", "results", "sources", "patents", "previous_snapshot", "current_snapshot"):
        if key in case_data:
            _collect_list(case_data[key], records, "case.json", str(case_data.get("source") or key))
    if "reference_set" in case_data:
        _collect_list(case_data["reference_set"], reference_set, "case.json:reference_set", "reference_set")
    if isinstance(case_data.get("blocked_sources"), list):
        for bs in case_data["blocked_sources"]:
            if isinstance(bs, dict):
                access_log.append({
                    "source_file": "case.json",
                    "record_id": str(bs.get("source_id") or bs.get("url") or bs.get("database") or "blocked_src"),
                    "status": "blocked_or_unavailable",
                    "reason": str(bs.get("reason") or bs.get("http_status") or "access_blocked"),
                    "action": "recorded_and_stopped_without_bypass",
                })

    for fp in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        if fp.name.lower() in ("case.json", "task.md", "readme.md"):
            continue
        rel = fp.relative_to(input_dir).as_posix()
        ext = fp.suffix.lower()
        sha = hashlib.sha256(fp.read_bytes()).hexdigest()
        before_n = len(records)

        if ext == ".bib":
            records.extend(parse_bibtex_records(fp.read_text(encoding="utf-8", errors="replace"), rel))
        elif ext in (".csv", ".tsv"):
            delim = "\t" if ext == ".tsv" else ","
            with fp.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                for idx, row in enumerate(csv.DictReader(fh, delimiter=delim), start=1):
                    target = reference_set if "reference" in fp.stem.lower() else records
                    target.append(normalize_record_dict(dict(row), default_id=f"{fp.stem}_{idx:04d}", source_file=rel, default_source=fp.stem))
        elif ext == ".pdf":
            pages = read_pdf_pages_safe(fp)
            txt = "\n\n".join(pages)
            raw_texts.append({"file": rel, "text": txt})
            pdf_text_by_file[rel] = txt
            pdf_pages_by_file[rel] = pages
            low_pages = [i + 1 for i, p in enumerate(pages) if len(p.strip()) < 30]
            if not pages or not txt.strip():
                ingest_gaps.append({"code": "PDF_PARSE_EMPTY", "severity": "warning", "source": rel, "message": "PDF yielded no extractable text layer; needs OCR or manual inspection."})
                access_log.append({"source_file": rel, "record_id": fp.stem, "status": "blocked_or_unavailable", "reason": "no_extractable_pdf_text", "action": "flagged_in_gaps"})
            else:
                if low_pages:
                    ingest_gaps.append({"code": "PDF_LOW_TEXT_PAGE", "severity": "info", "source": f"{rel}#pages={low_pages[:10]}", "message": f"{len(low_pages)} page(s) have <30 extracted chars."})
                meta = _extract_pdf_header_metadata(pages, rel, fp.stem)
                passages = [{"locator": f"{rel}#page={pno}", "text": pt[:6000]} for pno, pt in enumerate(pages, 1) if pt.strip()]
                meta["text"] = txt
                meta["passages"] = passages
                meta["pdf_file"] = rel
                records.append(normalize_record_dict(meta, default_id=fp.stem, source_file=rel, default_source="pdf", default_access="fulltext_pdf"))
        elif ext == ".xml":
            raw = fp.read_text(encoding="utf-8", errors="replace")
            raw_texts.append({"file": rel, "text": raw[:8000]})
            if "<feed" in raw[:600] and "arxiv" in raw[:1200].lower():
                records.extend(parse_arxiv_atom_xml(raw, rel))
            else:
                jats = parse_jats_xml_article(raw, rel)
                if jats:
                    records.append(jats)
                else:
                    ingest_gaps.append({"code": "XML_UNRECOGNIZED_SCHEMA", "severity": "info", "source": rel, "message": "XML file was neither arXiv Atom feed nor JATS article."})
        elif ext in (".md", ".txt", ".tex", ".html", ".htm"):
            raw = fp.read_text(encoding="utf-8", errors="replace")
            txt = _strip_html(raw) if ext in (".html", ".htm") else raw
            raw_texts.append({"file": rel, "text": txt[:12000]})
            note = parse_markdown_review_note(raw, rel) if ext == ".md" else None
            if note:
                records.append(note)
            else:
                m_date = EXPLICIT_DATE_LINE_RE.search(txt[:2000])
                yr = int(m_date.group(2)) if m_date else None
                lbl = m_date.group(1).lower() if m_date else ""
                db = "preprint" if ("preprint" in lbl or "posted" in lbl) else ("online_first" if "online" in lbl else ("published" if yr else "unknown"))
                first_h = next((ln.lstrip("# ").strip() for ln in txt.splitlines() if ln.strip().startswith("# ")), fp.stem)
                records.append(normalize_record_dict(
                    {
                        "record_id": fp.stem,
                        "title": first_h[:200],
                        "year": yr,
                        "date_basis": db,
                        "text": txt,
                        "passages": [{"locator": f"{rel}#L1", "text": txt[:12000]}],
                    },
                    default_id=fp.stem,
                    source_file=rel,
                    default_source=ext.lstrip("."),
                    default_access="fulltext_local_text" if txt.strip() else "blocked_or_unavailable",
                ))
        elif ext == ".json":
            try:
                obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
                target = reference_set if "reference" in fp.stem.lower() else records
                if isinstance(obj, list):
                    _collect_list(obj, target, rel, fp.stem)
                elif isinstance(obj, dict):
                    if isinstance(obj.get("results"), list) and isinstance(obj.get("meta"), dict):
                        _collect_list(obj["results"], target, rel, "openalex")
                        total_count = obj["meta"].get("count")
                        if isinstance(total_count, int) and total_count > len(obj["results"]):
                            pagination_warnings.append({
                                "source_file": rel,
                                "provider": "openalex",
                                "total_hits": total_count,
                                "loaded_records": len(obj["results"]),
                                "message": f"OpenAlex snapshot contains {len(obj['results'])} of {total_count} reported hits (paginated slice).",
                            })
                    elif isinstance(obj.get("message"), dict):
                        msg = obj["message"]
                        if isinstance(msg.get("items"), list):
                            _collect_list(msg["items"], target, rel, "crossref")
                            total_res = msg.get("total-results")
                            if isinstance(total_res, int) and total_res > len(msg["items"]):
                                pagination_warnings.append({
                                    "source_file": rel,
                                    "provider": "crossref",
                                    "total_hits": total_res,
                                    "loaded_records": len(msg["items"]),
                                    "message": f"Crossref snapshot contains {len(msg['items'])} of {total_res} reported hits.",
                                })
                        elif msg.get("DOI") or msg.get("title"):
                            target.append(normalize_record_dict(msg, default_id=fp.stem, source_file=rel, default_source="crossref"))
                    elif isinstance(obj.get("resultList"), dict) and isinstance(obj["resultList"].get("result"), list):
                        ep_items = obj["resultList"]["result"]
                        _collect_list(ep_items, target, rel, "europepmc")
                        hit_count = obj.get("hitCount")
                        if isinstance(hit_count, int) and hit_count > len(ep_items):
                            pagination_warnings.append({
                                "source_file": rel,
                                "provider": "europepmc",
                                "total_hits": hit_count,
                                "loaded_records": len(ep_items),
                                "next_cursor_mark": obj.get("nextCursorMark"),
                                "message": f"EuropePMC snapshot contains {len(ep_items)} of {hit_count} reported hits (cursor truncated).",
                            })
                    elif isinstance(obj.get("results"), list):
                        _collect_list(obj["results"], target, rel, fp.stem)
                    elif isinstance(obj.get("search_results"), list):
                        _collect_list(obj["search_results"], target, rel, "search_results")
                    elif isinstance(obj.get("items"), list):
                        _collect_list(obj["items"], target, rel, fp.stem)
                    elif isinstance(obj.get("records"), list):
                        _collect_list(obj["records"], target, rel, fp.stem)
                    elif isinstance(obj.get("papers"), list):
                        _collect_list(obj["papers"], target, rel, fp.stem)
                    elif obj.get("title") or obj.get("text") or obj.get("doi") or obj.get("DOI") or obj.get("access_status"):
                        rec = normalize_record_dict(obj, default_id=fp.stem, source_file=rel, default_source=fp.stem)
                        target.append(rec)
                        if rec["material_access"] == "blocked_or_unavailable":
                            access_log.append({
                                "source_file": rel,
                                "record_id": rec["record_id"],
                                "status": "blocked_or_unavailable",
                                "reason": rec.get("access_reason") or "Access barrier declared",
                                "action": "recorded_and_stopped_without_bypass",
                            })
            except Exception as exc:
                ingest_gaps.append({"code": "JSON_PARSE_ERROR", "severity": "warning", "source": rel, "message": str(exc)})
        else:
            ingest_gaps.append({"code": "UNSUPPORTED_FORMAT", "severity": "info", "source": rel, "message": f"Skipped unsupported suffix {ext}"})
            continue

        added_n = len(records) - before_n
        source_inventory.append({
            "file": rel,
            "ext": ext,
            "sha256": sha[:16],
            "records_extracted": added_n,
        })

    # Resolve note-first PDF links and verify exact page quotes
    input_root = input_dir.resolve()
    for rec in records:
        pdf_file = str(rec.get("pdf_file") or "").replace("\\", "/").strip()
        rec["pdf_file"] = pdf_file or None
        rec["note_file"] = str(rec.get("note_file") or "") or None
        rec["pdf_text"] = ""
        rec["pdf_link_status"] = "not_linked"
        if pdf_file:
            target = (input_dir / Path(pdf_file)).resolve()
            if not target.is_relative_to(input_root):
                rec["pdf_link_status"] = "outside_input"
            elif not target.is_file():
                rec["pdf_link_status"] = "missing"
            else:
                normalized_pdf = Path(pdf_file).as_posix()
                rec["pdf_text"] = pdf_text_by_file.get(normalized_pdf, "")
                rec["pdf_link_status"] = "available" if rec["pdf_text"].strip() else "text_unavailable"
                pages = pdf_pages_by_file.get(normalized_pdf, [])
                for span in rec.get("claim_spans") or []:
                    locator_match = PAGE_LOCATOR_RE.search(str(span.get("locator") or span.get("pdf_locator") or ""))
                    if not locator_match:
                        span["pdf_locator_status"] = "page_unparseable"
                        span["quote_grounded_at_locator"] = False
                        continue
                    page_number = int(locator_match.group(1))
                    span["pdf_page_number"] = page_number
                    if not 1 <= page_number <= len(pages):
                        span["pdf_locator_status"] = "page_not_found"
                        span["quote_grounded_at_locator"] = False
                        continue
                    span["quote_grounded_at_locator"] = verify_quote_grounded(str(span.get("quote") or ""), pages[page_number - 1])
                    span["pdf_locator_status"] = "page_quote_verified" if span["quote_grounded_at_locator"] else "quote_not_on_page"

    disc_count = sum(1 for r in records if r.get("material_access") == "discovery_only")
    if disc_count:
        ingest_gaps.append({
            "code": "DISCOVERY_ONLY_RECORDS_PRESENT",
            "severity": "info",
            "source": "corpus_loader",
            "message": f"{disc_count} record(s) contain discovery/metadata snippets only (no abstract or fulltext); excluded from direct claim support.",
        })
    for pw in pagination_warnings:
        ingest_gaps.append({
            "code": "API_PAGINATION_TRUNCATED",
            "severity": "info",
            "source": pw["source_file"],
            "message": pw["message"],
        })

    return {
        "case_id": str(case_data.get("case_id") or input_dir.name or "case-unknown"),
        "case_data": case_data,
        "task_md": task_md,
        "records": records,
        "reference_set": reference_set,
        "queries": queries,
        "raw_texts": raw_texts,
        "ingest_gaps": ingest_gaps,
        "access_log": access_log,
        "source_inventory": source_inventory,
        "pagination_warnings": pagination_warnings,
        "block_index_summary": block_index_summary,
    }
