"""Streamlined offline citation extraction, case-sensitive verbatim quote grounding,
unit/numeric/negation fidelity checking, and E/M/L/S/I fingerprinting.

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

# Case-sensitive scientific value + unit pattern (distinguishes mM vs mm, mV vs MV, M vs m, etc.)
_CASE_SENSITIVE_QTY_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"\s*"
    r"(%|mg/dL|g/dL|mg/L|g/L|ug/mL|µg/mL|ng/mL|pg/mL|mmol/L|umol/L|µmol/L|mol/L|"
    r"mM|uM|µM|nM|pM|M|mm|cm|nm|um|µm|km|m|"
    r"MV|kV|mV|uV|µV|V|MA|kA|mA|uA|µA|nA|A|MW|kW|mW|W|"
    r"mA/cm\^?2|mA\s*cm\^?-2|kJ/mol|kcal/mol|eV|meV|"
    r"kg|mg|ug|µg|ng|g|ms|us|µs|min|hr|h|s|"
    r"GPa|MPa|kPa|Pa|°C|K|ppm|ppb|wt%|at%|vol%|fold|/10,000|/100,000)"
    r"(?![A-Za-z0-9_])"
)

# Case-sensitive chemical/formula/symbol tokens where case changes physical identity (Co vs CO, No vs NO, etc.)
_CASE_SENSITIVE_SYMBOL_RE = re.compile(
    r"\b(?:CO2|CO|Co|NO2|NO|No|SO2|SO|CS2|CS|Cs|HF|Hf|IN|In|BI|Bi|OS|Os|PB|Pb|MN|Mn|ZN|Zn|CU|Cu|FE|Fe|NI|Ni|PT|Pt|AU|Au|AG|Ag|TI|Ti|SI|Si|AL|Al|MG|Mg|CA|Ca|NA|Na|CL|Cl)\b"
)

_NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?(?![A-Za-z0-9_])")

_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|neither|nor|without|lack(?:s|ed|ing)?|fail(?:s|ed|ing)?|cannot|can't|"
    r"did\s+not|does\s+not|do\s+not|was\s+not|were\s+not|is\s+not|are\s+not|no\s+significant|"
    r"not\s+significant|non-significant|absence\s+of|unable\s+to)\b|"
    r"(?:未观察到|不显著|并非|没有|缺乏|未能|未见|无显著|未|无)",
    re.I,
)


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
                    doi_clean = _trim_ref(dm.group(1))
                    doi_key = ("DOI", doi_clean.lower())
                    if doi_key not in seen:
                        seen.add(doi_key)
                        out.append(ExtractedIdentifier(kind="DOI", value=doi_clean, line=pos_to_line(m.start())))
            out.append(ExtractedIdentifier(kind=kind, value=val, line=pos_to_line(m.start())))
    return out


def normalize_whitespace_preserve_case(text: str) -> tuple[str, list[str]]:
    """Normalize line-break hyphenation and whitespace while strictly preserving character case."""
    ops: list[str] = []
    s = text or ""
    if "\r" in s:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        ops.append("crlf_to_lf")
    if "\u00a0" in s:
        s = s.replace("\u00a0", " ")
        ops.append("nbsp_to_space")
    dehyphen = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", s)
    if dehyphen != s:
        s = dehyphen
        ops.append("dehyphenate_linebreak")
    collapsed = " ".join(s.split())
    if collapsed != s:
        ops.append("collapse_whitespace")
    return collapsed, ops


def check_claim_fidelity(claim: str, source_text: str) -> dict[str, Any]:
    """Verify unit/numeric/chemical-symbol case sensitivity and negation consistency against source_text."""
    c_norm, _ = normalize_whitespace_preserve_case(claim)
    s_norm, _ = normalize_whitespace_preserve_case(source_text)

    issues: list[dict[str, Any]] = []

    # 1. Case-sensitive value + unit checks (blocks mM vs mm, mV vs MV, M vs m, etc.)
    src_qty_units = {(m.group(1).replace(",", ""), m.group(2)) for m in _CASE_SENSITIVE_QTY_UNIT_RE.finditer(s_norm)}
    src_qty_units_ci = {(num, u.casefold()): u for num, u in src_qty_units}
    for m in _CASE_SENSITIVE_QTY_UNIT_RE.finditer(c_norm):
        num_c, unit_c = m.group(1).replace(",", ""), m.group(2)
        if (num_c, unit_c) not in src_qty_units:
            if (num_c, unit_c.casefold()) in src_qty_units_ci:
                issues.append({
                    "code": "UNIT_CASE_MISMATCH",
                    "claim_token": f"{num_c} {unit_c}",
                    "source_token": f"{num_c} {src_qty_units_ci[(num_c, unit_c.casefold())]}",
                })
            else:
                issues.append({
                    "code": "NUMERIC_UNIT_NOT_IN_SOURCE",
                    "claim_token": f"{num_c} {unit_c}",
                })

    # 2. Case-sensitive chemical/formula symbols (blocks Co vs CO, No vs NO, Cs vs CS, etc.)
    src_syms = set(_CASE_SENSITIVE_SYMBOL_RE.findall(s_norm))
    src_syms_ci = {sym.casefold(): sym for sym in src_syms}
    for sym in sorted(set(_CASE_SENSITIVE_SYMBOL_RE.findall(c_norm))):
        if sym not in src_syms:
            if sym.casefold() in src_syms_ci:
                issues.append({
                    "code": "SYMBOL_CASE_MISMATCH",
                    "claim_token": sym,
                    "source_token": src_syms_ci[sym.casefold()],
                })

    # 3. Numeric token consistency (catch fabricated/altered numbers)
    src_nums = {n.replace(",", "") for n in _NUMERIC_TOKEN_RE.findall(s_norm)}
    for num in sorted({n.replace(",", "") for n in _NUMERIC_TOKEN_RE.findall(c_norm)}):
        if num not in src_nums:
            issues.append({
                "code": "NUMERIC_VALUE_MISMATCH",
                "claim_token": num,
            })

    # 4. Negation polarity consistency (detect deleted or inserted negations when comparing aligned quote/claim)
    c_negs = [m.group(0).lower() for m in _NEGATION_RE.finditer(c_norm)]
    s_negs = [m.group(0).lower() for m in _NEGATION_RE.finditer(s_norm)]
    if bool(c_negs) != bool(s_negs):
        issues.append({
            "code": "NEGATION_POLARITY_MISMATCH",
            "claim_negations": sorted(set(c_negs)),
            "source_negations": sorted(set(s_negs)),
        })

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "claim_negations": sorted(set(c_negs)),
        "source_negations": sorted(set(s_negs)),
    }


def verify_quote_strict(quote: str, source_text: str, min_chars: int = 8) -> dict[str, Any]:
    """Case-sensitive verbatim quote verification with auditable whitespace normalization and diagnostics."""
    q_norm, q_ops = normalize_whitespace_preserve_case(quote)
    s_norm, s_ops = normalize_whitespace_preserve_case(source_text)
    if len(q_norm) < min_chars or not s_norm:
        return {
            "ok": False,
            "reason": "QUOTE_TOO_SHORT_OR_EMPTY_SOURCE",
            "normalization_applied": sorted(set(q_ops + s_ops)),
            "fidelity_issues": [],
        }

    # Exact case-sensitive substring match
    idx = s_norm.find(q_norm)
    if idx != -1:
        # Also verify the matched span itself passes unit/numeric/negation fidelity
        matched_span = s_norm[idx : idx + len(q_norm)]
        fidelity = check_claim_fidelity(q_norm, matched_span)
        return {
            "ok": fidelity["ok"],
            "reason": "EXACT_VERBATIM_MATCH" if fidelity["ok"] else "FIDELITY_CHECK_FAILED",
            "match_char_span": [idx, idx + len(q_norm)],
            "normalization_applied": sorted(set(q_ops + s_ops)),
            "fidelity_issues": fidelity["issues"],
        }

    # Diagnose case-only tampering (e.g., mM -> mm, Co -> CO) vs negation/numeric edits
    fidelity = check_claim_fidelity(q_norm, s_norm)
    if q_norm.casefold() in s_norm.casefold():
        return {
            "ok": False,
            "reason": "CASE_MISMATCH_BLOCKED",
            "normalization_applied": sorted(set(q_ops + s_ops)),
            "fidelity_issues": fidelity["issues"] or [{"code": "CASE_DIFFERENCE_DETECTED"}],
        }

    reason = "NOT_FOUND_IN_SOURCE"
    issue_codes = {it["code"] for it in fidelity["issues"]}
    if "NEGATION_POLARITY_MISMATCH" in issue_codes:
        reason = "NEGATION_TAMPERING_BLOCKED"
    elif issue_codes & {"UNIT_CASE_MISMATCH", "NUMERIC_UNIT_NOT_IN_SOURCE", "NUMERIC_VALUE_MISMATCH"}:
        reason = "UNIT_OR_NUMERIC_MISMATCH_BLOCKED"
    elif "SYMBOL_CASE_MISMATCH" in issue_codes:
        reason = "SYMBOL_CASE_MISMATCH_BLOCKED"

    return {
        "ok": False,
        "reason": reason,
        "normalization_applied": sorted(set(q_ops + s_ops)),
        "fidelity_issues": fidelity["issues"],
    }


def verify_exact_quote(quote: str, source_text: str, min_chars: int = 8) -> bool:
    """Verify quote is grounded verbatim inside source_text (case-sensitive; whitespace/linebreak normalized)."""
    return bool(verify_quote_strict(quote, source_text, min_chars=min_chars)["ok"])


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
