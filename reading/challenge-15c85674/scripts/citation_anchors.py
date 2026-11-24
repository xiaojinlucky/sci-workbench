"""Offline citation extraction, paragraph-scoped quote verification, numeric-unit condition checks,
and cross-section conflict detection for grounded scientific literature Q&A.

Pure Python 3.12 standard library; zero network dependencies.
"""

from __future__ import annotations

import math
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
    "results", "result", "method", "methods", "see", "note", "abstract", "page",
}

CTX_CHARS = 300
MAX_CONTEXT_CHARS = 2400

# Canonical SI & scientific units (normalization only; no silent unit conversion)
QA_UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "%": ("%", "percent", "wt%", "at%", "vol%"),
    "kg": ("kg", "kilogram", "kilograms", "千克", "公斤"),
    "g": ("g", "gram", "grams", "克"),
    "mg": ("mg", "milligram", "milligrams", "毫克"),
    "ug": ("μg", "µg", "ug", "mcg", "microgram", "micrograms", "微克"),
    "ng": ("ng", "nanogram", "nanograms", "纳克"),
    "L": ("L", "liter", "liters", "litre", "litres", "升"),
    "mL": ("mL", "ml", "milliliter", "milliliters", "毫升"),
    "uL": ("μL", "µL", "uL", "ul", "microliter", "microliters", "微升"),
    "m": ("m", "meter", "meters", "米"),
    "cm": ("cm", "centimeter", "centimeters", "厘米"),
    "mm": ("mm", "millimeter", "millimeters", "毫米"),
    "um": ("μm", "µm", "um", "micrometer", "micrometers", "微米"),
    "nm": ("nm", "nanometer", "nanometers", "纳米"),
    "s": ("s", "sec", "second", "seconds", "秒"),
    "ms": ("ms", "millisecond", "milliseconds", "毫秒"),
    "min": ("min", "minute", "minutes", "分钟"),
    "h": ("h", "hr", "hrs", "hour", "hours", "小时"),
    "day": ("day", "days", "天", "日"),
    "V": ("V", "volt", "volts", "伏", "伏特"),
    "mV": ("mV", "millivolt", "millivolts", "毫伏"),
    "mA": ("mA", "milliampere", "毫安"),
    "mM": ("mM", "mmol/L", "毫摩尔"),
    "uM": ("μM", "µM", "uM", "umol/L", "微摩尔"),
    "nM": ("nM", "nmol/L", "纳摩尔"),
    "mg/L": ("mg/L", "mg/l", "毫克/升"),
    "mg/kg": ("mg/kg", "毫克/千克"),
    "ppm": ("ppm",),
    "ppb": ("ppb",),
    "Pa": ("Pa", "帕"),
    "kPa": ("kPa", "千帕"),
    "MPa": ("MPa", "兆帕"),
    "GPa": ("GPa", "吉帕"),
    "C": ("°C", "℃", "摄氏度"),
    "K": ("K", "kelvin", "开尔文"),
    "eV": ("eV", "电子伏特"),
    "Hz": ("Hz", "赫兹"),
    "kHz": ("kHz", "千赫兹"),
    "MHz": ("MHz", "兆赫兹"),
}

_UNIT_MAP: dict[str, str] = {}
_UNIT_MAP_CF: dict[str, str] = {}
for _u_canon, _u_als in QA_UNIT_ALIASES.items():
    for _al in _u_als:
        _UNIT_MAP[_al] = _u_canon
        _UNIT_MAP_CF[_al.casefold()] = _u_canon

_CI_UNITS = {al for al in _UNIT_MAP if len(al) >= 3 and al.isascii() and al.isalpha()}
_UNIT_PAT = "(?:" + "|".join(
    f"(?i:{re.escape(al)})" if al in _CI_UNITS else re.escape(al)
    for al in sorted(_UNIT_MAP, key=len, reverse=True)
) + ")"

NUM_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<num>-?\d+(?:,\d{{3}})*(?:\.\d+)?)\s*(?P<unit>{_UNIT_PAT})(?![A-Za-zµμ/])"
)
PH_RE = re.compile(r"\bpH\s*[=:≈~]?\s*(\d+(?:\.\d+)?)", re.I)
SAMPLE_N_RE = re.compile(r"\bn\s*[=:]\s*(\d+(?:,\d{3})*)", re.I)

POS_POLARITY_RE = re.compile(
    r"\b(?:significantly\s+(?:higher|increased|improved|enhanced|reduced|lower)|statistically\s+significant|effective|outperformed|confirmed)\b",
    re.I,
)
NEG_POLARITY_RE = re.compile(
    r"\b(?:not\s+statistically\s+significant|no\s+significant|did\s+not\s+(?:increase|improve|differ|show)|failed\s+to|no\s+difference|not\s+effective|absent)\b",
    re.I,
)


class Verdict(str, Enum):
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


def extract_numeric_unit_pairs(text: str) -> list[tuple[float, str, str]]:
    """Extract normalized (value, canonical_unit, raw_token) pairs including pH and n= counts."""
    pairs: list[tuple[float, str, str]] = []
    for m in NUM_UNIT_RE.finditer(text or ""):
        raw_num = m.group("num").replace(",", "")
        raw_unit = m.group("unit")
        canon = _UNIT_MAP.get(raw_unit) or _UNIT_MAP_CF.get(raw_unit.casefold())
        if canon is None:
            continue
        try:
            val = float(raw_num)
        except ValueError:
            continue
        pairs.append((val, canon, f"{raw_num} {raw_unit}"))
    for m in PH_RE.finditer(text or ""):
        pairs.append((float(m.group(1)), "pH", f"pH {m.group(1)}"))
    for m in SAMPLE_N_RE.finditer(text or ""):
        pairs.append((float(m.group(1).replace(",", "")), "n", f"n={m.group(1)}"))
    return pairs


def verify_conditions_against_paragraphs(
    conditions: list[str],
    cited_paragraph_texts: list[str],
) -> tuple[bool, list[str]]:
    """Verify every numeric+SI unit pair declared in conditions[] matches cited evidence paragraphs."""
    if not conditions or not cited_paragraph_texts:
        return True, []
    source_pairs: list[tuple[float, str, str]] = []
    for txt in cited_paragraph_texts:
        source_pairs.extend(extract_numeric_unit_pairs(txt))

    errors: list[str] = []
    for cond in conditions:
        cond_pairs = extract_numeric_unit_pairs(str(cond))
        for cv, cu, craw in cond_pairs:
            exact_hit = any(
                su == cu and math.isclose(sv, cv, rel_tol=1e-5, abs_tol=1e-9)
                for sv, su, _ in source_pairs
            )
            if exact_hit:
                continue
            same_val_diff_unit = [
                (sv, su, sraw)
                for sv, su, sraw in source_pairs
                if math.isclose(sv, cv, rel_tol=1e-5, abs_tol=1e-9) and su != cu
            ]
            if same_val_diff_unit:
                errors.append(
                    f"CONDITION_UNIT_DRIFT: condition {cond!r} claims {craw!r} (unit={cu}), "
                    f"but cited paragraph(s) report {same_val_diff_unit[0][2]!r} (unit={same_val_diff_unit[0][1]})"
                )
            else:
                errors.append(
                    f"CONDITION_UNGROUNDED_VALUE: condition {cond!r} states {craw!r} "
                    f"which is not present in the cited evidence paragraphs"
                )
    return len(errors) == 0, errors


def detect_cross_section_conflicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect potential numerical or polarity conflicts across distinct sections/paragraphs."""
    conflicts: list[dict[str, Any]] = []
    n = len(candidates)
    if n < 2:
        return conflicts

    parsed = []
    for c in candidates:
        txt = str(c.get("text") or "")
        ents = set(e.lower() for e in extract_emlsi_fingerprint(txt).get("E_entities", []))
        pairs = extract_numeric_unit_pairs(txt)
        by_unit: dict[str, set[float]] = {}
        for val, u, _ in pairs:
            by_unit.setdefault(u, set()).add(val)
        pos_hit = bool(POS_POLARITY_RE.search(txt))
        neg_hit = bool(NEG_POLARITY_RE.search(txt))
        parsed.append((c, ents, by_unit, pos_hit, neg_hit))

    seen_pairs: set[tuple[str, str]] = set()
    for i in range(n):
        c1, e1, u1, p1, n1 = parsed[i]
        for j in range(i + 1, n):
            c2, e2, u2, p2, n2 = parsed[j]
            pid1, pid2 = str(c1["paragraph_id"]), str(c2["paragraph_id"])
            if pid1 == pid2:
                continue
            key = (min(pid1, pid2), max(pid1, pid2))
            if key in seen_pairs:
                continue
            shared_ents = sorted(e1 & e2)
            shared_units = sorted(set(u1.keys()) & set(u2.keys()))
            sec1, sec2 = str(c1.get("section", "")), str(c2.get("section", ""))

            # 1) Numeric divergence on same unit + shared entity (or cross-section Abstract vs Body/Table)
            for u in shared_units:
                if u in ("n", "pH") and not shared_ents:
                    continue
                v1, v2 = u1[u], u2[u]
                if v1 != v2 and (shared_ents or sec1.lower() != sec2.lower()):
                    seen_pairs.add(key)
                    conflicts.append({
                        "type": "numeric_discrepancy",
                        "unit": u,
                        "paragraph_a": {"paragraph_id": pid1, "section": sec1, "values": sorted(v1)},
                        "paragraph_b": {"paragraph_id": pid2, "section": sec2, "values": sorted(v2)},
                        "shared_entities": shared_ents[:8],
                        "action": "Verify whether these values represent distinct conditions or an internal paper conflict; cite both if conflicting.",
                    })
                    break

            if key in seen_pairs:
                continue

            # 2) Polarity contradiction on shared entity
            if shared_ents and ((p1 and n2 and not n1) or (n1 and p2 and not p1)):
                seen_pairs.add(key)
                conflicts.append({
                    "type": "polarity_discrepancy",
                    "paragraph_a": {"paragraph_id": pid1, "section": sec1, "polarity": "positive" if p1 else "negative"},
                    "paragraph_b": {"paragraph_id": pid2, "section": sec2, "polarity": "positive" if p2 else "negative"},
                    "shared_entities": shared_ents[:8],
                    "action": "Inspect opposing significance/effect statements across sections and cite both anchors.",
                })
    return conflicts


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
    magnitudes = [raw for _, _, raw in extract_numeric_unit_pairs(text)]
    magnitudes.extend(re.findall(r"(?:±\s*\d+(?:\.\d+)?|[pP]\s*[<=>]\s*0\.\d+)", text))
    scope_cues = re.findall(
        r"\b(?:at\s+\d+[^\s,.;]*|under\s+[a-z0-9\s-]+|in\s+(?:vitro|vivo|mice|rats|patients|adults|simulated|acidic|alkaline|neutral)|for\s+\d+\s*[a-z]+|over\s+\d+\s*[a-z]+|pH\s*[=:]?\s*\d+(?:\.\d+)?|temperature|在.+?条件下|样本量|仅限|适用于|室温|高温)\b",
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
    """Verify quote is a verbatim substring of the exact named paragraph after whitespace normalization."""
    q_norm = " ".join((quote or "").split()).casefold()
    s_norm = " ".join((source_text or "").split()).casefold()
    if len(q_norm) < 6 or not s_norm:
        return False
    return q_norm in s_norm
