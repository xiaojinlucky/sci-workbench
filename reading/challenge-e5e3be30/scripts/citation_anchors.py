"""Offline citation extraction, inline-vs-structured drift audit, secondary-citation & localization detectors,
E/M/L/S/I fingerprinting, and verbatim quote verification (challenge-e5e3be30).

Pure Python standard library (zero third-party dependencies).
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

# Inline citation markers: [1], [2, 3], [4-6], (Ref. 2), (Refs. 1, 3)
_INLINE_BRACKET_RE = re.compile(r"\[(\s*\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*\s*)\]")
_INLINE_REF_PAREN_RE = re.compile(r"\(\s*(?:Refs?\.?|Citation|来源|文献)\s*(\d+(?:\s*[-–,]\s*\d+)*)\s*\)", re.I)

# Page / paragraph locator patterns in cited_ref
_PAGE_LOCATOR_RE = re.compile(
    r"(?:\bpp?\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?|\bpages?\s+(\d+)(?:\s*[-–]\s*(\d+))?|第\s*(\d+)\s*页)",
    re.I,
)
_PARA_LOCATOR_RE = re.compile(
    r"(?:\b(?:para(?:graph)?|§)\.?\s*([A-Za-z0-9_:-]+)|\b([A-Za-z0-9_-]+:(?:p\d+-b\d+|P\d+|b\d+))\b|\b(P\d{2,4})\b)",
    re.I,
)

_SECONDARY_CITATION_RE = re.compile(
    r"(?:\b(?:as\s+(?:shown|reported|demonstrated|noted|described|reviewed)\s+by|according\s+to|previous\s+(?:studies|work|reports|investigations)\s+(?:reported|showed|found|demonstrated|suggested)|prior\s+(?:work|studies)\s+(?:reported|showed|found)|reviewed\s+in|cited\s+in|earlier\s+reports?\s+by)\b|"
    r"\b[A-Z][a-z]+\s+et\s+al\.?\s*(?:\(\s*(?:19|20)\d{2}\s*\)|\[\s*\d+\s*\])\s*(?:reported|showed|found|demonstrated|observed|argued|claimed)|"
    r"既往研究表明|前人研究(?:报道|表明|指出)|已有研究(?:表明|指出)|文献\s*\[\d+\]\s*(?:报道|指出)|据.{1,20}报道)",
    re.I,
)
_PRIMARY_STUDY_RE = re.compile(
    r"(?:\b(?:we\s+(?:found|observed|measured|demonstrated|tested|evaluated|show|report)|our\s+(?:results?|data|experiments?|analysis|cohort|study)\s+(?:show|demonstrate|indicate|confirm|reveal)|in\s+this\s+study|present\s+study|table\s+\d+\s+shows|figure\s+\d+\s+shows)\b|"
    r"本研究(?:发现|表明|测定|证实)|本文(?:实验|结果)(?:表明|显示))",
    re.I,
)
_FIGURE_CAPTION_MISSING_RE = re.compile(
    r"(?:caption\s+(?:missing|unavailable|omitted|not\s+provided)|image[\s-]+only|no\s+caption|figure\s+text\s+unavailable|图注缺失|仅图像|无图注)",
    re.I,
)
_ABSTRACT_ONLY_RE = re.compile(
    r"(?:abstract[\s-]+only|full[\s-]+text\s+(?:unavailable|not\s+provided|paywalled|missing)|仅摘要|未提供全文)",
    re.I,
)

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


def extract_inline_citation_markers(text: str) -> list[int]:
    """Extract expanded numeric inline citation markers like [1], [2, 3], [4-6], (Ref. 2)."""
    ids: list[int] = []

    def _expand(expr: str) -> None:
        for part in expr.split(","):
            p = part.strip()
            if not p:
                continue
            m_rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", p)
            if m_rng:
                a, b = int(m_rng.group(1)), int(m_rng.group(2))
                if 1 <= a <= b <= 500 and (b - a) <= 30:
                    ids.extend(range(a, b + 1))
            elif p.isdigit():
                val = int(p)
                if 1 <= val <= 500:
                    ids.append(val)

    for m in _INLINE_BRACKET_RE.finditer(text or ""):
        _expand(m.group(1))
    for m in _INLINE_REF_PAREN_RE.finditer(text or ""):
        _expand(m.group(1))
    return ids


def audit_inline_vs_structured_citations(
    text: str,
    structured_citations: list[Any] | None,
) -> dict[str, Any]:
    """Audit inline citation marker count/IDs against structured citation objects (catches Issue #2416 drift)."""
    inline_all = extract_inline_citation_markers(text)
    inline_unique = sorted(set(inline_all))
    struct_list = structured_citations if isinstance(structured_citations, list) else []

    struct_ids: list[int] = []
    for idx, item in enumerate(struct_list, start=1):
        if isinstance(item, dict):
            raw_id = item.get("citation_number") or item.get("id") or item.get("citation_id") or item.get("index")
            if isinstance(raw_id, int):
                struct_ids.append(raw_id)
            elif isinstance(raw_id, str) and raw_id.strip().strip("[]").isdigit():
                struct_ids.append(int(raw_id.strip().strip("[]")))
            else:
                struct_ids.append(idx)
        elif isinstance(item, int):
            struct_ids.append(item)
        else:
            struct_ids.append(idx)

    struct_unique = sorted(set(struct_ids))
    missing_in_structured = sorted(set(inline_unique) - set(struct_unique)) if inline_unique else []
    unreferenced_in_text = sorted(set(struct_unique) - set(inline_unique)) if (inline_unique and struct_unique) else []

    # Drift occurs if inline markers exist without matching structured citations, OR structured_citations provided alongside inline markers with count/ID mismatch
    has_structured_field = structured_citations is not None
    drift_detected = False
    if inline_unique and has_structured_field:
        drift_detected = bool(missing_in_structured or unreferenced_in_text or len(inline_unique) != len(struct_list))
    elif inline_unique and not has_structured_field:
        # Inline markers present with no structured citations array at all is tracked as unmapped if caller expects mapping
        drift_detected = False
    elif not inline_unique and has_structured_field and len(struct_list) > 0:
        # Structured citations attached without any inline marker is not an inline drift unless explicitly mismatched
        drift_detected = False

    return {
        "inline_marker_occurrences": len(inline_all),
        "inline_marker_count": len(inline_unique),
        "inline_unique_ids": inline_unique,
        "structured_citation_provided": has_structured_field,
        "structured_citation_count": len(struct_list),
        "structured_unique_ids": struct_unique,
        "missing_in_structured": missing_in_structured,
        "unreferenced_in_text": unreferenced_in_text,
        "drift_detected": drift_detected,
    }


def extract_page_and_paragraph_refs(
    cited_ref: str,
    *,
    cited_page: Any = None,
    cited_paragraph: Any = None,
) -> dict[str, Any]:
    """Parse explicit page numbers and paragraph anchors from cited_ref or dedicated fields."""
    pages: set[int] = set()
    paragraphs: list[str] = []

    if isinstance(cited_page, int) and cited_page > 0:
        pages.add(cited_page)
    elif isinstance(cited_page, str) and cited_page.strip().isdigit():
        pages.add(int(cited_page.strip()))
    elif isinstance(cited_page, list):
        for p in cited_page:
            if isinstance(p, int) and p > 0:
                pages.add(p)

    if isinstance(cited_paragraph, str) and cited_paragraph.strip():
        paragraphs.append(cited_paragraph.strip())
    elif isinstance(cited_paragraph, list):
        paragraphs.extend(str(x).strip() for x in cited_paragraph if str(x).strip())

    for m in _PAGE_LOCATOR_RE.finditer(cited_ref or ""):
        g1, g2, g3, g4, g5 = m.groups()
        if g1:
            p1 = int(g1)
            p2 = int(g2) if g2 else p1
            if 1 <= p1 <= p2 <= 2000 and (p2 - p1) <= 20:
                pages.update(range(p1, p2 + 1))
        elif g3:
            p1 = int(g3)
            p2 = int(g4) if g4 else p1
            if 1 <= p1 <= p2 <= 2000 and (p2 - p1) <= 20:
                pages.update(range(p1, p2 + 1))
        elif g5:
            pages.add(int(g5))

    for m in _PARA_LOCATOR_RE.finditer(cited_ref or ""):
        cand = next((g for g in m.groups() if g), "")
        if cand and cand not in paragraphs:
            paragraphs.append(cand)

    return {"cited_pages": sorted(pages), "cited_paragraphs": paragraphs}


def detect_secondary_citation(paragraph_text: str, section_hint: str = "body") -> dict[str, Any]:
    """Detect whether a passage is a secondary/indirect citation of third-party work vs. primary empirical evidence."""
    sec_hits = [m.group(0).strip() for m in _SECONDARY_CITATION_RE.finditer(paragraph_text or "")]
    pri_hits = [m.group(0).strip() for m in _PRIMARY_STUDY_RE.finditer(paragraph_text or "")]

    if sec_hits and pri_hits:
        attr = "mixed"
        warn = True
    elif sec_hits:
        attr = "secondary_citation"
        warn = True
    elif section_hint == "introduction" and extract_inline_citation_markers(paragraph_text):
        attr = "secondary_citation"
        warn = True
    else:
        attr = "primary_study"
        warn = False

    return {
        "is_secondary_citation": warn,
        "attribution_type": attr,
        "secondary_cues": sec_hits[:6],
        "primary_cues": pri_hits[:6],
    }


def detect_document_localization_status(
    doc_meta: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    *,
    source_file: str = "",
    raw_text: str = "",
) -> dict[str, Any]:
    """Determine whether a corpus document has full text, is abstract-only, has no text layer, or lacks figure captions."""
    explicit_cov = str(doc_meta.get("coverage_status") or doc_meta.get("coverage") or "").strip().lower()
    if explicit_cov in {"abstract_only", "no_text_layer", "figure_caption_missing", "fulltext_available"}:
        return {
            "coverage_status": explicit_cov,
            "localization_insufficient": explicit_cov != "fulltext_available",
            "reason": f"Explicit document metadata coverage_status={explicit_cov}.",
        }
    if doc_meta.get("abstract_only") is True or doc_meta.get("fulltext_available") is False:
        return {
            "coverage_status": "abstract_only",
            "localization_insufficient": True,
            "reason": "Document metadata marks abstract_only / fulltext_available=false.",
        }
    if doc_meta.get("figure_caption_missing") is True or doc_meta.get("image_only_figures") is True:
        return {
            "coverage_status": "figure_caption_missing",
            "localization_insufficient": True,
            "reason": "Document metadata marks figure_caption_missing / image_only_figures.",
        }

    joined = raw_text or "\n".join(p.get("text", "") for p in paragraphs)
    if not paragraphs or len(joined.strip()) < 20:
        return {
            "coverage_status": "no_text_layer",
            "localization_insufficient": True,
            "reason": "No readable text extracted from document.",
        }

    low_name = source_file.casefold()
    if _FIGURE_CAPTION_MISSING_RE.search(joined) or "caption_missing" in low_name or "image_only" in low_name:
        return {
            "coverage_status": "figure_caption_missing",
            "localization_insufficient": True,
            "reason": "Document contains image-only figure reference with missing caption text.",
        }
    if _ABSTRACT_ONLY_RE.search(joined) or "abstract_only" in low_name:
        return {
            "coverage_status": "abstract_only",
            "localization_insufficient": True,
            "reason": "Only abstract is available in corpus; full text was not supplied.",
        }
    return {
        "coverage_status": "fulltext_available",
        "localization_insufficient": False,
        "reason": "Readable document text available.",
    }


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
        r"\b(?:no\s+significant(?:\s+difference|\s+effect|\s+benefit|\s+improvement)?|no|not|never|neither|nor|without|lack(?:s|ed|ing)?|fail(?:s|ed|ing)?|absent|none|cannot|can't|did\s+not|does\s+not|zero\s+evidence|unproven)\b|未观察到|无显著差异|没有|并非|不显著|未|缺乏",
        text,
        flags=re.I,
    )
    comparators = re.findall(
        r"\b(?:compared\s+to|compared\s+with|versus|vs\.?|than|higher|lower|greater|less|superior|inferior|outperform(?:s|ed|ing)?|baseline|control|placebo)\b|相比之下|相比|对比|显著优于|优于|低于|高于|对照组|基线",
        text,
        flags=re.I,
    )
    magnitudes = re.findall(
        r"(?:±\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*(?:%|mg/L|ug/L|mmol|µM|uM|mM|nM|mV|V|mA|cm-2|nm|µm|um|mm|cm|kg|g|mg|µg|days?|months?|years?|h|hr|min|s|ms|K|°C|Pa|kPa|MPa|GPa|Hz|ppm|ppb|wt%|fold|times)\b|[pP]\s*[<=>]\s*0?\.\d+|HR\s*=\s*\d+(?:\.\d+)?|OR\s*=\s*\d+(?:\.\d+)?|95%\s*CI)",
        text,
    )
    scope_cues = re.findall(
        r"\b(?:at\s+\d+[^\s,;.]*|under\s+[a-z0-9\s-]{3,30}|in\s+(?:vitro|vivo|mice|rats|humans?|patients?|adults?|pediatric|elderly|children|pregnant|simulated|acidic|alkaline|neutral|postmarketing|premarketing)|for\s+\d+\s*[a-z]+|over\s+\d+\s*[a-z]+|pH\s*[=:]?\s*\d+(?:\.\d+)?|all\s+tested|only\s+in|late-stage|early-stage)\b|在.{1,15}条件下|样本量|仅限|适用于|临床患者|小鼠|人体|儿童|老年",
        text,
        flags=re.I,
    )
    entities = re.findall(r"\b(?:[A-Z][A-Za-z0-9-]{1,}|[A-Z]{2,}[0-9]*)\b", strip_references(text))
    entities = [e for e in entities if e.lower() not in _AUTHOR_STOPWORDS]
    identifiers = [f"{c.type}:{c.ref}" for c in extract_citations(text)]
    return {
        "E_entities": sorted(set(entities))[:25],
        "M_magnitudes_stats": sorted(set(m.strip() for m in magnitudes))[:25],
        "L_logic_negations": [n.lower().strip() for n in negations],
        "S_scope_conditions": sorted(set(s.strip() for s in scope_cues))[:20],
        "I_identifiers_comparators": {
            "comparators": sorted(set(c.lower().strip() for c in comparators)),
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
