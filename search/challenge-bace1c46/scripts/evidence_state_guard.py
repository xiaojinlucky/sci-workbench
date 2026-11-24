"""Five-state evidence verification, citation identifier parser, author normalization, and offline checks.

Adapted for offline Harbor sandboxes from:
- nolainjin/paper-verify (MIT License, Copyright (c) 2026 Duchan Jin): extract.py, models.py, score.py
- Light0305/Light-Science (MIT License, Copyright (c) 2026 Light0305): honest UNKNOWN state separation

Five canonical evidence states:
  SUPPORTED | PARTIAL | REFUTED | OUTDATED | UNKNOWN
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Any

VALID_EVIDENCE_STATES = {"SUPPORTED", "PARTIAL", "REFUTED", "OUTDATED", "UNKNOWN"}

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)
ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
PMID_RE = re.compile(r"\bPMID:?\s*(\d+)\b", re.I)
PMC_RE = re.compile(r"\b(PMC\d+)\b", re.I)
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-3]\d)\b")
RETRACTION_CUE_RE = re.compile(
    r"\b(?:retract(?:ed|ion)|withdrawn|corrigendum|erratum|expression\s+of\s+concern)\b|撤稿|勘误|更正",
    re.I,
)


class EvidenceState(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    REFUTED = "REFUTED"
    OUTDATED = "OUTDATED"
    UNKNOWN = "UNKNOWN"


def normalize_doi(raw: str) -> str:
    txt = (raw or "").strip().lower()
    txt = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", txt)
    return txt.rstrip(".,;:!?)]")


def fold_unicode(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_first_author_surname(rec: dict[str, Any]) -> str:
    """Extract normalized first-author surname handling 'Last, First', 'First Last', CJK, and diacritics."""
    authors = rec.get("authors") or rec.get("author") or []
    if isinstance(authors, str):
        authors = [x.strip() for x in re.split(r"[;|]", authors) if x.strip()]
    if not isinstance(authors, list) or not authors:
        return ""
    first = authors[0]
    if isinstance(first, dict):
        first = first.get("family") or first.get("last") or first.get("name") or ""
    name = fold_unicode(str(first).strip())
    if not name:
        return ""
    if re.search(r"[\u4e00-\u9fff]", name):
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", name))
        return cjk[:2] if len(cjk) >= 2 else cjk
    if "," in name:
        name = name.split(",", 1)[0]
    parts = re.findall(r"[a-z0-9]+", name.casefold())
    return parts[-1] if parts else ""


def extract_identifiers(text: str) -> dict[str, list[str]]:
    """Extract normalized DOI, arXiv, PMID, and PMC identifiers without network calls."""
    dois = sorted({normalize_doi(m) for m in DOI_RE.findall(text or "") if normalize_doi(m)})
    arxivs = sorted({m.lower() for m in ARXIV_RE.findall(text or "")})
    pmids = sorted(set(PMID_RE.findall(text or "")))
    pmcs = sorted({m.upper() for m in PMC_RE.findall(text or "")})
    return {"doi": dois, "arxiv": arxivs, "pmid": pmids, "pmc": pmcs}


def extract_sentence_anchor(text: str, pattern: str | re.Pattern[str], *, max_chars: int = 220) -> str:
    """Extract the exact sentence containing a pattern match for verifiable quote provenance."""
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    regex = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    sentences = re.split(r"(?<=[.!?。！？;；])\s+", clean)
    for sent in sentences:
        if regex.search(sent):
            return sent[:max_chars]
    m = regex.search(clean)
    if m:
        start = max(0, m.start() - 60)
        end = min(len(clean), m.end() + 120)
        return clean[start:end][:max_chars]
    return clean[:max_chars]


def verify_quote_grounded(quote: str, source_text: str) -> bool:
    q_norm = " ".join((quote or "").split()).casefold()
    s_norm = " ".join((source_text or "").split()).casefold()
    return len(q_norm) >= 6 and q_norm in s_norm


def cross_check_record_metadata(record: dict[str, Any], corpus_text: str) -> dict[str, Any]:
    """Offline Crossref/EuropePMC/publisher metadata consistency check with neutral UNKNOWN fallback."""
    title = str(record.get("title") or "").strip()
    doi = normalize_doi(str(record.get("doi") or ""))
    year = record.get("year")
    authors = [str(a) for a in (record.get("authors") or [])]
    status_flags: list[str] = []

    raw_blob = " ".join([title, str(record.get("abstract") or ""), str(record.get("status") or ""), str(record.get("note") or "")])
    is_retracted_or_erratum = bool(RETRACTION_CUE_RE.search(raw_blob))
    if is_retracted_or_erratum:
        status_flags.append("retraction_or_erratum_cue")

    corpus_low = (corpus_text or "").lower()
    doi_grounded = bool(doi and corpus_low and doi in corpus_low)
    title_grounded = bool(title and corpus_low and " ".join(title.lower().split())[:40] in " ".join(corpus_low.split()))

    if not corpus_text.strip():
        meta_state = "UNKNOWN"
        note = "No local corpus text available; offline metadata unreachable treated neutrally as UNKNOWN."
    elif doi and not doi_grounded and title_grounded:
        meta_state = "PARTIAL"
        status_flags.append("doi_not_found_in_corpus_but_title_present")
        note = "Title found in supplied records while DOI string is absent or differs."
    elif doi_grounded or title_grounded:
        meta_state = "OUTDATED" if is_retracted_or_erratum else "SUPPORTED"
        note = "Grounded in supplied local metadata/corpus."
    else:
        meta_state = "UNKNOWN"
        note = "Record identifier/title not present in finite snapshot; not fabricated."

    year_val = str(year) if year is not None else ""
    if year_val and title_grounded and year_val not in corpus_low:
        status_flags.append("year_mismatch_risk")

    return {
        "record_id": str(record.get("record_id") or record.get("id") or doi or title[:32] or "unknown"),
        "doi": doi or None,
        "year": year,
        "author_count": len(authors),
        "evidence_state": meta_state,
        "is_retracted_or_erratum": is_retracted_or_erratum,
        "flags": status_flags,
        "note": note,
    }


def classify_five_state_support(
    *,
    has_direct_support: bool,
    has_opposing_evidence: bool,
    missing_critical_context: bool,
    is_superseded_or_retracted: bool,
    source_unavailable: bool,
) -> str:
    """Deterministic 5-state classification guard."""
    if source_unavailable:
        return EvidenceState.UNKNOWN.value
    if is_superseded_or_retracted:
        return EvidenceState.OUTDATED.value
    if has_opposing_evidence and not has_direct_support:
        return EvidenceState.REFUTED.value
    if (has_direct_support and has_opposing_evidence) or (has_direct_support and missing_critical_context):
        return EvidenceState.PARTIAL.value
    if has_direct_support:
        return EvidenceState.SUPPORTED.value
    return EvidenceState.UNKNOWN.value
