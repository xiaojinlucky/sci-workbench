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
    while txt and txt[-1] in ".,;:!?":
        txt = txt[:-1]
    if len(txt) >= 2 and ((txt[0] == "(" and txt[-1] == ")") or (txt[0] == "[" and txt[-1] == "]")):
        inner = txt[1:-1].strip()
        if inner.startswith(("http://", "https://", "10.", "doi:")):
            txt = inner
    txt = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", txt)
    while txt:
        if txt[-1] in ".,;:!?":
            txt = txt[:-1]
        elif txt[-1] == ")" and txt.count(")") > txt.count("("):
            txt = txt[:-1]
        elif txt[-1] == "]" and txt.count("]") > txt.count("["):
            txt = txt[:-1]
        elif txt[-1] == "}" and txt.count("}") > txt.count("{"):
            txt = txt[:-1]
        else:
            break
    return txt


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


_ABBREV_PROTECT_RE = re.compile(
    r"\b(et\s+al|Fig|Figs|Tab|Table|Eq|Eqs|Ref|Refs|Dr|Mr|Ms|Prof|No|Vol|pp|vs|approx|e\.g|i\.e|cf|resp)\.",
    re.I,
)


def split_sentences_accurate(text: str) -> list[dict[str, Any]]:
    """Split prose into true sentences while protecting scientific abbreviations and decimals."""
    clean = " ".join((text or "").split())
    if not clean:
        return []
    # Protect common scientific abbreviations and decimal numbers with placeholder
    sentinel = "\u241f"
    masked = _ABBREV_PROTECT_RE.sub(lambda m: m.group(0)[:-1] + sentinel, clean)
    masked = re.sub(r"(\d)\.(\d)", rf"\1{sentinel}\2", masked)

    out: list[dict[str, Any]] = []
    start = 0
    idx = 0
    for m in re.finditer(r"(?<=[.!?。！？;；])\s+", masked):
        end = m.start()
        seg = clean[start:end].strip()
        if seg:
            idx += 1
            out.append({"sentence_index": idx, "char_start": start, "char_end": end, "text": seg})
        start = m.end()
    tail = clean[start:].strip()
    if tail:
        idx += 1
        out.append({"sentence_index": idx, "char_start": start, "char_end": len(clean), "text": tail})
    return out


def extract_sentence_anchor_detail(
    text: str,
    pattern: str | re.Pattern[str],
    *,
    max_chars: int = 240,
) -> dict[str, Any]:
    """Return exact matching sentence plus sentence_index and character span for strict auditability."""
    clean = " ".join((text or "").split())
    if not clean:
        return {"quote": "", "sentence_index": None, "char_start": None, "char_end": None, "anchor_granularity": "none"}
    regex = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    sents = split_sentences_accurate(clean)
    for s in sents:
        if regex.search(s["text"]):
            q = s["text"][:max_chars]
            return {
                "quote": q,
                "sentence_index": s["sentence_index"],
                "char_start": s["char_start"],
                "char_end": min(s["char_end"], s["char_start"] + len(q)),
                "anchor_granularity": "sentence",
            }
    m = regex.search(clean)
    if m:
        st = max(0, m.start() - 60)
        en = min(len(clean), m.end() + 140)
        return {
            "quote": clean[st:en][:max_chars],
            "sentence_index": None,
            "char_start": st,
            "char_end": min(en, st + max_chars),
            "anchor_granularity": "span_window",
        }
    return {
        "quote": clean[:max_chars],
        "sentence_index": 1 if sents else None,
        "char_start": 0,
        "char_end": min(len(clean), max_chars),
        "anchor_granularity": "paragraph_head",
    }


def extract_sentence_anchor(text: str, pattern: str | re.Pattern[str], *, max_chars: int = 220) -> str:
    """Extract the exact sentence containing a pattern match for verifiable quote provenance."""
    return str(extract_sentence_anchor_detail(text, pattern, max_chars=max_chars)["quote"])


def stem_token(tok: str) -> str:
    """Lightweight deterministic morphological stemmer for cross-domain scientific English."""
    w = fold_unicode((tok or "").strip().lower())
    if len(w) <= 3 or not w.isascii():
        return w
    for suf, rep in (
        ("ies", "y"),
        ("izations", "iz"),
        ("ization", "iz"),
        ("ations", "at"),
        ("ation", "at"),
        ("ities", "it"),
        ("ments", "ment"),
        ("ingly", ""),
        ("edly", ""),
        ("ing", ""),
        ("ied", "y"),
        ("ed", ""),
        ("es", ""),
        ("s", ""),
    ):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)] + rep
    return w


def clause_term_match(term: str, text: str) -> tuple[bool, str]:
    """Word-boundary & morphological-aware term matcher preventing substring false hits (e.g. 'or' in 'for')."""
    t_norm = fold_unicode((term or "").strip()).lower()
    txt_norm = fold_unicode(text or "").lower()
    if not t_norm or not txt_norm:
        return False, "none"
    # CJK or non-ASCII phrase: direct match
    if re.search(r"[\u4e00-\u9fff]", t_norm):
        return (t_norm in txt_norm), ("exact_cjk" if t_norm in txt_norm else "none")
    # Exact phrase with word boundaries and flexible hyphen/whitespace
    flex_pat = r"\b" + re.sub(r"[\s-]+", r"[\\s-]+", re.escape(t_norm)) + r"\b"
    if re.search(flex_pat, txt_norm):
        return True, "exact_boundary"
    # Morphological stem match (only for tokens >= 4 chars to protect short acronyms like SC/OR/MI)
    t_words = re.findall(r"[a-z0-9]+", t_norm)
    if not t_words or any(len(w) < 4 for w in t_words):
        return False, "none"
    txt_tokens = re.findall(r"[a-z0-9]+", txt_norm)
    txt_stems = [stem_token(w) for w in txt_tokens]
    t_stems = [stem_token(w) for w in t_words]
    if len(t_stems) == 1:
        if t_stems[0] in txt_stems:
            return True, "morphological_stem"
        return False, "none"
    # Multi-word phrase stem window (within 3 tokens order-preserving)
    for i in range(len(txt_stems)):
        if txt_stems[i] == t_stems[0]:
            pos = i
            ok = True
            for ts in t_stems[1:]:
                nxt = next((j for j in range(pos + 1, min(len(txt_stems), pos + 4)) if txt_stems[j] == ts), None)
                if nxt is None:
                    ok = False
                    break
                pos = nxt
            if ok:
                return True, "morphological_phrase"
    return False, "none"


def align_output_contract(patch: dict[str, Any], case_data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Ensure any required fields declared in case.json.output_contract are present without fabricating data."""
    oc = case_data.get("output_contract")
    if not isinstance(oc, dict):
        return patch, []
    req = oc.get("required") or oc.get("required_fields") or []
    if not isinstance(req, list):
        return patch, []
    alias_sources = {
        "results": ("matched_references", "screening_decisions", "appraisals", "patents", "canonical_works"),
        "candidates": ("ranked_evidence_candidates", "screening_decisions", "matched_references", "patents", "canonical_works"),
        "evidence_records": ("matched_references", "screening_decisions", "appraisals", "cross_lingual_query_results", "patents"),
        "omissions": ("missed_references_attributed", "residual_omissions"),
        "summary": ("findings",),
        "status_notes": ("gaps",),
        "sources": ("per_source_coverage", "deduplication_log", "canonical_works", "patents"),
        "uncertainties": ("gaps", "arbitration_queue", "residual_omissions"),
    }
    extra_gaps: list[dict[str, Any]] = []
    for field in req:
        fname = str(field).strip()
        if not fname or fname in patch or fname in ("case_id", "status", "task_id", "schema_version"):
            continue
        mapped = False
        for src_key in alias_sources.get(fname, ()):
            if src_key in patch:
                patch[fname] = patch[src_key]
                mapped = True
                break
        if not mapped:
            patch[fname] = None
            extra_gaps.append({
                "code": "OUTPUT_CONTRACT_FIELD_UNPOPULATED",
                "severity": "info",
                "source": "align_output_contract",
                "message": f"Declared output_contract field '{fname}' had no direct structured counterpart; left null honestly without fabrication.",
            })
    return patch, extra_gaps


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
