"""Offline citation extraction, paragraph anchoring, and neutral-missing scoring.

Adapted and stripped for offline Harbor sandboxes from:
- nolainjin/paper-verify (MIT License, Copyright (c) 2026 Duchan Jin):
  extract.py, models.py, score.py, offline.py
- Archie AI protocol (atomic E/M/L/S/I fingerprint alignment)
- Light0305/Light-Science (MIT License, Copyright (c) 2026 Light0305):
  honest uncertainty state machine (never fabricate missing metadata).
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"https?://[^\s\]\"<>]+(?<![.,;:!?])", re.I)),
    ("DOI", re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)),
    ("PMC", re.compile(r"\bPMC\d+\b")),
    ("PMID", re.compile(r"\bPMID:?\s*\d+\b", re.I)),
    ("arXiv", re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?", re.I)),
]

_DOI_IN_URL_RE = re.compile(
    r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)$", re.I
)
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_AUTHOR_RE = re.compile(r"\b([A-Z][A-Za-z]{2,})\b")
_WORD_RE = re.compile(r"[a-z]+")

_AUTHOR_STOPWORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "the", "this", "that", "these", "those", "from", "and", "but", "for", "with",
    "when", "while", "their", "there", "here", "what", "which", "who", "how", "why",
    "our", "his", "her", "its", "however", "although", "because", "during",
    "between", "among", "also", "thus", "hence", "moreover", "furthermore",
    "study", "studies", "table", "figure", "section", "chapter", "data",
    "results", "result", "method", "methods", "see", "note", "abstract",
}

CTX_CHARS = 300
MAX_CONTEXT_CHARS = 2400


class Verdict(str, Enum):
    """Five-state evidence verdict with strict separation of unavailability."""

    MATCH = "Match"
    PARTIAL = "Partial"
    MISMATCH = "Mismatch"
    UNCERTAIN = "Uncertain"
    INACCESSIBLE = "Inaccessible"


_CLAIM_POINTS = {
    Verdict.MATCH: 50,
    Verdict.PARTIAL: 25,
    Verdict.UNCERTAIN: 15,
    Verdict.MISMATCH: 0,
    Verdict.INACCESSIBLE: 10,
}


@dataclass
class Citation:
    id: int
    type: str
    ref: str
    context: str
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocalSourceRecord:
    id: int
    available: bool = False
    title: str = ""
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    source_path: str = ""
    error: str = ""


def _line_index(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _pos_to_line(line_starts: list[int], pos: int) -> int:
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def _trim_ref(ref: str) -> str:
    depth = 0
    for index, char in enumerate(ref):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                ref = ref[:index]
                break
            depth -= 1
    return ref.rstrip(".,;:!?")


def _ref_key(type_name: str, ref: str) -> tuple[str, str]:
    if type_name == "URL":
        try:
            parts = urlsplit(ref)
            return type_name, urlunsplit(
                (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment)
            )
        except ValueError:
            return type_name, ref
    return type_name, ref.lower()


def strip_references(text: str) -> str:
    for _kind, pattern in PATTERNS:
        text = pattern.sub(" ", text)
    return text


def extract_citations(text: str) -> list[Citation]:
    """Extract deduped citations with paragraph-bounded context and 1-based lines."""
    line_starts = _line_index(text)
    paragraph_breaks = list(re.finditer(r"\n\s*\n", text))
    break_starts = [b.start() for b in paragraph_breaks]
    break_ends = [b.end() for b in paragraph_breaks]
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    cid = 0
    url_spans: list[tuple[int, int]] = []

    for type_name, pat in PATTERNS:
        for m in pat.finditer(text):
            if type_name == "URL":
                url_spans.append((m.start(), m.end()))
            elif any(s <= m.start() and m.end() <= e for s, e in url_spans):
                continue
            ref = _trim_ref(m.group(0))
            key = _ref_key(type_name, ref)
            if key in seen:
                continue
            seen.add(key)
            if type_name == "URL":
                doi_m = _DOI_IN_URL_RE.match(ref)
                if doi_m:
                    seen.add(("DOI", _trim_ref(doi_m.group(1)).lower()))
            cid += 1
            start = max(0, m.start() - CTX_CHARS)
            end = min(len(text), m.end() + CTX_CHARS)
            before = bisect_right(break_ends, m.start()) - 1
            after = bisect_left(break_starts, m.end())
            if before >= 0:
                start = max(start, break_ends[before])
            if after < len(break_starts):
                end = min(end, break_starts[after])
            context = text[start:end].replace("\n", " ").strip()
            if len(context) > MAX_CONTEXT_CHARS:
                context = context[:MAX_CONTEXT_CHARS].rstrip() + "..."
            out.append(Citation(id=cid, type=type_name, ref=ref, context=context, line=_pos_to_line(line_starts, m.start())))
    return out


def score_author_year_offline(citation: Citation, source: LocalSourceRecord | None) -> tuple[int, str]:
    """Award neutral partial credit (10/20) when metadata is unavailable offline rather than false 0."""
    if source is None or not source.available:
        return 10, "offline metadata unavailable - neutral credit"
    ctx = strip_references(citation.context)
    ctx_years = set(_YEAR_RE.findall(ctx))
    ctx_authors = [a for a in _AUTHOR_RE.findall(ctx) if a.lower() not in _AUTHOR_STOPWORDS]

    if source.authors or source.year is not None:
        year_ok = source.year is not None and bool(ctx_years) and str(source.year) in ctx_years
        meta_tokens = set(_WORD_RE.findall(" ".join(source.authors).lower()))
        author_ok = any(a.lower() in meta_tokens for a in ctx_authors)
        if year_ok and author_ok:
            return 20, "author+year match (local metadata)"
        if year_ok or author_ok:
            return 10, "partial author/year match (local metadata)"
        return 0, "author/year mismatch (local metadata)"

    blob = f"{source.title} {source.abstract}".lower().strip()
    if not blob:
        return 10, "local metadata empty - neutral credit"
    blob_tokens = set(_WORD_RE.findall(blob))
    year_ok = any(y in blob for y in ctx_years) if ctx_years else False
    author_ok = any(a.lower() in blob_tokens for a in ctx_authors)
    if year_ok and author_ok:
        return 20, "author+year match (local text)"
    if year_ok or author_ok:
        return 10, "partial author/year match (local text)"
    return 0, "author/year not found in local text"


def extract_emlsi_fingerprint(text: str) -> dict[str, Any]:
    """Extract 5-dimensional E/M/L/S/I fingerprint (Entity/Magnitude/Logic/Scope/Identifier) from text."""
    negations = re.findall(
        r"\b(?:no|not|never|neither|nor|without|lack(?:s|ed|ing)?|fail(?:s|ed|ing)?|absent|none|cannot|can't|did not|does not|未|没有|无|并非|不显著|未观察到|缺乏)\b",
        text,
        flags=re.I,
    )
    comparators = re.findall(
        r"\b(?:compared\s+to|compared\s+with|versus|vs\.?|than|higher|lower|greater|less|superior|inferior|baseline|control|placebo|相比|对比|优于|低于|高于|对照组|基线)\b",
        text,
        flags=re.I,
    )
    magnitudes = re.findall(
        r"(?:±\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*(?:%|mg/L|mmol|µM|uM|mM|nM|mV|V|mA|cm-2|nm|µm|um|mm|cm|kg|g|mg|µg|h|hr|min|s|ms|K|°C|C|Pa|kPa|MPa|GPa|Hz|kHz|MHz|ppm|ppb|wt%|at%|vol%|fold|times)\b|[pP]\s*[<=>]\s*0\.\d+)",
        text,
    )
    scope_cues = re.findall(
        r"\b(?:at\s+\d+|under\s+[a-z0-9\s-]+|in\s+(?:vitro|vivo|mice|rats|patients|adults|simulated|acidic|alkaline|neutral)|for\s+\d+|over\s+\d+|pH\s*[=:]?\s*\d+(?:\.\d+)?|temperature|在.+?条件下|样本量|仅限|适用于|室温|高温)\b",
        text,
        flags=re.I,
    )
    entities = re.findall(r"\b(?:[A-Z][A-Za-z0-9-]{1,}|[A-Z]{2,}[0-9]*)\b", strip_references(text))
    entities = [e for e in entities if e.lower() not in _AUTHOR_STOPWORDS]
    identifiers = [f"{c.type}:{c.ref}" for c in extract_citations(text)]
    return {
        "E_entities": sorted(set(entities))[:25],
        "M_magnitudes_stats": sorted(set(m.strip() for m in magnitudes))[:25],
        "L_logic_negations": [n.lower() for n in negations],
        "S_scope_conditions": sorted(set(s.strip() for s in scope_cues))[:20],
        "I_identifiers_comparators": {
            "comparators": sorted(set(c.lower() for c in comparators)),
            "identifiers": identifiers,
        },
    }


def verify_exact_quote(quote: str, source_text: str) -> bool:
    """Verify that quote is grounded in source_text after whitespace normalization."""
    q_norm = " ".join((quote or "").split()).casefold()
    s_norm = " ".join((source_text or "").split()).casefold()
    if len(q_norm) < 6 or not s_norm:
        return False
    return q_norm in s_norm
