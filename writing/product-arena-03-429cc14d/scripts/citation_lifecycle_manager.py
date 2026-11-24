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
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from writing_io_and_invariants import (  # noqa: E402
    build_writing_result,
    inspect_docx_ooxml,
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


def parse_csl_json(text: str, source_file: str) -> list[dict[str, Any]]:
    """CSL-JSON bibliography parser."""
    data = json.loads(text)
    items = data if isinstance(data, list) else data.get("references", data.get("items", []))
    records: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
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
            if dp:
                year = str(dp[0])[:4]
        elif item.get("year"):
            year = str(item["year"])[:4]
        venue = str(item.get("container-title") or item.get("journal") or item.get("publisher") or "")
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


def load_library_files(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for p in paths:
        if not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        suf = p.suffix.lower()
        if suf == ".bib":
            records.extend(parse_bibtex(txt, p.name))
        elif suf == ".ris":
            records.extend(parse_ris(txt, p.name))
        elif suf == ".json":
            records.extend(parse_csl_json(txt, p.name))
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
    """Merge duplicates with explainable criteria while protecting distinct papers by homonymous authors."""
    clusters: list[list[dict[str, Any]]] = []
    merge_decisions: list[dict[str, Any]] = []
    protected_homonymous_pairs: list[dict[str, Any]] = []

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

            # Criterion 2: High title similarity (>=0.90) + author overlap (including preprint vs formal published or bilingual alt title)
            if t_match and (shared_authors or not (s1 and s2)):
                # Distinguish preprint-vs-published vs cross-db export vs bilingual
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
        # Backfill any missing fields (e.g., abstract, alt_title, pages) from losers without overwriting winner
        for loser in cluster_sorted[1:]:
            for field in ("alt_title", "abstract", "pages", "volume", "issue", "url", "publisher"):
                if not winner.get(field) and loser.get(field):
                    winner[field] = loser[field]
            if loser.get("is_preprint") and loser.get("doi") and loser["doi"] != winner.get("doi"):
                winner["preprint_doi"] = loser["doi"]
        winner["merged_from"] = merged_ids
        # Integrity flags (retraction / Expression of Concern)
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
    excerpt_corpus_by_key: dict[str, str] = {}
    for rec in dedup_records:
        blob = " ".join(filter(None, [rec.get("title", ""), rec.get("abstract", ""), rec.get("note", "")])).strip()
        if blob:
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

        if meta_status == "missing_from_library":
            support_status = "unverifiable_source_missing"
            support_note = "Citation has no bibliography record; cannot verify claim support."
        elif not source_text_for_support or len(source_text_for_support) < 40:
            support_status = "unverified_fulltext_not_supplied"
            support_note = "Metadata record exists, but source abstract/full-text excerpt was not supplied locally; existence does NOT imply claim support."
        else:
            ck = _tokenize_keywords(ctx_text)
            sk = _tokenize_keywords(source_text_for_support)
            overlap = len(ck & sk)
            ratio = overlap / max(1, len(ck))
            if ratio >= 0.35 and overlap >= 2:
                support_status = "supported_by_supplied_excerpt"
                support_note = f"Lexical/topical alignment verified against supplied source text ({overlap}/{len(ck)} claim terms matched)."
            elif ratio >= 0.15 and overlap >= 1:
                support_status = "partially_supported"
                support_note = f"Partial alignment ({overlap}/{len(ck)} terms); manual check required for scope/strength match."
            else:
                support_status = "mismatch_or_unsupported"
                support_note = f"Source exists in library, but supplied source text shows minimal topical overlap ({overlap}/{len(ck)}) with citing claim."

        two_layer_checks.append({
            "original_marker": f"[{old_n}]",
            "renumbered_marker": f"[{old_to_new[old_n]}]",
            "cited_lines": [c["line"] for c in contexts],
            "in_caption": any(c["is_caption"] for c in contexts),
            "matched_record_id": matched_rec["id"] if matched_rec else None,
            "doi": (matched_rec.get("doi") if matched_rec else doi_found) or None,
            "metadata_existence_status": meta_status,
            "claim_support_status": support_status,
            "support_note": support_note,
        })

    # Author-year matching against deduplicated library
    author_year_checks: list[dict[str, Any]] = []
    for occ in author_year_occurrences:
        m = re.match(r"([A-Z][A-Za-z\-']+).*?((?:19|20)\d{2})", occ["marker"])
        fam = m.group(1).lower() if m else ""
        yr = m.group(2) if m else ""
        matches = [
            r for r in dedup_records
            if r.get("year") == yr and any(a["family"].lower() == fam for a in r.get("authors", []))
        ]
        author_year_checks.append({
            "marker": occ["marker"],
            "line": occ["line"],
            "in_caption": occ["is_caption"],
            "matched_record_ids": [r["id"] for r in matches],
            "metadata_existence_status": "verified_in_library" if len(matches) == 1 else ("ambiguous_multiple_matches" if len(matches) > 1 else "missing_from_library"),
            "claim_support_status": "unverified_fulltext_not_supplied" if matches else "unverifiable_source_missing",
        })

    closure_ok = (
        len(dangling_numbers) == 0
        and len(duplicate_bib_numbers) == 0
        and not collapse_audit["marker_to_source_collapse_detected"]
        and not any(c["metadata_existence_status"] == "retracted_or_eoc" for c in two_layer_checks)
    )

    return {
        "closure_ok": closure_ok,
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
            "## 1. OOXML Live Citation Field Audit (Word / Google Docs Round-Trip)",
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
    parser = argparse.ArgumentParser(description="Offline citation lifecycle manager (OOXML, sync, dedup, GB/T 7714, APA 7, 2-layer verify).")
    parser.add_argument("--manuscript", default="", help="Manuscript file (.md/.txt/.tex/.docx).")
    parser.add_argument("--docx", default="", help="Optional .docx file specifically for OOXML live-field inspection.")
    parser.add_argument("--bib", action="append", default=[], help="Bibliography file(s) (.bib/.ris/.json); repeatable.")
    parser.add_argument("--excerpts-dir", default="", help="Optional directory containing source paper excerpts for Layer 2 verification.")
    parser.add_argument("--source-mapping", default="", help="Optional JSON of structured citation source objects (checks NotebookLM #2416 collapse).")
    parser.add_argument("--out-dir", required=True, help="Output directory for audit reports, formatted refs, and cleaned BibTeX.")
    parser.add_argument("--case-id", default="CASE-UNSPECIFIED")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if closure fails or flattened citations are detected.")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
            source_files.append({"file": mp.name, "role": "manuscript"})

    bib_paths = [Path(b) for b in args.bib if Path(b).is_file()]
    for bp in bib_paths:
        source_files.append({"file": bp.name, "role": "bibliography_source"})
    raw_records = load_library_files(bib_paths)
    dedup = deduplicate_records(raw_records)

    sync_rep = sync_and_verify_manuscript(
        manuscript_text,
        dedup["records"],
        excerpts_dir=Path(args.excerpts_dir) if args.excerpts_dir else None,
        source_mapping_json=Path(args.source_mapping) if args.source_mapping else None,
    )

    gbt_list = [format_gbt7714(r, idx) for idx, r in enumerate(dedup["records"], start=1)]
    apa_list = [format_apa7(r) for r in dedup["records"]]

    # Write formatted artifacts
    clean_bib_path = out_dir / "cleaned_references.bib"
    clean_bib_path.write_text(export_clean_bibtex(dedup["records"]), encoding="utf-8")

    gbt_path = out_dir / "formatted_references_gbt7714.md"
    gbt_path.write_text("# References (GB/T 7714-2015)\n\n" + "\n".join(x["formatted"] for x in gbt_list) + "\n", encoding="utf-8")

    apa_path = out_dir / "formatted_references_apa7.md"
    apa_path.write_text("# References (APA 7th Edition)\n\n" + "\n".join(x["formatted"] for x in apa_list) + "\n", encoding="utf-8")

    renum_path = out_dir / "renumbered_manuscript.md"
    if manuscript_text:
        renum_path.write_text(sync_rep["renumbered_manuscript_text"], encoding="utf-8")

    full_report = {
        "passed": sync_rep["closure_ok"] and (not docx_audit or docx_audit.get("live_field_status") not in ("flattened_plain_text", "mixed")),
        "docx_live_field_audit": {k: v for k, v in docx_audit.items() if k != "full_text"} if docx_audit else None,
        "deduplication": dedup,
        "sync_and_verification": {k: v for k, v in sync_rep.items() if k != "renumbered_manuscript_text"},
        "formatted_gbt7714": gbt_list,
        "formatted_apa7": apa_list,
    }

    audit_json_path = out_dir / "citation_audit.json"
    audit_json_path.write_text(json.dumps(full_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    audit_md_path = out_dir / "citation_audit_report.md"
    audit_md_path.write_text(render_audit_markdown(full_report), encoding="utf-8")

    gaps: list[dict[str, Any]] = []
    if docx_audit and docx_audit.get("live_field_status") in ("flattened_plain_text", "mixed"):
        gaps.append({"code": "DOCX_FLATTENED_LIVE_FIELDS", "status": docx_audit["live_field_status"]})
    if sync_rep["dangling_citations"]:
        gaps.append({"code": "DANGLING_CITATIONS", "items": sync_rep["dangling_citations"]})
    if sync_rep["duplicate_bib_numbers"]:
        gaps.append({"code": "DUPLICATE_BIB_NUMBERS", "items": sync_rep["duplicate_bib_numbers"]})
    if sync_rep["notebooklm_collapse_audit"]["marker_to_source_collapse_detected"]:
        gaps.append({"code": "MULTI_MARKER_SINGLE_SOURCE_COLLAPSE", "detail": sync_rep["notebooklm_collapse_audit"].get("detail", "")})
    unverified_l2 = [c["original_marker"] for c in sync_rep["two_layer_verification"] if c["claim_support_status"] == "unverified_fulltext_not_supplied"]
    if unverified_l2:
        gaps.append({"code": "FULLTEXT_NOT_SUPPLIED_FOR_CLAIM_SUPPORT", "markers": unverified_l2})

    res = build_writing_result(
        case_id=args.case_id,
        task_id="academic-citation-management",
        deliverable={
            "audit_report_md": audit_md_path.name,
            "audit_json": audit_json_path.name,
            "cleaned_bibtex": clean_bib_path.name,
            "gbt7714_md": gbt_path.name,
            "apa7_md": apa_path.name,
            "renumbered_manuscript": renum_path.name if manuscript_text else None,
        },
        preserved_facts={
            "deduplicated_records_count": dedup["deduplicated_count"],
            "protected_homonymous_pairs_count": len(dedup["protected_homonymous_pairs"]),
            "live_field_status": docx_audit.get("live_field_status") if docx_audit else "not_applicable_non_docx",
            "renumbering_map": sync_rep["renumbering_map"],
        },
        evidence_map=sync_rep["two_layer_verification"],
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
            "Offline environment cannot query CrossRef/PubMed live APIs; metadata existence is verified strictly against supplied libraries and full-text claim support is reported separately.",
        ],
        verification={
            "closure_ok": sync_rep["closure_ok"],
            "live_field_status": docx_audit.get("live_field_status") if docx_audit else None,
            "merged_duplicates": dedup["merged_duplicates_count"],
            "protected_homonymous_pairs": len(dedup["protected_homonymous_pairs"]),
            "collapse_detected": sync_rep["notebooklm_collapse_audit"]["marker_to_source_collapse_detected"],
        },
        status="ok" if full_report["passed"] and not gaps else "partial_with_gaps",
    )
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_out = {
        "passed": full_report["passed"],
        "live_field_status": docx_audit.get("live_field_status") if docx_audit else None,
        "input_records": dedup["input_records_count"],
        "deduplicated_records": dedup["deduplicated_count"],
        "merged_duplicates": dedup["merged_duplicates_count"],
        "protected_homonymous_pairs": len(dedup["protected_homonymous_pairs"]),
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
