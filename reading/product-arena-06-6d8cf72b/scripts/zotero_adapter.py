"""Lightweight Zotero annotation JSON & BibTeX import/export adapter (zotero-mcp contract).

Pure Python 3.12 standard library. Supports:
- Exporting verified knowledge points and paper-card highlights to `zotero_annotations.json`
  following the Zotero Web API v3 / `zotero-mcp` annotation item schema.
- Importing existing Zotero annotation JSON files and auditing each highlight quote with
  case-sensitive `verify_quote_strict` against the paper corpus.
- Parsing and generating BibTeX entries (kept as strings/JSON fields by default so no
  unsolicited `.bib` files are written unless explicitly requested).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from citation_anchors import verify_quote_strict

_CATEGORY_COLORS: dict[str, str] = {
    "abstract": "#a28ae5",
    "introduction": "#2ea8e5",
    "methods": "#5fb236",
    "results": "#ffd400",
    "discussion": "#ff6666",
    "limitations": "#f19837",
    "conclusion": "#e56eee",
    "backmatter": "#aaaaaa",
}

_PAGE_ANCHOR_RE = re.compile(r"#page=(\d+)(?:&extracted-line=(\d+))?")
_LINE_ANCHOR_RE = re.compile(r"#source-line=(\d+)")
_BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,([\s\S]*?)\n\s*\}", re.M)
_BIB_FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(?:\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}|\"([^\"]*)\"|(\d+))")


def _stable_zotero_key(seed: str) -> str:
    """Generate a deterministic 8-char uppercase Zotero-style item key."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest().upper()
    return digest[:8]


def _extract_page_and_line(anchor: str) -> tuple[str, int, int]:
    m_pg = _PAGE_ANCHOR_RE.search(anchor or "")
    if m_pg:
        pg = int(m_pg.group(1))
        ln = int(m_pg.group(2)) if m_pg.group(2) else 1
        return str(pg), max(0, pg - 1), ln
    m_ln = _LINE_ANCHOR_RE.search(anchor or "")
    if m_ln:
        ln = int(m_ln.group(1))
        return "", 0, ln
    return "", 0, 1


def export_bibtex_string(paper_card: dict[str, Any], case_id: str) -> str:
    """Render a clean BibTeX string from paper_card metadata (in-memory string; no file side effects)."""
    title = str((paper_card.get("title") or {}).get("value") or case_id)
    ident = str((paper_card.get("identifier") or {}).get("value") or "")
    genre = str((paper_card.get("paper_genre") or {}).get("genre") or "article")
    entry_type = "article" if genre in {"research_article", "review", "perspective", "comment"} else "misc"
    cite_key = re.sub(r"[^A-Za-z0-9_:-]+", "_", case_id).strip("_") or "paper_note"

    year_m = re.search(r"\b(19|20)\d{2}\b", title + " " + ident)
    year = year_m.group(0) if year_m else ""

    fields = [f"  title = {{{title}}}"]
    if ident and ident != "Not reported in available source text.":
        if ident.startswith("10."):
            fields.append(f"  doi = {{{ident}}}")
        else:
            fields.append(f"  note = {{Identifier: {ident}}}")
    if year:
        fields.append(f"  year = {{{year}}}")
    fields.append(f"  keywords = {{structured-paper-notes, {genre}}}")
    return f"@{entry_type}{{{cite_key},\n" + ",\n".join(fields) + "\n}"


def parse_bibtex_string(bibtex_text: str) -> list[dict[str, Any]]:
    """Parse BibTeX entries into normalized metadata dictionaries without third-party dependencies."""
    entries: list[dict[str, Any]] = []
    for m in _BIB_ENTRY_RE.finditer(bibtex_text or ""):
        etype = m.group(1).strip().lower()
        cite_key = m.group(2).strip()
        body = m.group(3)
        fields: dict[str, str] = {}
        for fm in _BIB_FIELD_RE.finditer(body):
            k = fm.group(1).strip().lower()
            v = fm.group(2) if fm.group(2) is not None else (fm.group(3) if fm.group(3) is not None else (fm.group(4) or ""))
            fields[k] = " ".join(v.split()).strip()
        entries.append({
            "entry_type": etype,
            "cite_key": cite_key,
            "fields": fields,
        })
    return entries


def export_zotero_annotations_payload(
    *,
    case_id: str,
    paper_card: dict[str, Any],
    knowledge_points: list[dict[str, Any]],
    source_modalities: dict[str, str],
) -> dict[str, Any]:
    """Export verified knowledge points into a `zotero-mcp` compatible annotation JSON bundle."""
    title = str((paper_card.get("title") or {}).get("value") or case_id)
    ident = str((paper_card.get("identifier") or {}).get("value") or "")
    genre = str((paper_card.get("paper_genre") or {}).get("genre") or "unclassified")
    parent_key = _stable_zotero_key(f"{case_id}|{title}|{ident}")

    annotations: list[dict[str, Any]] = []
    for idx, kp in enumerate(knowledge_points, start=1):
        anchor = str(kp.get("anchor") or "")
        page_label, page_idx, line_no = _extract_page_and_line(anchor)
        cat = str(kp.get("category") or "other")
        ann_key = _stable_zotero_key(f"{parent_key}|{kp.get('kp_id', idx)}|{anchor}")
        annotations.append({
            "key": ann_key,
            "parentItem": parent_key,
            "itemType": "annotation",
            "annotationType": "highlight",
            "annotationText": str(kp.get("quote") or ""),
            "annotationComment": f"[{kp.get('attribution', 'author_reported')} | {kp.get('section', cat)}] {kp.get('kp_id', f'kp_{idx:03d}')} (anchor: {anchor})",
            "annotationColor": _CATEGORY_COLORS.get(cat, "#ffd400"),
            "annotationPageLabel": page_label,
            "annotationSortIndex": f"{page_idx:05d}|{line_no:06d}|{idx:05d}",
            "annotationPosition": {
                "pageIndex": page_idx,
                "anchor": anchor,
                "anchor_kind": kp.get("anchor_kind", "source-line"),
            },
            "tags": [
                {"tag": "structured-paper-notes"},
                {"tag": f"section:{cat}"},
                {"tag": f"attribution:{kp.get('attribution', 'author_reported')}"},
            ],
        })

    return {
        "schema": "zotero-mcp-annotations/v1",
        "compatible_contracts": ["zotero-mcp", "zotero-web-api-v3"],
        "case_id": case_id,
        "parent_item": {
            "key": parent_key,
            "title": title,
            "identifier": ident,
            "genre": genre,
            "source_modalities": source_modalities,
        },
        "bibtex_entry": export_bibtex_string(paper_card, case_id),
        "annotation_count": len(annotations),
        "annotations": annotations,
    }


def import_and_verify_zotero_annotations(
    json_path: Path,
    source_full_text: str,
) -> dict[str, Any]:
    """Import Zotero annotation JSON (zotero-mcp / Web API v3) and verify each quote case-sensitively."""
    raw = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(raw, dict):
        items = raw.get("annotations") or raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        data = it.get("data") if isinstance(it.get("data"), dict) else it
        quote = str(data.get("annotationText") or data.get("text") or "").strip()
        comment = str(data.get("annotationComment") or data.get("comment") or "").strip()
        page_label = str(data.get("annotationPageLabel") or data.get("page") or "").strip()
        key = str(it.get("key") or data.get("key") or f"ann_{idx:03d}")
        if not quote:
            rejected.append({"key": key, "reason": "EMPTY_ANNOTATION_TEXT"})
            continue
        chk = verify_quote_strict(quote, source_full_text)
        record = {
            "key": key,
            "annotationText": quote,
            "annotationComment": comment,
            "annotationPageLabel": page_label,
            "verified": chk["ok"],
            "verification_reason": chk["reason"],
            "fidelity_issues": chk["fidelity_issues"],
        }
        if chk["ok"]:
            verified.append(record)
        else:
            rejected.append(record)

    return {
        "source_file": json_path.name,
        "total_imported": len(verified) + len(rejected),
        "verified_count": len(verified),
        "rejected_count": len(rejected),
        "verified": verified,
        "rejected": rejected,
    }
