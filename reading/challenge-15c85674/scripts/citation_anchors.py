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
    source_plain_nums: set[float] = set()
    for txt in cited_paragraph_texts:
        source_pairs.extend(extract_numeric_unit_pairs(txt))
        for raw_n in re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", txt or ""):
            try:
                source_plain_nums.add(float(raw_n.replace(",", "")))
            except ValueError:
                pass

    errors: list[str] = []
    for cond in conditions:
        cond_pairs = extract_numeric_unit_pairs(str(cond))
        for cv, cu, craw in cond_pairs:
            exact_hit = any(
                su == cu and math.isclose(sv, cv, rel_tol=1e-5, abs_tol=1e-9)
                for sv, su, _ in source_pairs
            )
            if not exact_hit and cu == "n":
                exact_hit = any(math.isclose(sn, cv, rel_tol=1e-5, abs_tol=1e-9) for sn in source_plain_nums)
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


# --- Semantic Claim-Level Grounding & Epistemic Anti-Disguise Helpers (Gates 8 & 9) ---

_STRUCTURAL_NUM_NOISE_RE = re.compile(
    r"(?:\b(?:Table|Tables|Fig\.?|Figs\.?|Figure|Figures|Eq\.?|Equation|Section|Page|Ref\.?|Case|Item)\s*\d+[A-Za-z]?\b|"
    r"(?:表|图|式|第)\s*\d+\s*(?:页|节|章|条)?|"
    r"\b(?:P\d{2,4}(?:_p\d+)?|T\d{1,3}(?:_FN\d+)?|FN\d{1,3}|Q\d+(?:-C\d+)?|FigCap\d+)\b|"
    r"\[(?:\d+(?:\s*[-,–]\s*\d+)*)\]|"
    r"\b(?:CO2|H2O|H2O2|KHCO3|O2|N2|RuO2|IrO2|Ni-Fe|TiO2|SiO2|Al2O3|NH3|CH4|NO2|SO2|pH)\b)",
    re.I,
)

_CLAIM_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|万|亿|千|thousand|million|billion)?(?![A-Za-z0-9_])",
    re.I,
)

_CLAIM_MULT = {
    "": 1.0,
    "%": 1.0,
    "千": 1e3,
    "万": 1e4,
    "亿": 1e8,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
}

_EN_WORD_NUMS: dict[str, float] = {
    "zero": 0.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0,
    "ten": 10.0, "eleven": 11.0, "twelve": 12.0, "thirteen": 13.0, "fourteen": 14.0,
    "fifteen": 15.0, "sixteen": 16.0, "seventeen": 17.0, "eighteen": 18.0, "nineteen": 19.0,
    "twenty": 20.0,
}

_INFERENCE_PREMISE_RE = re.compile(
    r"(?:基于|结合|由.+?(?:推算|推断|得出|计算|可知)|推算|推断|综合|差值|相差|高于|低于|换算|推导|由此|据此|说明|表明|"
    r"\b(?:based\s+on|combining|inferred|derived|computed|calculated|difference\s+between|synthesiz(?:ed|ing)|implies|therefore|hence)\b)",
    re.I,
)

_EXPLICIT_INFERENCE_DISGUISE_RE = re.compile(
    r"(?:由此推算|推断得出|自行推断|综合推断|计算差值|相差\s*\d|"
    r"\b(?:inferred\s+from|derived\s+by\s+subtracting|calculated\s+as\s+the\s+difference)\b)",
    re.I,
)

# --- 6-Tuple Joint Binding: (value, unit, metric, entity, comparator, direction) ---

_PER_10K_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<num>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>/\s*10,?000(?:\s*(?:inhabitants|人|名居民|居民))?|/\s*(?:1\s*)?万(?:人|名居民|居民)?)",
    re.I,
)

_ENTITY_GROUP_SPECS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("group:girls", re.compile(r"(?:\bgirls?\b|\bfemales?\b|\bwomen\b|女童|女孩|女性)", re.I)),
    ("group:boys", re.compile(r"(?:\bboys?\b|\bmales?\b|\bmen\b|男童|男孩|男性)", re.I)),
    ("group:control", re.compile(r"(?:\bcontrol(?:\s+group)?\b|\bplacebo\b|对照组|安慰剂组)", re.I)),
    ("group:treatment", re.compile(r"(?:\btreatment(?:\s+group)?\b|\bintervention(?:\s+group)?\b|治疗组|干预组)", re.I)),
)

_METRIC_SPECS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("metric:incidence", re.compile(r"(?:\bincidence(?:\s+rate)?\b|发病率)", re.I)),
    ("metric:aapc", re.compile(r"(?:\baapc\b|\baverage\s+annual\s+percent\s+change\b|年均变化百分比)", re.I)),
    ("metric:prevalence", re.compile(r"(?:\bprevalence\b|\bproportion\b|\bpercentage\b|占比|比例|百分比)", re.I)),
    ("metric:count", re.compile(r"(?:\bcases?\b|\bcount\b|\bn\s*=|病例数|例数|例)", re.I)),
    ("metric:concentration", re.compile(r"(?:\bconcentration\b|\bthreshold\b|浓度|阈值)", re.I)),
    ("metric:cost", re.compile(r"(?:\bcost\b|\bexpense\b|费用|成本)", re.I)),
)

_COMPARATOR_SPECS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cmp:higher_than", re.compile(r"(?:\bhigher\s+than\b|\bgreater\s+than\b|\bmore\s+than\b|\bsuperior\s+to\b|高于|多于|大于|优于)", re.I)),
    ("cmp:lower_than", re.compile(r"(?:\blower\s+than\b|\bless\s+than\b|\binferior\s+to\b|低于|少于|小于|劣于)", re.I)),
    ("cmp:versus", re.compile(r"(?:\bversus\b|\bvs\.?\b|\bcompared\s+(?:to|with)\b|相比|对比|与.+相比)", re.I)),
)

# Direction & negation patterns (stationary/negated checked FIRST to prevent "did not increase" -> "increasing")
_DIR_STATIONARY_OR_NEG_RE = re.compile(
    r"(?:\b(?:stationary|stable|unchanged|flat|no\s+significant(?:\s+(?:increase|decrease|change|trend|difference))?|"
    r"not\s+(?:statistically\s+)?significant(?:ly)?(?:\s+(?:increased|decreased|higher|lower|different))?|"
    r"did\s+not\s+(?:significantly\s+)?(?:increase|decrease|differ|change|improve)|"
    r"no\s+(?:change|difference|trend)|failed\s+to\s+(?:increase|improve|show))\b|"
    r"(?:保持)?平稳|呈平稳趋势|稳定不变|持平|无显著(?:上升|下降|差异|变化|增加|减少)?|"
    r"不显著|未显著(?:上升|下降|增加|减少)|未见(?:明显|显著)?(?:上升|下降|变化|差异)|无统计学意义)",
    re.I,
)
_DIR_INCREASING_RE = re.compile(
    r"(?:\b(?:significantly\s+)?(?:increased|increasing|increases|increase|rose|rising|grew|growing|upward\s+trend)\b|"
    r"(?:显著|明显)?(?:上升|增加|增至|升高|增长|提高)|呈上升趋势)",
    re.I,
)
_DIR_DECREASING_RE = re.compile(
    r"(?:\b(?:significantly\s+)?(?:decreased|decreasing|decreases|decrease|declined|declining|reduced|reducing|fell|dropped|downward\s+trend)\b|"
    r"(?:显著|明显)?(?:下降|减少|降至|降低|下跌)|呈下降趋势)",
    re.I,
)

# Clause splitter that keeps contrasting entity assertions separate inside one paragraph
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:(?<=[。；;!?])\s*|(?<=[a-z0-9\)])\.\s+(?=[A-Z0-9])|"
    r"[，,]\s*(?=(?:而|但|相比之下|相反|同期|whereas|while|conversely|however|in\s+boys|in\s+girls)))",
    re.I,
)


def extract_unit_bound_values(text: str) -> list[tuple[float, str, str]]:
    """Extract (value, canonical_unit, raw_token) including per_10k and all SI/% units."""
    out: list[tuple[float, str, str]] = []
    cleaned = text or ""
    used_spans: list[tuple[int, int]] = []

    for m in _PER_10K_RE.finditer(cleaned):
        raw_num = m.group("num").replace(",", "")
        try:
            out.append((float(raw_num), "per_10k", m.group(0).strip()))
            used_spans.append((m.start(), m.end()))
        except ValueError:
            pass

    if used_spans:
        chars = list(cleaned)
        for s, e in used_spans:
            for i in range(s, e):
                chars[i] = " "
        cleaned = "".join(chars)

    out.extend(extract_numeric_unit_pairs(cleaned))
    return out


def _detect_direction(clause: str) -> str:
    """Classify directional/polarity stance while respecting negation scope."""
    if not clause:
        return "neutral"
    has_stat_neg = bool(_DIR_STATIONARY_OR_NEG_RE.search(clause))
    # Remove stationary/negated spans before testing positive increase/decrease so "did not increase" is never increasing
    stripped = _DIR_STATIONARY_OR_NEG_RE.sub(" ", clause)
    has_inc = bool(_DIR_INCREASING_RE.search(stripped))
    has_dec = bool(_DIR_DECREASING_RE.search(stripped))
    if has_stat_neg and not has_inc and not has_dec:
        return "stationary_or_null"
    if has_inc and not has_dec:
        return "increasing"
    if has_dec and not has_inc:
        return "decreasing"
    if has_stat_neg:
        return "stationary_or_null"
    return "neutral"


def extract_joint_semantic_bindings(text: str) -> list[dict[str, Any]]:
    """Extract clause-scoped (value, unit, metric, entity, comparator, direction) tuples from text."""
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(text or "") if c.strip()]
    if not clauses and (text or "").strip():
        clauses = [(text or "").strip()]
    bindings: list[dict[str, Any]] = []
    for cl in clauses:
        ents = [name for name, pat in _ENTITY_GROUP_SPECS if pat.search(cl)]
        metrics = [name for name, pat in _METRIC_SPECS if pat.search(cl)]
        cmps = [name for name, pat in _COMPARATOR_SPECS if pat.search(cl)]
        direction = _detect_direction(cl)
        unit_vals = extract_unit_bound_values(cl)
        scalar_vals = extract_claim_numeric_cores(cl, include_en_words=False)
        bindings.append({
            "clause": cl,
            "entities": ents,
            "metrics": metrics,
            "comparators": cmps,
            "direction": direction,
            "unit_values": unit_vals,
            "scalar_values": scalar_vals,
        })
    return bindings


def extract_claim_numeric_cores(text: str, *, include_en_words: bool = False) -> list[tuple[float, str, str]]:
    """Extract normalized numeric cores (supporting thousands separators, percentages, and CN/EN magnitudes)."""
    cleaned = _STRUCTURAL_NUM_NOISE_RE.sub(" ", text or "")
    # Normalize Chinese rate-per-10k shorthand ("/万" / "每万") into "/10000"
    cleaned = re.sub(r"(?<!\d)(?:/|每)\s*万", "/10000", cleaned)
    # Normalize "95% CI" / "95%CI" / "IC95%" so CI level itself is consistent
    cleaned = re.sub(r"(?:95%\s*CI|CI\s*95%|IC\s*95%)", " CI_LEVEL ", cleaned, flags=re.I)

    out: list[tuple[float, str, str]] = []
    for m in _CLAIM_NUM_RE.finditer(cleaned):
        raw_num = m.group(1).replace(",", "")
        mag = (m.group(2) or "").lower()
        try:
            val = float(raw_num) * _CLAIM_MULT.get(mag, 1.0)
        except ValueError:
            continue
        kind = "%" if mag == "%" else "scalar"
        out.append((val, kind, m.group(0).strip()))

    if include_en_words:
        for w in re.findall(r"\b[a-z]+\b", cleaned.lower()):
            if w in _EN_WORD_NUMS:
                out.append((_EN_WORD_NUMS[w], "scalar", w))
    return out


def verify_reported_claim_numeric_fidelity(
    claim_text: str,
    verified_quotes: list[str],
) -> tuple[bool, list[str]]:
    """Gate 8: Joint intra-paragraph (value, unit, metric, entity, comparator, direction) binding check.

    Blocks:
      1. Numeric tampering (value absent from verified quote)
      2. Same-number different-unit substitution (e.g., 18.2% vs 18.2 mg/L)
      3. Same-paragraph cross-group entity misbinding (e.g., assigning girls' rates to boys)
      4. Negation / trend-direction reversal (e.g., reporting 'stationary'/'not significant' as 'increasing')
    """
    if not claim_text or not verified_quotes:
        return True, []
    quote_blob = " \n ".join(verified_quotes)
    quote_raw_norm = " ".join(quote_blob.split())
    is_pipe_table_quote = "|" in quote_blob

    errors: list[str] = []

    # 1) Value-unit joint verification (catches same-number different-unit like 18.2% vs 18.2 mg/L)
    claim_uvals = extract_unit_bound_values(claim_text)
    quote_uvals = extract_unit_bound_values(quote_blob)
    quote_cores = extract_claim_numeric_cores(quote_blob, include_en_words=True)

    for cv, cu, craw in claim_uvals:
        if cu == "n":
            # Sample size n=X may appear as plain count in a table cell
            n_hit = any(
                (qu == "n" and math.isclose(qv, cv, rel_tol=1e-4, abs_tol=1e-9))
                for qv, qu, _ in quote_uvals
            ) or any(math.isclose(qv, cv, rel_tol=1e-4, abs_tol=1e-9) for qv, _, _ in quote_cores)
            if not n_hit:
                errors.append(
                    f"CLAIM_NUMERIC_NOT_IN_QUOTE: reported claim states {craw!r} ({cv:g}) "
                    f"which does not appear in its verified quote(s)"
                )
            continue

        exact_uv = any(
            qu == cu and math.isclose(qv, cv, rel_tol=1e-4, abs_tol=1e-9)
            for qv, qu, _ in quote_uvals
        )
        if exact_uv:
            continue

        # Pipe-delimited table rows often omit '%' in individual cells when column header carries '%'
        if cu == "%" and is_pipe_table_quote:
            if any(math.isclose(qv, cv, rel_tol=1e-4, abs_tol=1e-9) for qv, _, _ in quote_cores):
                continue

        same_val_diff_unit = [
            (qv, qu, qraw)
            for qv, qu, qraw in quote_uvals
            if math.isclose(qv, cv, rel_tol=1e-4, abs_tol=1e-9) and qu != cu
        ]
        if same_val_diff_unit:
            errors.append(
                f"CLAIM_UNIT_MISMATCH: reported claim binds {cv:g} to unit '{cu}' ({craw!r}), "
                f"but verified quote binds {cv:g} to unit '{same_val_diff_unit[0][1]}' ({same_val_diff_unit[0][2]!r})"
            )
        else:
            # Check if the number exists bare in prose with no unit (e.g. claiming '18.2 mg/L' when quote only says '18.2' or '18.2%')
            bare_val_exists = any(math.isclose(qv, cv, rel_tol=1e-4, abs_tol=1e-9) for qv, _, _ in quote_cores)
            if bare_val_exists:
                errors.append(
                    f"CLAIM_UNIT_MISMATCH: reported claim attaches ungrounded unit '{cu}' ({craw!r}) "
                    f"to value {cv:g} which does not carry unit '{cu}' in verified quote(s)"
                )
            else:
                errors.append(
                    f"CLAIM_NUMERIC_NOT_IN_QUOTE: reported claim states {craw!r} ({cv:g}) "
                    f"which does not appear in its verified quote(s)"
                )

    # 2) Scalar numeric-core verification (all remaining plain numbers)
    claim_cores = extract_claim_numeric_cores(claim_text, include_en_words=False)
    for cv, ck, craw in claim_cores:
        hit = any(
            math.isclose(cv, qv, rel_tol=1e-4, abs_tol=1e-9)
            and (ck == qk or (is_pipe_table_quote and (ck == "%" or qk == "%")))
            for qv, qk, _ in quote_cores
        )
        if not hit:
            raw_digits = craw.replace(",", "").rstrip("%")
            if raw_digits and re.search(rf"(?<![0-9.]){re.escape(raw_digits)}(?![0-9.])", quote_raw_norm.replace(",", "")):
                hit = True
        if not hit:
            err_msg = (
                f"CLAIM_NUMERIC_NOT_IN_QUOTE: reported claim states {craw!r} ({cv:g}) "
                f"which does not appear in its verified quote(s)"
            )
            if err_msg not in errors:
                errors.append(err_msg)

    # 3) Intra-paragraph Entity/Group & Direction/Negation joint binding
    claim_bindings = extract_joint_semantic_bindings(claim_text)
    quote_bindings = extract_joint_semantic_bindings(quote_blob)
    quote_entities_all = {e for qb in quote_bindings for e in qb["entities"]}

    # Only enforce entity-value/direction cross-binding when quote explicitly names entity groups (not unlabeled pipe rows)
    if quote_entities_all:
        # Map entity -> all values and directions attested in quote clauses mentioning that entity
        ent_to_qvals: dict[str, set[float]] = {}
        ent_to_qdirs: dict[str, set[str]] = {}
        for qb in quote_bindings:
            q_nums = {v for v, _, _ in qb["scalar_values"]} | {v for v, _, _ in qb["unit_values"]}
            for ent in qb["entities"]:
                ent_to_qvals.setdefault(ent, set()).update(q_nums)
                if qb["direction"] != "neutral":
                    ent_to_qdirs.setdefault(ent, set()).add(qb["direction"])

        # Years (1900-2099) and denominator constants (10000) can legitimately appear across both groups
        shared_context_nums = {10000.0, 1000.0, 100.0}

        for cb in claim_bindings:
            c_ents = cb["entities"]
            # Check single-entity claim clauses so paired clauses (e.g. "girls X, whereas boys Y") bind cleanly per side
            if len(c_ents) == 1:
                ent = c_ents[0]
                c_nums = [
                    (v, raw)
                    for v, _, raw in (cb["unit_values"] or cb["scalar_values"])
                    if not (1900.0 <= v <= 2099.0 and float(int(v)) == v) and v not in shared_context_nums
                ]
                ent_qvals = ent_to_qvals.get(ent, set())
                for cv, craw in c_nums:
                    in_own = any(math.isclose(cv, qv, rel_tol=1e-4, abs_tol=1e-9) for qv in ent_qvals)
                    if in_own:
                        continue
                    other_holders = [
                        oe for oe, ovals in ent_to_qvals.items()
                        if oe != ent and any(math.isclose(cv, qv, rel_tol=1e-4, abs_tol=1e-9) for qv in ovals)
                    ]
                    if other_holders:
                        errors.append(
                            f"CLAIM_ENTITY_GROUP_MISBINDING: reported claim binds {craw!r} to {ent!r}, "
                            f"but verified quote binds {craw!r} to {other_holders[0]!r}"
                        )

                # Direction / negation polarity check for this entity
                c_dir = cb["direction"]
                q_dirs = ent_to_qdirs.get(ent, set())
                if c_dir != "neutral" and q_dirs and c_dir not in q_dirs:
                    errors.append(
                        f"CLAIM_DIRECTION_POLARITY_REVERSAL: reported claim asserts direction {c_dir!r} "
                        f"for {ent!r}, conflicting with verified quote direction {sorted(q_dirs)}"
                    )
    else:
        # Single-subject (no contrasting gender/arm group): still verify overall claim direction vs quote direction
        c_dirs = {cb["direction"] for cb in claim_bindings if cb["direction"] != "neutral"}
        q_dirs = {qb["direction"] for qb in quote_bindings if qb["direction"] != "neutral"}
        if len(c_dirs) == 1 and len(q_dirs) == 1:
            cd = next(iter(c_dirs))
            qd = next(iter(q_dirs))
            if cd != qd:
                errors.append(
                    f"CLAIM_DIRECTION_POLARITY_REVERSAL: reported claim asserts direction {cd!r}, "
                    f"conflicting with verified quote direction {qd!r}"
                )

    return len(errors) == 0, errors


def extract_shortest_verbatim_quote(
    paragraph_text: str,
    query_text: str,
    *,
    max_len: int = 240,
) -> str:
    """Draft the shortest verbatim sentence or table row inside paragraph_text that answers query_text."""
    raw = (paragraph_text or "").strip()
    if not raw:
        return ""

    # Candidate units: preserve exact line breaks for pipe-delimited tables, else split into verbatim sentences
    if "|" in raw and "\n" in raw:
        units = [ln.strip() for ln in raw.splitlines() if ln.strip() and not re.fullmatch(r"[\|\-\s:]+", ln.strip())]
    else:
        norm_single = " ".join(raw.split())
        units = [u.strip() for u in re.split(r"(?<=[.!?。！？])\s+", norm_single) if len(u.strip()) >= 8]
        if not units:
            units = [norm_single]

    # Build query token set (EN words + digits + CN 2-grams + bilingual domain cues)
    q_low = (query_text or "").lower()
    q_tokens: set[str] = set(re.findall(r"[a-z0-9][a-z0-9._/-]*", q_low))
    # General bilingual scientific concept bridge (domain-agnostic, zero dataset hardcoding)
    bridge = {
        "总病例": ("cases", "total", "patients", "cohort", "subjects"),
        "病例数": ("cases", "patients", "count", "number"),
        "毒物": ("agent", "toxic", "substance", "drug", "medicines"),
        "药物": ("medicines", "medicine", "drug", "drugs", "pharmacological"),
        "情形": ("circumstance", "accidental", "cause", "etiology", "mechanism"),
        "女童": ("girls", "female", "females", "women"),
        "男童": ("boys", "male", "males", "men"),
        "趋势": ("increased", "decreased", "stationary", "trend", "change", "aapc"),
        "发病率": ("incidence", "rate", "inhabitants", "per"),
        "术语": ("glossary", "abbreviation", "acronym", "system", "definition"),
        "全称": ("system", "drug", "definition", "plan", "procedures", "acronym"),
        "血药浓度": ("concentration", "plasma", "serum", "threshold", "variables"),
        "医疗费用": ("cost", "expense", "economic", "hospitalization", "variables"),
        "对照组": ("control", "placebo", "baseline"),
        "治疗组": ("treatment", "intervention", "treated"),
        "显著": ("significant", "significantly", "p<", "p="),
    }
    # Extract numeric ranges (e.g., 0–4 / 0-4) dynamically as query cues ("0 to 4", "0-4")
    for m_rng in re.finditer(r"\b(\d+)\s*[–\-—]\s*(\d+)\b", query_text or ""):
        a, b = m_rng.group(1), m_rng.group(2)
        q_tokens.update({f"{a}-{b}", f"{a} to {b}", a, b})
    for zh_k, en_vals in bridge.items():
        if zh_k in (query_text or ""):
            q_tokens.update(en_vals)

    best_unit = units[0]
    best_score = -1.0
    for u in units:
        if not verify_exact_quote(u, raw):
            continue
        ul = u.lower()
        u_toks = set(re.findall(r"[a-z0-9][a-z0-9._/-]*", ul))
        overlap = sum(1 for t in q_tokens if t in u_toks or (len(t) >= 3 and t in ul))
        # Prefer concise verbatim sentences that directly contain the queried facts/numbers
        num_bonus = 0.5 if re.search(r"\d", u) else 0.0
        len_penalty = max(0, len(u) - max_len) / 400.0
        score = overlap * 2.0 + num_bonus - len_penalty
        if score > best_score:
            best_score = score
            best_unit = u

    if len(best_unit) <= max_len:
        return best_unit
    # Trim on word boundary while preserving verbatim match
    cut = best_unit[:max_len]
    cand = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return cand if verify_exact_quote(cand, raw) else best_unit[:max_len]


def verify_inferred_claim_premise(
    claim: dict[str, Any],
    verified_quotes_count: int,
) -> tuple[bool, list[str]]:
    """Gate 9: Verify inferred claims provide an explicit reasoning premise and grounded support, and prevent reported/inferred disguise."""
    kind = str(claim.get("kind") or "")
    text = str(claim.get("text") or "").strip()
    premise = str(
        claim.get("reasoning_premise")
        or claim.get("inference_basis")
        or claim.get("premise")
        or ""
    ).strip()
    supporting_ids = claim.get("supporting_claim_ids") or []
    errors: list[str] = []

    if kind == "inferred":
        has_premise = len(premise) >= 8 or bool(_INFERENCE_PREMISE_RE.search(text))
        if not has_premise:
            errors.append(
                "INFERRED_MISSING_PREMISE: inferred claim must explain its reasoning premise "
                "(via 'reasoning_premise' field or explicit synthesis cues such as 基于/结合/推算/差值/based on/derived from)"
            )
        if verified_quotes_count == 0 and not supporting_ids:
            errors.append(
                "INFERRED_UNGROUNDED_SUPPORT: inferred claim must attach >=1 verified evidence quote or supporting_claim_ids"
            )
    elif kind == "reported":
        if _EXPLICIT_INFERENCE_DISGUISE_RE.search(text):
            errors.append(
                "INFERENCE_DISGUISED_AS_REPORTED: claim text explicitly describes an inference/calculation "
                "but is tagged kind='reported'; change kind to 'inferred' with reasoning_premise"
            )
    return len(errors) == 0, errors
