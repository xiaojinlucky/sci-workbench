"""Shared offline I/O, OOXML DOCX engine, scientific invariant auditor, and academic prose linter.

Zero third-party dependencies: runs natively on Python 3.12+ stdlib inside
air-gapped Linux Docker containers, with optional enhancement when `soffice`,
`pdftoppm`, or `pdftotext` are installed.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NS = {"w": W_NS, "m": M_NS, "r": R_NS}

# ---------------------------------------------------------------------------
# 1. Scientific Invariant Extraction & Preservation Diff
# ---------------------------------------------------------------------------

RE_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
RE_PVAL = re.compile(
    r"\b(p|q|FDR|P_adj|padj)\s*([=<>≤≥]+)\s*([0-9]*\.?[0-9]+(?:[eE][+-]?\d+)?)",
    re.I,
)
RE_SAMPLE_N = re.compile(r"\b([nN])\s*=\s*(\d+(?:,\d{3})*)\b")
RE_STAT_NUM = re.compile(
    r"(?<!\w)([+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*[±]\s*\d+(?:\.\d+)?)?)"
    r"\s*(%|mg/kg|ug/mL|µg/mL|ng/mL|mg/mL|mM|µM|uM|nM|pM|mol/L|mmol/L|"
    r"°C|K|kDa|bp|kb|Mb|nm|µm|um|mm|cm|m|kg|g|mg|µg|ug|ng|ms|s|min|h|hr|days?|weeks?|months?|years?|"
    r"Hz|kHz|MHz|V|mV|A|mA|W|mW|Pa|kPa|MPa|GPa|fold|×|x|pt|px|dpi)?(?![A-Za-z0-9_])"
)
RE_FIG_REF = re.compile(
    r"\b((?:Supplementary\s+)?(?:Fig(?:ure|\.)?|Table|Tbl\.?|Scheme|Equation|Eq\.?)\s*S?\d+[A-Za-z]?)\b"
    r"|((?:补充)?(?:附图|附表|图|表)\s*S?\d+[A-Za-z]?)",
    re.I,
)
RE_CITE_BRACKET = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
RE_CITE_LATEX = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
RE_CITE_AUTHOR_YEAR = re.compile(
    r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+|&\s+[A-Z][A-Za-z\-]+))?,\s*(?:19|20)\d{2}[a-z]?"
    r"(?:;\s*[A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+|&\s+[A-Z][A-Za-z\-]+))?,\s*(?:19|20)\d{2}[a-z]?)*)\)"
)

RE_SENT_SPLIT_INV = re.compile(r"(?<=[.!?。！？])\s+|\n+")

TENTATIVE_MODAL_MARKERS = [
    ("may_be_associated", r"\bmay\s+be\s+associated\s+with\b"),
    ("may", r"\bmay\b"),
    ("might", r"\bmight\b"),
    ("could", r"\bcould\b"),
    ("possibly", r"\bpossibly\b"),
    ("potentially", r"\bpotentially\b"),
    ("preliminary", r"\bpreliminar(?:y|ily)\b"),
    ("trend_toward", r"\btrend(?:ed|ing)?\s+toward\b"),
    ("not_stat_sig", r"\bnot\s+statistically\s+significant\b"),
    ("zh_keneng", r"可能"),
    ("zh_huoxu", r"或许"),
    ("zh_qingxiang", r"倾向于"),
    ("zh_weida", r"未达(?:到)?统计学显著"),
]

ASSOCIATIVE_INTERP_MARKERS = [
    ("is_associated", r"\b(?:is|are|was|were)\s+associated\s+with\b"),
    ("associated_with", r"\bassociated\s+with\b"),
    ("correlates_with", r"\bcorrelat(?:ed|es|ion)\s+with\b"),
    ("linked_to", r"\blinked\s+to\b"),
    ("suggests", r"\bsuggest(?:s|ed|ing)?\b"),
    ("indicates", r"\bindicat(?:es|ed|ing)\b"),
    ("appears_to", r"\bappear(?:s|ed)?\s+to\b"),
    ("tends_to", r"\btend(?:s|ed)?\s+to\b"),
    ("demonstrates", r"\bdemonstrat(?:es|ed|ing)\b"),
    ("zh_tishi", r"提示"),
    ("zh_xiangguan", r"相关"),
    ("zh_bansui", r"伴随"),
]

HEDGED_MARKERS = [p for _, p in TENTATIVE_MODAL_MARKERS] + [
    r"\bsuggest(?:s|ed|ing)?\b", r"\bappear(?:s|ed)?\s+to\b", r"\btend(?:s|ed)?\s+to\b",
    r"\bassociated\s+with\b", r"\bcorrelat(?:ed|es|ion)\s+with\b", r"\blinked\s+to\b",
    r"提示", r"相关", r"伴随", r"尚需进一步验证",
]
STRONG_CAUSAL_MARKERS = [
    r"\bproves?\b", r"\bproven\b", r"\bcauses?\b", r"\bcaused\b", r"\bdriving\b",
    r"\bdrives?\b", r"\bdefinitely\b", r"\bconclusively\b", r"\bundoubtedly\b",
    r"\bestablish(?:es|ed)\s+that\b", r"\bconfirm(?:s|ed)\s+(?:a\s+)?causal\b",
    r"\bguarantees?\b", r"\bdirectly\s+triggers?\b", r"\bsolely\s+responsible\b",
    r"证明了", r"证实了因果", r"直接导致", r"必然引起", r"完全决定", r"毫无疑问", r"确凿表明",
]
NEGATION_MARKERS = [
    r"\bno\b", r"\bnot\b", r"\bnever\b", r"\bneither\b", r"\bnor\b", r"\bwithout\b",
    r"\bfailed\s+to\b", r"\bno\s+significant\b", r"\bnon-significant\b", r"\bnull\b",
    r"没有", r"并非", r"未见", r"未达", r"无显著", r"未能", r"排除", r"缺乏",
]


def _canonicalize_unit(unit: str) -> str:
    """Case-sensitive unit canonicalization (never lowercases: preserves mM vs mm, mV vs MV, M vs m)."""
    u = unit.strip()
    if not u:
        return ""
    u = u.replace("µ", "u").replace("μ", "u").replace("℃", "°C").replace("×", "x")
    return u


def _normalize_num_token(raw: str) -> str:
    """Preserve exact numeric precision via Decimal (no 6-sig-fig float :g truncation)."""
    s = raw.replace(",", "").strip()
    if "±" in s:
        parts = [p.strip() for p in s.split("±", 1)]
        return f"{_normalize_num_token(parts[0])}±{_normalize_num_token(parts[1])}"
    try:
        d = Decimal(s)
        if not d.is_finite():
            return s
        # Format exact decimal without scientific truncation, stripping redundant trailing fractional zeros
        sign, digits, exp = d.as_tuple()
        if exp >= 0:
            # Keep exact integer value without exponent
            return f"{'-' if sign else ''}{''.join(str(x) for x in digits)}{'0' * exp}"
        norm_str = format(d, "f")
        if "." in norm_str:
            norm_str = norm_str.rstrip("0").rstrip(".")
        return norm_str or "0"
    except (InvalidOperation, ValueError):
        return s


def classify_epistemic_strength(text: str) -> dict[str, Any]:
    """Classify epistemic/causal strength level AND exact modal lexical inventory (tentative vs associative vs strong)."""
    tentative_hits = [name for name, p in TENTATIVE_MODAL_MARKERS if re.search(p, text, re.I)]
    assoc_hits = [name for name, p in ASSOCIATIVE_INTERP_MARKERS if re.search(p, text, re.I)]
    hedged_hits = [p for p in HEDGED_MARKERS if re.search(p, text, re.I)]
    strong_hits = [p for p in STRONG_CAUSAL_MARKERS if re.search(p, text, re.I)]
    neg_hits = [p for p in NEGATION_MARKERS if re.search(p, text, re.I)]
    if strong_hits and not hedged_hits:
        level = "strong_causal"
        rank = 4
    elif strong_hits and hedged_hits:
        level = "mixed_causal_and_hedged"
        rank = 3
    elif assoc_hits and not tentative_hits:
        level = "associative_or_interpretive"
        rank = 2
    elif tentative_hits:
        level = "tentative_hedged"
        rank = 1
    else:
        level = "neutral_descriptive"
        rank = 2
    return {
        "level": level,
        "rank": rank,
        "tentative_modals": sorted(set(tentative_hits)),
        "associative_modals": sorted(set(assoc_hits)),
        "hedged_count": len(hedged_hits),
        "strong_causal_count": len(strong_hits),
        "negation_count": len(neg_hits),
    }


def _extract_sentence_bindings(text: str) -> list[dict[str, Any]]:
    """Extract ordered per-sentence numeric/unit sequence and local entity-context tokens for swap detection."""
    out: list[dict[str, Any]] = []
    for sent in RE_SENT_SPLIT_INV.split(text):
        s = sent.strip()
        if not s:
            continue
        seq: list[dict[str, Any]] = []
        for m in RE_STAT_NUM.finditer(s):
            raw_num, unit = m.group(1), _canonicalize_unit(m.group(2) or "")
            norm = _normalize_num_token(raw_num)
            if not unit and re.fullmatch(r"(?:19|20)\d{2}", norm):
                continue
            # Local entity/group window (45 chars preceding the number, retaining 1-letter group labels like Cohort A / Group B)
            left = s[max(0, m.start() - 45) : m.start()]
            ent_toks = [
                w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-]*|[\u4e00-\u9fff]{2,}", left)
                if w.lower() not in {"the", "and", "for", "with", "from", "was", "were", "are", "that", "this", "mean", "median", "value", "of", "in", "to", "by", "at", "achieved", "showed", "reached", "had", "is"}
            ][-3:]
            seq.append({"value": norm, "raw": raw_num.strip(), "unit": unit, "local_entities": ent_toks})
        ep = classify_epistemic_strength(s)
        out.append({"sentence": s, "numbers": seq, "epistemic": ep})
    return out


def extract_invariants(text: str) -> dict[str, Any]:
    """Extract exact-precision numbers, case-sensitive units, p-values, sample sizes, DOIs, citations, figure refs, and epistemic state."""
    numbers_with_units: list[dict[str, str]] = []
    ordered_numbers: list[dict[str, str]] = []
    bare_numbers: set[str] = set()
    for m in RE_STAT_NUM.finditer(text):
        raw_num, unit = m.group(1), _canonicalize_unit(m.group(2) or "")
        norm = _normalize_num_token(raw_num)
        # Skip standalone 4-digit years when no unit is attached
        if not unit and re.fullmatch(r"(?:19|20)\d{2}", norm):
            continue
        bare_numbers.add(norm)
        ordered_numbers.append({"value": norm, "raw": raw_num.strip(), "unit": unit})
        if unit:
            numbers_with_units.append({"value": norm, "unit": unit, "raw": m.group(0).strip()})

    pvalues = [
        {"metric": m.group(1), "op": m.group(2), "value": m.group(3), "raw": m.group(0)}
        for m in RE_PVAL.finditer(text)
    ]
    sample_sizes = [f"{m.group(1)}={m.group(2).replace(',', '')}" for m in RE_SAMPLE_N.finditer(text)]
    dois = sorted({m.group(1).rstrip(".,;)") for m in RE_DOI.finditer(text)})
    fig_refs = sorted({(m.group(1) or m.group(2)).strip() for m in RE_FIG_REF.finditer(text)})

    bracket_cites = sorted({m.group(0) for m in RE_CITE_BRACKET.finditer(text)})
    latex_cites: list[str] = []
    for m in RE_CITE_LATEX.finditer(text):
        latex_cites.extend([k.strip() for k in m.group(1).split(",") if k.strip()])
    author_year_cites = sorted({m.group(1).strip() for m in RE_CITE_AUTHOR_YEAR.finditer(text)})

    epistemic = classify_epistemic_strength(text)

    return {
        "numbers_with_units": numbers_with_units,
        "ordered_numbers": ordered_numbers,
        "bare_numbers": sorted(bare_numbers),
        "pvalues": pvalues,
        "sample_sizes": sorted(set(sample_sizes)),
        "dois": dois,
        "figure_table_refs": fig_refs,
        "bracket_citations": bracket_cites,
        "latex_citations": sorted(set(latex_cites)),
        "author_year_citations": author_year_cites,
        "epistemic": epistemic,
    }


def diff_invariants(source_text: str, revised_text: str) -> dict[str, Any]:
    """Compare source vs revised text with exact precision, case-sensitive units, sentence-level order/group swap, and modal drift."""
    src = extract_invariants(source_text)
    rev = extract_invariants(revised_text)
    violations: list[dict[str, Any]] = []

    src_num_set = set(src["bare_numbers"])
    rev_num_set = set(rev["bare_numbers"])
    dropped_nums = sorted(src_num_set - rev_num_set)
    added_nums = sorted(rev_num_set - src_num_set)
    if dropped_nums:
        violations.append({"code": "NUMERIC_DROPPED_OR_ALTERED", "severity": "high", "items": dropped_nums})
    if added_nums:
        violations.append({"code": "NUMERIC_UNGROUNDED_ADDITION", "severity": "high", "items": added_nums})

    # Strictly CASE-SENSITIVE unit matching (mM != mm, mV != MV, M != m)
    src_units = {(x["value"], x["unit"]) for x in src["numbers_with_units"]}
    rev_units = {(x["value"], x["unit"]) for x in rev["numbers_with_units"]}
    changed_units = sorted(src_units - rev_units)
    if changed_units:
        violations.append({
            "code": "VALUE_UNIT_MISMATCH",
            "severity": "high",
            "items": [f"{v} {u}" for v, u in changed_units],
        })

    # Sentence-level ordered numeric & entity-group swap detection
    src_sents = _extract_sentence_bindings(source_text)
    rev_sents = _extract_sentence_bindings(revised_text)
    if len(src_sents) == len(rev_sents):
        for idx_s, (ss, rs) in enumerate(zip(src_sents, rev_sents), start=1):
            s_vals = [x["value"] for x in ss["numbers"]]
            r_vals = [x["value"] for x in rs["numbers"]]
            if len(s_vals) >= 2 and sorted(s_vals) == sorted(r_vals) and s_vals != r_vals:
                # Compare full local entity tuple (e.g., ('cohort','a') -> 12.4) so single-letter group swaps always fire
                s_pairs = {(tuple(x["local_entities"][-2:]), x["value"]) for x in ss["numbers"] if x["local_entities"]}
                r_pairs = {(tuple(x["local_entities"][-2:]), x["value"]) for x in rs["numbers"] if x["local_entities"]}
                if s_pairs != r_pairs or not s_pairs:
                    violations.append({
                        "code": "NUMERIC_ORDER_OR_GROUP_SWAP",
                        "severity": "high",
                        "sentence_index": idx_s,
                        "source_sequence": s_vals,
                        "revised_sequence": r_vals,
                        "detail": f"Sentence {idx_s} preserves the same numeric multiset {sorted(s_vals)} but swaps value order/group binding ({s_vals} -> {r_vals}).",
                    })
            # Sentence-level modal downgrade/upgrade & negation scope check
            s_ep, r_ep = ss["epistemic"], rs["epistemic"]
            if s_ep["tentative_modals"] and not r_ep["tentative_modals"]:
                violations.append({
                    "code": "EPISTEMIC_MODAL_UPGRADE_OR_HEDGE_REMOVED",
                    "severity": "high",
                    "sentence_index": idx_s,
                    "dropped_modals": s_ep["tentative_modals"],
                    "revised_modals": r_ep["associative_modals"],
                    "detail": f"Sentence {idx_s} stripped tentative modal(s) {s_ep['tentative_modals']} (e.g., may->is/suggests).",
                })
            if (s_ep["negation_count"] > 0) != (r_ep["negation_count"] > 0):
                violations.append({
                    "code": "NEGATION_SCOPE_POLARITY_FLIP",
                    "severity": "high",
                    "sentence_index": idx_s,
                    "detail": f"Sentence {idx_s} flipped negation polarity ({s_ep['negation_count']} -> {r_ep['negation_count']}).",
                })

    src_p = {(x["metric"].lower(), x["op"], _normalize_num_token(x["value"])) for x in src["pvalues"]}
    rev_p = {(x["metric"].lower(), x["op"], _normalize_num_token(x["value"])) for x in rev["pvalues"]}
    if src_p != rev_p:
        violations.append({
            "code": "PVALUE_OR_SIGNIFICANCE_MUTATION",
            "severity": "high",
            "dropped": sorted(f"{a}{b}{c}" for a, b, c in (src_p - rev_p)),
            "added": sorted(f"{a}{b}{c}" for a, b, c in (rev_p - src_p)),
        })

    src_n = set(src["sample_sizes"])
    rev_n = set(rev["sample_sizes"])
    if src_n != rev_n:
        violations.append({
            "code": "SAMPLE_SIZE_MUTATION",
            "severity": "high",
            "dropped": sorted(src_n - rev_n),
            "added": sorted(rev_n - src_n),
        })

    src_cites = set(src["bracket_citations"]) | set(src["latex_citations"]) | set(src["author_year_citations"]) | set(src["dois"])
    rev_cites = set(rev["bracket_citations"]) | set(rev["latex_citations"]) | set(rev["author_year_citations"]) | set(rev["dois"])
    if src_cites - rev_cites:
        violations.append({"code": "CITATION_DROPPED", "severity": "high", "items": sorted(src_cites - rev_cites)})

    # Elevate dropped Figure/Table references to HIGH (blocking)
    src_figs = {re.sub(r"\s+", " ", f.lower()) for f in src["figure_table_refs"]}
    rev_figs = {re.sub(r"\s+", " ", f.lower()) for f in rev["figure_table_refs"]}
    if src_figs - rev_figs:
        violations.append({"code": "FIGURE_TABLE_REF_DROPPED", "severity": "high", "items": sorted(src_figs - rev_figs)})

    # Document-level epistemic/modality drift detection (covers multi-sentence split/merge)
    if rev["epistemic"]["rank"] > src["epistemic"]["rank"]:
        violations.append({
            "code": "EPISTEMIC_STRENGTH_ESCALATION",
            "severity": "high",
            "detail": f"Strength escalated from {src['epistemic']['level']} to {rev['epistemic']['level']}.",
        })
    if src["epistemic"]["tentative_modals"] and not rev["epistemic"]["tentative_modals"] and not any(v["code"] == "EPISTEMIC_MODAL_UPGRADE_OR_HEDGE_REMOVED" for v in violations):
        violations.append({
            "code": "EPISTEMIC_MODAL_UPGRADE_OR_HEDGE_REMOVED",
            "severity": "high",
            "dropped_modals": src["epistemic"]["tentative_modals"],
            "detail": f"All tentative hedging modals removed ({src['epistemic']['tentative_modals']} -> none).",
        })
    if src["epistemic"]["hedged_count"] > 0 and rev["epistemic"]["hedged_count"] == 0:
        violations.append({
            "code": "HEDGE_STRIPPED",
            "severity": "high",
            "detail": f"All hedging qualifiers removed ({src['epistemic']['hedged_count']} -> 0).",
        })
    if src["epistemic"]["negation_count"] != rev["epistemic"]["negation_count"] and not any(v["code"] == "NEGATION_SCOPE_POLARITY_FLIP" for v in violations):
        violations.append({
            "code": "NEGATION_COUNT_SHIFT",
            "severity": "medium",
            "detail": f"Negation marker count changed ({src['epistemic']['negation_count']} -> {rev['epistemic']['negation_count']}); verify polarity.",
        })

    return {
        "preserved": len([v for v in violations if v["severity"] == "high"]) == 0,
        "high_severity_count": len([v for v in violations if v["severity"] == "high"]),
        "medium_severity_count": len([v for v in violations if v["severity"] == "medium"]),
        "violations": violations,
        "source_invariants": src,
        "revised_invariants": rev,
    }


# ---------------------------------------------------------------------------
# 2. Bilingual Academic De-AI & Anti-Translationese Auditor (Whitelist-based)
# ---------------------------------------------------------------------------

ZH_CONTRARIAN_PATTERNS = [
    re.compile(r"不是[^。！？\n]{1,30}而是"),
    re.compile(r"并非[^。！？\n]{1,30}而是"),
    re.compile(r"不在于[^。！？\n]{1,30}而在于"),
    re.compile(r"与其说[^。！？\n]{1,30}(?:不如说|倒不如|毋宁说)"),
    re.compile(r"看似[^。！？\n]{1,25}实则"),
    re.compile(r"表面(?:上)?[^。！？\n]{1,25}实际(?:上)?"),
    re.compile(r"答案恰恰相反"),
]

ZH_CLICHE_OPENERS = [
    re.compile(r"近年来[，,]?[^。！？\n]{0,25}(?:受到(?:了)?广泛关注|成为研究热点|飞速发展|日新月异)"),
    re.compile(r"随着(?:科学)?技术(?:与社会)?的(?:不断|快速|飞速)发展"),
    re.compile(r"综上所述[，,]?[^。！？\n]{0,30}具有重要的(?:理论|学术|现实|实践)意义"),
]

ZH_BANNED_JARGON = [
    "赋能", "抓手", "商业闭环", "价值闭环", "底层逻辑", "顶层设计", "认知跃迁",
    "降本增效", "内容矩阵", "全链路", "组合拳", "技术底座", "公共底座", "材料锚点",
    "单点风险", "主脊柱", "认知增量", "迭代闭环", "说白了", "说穿了", "先说结论",
]

ZH_INSIGHT_SIGNPOSTS = [
    "更微妙的是", "还有一层", "只说对了一半", "值得注意的是", "需要指出的是", "从某种意义上说",
]

ZH_TRANSLATIONESE_MAP = {
    "折叠变化": "倍数变化 (fold change)",
    "抗原呈递": "抗原提呈 (antigen presentation)",
    "吞噬救赎": "吞噬功能恢复 (phagocytosis rescue)",
    "统计平局": "排名并列 (statistical ties)",
    "呈示出": "表现为 / 显示 (present)",
    "被无偏锁定": "经无偏筛选确定",
    "免疫刹车": "免疫检查点抑制/负向调控",
}

EN_AI_OVERUSED_PHRASES = [
    r"\bdelve(?:s|d|ing)?\s+into\b",
    r"\brich\s+tapestry\b",
    r"\ba\s+testament\s+to\b",
    r"\bplay(?:s|ed|ing)?\s+a\s+pivotal\s+role\b",
    r"\bin\s+the\s+realm\s+of\b",
    r"\bunderscore(?:s|d|ing)?\s+the\s+(?:importance|significance|need)\b",
    r"\bgroundbreaking\b",
    r"\bunprecedented\s+insights?\b",
    r"\bmeticulously\s+crafted\b",
    r"\bunlock(?:s|ing)?\s+new\s+avenues\b",
    r"\bpave(?:s|ing)?\s+the\s+way\s+for\b",
]

DEFENSIVE_SELF_UNDERMINING_PHRASES = [
    r"遗憾的是",
    r"仅(?:仅)?是一个初步尝试",
    r"仍明显落后于",
    r"效果非常有限",
    r"\bunfortunately,\s+our\b",
    r"\bmerely\s+a\s+preliminary\s+attempt\b",
    r"\bwe\s+only\s+managed\s+to\b",
]


def audit_academic_prose(text: str) -> dict[str, Any]:
    """Whitelist-based bilingual academic de-AI & anti-translationese check.

    Preserves legitimate scholarly passive voice, multi-clause syntax, and domain
    terminology; flags only verifiable rhetorical clichés, translationese, and jargon.
    """
    findings: list[dict[str, Any]] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("```") or stripped.startswith("|"):
            continue
        for pat in ZH_CONTRARIAN_PATTERNS:
            for m in pat.finditer(line):
                findings.append({
                    "line": lineno, "category": "contrarian_rhetoric", "severity": "warning",
                    "matched": m.group(0), "suggestion": "State the scholarly claim directly with supporting evidence instead of a 'not A, but B' rhetorical foil.",
                })
        for pat in ZH_CLICHE_OPENERS:
            for m in pat.finditer(line):
                findings.append({
                    "line": lineno, "category": "hollow_opener", "severity": "warning",
                    "matched": m.group(0), "suggestion": "Replace generic background opener with a specific research problem, technical constraint, or cited finding.",
                })
        for term in ZH_BANNED_JARGON:
            if term in line:
                findings.append({
                    "line": lineno, "category": "non_academic_jargon", "severity": "high",
                    "matched": term, "suggestion": "Replace corporate/AI buzzword with concrete scientific mechanism, entity, or observable quantity.",
                })
        for sp in ZH_INSIGHT_SIGNPOSTS:
            if sp in line:
                findings.append({
                    "line": lineno, "category": "ai_signpost", "severity": "info",
                    "matched": sp, "suggestion": "Remove filler signpost and let the data comparison carry the transition.",
                })
        for bad, good in ZH_TRANSLATIONESE_MAP.items():
            if bad in line:
                findings.append({
                    "line": lineno, "category": "translationese", "severity": "high",
                    "matched": bad, "suggestion": f"Use standard Chinese domain phrasing: {good}.",
                })
        for pat_s in EN_AI_OVERUSED_PHRASES:
            for m in re.finditer(pat_s, line, re.I):
                findings.append({
                    "line": lineno, "category": "en_ai_cliche", "severity": "warning",
                    "matched": m.group(0), "suggestion": "Use precise, restrained scientific phrasing without promotional adjectives.",
                })
        for pat_d in DEFENSIVE_SELF_UNDERMINING_PHRASES:
            for m in re.finditer(pat_d, line, re.I):
                findings.append({
                    "line": lineno, "category": "defensive_self_undermining", "severity": "info",
                    "matched": m.group(0), "suggestion": "Press-release narrative rule: state concrete quantitative boundaries or move genuine constraints to Limitations instead of self-undermining primary claims.",
                })
        # Excessive em-dashes in prose
        if line.count("—") >= 2 or "——" in line:
            findings.append({
                "line": lineno, "category": "em_dash_overuse", "severity": "info",
                "matched": "—/——", "suggestion": "Prefer standard commas, parentheses, or split sentences in formal academic prose.",
            })

    high_n = sum(1 for f in findings if f["severity"] == "high")
    warn_n = sum(1 for f in findings if f["severity"] == "warning")
    return {
        "clean": high_n == 0 and warn_n == 0,
        "high_count": high_n,
        "warning_count": warn_n,
        "info_count": len(findings) - high_n - warn_n,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# 3. Pure-Stdlib OOXML (.docx) Inspector & Generator (Zero python-docx needed)
# ---------------------------------------------------------------------------

def _wtag(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def _mtag(local: str) -> str:
    return f"{{{M_NS}}}{local}"


def inspect_docx_ooxml(docx_path: Path) -> dict[str, Any]:
    """Deeply inspect a .docx archive via stdlib zipfile + XML.

    Extracts: paragraphs & heading outline, tables, equations (OMML), footnotes,
    endnotes, comments, tracked changes (w:ins/w:del), page setup (margins/columns/size),
    fonts & styles, and live citation fields (Zotero/EndNote/Citavi/Mendeley/CrossRef/TOC)
    vs flattened plain-text citations.
    """
    if not docx_path.is_file():
        return {"ok": False, "error": f"file not found: {docx_path}"}
    try:
        zf = zipfile.ZipFile(docx_path, "r")
    except zipfile.BadZipFile as exc:
        return {"ok": False, "error": f"invalid docx zip: {exc}"}

    with zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            return {"ok": False, "error": "missing word/document.xml"}

        doc_root = ET.fromstring(zf.read("word/document.xml"))
        styles_root = ET.fromstring(zf.read("word/styles.xml")) if "word/styles.xml" in names else None

        paragraphs: list[dict[str, Any]] = []
        headings: list[dict[str, Any]] = []
        fonts_used: set[str] = set()
        font_sizes_pt: set[float] = set()
        line_spacings: set[str] = set()
        live_fields: list[dict[str, Any]] = []

        # Collect fields across full document XML
        instr_buf: list[str] = []
        in_field = False
        in_result = False
        result_buf: list[str] = []

        for p_idx, p in enumerate(doc_root.findall(".//w:body/w:p", NS), start=1):
            pstyle = ""
            ppr = p.find("w:pPr", NS)
            if ppr is not None:
                ps = ppr.find("w:pStyle", NS)
                if ps is not None:
                    pstyle = ps.attrib.get(_wtag("val"), "")
                sp = ppr.find("w:spacing", NS)
                if sp is not None and _wtag("line") in sp.attrib:
                    line_val = sp.attrib.get(_wtag("line"), "")
                    line_rule = sp.attrib.get(_wtag("lineRule"), "auto")
                    line_spacings.add(f"{line_val}/{line_rule}")

            texts: list[str] = []
            for elem in p.iter():
                tag = elem.tag
                if tag == _wtag("rFonts"):
                    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                        v = elem.attrib.get(_wtag(attr))
                        if v:
                            fonts_used.add(v)
                elif tag == _wtag("sz"):
                    val = elem.attrib.get(_wtag("val"))
                    if val and val.isdigit():
                        font_sizes_pt.add(round(int(val) / 2.0, 2))
                elif tag == _wtag("fldSimple"):
                    instr = elem.attrib.get(_wtag("instr"), "").strip()
                    display = "".join(t.text or "" for t in elem.findall(".//w:t", NS)).strip()
                    live_fields.append(_classify_field(p_idx, instr, display))
                elif tag == _wtag("fldChar"):
                    ftype = elem.attrib.get(_wtag("fldCharType"), "")
                    if ftype == "begin":
                        in_field = True
                        in_result = False
                        instr_buf = []
                        result_buf = []
                    elif ftype == "separate" and in_field:
                        in_result = True
                    elif ftype == "end" and in_field:
                        instr_str = "".join(instr_buf).strip()
                        res_str = "".join(result_buf).strip()
                        if instr_str:
                            live_fields.append(_classify_field(p_idx, instr_str, res_str))
                        in_field = False
                        in_result = False
                elif tag == _wtag("instrText") and in_field and not in_result:
                    instr_buf.append(elem.text or "")
                elif tag == _wtag("t"):
                    txt = elem.text or ""
                    texts.append(txt)
                    if in_field and in_result:
                        result_buf.append(txt)

            full_text = "".join(texts).strip()
            paragraphs.append({"index": p_idx, "style": pstyle or "Normal", "text": full_text})
            if re.match(r"^(?:Heading|标题)\s*\d+$", pstyle, re.I) or (
                pstyle.lower().startswith("heading")
            ):
                headings.append({"index": p_idx, "style": pstyle, "text": full_text})

        # Tables
        tables: list[list[list[str]]] = []
        for tbl in doc_root.findall(".//w:body/w:tbl", NS):
            rows_data: list[list[str]] = []
            for tr in tbl.findall("w:tr", NS):
                cells = []
                for tc in tr.findall("w:tc", NS):
                    ctext = "".join(t.text or "" for t in tc.findall(".//w:t", NS)).strip()
                    cells.append(ctext)
                rows_data.append(cells)
            tables.append(rows_data)

        # Page setup (sectPr)
        page_setup: list[dict[str, Any]] = []
        for sect in doc_root.findall(".//w:sectPr", NS):
            info: dict[str, Any] = {}
            pg_sz = sect.find("w:pgSz", NS)
            if pg_sz is not None:
                w_tw = int(pg_sz.attrib.get(_wtag("w"), "0") or 0)
                h_tw = int(pg_sz.attrib.get(_wtag("h"), "0") or 0)
                info["page_width_mm"] = round(w_tw / 1440.0 * 25.4, 2) if w_tw else None
                info["page_height_mm"] = round(h_tw / 1440.0 * 25.4, 2) if h_tw else None
            pg_mar = sect.find("w:pgMar", NS)
            if pg_mar is not None:
                margins = {}
                for side in ("top", "bottom", "left", "right"):
                    v = pg_mar.attrib.get(_wtag(side))
                    if v and v.lstrip("-").isdigit():
                        margins[f"{side}_mm"] = round(int(v) / 1440.0 * 25.4, 2)
                info["margins_mm"] = margins
            cols = sect.find("w:cols", NS)
            info["columns"] = int(cols.attrib.get(_wtag("num"), "1")) if cols is not None else 1
            page_setup.append(info)

        # Styles defaults
        style_defaults: dict[str, Any] = {}
        if styles_root is not None:
            for rf in styles_root.findall(".//w:docDefaults//w:rFonts", NS):
                for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                    v = rf.attrib.get(_wtag(attr))
                    if v:
                        style_defaults[f"default_font_{attr}"] = v
                        fonts_used.add(v)
            sz_def = styles_root.find(".//w:docDefaults//w:sz", NS)
            if sz_def is not None and _wtag("val") in sz_def.attrib:
                val = sz_def.attrib[_wtag("val")]
                if val.isdigit():
                    style_defaults["default_font_size_pt"] = round(int(val) / 2.0, 2)

        # Equations, tracked changes, footnotes, endnotes, comments, images
        eq_count = len(doc_root.findall(".//m:oMath", NS)) + len(doc_root.findall(".//m:oMathPara", NS))
        ins_count = len(doc_root.findall(".//w:ins", NS))
        del_count = len(doc_root.findall(".//w:del", NS))
        footnotes_count = 0
        if "word/footnotes.xml" in names:
            fn_root = ET.fromstring(zf.read("word/footnotes.xml"))
            footnotes_count = sum(
                1 for fn in fn_root.findall("w:footnote", NS)
                if fn.attrib.get(_wtag("type"), "normal") not in ("separator", "continuationSeparator")
            )
        comments_count = 0
        if "word/comments.xml" in names:
            cm_root = ET.fromstring(zf.read("word/comments.xml"))
            comments_count = len(cm_root.findall("w:comment", NS))
        media_files = sorted(n for n in names if n.startswith("word/media/"))

        full_body_text = "\n".join(p["text"] for p in paragraphs if p["text"])
        text_cites = extract_invariants(full_body_text)
        live_citation_fields = [f for f in live_fields if f["kind"] in ("zotero_citation", "endnote_citation", "citavi_citation", "mendeley_citation", "word_citation")]
        live_bib_fields = [f for f in live_fields if f["kind"].endswith("_bibliography")]

        plain_cite_count = (
            len(text_cites["bracket_citations"]) + len(text_cites["author_year_citations"])
        )
        flattened_suspected = plain_cite_count > 0 and len(live_citation_fields) == 0

        return {
            "ok": True,
            "path": str(docx_path),
            "paragraph_count": len(paragraphs),
            "non_empty_paragraph_count": sum(1 for p in paragraphs if p["text"]),
            "heading_count": len(headings),
            "headings": headings,
            "table_count": len(tables),
            "tables": tables,
            "equation_count": eq_count,
            "footnote_count": footnotes_count,
            "comment_count": comments_count,
            "tracked_insertions": ins_count,
            "tracked_deletions": del_count,
            "media_files": media_files,
            "fonts_used": sorted(fonts_used),
            "font_sizes_pt": sorted(font_sizes_pt),
            "line_spacings": sorted(line_spacings),
            "style_defaults": style_defaults,
            "page_setup": page_setup,
            "live_fields": live_fields,
            "live_citation_count": len(live_citation_fields),
            "live_bibliography_count": len(live_bib_fields),
            "plain_text_citation_markers": plain_cite_count,
            "flattened_citations_suspected": flattened_suspected,
            "paragraphs": paragraphs,
            "full_text": full_body_text,
        }


def _classify_field(paragraph_index: int, instr: str, display: str) -> dict[str, Any]:
    up = instr.upper()
    kind = "other_field"
    if "ZOTERO_ITEM" in up or "CSL_CITATION" in up:
        kind = "zotero_citation"
    elif "ZOTERO_BIBL" in up or "CSL_BIBLIOGRAPHY" in up:
        kind = "zotero_bibliography"
    elif "EN.CITE" in up:
        kind = "endnote_citation"
    elif "EN.REFLIST" in up:
        kind = "endnote_bibliography"
    elif "CITAVI" in up:
        kind = "citavi_citation"
    elif "MENDELEY" in up:
        kind = "mendeley_citation"
    elif up.startswith("CITATION "):
        kind = "word_citation"
    elif up.startswith("BIBLIOGRAPHY"):
        kind = "word_bibliography"
    elif up.startswith("REF ") or up.startswith("PAGEREF ") or up.startswith("SEQ "):
        kind = "cross_reference_or_seq"
    elif up.startswith("TOC "):
        kind = "toc_field"
    return {
        "paragraph_index": paragraph_index,
        "kind": kind,
        "display_text": display,
        "instruction_preview": instr[:240],
        "has_embedded_json": "{" in instr and "}" in instr,
    }


def _twips_from_mm(mm: float) -> int:
    return int(round(mm / 25.4 * 1440.0))


def build_docx_from_markdown(
    md_text: str,
    out_docx: Path,
    *,
    font_name: str = "Liberation Serif",
    body_size_pt: float = 11.0,
    line_spacing_multiple: float = 1.15,
    margins_mm: dict[str, float] | None = None,
    columns: int = 1,
    page_width_mm: float = 210.0,
    page_height_mm: float = 297.0,
) -> dict[str, Any]:
    """Generate a valid, editable OOXML .docx from Markdown using pure Python stdlib."""
    margins = margins_mm or {"top": 25.4, "bottom": 25.4, "left": 25.4, "right": 25.4}
    sz_half_pt = int(round(body_size_pt * 2))
    line_twips = int(round(line_spacing_multiple * 240))

    body_parts: list[str] = []
    lines = md_text.splitlines()
    i = 0
    heading_count = 0
    para_count = 0
    table_count = 0

    def _run_xml(text: str, *, bold: bool = False, italic: bool = False, size_hp: int | None = None) -> str:
        rpr = [f'<w:rFonts w:ascii="{xml_escape(font_name)}" w:hAnsi="{xml_escape(font_name)}" w:eastAsia="{xml_escape(font_name)}" w:cs="{xml_escape(font_name)}"/>']
        if bold:
            rpr.append("<w:b/>")
        if italic:
            rpr.append("<w:i/>")
        if size_hp is not None:
            rpr.append(f'<w:sz w:val="{size_hp}"/><w:szCs w:val="{size_hp}"/>')
        return f'<w:r><w:rPr>{"".join(rpr)}</w:rPr><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>'

    def _inline_runs(text: str, size_hp: int | None = None) -> str:
        # Handle **bold** and *italic* cleanly without leaking markdown markers
        tokens = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text)
        out = []
        for tok in tokens:
            if not tok:
                continue
            if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
                out.append(_run_xml(tok[2:-2], bold=True, size_hp=size_hp))
            elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
                out.append(_run_xml(tok[1:-1], italic=True, size_hp=size_hp))
            else:
                out.append(_run_xml(tok, size_hp=size_hp))
        return "".join(out) or _run_xml("", size_hp=size_hp)

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue

        # Markdown pipe table -> three-line academic Word table
        if stripped.startswith("|") and "|" in stripped[1:]:
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl_lines.append(lines[i].strip())
                i += 1
            parsed_rows = []
            for tl in tbl_lines:
                cells = [c.strip() for c in tl.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                    continue
                parsed_rows.append(cells)
            if parsed_rows:
                table_count += 1
                rows_xml = []
                for r_idx, row in enumerate(parsed_rows):
                    is_header = r_idx == 0
                    is_last = r_idx == len(parsed_rows) - 1
                    top_b = '<w:top w:val="single" w:sz="12" w:space="0" w:color="000000"/>' if is_header else '<w:top w:val="none"/>'
                    bot_b = (
                        '<w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
                        if is_header
                        else ('<w:bottom w:val="single" w:sz="12" w:space="0" w:color="000000"/>' if is_last else '<w:bottom w:val="none"/>')
                    )
                    tcs = []
                    for cell in row:
                        tcs.append(
                            f'<w:tc><w:tcPr><w:tcBorders>{top_b}{bot_b}<w:left w:val="none"/><w:right w:val="none"/></w:tcBorders></w:tcPr>'
                            f'<w:p><w:pPr><w:spacing w:line="{line_twips}" w:lineRule="auto"/></w:pPr>'
                            f'{_inline_runs(cell, size_hp=max(16, sz_half_pt - 2))}</w:p></w:tc>'
                        )
                    rows_xml.append(f"<w:tr>{''.join(tcs)}</w:tr>")
                body_parts.append(
                    '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>' + "".join(rows_xml) + "</w:tbl>"
                )
            continue

        # Headings
        hm = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if hm:
            lvl = min(len(hm.group(1)), 3)
            htext = hm.group(2).strip()
            heading_count += 1
            para_count += 1
            h_sz = sz_half_pt + (6 if lvl == 1 else (4 if lvl == 2 else 2))
            body_parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading{lvl}"/><w:outlineLvl w:val="{lvl - 1}"/>'
                f'<w:spacing w:before="200" w:after="100" w:line="{line_twips}" w:lineRule="auto"/></w:pPr>'
                f'{_run_xml(htext, bold=True, size_hp=h_sz)}</w:p>'
            )
            i += 1
            continue

        # Bullet item
        bm = re.match(r"^[-*]\s+(.*)$", stripped)
        if bm:
            para_count += 1
            body_parts.append(
                f'<w:p><w:pPr><w:ind w:left="360"/><w:spacing w:after="80" w:line="{line_twips}" w:lineRule="auto"/></w:pPr>'
                f'{_run_xml("• ", size_hp=sz_half_pt)}{_inline_runs(bm.group(1), size_hp=sz_half_pt)}</w:p>'
            )
            i += 1
            continue

        # Normal paragraph (join contiguous non-blank non-heading lines)
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith("#") or nxt.startswith("|") or re.match(r"^[-*]\s+", nxt):
                break
            para_lines.append(nxt)
            i += 1
        para_count += 1
        joined = " ".join(para_lines)
        body_parts.append(
            f'<w:p><w:pPr><w:pStyle w:val="Normal"/><w:spacing w:after="120" w:line="{line_twips}" w:lineRule="auto"/></w:pPr>'
            f'{_inline_runs(joined, size_hp=sz_half_pt)}</w:p>'
        )

    sect_pr = (
        f'<w:sectPr>'
        f'<w:pgSz w:w="{_twips_from_mm(page_width_mm)}" w:h="{_twips_from_mm(page_height_mm)}"/>'
        f'<w:pgMar w:top="{_twips_from_mm(margins.get("top", 25.4))}" '
        f'w:right="{_twips_from_mm(margins.get("right", 25.4))}" '
        f'w:bottom="{_twips_from_mm(margins.get("bottom", 25.4))}" '
        f'w:left="{_twips_from_mm(margins.get("left", 25.4))}" w:header="720" w:footer="720" w:gutter="0"/>'
        f'<w:cols w:num="{max(1, int(columns))}" w:space="425"/>'
        f'</w:sectPr>'
    )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
        f'<w:body>{"".join(body_parts)}{sect_pr}</w:body></w:document>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:styles xmlns:w="{W_NS}">'
        f'<w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{xml_escape(font_name)}" w:hAnsi="{xml_escape(font_name)}" '
        f'w:eastAsia="{xml_escape(font_name)}" w:cs="{xml_escape(font_name)}"/>'
        f'<w:sz w:val="{sz_half_pt}"/><w:szCs w:val="{sz_half_pt}"/>'
        f'</w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
        '</w:styles>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    out_docx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/document.xml", document_xml)

    return {
        "ok": True,
        "docx_path": str(out_docx),
        "font_name": font_name,
        "body_size_pt": body_size_pt,
        "line_spacing_multiple": line_spacing_multiple,
        "columns": columns,
        "margins_mm": margins,
        "heading_count": heading_count,
        "paragraph_count": para_count,
        "table_count": table_count,
    }


def render_docx_preview(docx_path: Path, out_dir: Path) -> dict[str, Any]:
    """Render .docx to PDF (and PNG pages if pdftoppm exists) via LibreOffice; degrade honestly if unavailable."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    pdftoppm = shutil.which("pdftoppm")
    if not soffice:
        return {
            "rendered": False,
            "degraded_reason": "soffice/libreoffice not installed in runtime environment",
            "pdf_path": None,
            "png_pages": [],
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    user_profile = out_dir / ".lo_profile"
    cmd = [
        soffice,
        "--headless",
        f"-env:UserInstallation=file://{user_profile.as_posix()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    pdf_path = out_dir / f"{docx_path.stem}.pdf"
    if proc.returncode != 0 or not pdf_path.is_file():
        return {
            "rendered": False,
            "degraded_reason": f"soffice failed (rc={proc.returncode}): {proc.stderr.strip()[:240]}",
            "pdf_path": None,
            "png_pages": [],
        }
    png_pages: list[str] = []
    png_note = None
    if pdftoppm:
        prefix = out_dir / f"{docx_path.stem}_page"
        p2 = subprocess.run(
            [pdftoppm, "-png", "-r", "150", str(pdf_path), str(prefix)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if p2.returncode == 0:
            png_pages = sorted(str(p) for p in out_dir.glob(f"{docx_path.stem}_page*.png"))
        else:
            png_note = f"pdftoppm failed (rc={p2.returncode})"
    else:
        png_note = "pdftoppm not installed; PDF preview generated without PNG rasterization"
    shutil.rmtree(user_profile, ignore_errors=True)
    return {
        "rendered": True,
        "pdf_path": str(pdf_path),
        "pdf_bytes": pdf_path.stat().st_size,
        "png_pages": png_pages,
        "note": png_note,
    }


# ---------------------------------------------------------------------------
# 4. General Input Corpus Reader & Structured Output Contract Helper
# ---------------------------------------------------------------------------

def _extract_pdf_text_stdlib(path: Path) -> str:
    """Pure-stdlib fallback PDF text extractor for uncompressed/FlateDecode Tj/TJ streams."""
    raw = path.read_bytes()
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        blob = m.group(1)
        data = blob
        try:
            data = zlib.decompress(blob)
        except Exception:
            try:
                data = zlib.decompress(blob, -zlib.MAX_WBITS)
            except Exception:
                pass
        if b"Tj" not in data and b"TJ" not in data:
            continue
        text_stream = data.decode("latin-1", errors="ignore")
        # Extract literal strings inside (...) before Tj or inside [...] TJ
        for sm in re.finditer(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj|\[(.*?)\]\s*TJ", text_stream, re.S):
            if sm.group(1) is not None:
                s = sm.group(1)
                s = s.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
                chunks.append(s)
            elif sm.group(2) is not None:
                parts = re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", sm.group(2))
                joined = "".join(p.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\") for p in parts)
                if joined:
                    chunks.append(joined)
        if len(chunks) > 4000:
            break
    out = re.sub(r"[ \t]+", " ", "\n".join(chunks)).strip()
    printable = sum(1 for c in out if c.isalnum() or c in " .,;:%+-()[]/")
    if len(out) >= 40 and printable / max(1, len(out)) >= 0.55:
        return out
    return ""


def _extract_jats_xml_or_html(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract structured Markdown-like full text from EuropePMC JATS XML or academic HTML."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    suf = path.suffix.lower()
    if suf == ".xml":
        try:
            root = ET.fromstring(raw)
            lines: list[str] = []
            # Title & DOI
            title_el = root.find(".//article-title")
            if title_el is not None:
                t = " ".join("".join(title_el.itertext()).split())
                if t:
                    lines.append(f"# {t}")
            for aid in root.findall(".//article-id"):
                if aid.attrib.get("pub-id-type") == "doi" and aid.text:
                    lines.append(f"DOI: {aid.text.strip()}")
            # Abstract
            abs_el = root.find(".//abstract")
            if abs_el is not None:
                lines.append("\n## Abstract")
                for p in abs_el.findall(".//p"):
                    pt = " ".join("".join(p.itertext()).split())
                    if pt:
                        lines.append(pt)
            # Body sections
            body = root.find(".//body")
            if body is not None:
                for sec in body.findall(".//sec"):
                    st = sec.find("title")
                    if st is not None:
                        ht = " ".join("".join(st.itertext()).split())
                        if ht:
                            lines.append(f"\n## {ht}")
                    for child in list(sec):
                        tag = child.tag.split("}")[-1]
                        if tag == "p":
                            pt = " ".join("".join(child.itertext()).split())
                            if pt:
                                lines.append(pt)
                        elif tag in ("table-wrap", "fig"):
                            cap = child.find(".//caption")
                            if cap is not None:
                                ct = " ".join("".join(cap.itertext()).split())
                                if ct:
                                    lines.append(ct)
            # References
            refs = root.findall(".//ref-list//ref")
            if refs:
                lines.append("\n## References")
                for idx, ref in enumerate(refs[:80], 1):
                    rt = " ".join("".join(ref.itertext()).split())
                    if rt:
                        lines.append(f"[{idx}] {rt}")
            text = "\n".join(lines).strip()
            if len(text) >= 60:
                return text, {"extractor": "stdlib_jats_xml", "ref_count": len(refs)}
        except ET.ParseError:
            pass
    # Fallback HTML / generic XML strip
    cleaned = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", raw, flags=re.S | re.I)
    cleaned = re.sub(r"<(h[1-4])[^>]*>(.*?)</\1>", lambda m: f"\n## {re.sub(r'<[^>]+>', '', m.group(2)).strip()}\n", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"</(?:p|div|li|tr|section)>", "\n", cleaned, flags=re.I)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, {"extractor": f"stdlib_{suf.lstrip('.')}_text"}


def read_document_text(path: Path) -> dict[str, Any]:
    """Read text from MD/TXT/TEX/JSON/CSV/BIB/DOCX/XML/HTML/PDF with stdlib fallback & honest degradation."""
    suf = path.suffix.lower()
    if suf == ".docx":
        info = inspect_docx_ooxml(path)
        return {"ok": info.get("ok", False), "format": "docx", "text": info.get("full_text", ""), "meta": info}
    if suf in {".xml", ".html", ".htm"}:
        try:
            text, meta = _extract_jats_xml_or_html(path)
            return {"ok": bool(text), "format": suf.lstrip("."), "text": text, "meta": meta}
        except OSError as exc:
            return {"ok": False, "format": suf.lstrip("."), "text": "", "meta": {"error": str(exc)}}
    if suf == ".pdf":
        pdftotext = shutil.which("pdftotext")
        pdf_timeout_note = None
        if pdftotext:
            try:
                p = subprocess.run([pdftotext, str(path), "-"], capture_output=True, text=True, timeout=60)
                if p.returncode == 0 and p.stdout.strip():
                    return {"ok": True, "format": "pdf", "text": p.stdout, "meta": {"extractor": "pdftotext"}}
            except subprocess.TimeoutExpired:
                pdf_timeout_note = "pdftotext_timeout_after_60s"
        fb = _extract_pdf_text_stdlib(path)
        if fb:
            return {
                "ok": True,
                "format": "pdf",
                "text": fb,
                "meta": {
                    "extractor": "stdlib_pdf_stream_fallback",
                    "fallback_used": True,
                    "note": pdf_timeout_note or "pdftotext unavailable; extracted uncompressed/FlateDecode PDF streams via Python stdlib",
                },
            }
        return {
            "ok": False,
            "format": "pdf",
            "text": "",
            "meta": {"error": pdf_timeout_note or "pdftotext unavailable and stdlib stream fallback extracted no readable text (likely scanned/encrypted/CID-encoded PDF)"},
        }
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "format": suf.lstrip(".") or "text", "text": text, "meta": {"bytes": path.stat().st_size}}
    except OSError as exc:
        return {"ok": False, "format": suf.lstrip("."), "text": "", "meta": {"error": str(exc)}}


read_text_auto = read_document_text


def load_case_contract(input_dir: Path | None) -> dict[str, Any]:
    """Consume /input/case.json and /input/task.md to capture case_id, dynamic output_contract keys, and audience/format constraints."""
    info: dict[str, Any] = {
        "case_id": None,
        "output_contract_keys": [],
        "task_constraints": {},
        "task_excerpt": "",
    }
    if not input_dir or not input_dir.is_dir():
        return info
    cj = input_dir / "case.json"
    if cj.is_file():
        try:
            obj = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict):
                if obj.get("case_id"):
                    info["case_id"] = str(obj["case_id"])
                oc = obj.get("output_contract")
                if isinstance(oc, dict):
                    info["output_contract_keys"] = list(oc.keys())
                for k in ("audience", "genre", "target_journal", "citation_style", "word_limit", "language", "journal_structure"):
                    if obj.get(k) not in (None, ""):
                        info["task_constraints"][k] = obj[k]
        except Exception:
            pass
    tm = input_dir / "task.md"
    if tm.is_file():
        txt = tm.read_text(encoding="utf-8", errors="replace")
        info["task_excerpt"] = txt[:600].strip()
        for key, pat in (
            ("target_journal", r"(?:target\s+journal|目标期刊)\s*[:：=]\s*([^\n。;]+)"),
            ("citation_style", r"(?:citation\s+style|reference\s+style|引用格式|参考文献格式)\s*[:：=]\s*([^\n。;]+)"),
            ("audience", r"(?:audience|受众|读者)\s*[:：=]\s*([^\n。;]+)"),
            ("word_limit", r"(?:word\s+limit|字数(?:上限|限制|要求))\s*[:：=]\s*([^\n。;]+)"),
        ):
            m = re.search(pat, txt, re.I)
            if m and key not in info["task_constraints"]:
                info["task_constraints"][key] = m.group(1).strip()
    return info


def build_writing_result(
    *,
    case_id: str,
    task_id: str,
    deliverable: Any,
    preserved_facts: dict[str, Any],
    evidence_map: list[dict[str, Any]],
    change_log: list[dict[str, Any]],
    artifacts: list[str],
    sources: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    limitations: list[str],
    verification: dict[str, Any],
    status: str | None = None,
    case_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured output-contract-compliant result.json payload preserving all required and dynamic output_contract keys."""
    final_gaps = list(gaps)
    payload: dict[str, Any] = {
        "case_id": (case_contract or {}).get("case_id") or case_id,
        "task_id": task_id,
        "status": status or ("partial_with_gaps" if final_gaps else "ok"),
        "deliverable": deliverable,
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "change_log": change_log,
        "artifacts": artifacts,
        "sources": sources,
        "gaps": final_gaps,
        "limitations": limitations,
        "verification": verification,
    }
    if case_contract:
        if case_contract.get("task_constraints"):
            payload["task_constraints_applied"] = case_contract["task_constraints"]
        for k in case_contract.get("output_contract_keys", []):
            if k not in payload:
                # Never guess unknown contract keys by substring heuristics; record null & honest gap
                payload[k] = None
                final_gaps.append({
                    "type": "unmapped_output_contract_key",
                    "field": k,
                    "detail": f"Dynamic output_contract key '{k}' has no verified deterministic source; left null rather than guessing.",
                })
        if final_gaps and not status:
            payload["status"] = "partial_with_gaps"
    return payload


if __name__ == "__main__":
    # Built-in self-test
    s1 = "In the high-dose cohort (n = 42), treatment may be associated with a 18.4% reduction (p = 0.041, Fig. 2A) [1-2]."
    s2 = "In the high-dose cohort (n = 42), treatment proves a 18.4% reduction (p = 0.041, Fig. 2A) [1-2]."
    d = diff_invariants(s1, s2)
    p = audit_academic_prose("近年来受到广泛关注，这不是数据问题，而是底层逻辑赋能的折叠变化。")
    print(json.dumps({"diff_preserved": d["preserved"], "violations": d["violations"], "prose_high": p["high_count"], "prose_warn": p["warning_count"]}, ensure_ascii=False, indent=2))
