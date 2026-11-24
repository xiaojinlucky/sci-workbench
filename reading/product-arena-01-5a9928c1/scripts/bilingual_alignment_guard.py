#!/usr/bin/env python3
"""Two-stage bilingual scientific translation preparation and deterministic invariant auditor.

Modes:
  1. --prepare: Reads /input/case.json (and PDF/Markdown/TXT under /input), creates immutable
     paragraph anchors (/app/work/source_segments.json), locks source bibliography identity
     (/app/work/source_metadata_lock.json), extracts terminology candidates
     (/app/work/glossary_candidates.json), and initializes /app/output/result.json.
  2. --audit: Audits a drafted translation JSON (--draft) or legacy source/target files
     (--source / --target) against bibliography identity locks, cross-item contamination,
     1:1 paragraph completeness, glossary consistency, numbers + CN/EN magnitudes,
     SI unit-value pairing, formulas/Greek symbols, citations, figure/table tags,
     negation polarity, and modal hedge preservation; optionally emits final
     /app/output/result.json and /app/output/translation_bilingual.md.

Pure Python 3.12 standard library (uses optional pdftotext/pdfinfo when available).
"""

from __future__ import annotations

import argparse
import hashlib
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
    r"(?<![A-Za-z0-9_])(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|万|亿|千|thousand|million|billion)?(?![A-Za-z0-9_])",
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

# Negation & epistemic modal regexes
NEG_EN_RE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|none|cannot|can't|fail(?:s|ed)?\s+to|unlike|absent|rarely|seldom|lack(?:s|ed)?|no\s+longer)\b",
    re.I,
)
NEG_ZH_RE = re.compile(
    r"不[是为会对能应可存在显同再足具构产导支影改包适允受必宜]|未[能见曾达发现观察出现报告]|无[法显明显需关]|并非|绝非|没有|缺乏|罕见|极少|不同于|否(?:定|认)"
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


def _extract_pdf_pages(pdf_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract page-by-page paragraphs from a PDF using poppler pdfinfo + pdftotext."""
    has_pdfinfo = shutil.which("pdfinfo") is not None
    has_pdftotext = shutil.which("pdftotext") is not None
    if not has_pdftotext:
        return [], {
            "pdf_file": pdf_path.name,
            "extractor": "unavailable",
            "total_pages": 0,
            "extracted_pages": 0,
            "empty_or_scanned_pages": [],
            "error": "pdftotext binary not available in sandbox",
        }

    total_pages = 0
    pdf_meta: dict[str, str] = {}
    if has_pdfinfo:
        try:
            info_out = subprocess.check_output(
                ["pdfinfo", str(pdf_path)],
                text=True,
                encoding="utf-8",
                errors="replace",
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

    segments: list[dict[str, Any]] = []
    empty_pages: list[int] = []
    per_page_chars: dict[str, int] = {}
    extracted_pages = 0
    current_section = "Title/FrontMatter"
    p_counter = 0

    if total_pages > 0:
        for page_no in range(1, total_pages + 1):
            try:
                page_txt = subprocess.check_output(
                    ["pdftotext", "-f", str(page_no), "-l", str(page_no), str(pdf_path), "-"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                )
            except Exception:
                page_txt = ""
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
    else:
        try:
            full_txt = subprocess.check_output(
                ["pdftotext", str(pdf_path), "-"],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except Exception:
            full_txt = ""
        pages = full_txt.split("\x0c")
        total_pages = max(len([p for p in pages if p is not None]), 1)
        for page_no, ptxt in enumerate(pages, start=1):
            clean_chars = len(_norm_ws(ptxt))
            per_page_chars[str(page_no)] = clean_chars
            if clean_chars < 15:
                empty_pages.append(page_no)
                continue
            extracted_pages += 1
            for ch in re.split(r"\n\s*\n+", ptxt):
                ch_one = _norm_ws(ch)
                if len(ch_one) < 4 or re.fullmatch(r"\d{1,4}", ch_one):
                    continue
                p_counter += 1
                segments.append({
                    "paragraph_id": f"P{p_counter:03d}",
                    "section": "Body",
                    "page": page_no,
                    "text": ch_one,
                })

    coverage = {
        "pdf_file": pdf_path.name,
        "extractor": "pdftotext",
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


# --- Quantitative, Unit, Formula, Citation, Negation & Modal Extraction ---

def _strip_non_scalar_noise(text: str) -> str:
    cleaned = CITE_RE.sub(" ", text)
    cleaned = FIG_TAG_RE.sub(" ", cleaned)
    cleaned = TABLE_TAG_RE.sub(" ", cleaned)
    cleaned = EQ_TAG_RE.sub(" ", cleaned)
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
        unit = (m.group(2) or "").lower()
        try:
            val = float(raw_num) * MULT.get(unit, 1.0)
        except ValueError:
            continue
        kind = "%" if unit == "%" else "scalar"
        out.append((val, kind, m.group(0).strip()))
    return out


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
    units: list[tuple[float, str, str]] = []
    cleaned = CITE_RE.sub(" ", text)
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


def _units_match(src_units: list[tuple[float, str, str]], tgt_units: list[tuple[float, str, str]]) -> tuple[bool, list[str]]:
    src_raw = [u[2] for u in src_units]
    tgt_raw = [u[2] for u in tgt_units]
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

        # Anti-summary collapse warning
        if len(sp) >= 180 and len(re.findall(r"[\u4e00-\u9fff]", tp)) < 20 and not needs_review:
            warnings.append(f"{pid} ({sec}): POSSIBLE_SUMMARY_COLLAPSE (long source {len(sp)} chars -> short target)")

        # Numbers + CN/EN magnitudes
        s_nums = _extract_normalized_numbers(sp)
        t_nums = _extract_normalized_numbers(tp)
        num_ok, num_diffs = _numbers_match(s_nums, t_nums)
        if not num_ok:
            numeric_drift += len(num_diffs)
            errors.extend([f"{pid} ({sec}): {d}" for d in num_diffs])

        # SI unit-value pairing
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


def emit_final_deliverables(
    draft_data: dict[str, Any],
    audit_report: dict[str, Any],
    *,
    metadata_lock: dict[str, Any] | None,
    result_path: Path,
    markdown_path: Path,
) -> dict[str, Any]:
    """Atomically emit translation_bilingual.md and result.json aligned with Harbor contract."""
    md_text = render_bilingual_markdown(draft_data, audit_report, metadata_lock)
    result_guard.atomic_write_text(markdown_path, md_text)

    case_id = str(draft_data.get("case_id") or (metadata_lock or {}).get("case_id") or "translation-case-01")
    if not result_path.is_file():
        result_guard.init_result(result_path, case_id=case_id, task_id="product-arena-01-5a9928c1")

    gaps: list[dict[str, Any]] = []
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

    segs = draft_data.get("bilingual_segments") or []
    src_cnt = max(audit_report.get("source_paragraphs", len(segs)), 1)
    needs_rev_cnt = sum(1 for s in segs if s.get("needs_human_review"))
    inv = audit_report.get("invariants_check") or {}

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
        "artifacts": ["result.json", markdown_path.name],
        "metrics": {
            "source_paragraphs": audit_report.get("source_paragraphs", len(segs)),
            "translated_paragraphs": len(segs),
            "terminology_consistency_rate": inv.get("terminology_consistency_rate", 1.0),
            "blocking_error_count": len(audit_report.get("errors") or []),
            "needs_human_review_count": needs_rev_cnt,
        },
        "metric_provenance": {
            "source_paragraphs": "source_segments.json",
            "translated_paragraphs": "bilingual_segments",
            "terminology_consistency_rate": "bilingual_alignment_guard:glossary",
            "blocking_error_count": "bilingual_alignment_guard:audit",
            "needs_human_review_count": "bilingual_segments[].needs_human_review",
        },
        "findings": [
            f"Audited {len(segs)} bilingual paragraph(s); passed={audit_report.get('passed')}, "
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
    args = parser.parse_args(argv)

    if args.prepare:
        paths = preflight_probe.resolve_default_paths(
            input_dir=args.input or None,
            output_dir=args.output or None,
            work_dir=args.work or None,
        )
        rep = prepare_workspace(paths["input_dir"], paths["work_dir"], paths["output_dir"])
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
        if args.out_json:
            result_guard.atomic_write_json(args.out_json, rep)
        if args.emit_result and args.emit_markdown:
            emit_final_deliverables(
                draft_data,
                rep,
                metadata_lock=lock_doc,
                result_path=Path(args.emit_result),
                markdown_path=Path(args.emit_markdown),
            )
        print(json.dumps({
            "passed": rep["passed"],
            "errors": rep["errors"],
            "warnings": rep["warnings"],
            "terminology_consistency_rate": rep["invariants_check"]["terminology_consistency_rate"],
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
