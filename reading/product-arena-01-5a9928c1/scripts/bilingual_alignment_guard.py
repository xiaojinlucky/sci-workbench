#!/usr/bin/env python3
"""Two-stage bilingual scientific translation preparation and deterministic invariant auditor.

Modes:
  1. --prepare: Reads /input/case.json (and PDF/Markdown/TXT under /input), creates immutable
     paragraph anchors (/app/work/source_segments.json), locks source bibliography identity
     (/app/work/source_metadata_lock.json), extracts terminology candidates
     (/app/work/glossary_candidates.json), optionally exports starter CSV (--export-glossary-csv),
     and initializes /app/output/result.json.
  2. --audit: Audits a drafted translation JSON (--draft) or legacy source/target files
     (--source / --target) against bibliography identity locks, cross-item contamination,
     1:1 paragraph completeness, sentence-count & information-density alignment, discourse
     connector (concession/contrast/causality) fidelity, glossary consistency, thousands-separated
     numbers & sample sizes (n=1,010) + CN/EN magnitudes, simple & compound SI unit-value pairing
     (e.g., mA cm^-2, mV dec^-1, inhabitants/km2, /10,000), formulas/Greek symbols, citations,
     figure/table tags, negation polarity, and modal hedge preservation; optionally emits final
     /app/output/result.json, /app/output/translation_bilingual.md, and reusable glossary CSV.

Pure Python 3.12 standard library (uses reading-order pdftotext -enc UTF-8 with optional pypdf
fallback when available).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import preflight_probe
import result_guard

# --- Regular expressions for scientific invariants ---

CITE_RE = re.compile(r"\\cite[pt]?\*?\{([^}]+)\}|\[(\d+(?:\s*[-,–]\s*\d+)*)\]")

FIG_TAG_RE = re.compile(
    r"(?:\b(?:Figs?\.?|Figures?)\s*|图\s*)(\d+[A-Za-z]?)(?![A-Za-z0-9])",
    re.I,
)
TABLE_TAG_RE = re.compile(
    r"(?:\bTables?\s*|表\s*)(\d+[A-Za-z]?)(?![A-Za-z0-9])",
    re.I,
)
EQ_TAG_RE = re.compile(
    r"(?:\bEqs?\.?\s*\(?|Equations?\s*\(?|式\s*[（(]?)(\d+[A-Za-z]?)(?![A-Za-z0-9])[)）]?",
    re.I,
)

# Chemical / gene / identifier tokens with embedded digits (e.g., CO2, H2O2, TP53, β2-AR, IL-6)
EMBEDDED_ID_DIGIT_RE = re.compile(
    r"(?:[A-Za-zα-ωΑ-Ωβμµ]{1,6}[-_]?\d+[A-Za-z0-9-]*|\b(?:19|20)\d{2}\b)"
)

# Scientific notation and CN/EN magnitude numbers
SCI_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(-?\d+(?:\.\d+)?)\s*(?:[×xX*]\s*10\s*\^?\s*\{?\s*([+-]?\d+)\s*\}?|[eE]([+-]?\d+))(?![A-Za-z0-9_])"
)
MAG_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(-?\d+(?:,\d{3})*(?:\.\d+)?)(?:\s*(%)|\s*(万|亿|千|thousand|million|billion)(?![A-Za-z0-9_])|(?![A-Za-z0-9_]))",
    re.I,
)

MULT = {
    "": 1.0,
    "%": 1.0,
    "千": 1e3,
    "万": 1e4,
    "亿": 1e8,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
}

# Comprehensive SI & scientific unit aliases (spelling normalization only; no unit conversion).
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "kg": ("kg", "kilogram", "kilograms", "千克", "公斤"),
    "g": ("g", "gram", "grams", "克"),
    "mg": ("mg", "milligram", "milligrams", "毫克"),
    "ug": ("μg", "µg", "ug", "mcg", "microgram", "micrograms", "微克"),
    "ng": ("ng", "nanogram", "nanograms", "纳克"),
    "pg": ("pg", "picogram", "picograms", "皮克"),
    "L": ("L", "liter", "liters", "litre", "litres", "升"),
    "mL": ("mL", "ml", "milliliter", "milliliters", "millilitre", "millilitres", "毫升"),
    "uL": ("μL", "µL", "uL", "ul", "microliter", "microliters", "microlitre", "microlitres", "微升"),
    "m": ("m", "meter", "meters", "metre", "metres", "米"),
    "cm": ("cm", "centimeter", "centimeters", "centimetre", "centimetres", "厘米"),
    "mm": ("mm", "millimeter", "millimeters", "millimetre", "millimetres", "毫米"),
    "um": ("μm", "µm", "um", "micrometer", "micrometers", "micrometre", "micrometres", "微米"),
    "nm": ("nm", "nanometer", "nanometers", "nanometre", "nanometres", "纳米"),
    "s": ("s", "sec", "second", "seconds", "秒"),
    "ms": ("ms", "millisecond", "milliseconds", "毫秒"),
    "us": ("μs", "µs", "us", "microsecond", "microseconds", "微秒"),
    "min": ("min", "minute", "minutes", "分钟"),
    "h": ("h", "hr", "hrs", "hour", "hours", "小时"),
    "kV": ("kV", "kilovolt", "kilovolts", "千伏"),
    "V": ("V", "volt", "volts", "伏", "伏特"),
    "mV": ("mV", "millivolt", "millivolts", "毫伏"),
    "uV": ("μV", "µV", "uV", "microvolt", "microvolts", "微伏"),
    "mA": ("mA", "milliampere", "milliamperes", "毫安"),
    "uA": ("μA", "µA", "uA", "microampere", "microamperes", "微安"),
    "M": ("mol/L", "molar", "摩尔/升"),
    "mM": ("mM", "mmol/L", "millimolar", "毫摩尔/升", "毫摩尔"),
    "uM": ("μM", "µM", "uM", "umol/L", "μmol/L", "µmol/L", "micromolar", "微摩尔/升", "微摩尔"),
    "nM": ("nM", "nmol/L", "nanomolar", "纳摩尔/升", "纳摩尔"),
    "mg/L": ("mg/L", "mg/l", "毫克/升"),
    "ug/L": ("μg/L", "µg/L", "ug/L", "微克/升"),
    "mg/mL": ("mg/mL", "mg/ml", "毫克/毫升"),
    "mg/kg": ("mg/kg", "毫克/千克"),
    "ppm": ("ppm",),
    "ppb": ("ppb",),
    "Pa": ("Pa", "pascal", "pascals", "帕"),
    "kPa": ("kPa", "kilopascal", "kilopascals", "千帕"),
    "MPa": ("MPa", "megapascal", "megapascals", "兆帕"),
    "GPa": ("GPa", "gigapascal", "gigapascals", "吉帕"),
    "Hz": ("Hz", "hertz", "赫兹"),
    "kHz": ("kHz", "kilohertz", "千赫兹"),
    "MHz": ("MHz", "megahertz", "兆赫兹"),
    "eV": ("eV", "电子伏特"),
    "C": ("°C", "℃", "摄氏度"),
    "K": ("K", "kelvin", "开尔文"),
}

UNIT_ALIAS_TO_CANONICAL: dict[str, str] = {}
UNIT_ALIAS_CASEFOLD: dict[str, str] = {}
for _canon, _aliases in UNIT_ALIASES.items():
    for _al in _aliases:
        UNIT_ALIAS_TO_CANONICAL[_al] = _canon
        UNIT_ALIAS_CASEFOLD[_al.casefold()] = _canon

CASE_INSENSITIVE_UNITS = {
    al
    for al in UNIT_ALIAS_TO_CANONICAL
    if len(al) >= 4 and al.isascii() and al.isalpha()
}

UNIT_PATTERN = "(?:" + "|".join(
    f"(?i:{re.escape(al)})" if al in CASE_INSENSITIVE_UNITS else re.escape(al)
    for al in sorted(UNIT_ALIAS_TO_CANONICAL, key=len, reverse=True)
) + ")"

NUMBER_WITH_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{{3}})*(?:\.\d+)?)\s*(?P<magnitude>万|亿|千|(?i:thousand|million|billion))?\s*(?P<unit>{UNIT_PATTERN})(?![A-Za-zµμ/])"
)

# Compound scientific SI units (electrochemistry, catalysis, epidemiology, materials)
# Strictly separates negative exponents (e.g., cm^-2 / cm⁻² / /cm2) from positive exponents
# (e.g., cm^2 / cm2 / cm²) into distinct canonical unit buckets so exponent sign flips are caught.
COMPOUND_UNIT_SPECS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Negative-exponent / per-area electrochemistry & flux units (must require explicit '-' or '/')
    ("mA/cm2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mA\s*(?:·|\*)?\s*cm\s*(?:\^\s*-\s*2|-\s*2|⁻²)|mA\s*/\s*cm(?:\^\s*\+?2|²|2))(?![A-Za-z0-9])")),
    ("mA*cm2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mA\s*(?:·|\*)?\s*cm\s*(?:\^\s*\+?2|²|2))(?![A-Za-z0-9])")),
    ("A/cm2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>A\s*(?:·|\*)?\s*cm\s*(?:\^\s*-\s*2|-\s*2|⁻²)|A\s*/\s*cm(?:\^\s*\+?2|²|2))(?![A-Za-z0-9])")),
    ("A*cm2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>A\s*(?:·|\*)?\s*cm\s*(?:\^\s*\+?2|²|2))(?![A-Za-z0-9])")),
    ("mV/dec", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mV\s*(?:·|\*)?\s*dec\s*(?:\^\s*-\s*1|-\s*1|⁻¹)|mV\s*/\s*dec)(?![A-Za-z0-9])", re.I)),
    ("mV*dec", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mV\s*(?:·|\*)?\s*dec\s*(?:\^\s*\+?1|¹))(?![A-Za-z0-9])", re.I)),
    ("mg/cm2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mg\s*(?:·|\*)?\s*cm\s*(?:\^\s*-\s*2|-\s*2|⁻²)|mg\s*/\s*cm(?:\^\s*\+?2|²|2))(?![A-Za-z0-9])")),
    ("mg*cm2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mg\s*(?:·|\*)?\s*cm\s*(?:\^\s*\+?2|²|2))(?![A-Za-z0-9])")),
    ("g/cm3", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>g\s*(?:·|\*)?\s*cm\s*(?:\^\s*-\s*3|-\s*3|⁻³)|g\s*/\s*cm(?:\^\s*\+?3|³|3))(?![A-Za-z0-9])")),
    ("g*cm3", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>g\s*(?:·|\*)?\s*cm\s*(?:\^\s*\+?3|³|3))(?![A-Za-z0-9])")),
    ("M", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mol\s*(?:·|\*)?\s*L\s*(?:\^\s*-\s*1|-\s*1|⁻¹)|mol\s*/\s*L)(?![A-Za-z0-9])")),
    ("mol*L", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>mol\s*(?:·|\*)?\s*L\s*(?:\^\s*\+?1|¹))(?![A-Za-z0-9])")),
    ("inhabitants/km2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>(?:inhabitants|persons?|people)\s*/\s*km(?:\^\s*\+?2|²|2)|人\s*/\s*(?:km(?:\^\s*\+?2|²|2)|平方公里|平方千米))(?![A-Za-z0-9])", re.I)),
    ("per_10k", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>/\s*10,?000(?:\s*(?:inhabitants|人|名居民|居民))?|/\s*(?:1\s*)?万(?:人|名居民|居民)?)(?![A-Za-z0-9])", re.I)),
    # Standalone inverse-area / inverse-volume vs positive area / volume (negative MUST run before positive)
    ("cm^-2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>/\s*cm(?:\^\s*\+?2|²|2)|cm\s*(?:\^\s*-\s*2|-\s*2|⁻²))(?![A-Za-z0-9])")),
    ("cm^2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>cm\s*(?:\^\s*\+?2|²|2)|平方厘米)(?![A-Za-z0-9])")),
    ("cm^-3", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>/\s*cm(?:\^\s*\+?3|³|3)|cm\s*(?:\^\s*-\s*3|-\s*3|⁻³))(?![A-Za-z0-9])")),
    ("cm^3", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>cm\s*(?:\^\s*\+?3|³|3)|立方厘米)(?![A-Za-z0-9])")),
    ("m^-2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>/\s*m(?:\^\s*\+?2|²|2)|m\s*(?:\^\s*-\s*2|-\s*2|⁻²))(?![A-Za-z0-9])")),
    ("m^2", re.compile(r"(?<![A-Za-z0-9_])(?P<number>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>m\s*(?:\^\s*\+?2|²|2)|平方米)(?![A-Za-z0-9])")),
)

# Standalone compound-unit exponent tokens to mask out before scalar number extraction
COMPOUND_EXP_NOISE_RE = re.compile(
    r"\b(?:cm|km|mm|m|L|dec)\s*(?:\^\s*[+-]?\s*\d+|-\s*\d+|[⁻⁺²³¹]+)\b",
    re.I,
)

# Explicit sample size tokens (preserves thousands separators: n=1,010, n = 5,539)
SAMPLE_N_RE = re.compile(r"(?<![A-Za-z0-9_])n\s*[=:]\s*(\d+(?:,\d{3})*)(?![A-Za-z0-9_,])", re.I)

# Logical discourse connectors (concession/contrast & causality/consequence)
CONTRAST_EN_RE = re.compile(
    r"\b(?:however|whereas|although|though|despite|in\s+spite\s+of|conversely|nevertheless|nonetheless|on\s+the\s+other\s+hand)\b|"
    r"(?:^|[.;!?]\s+)while\b|,\s+while\b",
    re.I,
)
CONTRAST_ZH_RE = re.compile(
    r"然而|但是|但|不过|而|相比之下|相反|反之|尽管|虽然|虽|即便|即使|却|仍|与之相对|另一方面|反观|但仍"
)
CAUSAL_EN_RE = re.compile(
    r"\b(?:because|therefore|thus|hence|consequently|as\s+a\s+result|due\s+to|owing\s+to)\b|"
    r"\bsince\b(?!\s+(?:19|20)\d{2}\b|\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))",
    re.I,
)
CAUSAL_ZH_RE = re.compile(
    r"因为|由于|因|因此|故|所以|从而|致使|导致|由此|据此|结果|鉴于|使得|进而|因而"
)

# Abbreviations ignored during English sentence segmentation
EN_ABBREV_RE = re.compile(
    r"\b(?:Fig|Figs|Eq|Eqs|Tab|Ref|Refs|Dr|Mr|Mrs|Ms|Prof|No|Vol|vs|et\s+al|e\.g|i\.e|ca|approx|St|Inc|Ltd|p|pp)\.",
    re.I,
)

# Negation & epistemic modal regexes
NEG_EN_RE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|none|cannot|can't|fail(?:s|ed)?\s+to|unlike|absent|rarely|seldom|lack(?:s|ed)?|no\s+longer)\b",
    re.I,
)
NEG_ZH_RE = re.compile(
    r"不[是为会对能应可存在显同再足具构产导支影改包适允受必宜含]|未(?!来)|无[法显明显需关]|并非|绝非|没有|缺乏|罕见|极少|不同于|否(?:定|认)"
)
HEDGE_EN_RE = re.compile(
    r"\b(?:may|might|could|suggests?|suggested|indicates?|indicated|appears?\s+to|potentially|possibly|likely|approximately|roughly|only|primarily|limited\s+to)\b",
    re.I,
)
HARD_LIMITER_EN_RE = re.compile(r"\b(?:only|solely|exclusively|limited\s+to|rarely|seldom)\b", re.I)
HARD_LIMITER_ZH_RE = re.compile(r"仅|只|唯一|局限于|限于|罕见|极少")
CERTAINTY_EN_RE = re.compile(
    r"\b(?:prove[sd]?|proven|confirm(?:s|ed)?|establish(?:es|ed)?|definitively|conclusively|demonstrate[sd]?\s+definitively)\b",
    re.I,
)
HEDGE_ZH_RE = re.compile(
    r"可能|或许|提示|倾向于|似乎|潜在|约|大约|大致|仅|只|主要|局限于|限于|表明可能|初步提示"
)
CERTAINTY_UPGRADE_ZH_RE = re.compile(
    r"确切证明|充分证明|完全证明|彻底证明|证明了|证实了|确定无疑|必然导致|肯定会|绝对|完全证实"
)

# Formula / Greek / statistical markers
LATEX_CMD_RE = re.compile(r"\\(?:frac|sqrt|sum|int|prod|partial|nabla|alpha|beta|gamma|delta|mu|sigma|lambda|leq|geq|approx|pm|times|cdot)\b")
GREEK_MATH_RE = re.compile(r"[αβγδΔμµσλ≤≥≈±×]")
STAT_CI_RE = re.compile(r"(?:95%\s*CI|confidence\s+interval|置信区间)", re.I)
STAT_PVAL_RE = re.compile(r"\b[pP]\s*[<=>≤≥]\s*0?\.\d+")

# Terminology candidate extraction patterns
ACRONYM_DEF_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9\-/]*(?:\s+[A-Za-z][A-Za-z0-9\-/]*){1,6})\s*\(([A-Z][A-Za-z0-9\-]{1,10})\)"
)
REV_ACRONYM_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9\-]{1,10})\s*\(([A-Za-z][A-Za-z0-9\-/]*(?:\s+[A-Za-z][A-Za-z0-9\-/]*){1,6})\)"
)
BIOMED_CHEM_RE = re.compile(
    r"\b(?:[αβγΔ]-?[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?|[A-Z]{2,}[0-9]+(?:-[A-Za-z0-9]+)?|[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d*)+)\b"
)
TECH_BIGRAM_RE = re.compile(r"\b([a-z]{4,}(?:-[a-z]{2,})?\s+[a-z]{4,}(?:\s+[a-z]{4,})?)\b")

TERM_STOPWORDS = {
    "this study", "these results", "our results", "the results", "this paper", "in this",
    "based on", "used to", "shown in", "table shows", "figure shows", "such as",
    "more than", "less than", "compared with", "compared to", "associated with",
    "that were", "which were", "have been", "from this", "with this", "that this",
    "should be", "could be", "would be", "may be", "also be", "can be",
}

SECTION_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+)?(ABSTRACT|INTRODUCTION|BACKGROUND|METHODS?|MATERIALS?\s+AND\s+METHODS?|RESULTS?|DISCUSSION|CONCLUSIONS?|REFERENCES?|APPENDIX|TABLE\s+\d+|FIG(?:URE)?\.?\s+\d+)\b",
    re.I,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm_ws(text: str) -> str:
    return " ".join((text or "").split())


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _dehyphenate_pdf_text(text: str) -> str:
    """Repair soft line-wrap hyphenations common in two-column academic PDFs."""
    return re.sub(r"([A-Za-z])-\s*\n\s*([a-z])", r"\1\2", text or "")


def _extract_pdf_pages(pdf_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract page-by-page paragraphs in reading order via pdftotext -enc UTF-8 (no -layout) or pypdf fallback."""
    has_pdfinfo = shutil.which("pdfinfo") is not None
    has_pdftotext = shutil.which("pdftotext") is not None

    pdf_meta: dict[str, str] = {}
    page_texts: list[tuple[int, str]] = []
    extractor_used = "unavailable"
    total_pages = 0

    if has_pdftotext:
        extractor_used = "pdftotext_utf8_reading_order"
        if has_pdfinfo:
            try:
                info_out = subprocess.check_output(
                    ["pdfinfo", str(pdf_path)],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
                for line in info_out.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        pdf_meta[k.strip().lower()] = v.strip()
                if pdf_meta.get("pages", "").isdigit():
                    total_pages = int(pdf_meta["pages"])
            except Exception:
                total_pages = 0

        if total_pages > 0:
            for page_no in range(1, total_pages + 1):
                try:
                    page_txt = subprocess.check_output(
                        ["pdftotext", "-enc", "UTF-8", "-f", str(page_no), "-l", str(page_no), str(pdf_path), "-"],
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        stderr=subprocess.DEVNULL,
                        timeout=20,
                    )
                except Exception:
                    page_txt = ""
                page_texts.append((page_no, page_txt))
        else:
            try:
                full_txt = subprocess.check_output(
                    ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except Exception:
                full_txt = ""
            raw_pages = full_txt.split("\x0c")
            if len(raw_pages) > 1 and not raw_pages[-1].strip():
                raw_pages = raw_pages[:-1]
            total_pages = max(len(raw_pages), 1)
            page_texts = [(i, ptxt) for i, ptxt in enumerate(raw_pages, start=1)]
    else:
        # Optional pure-Python fallback if pypdf is installed in the runtime
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(str(pdf_path))
            extractor_used = "pypdf_fallback"
            total_pages = len(reader.pages)
            meta_obj = getattr(reader, "metadata", None) or {}
            pdf_meta["title"] = str(meta_obj.get("/Title") or "").strip()
            pdf_meta["author"] = str(meta_obj.get("/Author") or "").strip()
            for page_no, pg in enumerate(reader.pages, start=1):
                try:
                    page_txt = pg.extract_text() or ""
                except Exception:
                    page_txt = ""
                page_texts.append((page_no, page_txt))
        except Exception:
            return [], {
                "pdf_file": pdf_path.name,
                "extractor": "unavailable",
                "total_pages": 0,
                "extracted_pages": 0,
                "empty_or_scanned_pages": [],
                "error": "Neither pdftotext nor pypdf is available in sandbox",
            }

    segments: list[dict[str, Any]] = []
    empty_pages: list[int] = []
    per_page_chars: dict[str, int] = {}
    extracted_pages = 0
    current_section = "Title/FrontMatter"
    p_counter = 0

    for page_no, raw_ptxt in page_texts:
        page_txt = _dehyphenate_pdf_text(raw_ptxt)
        clean_chars = len(_norm_ws(page_txt))
        per_page_chars[str(page_no)] = clean_chars
        if clean_chars < 15:
            empty_pages.append(page_no)
            continue
        extracted_pages += 1
        raw_chunks = [c.strip() for c in re.split(r"\n\s*\n+", page_txt) if c.strip()]
        for ch in raw_chunks:
            ch_one = _norm_ws(ch)
            if len(ch_one) < 4 or re.fullmatch(r"\d{1,4}", ch_one):
                continue
            m_sec = SECTION_HEADING_RE.match(ch_one)
            if m_sec and len(ch_one) <= 90:
                current_section = m_sec.group(1).title()
            p_counter += 1
            segments.append({
                "paragraph_id": f"P{p_counter:03d}",
                "section": current_section,
                "page": page_no,
                "text": ch_one,
            })

    coverage = {
        "pdf_file": pdf_path.name,
        "extractor": extractor_used,
        "total_pages": total_pages,
        "extracted_pages": extracted_pages,
        "empty_or_scanned_pages": empty_pages,
        "pdf_title": pdf_meta.get("title", ""),
        "pdf_author": pdf_meta.get("author", ""),
        "per_page_chars": per_page_chars,
    }
    return segments, coverage


def extract_glossary_candidates(segments: list[dict[str, Any]], max_terms: int = 40) -> list[dict[str, Any]]:
    """Extract candidate domain terms (acronyms, biomedical/chemical symbols, recurring phrases)."""
    candidates: dict[str, dict[str, Any]] = {}

    def _register(term: str, category: str, pid: str, sec: str, abbr: str = "") -> None:
        key = term.strip()
        if len(key) < 2 or key.lower() in TERM_STOPWORDS:
            return
        k_low = key.lower()
        if k_low not in candidates:
            candidates[k_low] = {
                "source": key,
                "abbreviation": abbr,
                "category": category,
                "occurrences": 0,
                "paragraph_ids": [],
                "sections": [],
            }
        rec = candidates[k_low]
        rec["occurrences"] += 1
        if pid not in rec["paragraph_ids"]:
            rec["paragraph_ids"].append(pid)
        if sec not in rec["sections"]:
            rec["sections"].append(sec)
        if abbr and not rec["abbreviation"]:
            rec["abbreviation"] = abbr

    phrase_counter: Counter[str] = Counter()
    phrase_locs: dict[str, list[tuple[str, str]]] = {}

    for seg in segments:
        pid = str(seg.get("paragraph_id", ""))
        sec = str(seg.get("section", "Body"))
        txt = str(seg.get("text") or seg.get("source_en") or "")

        for m in ACRONYM_DEF_RE.finditer(txt):
            full_t, abbr = m.group(1).strip(), m.group(2).strip()
            _register(full_t, "acronym_definition", pid, sec, abbr=abbr)
            _register(abbr, "acronym", pid, sec, abbr=full_t)

        for m in REV_ACRONYM_RE.finditer(txt):
            abbr, full_t = m.group(1).strip(), m.group(2).strip()
            _register(full_t, "acronym_definition", pid, sec, abbr=abbr)
            _register(abbr, "acronym", pid, sec, abbr=full_t)

        for m in BIOMED_CHEM_RE.finditer(txt):
            tok = m.group(0).strip()
            if not re.fullmatch(r"P\d{2,4}|F\d+|T\d+", tok):
                _register(tok, "molecular_or_symbol", pid, sec)

        for m in TECH_BIGRAM_RE.finditer(txt.lower()):
            ph = m.group(1).strip()
            if ph in TERM_STOPWORDS or any(w in ("that", "with", "from", "this", "have", "were", "their") for w in ph.split()):
                continue
            phrase_counter[ph] += 1
            phrase_locs.setdefault(ph, []).append((pid, sec))

    for ph, cnt in phrase_counter.most_common(30):
        if cnt >= 2 and ph not in candidates:
            for pid, sec in phrase_locs[ph]:
                _register(ph, "recurring_phrase", pid, sec)

    ranked = sorted(
        candidates.values(),
        key=lambda x: (
            0 if x["category"] == "acronym_definition" else 1 if x["category"] == "molecular_or_symbol" else 2,
            -x["occurrences"],
            x["source"].lower(),
        ),
    )
    return ranked[:max_terms]


def prepare_workspace(input_dir: Path, work_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Stage 1 (--prepare): segment source, lock bibliography metadata, extract glossary candidates."""
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    case_path = input_dir / "case.json"
    case_data: dict[str, Any] = {}
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))

    case_id = str(case_data.get("case_id") or "translation-case-01")
    result_path = output_dir / "result.json"
    result_guard.init_result(result_path, case_id=case_id, task_id="product-arena-01-5a9928c1")

    segments: list[dict[str, Any]] = []
    page_coverage: dict[str, Any] = {
        "source_mode": "structured_json",
        "total_pages": 0,
        "extracted_pages": 0,
        "empty_or_scanned_pages": [],
    }
    source_file_name = "case.json"
    source_sha256 = _sha256_bytes(case_path.read_bytes()) if case_path.is_file() else ""
    pdf_meta_title = ""
    pdf_meta_author = ""

    # 1) Try structured paragraphs in case.json
    raw_paras = case_data.get("paragraphs") or case_data.get("segments") or case_data.get("source_paragraphs")
    if isinstance(raw_paras, list) and raw_paras:
        for idx, item in enumerate(raw_paras, start=1):
            if isinstance(item, dict):
                pid = str(item.get("paragraph_id") or item.get("id") or f"P{idx:03d}")
                sec = str(item.get("section") or item.get("heading") or "Body")
                page = item.get("page")
                txt = str(item.get("text") or item.get("source_en") or item.get("content") or "").strip()
            else:
                pid, sec, page, txt = f"P{idx:03d}", "Body", None, str(item).strip()
            if txt:
                segments.append({"paragraph_id": pid, "section": sec, "page": page, "text": txt})
    elif isinstance(case_data.get("source_text") or case_data.get("paper_text"), str):
        raw_txt = str(case_data.get("source_text") or case_data.get("paper_text"))
        for idx, chunk in enumerate(re.split(r"\n\s*\n+", raw_txt), start=1):
            if chunk.strip():
                segments.append({"paragraph_id": f"P{idx:03d}", "section": "Body", "page": None, "text": chunk.strip()})

    # 2) If case.json references a PDF or /input contains a PDF and no paragraphs yet
    pdf_candidate: Path | None = None
    if isinstance(case_data.get("pdf_file"), str):
        p = input_dir / case_data["pdf_file"]
        if p.is_file():
            pdf_candidate = p
    elif isinstance(case_data.get("source_file"), str) and case_data["source_file"].lower().endswith(".pdf"):
        p = input_dir / case_data["source_file"]
        if p.is_file():
            pdf_candidate = p
    if pdf_candidate is None and not segments and input_dir.is_dir():
        pdfs = sorted(input_dir.glob("*.pdf"))
        if pdfs:
            pdf_candidate = pdfs[0]

    if pdf_candidate is not None and not segments:
        segments, pdf_cov = _extract_pdf_pages(pdf_candidate)
        page_coverage = {"source_mode": "pdf", **pdf_cov}
        source_file_name = pdf_candidate.name
        source_sha256 = _sha256_bytes(pdf_candidate.read_bytes())
        pdf_meta_title = pdf_cov.get("pdf_title", "")
        pdf_meta_author = pdf_cov.get("pdf_author", "")

    # 3) Fallback to Markdown or TXT files under /input
    if not segments and input_dir.is_dir():
        for ext in ("*.md", "*.txt"):
            cands = [p for p in sorted(input_dir.glob(ext)) if p.name.lower() != "task.md"]
            if cands:
                fp = cands[0]
                source_file_name = fp.name
                raw_bytes = fp.read_bytes()
                source_sha256 = _sha256_bytes(raw_bytes)
                txt = raw_bytes.decode("utf-8", errors="replace")
                for idx, chunk in enumerate(re.split(r"\n\s*\n+", txt), start=1):
                    if chunk.strip():
                        segments.append({"paragraph_id": f"P{idx:03d}", "section": "Body", "page": None, "text": chunk.strip()})
                page_coverage["source_mode"] = f"text_{fp.suffix.lstrip('.')}"
                break

    # Lock source bibliography metadata (Zotero #117395 / #306 protection)
    meta_in = case_data.get("source_metadata") if isinstance(case_data.get("source_metadata"), dict) else {}
    item_id = str(meta_in.get("item_id") or case_data.get("item_id") or case_data.get("paper_id") or case_id)
    source_title = str(
        meta_in.get("source_title")
        or case_data.get("source_title")
        or case_data.get("title")
        or pdf_meta_title
        or (segments[0]["text"][:140] if segments else "Untitled Source")
    ).strip()
    authors_raw = meta_in.get("authors") or case_data.get("authors") or ([pdf_meta_author] if pdf_meta_author else [])
    if isinstance(authors_raw, str):
        authors = [a.strip() for a in re.split(r"[;,]", authors_raw) if a.strip()]
    elif isinstance(authors_raw, list):
        authors = [str(a).strip() for a in authors_raw if str(a).strip()]
    else:
        authors = []
    doi = str(meta_in.get("doi") or case_data.get("doi") or "").strip()
    year = meta_in.get("year") or case_data.get("year")

    # Record neighbor items if supplied so cross-item contamination can be caught deterministically
    neighbor_items: list[dict[str, Any]] = []
    for nb in (case_data.get("other_items") or case_data.get("neighbor_items") or []):
        if isinstance(nb, dict):
            nb_id = str(nb.get("item_id") or nb.get("id") or "").strip()
            if nb_id and nb_id == item_id:
                continue
            neighbor_items.append({
                "item_id": nb_id,
                "source_title": str(nb.get("source_title") or nb.get("title") or "").strip(),
                "doi": str(nb.get("doi") or "").strip(),
            })

    metadata_lock = {
        "case_id": case_id,
        "item_id": item_id,
        "source_title": source_title,
        "authors": authors,
        "doi": doi,
        "year": year,
        "source_file": source_file_name,
        "source_sha256": source_sha256,
        "neighbor_items": neighbor_items,
    }

    # Attach immutable item_id to every segment
    for seg in segments:
        seg["item_id"] = item_id

    glossary_candidates = extract_glossary_candidates(segments)
    supplied_glossary = case_data.get("glossary") or []

    source_segments_doc = {
        "case_id": case_id,
        "item_id": item_id,
        "total_paragraphs": len(segments),
        "page_coverage": page_coverage,
        "segments": segments,
    }
    glossary_cand_doc = {
        "case_id": case_id,
        "supplied_glossary": supplied_glossary,
        "candidates": glossary_candidates,
    }

    result_guard.atomic_write_json(work_dir / "source_segments.json", source_segments_doc)
    result_guard.atomic_write_json(work_dir / "source_metadata_lock.json", metadata_lock)
    result_guard.atomic_write_json(work_dir / "glossary_candidates.json", glossary_cand_doc)

    return {
        "ok": len(segments) > 0,
        "case_id": case_id,
        "item_id": item_id,
        "total_paragraphs": len(segments),
        "glossary_candidates_count": len(glossary_candidates),
        "page_coverage": page_coverage,
    }


# --- Quantitative, Unit, Formula, Citation, Sentence-Density, Discourse & Modal Extraction ---

def _strip_non_scalar_noise(text: str) -> str:
    cleaned = CITE_RE.sub(" ", text)
    cleaned = FIG_TAG_RE.sub(" ", cleaned)
    cleaned = TABLE_TAG_RE.sub(" ", cleaned)
    cleaned = EQ_TAG_RE.sub(" ", cleaned)
    cleaned = COMPOUND_EXP_NOISE_RE.sub(" ", cleaned)
    # Normalize Chinese rate-per-10k shorthand ("/万" or "每万") into explicit "/10,000" for scalar alignment
    cleaned = re.sub(r"(?<!\d)(?:/|每)\s*万", "/10000", cleaned)
    cleaned = EMBEDDED_ID_DIGIT_RE.sub(" ", cleaned)
    return cleaned


def _extract_normalized_numbers(text: str) -> list[tuple[float, str, str]]:
    out: list[tuple[float, str, str]] = []
    cleaned = _strip_non_scalar_noise(text)

    # Extract scientific notation first and blank out spans so MAG_NUM_RE does not double-count
    spans_used: list[tuple[int, int]] = []
    for m in SCI_NUM_RE.finditer(cleaned):
        mant = float(m.group(1))
        exp = int(m.group(2) if m.group(2) is not None else m.group(3))
        out.append((mant * (10.0 ** exp), "scalar", m.group(0).strip()))
        spans_used.append((m.start(), m.end()))

    if spans_used:
        chars = list(cleaned)
        for s, e in spans_used:
            for i in range(s, e):
                chars[i] = " "
        cleaned = "".join(chars)

    for m in MAG_NUM_RE.finditer(cleaned):
        raw_num = m.group(1).replace(",", "")
        unit = (m.group(2) or m.group(3) or "").lower()
        try:
            val = float(raw_num) * MULT.get(unit, 1.0)
        except ValueError:
            continue
        kind = "%" if unit == "%" else "scalar"
        out.append((val, kind, m.group(0).strip()))
    return out


def _extract_sample_sizes(text: str) -> list[tuple[int, str]]:
    """Extract thousands-aware sample sizes (e.g., n=1,010, n = 5,539)."""
    out: list[tuple[int, str]] = []
    for m in SAMPLE_N_RE.finditer(text or ""):
        raw = m.group(1)
        try:
            out.append((int(raw.replace(",", "")), f"n={raw}"))
        except ValueError:
            continue
    return sorted(out, key=lambda x: x[0])


def _sample_sizes_match(src_n: list[tuple[int, str]], tgt_n: list[tuple[int, str]]) -> tuple[bool, list[str]]:
    if not src_n and not tgt_n:
        return True, []
    s_vals = [x[0] for x in src_n]
    t_vals = [x[0] for x in tgt_n]
    if s_vals != t_vals:
        return False, [
            f"Sample-size (n=) drift or thousands-separator truncation: "
            f"source={[x[1] for x in src_n]} vs target={[x[1] for x in tgt_n]}"
        ]
    return True, []


def _numbers_match(src_nums: list[tuple[float, str, str]], tgt_nums: list[tuple[float, str, str]]) -> tuple[bool, list[str]]:
    if len(src_nums) != len(tgt_nums):
        return False, [f"Numeric count mismatch: source={[x[2] for x in src_nums]} vs target={[x[2] for x in tgt_nums]}"]
    diffs: list[str] = []
    s_sorted = sorted(src_nums, key=lambda x: (x[1], x[0]))
    t_sorted = sorted(tgt_nums, key=lambda x: (x[1], x[0]))
    for (sv, sk, sr), (tv, tk, tr) in zip(s_sorted, t_sorted):
        if sk != tk or not math.isclose(sv, tv, rel_tol=1e-5, abs_tol=1e-9):
            diffs.append(f"Value drift: {sr!r} ({sv:g}) != {tr!r} ({tv:g})")
    return len(diffs) == 0, diffs


def _extract_physical_units(text: str) -> list[tuple[float, str, str]]:
    """Extract both compound SI units (strictly separating positive/negative exponents) and simple SI units."""
    units: list[tuple[float, str, str]] = []
    cleaned = CITE_RE.sub(" ", text)

    # 1) Match compound units sequentially and blank out matched spans after each spec
    # so negative-exponent specs (e.g., mA/cm2, cm^-2) are never double-counted by positive specs (cm^2).
    for canon, pat in COMPOUND_UNIT_SPECS:
        spec_spans: list[tuple[int, int]] = []
        for m in pat.finditer(cleaned):
            raw_number = m.group("number")
            raw_unit = " ".join(m.group("unit").split())
            try:
                value = float(raw_number.replace(",", ""))
            except ValueError:
                continue
            units.append((value, canon, f"{raw_number} {raw_unit}".strip()))
            spec_spans.append((m.start(), m.end()))
        if spec_spans:
            chars = list(cleaned)
            for s, e in spec_spans:
                for i in range(s, e):
                    chars[i] = " "
            cleaned = "".join(chars)

    # 2) Match standard SI units
    for match in NUMBER_WITH_UNIT_RE.finditer(cleaned):
        raw_number = match.group("number")
        raw_mag = (match.group("magnitude") or "").lower()
        raw_unit = match.group("unit")
        canonical = UNIT_ALIAS_TO_CANONICAL.get(raw_unit) or UNIT_ALIAS_CASEFOLD.get(raw_unit.casefold())
        if canonical is None:
            continue
        value = float(raw_number.replace(",", "")) * MULT.get(raw_mag, 1.0)
        units.append((value, canonical, f"{raw_number}{raw_mag} {raw_unit}".strip()))
    return units


def _count_en_sentences(text: str) -> int:
    cleaned = EN_ABBREV_RE.sub("ABBR", text or "")
    # Protect decimal numbers (18.2 -> 18_2) and URLs/DOIs
    cleaned = re.sub(r"https?://\S+|10\.\d{4,9}/\S+", "URL", cleaned)
    cleaned = re.sub(r"(\d)\.(\d)", r"\1_\2", cleaned)
    parts = [p.strip() for p in re.split(r"[.!?]+(?:\s+|$)|;\s+", cleaned) if len(re.findall(r"[A-Za-z0-9]", p)) >= 3]
    return max(len(parts), 1 if (text or "").strip() else 0)


def _count_zh_sentences(text: str) -> int:
    cleaned = re.sub(r"https?://\S+|10\.\d{4,9}/\S+", "URL", text or "")
    parts = [p.strip() for p in re.split(r"[。！？；]+", cleaned) if len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", p)) >= 2]
    return max(len(parts), 1 if (text or "").strip() else 0)


def _audit_sentence_and_density(
    source_en: str,
    target_zh: str,
    *,
    needs_review: bool = False,
) -> tuple[bool, list[str], list[str], dict[str, Any]]:
    """Detect implicit half-sentence omissions or summary collapse via sentence count and EN-word/ZH-char density."""
    en_words = len(re.findall(r"[A-Za-z][A-Za-z0-9'-]*", source_en or ""))
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", target_zh or ""))
    en_sents = _count_en_sentences(source_en)
    zh_sents = _count_zh_sentences(target_zh)
    zh_per_en = round(zh_chars / max(en_words, 1), 3) if en_words > 0 else 0.0
    sent_ratio = round(zh_sents / max(en_sents, 1), 3) if en_sents > 0 else 1.0

    info = {
        "en_words": en_words,
        "zh_chars": zh_chars,
        "en_sentences": en_sents,
        "zh_sentences": zh_sents,
        "zh_per_en_word": zh_per_en,
        "sentence_ratio": sent_ratio,
    }
    errs: list[str] = []
    warns: list[str] = []
    if needs_review or en_words < 20:
        return True, errs, warns, info

    if en_words >= 30 and zh_per_en < 0.78:
        errs.append(
            f"IMPLICIT_OMISSION_OR_SUMMARY_COLLAPSE (en_words={en_words}, zh_chars={zh_chars}, "
            f"ratio={zh_per_en:.2f} < 0.78; source_sents={en_sents}, target_sents={zh_sents})"
        )
    elif en_words >= 30 and en_sents >= 3 and zh_sents <= max(1, en_sents // 3) and zh_per_en < 1.25:
        errs.append(
            f"SENTENCE_OMISSION_DETECTED (source_sents={en_sents} collapsed to target_sents={zh_sents}, "
            f"zh_per_en_word={zh_per_en:.2f})"
        )
    elif en_words >= 25 and (zh_per_en < 1.05 or zh_per_en > 4.2 or (en_sents >= 3 and sent_ratio < 0.5)):
        warns.append(
            f"SENTENCE_DENSITY_WARNING (en_words={en_words}, zh_chars={zh_chars}, "
            f"ratio={zh_per_en:.2f}, sents={en_sents}->{zh_sents})"
        )
    return len(errs) == 0, errs, warns, info


def _audit_discourse_connectors(source_en: str, target_zh: str) -> tuple[bool, list[str]]:
    """Verify explicit contrast/concession and causality/consequence discourse markers are preserved."""
    errs: list[str] = []
    src_contrast = CONTRAST_EN_RE.findall(source_en or "")
    tgt_contrast = CONTRAST_ZH_RE.findall(target_zh or "")
    if src_contrast and not tgt_contrast:
        errs.append(
            f"DISCOURSE_LOGIC_DRIFT (contrast/concession markers {sorted(set(s.strip().lower() for s in src_contrast))} dropped in target)"
        )
    src_causal = CAUSAL_EN_RE.findall(source_en or "")
    tgt_causal = CAUSAL_ZH_RE.findall(target_zh or "")
    if src_causal and not tgt_causal:
        errs.append(
            f"DISCOURSE_LOGIC_DRIFT (causality/consequence markers {sorted(set(s.strip().lower() for s in src_causal))} dropped in target)"
        )
    return len(errs) == 0, errs


def export_glossary_csv(rows: list[dict[str, Any]], csv_path: Path, *, mode: str = "audit") -> Path:
    """Export structured bilingual terminology table or candidates to reusable UTF-8 CSV (never TSV)."""
    if csv_path.suffix.lower() == ".tsv":
        csv_path = csv_path.with_suffix(".csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    if mode == "prepare":
        fields = ["source", "abbreviation", "category", "occurrences", "paragraph_ids", "sections", "target", "forbidden_variants"]
        writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "source": r.get("source", ""),
                "abbreviation": r.get("abbreviation", ""),
                "category": r.get("category", ""),
                "occurrences": r.get("occurrences", 0),
                "paragraph_ids": ";".join(str(x) for x in (r.get("paragraph_ids") or [])),
                "sections": ";".join(str(x) for x in (r.get("sections") or [])),
                "target": r.get("target", ""),
                "forbidden_variants": "",
            })
    else:
        fields = ["source", "target", "first_mention_anchor", "occurrences_source", "occurrences_target", "status", "violations_paragraphs"]
        writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "source": r.get("source", ""),
                "target": r.get("target", ""),
                "first_mention_anchor": r.get("first_mention_anchor", ""),
                "occurrences_source": r.get("occurrences_source", 0),
                "occurrences_target": r.get("occurrences_target", 0),
                "status": r.get("status", ""),
                "violations_paragraphs": ";".join(str(x) for x in (r.get("violations_paragraphs") or [])),
            })
    result_guard.atomic_write_text(csv_path, buf.getvalue())
    return csv_path


def _units_match(src_units: list[tuple[float, str, str]], tgt_units: list[tuple[float, str, str]]) -> tuple[bool, list[str]]:
    src_raw = [f"{u[2]} [{u[1]}]" for u in src_units]
    tgt_raw = [f"{u[2]} [{u[1]}]" for u in tgt_units]
    if len(src_units) != len(tgt_units):
        return False, [f"SI unit count mismatch: source={src_raw} vs target={tgt_raw}"]
    # Sort by (value, canonical_unit) so pairing swaps and unit drifts (5 mg->5 ug, 25 C->25 K, 50 mV->50 V)
    # are all reported individually without stopping at the first mismatch.
    s_sorted = sorted(src_units, key=lambda x: (x[0], x[1]))
    t_sorted = sorted(tgt_units, key=lambda x: (x[0], x[1]))
    diffs: list[str] = []
    for (sv, su, sr), (tv, tu, tr) in zip(s_sorted, t_sorted):
        if su != tu:
            diffs.append(f"SI unit drift: {sr!r} ({su}) vs {tr!r} ({tu})")
        elif not math.isclose(sv, tv, rel_tol=1e-5, abs_tol=1e-9):
            diffs.append(f"SI value-to-unit pairing drift: {sr!r} vs {tr!r}")
    return len(diffs) == 0, diffs


def _extract_figure_table_tags(text: str) -> list[tuple[str, str]]:
    tags: list[tuple[str, str]] = []
    for m in FIG_TAG_RE.finditer(text):
        tags.append(("fig", m.group(1).lower()))
    for m in TABLE_TAG_RE.finditer(text):
        tags.append(("table", m.group(1).lower()))
    for m in EQ_TAG_RE.finditer(text):
        tags.append(("eq", m.group(1).lower()))
    return sorted(tags)


def _extract_formula_symbols(text: str) -> tuple[list[str], list[str]]:
    latex_cmds = sorted(LATEX_CMD_RE.findall(text))
    # Normalize U+00B5 (micro sign) to U+03BC (Greek small letter mu)
    norm_txt = text.replace("\u00b5", "\u03bc")
    # Exclude mu when already part of SI unit (μg, μm, μL, μM, μV, μA, μs) since units are audited separately
    norm_txt = re.sub(r"\u03bc(?:g|m|L|M|V|A|s|mol)\b", " ", norm_txt)
    symbols = sorted(GREEK_MATH_RE.findall(norm_txt))
    return latex_cmds, symbols


def _term_present_in_source(term: str, source_text: str) -> bool:
    t = term.strip()
    if not t:
        return False
    if re.match(r"^[A-Za-z0-9]", t) and re.search(r"[A-Za-z0-9]$", t):
        pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", re.I)
        return bool(pat.search(source_text))
    return t.lower() in source_text.lower()


def _audit_bibliography_and_cross_item(
    draft_meta: dict[str, Any],
    segments: list[dict[str, Any]],
    lock_meta: dict[str, Any] | None,
) -> tuple[bool, bool, list[str]]:
    """Verify source bibliography identity is not overwritten and no neighbor item leaks in."""
    errors: list[str] = []
    bib_ok = True
    cross_ok = True

    if not lock_meta:
        return bib_ok, cross_ok, errors

    for field in ("item_id", "source_title", "doi"):
        expected = str(lock_meta.get(field) or "").strip()
        actual = str(draft_meta.get(field) or "").strip()
        if expected and actual != expected:
            bib_ok = False
            if field == "source_title" and _has_cjk(actual) and not _has_cjk(expected):
                errors.append(
                    f"BIBLIOGRAPHY_TITLE_OVERWRITTEN: source_title overwritten by translation ({actual!r}); "
                    f"keep source_title={expected!r} and place Chinese title in translated_title"
                )
            else:
                errors.append(
                    f"BIBLIOGRAPHY_IDENTITY_DRIFT: {field} changed from {expected!r} to {actual!r}"
                )

    exp_authors = [str(a).strip() for a in (lock_meta.get("authors") or []) if str(a).strip()]
    act_authors = [str(a).strip() for a in (draft_meta.get("authors") or []) if str(a).strip()]
    if exp_authors and act_authors != exp_authors:
        bib_ok = False
        errors.append(f"BIBLIOGRAPHY_AUTHORS_DRIFT: expected {exp_authors} != actual {act_authors}")

    locked_item_id = str(lock_meta.get("item_id") or "").strip()
    for seg in segments:
        seg_item = str(seg.get("item_id") or "").strip()
        if locked_item_id and seg_item and seg_item != locked_item_id:
            cross_ok = False
            errors.append(
                f"CROSS_ITEM_CONTAMINATION [{seg.get('paragraph_id')}]: segment item_id={seg_item!r} "
                f"does not match locked item_id={locked_item_id!r}"
            )

    neighbors = lock_meta.get("neighbor_items") or []
    combined_targets = "\n".join(str(s.get("target_zh") or "") for s in segments)
    combined_meta = json.dumps(draft_meta, ensure_ascii=False)
    for nb in neighbors:
        nb_id = str(nb.get("item_id") or "").strip()
        nb_doi = str(nb.get("doi") or "").strip()
        nb_title = str(nb.get("source_title") or "").strip()
        if nb_doi and (nb_doi.lower() in combined_targets.lower() or nb_doi.lower() in combined_meta.lower()):
            cross_ok = False
            errors.append(f"CROSS_ITEM_CONTAMINATION: neighbor DOI {nb_doi!r} leaked into target output")
        if nb_id and len(nb_id) >= 4 and nb_id in combined_meta and nb_id != locked_item_id:
            cross_ok = False
            errors.append(f"CROSS_ITEM_CONTAMINATION: neighbor item_id {nb_id!r} leaked into source_metadata")
        if nb_title and len(nb_title) >= 16 and nb_title.lower() in combined_targets.lower():
            cross_ok = False
            errors.append(f"CROSS_ITEM_CONTAMINATION: neighbor title {nb_title!r} leaked into translated segments")

    return bib_ok, cross_ok, errors


def audit_structured_draft(
    draft_data: dict[str, Any],
    *,
    source_segments_doc: dict[str, Any] | None = None,
    metadata_lock: dict[str, Any] | None = None,
    glossary_data: Any = None,
) -> dict[str, Any]:
    """Full structured bilingual audit covering bibliography, alignment, terms, and all invariants."""
    errors: list[str] = []
    warnings: list[str] = []
    uncertainty_or_gaps: list[dict[str, Any]] = []

    segments = draft_data.get("bilingual_segments")
    if not isinstance(segments, list):
        return {
            "passed": False,
            "errors": ["bilingual_segments must be a list"],
            "warnings": [],
            "invariants_check": {"passed": False},
        }

    draft_meta = draft_data.get("source_metadata") if isinstance(draft_data.get("source_metadata"), dict) else {}
    bib_ok, cross_ok, bib_errs = _audit_bibliography_and_cross_item(draft_meta, segments, metadata_lock)
    errors.extend(bib_errs)

    # 1:1 Paragraph alignment check
    para_align_ok = True
    src_segs = (source_segments_doc or {}).get("segments") or []
    if src_segs:
        expected_ids = [str(s["paragraph_id"]) for s in src_segs]
        actual_ids = [str(s.get("paragraph_id") or "") for s in segments]
        src_map = {str(s["paragraph_id"]): s for s in src_segs}

        missing_ids = [pid for pid in expected_ids if pid not in actual_ids]
        extra_ids = [pid for pid in actual_ids if pid not in src_map]
        dup_ids = [pid for pid, cnt in Counter(actual_ids).items() if cnt > 1]
        if missing_ids:
            para_align_ok = False
            errors.append(f"PARAGRAPH_MISSING: missing source paragraph_id(s): {missing_ids}")
        if dup_ids:
            para_align_ok = False
            errors.append(f"PARAGRAPH_DUPLICATE: duplicate paragraph_id(s): {dup_ids}")
        if extra_ids:
            para_align_ok = False
            errors.append(f"PARAGRAPH_UNEXPECTED: unexpected paragraph_id(s): {extra_ids}")
        if not missing_ids and not dup_ids and not extra_ids and expected_ids != actual_ids:
            para_align_ok = False
            errors.append("PARAGRAPH_ORDER_MISMATCH: bilingual_segments order differs from immutable source_segments")

        for seg in segments:
            pid = str(seg.get("paragraph_id") or "")
            if pid in src_map:
                exp_txt = _norm_ws(str(src_map[pid].get("text") or ""))
                act_txt = _norm_ws(str(seg.get("source_en") or ""))
                if exp_txt and act_txt and exp_txt != act_txt:
                    para_align_ok = False
                    errors.append(f"{pid}: SOURCE_TEXT_MUTATED (source_en does not match immutable source_segments)")

    # Prepare glossary entries
    glossary_entries: list[dict[str, Any]] = []
    if isinstance(glossary_data, dict):
        raw_g = glossary_data.get("terms") or []
    elif isinstance(glossary_data, list):
        raw_g = glossary_data
    else:
        raw_g = []
    for item in raw_g:
        if isinstance(item, dict) and str(item.get("source", "")).strip() and str(item.get("target", "")).strip():
            glossary_entries.append({
                "source": str(item["source"]).strip(),
                "target": str(item["target"]).strip(),
                "first_mention_anchor": str(item.get("first_mention_anchor") or f"{item['target']} ({item['source']})").strip(),
                "forbidden_variants": [str(f).strip() for f in (item.get("forbidden_variants") or []) if str(f).strip()],
                "occurrences_source": 0,
                "occurrences_target": 0,
                "violations_paragraphs": [],
            })

    # Per-paragraph invariant checks
    numeric_drift = 0
    unit_drift = 0
    formula_drift = 0
    citation_drift = 0
    fig_table_drift = 0
    negation_drift = 0
    modal_upgrade_drift = 0
    sentence_density_drift = 0
    discourse_logic_drift = 0
    para_checks: list[dict[str, Any]] = []

    total_term_checks = 0
    passed_term_checks = 0

    for idx, seg in enumerate(segments, start=1):
        pid = str(seg.get("paragraph_id") or f"P{idx:03d}")
        sec = str(seg.get("section") or "Body")
        sp = str(seg.get("source_en") or "").strip()
        tp = str(seg.get("target_zh") or "").strip()
        needs_review = bool(seg.get("needs_human_review", False))
        review_reason = str(seg.get("review_reason") or "").strip()

        if needs_review:
            uncertainty_or_gaps.append({
                "paragraph_id": pid,
                "category": "needs_human_review",
                "reason": review_reason or "Flagged by translator for human verification.",
                "recommended_action": "Verify translation against original source segment.",
            })

        if not tp:
            errors.append(f"{pid} ({sec}): EMPTY_TRANSLATION")
            continue

        # Sentence-count & information-density alignment (catches implicit half-sentence omission & summary collapse)
        dens_ok, dens_errs, dens_warns, dens_info = _audit_sentence_and_density(sp, tp, needs_review=needs_review)
        if not dens_ok:
            sentence_density_drift += len(dens_errs)
            errors.extend([f"{pid} ({sec}): {d}" for d in dens_errs])
        warnings.extend([f"{pid} ({sec}): {w}" for w in dens_warns])

        # Logical discourse connector fidelity (concession/contrast & causality/consequence)
        disc_ok, disc_errs = _audit_discourse_connectors(sp, tp)
        if not disc_ok:
            discourse_logic_drift += len(disc_errs)
            errors.extend([f"{pid} ({sec}): {d}" for d in disc_errs])

        # Numbers + CN/EN magnitudes + thousands-separated sample sizes (n=1,010)
        s_nums = _extract_normalized_numbers(sp)
        t_nums = _extract_normalized_numbers(tp)
        num_ok, num_diffs = _numbers_match(s_nums, t_nums)
        s_ns = _extract_sample_sizes(sp)
        t_ns = _extract_sample_sizes(tp)
        n_ok, n_diffs = _sample_sizes_match(s_ns, t_ns)
        if not num_ok:
            numeric_drift += len(num_diffs)
            errors.extend([f"{pid} ({sec}): {d}" for d in num_diffs])
        if not n_ok:
            num_ok = False
            numeric_drift += len(n_diffs)
            errors.extend([f"{pid} ({sec}): {d}" for d in n_diffs])

        # Simple & compound SI unit-value pairing (e.g., 10 mA cm^-2, 55.69/10,000, 5 mg)
        s_units = _extract_physical_units(sp)
        t_units = _extract_physical_units(tp)
        units_ok, unit_diffs = _units_match(s_units, t_units)
        if not units_ok:
            unit_drift += len(unit_diffs)
            errors.extend([f"{pid} ({sec}): {d}" for d in unit_diffs])

        # Citations
        s_cites = sorted(CITE_RE.findall(sp))
        t_cites = sorted(CITE_RE.findall(tp))
        cites_ok = s_cites == t_cites
        if not cites_ok:
            citation_drift += 1
            errors.append(f"{pid} ({sec}): CITATION_DRIFT ({s_cites} != {t_cites})")

        # Figure / Table / Equation tags
        s_tags = _extract_figure_table_tags(sp)
        t_tags = _extract_figure_table_tags(tp)
        tags_ok = s_tags == t_tags
        if not tags_ok:
            fig_table_drift += 1
            errors.append(f"{pid} ({sec}): FIGURE_TABLE_TAG_DRIFT ({s_tags} != {t_tags})")

        # Formulas & Greek/math symbols
        s_ltx, s_sym = _extract_formula_symbols(sp)
        t_ltx, t_sym = _extract_formula_symbols(tp)
        form_ok = (s_ltx == t_ltx) and (Counter(s_sym) == Counter(t_sym))
        if not form_ok:
            formula_drift += 1
            errors.append(
                f"{pid} ({sec}): FORMULA_SYMBOL_DRIFT (latex {s_ltx} vs {t_ltx}; symbols {s_sym} vs {t_sym})"
            )

        # Statistical CI / p-value presence
        if STAT_CI_RE.search(sp) and not STAT_CI_RE.search(tp):
            warnings.append(f"{pid} ({sec}): 95% CI / confidence interval marker may be omitted in target")
        if STAT_PVAL_RE.search(sp) and not STAT_PVAL_RE.search(tp):
            errors.append(f"{pid} ({sec}): P_VALUE_OMITTED in target")
            numeric_drift += 1

        # Negation polarity
        s_neg = len(NEG_EN_RE.findall(sp)) + len(NEG_ZH_RE.findall(sp))
        t_neg = len(NEG_EN_RE.findall(tp)) + len(NEG_ZH_RE.findall(tp))
        neg_ok = (s_neg > 0) == (t_neg > 0)
        if not neg_ok:
            negation_drift += 1
            errors.append(f"{pid} ({sec}): NEGATION_POLARITY_FLIP (source_neg={s_neg}, target_neg={t_neg})")

        # Modal hedge conservation & anti-certainty-upgrade check
        s_hedge = len(HEDGE_EN_RE.findall(sp))
        s_cert = len(CERTAINTY_EN_RE.findall(sp))
        t_hedge = len(HEDGE_ZH_RE.findall(tp))
        t_upgrade = CERTAINTY_UPGRADE_ZH_RE.findall(tp)
        modal_ok = True
        if s_hedge > 0 and s_cert == 0 and t_upgrade:
            modal_ok = False
            modal_upgrade_drift += 1
            errors.append(
                f"{pid} ({sec}): MODAL_CERTAINTY_UPGRADE (tentative source translated with certainty marker(s) {t_upgrade})"
            )
        elif HARD_LIMITER_EN_RE.search(sp) and not HARD_LIMITER_ZH_RE.search(tp):
            modal_ok = False
            modal_upgrade_drift += 1
            errors.append(f"{pid} ({sec}): HARD_LIMITER_OMITTED (only/rarely/limited to omitted in target)")
        elif s_hedge > 0 and t_hedge == 0:
            warnings.append(f"{pid} ({sec}): MODAL_HEDGE_WEAKENED (source_hedges={s_hedge}, target_hedges=0)")

        # Paragraph-level glossary enforcement across Title/Abstract/Body/Caption
        para_term_violations: list[str] = []
        for g in glossary_entries:
            src_term = g["source"]
            req_tgt = g["target"]
            if _term_present_in_source(src_term, sp):
                g["occurrences_source"] += 1
                total_term_checks += 1
                if req_tgt.casefold() in tp.casefold():
                    g["occurrences_target"] += 1
                    passed_term_checks += 1
                else:
                    msg = f"{pid} ({sec}): GLOSSARY_MISMATCH for {src_term!r} (expected {req_tgt!r})"
                    errors.append(msg)
                    para_term_violations.append(src_term)
                    if pid not in g["violations_paragraphs"]:
                        g["violations_paragraphs"].append(pid)
            for fv in g["forbidden_variants"]:
                if fv.casefold() in tp.casefold():
                    msg = f"{pid} ({sec}): GLOSSARY_FORBIDDEN_VARIANT {fv!r} used for {src_term!r} (must use {req_tgt!r})"
                    errors.append(msg)
                    para_term_violations.append(f"forbidden:{fv}")
                    if pid not in g["violations_paragraphs"]:
                        g["violations_paragraphs"].append(pid)

        para_checks.append({
            "paragraph_id": pid,
            "section": sec,
            "numbers_ok": num_ok,
            "units_ok": units_ok,
            "citations_ok": cites_ok,
            "figure_table_ok": tags_ok,
            "formulas_ok": form_ok,
            "negation_ok": neg_ok,
            "modal_ok": modal_ok,
            "sentence_density_ok": dens_ok,
            "discourse_logic_ok": disc_ok,
            "density_stats": dens_info,
            "glossary_violations": para_term_violations,
        })

    # Add source-side page coverage gaps if any PDF pages had empty text layers
    page_cov = (source_segments_doc or {}).get("page_coverage") or {}
    empty_pages = page_cov.get("empty_or_scanned_pages") or []
    if empty_pages:
        uncertainty_or_gaps.append({
            "paragraph_id": "PDF_PAGES",
            "category": "empty_or_scanned_pages",
            "reason": f"Pages {empty_pages} had no extractable text layer; visual/OCR verification required.",
            "recommended_action": "Run OCR or inspect scanned PDF pages manually.",
        })

    term_rate = round(passed_term_checks / total_term_checks, 4) if total_term_checks > 0 else 1.0
    terminology_table = [
        {
            "source": g["source"],
            "target": g["target"],
            "first_mention_anchor": g["first_mention_anchor"],
            "occurrences_source": g["occurrences_source"],
            "occurrences_target": g["occurrences_target"],
            "violations_paragraphs": g["violations_paragraphs"],
            "status": "locked_ok" if not g["violations_paragraphs"] else "violation",
        }
        for g in glossary_entries
    ]

    invariants_check = {
        "passed": len(errors) == 0,
        "bibliography_identity_ok": bib_ok,
        "cross_item_clean": cross_ok,
        "paragraph_alignment_ok": para_align_ok,
        "terminology_consistency_rate": term_rate,
        "numeric_drift": numeric_drift,
        "unit_drift": unit_drift,
        "formula_drift": formula_drift,
        "citation_drift": citation_drift,
        "figure_table_drift": fig_table_drift,
        "negation_drift": negation_drift,
        "modal_upgrade_drift": modal_upgrade_drift,
        "sentence_density_drift": sentence_density_drift,
        "discourse_logic_drift": discourse_logic_drift,
        "page_coverage": page_cov,
        "blocking_errors": errors,
        "warnings": warnings,
    }

    return {
        "passed": len(errors) == 0,
        "source_paragraphs": len(src_segs) if src_segs else len(segments),
        "target_paragraphs": len(segments),
        "errors": errors,
        "warnings": warnings,
        "terminology_table": terminology_table,
        "invariants_check": invariants_check,
        "uncertainty_or_gaps": uncertainty_or_gaps,
        "paragraph_checks": para_checks,
    }


def render_bilingual_markdown(
    draft_data: dict[str, Any],
    audit_report: dict[str, Any],
    metadata_lock: dict[str, Any] | None = None,
) -> str:
    """Render verifiable bilingual Markdown deliverable with protected header, glossary, and review checklist."""
    meta = dict(metadata_lock or {})
    meta.update(draft_data.get("source_metadata") or {})
    inv = audit_report.get("invariants_check") or {}

    lines: list[str] = [
        "# Bilingual Scientific Translation Deliverable",
        "",
        "## 1. Protected Source Metadata",
        f"- **Item ID**: `{meta.get('item_id', 'unknown')}`",
        f"- **Original Title (`source_title`, immutable)**: {meta.get('source_title', 'N/A')}",
        f"- **Translated Title (`translated_title`)**: {meta.get('translated_title', 'N/A')}",
        f"- **Authors**: {', '.join(meta.get('authors') or []) or 'N/A'}",
        f"- **DOI**: {meta.get('doi') or 'N/A'}",
        f"- **Year**: {meta.get('year') or 'N/A'}",
        f"- **Audit Verdict**: {'PASS' if audit_report.get('passed') else 'NEEDS_FIX'} "
        f"(Terminology consistency: {inv.get('terminology_consistency_rate', 1.0):.1%}, "
        f"Numeric drift: {inv.get('numeric_drift', 0)}, Unit drift: {inv.get('unit_drift', 0)}, "
        f"Negation drift: {inv.get('negation_drift', 0)}, Modal upgrade drift: {inv.get('modal_upgrade_drift', 0)})",
        "",
        "## 2. Locked Terminology Table",
        "",
        "| Source Term | Canonical Chinese | First-Mention Anchor | Source Hits | Status |",
        "| --- | --- | --- | ---: | --- |",
    ]
    terms = audit_report.get("terminology_table") or []
    if terms:
        for t in terms:
            lines.append(
                f"| {t['source']} | {t['target']} | {t['first_mention_anchor']} | "
                f"{t['occurrences_source']} | {t['status']} |"
            )
    else:
        lines.append("| (none locked) | - | - | 0 | n/a |")

    lines.extend(["", "## 3. Paragraph-Aligned Bilingual Translation", ""])
    for seg in draft_data.get("bilingual_segments") or []:
        pid = seg.get("paragraph_id", "P?")
        sec = seg.get("section", "Body")
        page = seg.get("page")
        page_str = f" | Page {page}" if page is not None else ""
        lines.append(f"### [{pid}] {sec}{page_str}")
        lines.append("")
        lines.append(f"**Source (`{pid}`)**: {seg.get('source_en', '')}")
        lines.append("")
        lines.append(f"**Translation (`{pid}`)**: {seg.get('target_zh', '')}")
        lines.append("")
        terms_used = ", ".join(seg.get("terms_used") or []) or "none"
        basis = seg.get("translation_basis") or "Direct faithful translation preserving domain terminology and quantitative invariants."
        flag = f" | **[NEEDS HUMAN REVIEW: {seg.get('review_reason', 'verify against source')}]**" if seg.get("needs_human_review") else ""
        lines.append(f"- **Verification**: Terms=`{terms_used}` | Basis={basis}{flag}")
        lines.append("")

    lines.extend(["## 4. Human Review & Uncertainty Checklist", ""])
    gaps = audit_report.get("uncertainty_or_gaps") or []
    if gaps:
        for g in gaps:
            lines.append(f"- **[{g['paragraph_id']}]** ({g['category']}): {g['reason']} -> {g['recommended_action']}")
    else:
        lines.append("- No unresolved ambiguities or degraded source pages flagged.")

    if audit_report.get("errors"):
        lines.extend(["", "## 5. Blocking Audit Findings", ""])
        for err in audit_report["errors"]:
            lines.append(f"- {err}")

    return "\n".join(lines).rstrip() + "\n"


_MD_HEADER_PID_RE = re.compile(r"^###\s+\[([A-Za-z0-9_.-]+)\]\s*", re.M)
_MD_TRANS_LINE_RE = re.compile(r"^\*\*Translation\s*\(`([A-Za-z0-9_.-]+)`\)\*\*:\s*(.*)$", re.M)
_VALID_TERMINAL_END_RE = re.compile(r"[。！？.!?）”’\"'】》\]]\s*$")
_DANGLING_TAIL_WORD_RE = re.compile(
    r"(?:(?:的|和|与|及|或|为|在|由|从|至|到|对|将|把|被|以|中|于|且|但|而|并|其|该|等|即|如|若|当)\s*$|"
    r"\b(?:the|a|an|of|and|or|in|to|for|with|from|by|at|on|as|that|which|is|are|was|were)\s*$)",
    re.I,
)
_TRUNCATED_TAIL_PUNCT_RE = re.compile(r"(?:\.{2,}|…|[,，、:：;；—–\-/=<≤≥(（\[【{]\s*$)")
_TRUNCATED_NUMERIC_END_RE = re.compile(r"(?:(?:n|p|CI|IC)\s*[=<>≤≥:]?\s*\d*\.?\d*|\d+(?:\.\d*)?)\s*$", re.I)


def verify_rendered_bilingual_markdown(
    markdown_path: Path,
    expected_paragraph_ids: list[str],
) -> tuple[bool, list[str]]:
    """Read back rendered translation_bilingual.md and verify exact 1-to-1 paragraph coverage & zero truncation."""
    errors: list[str] = []
    if not markdown_path.is_file():
        return False, [f"MARKDOWN_READBACK_MISSING: file not found at {markdown_path}"]

    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return False, ["MARKDOWN_READBACK_EMPTY: translation_bilingual.md is empty"]

    header_pids = _MD_HEADER_PID_RE.findall(text)
    trans_matches = _MD_TRANS_LINE_RE.findall(text)
    trans_pids = [pid for pid, _ in trans_matches]

    hdr_counts = Counter(header_pids)
    tr_counts = Counter(trans_pids)

    for pid in expected_paragraph_ids:
        hc = hdr_counts.get(pid, 0)
        tc = tr_counts.get(pid, 0)
        if hc == 0:
            errors.append(f"MARKDOWN_MISSING_PARAGRAPH_HEADER: expected paragraph_id '{pid}' absent from headings")
        elif hc > 1:
            errors.append(f"MARKDOWN_DUPLICATE_PARAGRAPH_HEADER: paragraph_id '{pid}' appeared {hc} times in headings")
        if tc == 0:
            errors.append(f"MARKDOWN_MISSING_TRANSLATION_LINE: expected paragraph_id '{pid}' absent from Translation blocks")
        elif tc > 1:
            errors.append(f"MARKDOWN_DUPLICATE_TRANSLATION_LINE: paragraph_id '{pid}' appeared {tc} times in Translation blocks")

    expected_set = set(expected_paragraph_ids)
    for pid, cnt in tr_counts.items():
        if pid not in expected_set:
            errors.append(f"MARKDOWN_UNEXPECTED_PARAGRAPH_ID: unexpected paragraph_id '{pid}' in Translation blocks")
        elif cnt > 1 and pid not in expected_set:
            errors.append(f"MARKDOWN_DUPLICATE_TRANSLATION_LINE: paragraph_id '{pid}' appeared {cnt} times")

    # Sentence completeness & anti-truncation check on every rendered translation paragraph
    bracket_pairs = (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"), ("《", "》"), ("{", "}"))
    for pid, body in trans_matches:
        st = body.strip()
        if not st:
            errors.append(f"MARKDOWN_EMPTY_TRANSLATION: paragraph '{pid}' has empty translation text")
            continue
        for op, cl in bracket_pairs:
            if st.count(op) != st.count(cl):
                errors.append(
                    f"MARKDOWN_UNCLOSED_BRACKET: paragraph '{pid}' has unbalanced '{op}'/'{cl}' "
                    f"({st.count(op)} vs {st.count(cl)}), indicating possible truncation"
                )
        if _TRUNCATED_TAIL_PUNCT_RE.search(st):
            errors.append(
                f"MARKDOWN_TRUNCATED_ENDING_PUNCT: paragraph '{pid}' ends abruptly with dangling punctuation/ellipsis ({st[-24:]!r})"
            )
        elif _TRUNCATED_NUMERIC_END_RE.search(st):
            errors.append(
                f"MARKDOWN_TRUNCATED_NUMERIC_TAIL: paragraph '{pid}' ends with an incomplete/unpunctuated numeric token ({st[-24:]!r})"
            )
        elif _DANGLING_TAIL_WORD_RE.search(st):
            errors.append(
                f"MARKDOWN_DANGLING_TAIL_WORD: paragraph '{pid}' ends with a dangling connector/function word ({st[-24:]!r})"
            )
        elif not _VALID_TERMINAL_END_RE.search(st):
            errors.append(
                f"MARKDOWN_INCOMPLETE_FINAL_SENTENCE: paragraph '{pid}' lacks terminal sentence punctuation ({st[-24:]!r})"
            )

    return len(errors) == 0, errors


def emit_final_deliverables(
    draft_data: dict[str, Any],
    audit_report: dict[str, Any],
    *,
    metadata_lock: dict[str, Any] | None,
    result_path: Path,
    markdown_path: Path,
    glossary_csv_path: Path | None = None,
    expected_paragraph_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Atomically emit translation_bilingual.md, read it back to block duplicates/omissions/truncation, and emit result.json."""
    md_text = render_bilingual_markdown(draft_data, audit_report, metadata_lock)
    result_guard.atomic_write_text(markdown_path, md_text)

    segs = draft_data.get("bilingual_segments") or []
    exp_pids = list(expected_paragraph_ids) if expected_paragraph_ids else [
        str(s.get("paragraph_id") or "") for s in segs if str(s.get("paragraph_id") or "")
    ]
    readback_ok, readback_errors = verify_rendered_bilingual_markdown(markdown_path, exp_pids)
    inv = audit_report.setdefault("invariants_check", {})
    inv["markdown_readback_ok"] = readback_ok
    inv["markdown_readback_errors"] = readback_errors
    if not readback_ok:
        audit_report.setdefault("errors", []).extend(readback_errors)
        audit_report["passed"] = False
        # Re-render markdown so Section 5 records blocking readback errors transparently
        md_text = render_bilingual_markdown(draft_data, audit_report, metadata_lock)
        result_guard.atomic_write_text(markdown_path, md_text)

    artifacts_list = ["result.json", markdown_path.name]
    if glossary_csv_path is not None:
        actual_csv = export_glossary_csv(audit_report.get("terminology_table") or [], glossary_csv_path, mode="audit")
        if actual_csv.parent.resolve() == result_path.parent.resolve():
            artifacts_list.append(actual_csv.name)

    case_id = str(draft_data.get("case_id") or (metadata_lock or {}).get("case_id") or "translation-case-01")
    if not result_path.is_file():
        result_guard.init_result(result_path, case_id=case_id, task_id="product-arena-01-5a9928c1")

    gaps: list[dict[str, Any]] = []
    if not readback_ok:
        gaps.append({
            "code": "MARKDOWN_READBACK_INTEGRITY_FAILED",
            "severity": "error",
            "source": markdown_path.name,
            "message": "; ".join(readback_errors[:12]),
        })
    if audit_report.get("errors"):
        gaps.append({
            "code": "BILINGUAL_INVARIANT_VIOLATIONS",
            "severity": "error",
            "source": "bilingual_alignment_guard",
            "message": "; ".join(audit_report["errors"][:12]),
        })
    for u in audit_report.get("uncertainty_or_gaps") or []:
        gaps.append({
            "code": str(u.get("category", "NEEDS_HUMAN_REVIEW")).upper(),
            "severity": "warning",
            "source": str(u.get("paragraph_id", "source")),
            "message": str(u.get("reason", "")),
        })

    src_cnt = max(audit_report.get("source_paragraphs", len(segs)), 1)
    needs_rev_cnt = sum(1 for s in segs if s.get("needs_human_review"))

    if not audit_report.get("passed"):
        trans_status = "needs_human_review"
    elif needs_rev_cnt > 0 or bool((inv.get("page_coverage") or {}).get("empty_or_scanned_pages")):
        trans_status = "partial"
    else:
        trans_status = "completed"

    summary = draft_data.get("summary") if isinstance(draft_data.get("summary"), dict) else {}
    summary.setdefault("domain", draft_data.get("domain", "scientific_literature"))
    summary["total_paragraphs"] = audit_report.get("source_paragraphs", len(segs))
    summary["translated_paragraphs"] = len(segs)
    summary["coverage_ratio"] = round(len(segs) / src_cnt, 4)
    summary["needs_human_review_count"] = needs_rev_cnt
    summary.setdefault("overview", f"Translated {len(segs)}/{src_cnt} paragraphs with locked terminology and invariant audit.")

    patch = {
        "schema_version": "2.0",
        "case_id": case_id,
        "task_id": "product-arena-01-5a9928c1",
        "translation_status": trans_status,
        "summary": summary,
        "source_metadata": draft_data.get("source_metadata") or (metadata_lock or {}),
        "bilingual_segments": segs,
        "terminology_table": audit_report.get("terminology_table") or [],
        "invariants_check": inv,
        "uncertainty_or_gaps": audit_report.get("uncertainty_or_gaps") or [],
        "errors": list(audit_report.get("errors") or []),
        "artifacts": artifacts_list,
        "metrics": {
            "source_paragraphs": audit_report.get("source_paragraphs", len(segs)),
            "translated_paragraphs": len(segs),
            "terminology_consistency_rate": inv.get("terminology_consistency_rate", 1.0),
            "blocking_error_count": len(audit_report.get("errors") or []),
            "needs_human_review_count": needs_rev_cnt,
            "markdown_readback_ok": readback_ok,
        },
        "metric_provenance": {
            "source_paragraphs": "source_segments.json",
            "translated_paragraphs": "bilingual_segments",
            "terminology_consistency_rate": "bilingual_alignment_guard:glossary",
            "blocking_error_count": "bilingual_alignment_guard:audit",
            "needs_human_review_count": "bilingual_segments[].needs_human_review",
            "markdown_readback_ok": "bilingual_alignment_guard:verify_rendered_bilingual_markdown",
        },
        "findings": [
            f"Audited {len(segs)} bilingual paragraph(s); passed={audit_report.get('passed')}, "
            f"markdown_readback_ok={readback_ok}, "
            f"terminology_consistency={inv.get('terminology_consistency_rate', 1.0):.1%}, "
            f"blocking_errors={len(audit_report.get('errors') or [])}."
        ],
        "gaps": gaps,
        "warnings": audit_report.get("warnings") or [],
    }
    return result_guard.update_result(result_path, patch, finalize=True)


def audit_bilingual_pair(
    source_path: Path,
    target_path: Path,
    glossary_path: Path | None = None,
) -> dict[str, Any]:
    """Legacy two-file source/target pair auditing compatibility entrypoint."""
    src_paras = [p.strip() for p in re.split(r"\n\s*\n+", source_path.read_text(encoding="utf-8", errors="replace")) if p.strip()]
    tgt_paras = [p.strip() for p in re.split(r"\n\s*\n+", target_path.read_text(encoding="utf-8", errors="replace")) if p.strip()]

    src_segs = [{"paragraph_id": f"P{i:03d}", "section": "Body", "text": sp} for i, sp in enumerate(src_paras, start=1)]
    draft_segs = [
        {
            "paragraph_id": f"P{i:03d}",
            "section": "Body",
            "source_en": src_paras[i - 1] if i - 1 < len(src_paras) else "",
            "target_zh": tp,
        }
        for i, tp in enumerate(tgt_paras, start=1)
    ]
    gdata = json.loads(glossary_path.read_text(encoding="utf-8")) if (glossary_path and glossary_path.is_file()) else None
    return audit_structured_draft(
        {"case_id": "pair-audit", "bilingual_segments": draft_segs},
        source_segments_doc={"segments": src_segs, "page_coverage": {"source_mode": "pair_files"}},
        metadata_lock=None,
        glossary_data=gdata,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Two-stage bilingual scientific translation preparation and invariant auditor.")
    parser.add_argument("--prepare", action="store_true", help="Stage 1: segment input, lock source bibliography, extract glossary candidates.")
    parser.add_argument("--audit", action="store_true", help="Stage 2: audit structured translation draft and optionally emit deliverables.")
    parser.add_argument("--input", default="", help="Input directory for --prepare.")
    parser.add_argument("--work", default="", help="Work directory for --prepare.")
    parser.add_argument("--output", default="", help="Output directory for --prepare.")
    parser.add_argument("--draft", default="", help="Structured translation draft JSON for --audit.")
    parser.add_argument("--source-segments", default="", help="Optional source_segments.json from --prepare.")
    parser.add_argument("--metadata-lock", default="", help="Optional source_metadata_lock.json from --prepare.")
    parser.add_argument("--glossary", default="", help="Optional frozen glossary.json.")
    parser.add_argument("--source", default="", help="Legacy plain-text source document path.")
    parser.add_argument("--target", default="", help="Legacy plain-text translated target document path.")
    parser.add_argument("--out-json", default="", help="Output alignment audit JSON path.")
    parser.add_argument("--emit-result", default="", help="Optional /app/output/result.json path to write after audit.")
    parser.add_argument("--emit-markdown", default="", help="Optional /app/output/translation_bilingual.md path to write after audit.")
    parser.add_argument("--verify-markdown", default="", help="Standalone read-back verification of an existing translation_bilingual.md.")
    parser.add_argument("--export-glossary-csv", default="", help="Optional reusable UTF-8 CSV path to export structured bilingual glossary (never .tsv).")
    args = parser.parse_args(argv)

    if args.verify_markdown:
        src_doc = json.loads(Path(args.source_segments).read_text(encoding="utf-8")) if (args.source_segments and Path(args.source_segments).is_file()) else None
        exp_pids = [str(s.get("paragraph_id") or "") for s in ((src_doc or {}).get("segments") or []) if str(s.get("paragraph_id") or "")]
        if not exp_pids:
            # Derive expected unique paragraph IDs from markdown headers when source_segments is not supplied
            exp_pids = list(dict.fromkeys(_MD_HEADER_PID_RE.findall(Path(args.verify_markdown).read_text(encoding="utf-8", errors="replace"))))
        ok_rb, errs_rb = verify_rendered_bilingual_markdown(Path(args.verify_markdown), exp_pids)
        out_rb = {
            "passed": ok_rb,
            "status": "ok" if ok_rb else "partial_with_gaps",
            "expected_paragraph_ids": exp_pids,
            "errors": errs_rb,
        }
        if args.out_json:
            result_guard.atomic_write_json(args.out_json, out_rb)
        print(json.dumps(out_rb, ensure_ascii=False, indent=2))
        return 0 if ok_rb else 1

    if args.prepare:
        paths = preflight_probe.resolve_default_paths(
            input_dir=args.input or None,
            output_dir=args.output or None,
            work_dir=args.work or None,
        )
        rep = prepare_workspace(paths["input_dir"], paths["work_dir"], paths["output_dir"])
        if args.export_glossary_csv:
            gcand = json.loads((paths["work_dir"] / "glossary_candidates.json").read_text(encoding="utf-8"))
            csv_out = export_glossary_csv(gcand.get("candidates") or [], Path(args.export_glossary_csv), mode="prepare")
            rep["exported_glossary_csv"] = str(csv_out)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 1

    if args.draft:
        draft_data = json.loads(Path(args.draft).read_text(encoding="utf-8", errors="replace"))
        src_doc = json.loads(Path(args.source_segments).read_text(encoding="utf-8")) if (args.source_segments and Path(args.source_segments).is_file()) else None
        lock_doc = json.loads(Path(args.metadata_lock).read_text(encoding="utf-8")) if (args.metadata_lock and Path(args.metadata_lock).is_file()) else None
        gloss_doc = json.loads(Path(args.glossary).read_text(encoding="utf-8")) if (args.glossary and Path(args.glossary).is_file()) else None

        rep = audit_structured_draft(
            draft_data,
            source_segments_doc=src_doc,
            metadata_lock=lock_doc,
            glossary_data=gloss_doc,
        )
        csv_path = Path(args.export_glossary_csv) if args.export_glossary_csv else None
        exp_pids = [str(s.get("paragraph_id") or "") for s in ((src_doc or {}).get("segments") or []) if str(s.get("paragraph_id") or "")]
        if args.emit_result and args.emit_markdown:
            emit_final_deliverables(
                draft_data,
                rep,
                metadata_lock=lock_doc,
                result_path=Path(args.emit_result),
                markdown_path=Path(args.emit_markdown),
                glossary_csv_path=csv_path,
                expected_paragraph_ids=exp_pids or None,
            )
        elif csv_path is not None:
            export_glossary_csv(rep.get("terminology_table") or [], csv_path, mode="audit")
        if args.out_json:
            result_guard.atomic_write_json(args.out_json, rep)
        print(json.dumps({
            "passed": rep["passed"],
            "errors": rep["errors"],
            "warnings": rep["warnings"],
            "markdown_readback_ok": rep["invariants_check"].get("markdown_readback_ok", True),
            "terminology_consistency_rate": rep["invariants_check"]["terminology_consistency_rate"],
            "sentence_density_drift": rep["invariants_check"]["sentence_density_drift"],
            "discourse_logic_drift": rep["invariants_check"]["discourse_logic_drift"],
        }, ensure_ascii=False, indent=2))
        return 0 if rep["passed"] else 1

    if args.source and args.target:
        rep = audit_bilingual_pair(Path(args.source), Path(args.target), Path(args.glossary) if args.glossary else None)
        if args.out_json:
            result_guard.atomic_write_json(args.out_json, rep)
        print(json.dumps({"passed": rep["passed"], "errors": rep["errors"], "warnings": rep["warnings"]}, ensure_ascii=False, indent=2))
        return 0 if rep["passed"] else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
