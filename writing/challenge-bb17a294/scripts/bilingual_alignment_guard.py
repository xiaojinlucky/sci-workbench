#!/usr/bin/env python3
"""Deterministic bilingual academic translation guard: numeric/magnitude/unit/order parity,
live citations & bilingual bibliography integrity, negation & modal-hedge conservation,
glossary locking, anti-translationese audit, and aligned/contract export.

Pure Python 3.12 standard library implementation with zero external dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

# Unicode superscript translation table for scientific notation like 1.5×10⁻⁴
SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")

# Magnitude multipliers (EN + ZH)
MULT: dict[str, float] = {
    "": 1.0,
    "%": 1.0,
    "％": 1.0,
    "千": 1e3,
    "万": 1e4,
    "十万": 1e5,
    "百万": 1e6,
    "千万": 1e7,
    "亿": 1e8,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
}

# Canonical SI & scientific unit normalization map (EN + ZH -> canonical ASCII key)
UNIT_CANON_MAP: dict[str, str] = {
    "mg/kg": "mg/kg",
    "毫克/千克": "mg/kg",
    "ug/ml": "ug/mL",
    "μg/ml": "ug/mL",
    "µg/ml": "ug/mL",
    "微克/毫升": "ug/mL",
    "mg/ml": "mg/mL",
    "毫克/毫升": "mg/mL",
    "ng/ml": "ng/mL",
    "纳克/毫升": "ng/mL",
    "pg/ml": "pg/mL",
    "ug/l": "ug/L",
    "μg/l": "ug/L",
    "µg/l": "ug/L",
    "mg/l": "mg/L",
    "mol/l": "mol/L",
    "mmol/l": "mmol/L",
    "umol/l": "umol/L",
    "μmol/l": "umol/L",
    "µmol/l": "umol/L",
    "um": "uM_or_um",  # disambiguated by case below
    "mm": "mM_or_mm",
    "nm": "nM_or_nm",
    "pm": "pM_or_pm",
    "μm": "uM_or_um",
    "µm": "uM_or_um",
    "微摩尔": "uM",
    "毫摩尔": "mM",
    "纳摩尔": "nM",
    "纳米": "nm",
    "微米": "um",
    "毫米": "mm",
    "厘米": "cm",
    "千米": "km",
    "cm": "cm",
    "km": "km",
    "ml": "mL",
    "毫升": "mL",
    "ul": "uL",
    "μl": "uL",
    "µl": "uL",
    "微升": "uL",
    "kg": "kg",
    "千克": "kg",
    "mg": "mg",
    "毫克": "mg",
    "ug": "ug",
    "μg": "ug",
    "µg": "ug",
    "微克": "ug",
    "kda": "kDa",
    "千道尔顿": "kDa",
    "da": "Da",
    "°c": "degC",
    "℃": "degC",
    "摄氏度": "degC",
    "hz": "Hz",
    "khz": "kHz",
    "mhz": "MHz",
    "ghz": "GHz",
    "ev": "eV",
    "kev": "keV",
    "mev": "MeV",
    "pa": "Pa",
    "kpa": "kPa",
    "mpa": "MPa",
    "ms": "ms",
    "bp": "bp",
    "kb": "kb",
    "mb": "Mb",
    "gb": "Gb",
}

UNIT_TOKEN_PATTERN = (
    r"(?:"
    r"mg/kg|毫克/千克|[uμµ]g/m[lL]|微克/毫升|mg/m[lL]|毫克/毫升|ng/m[lL]|纳克/毫升|pg/m[lL]|"
    r"[uμµ]g/L|mg/L|mmol/L|[uμµ]mol/L|mol/L|"
    r"[uμµ]M|mM|nM|pM|微摩尔|毫摩尔|纳摩尔|"
    r"[uμµ]m|nm|mm|cm|km|纳米|微米|毫米|厘米|千米|"
    r"mL|[uμµ]L|毫升|微升|kg|mg|[uμµ]g|千克|毫克|微克|"
    r"kDa|Da|千道尔顿|°C|℃|摄氏度|GHz|MHz|kHz|Hz|MeV|keV|eV|MPa|kPa|Pa|ms|kb|Mb|Gb|bp"
    r")"
)

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
INLINE_REORDER_NOTE_RE = re.compile(r"<!--\s*reordered_clause_note\s*:\s*(.*?)\s*-->", re.I | re.S)

# Math blocks: display $$...$$ or inline $...$ (excluding plain currency like $50 or $5.2 million)
MATH_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$|\$(?!\s*\d+(?:,\d{3})*(?:\.\d+)?\s*(?:thousand|million|billion)?(?:\s|$|[.,;:\)]))([^\$\n]+?)\$", re.S)

# Display numbering: Fig. 1 / Figure S2 / Extended Data Fig. 1 / 图 1 / 附图 S2 / Table 2 / 表 2 / Eq. (3) / 式 (3)
DISPLAY_RE = re.compile(
    r"(?:"
    r"(?:Extended\s+Data\s+|Supplementary\s+)?(?:Fig(?:ure|\.)?|图|附图|补充图)\s*([Ss]?\d+[A-Za-z]?)"
    r"|(?:Supplementary\s+)?(?:Table|Tab\.|表|附表|补充表)\s*([Ss]?\d+[A-Za-z]?)"
    r"|(?:Equation|Eq\.?|公式|式)\s*\(?([Ss]?\d+[A-Za-z]?)\)?"
    r")",
    re.I,
)

# Citations: LaTeX \cite{}, numeric [1], [1-3], [1, 2] (not preceded by CI), and author-year (Author et al., 2024) / （张伟等，2023）
LATEX_CITE_RE = re.compile(r"\\cite[pt]?\*?\{([^}]+)\}")
BRACKET_CITE_RE = re.compile(r"(?<!CI\s)(?<!置信区间\s)(?<!CI)\[(\d+(?:\s*[-–—,]\s*\d+)*)\]")
AUTHOR_YEAR_GROUP_RE = re.compile(
    r"[\(（]([^()（）\n]{2,120}?(?:19|20)\d{2}[a-z]?(?:\s*[;；]\s*[^()（）\n]{2,120}?(?:19|20)\d{2}[a-z]?)*)[\)）]"
)
AUTHOR_YEAR_ITEM_RE = re.compile(
    r"([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|&\s+[A-Z][A-Za-z\-]+|and\s+[A-Z][A-Za-z\-]+))?|[\u4e00-\u9fff]{2,4}(?:等|和[\u4e00-\u9fff]{2,4}|与[\u4e00-\u9fff]{2,4})?)\s*[,，]\s*((?:19|20)\d{2}[a-z]?)"
)

# Statistical p-value comparisons: p < 0.001, P = 0.042, p ≤ 1.5×10^-4
PVAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])([pP])\s*(<=|>=|≤|≥|<|>|=)\s*"
    r"(\d+(?:\.\d+)?(?:\s*(?:[×x\*]\s*10(?:\s*\^\s*\{?\s*[+-]?\d+\s*\}?|[⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+)|[eE][+-]?\d+))?)"
)

# Combined prose number scanner (scientific notation, ZH 百分之X, or scalar with optional magnitude/percent/SI unit)
PROSE_NUM_RE = re.compile(
    r"(?:"
    r"百分之\s*(\d+(?:\.\d+)?)"
    r"|"
    r"(?<![A-Za-z0-9_.\-])(-?)(?<![0-9])(\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:"
    r"\s*(?:[×x\*]\s*10(?:\s*\^\s*\{?\s*([+-]?\d+)\s*\}?|([⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+))|[eE]([+-]?\d+))"
    r"|\s*(%|％|十万|百万|千万|万|亿|千|thousand|million|billion)(?![A-Za-z])"
    r")?"
    r"(?:\s*(" + UNIT_TOKEN_PATTERN + r")(?![A-Za-z]))?"
    r")",
    re.I,
)

# Negation detectors (EN + ZH)
NEG_EN_RE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|none|cannot|can't|fail(?:s|ed)?\s+to|"
    r"unable\s+to|lack(?:s|ed|ing)?\s+of|absence\s+of|unlike|no\s+longer|non-?significant)\b",
    re.I,
)
NEG_ZH_RE = re.compile(
    r"不[是会能应可存在足同均再具有显著]|未[能见曾达发现观察出现显著产生]|无[法显明显统计学意义]|并非|绝非|缺乏|缺少|不同于|不再|没有|非显著"
)

# Epistemic hedges & associative markers (EN + ZH)
HEDGE_EN_RE = re.compile(
    r"\b(?:may|might|could|suggests?|indicates?|associated\s+with|correlated\s+with|"
    r"approximately|roughly|only|primarily|limited\s+to|preliminary|trend\s+toward|consistent\s+with|potentially)\b",
    re.I,
)
HEDGE_ZH_RE = re.compile(
    r"可能|或许|提示|表明可能|相关|关联|约|大约|仅|主要|局限于|初步|趋势|一致|潜在"
)

# Strong definitive causal / proof markers (for anti-causal-escalation gate)
CAUSAL_STRONG_EN_RE = re.compile(
    r"\b(?:proves?|proved|proven|definitively\s+establishes?|directly\s+causes?|causes?|caused|deterministically)\b",
    re.I,
)
CAUSAL_STRONG_ZH_RE = re.compile(
    r"证明了?|证实了?|确证了?|直接导致|导致了|必然引起|必然导致|决定性(?:地)?(?:引起|决定)"
)

# Translationese & AI-tone warning rules (warnings only with exact location, never blocking technical terms)
TRANSLATIONESE_ZH_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("轻动词膨胀", re.compile(r"(?:进行|作出|给予|予以)了?[^，。\n]{0,6}(?:分析|调整|优化|支持|评估|检查|讨论|验证|比较)"), "改为直接动词（如“分析了/评估了/优化了”）"),
    ("扮演角色直译", re.compile(r"(?:扮演|承担)了?[^，。\n]{0,8}角色"), "改为“起关键作用/介导/调控”等学术常用表达"),
    ("以一种…方式", re.compile(r"以一种[^，。\n]{2,12}的(?:方式|形式)"), "改为直接副词或具体机制表述"),
    ("翻案腔", re.compile(r"(?:不是|并非)[^，。\n]{1,20}[，,]?而是"), "按正面判断直接陈述，避免翻案修辞"),
    ("破折号滥用", re.compile(r"——"), "减少双破折号，改用逗号、括号或短句"),
    ("形式主语填充", re.compile(r"值得注意的是|有必要指出的是|可以说的是|需要指出的是"), "删除过渡套话，直接切入研究事实"),
    ("在…的过程中", re.compile(r"在[^，。\n]{2,18}(?:的过程中|的情况下)"), "简化为“……期间/……时/在……条件下”"),
    ("使得…能够", re.compile(r"使得?[^，。\n]{0,12}(?:能够|可以)"), "改为更紧凑的“使……可……”或直接主动句"),
    ("程度直译", re.compile(r"在某种程度上|从某种意义上说|在很大程度上|相对而言"), "交代具体范围或限定条件，减少虚泛程度词"),
    ("的…的…的连用", re.compile(r"的[^，。\n]{1,8}的[^，。\n]{1,8}的"), "拆分为两句，避免三层嵌套定语"),
    ("AI模板总结/拔高", re.compile(r"综上所述|总而言之|由此可见|不难发现|众所周知|显著赋能|深刻揭示|开启新的篇章"), "删除 AI 模板语，保留客观实证表述"),
    ("提示性冒号", re.compile(r"(?:一句话总结|核心是|关键是|结论是|本质是|重点是)[：:]"), "去掉提示性冒号壳，直接陈述结论"),
]

TRANSLATIONESE_EN_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("EN AI Cliché", re.compile(r"\b(?:it\s+is\s+worth\s+noting\s+that|plays\s+a\s+pivotal\s+role|delve\s+into|in\s+the\s+realm\s+of|a\s+myriad\s+of|tapestry\s+of|underscores\s+the\s+importance\s+of)\b", re.I), "Replace stock LLM phrasing with concrete domain verbs"),
    ("EN Em-dash Overuse", re.compile(r"—"), "Prefer commas, parentheses, or split sentences in formal scholarly prose"),
]

REF_HEADING_RE = re.compile(r"^\s*#{0,6}\s*(?:References|Bibliography|参考文献)\s*$", re.I)
REF_ENTRY_PREFIX_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2}[a-z]?)\b")
CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_unit(raw_unit: str) -> str:
    if not raw_unit:
        return ""
    u = raw_unit.strip()
    # Preserve case distinction between molarity (M) and length (m)
    u_mu = u.replace("µ", "u").replace("μ", "u")
    if u_mu in ("uM", "mM", "nM", "pM"):
        return u_mu
    if u_mu in ("um", "mm", "nm", "pm"):
        return u_mu
    low = u_mu.lower()
    mapped = UNIT_CANON_MAP.get(low, UNIT_CANON_MAP.get(u, u_mu))
    if mapped.endswith("_or_um"):
        return "uM" if u.endswith("M") else "um"
    if mapped.endswith("_or_mm"):
        return "mM" if u.endswith("M") else "mm"
    if mapped.endswith("_or_nm"):
        return "nM" if u.endswith("M") else "nm"
    if mapped.endswith("_or_pm"):
        return "pM" if u.endswith("M") else "pm"
    return mapped


def normalize_operator(op: str) -> str:
    return {"≤": "<=", "≥": ">="}.get(op.strip(), op.strip())


def parse_sci_number(raw: str) -> float:
    s = raw.strip().replace(",", "")
    m = re.match(
        r"^([+-]?\d+(?:\.\d+)?)\s*(?:[×x\*]\s*10(?:\s*\^\s*\{?\s*([+-]?\d+)\s*\}?|([⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+))|[eE]([+-]?\d+))$",
        s,
    )
    if m:
        mant = float(m.group(1))
        exp_str = m.group(2) or (m.group(3).translate(SUPERSCRIPT_MAP) if m.group(3) else None) or m.group(4)
        return mant * (10.0 ** int(exp_str))
    return float(s)


def split_paragraphs_and_refs(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Split text into body paragraphs (with inline reorder notes extracted) and reference entries."""
    lines = text.splitlines()
    ref_start_line: int | None = None
    for idx, line in enumerate(lines):
        if REF_HEADING_RE.match(line):
            ref_start_line = idx
            break

    body_text = "\n".join(lines[:ref_start_line]) if ref_start_line is not None else text
    ref_text = "\n".join(lines[ref_start_line + 1:]) if ref_start_line is not None else ""

    # Parse body paragraphs with approximate starting line number
    body_paras: list[dict[str, Any]] = []
    cur_buf: list[str] = []
    cur_start = 1

    def _flush_body() -> None:
        nonlocal cur_buf
        if not cur_buf:
            return
        raw_p = "\n".join(cur_buf).strip()
        cur_buf = []
        if not raw_p:
            return
        notes = [m.group(1).strip() for m in INLINE_REORDER_NOTE_RE.finditer(raw_p) if m.group(1).strip()]
        clean_p = INLINE_REORDER_NOTE_RE.sub("", raw_p).strip()
        if not clean_p:
            return
        body_paras.append({
            "index": len(body_paras) + 1,
            "start_line": cur_start,
            "text": clean_p,
            "inline_reorder_notes": notes,
        })

    for ln_no, line in enumerate(body_text.splitlines(), 1):
        if not line.strip():
            _flush_body()
        else:
            if not cur_buf:
                cur_start = ln_no
            cur_buf.append(line)
    _flush_body()

    # Parse reference entries
    ref_entries: list[dict[str, Any]] = []
    if ref_text.strip():
        cur_entry: dict[str, Any] | None = None
        for ln_no, line in enumerate(ref_text.splitlines(), (ref_start_line or 0) + 2):
            s = line.strip()
            if not s:
                if cur_entry:
                    ref_entries.append(cur_entry)
                    cur_entry = None
                continue
            pm = REF_ENTRY_PREFIX_RE.match(s)
            if pm:
                if cur_entry:
                    ref_entries.append(cur_entry)
                key = pm.group(1) or pm.group(2)
                cur_entry = {"key": key, "line": ln_no, "raw_text": pm.group(3).strip()}
            elif cur_entry is not None:
                cur_entry["raw_text"] += " " + s
            else:
                # Unnumbered reference line/paragraph (e.g. author-year bibliography)
                cur_entry = {"key": str(len(ref_entries) + 1), "line": ln_no, "raw_text": s}
        if cur_entry:
            ref_entries.append(cur_entry)

        for entry in ref_entries:
            rt = entry["raw_text"]
            dois = DOI_RE.findall(rt)
            years = YEAR_RE.findall(rt)
            entry["doi"] = dois[0].lower().rstrip(".,;)") if dois else None
            entry["year"] = years[0] if years else None
            entry["has_cjk"] = bool(CJK_CHAR_RE.search(rt))
            entry["has_latin"] = bool(re.search(r"[A-Za-z]{2,}", rt))

    return body_paras, ref_entries, ref_start_line is not None


def extract_paragraph_channels(text: str) -> dict[str, Any]:
    """Decompose a paragraph into isolated verification channels: equations, displays, citations, p-values, numbers."""
    work = text

    # 1. Equations
    equations: list[str] = []
    def _strip_math(m: re.Match[str]) -> str:
        body = (m.group(1) or m.group(2) or "").strip()
        equations.append(" ".join(body.split()))
        return " "
    work = MATH_BLOCK_RE.sub(_strip_math, work)

    # 2. DOIs
    dois = [d.lower().rstrip(".,;)") for d in DOI_RE.findall(work)]
    work = DOI_RE.sub(" ", work)

    # 3. Display references (Fig. 1 / 图 1 / Table 2 / 表 2 / Eq. 3 / 式 3)
    displays: list[tuple[str, str]] = []
    def _strip_display(m: re.Match[str]) -> str:
        if m.group(1):
            displays.append(("FIG", m.group(1).upper()))
        elif m.group(2):
            displays.append(("TABLE", m.group(2).upper()))
        elif m.group(3):
            displays.append(("EQ", m.group(3).upper()))
        return " "
    work = DISPLAY_RE.sub(_strip_display, work)

    # 4. Citations (LaTeX, numeric brackets, author-year)
    latex_cites: list[str] = []
    def _strip_latex(m: re.Match[str]) -> str:
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        latex_cites.extend(keys)
        return " "
    work = LATEX_CITE_RE.sub(_strip_latex, work)

    bracket_cites: list[str] = []
    bracket_cite_nums: list[int] = []
    def _strip_bracket(m: re.Match[str]) -> str:
        raw_inner = m.group(1)
        norm_inner = re.sub(r"\s*([-,–—])\s*", lambda x: "-" if x.group(1) in "-–—" else ",", raw_inner.strip())
        bracket_cites.append(norm_inner)
        for part in norm_inner.split(","):
            if "-" in part:
                a_s, b_s = part.split("-", 1)
                a, b = int(a_s), int(b_s)
                if 0 < b - a <= 50:
                    bracket_cite_nums.extend(range(a, b + 1))
                else:
                    bracket_cite_nums.extend([a, b])
            elif part.isdigit():
                bracket_cite_nums.append(int(part))
        return " "
    work = BRACKET_CITE_RE.sub(_strip_bracket, work)

    author_year_cites: list[dict[str, Any]] = []
    def _strip_ay(m: re.Match[str]) -> str:
        inner = m.group(1)
        items = list(AUTHOR_YEAR_ITEM_RE.finditer(inner))
        if not items:
            return m.group(0)
        for it in items:
            author_str = it.group(1).strip()
            year_str = it.group(2).strip()
            is_cjk = bool(CJK_CHAR_RE.search(author_str))
            lead = re.split(r"[\s&]|et\s+al|等|和|与", author_str)[0].strip()
            author_year_cites.append({
                "raw": f"{author_str}, {year_str}",
                "author": author_str,
                "lead": lead,
                "year": year_str,
                "script": "cjk" if is_cjk else "latin",
            })
        return " "
    work = AUTHOR_YEAR_GROUP_RE.sub(_strip_ay, work)

    # 5. Statistical p-value channel
    pvals: list[tuple[str, float, str]] = []
    def _strip_pval(m: re.Match[str]) -> str:
        op = normalize_operator(m.group(2))
        val = parse_sci_number(m.group(3))
        pvals.append((op, val, m.group(0).strip()))
        return " "
    work = PVAL_RE.sub(_strip_pval, work)

    # 6. Prose numbers, magnitudes, scientific notation, percentages, SI units (in appearance order)
    numbers: list[dict[str, Any]] = []
    for m in PROSE_NUM_RE.finditer(work):
        if m.group(1):  # 百分之X
            val = float(m.group(1))
            numbers.append({"val": val, "kind": "%", "unit": "", "raw": m.group(0).strip()})
            continue

        sign_str = m.group(2) or ""
        # Prevent range hyphen like 12-18 from making 18 negative
        if sign_str == "-" and m.start() > 0 and work[m.start() - 1].isdigit():
            sign_str = ""
        mant_str = (sign_str + m.group(3)).replace(",", "")
        exp_caret = m.group(4)
        exp_sup = m.group(5)
        exp_e = m.group(6)
        mag_unit = (m.group(7) or "").lower()
        si_unit = normalize_unit(m.group(8) or "")

        try:
            base_val = float(mant_str)
        except ValueError:
            continue

        if exp_caret or exp_sup or exp_e:
            exp_val = int(exp_caret or (exp_sup.translate(SUPERSCRIPT_MAP) if exp_sup else exp_e))
            val = base_val * (10.0 ** exp_val)
            kind = "scalar"
        elif mag_unit in ("%", "％"):
            val = base_val
            kind = "%"
        else:
            val = base_val * MULT.get(mag_unit, 1.0)
            kind = "scalar"

        numbers.append({
            "val": val,
            "kind": kind,
            "unit": si_unit,
            "raw": m.group(0).strip(),
        })

    return {
        "equations": equations,
        "dois": dois,
        "displays": displays,
        "latex_cites": latex_cites,
        "bracket_cites": bracket_cites,
        "bracket_cite_nums": bracket_cite_nums,
        "author_year_cites": author_year_cites,
        "pvals": pvals,
        "numbers": numbers,
    }


def _num_close(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["kind"] != b["kind"] or a["unit"] != b["unit"]:
        return False
    return math.isclose(a["val"], b["val"], rel_tol=1e-6, abs_tol=1e-12)


def compare_numbers_with_order(
    src_nums: list[dict[str, Any]],
    tgt_nums: list[dict[str, Any]],
    reorder_note: str | None,
) -> tuple[str, list[str]]:
    """Compare numeric+unit sequences in appearance order, allowing attested clause reordering."""
    if len(src_nums) != len(tgt_nums):
        return (
            "count_mismatch",
            [f"Numeric count mismatch: source={[x['raw'] for x in src_nums]} vs target={[x['raw'] for x in tgt_nums]}"],
        )

    # 1. Exact order match
    order_diffs: list[str] = []
    for s, t in zip(src_nums, tgt_nums):
        if not _num_close(s, t):
            order_diffs.append(
                f"{s['raw']!r} (val={s['val']}, kind={s['kind']}, unit={s['unit']!r}) != "
                f"{t['raw']!r} (val={t['val']}, kind={t['kind']}, unit={t['unit']!r})"
            )
    if not order_diffs:
        return "order_exact", []

    # 2. Check whether multiset matches (same values/units, reordered)
    unmatched_tgt = list(range(len(tgt_nums)))
    multiset_ok = True
    for s in src_nums:
        found_idx = None
        for idx in unmatched_tgt:
            if _num_close(s, tgt_nums[idx]):
                found_idx = idx
                break
        if found_idx is None:
            multiset_ok = False
            break
        unmatched_tgt.remove(found_idx)

    if not multiset_ok:
        return "value_or_unit_drift", [f"Value/unit drift: {d}" for d in order_diffs]

    # Multiset matches, order changed!
    if reorder_note and len(reorder_note.strip()) >= 8:
        return "verified_reordered", []

    return (
        "order_swap_unattested",
        [
            "Numeric multiset matches but appearance order changed without a verified reordered_clause_note "
            f"(possible group/condition value swap): source={[x['raw'] for x in src_nums]} vs target={[x['raw'] for x in tgt_nums]}"
        ],
    )


def audit_bibliography(
    src_refs: list[dict[str, Any]],
    tgt_refs: list[dict[str, Any]],
    all_tgt_bracket_nums: list[int],
    all_tgt_ay_cites: list[dict[str, Any]],
    ref_metadata: list[dict[str, Any]],
) -> dict[str, Any]:
    """Audit live citation resolution, bilingual script alignment, and reference DOI/year/title 1:1 integrity."""
    errors: list[str] = []
    warnings: list[str] = []
    doi_matches = 0
    title_checks = 0

    if src_refs or tgt_refs:
        if len(src_refs) != len(tgt_refs):
            errors.append(f"Bibliography entry count mismatch: source has {len(src_refs)} vs target has {len(tgt_refs)}")

        src_doi_to_key = {r["doi"]: r["key"] for r in src_refs if r.get("doi")}
        for idx, (sr, tr) in enumerate(zip(src_refs, tgt_refs), 1):
            if sr.get("doi"):
                if tr.get("doi") == sr["doi"]:
                    doi_matches += 1
                else:
                    swapped_from = src_doi_to_key.get(tr.get("doi") or "")
                    if swapped_from:
                        errors.append(
                            f"Ref [{sr['key']}]: Cross-entry DOI swap detected (expected {sr['doi']}, got {tr.get('doi')} from source Ref [{swapped_from}])"
                        )
                    else:
                        errors.append(
                            f"Ref [{sr['key']}]: DOI mismatch or missing in target (expected {sr['doi']}, got {tr.get('doi')})"
                        )
            if sr.get("year") and tr.get("year") and sr["year"] != tr["year"]:
                errors.append(
                    f"Ref [{sr['key']}]: Publication year mismatch/swap (source {sr['year']} vs target {tr['year']})"
                )

    # Verify numeric bracket citations resolve to target bibliography keys
    if tgt_refs and all_tgt_bracket_nums:
        valid_keys = {int(r["key"]) for r in tgt_refs if str(r["key"]).isdigit()}
        for k in sorted(set(all_tgt_bracket_nums)):
            if valid_keys and k not in valid_keys:
                errors.append(f"Dangling in-text numeric citation [{k}] has no matching entry in target References")

    # Verify author-year live citation alignment against target bibliography (Zotero #104013)
    if tgt_refs and all_tgt_ay_cites:
        tgt_bib_has_cjk = any(r["has_cjk"] for r in tgt_refs)
        tgt_bib_has_latin = any(r["has_latin"] for r in tgt_refs)
        for ay in all_tgt_ay_cites:
            lead = ay["lead"]
            yr = ay["year"]
            if ay["script"] == "cjk" and tgt_bib_has_latin and not tgt_bib_has_cjk:
                errors.append(
                    f"Bilingual citation script mismatch (detached live link): in-text citation ({ay['raw']}) remains Chinese while target bibliography is English-only"
                )
                continue
            matched = any(
                (lead.lower() in r["raw_text"].lower()) and (yr in r["raw_text"])
                for r in tgt_refs
            )
            if not matched:
                errors.append(
                    f"Dangling in-text author-year citation ({ay['raw']}) not found in target bibliography"
                )

    # Verify structured bilingual reference metadata (catches zotero-pdf-translate #306 title/author swaps!)
    if ref_metadata and tgt_refs:
        tgt_by_key = {str(r["key"]): r for r in tgt_refs}
        all_expected_titles = [
            (str(m.get("key", i + 1)), str(m.get("target_title", "")).strip())
            for i, m in enumerate(ref_metadata)
            if str(m.get("target_title", "")).strip()
        ]
        for i, meta in enumerate(ref_metadata):
            key = str(meta.get("key", i + 1))
            tr = tgt_by_key.get(key) or (tgt_refs[i] if i < len(tgt_refs) else None)
            if tr is None:
                errors.append(f"Metadata Ref [{key}] missing from target bibliography")
                continue
            rt_low = tr["raw_text"].lower()
            exp_doi = str(meta.get("doi", "")).strip().lower()
            if exp_doi and tr.get("doi") != exp_doi:
                errors.append(f"Ref [{key}]: DOI does not match reference ledger ({exp_doi} vs {tr.get('doi')})")

            exp_author = str(meta.get("target_author", "")).strip()
            if exp_author:
                lead_surname = re.split(r"[\s,;]+", exp_author)[0].strip()
                if lead_surname and lead_surname.lower() not in rt_low:
                    errors.append(f"Ref [{key}]: Expected target author '{exp_author}' missing in target entry")

            exp_title = str(meta.get("target_title", "")).strip()
            if exp_title:
                title_checks += 1
                exp_norm = " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", exp_title.lower()))
                rt_norm = " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", rt_low))
                if exp_norm and exp_norm not in rt_norm:
                    # Check if another entry's title was accidentally swapped into this slot!
                    swapped_key = None
                    for other_k, other_t in all_expected_titles:
                        if other_k == key:
                            continue
                        o_norm = " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", other_t.lower()))
                        if o_norm and o_norm in rt_norm:
                            swapped_key = other_k
                            break
                    if swapped_key:
                        errors.append(
                            f"Ref [{key}]: Cross-entry translated title swap detected (contains title of Ref [{swapped_key}] instead of '{exp_title[:60]}')"
                        )
                    else:
                        errors.append(
                            f"Ref [{key}]: Target entry missing expected translated title '{exp_title[:70]}'"
                        )

    return {
        "source_entries": len(src_refs),
        "target_entries": len(tgt_refs),
        "doi_matches": doi_matches,
        "title_metadata_checks": title_checks,
        "errors": errors,
        "warnings": warnings,
    }


def scan_translationese(tgt_paras: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """Scan translated body paragraphs for Chinese/English translationese and AI clichés."""
    findings: list[dict[str, Any]] = []
    full_body = "\n".join(p["text"] for p in tgt_paras)
    cjk_count = len(CJK_CHAR_RE.findall(full_body))
    word_count = len(re.findall(r"[A-Za-z]+", full_body))
    is_zh_target = cjk_count >= max(20, word_count // 2)

    rules = TRANSLATIONESE_ZH_RULES if is_zh_target else TRANSLATIONESE_EN_RULES
    for p in tgt_paras:
        for rel_ln, line in enumerate(p["text"].splitlines()):
            abs_ln = p["start_line"] + rel_ln
            for cat, rx, advice in rules:
                for m in rx.finditer(line):
                    findings.append({
                        "paragraph": p["index"],
                        "line": abs_ln,
                        "category": cat,
                        "match": m.group(0),
                        "suggestion": advice,
                    })

    denom_k = (cjk_count / 1000.0) if is_zh_target else (max(1, word_count) / 1000.0)
    density = round(len(findings) / denom_k, 2) if denom_k > 0 else 0.0
    return findings, density


def audit_bilingual_pair(
    source_path: Path,
    target_path: Path,
    glossary_path: Path | None = None,
    references_path: Path | None = None,
    reordered_notes_path: Path | None = None,
    *,
    case_id: str = "",
) -> dict[str, Any]:
    src_raw = source_path.read_text(encoding="utf-8", errors="replace")
    tgt_raw = target_path.read_text(encoding="utf-8", errors="replace")

    src_paras, src_refs, _ = split_paragraphs_and_refs(src_raw)
    tgt_paras, tgt_refs, _ = split_paragraphs_and_refs(tgt_raw)

    # Load glossary, optional reordered_clause_notes, and optional reference_map
    glossary_entries: list[dict[str, Any]] = []
    reorder_map: dict[int, str] = {}
    ref_metadata: list[dict[str, Any]] = []

    if glossary_path and glossary_path.is_file():
        gdata = json.loads(glossary_path.read_text(encoding="utf-8"))
        if isinstance(gdata, dict):
            glossary_entries = list(gdata.get("terms", []))
            for note in gdata.get("reordered_clause_notes", []):
                if isinstance(note, dict) and note.get("paragraph") and note.get("reason"):
                    reorder_map[int(note["paragraph"])] = str(note["reason"]).strip()
            if isinstance(gdata.get("references"), list):
                ref_metadata.extend(gdata["references"])
        elif isinstance(gdata, list):
            glossary_entries = gdata

    if reordered_notes_path and reordered_notes_path.is_file():
        rdata = json.loads(reordered_notes_path.read_text(encoding="utf-8"))
        notes_list = rdata.get("reordered_clause_notes", rdata) if isinstance(rdata, dict) else rdata
        if isinstance(notes_list, list):
            for note in notes_list:
                if isinstance(note, dict) and note.get("paragraph") and note.get("reason"):
                    reorder_map[int(note["paragraph"])] = str(note["reason"]).strip()

    if references_path and references_path.is_file():
        rfdata = json.loads(references_path.read_text(encoding="utf-8"))
        if isinstance(rfdata, dict):
            ref_metadata.extend(rfdata.get("references", []))
        elif isinstance(rfdata, list):
            ref_metadata.extend(rfdata)

    errors: list[str] = []
    warnings: list[str] = []
    para_checks: list[dict[str, Any]] = []
    reordered_verified: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    change_log: list[dict[str, Any]] = []

    if len(src_paras) != len(tgt_paras):
        errors.append(f"Body paragraph alignment mismatch: source has {len(src_paras)} paragraphs vs target has {len(tgt_paras)}")

    all_tgt_bracket_nums: list[int] = []
    all_tgt_ay_cites: list[dict[str, Any]] = []
    total_verified_nums = 0
    total_verified_cites = 0
    total_verified_displays = 0
    total_verified_equations = 0
    all_verified_dois: set[str] = set()

    for sp_obj, tp_obj in zip(src_paras, tgt_paras):
        idx = sp_obj["index"]
        sp = sp_obj["text"]
        tp = tp_obj["text"]

        s_ch = extract_paragraph_channels(sp)
        t_ch = extract_paragraph_channels(tp)
        all_tgt_bracket_nums.extend(t_ch["bracket_cite_nums"])
        all_tgt_ay_cites.extend(t_ch["author_year_cites"])

        # 1. Numeric, magnitude, unit & order comparison
        inline_note = "; ".join(tp_obj.get("inline_reorder_notes") or [])
        note_reason = inline_note or reorder_map.get(idx)
        num_status, num_errs = compare_numbers_with_order(s_ch["numbers"], t_ch["numbers"], note_reason)
        if num_errs:
            errors.extend([f"P{idx}: {e}" for e in num_errs])
        else:
            total_verified_nums += len(t_ch["numbers"])
            if num_status == "verified_reordered":
                reordered_verified.append({
                    "paragraph": idx,
                    "reason": note_reason,
                    "source_order": [x["raw"] for x in s_ch["numbers"]],
                    "target_order": [x["raw"] for x in t_ch["numbers"]],
                })
                change_log.append({
                    "type": "reordered_clause_attestation",
                    "location": f"P{idx}",
                    "detail": note_reason,
                    "requires_human_confirmation": False,
                })

        # 2. Statistical p-value comparison (operator + value)
        pval_ok = True
        if len(s_ch["pvals"]) != len(t_ch["pvals"]):
            pval_ok = False
            errors.append(
                f"P{idx}: Statistical p-value count mismatch: source={[x[2] for x in s_ch['pvals']]} vs target={[x[2] for x in t_ch['pvals']]}"
            )
        else:
            for (so, sv, sr), (to, tv, tr) in zip(s_ch["pvals"], t_ch["pvals"]):
                if so != to or not math.isclose(sv, tv, rel_tol=1e-6, abs_tol=1e-12):
                    pval_ok = False
                    errors.append(f"P{idx}: Statistical p-value drift: {sr!r} != {tr!r}")

        # 3. Citations (LaTeX, numeric bracket, author-year count + years)
        cites_ok = True
        if sorted(s_ch["latex_cites"]) != sorted(t_ch["latex_cites"]):
            cites_ok = False
            errors.append(f"P{idx}: LaTeX citation drift ({s_ch['latex_cites']} != {t_ch['latex_cites']})")
        if sorted(s_ch["bracket_cites"]) != sorted(t_ch["bracket_cites"]):
            cites_ok = False
            errors.append(f"P{idx}: Numeric citation marker drift ({s_ch['bracket_cites']} != {t_ch['bracket_cites']})")
        s_ay_years = sorted(c["year"] for c in s_ch["author_year_cites"])
        t_ay_years = sorted(c["year"] for c in t_ch["author_year_cites"])
        if s_ay_years != t_ay_years:
            cites_ok = False
            errors.append(
                f"P{idx}: Author-year citation mismatch: source={[c['raw'] for c in s_ch['author_year_cites']]} vs target={[c['raw'] for c in t_ch['author_year_cites']]}"
            )
        if cites_ok:
            total_verified_cites += len(t_ch["latex_cites"]) + len(t_ch["bracket_cites"]) + len(t_ch["author_year_cites"])

        # 4. Display numbering (Fig. / Table / Eq.)
        displays_ok = sorted(s_ch["displays"]) == sorted(t_ch["displays"])
        if not displays_ok:
            errors.append(f"P{idx}: Display numbering drift (source={s_ch['displays']} vs target={t_ch['displays']})")
        else:
            total_verified_displays += len(t_ch["displays"])

        # 5. Math equations
        eq_ok = s_ch["equations"] == t_ch["equations"]
        if not eq_ok:
            errors.append(f"P{idx}: Equation block drift (source={s_ch['equations']} vs target={t_ch['equations']})")
        else:
            total_verified_equations += len(t_ch["equations"])

        # 6. Inline DOIs
        if sorted(s_ch["dois"]) != sorted(t_ch["dois"]):
            errors.append(f"P{idx}: Inline DOI drift (source={s_ch['dois']} vs target={t_ch['dois']})")
        else:
            all_verified_dois.update(t_ch["dois"])

        # 7. Negation scope & polarity
        s_neg = len(NEG_EN_RE.findall(sp)) + len(NEG_ZH_RE.findall(sp))
        t_neg = len(NEG_EN_RE.findall(tp)) + len(NEG_ZH_RE.findall(tp))
        neg_ok = (s_neg > 0) == (t_neg > 0)
        if not neg_ok:
            errors.append(f"P{idx}: Negation polarity shift (source_neg={s_neg}, target_neg={t_neg})")
        elif abs(s_neg - t_neg) >= 2:
            warnings.append(f"P{idx}: Negation marker count differs substantially (source_neg={s_neg}, target_neg={t_neg})")

        # 8. Modal hedges & causal overclaim escalation + sentence coverage / truncation check
        s_hedge = len(HEDGE_EN_RE.findall(sp)) + len(HEDGE_ZH_RE.findall(sp))
        t_hedge = len(HEDGE_EN_RE.findall(tp)) + len(HEDGE_ZH_RE.findall(tp))
        s_causal = len(CAUSAL_STRONG_EN_RE.findall(sp)) + len(CAUSAL_STRONG_ZH_RE.findall(sp))
        t_causal = len(CAUSAL_STRONG_EN_RE.findall(tp)) + len(CAUSAL_STRONG_ZH_RE.findall(tp))
        causal_ok = True
        if s_hedge > 0 and s_causal == 0 and t_causal > 0:
            causal_ok = False
            errors.append(
                f"P{idx}: Causal overclaim escalation detected (source is hedged/associative with 0 causal verbs, target introduced {t_causal} definitive causal/proof verb(s))"
            )
        elif s_hedge > 0 and t_hedge == 0:
            warnings.append(f"P{idx}: Source contains {s_hedge} epistemic hedge/limiter(s) omitted in target")

        # Sentence coverage & truncation guard (flags dropped clauses/sentences across EN<->ZH)
        s_sents = [x for x in re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", sp.strip()) if len(x.strip()) >= 4]
        t_sents = [x for x in re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", tp.strip()) if len(x.strip()) >= 4]
        s_sent_n, t_sent_n = max(1, len(s_sents)), max(1, len(t_sents))
        truncation_suspected = False
        if s_sent_n >= 3 and t_sent_n <= max(1, int(s_sent_n * 0.45)):
            truncation_suspected = True
            errors.append(
                f"P{idx}: Suspected translation truncation/omission (source has {s_sent_n} sentences vs target has {t_sent_n})"
            )
        elif len(sp.strip()) >= 180 and len(tp.strip()) < max(20, int(len(sp.strip()) * 0.14)):
            truncation_suspected = True
            errors.append(
                f"P{idx}: Severe length collapse ({len(tp.strip())} chars vs {len(sp.strip())} source chars); check for omitted content"
            )

        para_rec = {
            "paragraph": idx,
            "numbers_status": num_status,
            "numbers_ok": num_status in ("order_exact", "verified_reordered"),
            "pvals_ok": pval_ok,
            "citations_ok": cites_ok,
            "displays_ok": displays_ok,
            "equations_ok": eq_ok,
            "negation_parity_ok": neg_ok,
            "causal_escalation_ok": causal_ok,
            "source_sentences": s_sent_n,
            "target_sentences": t_sent_n,
            "truncation_ok": not truncation_suspected,
            "hedges_source": s_hedge,
            "hedges_target": t_hedge,
            "source_numbers": [x["raw"] for x in s_ch["numbers"]],
            "target_numbers": [x["raw"] for x in t_ch["numbers"]],
        }
        para_checks.append(para_rec)
        evidence_map.append({
            "paragraph": idx,
            "numbers_status": num_status,
            "citations_ok": cites_ok,
            "displays_ok": displays_ok,
            "equations_ok": eq_ok,
            "negation_ok": neg_ok,
            "causal_escalation_ok": causal_ok,
        })

    # 9. Bibliography & live-citation cross-audit
    bib_report = audit_bibliography(src_refs, tgt_refs, all_tgt_bracket_nums, all_tgt_ay_cites, ref_metadata)
    errors.extend(bib_report["errors"])
    warnings.extend(bib_report["warnings"])
    for r in tgt_refs:
        if r.get("doi"):
            all_verified_dois.add(r["doi"])

    # 10. Glossary lock audit + explicit coverage rate & sentence alignment ratio
    glossary_hits: list[dict[str, Any]] = []
    glossary_matched_terms: list[str] = []
    glossary_missing_terms: list[str] = []
    if glossary_entries:
        src_full_low = "\n".join(p["text"] for p in src_paras).lower()
        tgt_full = "\n".join(p["text"] for p in tgt_paras)
        tgt_full_low = tgt_full.lower()
        for item in glossary_entries:
            if not isinstance(item, dict):
                continue
            src_term = str(item.get("source", "")).strip()
            req_tgt = str(item.get("target", "")).strip()
            allowed = [str(a).strip() for a in item.get("allowed_variants", []) if str(a).strip()]
            forbidden = [str(f).strip() for f in item.get("forbidden_variants", []) if str(f).strip()]
            if not src_term or not req_tgt:
                continue
            if src_term.lower() in src_full_low:
                accepted_targets = [req_tgt] + allowed
                present = any(cand.lower() in tgt_full_low for cand in accepted_targets)
                bad_used = [fv for fv in forbidden if fv.lower() in tgt_full_low]
                ok_term = present and not bad_used
                if not present:
                    errors.append(f"Glossary term {src_term!r} present in source missing canonical translation {req_tgt!r}")
                if bad_used:
                    errors.append(f"Glossary term {src_term!r} used forbidden variant(s): {bad_used}")
                if ok_term:
                    glossary_matched_terms.append(src_term)
                else:
                    glossary_missing_terms.append(src_term)
                glossary_hits.append({
                    "source": src_term,
                    "canonical_target": req_tgt,
                    "ok": ok_term,
                    "forbidden_hits": bad_used,
                })
                change_log.append({
                    "type": "glossary_lock",
                    "location": "full_document",
                    "detail": f"{src_term} -> {req_tgt}",
                    "requires_human_confirmation": False,
                })

    glossary_coverage_rate: float | None = (
        round(len(glossary_matched_terms) / len(glossary_hits), 4)
        if glossary_hits else (1.0 if glossary_entries else None)
    )
    total_src_sents = sum(int(c.get("source_sentences", 0)) for c in para_checks)
    total_tgt_sents = sum(int(c.get("target_sentences", 0)) for c in para_checks)
    sentence_alignment_ratio = round(total_tgt_sents / max(1, total_src_sents), 4) if total_src_sents else 1.0
    paragraph_alignment_ratio = round(len(tgt_paras) / max(1, len(src_paras)), 4) if src_paras else 1.0

    # 11. Translationese & AI-tone offline scan (warnings only)
    style_warnings, style_density = scan_translationese(tgt_paras)

    contract_patch: dict[str, Any] = {
        "case_id": case_id or "UNSET_CASE_ID",
        "deliverable": {
            "translated_manuscript": target_path.name,
            "bilingual_aligned": "bilingual_aligned.md",
            "translation_audit": "translation_audit.json",
        },
        "preserved_facts": {
            "source_file": source_path.name,
            "paragraphs_aligned": min(len(src_paras), len(tgt_paras)),
            "paragraph_alignment_ratio": paragraph_alignment_ratio,
            "sentence_alignment_ratio": sentence_alignment_ratio,
            "verified_numeric_tokens": total_verified_nums,
            "verified_citations": total_verified_cites,
            "verified_displays": total_verified_displays,
            "verified_equations": total_verified_equations,
            "verified_dois": sorted(all_verified_dois),
            "glossary_coverage_rate": glossary_coverage_rate,
        },
        "evidence_map": evidence_map,
        "change_log": change_log,
        "metrics": {
            "source_paragraphs": len(src_paras),
            "target_paragraphs": len(tgt_paras),
            "paragraph_alignment_ratio": paragraph_alignment_ratio,
            "source_sentences": total_src_sents,
            "target_sentences": total_tgt_sents,
            "sentence_alignment_ratio": sentence_alignment_ratio,
            "verified_numeric_tokens": total_verified_nums,
            "reordered_clauses_verified": len(reordered_verified),
            "bibliography_entries_checked": bib_report["target_entries"],
            "glossary_terms_checked": len(glossary_hits),
            "glossary_coverage_rate": glossary_coverage_rate,
            "hard_errors_count": len(errors),
            "style_warnings_count": len(style_warnings),
            "translationese_per_1k_chars": style_density,
        },
        "metric_provenance": {
            "source_paragraphs": source_path.name,
            "target_paragraphs": target_path.name,
            "paragraph_alignment_ratio": "translation_audit.json:paragraph_alignment_ratio",
            "sentence_alignment_ratio": "translation_audit.json:sentence_alignment_ratio",
            "verified_numeric_tokens": "translation_audit.json:paragraph_checks",
            "reordered_clauses_verified": "translation_audit.json:reordered_clauses_verified",
            "bibliography_entries_checked": "translation_audit.json:bibliography_check",
            "glossary_terms_checked": "translation_audit.json:glossary_checks",
            "glossary_coverage_rate": "translation_audit.json:glossary_coverage_rate",
            "hard_errors_count": "translation_audit.json:errors",
            "style_warnings_count": "translation_audit.json:style_warnings",
            "translationese_per_1k_chars": "translation_audit.json:translationese_per_1k_chars",
        },
        "gaps": [],
    }

    return {
        "passed": len(errors) == 0,
        "source_paragraphs": len(src_paras),
        "target_paragraphs": len(tgt_paras),
        "paragraph_alignment_ratio": paragraph_alignment_ratio,
        "source_sentences": total_src_sents,
        "target_sentences": total_tgt_sents,
        "sentence_alignment_ratio": sentence_alignment_ratio,
        "glossary_coverage_rate": glossary_coverage_rate,
        "glossary_matched_terms": glossary_matched_terms,
        "glossary_missing_terms": glossary_missing_terms,
        "errors": errors,
        "warnings": warnings,
        "reordered_clauses_verified": reordered_verified,
        "paragraph_checks": para_checks,
        "bibliography_check": bib_report,
        "glossary_checks": glossary_hits,
        "style_warnings": style_warnings,
        "translationese_per_1k_chars": style_density,
        "contract_patch": contract_patch,
        "_aligned_pairs": [
            {"index": sp["index"], "source": sp["text"], "target": tp["text"]}
            for sp, tp in zip(src_paras, tgt_paras)
        ],
        "_src_refs": src_refs,
        "_tgt_refs": tgt_refs,
    }


def render_bilingual_aligned_md(rep: dict[str, Any]) -> str:
    """Export editable paragraph-by-paragraph bilingual alignment Markdown."""
    status = "PASS" if rep["passed"] else "FAIL"
    lines: list[str] = [
        f"# Bilingual Aligned Manuscript ({status})",
        "",
        f"- **Paragraphs Aligned**: {rep['source_paragraphs']} -> {rep['target_paragraphs']}",
        f"- **Hard Errors**: {len(rep['errors'])} | **Fidelity Warnings**: {len(rep['warnings'])} | **Style Warnings**: {len(rep['style_warnings'])} ({rep['translationese_per_1k_chars']}/1k chars)",
        f"- **Attested Clause Reorderings**: {len(rep['reordered_clauses_verified'])}",
        "",
    ]

    checks_by_p = {c["paragraph"]: c for c in rep["paragraph_checks"]}
    style_by_p: dict[int, list[dict[str, Any]]] = {}
    for sw in rep["style_warnings"]:
        style_by_p.setdefault(sw["paragraph"], []).append(sw)

    for pair in rep.get("_aligned_pairs", []):
        idx = pair["index"]
        chk = checks_by_p.get(idx, {})
        ok_all = all(
            chk.get(k, True)
            for k in ("numbers_ok", "pvals_ok", "citations_ok", "displays_ok", "equations_ok", "negation_parity_ok", "causal_escalation_ok")
        )
        badge = "PASS" if ok_all else "CHECK_FAILED"
        lines.extend([
            f"## Paragraph {idx} [{badge} | numbers: `{chk.get('numbers_status', 'n/a')}`]",
            "",
            "**Source:**",
            pair["source"],
            "",
            "**Target:**",
            pair["target"],
            "",
        ])
        if idx in style_by_p:
            lines.append("**Style Notes:**")
            for sw in style_by_p[idx]:
                lines.append(f"- L{sw['line']} [{sw['category']}]: `{sw['match']}` -> {sw['suggestion']}")
            lines.append("")

    if rep.get("_src_refs") or rep.get("_tgt_refs"):
        lines.extend(["## Bilingual References Alignment", ""])
        for i, (sr, tr) in enumerate(zip(rep.get("_src_refs", []), rep.get("_tgt_refs", [])), 1):
            lines.append(f"- **[{sr['key']}]** (DOI: `{tr.get('doi') or 'none'}` | Year: `{tr.get('year') or 'n/a'}`)")
            lines.append(f"  - Source: {sr['raw_text']}")
            lines.append(f"  - Target: {tr['raw_text']}")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify bilingual academic alignment, numeric/magnitude/order parity, live citations, bibliography, negation/hedges, glossary, and style."
    )
    parser.add_argument("--source", required=True, help="Source document path.")
    parser.add_argument("--target", required=True, help="Translated target document path.")
    parser.add_argument("--glossary", default="", help="Optional glossary JSON lock file.")
    parser.add_argument("--references", default="", help="Optional bilingual reference metadata JSON file.")
    parser.add_argument("--reordered-notes", default="", help="Optional JSON file of attested clause reorderings.")
    parser.add_argument("--case-id", default="", help="Optional case_id embedded in contract patch.")
    parser.add_argument("--out-json", required=True, help="Output alignment audit JSON path.")
    parser.add_argument("--out-aligned", default="", help="Optional editable bilingual_aligned.md output path.")
    parser.add_argument("--out-contract-patch", default="", help="Optional result.json contract patch JSON path.")
    args = parser.parse_args(argv)

    rep = audit_bilingual_pair(
        Path(args.source),
        Path(args.target),
        Path(args.glossary) if args.glossary else None,
        Path(args.references) if args.references else None,
        Path(args.reordered_notes) if args.reordered_notes else None,
        case_id=args.case_id,
    )

    if args.out_aligned:
        ap = Path(args.out_aligned)
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(render_bilingual_aligned_md(rep), encoding="utf-8")

    if args.out_contract_patch:
        cp = Path(args.out_contract_patch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(rep["contract_patch"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Strip internal rendering fields before writing JSON audit
    public_rep = {k: v for k, v in rep.items() if not k.startswith("_")}
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(public_rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "passed": public_rep["passed"],
        "source_paragraphs": public_rep["source_paragraphs"],
        "target_paragraphs": public_rep["target_paragraphs"],
        "reordered_clauses_verified": len(public_rep["reordered_clauses_verified"]),
        "bibliography_entries": public_rep["bibliography_check"]["target_entries"],
        "errors": public_rep["errors"],
        "warnings": public_rep["warnings"],
        "style_warnings_count": len(public_rep["style_warnings"]),
        "translationese_per_1k_chars": public_rep["translationese_per_1k_chars"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if public_rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
