#!/usr/bin/env python3
"""Offline citation lifecycle manager: OOXML live-field audit, bidirectional renumbering,
multi-format dedup (.bib/.ris/CSL-JSON), GB/T 7714-2015 & APA 7 formatting, and two-layer verification.

Zero third-party dependencies (Python 3.12+ stdlib). Integrates `writing_io_and_invariants.py`.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from writing_io_and_invariants import (  # noqa: E402
    build_writing_result,
    inspect_docx_ooxml,
    load_case_contract,
    read_document_text,
)

# ---------------------------------------------------------------------------
# 1. Normalization & Regex Helpers
# ---------------------------------------------------------------------------

RE_BRACKET_GROUP = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
RE_LATEX_CITE = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
RE_AUTHOR_YEAR_GROUP = re.compile(
    r"\(([A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?,\s*(?:19|20)\d{2}[a-z]?"
    r"(?:;\s*[A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?,\s*(?:19|20)\d{2}[a-z]?)*)\)"
)
RE_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
RE_PREPRINT = re.compile(r"\b(?:arxiv|biorxiv|medrxiv|chemrxiv|ssrn|research\s*square|preprint)\b", re.I)
RE_RETRACT = re.compile(r"\b(?:retracted|retraction|withdrawn)\b|撤稿", re.I)
RE_EOC = re.compile(r"\b(?:expression\s+of\s+concern|editorial\s+note\s+of\s+concern)\b|关注声明", re.I)
RE_CJK = re.compile(r"[\u4e00-\u9fff]")


def normalize_doi(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = RE_DOI.search(s)
    return m.group(1).rstrip(".,;)").lower() if m else s.rstrip(".,;)").lower()


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "").lower()
    t = re.sub(r"[\{\}]", "", t)
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parse_author_name(raw_name: str) -> dict[str, str]:
    s = raw_name.strip().strip("{}")
    if not s:
        return {"family": "", "given": "", "raw": ""}
    if RE_CJK.search(s):
        return {"family": s, "given": "", "raw": s}
    if "," in s:
        fam, giv = [p.strip() for p in s.split(",", 1)]
        return {"family": fam, "given": giv, "raw": s}
    parts = s.split()
    if len(parts) == 1:
        return {"family": parts[0], "given": "", "raw": s}
    return {"family": parts[-1], "given": " ".join(parts[:-1]), "raw": s}


def _author_surname_set(authors: list[dict[str, str]]) -> set[str]:
    return {a["family"].lower() for a in authors if a.get("family")}


def _is_cjk_record(rec: dict[str, Any]) -> bool:
    return bool(RE_CJK.search(rec.get("title", "")))


# ---------------------------------------------------------------------------
# 2. Multi-Format Bibliography Parsers (.bib / .ris / CSL-JSON)
# ---------------------------------------------------------------------------

def parse_bibtex(text: str, source_file: str) -> list[dict[str, Any]]:
    """Balanced-brace BibTeX parser using pure stdlib."""
    records: list[dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        at = text.find("@", i)
        if at == -1:
            break
        m_head = re.match(r"@([A-Za-z]+)\s*([\{\(])", text[at:])
        if not m_head:
            i = at + 1
            continue
        entry_type = m_head.group(1).lower()
        open_ch = m_head.group(2)
        close_ch = "}" if open_ch == "{" else ")"
        pos = at + m_head.end()
        depth = 1
        start_body = pos
        while pos < n and depth > 0:
            ch = text[pos]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
            pos += 1
        body = text[start_body:pos - 1].strip()
        i = pos
        if entry_type in {"comment", "preamble", "string"}:
            continue
        comma = body.find(",")
        if comma == -1:
            continue
        cite_key = body[:comma].strip()
        rest = body[comma + 1:].strip()

        fields: dict[str, str] = {}
        f_pos = 0
        rn = len(rest)
        while f_pos < rn:
            m_k = re.match(r"\s*,?\s*([A-Za-z0-9_\-]+)\s*=\s*", rest[f_pos:])
            if not m_k:
                break
            fkey = m_k.group(1).lower()
            f_pos += m_k.end()
            if f_pos >= rn:
                break
            ch = rest[f_pos]
            if ch == "{":
                d = 1
                f_pos += 1
                v_start = f_pos
                while f_pos < rn and d > 0:
                    if rest[f_pos] == "{":
                        d += 1
                    elif rest[f_pos] == "}":
                        d -= 1
                    f_pos += 1
                val = rest[v_start:f_pos - 1]
            elif ch == '"':
                f_pos += 1
                v_start = f_pos
                while f_pos < rn and not (rest[f_pos] == '"' and rest[f_pos - 1] != "\\"):
                    f_pos += 1
                val = rest[v_start:f_pos]
                f_pos += 1
            else:
                v_start = f_pos
                while f_pos < rn and rest[f_pos] not in ",\n":
                    f_pos += 1
                val = rest[v_start:f_pos]
            fields[fkey] = re.sub(r"\s+", " ", val.strip())

        raw_authors = [a.strip() for a in re.split(r"\s+and\s+", fields.get("author", ""), flags=re.I) if a.strip()]
        authors = [_parse_author_name(a) for a in raw_authors]
        venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
        is_preprint = bool(
            RE_PREPRINT.search(venue)
            or RE_PREPRINT.search(fields.get("archiveprefix", ""))
            or RE_PREPRINT.search(fields.get("note", ""))
            or RE_PREPRINT.search(fields.get("doi", ""))
            or entry_type in {"unpublished", "misc"} and bool(fields.get("eprint"))
        )
        records.append({
            "id": cite_key,
            "source_format": "bibtex",
            "source_file": source_file,
            "entry_type": entry_type,
            "authors": authors,
            "year": re.sub(r"[^0-9]", "", fields.get("year", ""))[:4],
            "title": fields.get("title", "").strip("{}"),
            "alt_title": (fields.get("title_en") or fields.get("title_zh") or fields.get("translated_title") or "").strip("{}"),
            "venue": venue.strip("{}"),
            "volume": fields.get("volume", ""),
            "issue": fields.get("number", "") or fields.get("issue", ""),
            "pages": fields.get("pages", "").replace("--", "-"),
            "publisher": fields.get("publisher", ""),
            "doi": normalize_doi(fields.get("doi", "")),
            "url": fields.get("url", ""),
            "note": fields.get("note", ""),
            "abstract": fields.get("abstract", ""),
            "is_preprint": is_preprint,
        })
    return records


def parse_ris(text: str, source_file: str) -> list[dict[str, Any]]:
    """Standard RIS (.ris) parser using pure stdlib."""
    records: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    authors: list[str] = []
    sp = ""
    ep = ""

    def _flush() -> None:
        nonlocal cur, authors, sp, ep
        if not cur and not authors:
            return
        ty = cur.get("TY", "JOUR").upper()
        etype = {"JOUR": "article", "CONF": "inproceedings", "CPAPER": "inproceedings", "BOOK": "book", "THES": "phdthesis", "PREP": "misc"}.get(ty, "article")
        venue = cur.get("JF") or cur.get("JO") or cur.get("T2") or cur.get("PB") or ""
        pages = f"{sp}-{ep}" if sp and ep else (sp or ep or cur.get("PG", ""))
        doi = normalize_doi(cur.get("DO", ""))
        note = cur.get("N1", "")
        is_prep = ty == "PREP" or bool(RE_PREPRINT.search(venue + " " + doi + " " + note))
        rec_id = cur.get("ID") or f"ris_{len(records)+1:03d}"
        records.append({
            "id": rec_id,
            "source_format": "ris",
            "source_file": source_file,
            "entry_type": etype,
            "authors": [_parse_author_name(a) for a in authors],
            "year": re.sub(r"[^0-9]", "", cur.get("PY") or cur.get("Y1") or "")[:4],
            "title": cur.get("TI") or cur.get("T1") or "",
            "alt_title": cur.get("TT") or "",
            "venue": venue,
            "volume": cur.get("VL", ""),
            "issue": cur.get("IS", ""),
            "pages": pages,
            "publisher": cur.get("PB", ""),
            "doi": doi,
            "url": cur.get("UR", ""),
            "note": note,
            "abstract": cur.get("AB", ""),
            "is_preprint": is_prep,
        })
        cur = {}
        authors = []
        sp = ""
        ep = ""

    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", line.strip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "ER":
            _flush()
        elif tag in ("AU", "A1"):
            authors.append(val)
        elif tag == "SP":
            sp = val
        elif tag == "EP":
            ep = val
        else:
            if tag not in cur or not cur[tag]:
                cur[tag] = val
    _flush()
    return records


def _extract_crossref_year(msg: dict[str, Any]) -> tuple[str, str]:
    """Extract publication year from Crossref date-parts, falling back honestly to created/deposited when issued is null."""
    for key in ("published-print", "published-online", "published", "issued", "created", "deposited"):
        block = msg.get(key)
        if isinstance(block, dict):
            dp = block.get("date-parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0] and dp[0][0] is not None:
                yr = re.sub(r"[^0-9]", "", str(dp[0][0]))[:4]
                if len(yr) == 4:
                    return yr, key
    return "", ""


def _parse_crossref_work(msg: dict[str, Any], source_file: str, idx: int) -> dict[str, Any]:
    """Convert a single Crossref REST API work object (`message`) into a canonical bibliography record."""
    raw_auths = msg.get("author") or []
    authors: list[dict[str, str]] = []
    for a in raw_auths:
        if isinstance(a, dict):
            if a.get("name"):
                authors.append(_parse_author_name(str(a["name"])))
            else:
                fam, giv = str(a.get("family", "")).strip(), str(a.get("given", "")).strip()
                authors.append({"family": fam, "given": giv, "raw": f"{fam}, {giv}".strip(", ")})
        elif isinstance(a, str):
            authors.append(_parse_author_name(a))

    title_val = msg.get("title")
    title = str(title_val[0] if isinstance(title_val, list) and title_val else (title_val or "")).strip()
    orig_title_val = msg.get("original-title")
    alt_title = str(orig_title_val[0] if isinstance(orig_title_val, list) and orig_title_val else (orig_title_val or "")).strip()
    ct_val = msg.get("container-title") or msg.get("short-container-title") or []
    venue = str(ct_val[0] if isinstance(ct_val, list) and ct_val else (ct_val or msg.get("publisher") or "")).strip()

    year, date_src = _extract_crossref_year(msg)
    cr_type = str(msg.get("type") or "journal-article").lower()
    etype = {
        "journal-article": "article",
        "proceedings-article": "inproceedings",
        "book": "book",
        "book-chapter": "incollection",
        "dissertation": "phdthesis",
        "posted-content": "misc",
        "component": "misc",
    }.get(cr_type, "article")

    doi = normalize_doi(msg.get("DOI") or msg.get("doi") or "")
    # Capture parent DOI for Crossref component/supplemental items (e.g., ACS .s001 / .s003)
    parent_doi = ""
    rel = msg.get("relation") or {}
    if isinstance(rel, dict):
        for rkey in ("is-component-of", "is-part-of"):
            for robj in (rel.get(rkey) or []):
                if isinstance(robj, dict) and robj.get("id"):
                    parent_doi = normalize_doi(str(robj["id"]))
                    if parent_doi:
                        break
            if parent_doi:
                break
    desc = str(msg.get("description") or "").strip()
    if not parent_doi and desc:
        m_p = RE_DOI.search(desc)
        if m_p:
            cand_p = normalize_doi(m_p.group(1))
            if cand_p and cand_p != doi:
                parent_doi = cand_p

    resource_url = ""
    res_obj = msg.get("resource") or {}
    if isinstance(res_obj, dict) and isinstance(res_obj.get("primary"), dict):
        resource_url = str(res_obj["primary"].get("URL") or "")
    url = resource_url or str(msg.get("URL") or "")

    note_parts = [p for p in (desc, f"crossref_type={cr_type}", f"year_source={date_src}" if date_src and date_src != "issued" else "") if p]
    abstract = re.sub(r"<[^>]+>", " ", str(msg.get("abstract") or "")).strip()
    is_prep = cr_type == "posted-content" or bool(RE_PREPRINT.search(venue + " " + doi + " " + desc))

    rec_id = Path(source_file).stem if idx == 1 else f"{Path(source_file).stem}_{idx:03d}"
    if doi:
        slug = re.sub(r"[^a-z0-9]+", "_", doi).strip("_")[-36:]
        rec_id = f"cr_{slug}"

    return {
        "id": rec_id,
        "source_format": "crossref_json",
        "source_file": source_file,
        "entry_type": etype,
        "crossref_type": cr_type,
        "parent_doi": parent_doi,
        "authors": authors,
        "year": year,
        "title": title,
        "alt_title": alt_title,
        "venue": venue,
        "volume": str(msg.get("volume") or ""),
        "issue": str(msg.get("issue") or ""),
        "pages": str(msg.get("page") or ""),
        "publisher": str(msg.get("publisher") or ""),
        "doi": doi,
        "url": url,
        "note": "; ".join(note_parts),
        "abstract": abstract,
        "is_preprint": is_prep,
    }


def parse_csl_json(text: str, source_file: str) -> list[dict[str, Any]]:
    """Parse CSL-JSON or native Crossref REST API JSON (`work` / `work-list`)."""
    data = json.loads(text)
    if isinstance(data, dict) and data.get("message-type") == "work" and isinstance(data.get("message"), dict):
        return [_parse_crossref_work(data["message"], source_file, 1)]
    if isinstance(data, dict) and data.get("message-type") == "work-list" and isinstance(data.get("message"), dict):
        return [_parse_crossref_work(it, source_file, idx) for idx, it in enumerate(data["message"].get("items", []), start=1) if isinstance(it, dict)]

    items = data if isinstance(data, list) else data.get("references", data.get("items", []))
    records: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("title"), list) and ("DOI" in item or "prefix" in item):
            records.append(_parse_crossref_work(item, source_file, idx))
            continue
        raw_auths = item.get("author", [])
        authors: list[dict[str, str]] = []
        for a in raw_auths:
            if isinstance(a, dict):
                if a.get("literal"):
                    authors.append(_parse_author_name(str(a["literal"])))
                else:
                    authors.append({"family": str(a.get("family", "")), "given": str(a.get("given", "")), "raw": f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")})
            elif isinstance(a, str):
                authors.append(_parse_author_name(a))
        year = ""
        issued = item.get("issued", {})
        if isinstance(issued, dict) and issued.get("date-parts"):
            dp = issued["date-parts"][0]
            if dp and dp[0] is not None:
                year = str(dp[0])[:4]
        elif item.get("year"):
            year = str(item["year"])[:4]
        ct = item.get("container-title") or item.get("journal") or item.get("publisher") or ""
        venue = str(ct[0] if isinstance(ct, list) and ct else ct)
        csl_type = str(item.get("type", "article-journal")).lower()
        etype = {"article-journal": "article", "paper-conference": "inproceedings", "book": "book", "thesis": "phdthesis"}.get(csl_type, "article")
        doi = normalize_doi(item.get("DOI") or item.get("doi") or "")
        note = str(item.get("note") or "")
        is_prep = csl_type == "preprint" or bool(RE_PREPRINT.search(venue + " " + doi + " " + note))
        records.append({
            "id": str(item.get("id") or f"csl_{idx:03d}"),
            "source_format": "csl_json",
            "source_file": source_file,
            "entry_type": etype,
            "authors": authors,
            "year": year,
            "title": str(item.get("title") or ""),
            "alt_title": str(item.get("title_en") or item.get("title_zh") or item.get("translated_title") or ""),
            "venue": venue,
            "volume": str(item.get("volume") or ""),
            "issue": str(item.get("issue") or ""),
            "pages": str(item.get("page") or item.get("pages") or ""),
            "publisher": str(item.get("publisher") or ""),
            "doi": doi,
            "url": str(item.get("URL") or item.get("url") or ""),
            "note": note,
            "abstract": str(item.get("abstract") or ""),
            "is_preprint": is_prep,
        })
    return records


def parse_jats_xml_references(text: str, source_file: str) -> list[dict[str, Any]]:
    """Parse EuropePMC / PubMed Central JATS XML `<ref-list><ref>` entries into canonical bibliography records."""
    records: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return records

    for idx, ref in enumerate(root.findall(".//ref-list//ref"), start=1):
        ref_id = ref.attrib.get("id") or f"jats_ref_{idx:03d}"
        authors: list[dict[str, str]] = []
        for nm in ref.findall(".//name"):
            sn = (nm.findtext("surname") or "").strip()
            gn = (nm.findtext("given-names") or "").strip()
            if sn:
                authors.append({"family": sn, "given": gn, "raw": f"{sn}, {gn}".strip(", ")})
        title_el = ref.find(".//article-title")
        title = " ".join("".join(title_el.itertext()).split()) if title_el is not None else ""
        venue_el = ref.find(".//source")
        venue = " ".join("".join(venue_el.itertext()).split()) if venue_el is not None else ""
        year = re.sub(r"[^0-9]", "", ref.findtext(".//year") or "")[:4]
        vol = (ref.findtext(".//volume") or "").strip()
        iss = (ref.findtext(".//issue") or "").strip()
        fp = (ref.findtext(".//fpage") or "").strip()
        lp = (ref.findtext(".//lpage") or "").strip()
        pages = f"{fp}-{lp}" if fp and lp else (fp or lp)
        doi = ""
        for pid in ref.findall(".//pub-id"):
            if pid.attrib.get("pub-id-type") == "doi" and pid.text:
                doi = normalize_doi(pid.text)
                break
        if not doi:
            raw_ref = " ".join("".join(ref.itertext()).split())
            m_d = RE_DOI.search(raw_ref)
            if m_d:
                doi = normalize_doi(m_d.group(1))
        if not title:
            title = " ".join("".join(ref.itertext()).split())[:180]
        records.append({
            "id": ref_id,
            "source_format": "jats_xml",
            "source_file": source_file,
            "entry_type": "article",
            "authors": authors,
            "year": year,
            "title": title,
            "alt_title": "",
            "venue": venue,
            "volume": vol,
            "issue": iss,
            "pages": pages,
            "publisher": "",
            "doi": doi,
            "url": "",
            "note": "",
            "abstract": "",
            "is_preprint": bool(RE_PREPRINT.search(venue + " " + doi)),
        })
    return records


def parse_markdown_references(text: str, source_file: str) -> list[dict[str, Any]]:
    """Parse numbered or EuropePMC-exported `## References` blocks from Markdown/plain-text papers."""
    _, ref_lines = _split_body_and_refs(text)
    records: list[dict[str, Any]] = []
    for _, ln in ref_lines:
        st = ln.strip()
        m = re.match(r"^(?:\[(\d+)\]|(\d+)\.)\s+(?:\d+\.\s+)?(.+)$", st)
        if not m:
            continue
        num = m.group(1) or m.group(2)
        body = m.group(3).strip()
        # Clean EuropePMC compact DOI trailing PMID/PMCID digits (e.g., 10.1021/jacs.4c0718539437413 -> 10.1021/jacs.4c07185)
        doi = ""
        m_doi = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", body)
        if m_doi:
            raw_d = m_doi.group(1)
            raw_d = re.sub(r"PMC\d+.*$", "", raw_d)
            # Strip glued 8-digit PMID suffix after a dot/alphanumeric code when present
            raw_d = re.sub(r"(?<=[a-zA-Z0-9])(\d{8})$", "", raw_d)
            doi = normalize_doi(raw_d)

        # Extract year (supports compact `J. Am. Chem. Soc.202414630078` and normal `(2024)` / `. 2024;`)
        yr = ""
        m_yr = re.search(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)", body)
        if not m_yr:
            m_yr = re.search(r"\.((?:19|20)\d{2})\d", body)
        if m_yr:
            yr = m_yr.group(1)

        # Extract leading authors before title
        authors: list[dict[str, str]] = []
        # EuropePMC compact author format: `KongF. HuangY. YuX.`
        compact_auths = re.findall(r"\b([A-Z][a-z\-]{1,18})([A-Z]\.(?:[A-Z]\.)?)", body[:260])
        if len(compact_auths) >= 2:
            for fam, init in compact_auths[:12]:
                authors.append({"family": fam, "given": init, "raw": f"{fam}, {init}"})
            last_a = compact_auths[-1][0] + compact_auths[-1][1]
            pos = body.find(last_a)
            title_cand = body[pos + len(last_a):].strip(" .") if pos != -1 else body
        else:
            # Standard format: `Author A, Author B. Title. Journal (Year)...`
            parts = [p.strip() for p in re.split(r"\.\s+", body) if p.strip()]
            if parts:
                for raw_a in re.split(r",\s*(?:and\s+)?|;\s*|\band\b", parts[0]):
                    ra = raw_a.strip()
                    if ra and len(ra) <= 40 and not re.search(r"\d{4}", ra):
                        authors.append(_parse_author_name(ra))
            title_cand = parts[1] if len(parts) > 1 else body

        # Trim trailing journal/year/DOI from compact title
        if yr and yr in title_cand:
            title_cand = title_cand.split(yr, 1)[0].strip(" .,;(")
        if doi and doi in title_cand.lower():
            idx_d = title_cand.lower().find(doi)
            title_cand = title_cand[:idx_d].strip(" .,;")

        records.append({
            "id": f"{Path(source_file).stem}_ref{num}",
            "source_format": "markdown_refs",
            "source_file": source_file,
            "entry_type": "article",
            "authors": authors,
            "year": yr,
            "title": title_cand[:240],
            "alt_title": "",
            "venue": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "publisher": "",
            "doi": doi,
            "url": "",
            "note": f"raw_ref_num={num}",
            "abstract": "",
            "is_preprint": bool(RE_PREPRINT.search(body)),
        })
    return records


def load_library_files(paths: list[Path]) -> list[dict[str, Any]]:
    """Load bibliography records from .bib, .ris, .json (CSL/Crossref), .xml (JATS), .md/.txt, or directories."""
    expanded: list[Path] = []
    for p in paths:
        if p.is_dir():
            for fp in sorted(p.rglob("*")):
                if fp.is_file() and fp.suffix.lower() in {".bib", ".ris", ".json", ".xml"}:
                    expanded.append(fp)
        elif p.is_file():
            expanded.append(p)

    records: list[dict[str, Any]] = []
    for p in expanded:
        txt = p.read_text(encoding="utf-8", errors="replace")
        suf = p.suffix.lower()
        if suf == ".bib":
            records.extend(parse_bibtex(txt, p.name))
        elif suf == ".ris":
            records.extend(parse_ris(txt, p.name))
        elif suf == ".json":
            records.extend(parse_csl_json(txt, p.name))
        elif suf == ".xml":
            records.extend(parse_jats_xml_references(txt, p.name))
        elif suf in {".md", ".txt"}:
            records.extend(parse_markdown_references(txt, p.name))
    return records


# ---------------------------------------------------------------------------
# 3. Explainable Deduplication Engine with Homonymous-Author Protection
# ---------------------------------------------------------------------------

def _record_completeness_score(rec: dict[str, Any]) -> tuple[int, int]:
    """Higher tuple wins as canonical survivor: peer-reviewed > preprint, then richer metadata."""
    peer_reviewed = 0 if rec.get("is_preprint") else 1
    meta_pts = sum(
        1 for k in ("doi", "venue", "volume", "issue", "pages", "year", "title") if rec.get(k)
    ) + (1 if rec.get("authors") else 0)
    return (peer_reviewed, meta_pts)


def _titles_match(r1: dict[str, Any], r2: dict[str, Any]) -> tuple[bool, float, str]:
    t1s = [normalize_title(t) for t in (r1.get("title", ""), r1.get("alt_title", "")) if t]
    t2s = [normalize_title(t) for t in (r2.get("title", ""), r2.get("alt_title", "")) if t]
    best_sim = 0.0
    matched_via = ""
    for i1, a in enumerate(t1s):
        for i2, b in enumerate(t2s):
            if not a or not b:
                continue
            sim = difflib.SequenceMatcher(None, a, b).ratio()
            if sim > best_sim:
                best_sim = sim
                if i1 == 0 and i2 == 0:
                    matched_via = "primary_title"
                else:
                    matched_via = "bilingual_or_alt_title"
    return best_sim >= 0.90, round(best_sim, 3), matched_via


def deduplicate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge duplicates with explainable criteria while protecting distinct homonymous-author papers & SI components."""
    clusters: list[list[dict[str, Any]]] = []
    merge_decisions: list[dict[str, Any]] = []
    protected_homonymous_pairs: list[dict[str, Any]] = []
    related_component_groups: dict[str, list[dict[str, Any]]] = {}

    for rec in records:
        placed = False
        for cluster in clusters:
            rep = cluster[0]
            doi1, doi2 = rec.get("doi", ""), rep.get("doi", "")
            s1, s2 = _author_surname_set(rec.get("authors", [])), _author_surname_set(rep.get("authors", []))
            shared_authors = s1 & s2
            t_match, t_sim, t_via = _titles_match(rec, rep)

            # Criterion 1: Exact normalized DOI match
            if doi1 and doi2 and doi1 == doi2:
                cluster.append(rec)
                merge_decisions.append({
                    "kept_or_candidate": [rep["id"], rec["id"]],
                    "criterion": "exact_doi_match",
                    "doi": doi1,
                    "title_similarity": t_sim,
                    "rationale": f"Identical normalized DOI ({doi1}) across {rep['source_format']} and {rec['source_format']}.",
                })
                placed = True
                break

            # Protect distinct Crossref Supplemental Information components (.s001 vs .s003) sharing parent DOI/title
            is_distinct_si_components = (
                doi1 and doi2 and doi1 != doi2
                and (
                    (rec.get("crossref_type") == "component" and rep.get("crossref_type") == "component")
                    or (rec.get("parent_doi") and rec.get("parent_doi") == rep.get("parent_doi"))
                    or (rec.get("parent_doi") == doi2 or rep.get("parent_doi") == doi1)
                )
            )
            if is_distinct_si_components:
                p_doi = rec.get("parent_doi") or rep.get("parent_doi") or doi2
                grp = related_component_groups.setdefault(p_doi, [])
                for item in (rep, rec):
                    if item["id"] not in {x["id"] for x in grp}:
                        grp.append({"id": item["id"], "doi": item.get("doi", ""), "type": item.get("crossref_type", item.get("entry_type")), "url": item.get("url", "")})
                continue

            # Criterion 2: High title similarity (>=0.90) + author overlap (including preprint vs formal published or bilingual alt title)
            if t_match and (shared_authors or not (s1 and s2)):
                if rec.get("is_preprint") != rep.get("is_preprint"):
                    crit = "preprint_vs_published_title_author_match"
                    rat = f"Same work detected across preprint and formal publication (title_sim={t_sim} via {t_via}, shared_authors={sorted(shared_authors)}); formal publication preferred."
                elif t_via == "bilingual_or_alt_title":
                    crit = "bilingual_title_author_match"
                    rat = f"Bilingual/alternative title match (sim={t_sim}) with shared authors {sorted(shared_authors)}."
                else:
                    crit = "normalized_title_author_match"
                    rat = f"Normalized title match (sim={t_sim}) with shared authors {sorted(shared_authors)}."
                cluster.append(rec)
                merge_decisions.append({
                    "kept_or_candidate": [rep["id"], rec["id"]],
                    "criterion": crit,
                    "title_similarity": t_sim,
                    "rationale": rat,
                })
                placed = True
                break

            # Explicit protection check: homonymous authors (same author surname(s) & same/close year) with DISTINCT titles/DOIs
            if shared_authors and rec.get("year") and rec.get("year") == rep.get("year") and not t_match:
                protected_homonymous_pairs.append({
                    "record_a": {"id": rep["id"], "title": rep["title"], "doi": doi2, "year": rep["year"]},
                    "record_b": {"id": rec["id"], "title": rec["title"], "doi": doi1, "year": rec["year"]},
                    "shared_surnames": sorted(shared_authors),
                    "title_similarity": t_sim,
                    "decision": "kept_separate_homonymous_author_protection",
                    "rationale": "Same author surname and publication year, but distinct normalized titles/DOIs; protected from false merge.",
                })

        if not placed:
            clusters.append([rec])

    canonical_records: list[dict[str, Any]] = []
    alias_to_canonical: dict[str, str] = {}

    for cluster in clusters:
        cluster_sorted = sorted(cluster, key=_record_completeness_score, reverse=True)
        winner = dict(cluster_sorted[0])
        merged_ids = [r["id"] for r in cluster_sorted[1:]]
        for loser in cluster_sorted[1:]:
            for field in ("alt_title", "abstract", "pages", "volume", "issue", "url", "publisher", "parent_doi"):
                if not winner.get(field) and loser.get(field):
                    winner[field] = loser[field]
            if loser.get("is_preprint") and loser.get("doi") and loser["doi"] != winner.get("doi"):
                winner["preprint_doi"] = loser["doi"]
        winner["merged_from"] = merged_ids
        blob = f"{winner.get('title','')} {winner.get('note','')} {winner.get('venue','')}"
        if RE_RETRACT.search(blob):
            winner["integrity_flag"] = "retracted"
        elif RE_EOC.search(blob):
            winner["integrity_flag"] = "expression_of_concern"
        elif winner.get("is_preprint"):
            winner["integrity_flag"] = "preprint_not_peer_reviewed"
        else:
            winner["integrity_flag"] = "clean"
        canonical_records.append(winner)
        for r in cluster_sorted:
            alias_to_canonical[r["id"]] = winner["id"]

    return {
        "input_records_count": len(records),
        "deduplicated_count": len(canonical_records),
        "merged_duplicates_count": len(records) - len(canonical_records),
        "merge_decisions": merge_decisions,
        "protected_homonymous_pairs": protected_homonymous_pairs,
        "related_component_groups": related_component_groups,
        "alias_to_canonical": alias_to_canonical,
        "records": canonical_records,
    }


# ---------------------------------------------------------------------------
# 4. GB/T 7714-2015 & APA 7th Offline Formatters + BibTeX Exporter
# ---------------------------------------------------------------------------

def _doc_type_code_gbt(rec: dict[str, Any]) -> str:
    if rec.get("is_preprint"):
        return "[EB/OL]"
    et = rec.get("entry_type", "article")
    return {"article": "[J]", "inproceedings": "[C]", "book": "[M]", "phdthesis": "[D]", "mastersthesis": "[D]"}.get(et, "[J]")


def _format_authors_gbt(authors: list[dict[str, str]], is_zh: bool) -> str:
    if not authors:
        return "[MISSING_FIELD: authors]"
    formatted: list[str] = []
    for a in authors[:3]:
        if is_zh or RE_CJK.search(a.get("family", "")):
            formatted.append(a["raw"])
        else:
            fam = a["family"].upper()
            inits = " ".join(p[0].upper() for p in re.split(r"[\s\-]+", a.get("given", "")) if p)
            formatted.append(f"{fam} {inits}".strip())
    joined = ", ".join(formatted)
    if len(authors) > 3:
        joined += ", 等" if is_zh else ", et al"
    return joined


def format_gbt7714(rec: dict[str, Any], index_num: int) -> dict[str, Any]:
    """Format a record into GB/T 7714-2015 and report missing bibliographic fields."""
    is_zh = _is_cjk_record(rec)
    missing: list[str] = []
    for req in ("authors", "title", "year", "venue", "doi"):
        if not rec.get(req):
            missing.append(req)
    if rec.get("entry_type") == "article" and not rec.get("is_preprint"):
        for req in ("volume", "pages"):
            if not rec.get(req):
                missing.append(req)

    auth_str = _format_authors_gbt(rec.get("authors", []), is_zh)
    title = rec.get("title") or "[MISSING_FIELD: title]"
    code = _doc_type_code_gbt(rec)
    venue = rec.get("venue") or "[MISSING_FIELD: venue]"
    year = rec.get("year") or "[MISSING_FIELD: year]"

    vol_issue = ""
    if rec.get("volume") and rec.get("issue"):
        vol_issue = f", {rec['volume']}({rec['issue']})"
    elif rec.get("volume"):
        vol_issue = f", {rec['volume']}"
    pages = f": {rec['pages']}" if rec.get("pages") else ""
    doi_part = f" DOI: {rec['doi']}." if rec.get("doi") else ""
    miss_note = f" [MISSING_FIELDS: {', '.join(missing)}]" if missing else ""

    text = f"[{index_num}] {auth_str}. {title}{code}. {venue}, {year}{vol_issue}{pages}.{doi_part}{miss_note}"
    return {"index": index_num, "id": rec["id"], "formatted": text, "missing_fields": missing}


def _format_authors_apa7(authors: list[dict[str, str]]) -> str:
    if not authors:
        return "[MISSING_FIELD: authors]"
    formatted: list[str] = []
    for a in authors:
        if RE_CJK.search(a.get("family", "")):
            formatted.append(a["raw"])
        else:
            inits = " ".join(f"{p[0].upper()}." for p in re.split(r"[\s\-]+", a.get("given", "")) if p)
            formatted.append(f"{a['family']}, {inits}".strip(", "))
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) <= 20:
        return ", ".join(formatted[:-1]) + ", & " + formatted[-1]
    return ", ".join(formatted[:19]) + ", ... " + formatted[-1]


def format_apa7(rec: dict[str, Any]) -> dict[str, Any]:
    """Format a record into APA 7th edition and report missing bibliographic fields."""
    missing: list[str] = []
    for req in ("authors", "year", "title", "venue", "doi"):
        if not rec.get(req):
            missing.append(req)
    auth_str = _format_authors_apa7(rec.get("authors", []))
    year = rec.get("year") or "n.d."
    title = rec.get("title") or "[MISSING_FIELD: title]"
    venue = rec.get("venue") or "[MISSING_FIELD: venue]"
    vol = f"*{rec['volume']}*" if rec.get("volume") else ""
    iss = f"({rec['issue']})" if rec.get("issue") else ""
    pg = f", {rec['pages']}" if rec.get("pages") else ""
    loc_str = f", {vol}{iss}{pg}" if (vol or pg) else ""
    doi_url = f" https://doi.org/{rec['doi']}" if rec.get("doi") else ""
    miss_note = f" [MISSING_FIELDS: {', '.join(missing)}]" if missing else ""
    text = f"{auth_str} ({year}). {title}. *{venue}*{loc_str}.{doi_url}{miss_note}"
    return {"id": rec["id"], "formatted": text, "missing_fields": missing}


def export_clean_bibtex(records: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for r in records:
        etype = r.get("entry_type", "article")
        auth = " and ".join(a["raw"] for a in r.get("authors", []))
        lines = [f"@{etype}{{{r['id']},"]
        kv = [
            ("author", auth),
            ("title", r.get("title", "")),
            ("journal" if etype == "article" else "booktitle", r.get("venue", "")),
            ("year", r.get("year", "")),
            ("volume", r.get("volume", "")),
            ("number", r.get("issue", "")),
            ("pages", r.get("pages", "")),
            ("doi", r.get("doi", "")),
            ("note", r.get("note", "")),
        ]
        for k, v in kv:
            if v:
                lines.append(f"  {k} = {{{v}}},")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# 5. OOXML Live-Field Auditor (Word <-> Google Docs Round-Trip Pain Point)
# ---------------------------------------------------------------------------

ZOTERO_MIGRATION_RECOVERY_STEPS = [
    "Preserve a read-only backup of the master .docx containing intact OOXML fields before sharing.",
    "For Word <-> Google Docs transfer, use Zotero Plugin -> Document Preferences -> 'Switch to a Different Word Processor' to generate a transfer document rather than opening .docx directly in Google Docs.",
    "After opening in the target processor, run Zotero -> Refresh to restore active field codes and verify citations remain editable (not plain text).",
    "If fields are already flattened, recover from the intact master copy via Word Compare/Merge or re-bind each flattened marker to its canonical Zotero/BibTeX item; never treat visible '[1]' text as proof of live linkage.",
]


def audit_docx_live_fields(docx_path: Path) -> dict[str, Any]:
    """Distinguish intact Zotero/EndNote/Citavi live OOXML fields vs flattened plain-text citations."""
    info = inspect_docx_ooxml(docx_path)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error", "docx inspection failed")}

    live_cite_fields = [
        f for f in info["live_fields"]
        if f["kind"] in ("zotero_citation", "endnote_citation", "citavi_citation", "mendeley_citation", "word_citation")
    ]
    live_bib_fields = [f for f in info["live_fields"] if f["kind"].endswith("_bibliography")]
    live_paras = {f["paragraph_index"] for f in live_cite_fields + live_bib_fields}

    # Count paragraph-level plain citation markers that have NO live field in the same paragraph
    flattened_paragraphs: list[dict[str, Any]] = []
    total_text_markers = 0
    for p in info["paragraphs"]:
        txt = p["text"]
        if not txt:
            continue
        b_hits = RE_BRACKET_GROUP.findall(txt)
        ay_hits = RE_AUTHOR_YEAR_GROUP.findall(txt)
        p_markers = len(b_hits) + len(ay_hits)
        if p_markers > 0:
            total_text_markers += p_markers
            if p["index"] not in live_paras:
                flattened_paragraphs.append({
                    "paragraph_index": p["index"],
                    "text_preview": txt[:140],
                    "bracket_groups": b_hits,
                    "author_year_groups": ay_hits,
                })

    if len(live_cite_fields) > 0 and len(flattened_paragraphs) == 0:
        status = "intact"
    elif len(live_cite_fields) > 0 and len(flattened_paragraphs) > 0:
        status = "mixed"
    elif total_text_markers > 0 and len(live_cite_fields) == 0:
        status = "flattened_plain_text"
    else:
        status = "no_citations_detected"

    return {
        "ok": True,
        "path": str(docx_path.name),
        "verification_scope": "static_ooxml_field_inspection_only",
        "word_zotero_dynamic_refresh_verified": False,
        "scope_disclaimer": (
            "静态 OOXML 域结构检查（未运行 Word/Zotero 动态 Refresh）：仅离线解析 word/document.xml 中的域指令与纯文本引文标记，"
            "不能替代真实 Microsoft Word / Zotero Refresh 后的动态更新验证。"
        ),
        "live_field_status": status,
        "live_citation_field_count": len(live_cite_fields),
        "live_bibliography_field_count": len(live_bib_fields),
        "visible_citation_marker_groups": total_text_markers,
        "paragraphs_with_flattened_markers": flattened_paragraphs,
        "live_field_kinds": sorted({f["kind"] for f in live_cite_fields + live_bib_fields}),
        "recovery_protocol": ZOTERO_MIGRATION_RECOVERY_STEPS if status in ("flattened_plain_text", "mixed") else [],
        "full_text": info["full_text"],
    }


# ---------------------------------------------------------------------------
# 6. Bidirectional Synchronization, Renumbering & Two-Layer Verification
# ---------------------------------------------------------------------------

def _expand_bracket_group(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi >= lo and (hi - lo) <= 50:
                out.extend(range(lo, hi + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def _compress_numbers(nums: list[int]) -> str:
    uniq = sorted(set(nums))
    if not uniq:
        return ""
    ranges: list[str] = []
    start = prev = uniq[0]
    for n in uniq[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if prev - start >= 2 else (f"{start}, {prev}" if prev > start else str(start)))
            start = prev = n
    ranges.append(f"{start}-{prev}" if prev - start >= 2 else (f"{start}, {prev}" if prev > start else str(start)))
    return "[" + ", ".join(ranges) + "]"


def _split_body_and_refs(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    body: list[tuple[int, str]] = []
    refs: list[tuple[int, str]] = []
    in_refs = False
    for lno, ln in enumerate(text.splitlines(), start=1):
        st = ln.strip()
        if re.match(r"^(?:#+\s*)?(?:References|Bibliography|参考文献)\s*$", st, re.I):
            in_refs = True
            continue
        if in_refs:
            refs.append((lno, ln))
        else:
            body.append((lno, ln))
    return body, refs


# Outcome-aware polarity equivalence & condition-boundary helpers for Layer 2 verification
RE_UNDESIRABLE_METRIC = re.compile(
    r"\b(?:mortality|death|fatality|error|mae|rmse|loss|overpotential|risk|toxicity|inflammation|"
    r"neuroinflammation|permeability|failure\s+rate|relapse|dissolution|degradation|agglomeration|"
    r"complication|latency|cost|resistance|bias|leakage)\b|死亡率|误差|过电位|风险|毒性|炎症|渗透性|降解|失效",
    re.I,
)
RE_DESIRABLE_METRIC = re.compile(
    r"\b(?:survival|accuracy|auroc|auc|f1|efficiency|capacity|stability|retention|recovery|"
    r"activity|selectivity|performance|integrity|durability|tolerance|butyrate|treg|efficacy)\b|"
    r"生存率|准确率|效率|容量|稳定性|恢复|活性|选择性|完整性|疗效",
    re.I,
)
RE_DIR_DECREASE = re.compile(
    r"\b(?:reduc(?:es?|ed|ing)|decreas(?:es?|ed|ing)|lower(?:s|ed|ing)?|suppress(?:es?|ed|ing)|"
    r"mitigat(?:es?|ed|ing)|alleviat(?:es?|ed|ing)|prevent(?:s|ed|ing)|inhibit(?:s|ed|ing)|"
    r"attenuat(?:es?|ed|ing)|shorten(?:s|ed|ing)|minimis(?:es?|ed)|minimiz(?:es?|ed))\b|降低|减少|抑制|缓解|缩短",
    re.I,
)
RE_DIR_INCREASE = re.compile(
    r"\b(?:increas(?:es?|ed|ing)|improv(?:es?|ed|ing)|enhanc(?:es?|ed|ing)|elevat(?:es?|ed|ing)|"
    r"higher|boost(?:s|ed|ing)|promot(?:es?|ed|ing)|outperform(?:s|ed|ing)|accelerat(?:es?|ed)|"
    r"aggravat(?:es?|ed)|worsen(?:s|ed)|prolong(?:s|ed))\b|提高|增加|增强|改善|促进|优于|加重|恶化",
    re.I,
)
RE_NULL_EFFECT = re.compile(
    r"\b(?:no\s+significant\s+(?:effect|difference|improvement|benefit|change)|failed\s+to\s+(?:improve|reduce|increase|show)|"
    r"did\s+not\s+(?:improve|reduce|increase|differ|change)|null\s+effect|no\s+benefit)\b|无显著|未能|未见明显",
    re.I,
)
RE_SCOPE_CONDS = re.compile(
    r"\b(?:acidic|alkaline|neutral|seawater|1\.?0?\s*m\s*koh|in\s+vitro|in\s+vivo|in\s+silico|"
    r"in\s+mice|murine|rat\s+model|pediatric|elderly|high-?dose|low-?dose|chronic|acute|"
    r"observational|retrospective|cross-?sectional|scaffold\s+splitting|random\s+splitting)\b|"
    r"(?:酸性|碱性|海水|体外|体内|动物模型|小鼠|儿童|老年|高剂量|低剂量|回顾性|观察性)",
    re.I,
)
SEMANTIC_SYNONYM_MAP = {
    "mortality": "survival_axis", "death": "survival_axis", "survival": "survival_axis", "fatality": "survival_axis",
    "error": "accuracy_axis", "rmse": "accuracy_axis", "mae": "accuracy_axis", "accuracy": "accuracy_axis",
    "auroc": "accuracy_axis", "performance": "accuracy_axis", "overpotential": "activity_axis",
    "activity": "activity_axis", "stability": "durability_axis", "durability": "durability_axis",
    "dissolution": "durability_axis", "degradation": "durability_axis", "inflammation": "inflam_axis",
    "neuroinflammation": "inflam_axis", "cytokines": "inflam_axis", "barrier": "barrier_axis",
    "permeability": "barrier_axis", "microbiota": "gut_axis", "dysbiosis": "gut_axis", "scfas": "scfa_axis",
    "butyrate": "scfa_axis", "mice": "murine_axis", "mouse": "murine_axis", "murine": "murine_axis",
    "rats": "rodent_axis", "septic": "sepsis_axis", "sepsis": "sepsis_axis",
    "therapy": "intervention_axis", "treatment": "intervention_axis", "supplementation": "intervention_axis",
    "intervention": "intervention_axis", "catalyst": "catalyst_axis", "electrocatalyst": "catalyst_axis",
    "electrocatalysts": "catalyst_axis", "catalysts": "catalyst_axis", "molecular": "molecule_axis",
    "molecules": "molecule_axis", "molecule": "molecule_axis",
}
POLARITY_MODIFIER_STOP = {
    "significantly", "markedly", "substantially", "reduced", "decreased", "lower", "lowered",
    "improved", "increased", "higher", "enhanced", "targeted", "oral", "rates", "rate", "level", "levels",
}


def _normalize_outcome_polarity(text: str) -> str:
    """Normalize direction + target valence so 'reduced mortality' == 'improved survival' ('beneficial')."""
    if RE_NULL_EFFECT.search(text):
        return "null_effect"
    dec_undesir = bool(re.search(
        rf"(?:{RE_DIR_DECREASE.pattern})\s+(?:[a-z0-9\-]+\s+){{0,3}}(?:{RE_UNDESIRABLE_METRIC.pattern})",
        text, re.I,
    ))
    inc_desir = bool(re.search(
        rf"(?:{RE_DIR_INCREASE.pattern})\s+(?:[a-z0-9\-]+\s+){{0,3}}(?:{RE_DESIRABLE_METRIC.pattern})",
        text, re.I,
    ))
    inc_undesir = bool(re.search(
        rf"(?:{RE_DIR_INCREASE.pattern})\s+(?:[a-z0-9\-]+\s+){{0,3}}(?:{RE_UNDESIRABLE_METRIC.pattern})",
        text, re.I,
    ))
    dec_desir = bool(re.search(
        rf"(?:{RE_DIR_DECREASE.pattern})\s+(?:[a-z0-9\-]+\s+){{0,3}}(?:{RE_DESIRABLE_METRIC.pattern})",
        text, re.I,
    ))
    beneficial = dec_undesir or inc_desir or (bool(re.search(r"\b(?:improv(?:es?|ed)|outperform(?:s|ed)|benefit(?:s|ed)?|alleviat(?:es?|ed))\b|改善|优于|缓解", text, re.I)) and not inc_undesir)
    adverse = inc_undesir or dec_desir or bool(re.search(r"\b(?:impair(?:s|ed)|worsen(?:s|ed)|aggravat(?:es?|ed)|adverse)\b|恶化|受损", text, re.I))
    if beneficial and adverse:
        return "mixed_conditional"
    if beneficial:
        return "beneficial"
    if adverse:
        return "adverse"
    return "neutral"


def _evaluate_claim_support_semantic(claim_text: str, source_text: str) -> tuple[str, str, dict[str, Any]]:
    """Layer-2 semantic verifier combining concept overlap, polarity-equivalence normalization, and condition boundaries."""
    ck = _tokenize_keywords(claim_text) - POLARITY_MODIFIER_STOP
    sk = _tokenize_keywords(source_text) - POLARITY_MODIFIER_STOP
    ck_sem = {SEMANTIC_SYNONYM_MAP.get(w, w) for w in ck}
    sk_sem = {SEMANTIC_SYNONYM_MAP.get(w, w) for w in sk}
    overlap = len(ck_sem & sk_sem)
    ratio = overlap / max(1, len(ck_sem))

    c_pol = _normalize_outcome_polarity(claim_text)
    s_pol = _normalize_outcome_polarity(source_text)
    c_conds = sorted({m.group(0).lower().strip() for m in RE_SCOPE_CONDS.finditer(claim_text)})
    s_conds = sorted({m.group(0).lower().strip() for m in RE_SCOPE_CONDS.finditer(source_text)})

    polarity_conflict = (
        (c_pol == "beneficial" and s_pol in ("adverse", "null_effect"))
        or (c_pol in ("adverse", "null_effect") and s_pol == "beneficial")
    )
    condition_mismatch = bool(
        (set(c_conds) & {"acidic"} and set(s_conds) & {"alkaline"} and not (set(s_conds) & {"acidic"}))
        or (set(c_conds) & {"alkaline"} and set(s_conds) & {"acidic"} and not (set(s_conds) & {"alkaline"}))
    )
    condition_omission = bool(s_conds and not c_conds and re.search(r"\b(?:universally|always|all\s+conditions|broadly)\b|普遍|始终", claim_text, re.I))

    diag = {
        "semantic_overlap": overlap,
        "claim_terms": len(ck_sem),
        "overlap_ratio": round(ratio, 3),
        "claim_polarity": c_pol,
        "source_polarity": s_pol,
        "polarity_aligned": not polarity_conflict,
        "claim_conditions": c_conds,
        "source_conditions": s_conds,
    }

    if overlap < 1 or (overlap < 2 and ratio < 0.15):
        return (
            "mismatch_or_unsupported",
            f"Source exists in library, but supplied text shows minimal semantic overlap ({overlap}/{len(ck_sem)}) with citing claim.",
            diag,
        )
    if polarity_conflict:
        return (
            "contradicted_by_supplied_excerpt",
            f"Polarity conflict: citing claim asserts `{c_pol}` while supplied source excerpt indicates `{s_pol}` (overlap={overlap}/{len(ck_sem)}).",
            diag,
        )
    if condition_mismatch:
        return (
            "partially_supported",
            f"Condition mismatch: claim specifies {c_conds} whereas supplied source is bounded by {s_conds}.",
            diag,
        )
    if condition_omission:
        return (
            "partially_supported",
            f"Scope overgeneralization: source is bounded by {s_conds}, omitted in broad citing claim.",
            diag,
        )
    if ratio >= 0.30 and overlap >= 2:
        pol_note = f", polarity aligned (`{c_pol}` <-> `{s_pol}`)" if c_pol != "neutral" or s_pol != "neutral" else ""
        return (
            "supported_by_supplied_excerpt",
            f"Semantic & topical alignment verified against supplied source text ({overlap}/{len(ck_sem)} normalized concepts{pol_note}).",
            diag,
        )
    return (
        "partially_supported",
        f"Partial semantic alignment ({overlap}/{len(ck_sem)} normalized concepts); manual verification recommended for exact scope.",
        diag,
    )


def _tokenize_keywords(text: str) -> set[str]:
    en = set(re.findall(r"[a-z]{4,}", text.lower()))
    zh = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    stop = {"that", "this", "with", "from", "were", "have", "been", "also", "into", "their", "these", "those", "study", "studies", "results", "showed", "reported", "using", "between", "within"}
    return (en - stop) | zh


def sync_and_verify_manuscript(
    manuscript_text: str,
    dedup_records: list[dict[str, Any]],
    *,
    excerpts_dir: Path | None = None,
    source_mapping_json: Path | None = None,
) -> dict[str, Any]:
    """Audit bidirectional closure, compute sequential renumbering, check NotebookLM #2416 collapse, and run two-layer verification."""
    body_lines, ref_lines = _split_body_and_refs(manuscript_text)

    # Parse numbered bibliography entries present in manuscript
    bib_by_num: dict[int, list[str]] = {}
    for _, ln in ref_lines:
        m = re.match(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$", ln.strip())
        if m:
            num = int(m.group(1) or m.group(2))
            bib_by_num.setdefault(num, []).append(m.group(3).strip())

    duplicate_bib_numbers = sorted(k for k, v in bib_by_num.items() if len(v) > 1)

    # Load optional structured source excerpts for Layer 2 (claim support verification)
    # Strictly require substantive abstract (>=40 chars) or explicit --excerpts-dir text;
    # metadata-only title/note strings MUST NOT masquerade as full-text/abstract excerpts.
    excerpt_corpus_by_key: dict[str, str] = {}
    for rec in dedup_records:
        abs_txt = str(rec.get("abstract") or "").strip()
        if len(abs_txt) >= 40:
            blob = " ".join(filter(None, [rec.get("title", ""), abs_txt])).strip()
            excerpt_corpus_by_key[rec["id"].lower()] = blob
            if rec.get("doi"):
                excerpt_corpus_by_key[rec["doi"].lower()] = blob
    if excerpts_dir and excerpts_dir.is_dir():
        for fp in sorted(excerpts_dir.rglob("*")):
            if fp.is_file() and fp.suffix.lower() in {".md", ".txt"}:
                excerpt_corpus_by_key[fp.stem.lower()] = fp.read_text(encoding="utf-8", errors="replace")

    # Scan body lines (including figure/table captions) in order of first appearance
    cited_nums_order: list[int] = []
    cited_num_contexts: dict[int, list[dict[str, Any]]] = {}
    author_year_occurrences: list[dict[str, Any]] = []
    total_marker_instances = 0

    for lno, ln in body_lines:
        is_caption = bool(re.match(r"^\s*(?:\*\*)?(?:Fig(?:ure|\.)?|Table|图|表)\s*S?\d+", ln.strip(), re.I))
        for m in RE_BRACKET_GROUP.finditer(ln):
            nums = _expand_bracket_group(m.group(1))
            for n in nums:
                total_marker_instances += 1
                if n not in cited_nums_order:
                    cited_nums_order.append(n)
                cited_num_contexts.setdefault(n, []).append({"line": lno, "is_caption": is_caption, "sentence": ln.strip()[:220]})
        for m in RE_AUTHOR_YEAR_GROUP.finditer(ln):
            parts = [p.strip() for p in m.group(1).split(";") if p.strip()]
            for p in parts:
                total_marker_instances += 1
                author_year_occurrences.append({"line": lno, "marker": p, "is_caption": is_caption, "sentence": ln.strip()[:220]})

    # Bidirectional closure checks (numeric)
    bib_nums_set = set(bib_by_num.keys())
    cited_nums_set = set(cited_nums_order)
    dangling_numbers = sorted(cited_nums_set - bib_nums_set) if bib_nums_set else []
    orphan_bib_numbers = sorted(bib_nums_set - cited_nums_set)
    expected_seq = list(range(1, len(cited_nums_order) + 1))
    non_sequential_order = cited_nums_order != expected_seq and len(cited_nums_order) > 0

    # Compute sequential renumbering map (1..K in exact first-appearance order, keeping captions synced)
    old_to_new: dict[int, int] = {old_n: new_i for new_i, old_n in enumerate(cited_nums_order, start=1)}

    def _renumber_line(line: str) -> str:
        def _repl(match: re.Match[str]) -> str:
            nums = _expand_bracket_group(match.group(1))
            mapped = [old_to_new.get(n, n) for n in nums]
            return _compress_numbers(mapped)
        return RE_BRACKET_GROUP.sub(_repl, line)

    renumbered_body = [_renumber_line(ln) for _, ln in body_lines]
    renumbered_refs: list[str] = []
    for old_n, new_i in sorted(old_to_new.items(), key=lambda x: x[1]):
        if old_n in bib_by_num:
            renumbered_refs.append(f"[{new_i}] {bib_by_num[old_n][0]}")
        else:
            renumbered_refs.append(f"[{new_i}] [DANGLING_CITATION_MISSING_IN_SOURCE_LIST: old_id={old_n}]")

    renumbered_full_text = "\n".join(renumbered_body)
    if renumbered_refs:
        renumbered_full_text += "\n\n## References\n\n" + "\n".join(renumbered_refs) + "\n"

    # NotebookLM #2416 check: multi-marker -> single-source collapse
    collapse_audit: dict[str, Any] = {
        "checked": False,
        "distinct_markers_in_text": len(cited_nums_order) + len({a["marker"] for a in author_year_occurrences}),
        "total_marker_instances": total_marker_instances,
        "distinct_source_objects": len(dedup_records) if dedup_records else len(bib_by_num),
        "marker_to_source_collapse_detected": False,
        "unmapped_markers": [],
    }
    if source_mapping_json and source_mapping_json.is_file():
        sm = json.loads(source_mapping_json.read_text(encoding="utf-8"))
        src_objs = sm.get("sources", []) if isinstance(sm, dict) else sm
        distinct_src_ids = {str(s.get("source_id") or s.get("id") or i) for i, s in enumerate(src_objs) if isinstance(s, dict)}
        collapse_audit["checked"] = True
        collapse_audit["distinct_source_objects"] = len(distinct_src_ids)
        if collapse_audit["distinct_markers_in_text"] >= 3 and len(distinct_src_ids) <= 1:
            collapse_audit["marker_to_source_collapse_detected"] = True
            collapse_audit["unmapped_markers"] = [f"[{n}]" for n in cited_nums_order[1:]]
            collapse_audit["detail"] = (
                f"Detected {collapse_audit['distinct_markers_in_text']} distinct citation markers "
                f"({total_marker_instances} total occurrences) backed by only {len(distinct_src_ids)} structured source object(s) (NotebookLM #2416 failure mode)."
            )
    elif collapse_audit["distinct_markers_in_text"] >= 3 and collapse_audit["distinct_source_objects"] == 1:
        collapse_audit["checked"] = True
        collapse_audit["marker_to_source_collapse_detected"] = True
        collapse_audit["unmapped_markers"] = [f"[{n}]" for n in cited_nums_order[1:]]
        collapse_audit["detail"] = (
            f"Text has {collapse_audit['distinct_markers_in_text']} distinct citation markers but only 1 distinct bibliography/source object."
        )

    # Two-layer verification per citation: Layer 1 (metadata_existence_status) != Layer 2 (claim_support_status)
    two_layer_checks: list[dict[str, Any]] = []
    for old_n in cited_nums_order:
        ref_entry_text = bib_by_num.get(old_n, [""])[0]
        doi_found = normalize_doi(ref_entry_text) if RE_DOI.search(ref_entry_text) else ""
        # Match against dedup library if available
        matched_rec = None
        if doi_found:
            matched_rec = next((r for r in dedup_records if r.get("doi") == doi_found), None)
        if matched_rec is None and ref_entry_text:
            nt = normalize_title(ref_entry_text)
            for r in dedup_records:
                rt = normalize_title(r.get("title", ""))
                if rt and rt in nt:
                    matched_rec = r
                    break

        # Layer 1: metadata existence
        if not ref_entry_text and matched_rec is None:
            meta_status = "missing_from_library"
        elif matched_rec and matched_rec.get("integrity_flag") in ("retracted", "expression_of_concern"):
            meta_status = "retracted_or_eoc"
        elif ref_entry_text and (RE_RETRACT.search(ref_entry_text) or RE_EOC.search(ref_entry_text)):
            meta_status = "retracted_or_eoc"
        elif matched_rec and (not matched_rec.get("doi") or not matched_rec.get("year")):
            meta_status = "incomplete_metadata"
        else:
            meta_status = "verified_in_library"

        # Layer 2: claim support (strictly separate from Layer 1!)
        contexts = cited_num_contexts.get(old_n, [])
        ctx_text = " ".join(c["sentence"] for c in contexts)
        source_text_for_support = ""
        if matched_rec:
            source_text_for_support = excerpt_corpus_by_key.get(matched_rec["id"].lower(), "") or excerpt_corpus_by_key.get(matched_rec.get("doi", "").lower(), "")
        if not source_text_for_support and f"ref_{old_n}" in excerpt_corpus_by_key:
            source_text_for_support = excerpt_corpus_by_key[f"ref_{old_n}"]

        sem_diag: dict[str, Any] = {}
        if meta_status == "missing_from_library":
            support_status = "unverifiable_source_missing"
            support_note = "Citation has no bibliography record; cannot verify claim support."
        elif not source_text_for_support or len(source_text_for_support) < 40:
            support_status = "unverified_fulltext_not_supplied"
            support_note = "Metadata record exists, but source abstract/full-text excerpt was not supplied locally; existence does NOT imply claim support."
        else:
            support_status, support_note, sem_diag = _evaluate_claim_support_semantic(ctx_text, source_text_for_support)

        two_layer_checks.append({
            "original_marker": f"[{old_n}]",
            "renumbered_marker": f"[{old_to_new[old_n]}]",
            "cited_lines": [c["line"] for c in contexts],
            "in_caption": any(c["is_caption"] for c in contexts),
            "matched_record_id": matched_rec["id"] if matched_rec else None,
            "doi": (matched_rec.get("doi") if matched_rec else doi_found) or None,
            "metadata_existence_status": meta_status,
            "claim_support_status": support_status,
            "semantic_diagnostics": sem_diag,
            "support_note": support_note,
        })

    # Author-year matching & Layer 2 semantic verification against deduplicated library + excerpts
    author_year_checks: list[dict[str, Any]] = []
    for occ in author_year_occurrences:
        m = re.match(r"([A-Z][A-Za-z\-']+).*?((?:19|20)\d{2})", occ["marker"])
        fam = m.group(1).lower() if m else ""
        yr = m.group(2) if m else ""
        matches = [
            r for r in dedup_records
            if r.get("year") == yr and any(a["family"].lower() == fam for a in r.get("authors", []))
        ]
        ay_meta = "verified_in_library" if len(matches) == 1 else ("ambiguous_multiple_matches" if len(matches) > 1 else "missing_from_library")
        ay_support = "unverifiable_source_missing"
        ay_note = "No matching record in bibliography."
        ay_diag: dict[str, Any] = {}
        if matches:
            src_txt = ""
            for mr in matches:
                src_txt = excerpt_corpus_by_key.get(mr["id"].lower(), "") or excerpt_corpus_by_key.get(mr.get("doi", "").lower(), "")
                if src_txt and len(src_txt) >= 40:
                    break
            if not src_txt:
                src_txt = excerpt_corpus_by_key.get(f"{fam}{yr}", "") or excerpt_corpus_by_key.get(f"{fam}_{yr}", "")
            if src_txt and len(src_txt) >= 40:
                ay_support, ay_note, ay_diag = _evaluate_claim_support_semantic(occ["sentence"], src_txt)
            else:
                ay_support = "unverified_fulltext_not_supplied"
                ay_note = "Metadata exists in library, but full text/abstract excerpt was not supplied locally."
        author_year_checks.append({
            "marker": occ["marker"],
            "line": occ["line"],
            "in_caption": occ["is_caption"],
            "matched_record_ids": [r["id"] for r in matches],
            "metadata_existence_status": ay_meta,
            "claim_support_status": ay_support,
            "semantic_diagnostics": ay_diag,
            "support_note": ay_note,
        })

    # Strictly separate CITED bibliography records (ordered 1..K matching in-text markers)
    # from UNCITED library records (e.g., Crossref SI components or unused library entries).
    rec_by_id = {r["id"]: r for r in dedup_records}
    cited_bibliography_records: list[dict[str, Any]] = []
    cited_record_ids_set: set[str] = set()

    if cited_nums_order:
        for chk in two_layer_checks:
            m_id = chk.get("matched_record_id")
            if m_id and m_id in rec_by_id:
                rec_copy = dict(rec_by_id[m_id])
                rec_copy["cited_index"] = int(chk["renumbered_marker"].strip("[]"))
                cited_bibliography_records.append(rec_copy)
                cited_record_ids_set.add(m_id)
            else:
                old_num = int(chk["original_marker"].strip("[]"))
                raw_entry = bib_by_num.get(old_num, [""])[0]
                cited_bibliography_records.append({
                    "id": f"in_text_ref_{chk['renumbered_marker'].strip('[]')}",
                    "cited_index": int(chk["renumbered_marker"].strip("[]")),
                    "source_format": "manuscript_fallback",
                    "source_file": "manuscript",
                    "entry_type": "article",
                    "authors": [],
                    "year": "",
                    "title": raw_entry or f"[DANGLING_CITATION: old_id={old_num}]",
                    "alt_title": "",
                    "venue": "",
                    "volume": "",
                    "issue": "",
                    "pages": "",
                    "publisher": "",
                    "doi": chk.get("doi") or "",
                    "url": "",
                    "note": "fallback_from_manuscript_reference_entry",
                    "abstract": "",
                    "is_preprint": False,
                })
    elif author_year_checks:
        for ay in author_year_checks:
            for mid in ay.get("matched_record_ids", []):
                if mid in rec_by_id and mid not in cited_record_ids_set:
                    cited_bibliography_records.append(dict(rec_by_id[mid]))
                    cited_record_ids_set.add(mid)
    else:
        # Library-only mode (no in-text citations): exclude Crossref SI components from main bibliography
        for r in dedup_records:
            if r.get("crossref_type") == "component":
                continue
            cited_bibliography_records.append(dict(r))
            cited_record_ids_set.add(r["id"])

    uncited_library_records: list[dict[str, Any]] = []
    for r in dedup_records:
        if r["id"] not in cited_record_ids_set:
            excl_reason = (
                "uncited_crossref_supplemental_component"
                if r.get("crossref_type") == "component"
                else "present_in_library_but_not_cited_in_manuscript_body"
            )
            uncited_library_records.append({
                "id": r["id"],
                "doi": r.get("doi", ""),
                "crossref_type": r.get("crossref_type", r.get("entry_type", "")),
                "parent_doi": r.get("parent_doi", ""),
                "title": r.get("title", ""),
                "source_file": r.get("source_file", ""),
                "exclusion_reason": excl_reason,
            })

    cited_bib_alignment_ok = (not cited_nums_order) or (len(cited_bibliography_records) == len(cited_nums_order))

    closure_ok = (
        len(dangling_numbers) == 0
        and len(duplicate_bib_numbers) == 0
        and cited_bib_alignment_ok
        and not collapse_audit["marker_to_source_collapse_detected"]
        and not any(c["metadata_existence_status"] == "retracted_or_eoc" for c in two_layer_checks)
    )

    return {
        "closure_ok": closure_ok,
        "cited_bib_alignment_ok": cited_bib_alignment_ok,
        "cited_records_count": len(cited_bibliography_records),
        "uncited_library_records_count": len(uncited_library_records),
        "cited_bibliography_records": cited_bibliography_records,
        "uncited_library_records": uncited_library_records,
        "cited_numbers_original_order": cited_nums_order,
        "renumbering_map": {str(k): v for k, v in old_to_new.items()},
        "non_sequential_order_detected": non_sequential_order,
        "duplicate_bib_numbers": duplicate_bib_numbers,
        "dangling_citations": dangling_numbers,
        "orphan_bib_entries": orphan_bib_numbers,
        "notebooklm_collapse_audit": collapse_audit,
        "two_layer_verification": two_layer_checks,
        "author_year_verification": author_year_checks,
        "renumbered_manuscript_text": renumbered_full_text,
    }


def render_audit_markdown(report: dict[str, Any]) -> str:
    lines = ["# References & Citation Lifecycle Audit Report", ""]
    if report.get("docx_live_field_audit"):
        d = report["docx_live_field_audit"]
        lines.extend([
            "## 1. 静态 OOXML 域结构检查（未运行 Word/Zotero 动态 Refresh）",
            f"- **Verification Scope**: `{d.get('verification_scope', 'static_ooxml_field_inspection_only')}` | **Dynamic Word/Zotero Refresh Verified**: `{d.get('word_zotero_dynamic_refresh_verified', False)}`",
            f"- **Disclaimer**: {d.get('scope_disclaimer', '静态 OOXML 域结构检查（未运行 Word/Zotero 动态 Refresh）。')}",
            f"- **Document**: `{d.get('path')}`",
            f"- **Live Field Status**: `{d.get('live_field_status')}`",
            f"- **Live Citation Fields**: `{d.get('live_citation_field_count', 0)}` | **Live Bibliography Fields**: `{d.get('live_bibliography_field_count', 0)}`",
            f"- **Visible Text Citation Groups**: `{d.get('visible_citation_marker_groups', 0)}` | **Flattened Paragraphs**: `{len(d.get('paragraphs_with_flattened_markers', []))}`",
            "",
        ])
        if d.get("recovery_protocol"):
            lines.append("### Official Safe Migration & Recovery Protocol")
            for step in d["recovery_protocol"]:
                lines.append(f"- {step}")
            lines.append("")

    dd = report["deduplication"]
    lines.extend([
        "## 2. Multi-Format Bibliography Deduplication",
        f"- **Input Records**: `{dd['input_records_count']}` -> **Canonical Unique**: `{dd['deduplicated_count']}` (merged `{dd['merged_duplicates_count']}` duplicates)",
        f"- **Protected Homonymous-Author Pairs**: `{len(dd['protected_homonymous_pairs'])}`",
        "",
    ])
    for m in dd["merge_decisions"]:
        lines.append(f"- **Merged** `{m['kept_or_candidate']}` (`{m['criterion']}`): {m['rationale']}")
    for p in dd["protected_homonymous_pairs"]:
        lines.append(f"- **Protected Distinct** `{p['record_a']['id']}` vs `{p['record_b']['id']}` (shared surname `{p['shared_surnames']}`, year `{p['record_a']['year']}`): {p['rationale']}")

    sy = report["sync_and_verification"]
    lines.extend([
        "",
        "## 3. Bidirectional Sync, Renumbering & Two-Layer Verification",
        f"- **Closure Passed**: `{sy['closure_ok']}`",
        f"- **Non-Sequential Order Detected**: `{sy['non_sequential_order_detected']}` | **Renumbering Map**: `{sy['renumbering_map']}`",
        f"- **Duplicate Numbers**: `{sy['duplicate_bib_numbers']}` | **Dangling**: `{sy['dangling_citations']}` | **Orphans**: `{sy['orphan_bib_entries']}`",
        f"- **Multi-Marker Single-Source Collapse (NotebookLM #2416)**: `{sy['notebooklm_collapse_audit']['marker_to_source_collapse_detected']}`",
        "",
        "| Old | New | Lines | Caption? | Metadata Existence (Layer 1) | Claim Support (Layer 2) | Note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for c in sy["two_layer_verification"]:
        lines.append(
            f"| {c['original_marker']} | {c['renumbered_marker']} | {c['cited_lines']} | {c['in_caption']} | `{c['metadata_existence_status']}` | `{c['claim_support_status']}` | {c['support_note']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline citation lifecycle manager (OOXML, Crossref/JATS/MD/Bib/RIS/CSL dedup, sync, GB/T 7714, APA 7, 2-layer verify).")
    parser.add_argument("--manuscript", default="", help="Manuscript file (.md/.txt/.tex/.docx/.xml/.pdf).")
    parser.add_argument("--docx", default="", help="Optional .docx file specifically for OOXML live-field inspection.")
    parser.add_argument("--input-dir", default="", help="Optional /input directory containing case.json and task.md for dynamic output_contract & citation_style binding.")
    parser.add_argument("--bib", action="append", default=[], help="Bibliography file(s) or directory (.bib/.ris/.json/Crossref/.xml/.md); repeatable.")
    parser.add_argument("--include-manuscript-refs", action="store_true", help="Also parse the manuscript's own References section into the deduplication library.")
    parser.add_argument("--excerpts-dir", default="", help="Optional directory containing source paper excerpts for Layer 2 verification.")
    parser.add_argument("--source-mapping", default="", help="Optional JSON of structured citation source objects (checks NotebookLM #2416 collapse).")
    parser.add_argument("--out-dir", required=True, help="Output directory for audit reports, formatted refs, and cleaned BibTeX.")
    parser.add_argument("--case-id", default="CASE-UNSPECIFIED")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if closure fails or flattened citations are detected.")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    in_dir = Path(args.input_dir) if args.input_dir else (Path(args.manuscript).parent if args.manuscript else None)
    case_contract = load_case_contract(in_dir if in_dir and in_dir.is_dir() else None)
    effective_case_id = case_contract.get("case_id") or args.case_id

    docx_audit: dict[str, Any] | None = None
    manuscript_text = ""
    source_files: list[dict[str, Any]] = []

    docx_target = Path(args.docx) if args.docx else (Path(args.manuscript) if args.manuscript.lower().endswith(".docx") else None)
    if docx_target and docx_target.is_file():
        docx_audit = audit_docx_live_fields(docx_target)
        if docx_audit.get("ok"):
            manuscript_text = docx_audit.get("full_text", "")
            source_files.append({"file": docx_target.name, "role": "docx_manuscript"})

    if args.manuscript and not args.manuscript.lower().endswith(".docx"):
        mp = Path(args.manuscript)
        doc = read_document_text(mp)
        if doc["ok"]:
            manuscript_text = doc["text"]
            source_files.append({"file": mp.name, "role": "manuscript", "extractor": doc.get("meta", {}).get("extractor", doc.get("format"))})

    bib_paths = [Path(b) for b in args.bib if Path(b).exists()]
    for bp in bib_paths:
        source_files.append({"file": bp.name, "role": "bibliography_source"})
    raw_records = load_library_files(bib_paths)
    if args.include_manuscript_refs and manuscript_text:
        ms_name = Path(args.manuscript).name if args.manuscript else "manuscript"
        raw_records.extend(parse_markdown_references(manuscript_text, ms_name))
    dedup = deduplicate_records(raw_records)

    sync_rep = sync_and_verify_manuscript(
        manuscript_text,
        dedup["records"],
        excerpts_dir=Path(args.excerpts_dir) if args.excerpts_dir else None,
        source_mapping_json=Path(args.source_mapping) if args.source_mapping else None,
    )

    cited_records = sync_rep["cited_bibliography_records"]
    uncited_records = sync_rep["uncited_library_records"]

    # Format ONLY cited bibliography records (strictly 1-to-1 with in-text markers; uncited library/component records excluded)
    gbt_list = [format_gbt7714(r, r.get("cited_index", idx)) for idx, r in enumerate(cited_records, start=1)]
    apa_list = [format_apa7(r) for r in cited_records]

    clean_bib_path = out_dir / "cleaned_references.bib"
    clean_bib_path.write_text(export_clean_bibtex(dedup["records"]), encoding="utf-8")

    gbt_path = out_dir / "formatted_references_gbt7714.md"
    gbt_path.write_text("# References (GB/T 7714-2015 — Cited Bibliography Only)\n\n" + "\n".join(x["formatted"] for x in gbt_list) + "\n", encoding="utf-8")

    apa_path = out_dir / "formatted_references_apa7.md"
    apa_path.write_text("# References (APA 7th Edition — Cited Bibliography Only)\n\n" + "\n".join(x["formatted"] for x in apa_list) + "\n", encoding="utf-8")

    renum_path = out_dir / "renumbered_manuscript.md"
    if manuscript_text:
        renum_path.write_text(sync_rep["renumbered_manuscript_text"], encoding="utf-8")

    # Unified gaps aggregation covering BOTH numeric two_layer_verification AND author_year_verification
    gaps: list[dict[str, Any]] = []
    if docx_audit and docx_audit.get("live_field_status") in ("flattened_plain_text", "mixed"):
        gaps.append({"code": "DOCX_FLATTENED_LIVE_FIELDS", "status": docx_audit["live_field_status"]})
    if sync_rep["dangling_citations"]:
        gaps.append({"code": "DANGLING_CITATIONS", "items": sync_rep["dangling_citations"]})
    if sync_rep["duplicate_bib_numbers"]:
        gaps.append({"code": "DUPLICATE_BIB_NUMBERS", "items": sync_rep["duplicate_bib_numbers"]})
    if not sync_rep["cited_bib_alignment_ok"]:
        gaps.append({"code": "CITED_BIBLIOGRAPHY_NUMBERING_MISMATCH", "cited_count": len(cited_records), "in_text_count": len(sync_rep["cited_numbers_original_order"])})
    if sync_rep["notebooklm_collapse_audit"]["marker_to_source_collapse_detected"]:
        gaps.append({"code": "MULTI_MARKER_SINGLE_SOURCE_COLLAPSE", "detail": sync_rep["notebooklm_collapse_audit"].get("detail", "")})

    # Merge numeric + author-year Layer 1 & Layer 2 findings into unified gap lists
    ay_missing = [c["marker"] for c in sync_rep["author_year_verification"] if c["metadata_existence_status"] == "missing_from_library"]
    if ay_missing:
        gaps.append({"code": "AUTHOR_YEAR_MISSING_FROM_LIBRARY", "markers": ay_missing})
    ay_ambiguous = [c["marker"] for c in sync_rep["author_year_verification"] if c["metadata_existence_status"] == "ambiguous_multiple_matches"]
    if ay_ambiguous:
        gaps.append({"code": "AUTHOR_YEAR_AMBIGUOUS_MATCHES", "markers": ay_ambiguous})

    unverified_l2 = [c["original_marker"] for c in sync_rep["two_layer_verification"] if c["claim_support_status"] == "unverified_fulltext_not_supplied"] + [
        c["marker"] for c in sync_rep["author_year_verification"] if c["claim_support_status"] == "unverified_fulltext_not_supplied"
    ]
    if unverified_l2:
        gaps.append({"code": "FULLTEXT_NOT_SUPPLIED_FOR_CLAIM_SUPPORT", "markers": unverified_l2})
    contradicted_l2 = [c["original_marker"] for c in sync_rep["two_layer_verification"] if c["claim_support_status"] == "contradicted_by_supplied_excerpt"] + [
        c["marker"] for c in sync_rep["author_year_verification"] if c["claim_support_status"] == "contradicted_by_supplied_excerpt"
    ]
    if contradicted_l2:
        gaps.append({"code": "CLAIM_POLARITY_CONTRADICTED_BY_SOURCE", "markers": contradicted_l2})
    mismatch_l2 = [c["original_marker"] for c in sync_rep["two_layer_verification"] if c["claim_support_status"] == "mismatch_or_unsupported"] + [
        c["marker"] for c in sync_rep["author_year_verification"] if c["claim_support_status"] == "mismatch_or_unsupported"
    ]
    if mismatch_l2:
        gaps.append({"code": "CLAIM_MISMATCH_OR_UNSUPPORTED", "markers": mismatch_l2})

    gap_summary = {
        "total_gap_categories": len(gaps),
        "numeric_checked_count": len(sync_rep["two_layer_verification"]),
        "author_year_checked_count": len(sync_rep["author_year_verification"]),
        "unverified_fulltext_count": len(unverified_l2),
        "contradicted_count": len(contradicted_l2),
        "mismatch_count": len(mismatch_l2),
        "author_year_missing_count": len(ay_missing),
        "author_year_ambiguous_count": len(ay_ambiguous),
        "uncited_library_records_excluded_count": len(uncited_records),
    }

    full_report = {
        "passed": sync_rep["closure_ok"] and (not docx_audit or docx_audit.get("live_field_status") not in ("flattened_plain_text", "mixed")),
        "case_constraints_applied": case_contract.get("task_constraints", {}),
        "docx_live_field_audit": {k: v for k, v in docx_audit.items() if k != "full_text"} if docx_audit else None,
        "deduplication": dedup,
        "sync_and_verification": {k: v for k, v in sync_rep.items() if k not in ("renumbered_manuscript_text", "cited_bibliography_records")},
        "cited_bibliography_count": len(cited_records),
        "uncited_library_records_count": len(uncited_records),
        "uncited_library_records": uncited_records,
        "gap_summary": gap_summary,
        "gaps": gaps,
        "formatted_gbt7714": gbt_list,
        "formatted_apa7": apa_list,
    }

    audit_json_path = out_dir / "citation_audit.json"
    audit_json_path.write_text(json.dumps(full_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    audit_md_path = out_dir / "citation_audit_report.md"
    audit_md_path.write_text(render_audit_markdown(full_report), encoding="utf-8")

    res = build_writing_result(
        case_id=effective_case_id,
        task_id="academic-citation-management",
        deliverable={
            "audit_report_md": audit_md_path.name,
            "audit_json": audit_json_path.name,
            "cleaned_bibtex": clean_bib_path.name,
            "gbt7714_md": gbt_path.name,
            "apa7_md": apa_path.name,
            "renumbered_manuscript": renum_path.name if manuscript_text else None,
            "cited_bibliography_count": len(cited_records),
            "uncited_library_records_count": len(uncited_records),
        },
        preserved_facts={
            "deduplicated_records_count": dedup["deduplicated_count"],
            "cited_records_count": len(cited_records),
            "uncited_library_records_count": len(uncited_records),
            "protected_homonymous_pairs_count": len(dedup["protected_homonymous_pairs"]),
            "related_component_groups_count": len(dedup.get("related_component_groups", {})),
            "live_field_status": docx_audit.get("live_field_status") if docx_audit else "not_applicable_non_docx",
            "docx_verification_scope": "static_ooxml_field_inspection_only" if docx_audit else "not_applicable",
            "renumbering_map": sync_rep["renumbering_map"],
        },
        evidence_map=sync_rep["two_layer_verification"] + sync_rep["author_year_verification"],
        change_log=[
            {"action": "deduplicate", "criterion": m["criterion"], "items": m["kept_or_candidate"], "rationale": m["rationale"]}
            for m in dedup["merge_decisions"]
        ] + (
            [{"action": "renumber_sequential", "mapping": sync_rep["renumbering_map"], "rationale": "Synchronized in-text citations, captions, and reference list in first-appearance order."}]
            if sync_rep["non_sequential_order_detected"] else []
        ),
        artifacts=[p.name for p in [audit_md_path, audit_json_path, clean_bib_path, gbt_path, apa_path, renum_path] if p.is_file()] + ["result.json"],
        sources=source_files,
        gaps=gaps,
        limitations=[
            "Offline environment cannot query live CrossRef/PubMed APIs; metadata existence is verified strictly against supplied libraries (.bib/.ris/CSL-JSON/Crossref JSON/JATS XML/Markdown) and Layer-2 semantic polarity/condition claim support is reported independently.",
        ],
        verification={
            "closure_ok": sync_rep["closure_ok"],
            "live_field_status": docx_audit.get("live_field_status") if docx_audit else None,
            "merged_duplicates": dedup["merged_duplicates_count"],
            "protected_homonymous_pairs": len(dedup["protected_homonymous_pairs"]),
            "related_component_groups": len(dedup.get("related_component_groups", {})),
            "collapse_detected": sync_rep["notebooklm_collapse_audit"]["marker_to_source_collapse_detected"],
        },
        status="ok" if full_report["passed"] and not gaps else "partial_with_gaps",
        case_contract=case_contract,
    )
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_out = {
        "passed": full_report["passed"],
        "live_field_status": docx_audit.get("live_field_status") if docx_audit else None,
        "input_records": dedup["input_records_count"],
        "deduplicated_records": dedup["deduplicated_count"],
        "merged_duplicates": dedup["merged_duplicates_count"],
        "protected_homonymous_pairs": len(dedup["protected_homonymous_pairs"]),
        "related_component_groups": len(dedup.get("related_component_groups", {})),
        "dangling_citations": len(sync_rep["dangling_citations"]),
        "orphan_bib_entries": len(sync_rep["orphan_bib_entries"]),
        "collapse_detected": sync_rep["notebooklm_collapse_audit"]["marker_to_source_collapse_detected"],
    }
    print(json.dumps(summary_out, ensure_ascii=False))
    if args.strict and not full_report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
