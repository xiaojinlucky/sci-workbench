"""Streamlined offline citation extraction, verbatim quote grounding, and E/M/L/S/I fingerprinting.

Pure Python 3.12 standard library. Designed for Harbor offline sandboxes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

IDENTIFIER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"https?://[^\s\]\"<>]+(?<![.,;:!?])", re.I)),
    ("DOI", re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)),
    ("PMC", re.compile(r"\bPMC\d+\b")),
    ("PMID", re.compile(r"\bPMID:?\s*\d+\b", re.I)),
    ("arXiv", re.compile(r"\barXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?", re.I)),
]

_DOI_IN_URL_RE = re.compile(
    r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)$", re.I
)

_STOP_ENTITIES = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "the", "this", "that",
    "these", "those", "from", "with", "however", "although", "because",
    "during", "between", "among", "also", "thus", "hence", "moreover",
    "study", "studies", "table", "figure", "section", "data", "results",
    "result", "method", "methods", "abstract", "introduction", "discussion",
    "conclusion", "limitations", "here", "our", "their", "while", "when",
}


@dataclass
class ExtractedIdentifier:
    kind: str
    value: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trim_ref(ref: str) -> str:
    depth = 0
    for idx, ch in enumerate(ref):
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                ref = ref[:idx]
                break
            depth -= 1
    return ref.rstrip(".,;:!?")


def _norm_key(kind: str, ref: str) -> tuple[str, str]:
    if kind == "URL":
        try:
            parts = urlsplit(ref)
            return kind, urlunsplit(
                (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment)
            )
        except ValueError:
            return kind, ref
    return kind, ref.lower()


def extract_identifiers(text: str) -> list[ExtractedIdentifier]:
    """Extract deduplicated DOI/PMID/PMC/arXiv/URL identifiers with 1-based line numbers."""
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def pos_to_line(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    seen: set[tuple[str, str]] = set()
    url_spans: list[tuple[int, int]] = []
    out: list[ExtractedIdentifier] = []

    for kind, pat in IDENTIFIER_PATTERNS:
        for m in pat.finditer(text):
            if kind == "URL":
                url_spans.append((m.start(), m.end()))
            elif any(s <= m.start() and m.end() <= e for s, e in url_spans):
                continue
            val = _trim_ref(m.group(0))
            key = _norm_key(kind, val)
            if key in seen:
                continue
            seen.add(key)
            if kind == "URL":
                dm = _DOI_IN_URL_RE.match(val)
                if dm:
                    seen.add(("DOI", _trim_ref(dm.group(1)).lower()))
            out.append(ExtractedIdentifier(kind=kind, value=val, line=pos_to_line(m.start())))
    return out


def verify_exact_quote(quote: str, source_text: str, min_chars: int = 8) -> bool:
    """Verify quote is grounded verbatim inside source_text after whitespace normalization."""
    q_norm = " ".join((quote or "").split()).casefold()
    s_norm = " ".join((source_text or "").split()).casefold()
    if len(q_norm) < min_chars or not s_norm:
        return False
    return q_norm in s_norm


def extract_emlsi_fingerprint(text: str) -> dict[str, Any]:
    """Extract Entity / Magnitude / Logic-negation / Scope / Identifier cues from a claim."""
    negations = re.findall(
        r"\b(?:no|not|never|neither|nor|without|lack(?:s|ed|ing)?|fail(?:s|ed|ing)?|cannot|did not|does not|未|没有|无|并非|不显著|未观察到|缺乏)\b",
        text,
        flags=re.I,
    )
    comparators = re.findall(
        r"\b(?:compared\s+(?:to|with)|versus|vs\.?|than|higher|lower|greater|less|superior|inferior|baseline|control|placebo|ablation|相比|对比|优于|低于|高于|对照|基线)\b",
        text,
        flags=re.I,
    )
    magnitudes = re.findall(
        r"(?:±\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*(?:%|mg/L|mmol|µM|uM|mM|nM|mV|V|mA|cm-2|mA/cm\^?2|nm|µm|um|mm|cm|kg|g|mg|µg|h|hr|min|s|ms|K|°C|Pa|kPa|MPa|ppm|ppb|wt%|fold)\b|[pP]\s*[<=>]\s*0\.\d+|\bn\s*=\s*\d+)",
        text,
    )
    scope_cues = re.findall(
        r"\b(?:at\s+\d+[^,.;]*|under\s+[a-z0-9\s-]{2,30}|in\s+(?:vitro|vivo|mice|rats|patients|adults|simulated|acidic|alkaline|neutral)|pH\s*[=:]?\s*\d+(?:\.\d+)?|confined\s+to|limited\s+to|仅限|适用于|条件下)\b",
        text,
        flags=re.I,
    )
    entities = [
        e for e in re.findall(r"\b(?:[A-Z][A-Za-z0-9-]{1,}|[A-Z]{2,}[0-9]*)\b", text)
        if e.lower() not in _STOP_ENTITIES
    ]
    return {
        "entities": sorted(set(entities))[:15],
        "magnitudes": sorted(set(m.strip() for m in magnitudes))[:15],
        "negations": sorted(set(n.lower() for n in negations)),
        "scope_cues": sorted(set(s.strip() for s in scope_cues))[:10],
        "comparators": sorted(set(c.lower() for c in comparators)),
    }
