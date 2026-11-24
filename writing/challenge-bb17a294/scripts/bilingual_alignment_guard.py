#!/usr/bin/env python3
"""Deterministic bilingual academic translation guard and /input auto-extractor.

Pure Python 3.12 stdlib (zero third-party dependencies). Supports:
  - Native .pdf/.docx/.md/.txt auto-extraction via doc_extract.py (--source-format auto),
    including OOXML w:instrText / w:fldChar live citation field detection (Zotero/CSL,
    Mendeley, EndNote, Citavi, Word REF) with honest citation_fields_preserved=false reporting
  - --extract-terms-and-refs / --from-input: auto-generate source_extracted.txt,
    source_blocks.json, glossary_draft.json, and immutable source_citation_ledger /
    reference_map_draft.json from /input
  - Multi-channel fidelity audit: Decimal-backed numbers/ranges/magnitudes/SI units/order
    (with attested reordered_clause_note), p-values, Markdown table cell-by-cell structure
    (respecting escaped \\|), heading hierarchy 1:1 alignment (HEADING_HIERARCHY_DRIFT),
    displays, LaTeX math, live citations & immutable source_citation_ledger bibliography
    integrity (blocking unverified external additions, unauthorized DOI completions, and
    cross-entry translated title swaps), sentence-level negation scope, causal overclaim
    escalation, glossary lock, and bounded anti-translationese
  - Sentence-level bilingual_aligned.md export + 5-field Harbor contract patch
"""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path
from typing import Any

from doc_extract import extract_document

SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
MULT_DEC: dict[str, Decimal] = {
    "": Decimal("1"), "%": Decimal("1"), "％": Decimal("1"),
    "千": Decimal("1000"), "万": Decimal("10000"), "十万": Decimal("100000"),
    "百万": Decimal("1000000"), "千万": Decimal("10000000"), "亿": Decimal("100000000"),
    "thousand": Decimal("1000"), "million": Decimal("1000000"), "billion": Decimal("1000000000"),
}
UNIT_CANON_MAP: dict[str, str] = {
    "mg/kg": "mg/kg", "毫克/千克": "mg/kg",
    "ug/ml": "ug/mL", "μg/ml": "ug/mL", "µg/ml": "ug/mL", "微克/毫升": "ug/mL",
    "mg/ml": "mg/mL", "毫克/毫升": "mg/mL", "ng/ml": "ng/mL", "纳克/毫升": "ng/mL", "pg/ml": "pg/mL",
    "ug/l": "ug/L", "μg/l": "ug/L", "µg/l": "ug/L", "mg/l": "mg/L", "毫克/升": "mg/L",
    "mol/l": "mol/L", "mmol/l": "mmol/L", "umol/l": "umol/L", "μmol/l": "umol/L", "µmol/l": "umol/L",
    "um": "uM_or_um", "mm": "mM_or_mm", "nm": "nM_or_nm", "pm": "pM_or_pm", "μm": "uM_or_um", "µm": "uM_or_um",
    "微摩尔": "uM", "毫摩尔": "mM", "纳摩尔": "nM",
    "纳米": "nm", "微米": "um", "毫米": "mm", "厘米": "cm", "千米": "km", "cm": "cm", "km": "km",
    "ml": "mL", "毫升": "mL", "ul": "uL", "μl": "uL", "µl": "uL", "微升": "uL",
    "kg": "kg", "千克": "kg", "mg": "mg", "毫克": "mg", "ug": "ug", "μg": "ug", "µg": "ug", "微克": "ug",
    "kda": "kDa", "千道尔顿": "kDa", "da": "Da", "°c": "degC", "℃": "degC", "摄氏度": "degC",
    "hz": "Hz", "khz": "kHz", "mhz": "MHz", "ghz": "GHz", "ev": "eV", "kev": "keV", "mev": "MeV",
    "pa": "Pa", "kpa": "kPa", "mpa": "MPa", "ms": "ms", "bp": "bp", "kb": "kb", "mb": "Mb", "gb": "Gb",
    "ma/cm2": "mA/cm2", "ma/cm²": "mA/cm2", "rpm": "rpm",
}
UNIT_TOKEN_PATTERN = (
    r"(?:mg/kg|毫克/千克|[uμµ]g/m[lL]|微克/毫升|mg/m[lL]|毫克/毫升|ng/m[lL]|纳克/毫升|pg/m[lL]|"
    r"[uμµ]g/L|mg/L|毫克/升|mmol/L|[uμµ]mol/L|mol/L|mA/cm[2²]|rpm|"
    r"[uμµ]M|mM|nM|pM|微摩尔|毫摩尔|纳摩尔|[uμµ]m|nm|mm|cm|km|纳米|微米|毫米|厘米|千米|"
    r"mL|[uμµ]L|毫升|微升|kg|mg|[uμµ]g|千克|毫克|微克|"
    r"kDa|Da|千道尔顿|°C|℃|摄氏度|GHz|MHz|kHz|Hz|MeV|keV|eV|MPa|kPa|Pa|ms|kb|Mb|Gb|bp)"
)

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]{2,}[A-Za-z0-9)]")
TRUNCATED_DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/|doi\s*[:：]\s*)(10\.(?:\d{1,3}(?=$|[\s.,;)\]）])|\d{4,9}/?(?=$|[\s.,;)\]）])|\d{4,9}/[-._;()/:A-Za-z0-9]{1,2}(?=$|[\s.,;)\]）])))",
    re.I,
)
INLINE_REORDER_NOTE_RE = re.compile(r"<!--\s*reordered_clause_note\s*:\s*(.*?)\s*-->", re.I | re.S)
HEADING_LINE_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$")
MATH_BLOCK_RE = re.compile(
    r"\$\$(.+?)\$\$|\$(?!\s*\d+(?:,\d{3})*(?:\.\d+)?\s*(?:thousand|million|billion)?(?:\s|$|[.,;:\)]))([^\$\n]+?)\$",
    re.S,
)
DISPLAY_RE = re.compile(
    r"(?:(?:Extended\s+Data\s+|Supplementary\s+)?(?:Fig(?:ure|\.)?|图|附图|补充图)\s*([Ss]?\d+[A-Za-z]?)"
    r"|(?:Supplementary\s+)?(?:Table|Tab\.|表|附表|补充表)\s*([Ss]?\d+[A-Za-z]?)"
    r"|(?:Equation|Eq\.?|公式|式)\s*\(?([Ss]?\d+[A-Za-z]?)\)?)",
    re.I,
)
LATEX_CITE_RE = re.compile(r"\\cite[pt]?\*?\{([^}]+)\}")
BRACKET_CITE_RE = re.compile(r"(?<!CI\s)(?<!置信区间\s)(?<!CI)\[(\d+(?:\s*[-–—,]\s*\d+)*)\]")
AUTHOR_YEAR_GROUP_RE = re.compile(
    r"[\(（]([^()（）\n]{2,120}?(?:19|20)\d{2}[a-z]?(?:\s*[;；]\s*[^()（）\n]{2,120}?(?:19|20)\d{2}[a-z]?)*)[\)）]"
)
AUTHOR_YEAR_ITEM_RE = re.compile(
    r"([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|&\s+[A-Z][A-Za-z\-]+|and\s+[A-Z][A-Za-z\-]+))?|"
    r"[\u4e00-\u9fff]{2,4}(?:等|和[\u4e00-\u9fff]{2,4}|与[\u4e00-\u9fff]{2,4})?)\s*[,，]\s*((?:19|20)\d{2}[a-z]?)"
)
PVAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])([pP])\s*(<=|>=|≤|≥|<|>|=)\s*"
    r"(\d+(?:\.\d+)?(?:\s*(?:[×x\*]\s*10(?:\s*\^\s*\{?\s*[+-]?\d+\s*\}?|[⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+)|[eE][+-]?\d+))?)"
)

# Explicit numeric range parser: catches 40-60, 12.5–18.0%, 10~20 mg/L, -0.2-28.4, 0 to 19, 0至19
RANGE_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])"
    r"(-?\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*(%|％|十万|百万|千万|万|亿|千|thousand|million|billion)(?![A-Za-z]))?"
    r"(?:\s*(" + UNIT_TOKEN_PATTERN + r")(?![A-Za-z]))?"
    r"(?:\s*([-–—~～])\s*|\s+(?:to)\s+|\s*(?:至|到)\s*)"
    r"(-?\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*(%|％|十万|百万|千万|万|亿|千|thousand|million|billion)(?![A-Za-z]))?"
    r"(?:\s*(" + UNIT_TOKEN_PATTERN + r")(?![A-Za-z]))?"
    r"(?![-\d])",
    re.I,
)

PROSE_NUM_RE = re.compile(
    r"(?:百分之\s*(\d+(?:\.\d+)?)|"
    r"(?<![A-Za-z0-9_.])(-?)(?<![0-9])(\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*(?:[×x\*]\s*10(?:\s*\^\s*\{?\s*([+-]?\d+)\s*\}?|([⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+))|[eE]([+-]?\d+))"
    r"|\s*(%|％|十万|百万|千万|万|亿|千|thousand|million|billion)(?![A-Za-z]))?"
    r"(?:\s*(" + UNIT_TOKEN_PATTERN + r")(?![A-Za-z]))?)",
    re.I,
)

NEG_EN_RE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|none|cannot|can't|fail(?:s|ed)?\s+to|"
    r"unable\s+to|lack(?:s|ed|ing)?\s+of|absence\s+of|unlike|no\s+longer|non-?significant)\b",
    re.I,
)
# Exclude non-polar "不同" (different) while keeping "不同于" (unlike) and genuine negations
NEG_ZH_RE = re.compile(
    r"不[是会能应可存在足均再具有显著使采含]|"
    r"未[能见曾达发观出显著产呈报检获经使采必含]|"
    r"尚[未无]|"
    r"无[法显明统差关相需须受身数证记变异]|"
    r"并非|绝非|缺乏|缺少|不同于|不再|没有|非显著"
)
HEDGE_EN_RE = re.compile(
    r"\b(?:may|might|could|suggests?|indicates?|associated\s+with|correlated\s+with|"
    r"approximately|roughly|only|primarily|limited\s+to|preliminary|trend\s+toward|consistent\s+with|potentially)\b",
    re.I,
)
HEDGE_ZH_RE = re.compile(r"可能|或许|提示|表明可能|相关|关联|约|大约|仅|主要|局限于|初步|趋势|一致|潜在")
CAUSAL_STRONG_EN_RE = re.compile(
    r"\b(?:proves?|proved|proven|definitively\s+establishes?|directly\s+causes?|causes?|caused|deterministically)\b",
    re.I,
)
CAUSAL_STRONG_ZH_RE = re.compile(r"证明了?|证实了?|确证了?|直接导致|导致了|必然引起|必然导致|决定性(?:地)?(?:引起|决定)")

TRANSLATIONESE_ZH_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("轻动词膨胀", re.compile(r"(?:进行|作出|给予|予以)了?[^，。\n]{0,6}(?:分析|调整|优化|支持|评估|检查|讨论|验证|比较)"), "改为直接动词（如“分析了/评估了/优化了”）"),
    ("扮演角色直译", re.compile(r"(?:扮演|承担)了?[^，。\n]{0,8}角色"), "改为“起关键作用/介导/调控”"),
    ("以一种…方式", re.compile(r"以一种[^，。\n]{2,12}的(?:方式|形式)"), "改为直接副词或机制表述"),
    ("翻案腔", re.compile(r"(?:不是|并非)[^，。\n]{1,20}[，,]?而是"), "正面直接陈述，避免翻案修辞"),
    ("破折号滥用", re.compile(r"——"), "减少双破折号，改用逗号、括号或短句"),
    ("形式主语填充", re.compile(r"值得注意的是|有必要指出的是|可以说的是|需要指出的是"), "删过渡套话，直入研究事实"),
    ("在…的过程中", re.compile(r"在[^，。\n]{2,18}(?:的过程中|的情况下)"), "简化为“……期间/……时/在……条件下”"),
    ("使得…能够", re.compile(r"使得?[^，。\n]{0,12}(?:能够|可以)"), "改为紧凑主动句"),
    ("程度直译", re.compile(r"在某种程度上|从某种意义上说|在很大程度上|相对而言"), "交代具体限定范围"),
    ("的…的…的连用", re.compile(r"的[^，。\n]{1,8}的[^，。\n]{1,8}的"), "拆分为两句，避免三层嵌套定语"),
    ("AI模板总结/拔高", re.compile(r"综上所述|总而言之|由此可见|不难发现|众所周知|显著赋能|深刻揭示|开启新的篇章"), "删除 AI 模板语"),
    ("提示性冒号", re.compile(r"(?:一句话总结|核心是|关键是|结论是|本质是|重点是)[：:]"), "去掉提示性冒号壳"),
]
TRANSLATIONESE_EN_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("EN AI Cliché", re.compile(r"\b(?:it\s+is\s+worth\s+noting\s+that|plays\s+a\s+pivotal\s+role|delve\s+into|in\s+the\s+realm\s+of|a\s+myriad\s+of|tapestry\s+of|underscores\s+the\s+importance\s+of)\b", re.I), "Replace stock phrasing with concrete domain verbs"),
    ("EN Em-dash Overuse", re.compile(r"—"), "Prefer commas, parentheses, or split sentences"),
]

REF_HEADING_RE = re.compile(
    r"^\s*#{0,6}\s*(?:References|Bibliography|Literature\s+Cited|Works\s+Cited|Cited\s+Literature|参考文献|引文目录|对比文件)\s*:?\s*$",
    re.I,
)
REF_ENTRY_PREFIX_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2}[a-z]?)\b")
CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
ABBREV_PROTECT_RE = re.compile(
    r"\b(?:et\s+al|Fig|Figs|Tab|Eq|Eqs|Dr|Prof|Mr|Mrs|Ms|vs|e\.g|i\.e|cf|No|Vol|approx|Rev|St|Inc|Ltd|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|S\.D|p\.o|i\.v)\.",
    re.I,
)
TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")

# Bilingual academic domain-concept pairs used to catch swapped Chinese->English reference titles
# even when --references metadata is omitted.
BILINGUAL_CONCEPT_PAIRS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("中毒", "毒物", "毒理"), ("poison", "intoxicat", "toxicol")),
    (("儿科", "儿童", "青少年", "小儿"), ("pediatr", "paediatr", "child", "adolesc")),
    (("流行病学", "发病率", "监测"), ("epidemiol", "incidence", "surveillance")),
    (("电池", "液流电池", "储能"), ("battery", "batteries", "flow battery", "energy storage")),
    (("电极", "碳毡", "电解液"), ("electrode", "carbon felt", "electrolyte")),
    (("催化", "改性", "催化剂"), ("cataly", "modif", "catalyst")),
    (("肿瘤", "癌症", "癌"), ("tumor", "tumour", "cancer", "carcinoma", "oncol")),
    (("基因", "表达", "转录"), ("gene", "expression", "transcript")),
    (("蛋白", "蛋白质", "酶"), ("protein", "enzyme", "proteom")),
    (("免疫", "抗体", "细胞因子"), ("immun", "antibod", "cytokine")),
    (("神经", "脑", "阿尔茨海默"), ("neur", "brain", "alzheimer")),
    (("心血管", "心肌", "高血压"), ("cardiovasc", "myocard", "hypertens")),
    (("糖尿病", "血糖", "胰岛素"), ("diabet", "glucose", "insulin")),
    (("临床", "随机对照", "队列"), ("clinical", "randomized", "cohort")),
    (("气候", "碳排放", "温室"), ("climate", "carbon emission", "greenhouse")),
    (("纳米", "复合材料", "薄膜"), ("nano", "composite", "thin film", "membrane")),
    (("机器学习", "深度学习", "神经网络"), ("machine learning", "deep learning", "neural network")),
    (("疫苗", "病毒", "感染"), ("vaccine", "virus", "viral", "infect")),
    (("农药", "除草剂", "杀虫剂"), ("pesticide", "herbicide", "insecticide", "agrochemical")),
    (("微生物", "肠道菌群", "细菌"), ("microb", "gut microbiota", "bacteri")),
    (("水稻", "小麦", "作物"), ("rice", "wheat", "crop")),
    (("抗生素", "耐药", "抗菌"), ("antibiotic", "resistance", "antimicrob")),
    (("光谱", "质谱", "色谱"), ("spectr", "mass spectr", "chromatogr")),
    (("传感器", "检测", "诊断"), ("sensor", "detect", "diagnos")),
    (("污染", "废水", "土壤"), ("pollut", "wastewater", "soil")),
    (("药物", "用药", "药代"), ("drug", "medicin", "pharmac")),
    (("自杀", "心理", "抑郁"), ("suicide", "psycholog", "depress")),
    (("危险品", "家庭", "储存"), ("hazardous", "household", "storage")),
]


def clean_doi(raw: str) -> str:
    d = raw.strip().lower()
    while d and d[-1] in ".,;":
        d = d[:-1]
    while d.endswith(")") and d.count(")") > d.count("("):
        d = d[:-1]
    return d


def extract_doi_and_truncation(raw_text: str) -> tuple[str | None, bool, str | None]:
    """Return (valid_doi, is_truncated, truncated_fragment)."""
    valid_matches = [clean_doi(m.group(0)) for m in DOI_RE.finditer(raw_text)]
    # Validate that suffix after '/' has at least 3 non-punctuation chars
    full_dois = [d for d in valid_matches if "/" in d and len(d.split("/", 1)[1]) >= 3]
    if full_dois:
        return full_dois[0], False, None
    tm = TRUNCATED_DOI_RE.search(raw_text)
    if tm:
        return None, True, clean_doi(tm.group(1))
    # Catch bare truncated DOI-like prefix such as "doi: 10.1" or "doi.org/10.1590"
    bare_tm = re.search(r"(?:doi\.org/|doi\s*[:：]\s*)(10\.\d{0,9}/?)", raw_text, re.I)
    if bare_tm:
        return None, True, clean_doi(bare_tm.group(1))
    return None, False, None


def normalize_unit(raw_unit: str) -> str:
    if not raw_unit:
        return ""
    u = raw_unit.strip()
    u_mu = u.replace("µ", "u").replace("μ", "u")
    if u_mu in ("uM", "mM", "nM", "pM", "um", "mm", "nm", "pm"):
        return u_mu
    low = u_mu.lower()
    mapped = UNIT_CANON_MAP.get(low, UNIT_CANON_MAP.get(u, u_mu))
    for suffix, pair in (("_or_um", ("uM", "um")), ("_or_mm", ("mM", "mm")), ("_or_nm", ("nM", "nm")), ("_or_pm", ("pM", "pm"))):
        if mapped.endswith(suffix):
            return pair[0] if u.endswith("M") else pair[1]
    return mapped


def normalize_operator(op: str) -> str:
    return {"≤": "<=", "≥": ">="}.get(op.strip(), op.strip())


def parse_sci_decimal(raw: str) -> Decimal:
    s = raw.strip().replace(",", "")
    m = re.match(
        r"^([+-]?\d+(?:\.\d+)?)\s*(?:[×x\*]\s*10(?:\s*\^\s*\{?\s*([+-]?\d+)\s*\}?|([⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+))|[eE]([+-]?\d+))$",
        s,
    )
    if m:
        exp_str = m.group(2) or (m.group(3).translate(SUPERSCRIPT_MAP) if m.group(3) else None) or m.group(4)
        return Decimal(m.group(1)) * (Decimal("10") ** int(exp_str))
    return Decimal(s)


def split_sentences_bilingual(text: str) -> list[str]:
    """Abbreviation-aware bilingual sentence splitter (EN + ZH)."""
    s = text.strip()
    if not s:
        return []
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if len(lines) == 1 and lines[0].startswith("#"):
        return [lines[0]]
    if len(lines) >= 2 and any(TABLE_SEP_RE.match(ln) for ln in lines[:3]):
        return [ln for ln in lines if not TABLE_SEP_RE.match(ln)]
    protected = ABBREV_PROTECT_RE.sub(lambda m: m.group(0).replace(".", "<DOT>"), s)
    protected = DOI_RE.sub(lambda m: m.group(0).replace(".", "<DOT>"), protected)
    protected = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", protected)
    raw_parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff\[\(\"“‘])|(?<=[。！？])\s*", protected)
    sents = [p.replace("<DOT>", ".").strip() for p in raw_parts if len(p.strip()) >= 3]
    return sents or [s]


def _split_md_table_row(line: str) -> list[str]:
    """Split a Markdown table row while preserving escaped pipes (\\|) inside cells."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith(r"\|"):
        s = s[:-1]
    cells: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n and s[i + 1] == "|":
            cur.append("|")
            i += 2
        elif s[i] == "|":
            cells.append("".join(cur).strip())
            cur = []
            i += 1
        else:
            cur.append(s[i])
            i += 1
    cells.append("".join(cur).strip())
    return cells


def parse_markdown_table(text: str) -> list[list[str]] | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2 or not any(TABLE_SEP_RE.match(ln) for ln in lines[:3]):
        return None
    rows: list[list[str]] = []
    for ln in lines:
        if TABLE_SEP_RE.match(ln) or "|" not in ln:
            continue
        rows.append(_split_md_table_row(ln))
    return rows if len(rows) >= 2 else None


def _page_for_line(line_no: int, page_offsets: list[dict[str, Any]]) -> int:
    for po in page_offsets:
        if not po.get("empty") and po["start_line"] <= line_no <= po["end_line"]:
            return int(po["page"])
    non_empty = [po for po in page_offsets if not po.get("empty")]
    return int(non_empty[-1]["page"]) if non_empty else 1


def _extract_lead_author(raw_ref: str) -> str:
    m = re.match(r"^\s*([A-Z][A-Za-z\-]+|[\u4e00-\u9fff]{2,4})", raw_ref)
    return m.group(1) if m else ""


def _extract_title_candidate(raw_ref: str) -> str:
    s = DOI_RE.sub("", raw_ref)
    s = re.sub(r"https?://\S+", "", s).strip()
    parts = [p.strip() for p in re.split(r"[.。]", s) if len(p.strip()) >= 4]
    if len(parts) >= 2:
        return parts[1][:180]
    return s[:180]


def split_paragraphs_and_refs(
    text: str, page_offsets: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    lines = text.splitlines()
    ref_start_line: int | None = None
    for idx, line in enumerate(lines):
        if REF_HEADING_RE.match(line):
            ref_start_line = idx
            break

    body_text = "\n".join(lines[:ref_start_line]) if ref_start_line is not None else text
    ref_text = "\n".join(lines[ref_start_line + 1:]) if ref_start_line is not None else ""
    body_paras: list[dict[str, Any]] = []
    cur_buf: list[str] = []
    cur_start = 1

    def _flush_body(end_ln: int) -> None:
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
        tbl = parse_markdown_table(clean_p)
        hd_m = HEADING_LINE_RE.match(clean_p) if "\n" not in clean_p else None
        kind = "table" if tbl else ("heading" if hd_m else ("equation" if clean_p.startswith("$$") else "prose"))
        body_paras.append({
            "index": len(body_paras) + 1,
            "block_id": f"P{len(body_paras) + 1}",
            "kind": kind,
            "heading_level": len(hd_m.group(1)) if hd_m else 0,
            "heading_text": hd_m.group(2).strip() if hd_m else "",
            "start_line": cur_start,
            "end_line": max(cur_start, end_ln),
            "page": _page_for_line(cur_start, page_offsets or []),
            "text": clean_p,
            "table_rows": tbl,
            "inline_reorder_notes": notes,
        })

    body_lines = body_text.splitlines()
    for ln_no, line in enumerate(body_lines, 1):
        if not line.strip():
            _flush_body(ln_no - 1)
        else:
            if not cur_buf:
                cur_start = ln_no
            cur_buf.append(line)
    _flush_body(len(body_lines))

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
                cur_entry = {"key": pm.group(1) or pm.group(2), "line": ln_no, "raw_text": pm.group(3).strip()}
            elif cur_entry is not None:
                cur_entry["raw_text"] += " " + s
            else:
                cur_entry = {"key": str(len(ref_entries) + 1), "line": ln_no, "raw_text": s}
        if cur_entry:
            ref_entries.append(cur_entry)
        for entry in ref_entries:
            rt = entry["raw_text"]
            valid_doi, is_trunc, trunc_frag = extract_doi_and_truncation(rt)
            years = YEAR_RE.findall(rt)
            entry["page"] = _page_for_line(entry["line"], page_offsets or [])
            entry["doi"] = valid_doi
            entry["doi_truncated"] = is_trunc
            entry["truncated_doi_fragment"] = trunc_frag
            entry["year"] = years[0] if years else None
            entry["lead_author"] = _extract_lead_author(rt)
            entry["title_excerpt"] = _extract_title_candidate(rt)
            entry["has_cjk"] = bool(CJK_CHAR_RE.search(rt))
            entry["has_latin"] = bool(re.search(r"[A-Za-z]{2,}", rt))
    return body_paras, ref_entries, ref_start_line is not None


def _make_num_token(val_dec: Decimal, kind: str, unit: str, raw: str, *, is_range_bound: bool = False) -> dict[str, Any]:
    return {
        "dec_val": val_dec,
        "val_str": format(val_dec.normalize(), "f") if val_dec != 0 else "0",
        "val": float(val_dec),
        "kind": kind,
        "unit": unit,
        "raw": raw,
        "is_range_bound": is_range_bound,
    }


def extract_paragraph_channels(text: str) -> dict[str, Any]:
    work = text
    equations: list[str] = []
    work = MATH_BLOCK_RE.sub(lambda m: (equations.append(" ".join((m.group(1) or m.group(2) or "").split())) or " "), work)

    inline_valid_dois: list[str] = []
    inline_truncated_dois: list[str] = []
    for dm in DOI_RE.finditer(work):
        d = clean_doi(dm.group(0))
        if "/" in d and len(d.split("/", 1)[1]) >= 3:
            inline_valid_dois.append(d)
    work = DOI_RE.sub(" ", work)
    for tm in TRUNCATED_DOI_RE.finditer(work):
        inline_truncated_dois.append(clean_doi(tm.group(1)))
    work = TRUNCATED_DOI_RE.sub(" ", work)

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

    latex_cites: list[str] = []
    work = LATEX_CITE_RE.sub(lambda m: (latex_cites.extend([k.strip() for k in m.group(1).split(",") if k.strip()]) or " "), work)

    bracket_cites: list[str] = []
    bracket_cite_nums: list[int] = []
    def _strip_bracket(m: re.Match[str]) -> str:
        norm_inner = re.sub(r"\s*([-,–—])\s*", lambda x: "-" if x.group(1) in "-–—" else ",", m.group(1).strip())
        bracket_cites.append(norm_inner)
        for part in norm_inner.split(","):
            if "-" in part:
                a, b = map(int, part.split("-", 1))
                bracket_cite_nums.extend(range(a, b + 1) if 0 < b - a <= 50 else [a, b])
            elif part.isdigit():
                bracket_cite_nums.append(int(part))
        return " "
    work = BRACKET_CITE_RE.sub(_strip_bracket, work)

    author_year_cites: list[dict[str, Any]] = []
    def _strip_ay(m: re.Match[str]) -> str:
        items = list(AUTHOR_YEAR_ITEM_RE.finditer(m.group(1)))
        if not items:
            return m.group(0)
        for it in items:
            author_str, year_str = it.group(1).strip(), it.group(2).strip()
            lead = re.split(r"[\s&]|et\s+al|等|和|与", author_str)[0].strip()
            author_year_cites.append({
                "raw": f"{author_str}, {year_str}", "author": author_str, "lead": lead,
                "year": year_str, "script": "cjk" if CJK_CHAR_RE.search(author_str) else "latin",
            })
        return " "
    work = AUTHOR_YEAR_GROUP_RE.sub(_strip_ay, work)

    pvals: list[tuple[str, Decimal, str]] = []
    def _strip_pval(m: re.Match[str]) -> str:
        try:
            pvals.append((normalize_operator(m.group(2)), parse_sci_decimal(m.group(3)), m.group(0).strip()))
        except InvalidOperation:
            pass
        return " "
    work = PVAL_RE.sub(_strip_pval, work)

    # Extract explicit numeric ranges first with Decimal, preserving exact position order
    ordered_tokens: list[tuple[int, dict[str, Any]]] = []
    ranges_parsed: list[dict[str, Any]] = []
    chars = list(work)

    for m in RANGE_NUM_RE.finditer(work):
        lo_str, lo_mag, lo_u, sep, hi_str, hi_mag, hi_u = (
            m.group(1), (m.group(2) or "").lower(), m.group(3) or "",
            m.group(4) or "", m.group(5), (m.group(6) or "").lower(), m.group(7) or "",
        )
        # Skip ISO-like dates (e.g. 2021-05-16 already prevented by (?!-\d), also skip MM-DD after YYYY-)
        if sep == "-" and m.start() > 0 and work[m.start() - 1] == "-":
            continue
        try:
            lo_base = Decimal(lo_str.replace(",", ""))
            hi_base = Decimal(hi_str.replace(",", ""))
        except InvalidOperation:
            continue
        eff_lo_mag = lo_mag or (hi_mag if hi_mag in ("%", "％") else "")
        eff_hi_mag = hi_mag or (lo_mag if lo_mag in ("%", "％") else "")
        eff_lo_u = normalize_unit(lo_u or hi_u)
        eff_hi_u = normalize_unit(hi_u or lo_u)
        lo_kind = "%" if eff_lo_mag in ("%", "％") else "scalar"
        hi_kind = "%" if eff_hi_mag in ("%", "％") else "scalar"
        lo_val = lo_base if lo_kind == "%" else lo_base * MULT_DEC.get(eff_lo_mag, Decimal("1"))
        hi_val = hi_base if hi_kind == "%" else hi_base * MULT_DEC.get(eff_hi_mag, Decimal("1"))

        raw_span = m.group(0).strip()
        ordered_tokens.append((m.start(), _make_num_token(lo_val, lo_kind, eff_lo_u, f"{raw_span}[lo]", is_range_bound=True)))
        ordered_tokens.append((m.start() + 1, _make_num_token(hi_val, hi_kind, eff_hi_u, f"{raw_span}[hi]", is_range_bound=True)))
        ranges_parsed.append({
            "raw": raw_span, "lower": str(lo_val), "upper": str(hi_val),
            "kind": hi_kind, "unit": eff_hi_u,
        })
        for ci in range(m.start(), m.end()):
            chars[ci] = " "

    masked_work = "".join(chars)
    for m in PROSE_NUM_RE.finditer(masked_work):
        if m.group(1):
            try:
                ordered_tokens.append((m.start(), _make_num_token(Decimal(m.group(1)), "%", "", m.group(0).strip())))
            except InvalidOperation:
                pass
            continue
        sign_str = m.group(2) or ""
        if sign_str == "-" and m.start() > 0 and masked_work[m.start() - 1].isdigit():
            sign_str = ""
        try:
            base_val = Decimal((sign_str + m.group(3)).replace(",", ""))
        except InvalidOperation:
            continue
        exp_caret, exp_sup, exp_e = m.group(4), m.group(5), m.group(6)
        mag_unit = (m.group(7) or "").lower()
        si_unit = normalize_unit(m.group(8) or "")
        if exp_caret or exp_sup or exp_e:
            exp_val = int(exp_caret or (exp_sup.translate(SUPERSCRIPT_MAP) if exp_sup else exp_e))
            val, kind = base_val * (Decimal("10") ** exp_val), "scalar"
        elif mag_unit in ("%", "％"):
            val, kind = base_val, "%"
        else:
            val, kind = base_val * MULT_DEC.get(mag_unit, Decimal("1")), "scalar"
        ordered_tokens.append((m.start(), _make_num_token(val, kind, si_unit, m.group(0).strip())))

    ordered_tokens.sort(key=lambda pair: pair[0])
    numbers = [tok for _, tok in ordered_tokens]

    return {
        "equations": equations,
        "dois": inline_valid_dois,
        "truncated_dois": inline_truncated_dois,
        "displays": displays,
        "latex_cites": latex_cites,
        "bracket_cites": bracket_cites,
        "bracket_cite_nums": bracket_cite_nums,
        "author_year_cites": author_year_cites,
        "pvals": pvals,
        "numbers": numbers,
        "ranges": ranges_parsed,
    }


def _num_exact_dec(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["kind"] != b["kind"] or a["unit"] != b["unit"]:
        return False
    return a["dec_val"] == b["dec_val"]


def compare_numbers_with_order(
    src_nums: list[dict[str, Any]], tgt_nums: list[dict[str, Any]], reorder_note: str | None
) -> tuple[str, list[str]]:
    if len(src_nums) != len(tgt_nums):
        return "count_mismatch", [f"Numeric count mismatch: source={[x['raw'] for x in src_nums]} vs target={[x['raw'] for x in tgt_nums]}"]
    order_diffs = [
        f"{s['raw']!r} (val={s['val_str']}, kind={s['kind']}, unit={s['unit']!r}) != {t['raw']!r} (val={t['val_str']}, kind={t['kind']}, unit={t['unit']!r})"
        for s, t in zip(src_nums, tgt_nums) if not _num_exact_dec(s, t)
    ]
    if not order_diffs:
        return "order_exact", []
    unmatched = list(range(len(tgt_nums)))
    for s in src_nums:
        hit = next((i for i in unmatched if _num_exact_dec(s, tgt_nums[i])), None)
        if hit is None:
            return "value_or_unit_drift", [f"Value/unit drift: {d}" for d in order_diffs]
        unmatched.remove(hit)
    if reorder_note and len(reorder_note.strip()) >= 8:
        return "verified_reordered", []
    return "order_swap_unattested", [
        f"Numeric multiset matches but order changed without verified reordered_clause_note: source={[x['raw'] for x in src_nums]} vs target={[x['raw'] for x in tgt_nums]}"
    ]


def compare_markdown_tables(src_rows: list[list[str]], tgt_rows: list[list[str]], para_idx: int) -> list[str]:
    errs: list[str] = []
    if len(src_rows) != len(tgt_rows):
        errs.append(f"P{para_idx}: Table row count mismatch (source={len(src_rows)} vs target={len(tgt_rows)})")
    for r_idx, (sr, tr) in enumerate(zip(src_rows, tgt_rows), 1):
        if len(sr) != len(tr):
            errs.append(f"P{para_idx} Row {r_idx}: Table column count mismatch (source={len(sr)} vs target={len(tr)})")
            continue
        for c_idx, (sc, tc) in enumerate(zip(sr, tr), 1):
            sch, tch = extract_paragraph_channels(sc), extract_paragraph_channels(tc)
            _, n_errs = compare_numbers_with_order(sch["numbers"], tch["numbers"], None)
            if n_errs:
                errs.append(f"P{para_idx} Table[R{r_idx}C{c_idx}]: {n_errs[0]}")
            if len(sch["pvals"]) != len(tch["pvals"]) or any(
                so != to or sv != tv
                for (so, sv, _), (to, tv, _) in zip(sch["pvals"], tch["pvals"])
            ):
                errs.append(f"P{para_idx} Table[R{r_idx}C{c_idx}]: p-value mismatch ({[x[2] for x in sch['pvals']]} vs {[x[2] for x in tch['pvals']]})")
    return errs


def audit_heading_hierarchy(
    src_paras: list[dict[str, Any]], tgt_paras: list[dict[str, Any]]
) -> dict[str, Any]:
    """Verify 1:1 heading hierarchy count, depth sequence (H1/H2/H3...), and positional alignment."""
    src_headings = [
        {"paragraph": p["index"], "level": p["heading_level"], "text": p["heading_text"], "line": p["start_line"]}
        for p in src_paras if p["kind"] == "heading"
    ]
    tgt_headings = [
        {"paragraph": p["index"], "level": p["heading_level"], "text": p["heading_text"], "line": p["start_line"]}
        for p in tgt_paras if p["kind"] == "heading"
    ]
    drifts: list[str] = []
    src_levels = [h["level"] for h in src_headings]
    tgt_levels = [h["level"] for h in tgt_headings]
    if len(src_headings) != len(tgt_headings):
        drifts.append(
            f"Heading count mismatch: source has {len(src_headings)} heading(s) {src_levels} "
            f"vs target has {len(tgt_headings)} heading(s) {tgt_levels} (silent merging or dropping of section headings is forbidden)."
        )
    elif src_levels != tgt_levels:
        drifts.append(
            f"Heading depth sequence mismatch: source levels {src_levels} != target levels {tgt_levels}."
        )
    for sp, tp in zip(src_paras, tgt_paras):
        if (sp["kind"] == "heading") != (tp["kind"] == "heading"):
            drifts.append(
                f"P{sp['index']}: Heading/prose structural mismatch (source kind={sp['kind']} H{sp['heading_level']} "
                f"vs target kind={tp['kind']} H{tp['heading_level']})."
            )
    return {
        "aligned": len(drifts) == 0,
        "source_headings_count": len(src_headings),
        "target_headings_count": len(tgt_headings),
        "source_levels": src_levels,
        "target_levels": tgt_levels,
        "source_headings": src_headings,
        "target_headings": tgt_headings,
        "drifts": drifts,
    }


def _concept_overlap_score(zh_text: str, en_text: str) -> int:
    en_low = en_text.lower()
    score = 0
    for zh_terms, en_terms in BILINGUAL_CONCEPT_PAIRS:
        if any(z in zh_text for z in zh_terms) and any(e in en_low for e in en_terms):
            score += 1
    return score


def _distinctive_ascii_tokens(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "that", "this", "doi", "http", "https", "org", "vol", "pp", "et", "al", "rev", "med", "sci"}
    toks = set()
    for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text):
        tl = t.lower()
        if tl not in stop and not re.match(r"^(?:19|20)\d{2}$", tl):
            toks.add(tl)
    return toks


def build_source_citation_ledger(
    src_refs: list[dict[str, Any]],
    live_citation_fields: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Construct immutable source_citation_ledger preserving original fields, cite_key, DOI, and location."""
    ledger: list[dict[str, Any]] = []
    for r in src_refs:
        ledger.append({
            "cite_key": f"REF-{r['key']}",
            "key": str(r["key"]),
            "source_page": r.get("page", 1),
            "source_line": r["line"],
            "raw_source_text": r["raw_text"],
            "source_lead_author": r.get("lead_author", ""),
            "source_year": r.get("year"),
            "source_doi": r.get("doi"),
            "doi_truncated": bool(r.get("doi_truncated")),
            "truncated_doi_fragment": r.get("truncated_doi_fragment"),
            "source_title_excerpt": r.get("title_excerpt", ""),
            "translated_title": "",  # display-only field, strictly decoupled from raw_source_text
            "script": "cjk" if r.get("has_cjk") else "latin",
            "grounding_status": "SOURCE_DOI_TRUNCATED" if r.get("doi_truncated") else "SOURCE_GROUNDED",
        })
    for idx, lf in enumerate(live_citation_fields or [], 1):
        ledger.append({
            "cite_key": (lf.get("citation_keys") or [f"LIVE-FIELD-{idx}"])[0],
            "key": f"FIELD-{idx}",
            "source_page": 1,
            "source_line": lf.get("paragraph_index", 0),
            "raw_source_text": lf.get("display_text") or lf.get("instruction_preview", ""),
            "source_lead_author": "",
            "source_year": None,
            "source_doi": (lf.get("dois") or [None])[0],
            "doi_truncated": False,
            "truncated_doi_fragment": None,
            "source_title_excerpt": "",
            "translated_title": "",
            "script": "ooxml_field",
            "field_type": lf.get("field_type", ""),
            "preserved_in_markdown": False,
            "grounding_status": "OOXML_LIVE_FIELD_DEGRADED_TO_TEXT",
        })
    return ledger


def audit_bibliography(
    src_refs: list[dict[str, Any]],
    tgt_refs: list[dict[str, Any]],
    all_src_bracket_nums: list[int],
    all_tgt_bracket_nums: list[int],
    all_tgt_ay_cites: list[dict[str, Any]],
    ref_metadata: list[dict[str, Any]],
    live_citation_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[tuple[str, str]] = []  # (code, message)
    warnings: list[str] = []
    gaps: list[dict[str, Any]] = []
    verified_dois: set[str] = set()
    unverified_external_additions: list[dict[str, Any]] = []
    unverified_doi_completions: list[dict[str, Any]] = []
    doi_matches = 0
    title_checks = 0

    ledger = build_source_citation_ledger(src_refs, live_citation_fields)
    src_by_key = {str(r["key"]): r for r in src_refs}
    tgt_by_key = {str(r["key"]): r for r in tgt_refs}
    ledger_by_key = {str(item["key"]): item for item in ledger if not str(item["key"]).startswith("FIELD-")}

    # 1. Detect unverified external additions (target entries absent from immutable source ledger)
    for tr in tgt_refs:
        tk = str(tr["key"])
        if tk not in src_by_key:
            rec = {
                "key": tk,
                "target_line": tr["line"],
                "target_doi": tr.get("doi"),
                "target_text_preview": tr["raw_text"][:160],
                "reason": "Entry absent from source bibliography; external additions cannot enter verified_citations or verified_dois.",
            }
            unverified_external_additions.append(rec)
            errors.append((
                "unverified_external_addition",
                f"Ref [{tk}]: Unverified external bibliography addition not present in source document (forbidden from verified set).",
            ))

    # 2. Detect missing source entries dropped in target
    for sr in src_refs:
        sk = str(sr["key"])
        if sk not in tgt_by_key:
            errors.append((
                "bibliography_drift",
                f"Ref [{sk}]: Source bibliography entry missing from target bibliography.",
            ))

    # 3. Key-bound DOI, truncated-DOI completion, and publication year verification
    src_doi_to_key = {r["doi"]: str(r["key"]) for r in src_refs if r.get("doi")}
    for sr in src_refs:
        sk = str(sr["key"])
        tr = tgt_by_key.get(sk)
        if tr is None:
            continue
        # Populate display-only translated_title in ledger view without mutating raw_source_text
        if sk in ledger_by_key:
            ledger_by_key[sk]["translated_title"] = tr.get("title_excerpt", "")

        s_doi = sr.get("doi")
        t_doi = tr.get("doi")
        s_trunc = bool(sr.get("doi_truncated"))
        s_trunc_frag = sr.get("truncated_doi_fragment")

        if s_trunc:
            if t_doi:
                rec = {
                    "key": sk,
                    "source_truncated_doi": s_trunc_frag,
                    "target_completed_doi": t_doi,
                    "reason": "Source DOI was truncated; target completed DOI without source grounding (blocked from verified_dois).",
                }
                unverified_doi_completions.append(rec)
                errors.append((
                    "unverified_doi_completion",
                    f"Ref [{sk}]: Unauthorized DOI completion from truncated source fragment '{s_trunc_frag}' to '{t_doi}' (forbidden from verified_dois).",
                ))
            else:
                warnings.append(f"Ref [{sk}]: Source DOI is truncated ('{s_trunc_frag}') and honestly left unverified.")
                gaps.append({"code": "SOURCE_DOI_TRUNCATED", "location": f"Ref [{sk}]", "detail": f"Truncated source DOI '{s_trunc_frag}' not completed."})
        elif s_doi:
            if t_doi == s_doi:
                doi_matches += 1
                verified_dois.add(s_doi)
            else:
                swapped = src_doi_to_key.get(t_doi or "")
                errors.append((
                    "reference_doi_or_year_swap",
                    f"Ref [{sk}]: Cross-entry DOI swap (expected {s_doi}, got {t_doi} from Ref [{swapped}])"
                    if swapped else f"Ref [{sk}]: DOI mismatch/missing (expected {s_doi}, got {t_doi})",
                ))
        elif t_doi:
            # Source had NO DOI at all, yet target introduced an external DOI
            rec = {
                "key": sk,
                "source_truncated_doi": None,
                "target_completed_doi": t_doi,
                "reason": "Source entry contained no DOI; externally added target DOI cannot enter verified_dois.",
            }
            unverified_doi_completions.append(rec)
            errors.append((
                "unverified_doi_completion",
                f"Ref [{sk}]: Unauthorized external DOI '{t_doi}' added where source entry had no DOI (forbidden from verified_dois).",
            ))

        if sr.get("year") and tr.get("year") and sr["year"] != tr["year"]:
            errors.append((
                "reference_doi_or_year_swap",
                f"Ref [{sk}]: Publication year mismatch ({sr['year']} vs {tr['year']})",
            ))

    # 4. In-text numeric citation grounding (do NOT incentivize fabricating missing source entries)
    if src_refs or tgt_refs:
        valid_src_keys = {int(r["key"]) for r in src_refs if str(r["key"]).isdigit()}
        valid_tgt_keys = {int(r["key"]) for r in tgt_refs if str(r["key"]).isdigit()}
        for k in sorted(set(all_tgt_bracket_nums)):
            if k in valid_src_keys and k not in valid_tgt_keys:
                errors.append((
                    "bibliography_drift",
                    f"Dangling in-text numeric citation [{k}] exists in source References but missing in target References.",
                ))
            elif k not in valid_src_keys and k not in valid_tgt_keys:
                if k in set(all_src_bracket_nums):
                    warnings.append(f"In-text citation [{k}] was already absent from source References; preserved as source gap without fabrication.")
                    gaps.append({"code": "SOURCE_REF_ENTRY_MISSING", "location": f"Ref [{k}]", "detail": f"Citation [{k}] absent from source References."})
                else:
                    errors.append((
                        "citation_drift",
                        f"Dangling in-text numeric citation [{k}] has no matching entry in source or target References.",
                    ))

    # 5. Author-year bilingual script & reference grounding
    if tgt_refs and all_tgt_ay_cites:
        tgt_has_cjk = any(r["has_cjk"] for r in tgt_refs)
        tgt_has_latin = any(r["has_latin"] for r in tgt_refs)
        for ay in all_tgt_ay_cites:
            if ay["script"] == "cjk" and tgt_has_latin and not tgt_has_cjk:
                errors.append((
                    "bilingual_citation_script_mismatch",
                    f"Bilingual citation script mismatch: ({ay['raw']}) remains Chinese while target bibliography is English-only",
                ))
                continue
            if not any((ay["lead"].lower() in r["raw_text"].lower()) and (ay["year"] in r["raw_text"]) for r in tgt_refs):
                errors.append((
                    "bibliography_drift",
                    f"Dangling in-text author-year citation ({ay['raw']}) not found in target bibliography",
                ))

    # 6. Metadata-backed key-bound title/author/DOI swap audit (--references)
    swap_flagged_keys: set[str] = set()
    if ref_metadata and tgt_refs:
        all_titles = [
            (str(m.get("key", i + 1)), str(m.get("target_title", "")).strip())
            for i, m in enumerate(ref_metadata) if str(m.get("target_title", "")).strip()
        ]
        for i, meta in enumerate(ref_metadata):
            key = str(meta.get("key", i + 1))
            tr = tgt_by_key.get(key) or (tgt_refs[i] if i < len(tgt_refs) else None)
            if tr is None:
                errors.append(("bibliography_drift", f"Metadata Ref [{key}] missing from target bibliography"))
                continue
            rt_low = tr["raw_text"].lower()
            exp_doi = clean_doi(str(meta.get("doi", ""))) if str(meta.get("doi", "")).strip() else ""
            sr = src_by_key.get(key)
            if exp_doi:
                if sr and (sr.get("doi_truncated") or not sr.get("doi")):
                    # Metadata cannot bypass source grounding for truncated/missing source DOI
                    pass
                elif tr.get("doi") != exp_doi:
                    errors.append(("reference_doi_or_year_swap", f"Ref [{key}]: DOI does not match reference ledger ({exp_doi} vs {tr.get('doi')})"))
            exp_author = str(meta.get("target_author", "")).strip()
            if exp_author:
                lead = re.split(r"[\s,;]+", exp_author)[0].strip()
                if lead and lead.lower() not in rt_low:
                    errors.append(("reference_title_swap", f"Ref [{key}]: Expected target author '{exp_author}' missing in target entry"))
            exp_title = str(meta.get("target_title", "")).strip()
            if exp_title:
                title_checks += 1
                if key in ledger_by_key:
                    ledger_by_key[key]["translated_title"] = exp_title
                exp_norm = " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", exp_title.lower()))
                rt_norm = " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", rt_low))
                if exp_norm and exp_norm not in rt_norm:
                    swapped_k = next(
                        (ok for ok, ot in all_titles if ok != key and " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", ot.lower())) in rt_norm),
                        None,
                    )
                    swap_flagged_keys.add(key)
                    errors.append((
                        "reference_title_swap",
                        f"Ref [{key}]: Cross-entry translated title swap (contains title of Ref [{swapped_k}])"
                        if swapped_k else f"Ref [{key}]: Target entry missing expected translated title '{exp_title[:70]}'",
                    ))

    # 7. Automatic cross-entry translated title swap detection (even without --references)
    matched_keys = [str(sr["key"]) for sr in src_refs if str(sr["key"]) in tgt_by_key]
    for i in range(len(matched_keys)):
        for j in range(i + 1, len(matched_keys)):
            ki, kj = matched_keys[i], matched_keys[j]
            if ki in swap_flagged_keys and kj in swap_flagged_keys:
                continue
            sri, srj = src_by_key[ki], src_by_key[kj]
            tri, trj = tgt_by_key[ki], tgt_by_key[kj]

            # 7a. CJK source -> Latin target semantic domain-concept swap detector
            if sri["has_cjk"] and srj["has_cjk"] and tri["has_latin"] and trj["has_latin"]:
                diag = _concept_overlap_score(sri["raw_text"], tri["raw_text"]) + _concept_overlap_score(srj["raw_text"], trj["raw_text"])
                cross = _concept_overlap_score(sri["raw_text"], trj["raw_text"]) + _concept_overlap_score(srj["raw_text"], tri["raw_text"])
                if cross >= 2 and cross > diag and (diag == 0 or cross >= diag * 2):
                    swap_flagged_keys.update((ki, kj))
                    errors.append((
                        "reference_title_swap",
                        f"Ref [{ki}] & Ref [{kj}]: Detected cross-entry translated title swap across Chinese->English references (aligned_concept_score={diag} vs swapped_score={cross}).",
                    ))
                    continue

            # 7b. Shared distinctive ASCII/acronym token swap detector (works for EN->EN/ZH->EN/EN->ZH with acronyms)
            si_toks = _distinctive_ascii_tokens(sri.get("title_excerpt", ""))
            sj_toks = _distinctive_ascii_tokens(srj.get("title_excerpt", ""))
            ti_toks = _distinctive_ascii_tokens(tri.get("title_excerpt", ""))
            tj_toks = _distinctive_ascii_tokens(trj.get("title_excerpt", ""))
            if si_toks and sj_toks and ti_toks and tj_toks:
                diag_t = len(si_toks & ti_toks) + len(sj_toks & tj_toks)
                cross_t = len(si_toks & tj_toks) + len(sj_toks & ti_toks)
                if cross_t >= 2 and cross_t > diag_t and diag_t == 0:
                    swap_flagged_keys.update((ki, kj))
                    errors.append((
                        "reference_title_swap",
                        f"Ref [{ki}] & Ref [{kj}]: Detected cross-entry title swap via distinctive title tokens (diag={diag_t}, swapped={cross_t}).",
                    ))

    return {
        "source_entries": len(src_refs),
        "target_entries": len(tgt_refs),
        "doi_matches": doi_matches,
        "verified_dois": sorted(verified_dois),
        "unverified_external_additions": unverified_external_additions,
        "unverified_doi_completions": unverified_doi_completions,
        "source_citation_ledger": ledger,
        "title_metadata_checks": title_checks,
        "typed_errors": errors,
        "errors": [msg for _, msg in errors],
        "warnings": warnings,
        "gaps": gaps,
    }


def scan_translationese(tgt_paras: list[dict[str, Any]], max_items: int = 40) -> tuple[list[dict[str, Any]], int, dict[str, int], float]:
    findings: list[dict[str, Any]] = []
    full_body = "\n".join(p["text"] for p in tgt_paras)
    cjk_n = len(CJK_CHAR_RE.findall(full_body))
    word_n = len(re.findall(r"[A-Za-z]+", full_body))
    is_zh = cjk_n >= max(20, word_n // 2)
    rules = TRANSLATIONESE_ZH_RULES if is_zh else TRANSLATIONESE_EN_RULES
    cat_counts: Counter[str] = Counter()
    for p in tgt_paras:
        for rel_ln, line in enumerate(p["text"].splitlines()):
            for cat, rx, advice in rules:
                for m in rx.finditer(line):
                    cat_counts[cat] += 1
                    if len(findings) < max_items:
                        findings.append({"paragraph": p["index"], "line": p["start_line"] + rel_ln, "category": cat, "match": m.group(0), "suggestion": advice})
    total = sum(cat_counts.values())
    denom = (cjk_n / 1000.0) if is_zh else (max(1, word_n) / 1000.0)
    return findings, total, dict(cat_counts), round(total / denom, 2) if denom > 0 else 0.0


def _discover_primary_source(input_path: Path) -> Path:
    if input_path.is_file():
        return input_path
    skip_names = {"case.json", "task.md", "glossary.json", "reference_map.json", "reordered_notes.json"}
    prio_exts = {".pdf": 0, ".docx": 1, ".md": 2, ".txt": 3}
    cands = [p for p in sorted(input_path.rglob("*")) if p.is_file() and p.name.lower() not in skip_names and p.suffix.lower() in prio_exts]
    if not cands:
        raise FileNotFoundError(f"No supported source document found under {input_path}")
    cands.sort(key=lambda p: (prio_exts[p.suffix.lower()], -p.stat().st_size))
    return cands[0]


def extract_terms_and_refs_from_input(
    input_path: Path,
    work_dir: Path,
    source_format: str = "auto",
) -> dict[str, Any]:
    """Auto-extract source text, structured blocks, glossary draft, and immutable source_citation_ledger from /input."""
    src_file = _discover_primary_source(input_path)
    doc = extract_document(src_file, fmt=source_format)
    body_paras, ref_entries, has_ref_sec = split_paragraphs_and_refs(doc["text"], doc["page_offsets"])
    live_fields = doc.get("live_citation_fields", [])
    source_ledger = build_source_citation_ledger(ref_entries, live_fields)

    work_dir.mkdir(parents=True, exist_ok=True)
    extracted_txt_path = work_dir / "source_extracted.txt"
    extracted_txt_path.write_text(doc["text"] + "\n", encoding="utf-8")

    blocks: list[dict[str, Any]] = []
    full_body_text = "\n\n".join(p["text"] for p in body_paras)
    for p in body_paras:
        ch = extract_paragraph_channels(p["text"])
        blocks.append({
            "block_id": p["block_id"], "kind": p["kind"], "heading_level": p["heading_level"],
            "page": p["page"], "start_line": p["start_line"], "end_line": p["end_line"],
            "sentences": len(split_sentences_bilingual(p["text"])),
            "numbers_count": len(ch["numbers"]), "ranges_count": len(ch["ranges"]),
            "pvals_count": len(ch["pvals"]),
            "citations_count": len(ch["latex_cites"]) + len(ch["bracket_cites"]) + len(ch["author_year_cites"]),
            "displays_count": len(ch["displays"]), "equations_count": len(ch["equations"]),
            "preview": p["text"][:140],
        })

    terms: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9\- ]{4,60}?)\s*\(([A-Z][A-Za-z0-9\-]{1,10})\)", full_body_text):
        full_t, abbr = " ".join(m.group(1).split()), m.group(2)
        if len(full_t.split()) <= 7 and full_t.lower() not in seen_terms:
            seen_terms.add(full_t.lower())
            freq = len(re.findall(re.escape(full_t), full_body_text, re.I)) + len(re.findall(rf"\b{re.escape(abbr)}\b", full_body_text))
            terms.append({"source": full_t, "abbreviation": abbr, "target": "", "forbidden_variants": [], "frequency": freq, "origin": "auto_abbr_definition"})
    en_stop = {"the", "and", "for", "with", "from", "that", "this", "were", "was", "are", "have", "been", "into", "between", "during", "using", "study", "table", "figure", "results", "methods", "showed", "found"}
    en_ngrams = Counter(
        m.group(0).lower()
        for m in re.finditer(r"\b([A-Za-z][a-z]{3,}(?:\s+[a-z]{3,}){1,2})\b", full_body_text)
        if not any(w in en_stop for w in m.group(0).lower().split())
    )
    for phrase, cnt in en_ngrams.most_common(20):
        if cnt >= 2 and phrase not in seen_terms:
            seen_terms.add(phrase)
            terms.append({"source": phrase, "target": "", "forbidden_variants": [], "frequency": cnt, "origin": "auto_en_ngram"})
    zh_stop_sub = ("本发明", "所述", "包括如下", "其特征在于", "权利要求", "具体实施", "技术领域", "背景技术")
    zh_phrases = Counter(m.group(0) for m in re.finditer(r"[\u4e00-\u9fff]{3,7}", full_body_text))
    for zp, cnt in zh_phrases.most_common(25):
        if cnt >= 3 and not any(st in zp for st in zh_stop_sub) and zp not in seen_terms:
            seen_terms.add(zp)
            terms.append({"source": zp, "target": "", "forbidden_variants": [], "frequency": cnt, "origin": "auto_zh_compound"})

    glossary_draft = {
        "source_file": src_file.name,
        "extraction_engine": doc["engine"],
        "terms": terms[:35],
        "reordered_clause_notes": [],
    }
    ref_draft_items: list[dict[str, Any]] = []
    for r in ref_entries:
        ref_draft_items.append({
            "cite_key": f"REF-{r['key']}",
            "key": str(r["key"]),
            "source_page": r.get("page", 1),
            "source_line": r["line"],
            "raw_source_text": r["raw_text"],
            "doi": r.get("doi") or "",
            "doi_truncated": bool(r.get("doi_truncated")),
            "truncated_doi_fragment": r.get("truncated_doi_fragment") or "",
            "year": r.get("year") or "",
            "source_author": r.get("lead_author", ""),
            "target_author": "",
            "source_title": r.get("title_excerpt", ""),
            "target_title": "",
            "script": "cjk" if r["has_cjk"] else "latin",
            "metadata_flag": (
                "SOURCE_DOI_TRUNCATED" if r.get("doi_truncated")
                else ("UNVERIFIED_EXTERNAL_REF_METADATA" if (r["has_cjk"] and not r.get("doi")) else "SOURCE_GROUNDED")
            ),
        })
    ref_map_draft = {
        "source_file": src_file.name,
        "has_reference_section": has_ref_sec,
        "citation_fields_preserved": False,
        "live_citation_fields": live_fields,
        "source_citation_ledger": source_ledger,
        "references": ref_draft_items,
    }

    (work_dir / "source_blocks.json").write_text(json.dumps({"source_file": src_file.name, "extraction": {k: v for k, v in doc.items() if k != "text"}, "blocks": blocks}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (work_dir / "glossary_draft.json").write_text(json.dumps(glossary_draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (work_dir / "reference_map_draft.json").write_text(json.dumps(ref_map_draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": bool(doc["text"].strip()),
        "source_file": str(src_file),
        "format": doc["format"],
        "engine": doc["engine"],
        "pages_count": doc["pages_count"],
        "empty_pages": doc.get("empty_pages", []),
        "body_blocks_count": len(blocks),
        "references_extracted": len(ref_draft_items),
        "glossary_candidates": len(glossary_draft["terms"]),
        "live_citation_fields_count": len(live_fields),
        "citation_fields_preserved": False,
        "degradation_notice": doc.get("degradation_notice", ""),
        "outputs": {
            "source_extracted": str(extracted_txt_path),
            "source_blocks": str(work_dir / "source_blocks.json"),
            "glossary_draft": str(work_dir / "glossary_draft.json"),
            "reference_map_draft": str(work_dir / "reference_map_draft.json"),
        },
        "warning": doc.get("warning", ""),
    }


def audit_bilingual_pair(
    source_path: Path,
    target_path: Path,
    glossary_path: Path | None = None,
    references_path: Path | None = None,
    reordered_notes_path: Path | None = None,
    *,
    case_id: str = "",
    source_format: str = "auto",
    extracted_source_out: Path | None = None,
    para_mismatch_policy: str = "strict",
    max_style_warnings: int = 40,
) -> dict[str, Any]:
    src_doc = extract_document(source_path, fmt=source_format)
    tgt_doc = extract_document(target_path, fmt="auto")
    if extracted_source_out is not None:
        extracted_source_out.parent.mkdir(parents=True, exist_ok=True)
        extracted_source_out.write_text(src_doc["text"] + "\n", encoding="utf-8")

    src_paras, src_refs, _ = split_paragraphs_and_refs(src_doc["text"], src_doc["page_offsets"])
    tgt_paras, tgt_refs, _ = split_paragraphs_and_refs(tgt_doc["text"], tgt_doc["page_offsets"])

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
        for note in (rdata.get("reordered_clause_notes", rdata) if isinstance(rdata, dict) else rdata):
            if isinstance(note, dict) and note.get("paragraph") and note.get("reason"):
                reorder_map[int(note["paragraph"])] = str(note["reason"]).strip()
    if references_path and references_path.is_file():
        rfdata = json.loads(references_path.read_text(encoding="utf-8"))
        ref_metadata.extend(rfdata.get("references", []) if isinstance(rfdata, dict) else rfdata)

    errors: list[str] = []
    blocking_issues: list[dict[str, str]] = []
    warnings: list[str] = []
    gaps: list[dict[str, Any]] = []

    def add_block(code: str, loc: str, msg: str) -> None:
        errors.append(msg)
        blocking_issues.append({"code": code, "location": loc, "message": msg})

    if not src_doc["text"].strip():
        add_block("empty_source_extraction", "document", f"Extracted source text is empty ({src_doc.get('warning') or src_doc['engine']})")
    elif src_doc.get("warning"):
        warnings.append(src_doc["warning"])

    live_fields = src_doc.get("live_citation_fields", [])
    citation_fields_preserved = False
    if live_fields:
        gaps.append({
            "code": "LIVE_CITATION_FIELDS_DEGRADED_TO_MARKDOWN",
            "location": "document",
            "detail": src_doc.get("degradation_notice") or f"{len(live_fields)} OOXML live citation fields not preserved in Markdown output.",
        })
    if src_doc.get("empty_pages"):
        gaps.append({
            "code": "PDF_EMPTY_OR_SCANNED_PAGES",
            "location": "document",
            "detail": f"Physical PDF pages {src_doc['empty_pages']} had no extractable text layer.",
        })

    # Heading hierarchy 1:1 check (HEADING_HIERARCHY_DRIFT)
    heading_check = audit_heading_hierarchy(src_paras, tgt_paras)
    if not heading_check["aligned"]:
        for hmsg in heading_check["drifts"]:
            add_block("HEADING_HIERARCHY_DRIFT", "headings", hmsg)

    # Paragraph mismatch handling without silent zip truncation
    unaudited_paragraphs: list[dict[str, Any]] = []
    para_ratio = round(len(tgt_paras) / max(1, len(src_paras)), 4) if src_paras else 1.0
    if len(src_paras) != len(tgt_paras):
        msg = f"Body paragraph alignment mismatch: source={len(src_paras)} vs target={len(tgt_paras)} (ratio={para_ratio})"
        if para_mismatch_policy == "ratio" and 0.90 <= para_ratio <= 1.10:
            warnings.append(msg + "; review unaudited_paragraphs for merge/split alignment.")
        else:
            add_block("paragraph_count_mismatch", "document", msg)
        common = min(len(src_paras), len(tgt_paras))
        for sp in src_paras[common:]:
            ch = extract_paragraph_channels(sp["text"])
            unaudited_paragraphs.append({
                "side": "source", "paragraph": sp["index"], "page": sp["page"], "line": sp["start_line"],
                "status": "UNAUDITED", "numbers": [x["raw"] for x in ch["numbers"]], "excerpt": sp["text"][:100],
            })
        for tp in tgt_paras[common:]:
            ch = extract_paragraph_channels(tp["text"])
            unaudited_paragraphs.append({
                "side": "target", "paragraph": tp["index"], "page": tp["page"], "line": tp["start_line"],
                "status": "UNAUDITED", "numbers": [x["raw"] for x in ch["numbers"]], "excerpt": tp["text"][:100],
            })

    para_checks: list[dict[str, Any]] = []
    reordered_verified: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    change_log: list[dict[str, Any]] = []
    aligned_pairs: list[dict[str, Any]] = []
    all_src_bracket_nums: list[int] = []
    all_tgt_bracket_nums: list[int] = []
    all_tgt_ay_cites: list[dict[str, Any]] = []
    total_verified_nums = total_verified_cites = total_verified_displays = total_verified_equations = tables_verified = total_ranges_verified = 0
    all_verified_dois: set[str] = set()
    inline_doi_completions: list[dict[str, Any]] = []
    src_ref_keys = {str(r["key"]) for r in src_refs}

    for sp_obj, tp_obj in zip(src_paras, tgt_paras):
        idx = sp_obj["index"]
        sp, tp = sp_obj["text"], tp_obj["text"]
        s_ch, t_ch = extract_paragraph_channels(sp), extract_paragraph_channels(tp)
        all_src_bracket_nums.extend(s_ch["bracket_cite_nums"])
        all_tgt_bracket_nums.extend(t_ch["bracket_cite_nums"])
        all_tgt_ay_cites.extend(t_ch["author_year_cites"])

        # 1. Table cell-level structure check or prose numeric/range/order check
        table_ok = True
        if sp_obj.get("table_rows") and tp_obj.get("table_rows"):
            t_errs = compare_markdown_tables(sp_obj["table_rows"], tp_obj["table_rows"], idx)
            if t_errs:
                table_ok = False
                for te in t_errs:
                    add_block("table_alignment_drift", f"P{idx}", te)
                num_status = "table_drift"
            else:
                tables_verified += 1
                total_verified_nums += len(t_ch["numbers"])
                total_ranges_verified += len(t_ch["ranges"])
                num_status = "table_exact"
        else:
            note_reason = "; ".join(tp_obj.get("inline_reorder_notes") or []) or reorder_map.get(idx)
            num_status, num_errs = compare_numbers_with_order(s_ch["numbers"], t_ch["numbers"], note_reason)
            if num_errs:
                code = "numeric_order_swap_unattested" if num_status == "order_swap_unattested" else "numeric_or_unit_drift"
                for ne in num_errs:
                    add_block(code, f"P{idx}", f"P{idx}: {ne}")
            else:
                total_verified_nums += len(t_ch["numbers"])
                total_ranges_verified += len(t_ch["ranges"])
                if num_status == "verified_reordered":
                    reordered_verified.append({
                        "paragraph": idx, "reason": note_reason,
                        "source_order": [x["raw"] for x in s_ch["numbers"]],
                        "target_order": [x["raw"] for x in t_ch["numbers"]],
                    })
                    change_log.append({"type": "reordered_clause_attestation", "location": f"P{idx}", "detail": note_reason, "requires_human_confirmation": False})

        # 2. p-values (Decimal exact)
        pval_ok = True
        if len(s_ch["pvals"]) != len(t_ch["pvals"]):
            pval_ok = False
            add_block("pvalue_drift", f"P{idx}", f"P{idx}: Statistical p-value count mismatch: {[x[2] for x in s_ch['pvals']]} vs {[x[2] for x in t_ch['pvals']]}")
        else:
            for (so, sv, sr), (to, tv, tr) in zip(s_ch["pvals"], t_ch["pvals"]):
                if so != to or sv != tv:
                    pval_ok = False
                    add_block("pvalue_drift", f"P{idx}", f"P{idx}: Statistical p-value drift: {sr!r} != {tr!r}")

        # 3. Citations
        cites_ok = True
        if sorted(s_ch["latex_cites"]) != sorted(t_ch["latex_cites"]):
            cites_ok = False
            add_block("citation_drift", f"P{idx}", f"P{idx}: LaTeX citation drift ({s_ch['latex_cites']} != {t_ch['latex_cites']})")
        if sorted(s_ch["bracket_cites"]) != sorted(t_ch["bracket_cites"]):
            cites_ok = False
            add_block("citation_drift", f"P{idx}", f"P{idx}: Numeric citation marker drift ({s_ch['bracket_cites']} != {t_ch['bracket_cites']})")
        if sorted(c["year"] for c in s_ch["author_year_cites"]) != sorted(c["year"] for c in t_ch["author_year_cites"]):
            cites_ok = False
            add_block("citation_drift", f"P{idx}", f"P{idx}: Author-year citation mismatch: {[c['raw'] for c in s_ch['author_year_cites']]} vs {[c['raw'] for c in t_ch['author_year_cites']]}")
        if cites_ok:
            # Count only citations grounded in source_ref_keys when a reference list exists
            grounded_brackets = (
                [n for n in t_ch["bracket_cite_nums"] if str(n) in src_ref_keys]
                if src_ref_keys else t_ch["bracket_cite_nums"]
            )
            total_verified_cites += len(t_ch["latex_cites"]) + len(grounded_brackets) + len(t_ch["author_year_cites"])

        # 4-6. Displays, Equations, Inline DOIs (never allow unauthorized completion into verified_dois)
        displays_ok = sorted(s_ch["displays"]) == sorted(t_ch["displays"])
        if not displays_ok:
            add_block("display_numbering_drift", f"P{idx}", f"P{idx}: Display numbering drift ({s_ch['displays']} vs {t_ch['displays']})")
        else:
            total_verified_displays += len(t_ch["displays"])

        eq_ok = s_ch["equations"] == t_ch["equations"]
        if not eq_ok:
            add_block("equation_drift", f"P{idx}", f"P{idx}: Equation block drift ({s_ch['equations']} vs {t_ch['equations']})")
        else:
            total_verified_equations += len(t_ch["equations"])

        if sorted(s_ch["dois"]) != sorted(t_ch["dois"]):
            extra_t_dois = [d for d in t_ch["dois"] if d not in s_ch["dois"]]
            if extra_t_dois and (s_ch["truncated_dois"] or not s_ch["dois"]):
                for ed in extra_t_dois:
                    inline_doi_completions.append({"location": f"P{idx}", "source_truncated_dois": s_ch["truncated_dois"], "target_completed_doi": ed})
                add_block("unverified_doi_completion", f"P{idx}", f"P{idx}: Unauthorized inline DOI completion {extra_t_dois} not present in source.")
            else:
                add_block("doi_drift", f"P{idx}", f"P{idx}: Inline DOI drift ({s_ch['dois']} vs {t_ch['dois']})")
        else:
            all_verified_dois.update(s_ch["dois"])

        # 7. Sentence-level + paragraph-level negation polarity check
        s_sents = split_sentences_bilingual(sp)
        t_sents = split_sentences_bilingual(tp)
        s_sent_n, t_sent_n = max(1, len(s_sents)), max(1, len(t_sents))

        s_neg = len(NEG_EN_RE.findall(sp)) + len(NEG_ZH_RE.findall(sp))
        t_neg = len(NEG_EN_RE.findall(tp)) + len(NEG_ZH_RE.findall(tp))
        neg_ok = (s_neg > 0) == (t_neg > 0)
        sent_neg_mismatches: list[int] = []
        if neg_ok and len(s_sents) == len(t_sents) and len(s_sents) >= 2:
            for si, (ss, ts) in enumerate(zip(s_sents, t_sents), 1):
                sn = len(NEG_EN_RE.findall(ss)) + len(NEG_ZH_RE.findall(ss))
                tn = len(NEG_EN_RE.findall(ts)) + len(NEG_ZH_RE.findall(ts))
                if (sn > 0) != (tn > 0):
                    sent_neg_mismatches.append(si)
        if not neg_ok:
            add_block("negation_polarity_shift", f"P{idx}", f"P{idx}: Negation polarity shift (source_neg={s_neg}, target_neg={t_neg})")
        elif sent_neg_mismatches:
            neg_ok = False
            add_block("negation_scope_shift", f"P{idx}", f"P{idx}: Sentence-level negation scope shift on sentence(s) {sent_neg_mismatches}")
        elif abs(s_neg - t_neg) >= 2:
            warnings.append(f"P{idx}: Negation marker count differs substantially (source_neg={s_neg}, target_neg={t_neg})")

        # 8. Modal hedges, causal escalation, and truncation check
        s_hedge = len(HEDGE_EN_RE.findall(sp)) + len(HEDGE_ZH_RE.findall(sp))
        t_hedge = len(HEDGE_EN_RE.findall(tp)) + len(HEDGE_ZH_RE.findall(tp))
        s_causal = len(CAUSAL_STRONG_EN_RE.findall(sp)) + len(CAUSAL_STRONG_ZH_RE.findall(sp))
        t_causal = len(CAUSAL_STRONG_EN_RE.findall(tp)) + len(CAUSAL_STRONG_ZH_RE.findall(tp))
        causal_ok = True
        if s_hedge > 0 and s_causal == 0 and t_causal > 0:
            causal_ok = False
            add_block("causal_overclaim_escalation", f"P{idx}", f"P{idx}: Causal overclaim escalation (hedged source escalated to {t_causal} definitive causal verb(s))")
        elif s_hedge > 0 and t_hedge == 0:
            warnings.append(f"P{idx}: Source contains {s_hedge} epistemic hedge/limiter(s) omitted in target")

        truncation_suspected = False
        if s_sent_n >= 3 and t_sent_n <= max(1, int(s_sent_n * 0.45)):
            truncation_suspected = True
            add_block("translation_truncation", f"P{idx}", f"P{idx}: Suspected truncation/omission (source={s_sent_n} sentences vs target={t_sent_n})")
        elif len(sp.strip()) >= 180 and len(tp.strip()) < max(20, int(len(sp.strip()) * 0.14)):
            truncation_suspected = True
            add_block("translation_truncation", f"P{idx}", f"P{idx}: Severe length collapse ({len(tp.strip())} vs {len(sp.strip())} source chars)")

        para_checks.append({
            "paragraph": idx, "kind": sp_obj["kind"], "heading_level": sp_obj["heading_level"],
            "source_page": sp_obj["page"], "source_line": sp_obj["start_line"],
            "numbers_status": num_status, "numbers_ok": num_status in ("order_exact", "verified_reordered", "table_exact"),
            "ranges_verified": len(t_ch["ranges"]),
            "table_ok": table_ok, "pvals_ok": pval_ok, "citations_ok": cites_ok, "displays_ok": displays_ok,
            "equations_ok": eq_ok, "negation_parity_ok": neg_ok, "causal_escalation_ok": causal_ok,
            "source_sentences": s_sent_n, "target_sentences": t_sent_n, "truncation_ok": not truncation_suspected,
            "hedges_source": s_hedge, "hedges_target": t_hedge,
            "source_numbers": [x["raw"] for x in s_ch["numbers"]], "target_numbers": [x["raw"] for x in t_ch["numbers"]],
        })
        evidence_map.append({
            "paragraph": idx, "source_page": sp_obj["page"], "source_line": sp_obj["start_line"],
            "numbers_status": num_status, "table_ok": table_ok, "citations_ok": cites_ok,
            "displays_ok": displays_ok, "equations_ok": eq_ok, "negation_ok": neg_ok, "causal_escalation_ok": causal_ok,
        })
        aligned_pairs.append({
            "index": idx, "kind": sp_obj["kind"], "page": sp_obj["page"], "line": sp_obj["start_line"],
            "source": sp, "target": tp, "source_sentences": s_sents, "target_sentences": t_sents,
        })

    # 9. Immutable source_citation_ledger bibliography audit
    bib_report = audit_bibliography(
        src_refs, tgt_refs, all_src_bracket_nums, all_tgt_bracket_nums,
        all_tgt_ay_cites, ref_metadata, live_fields,
    )
    for bcode, berr in bib_report["typed_errors"]:
        add_block(bcode, "references", berr)
    warnings.extend(bib_report["warnings"])
    gaps.extend(bib_report["gaps"])
    # ONLY verified source-grounded DOIs enter all_verified_dois
    all_verified_dois.update(bib_report["verified_dois"])

    all_unverified_external = list(bib_report["unverified_external_additions"])
    all_unverified_doi_comp = list(bib_report["unverified_doi_completions"]) + inline_doi_completions

    # 10. Glossary lock
    glossary_hits: list[dict[str, Any]] = []
    glossary_matched: list[str] = []
    glossary_missing: list[str] = []
    if glossary_entries:
        src_low = "\n".join(p["text"] for p in src_paras).lower()
        tgt_low = "\n".join(p["text"] for p in tgt_paras).lower()
        for item in glossary_entries:
            if not isinstance(item, dict):
                continue
            src_term, req_tgt = str(item.get("source", "")).strip(), str(item.get("target", "")).strip()
            if not src_term or not req_tgt:
                continue
            if src_term.lower() in src_low:
                allowed = [req_tgt] + [str(a).strip() for a in item.get("allowed_variants", []) if str(a).strip()]
                forbidden = [str(f).strip() for f in item.get("forbidden_variants", []) if str(f).strip()]
                present = any(c.lower() in tgt_low for c in allowed)
                bad_used = [
                    fv for fv in forbidden
                    if (re.search(rf"(?<![A-Za-z0-9_]){re.escape(fv)}(?![A-Za-z0-9_])", tgt_low, re.I) if re.search(r"[A-Za-z]", fv) else (fv.lower() in tgt_low))
                ]
                if not present:
                    add_block("glossary_conflict", "full_document", f"Glossary term {src_term!r} missing canonical translation {req_tgt!r}")
                if bad_used:
                    add_block("glossary_conflict", "full_document", f"Glossary term {src_term!r} used forbidden variant(s): {bad_used}")
                ok_term = present and not bad_used
                (glossary_matched if ok_term else glossary_missing).append(src_term)
                glossary_hits.append({"source": src_term, "canonical_target": req_tgt, "ok": ok_term, "forbidden_hits": bad_used})
                change_log.append({"type": "glossary_lock", "location": "full_document", "detail": f"{src_term} -> {req_tgt}", "requires_human_confirmation": False})

    glossary_cov = round(len(glossary_matched) / len(glossary_hits), 4) if glossary_hits else (1.0 if glossary_entries else None)
    total_src_sents = sum(int(c["source_sentences"]) for c in para_checks)
    total_tgt_sents = sum(int(c["target_sentences"]) for c in para_checks)
    sent_ratio = round(total_tgt_sents / max(1, total_src_sents), 4) if total_src_sents else 1.0

    # 11. Bounded style scan
    style_warnings, style_total, style_summary, style_density = scan_translationese(tgt_paras, max_items=max_style_warnings)

    bib_public = {k: v for k, v in bib_report.items() if k != "typed_errors"}

    contract_patch: dict[str, Any] = {
        "case_id": case_id or "UNSET_CASE_ID",
        "deliverable": {"translated_manuscript": target_path.name, "bilingual_aligned": "bilingual_aligned.md", "translation_audit": "translation_audit.json"},
        "preserved_facts": {
            "source_file": source_path.name,
            "source_extraction_engine": src_doc["engine"],
            "source_pages": src_doc["pages_count"],
            "paragraphs_aligned": min(len(src_paras), len(tgt_paras)),
            "paragraph_alignment_ratio": para_ratio,
            "sentence_alignment_ratio": sent_ratio,
            "heading_hierarchy_aligned": heading_check["aligned"],
            "tables_verified": tables_verified,
            "verified_numeric_tokens": total_verified_nums,
            "verified_numeric_ranges": total_ranges_verified,
            "verified_citations": total_verified_cites,
            "verified_displays": total_verified_displays,
            "verified_equations": total_verified_equations,
            "verified_dois": sorted(all_verified_dois),
            "unverified_external_additions": all_unverified_external,
            "unverified_doi_completions": all_unverified_doi_comp,
            "live_citation_fields_count": len(live_fields),
            "citation_fields_preserved": citation_fields_preserved,
            "glossary_coverage_rate": glossary_cov,
        },
        "evidence_map": evidence_map,
        "change_log": change_log,
        "metrics": {
            "source_paragraphs": len(src_paras), "target_paragraphs": len(tgt_paras),
            "paragraph_alignment_ratio": para_ratio, "source_sentences": total_src_sents,
            "target_sentences": total_tgt_sents, "sentence_alignment_ratio": sent_ratio,
            "tables_verified": tables_verified, "verified_numeric_tokens": total_verified_nums,
            "verified_numeric_ranges": total_ranges_verified,
            "reordered_clauses_verified": len(reordered_verified), "bibliography_entries_checked": bib_report["target_entries"],
            "unverified_external_additions_count": len(all_unverified_external),
            "unverified_doi_completions_count": len(all_unverified_doi_comp),
            "glossary_terms_checked": len(glossary_hits), "glossary_coverage_rate": glossary_cov,
            "hard_errors_count": len(errors), "style_warnings_count": style_total,
            "translationese_per_1k_chars": style_density,
        },
        "metric_provenance": {
            "source_paragraphs": source_path.name, "target_paragraphs": target_path.name,
            "paragraph_alignment_ratio": "translation_audit.json:paragraph_alignment_ratio",
            "source_sentences": "translation_audit.json:source_sentences",
            "target_sentences": "translation_audit.json:target_sentences",
            "sentence_alignment_ratio": "translation_audit.json:sentence_alignment_ratio",
            "tables_verified": "translation_audit.json:tables_verified",
            "verified_numeric_tokens": "translation_audit.json:paragraph_checks",
            "verified_numeric_ranges": "translation_audit.json:paragraph_checks",
            "reordered_clauses_verified": "translation_audit.json:reordered_clauses_verified",
            "bibliography_entries_checked": "translation_audit.json:bibliography_check",
            "unverified_external_additions_count": "translation_audit.json:unverified_external_additions",
            "unverified_doi_completions_count": "translation_audit.json:unverified_doi_completions",
            "glossary_terms_checked": "translation_audit.json:glossary_checks",
            "glossary_coverage_rate": "translation_audit.json:glossary_coverage_rate",
            "hard_errors_count": "translation_audit.json:errors",
            "style_warnings_count": "translation_audit.json:style_warnings_total",
            "translationese_per_1k_chars": "translation_audit.json:translationese_per_1k_chars",
        },
        "gaps": gaps,
    }

    return {
        "passed": len(errors) == 0,
        "source_extraction": {k: v for k, v in src_doc.items() if k != "text"},
        "citation_fields_preserved": citation_fields_preserved,
        "live_citation_fields": live_fields,
        "source_citation_ledger": bib_report["source_citation_ledger"],
        "verified_dois": sorted(all_verified_dois),
        "unverified_external_additions": all_unverified_external,
        "unverified_doi_completions": all_unverified_doi_comp,
        "heading_hierarchy_check": heading_check,
        "source_paragraphs": len(src_paras), "target_paragraphs": len(tgt_paras),
        "paragraph_alignment_ratio": para_ratio, "unaudited_paragraphs": unaudited_paragraphs,
        "source_sentences": total_src_sents, "target_sentences": total_tgt_sents,
        "sentence_alignment_ratio": sent_ratio, "tables_verified": tables_verified,
        "verified_numeric_ranges": total_ranges_verified,
        "glossary_coverage_rate": glossary_cov, "glossary_matched_terms": glossary_matched,
        "glossary_missing_terms": glossary_missing, "errors": errors, "blocking_issues": blocking_issues,
        "warnings": warnings, "gaps": gaps, "reordered_clauses_verified": reordered_verified,
        "paragraph_checks": para_checks, "bibliography_check": bib_public,
        "glossary_checks": glossary_hits, "style_warnings": style_warnings,
        "style_warnings_total": style_total, "style_warning_summary": style_summary,
        "translationese_per_1k_chars": style_density, "contract_patch": contract_patch,
        "_aligned_pairs": aligned_pairs, "_src_refs": src_refs, "_tgt_refs": tgt_refs,
    }


def render_bilingual_aligned_md(rep: dict[str, Any]) -> str:
    """Export editable sentence-and-paragraph bilingual alignment Markdown."""
    status = "PASS" if rep["passed"] else "FAIL"
    ext = rep.get("source_extraction", {})
    live_n = len(rep.get("live_citation_fields") or [])
    lines: list[str] = [
        f"# Bilingual Aligned Manuscript ({status})",
        "",
        f"- **Source Format / Engine**: `{ext.get('format', 'text')}` via `{ext.get('engine', 'n/a')}` ({ext.get('pages_count', 1)} page(s))",
        f"- **Paragraphs / Sentences Aligned**: {rep['source_paragraphs']}->{rep['target_paragraphs']} paras ({rep['paragraph_alignment_ratio']}) | {rep['source_sentences']}->{rep['target_sentences']} sents ({rep['sentence_alignment_ratio']}) | Tables verified: {rep.get('tables_verified', 0)} | Ranges verified: {rep.get('verified_numeric_ranges', 0)}",
        f"- **Heading Hierarchy 1:1**: `{'PASS' if rep.get('heading_hierarchy_check', {}).get('aligned') else 'DRIFT'}` ({rep.get('heading_hierarchy_check', {}).get('source_levels', [])} vs {rep.get('heading_hierarchy_check', {}).get('target_levels', [])})",
        f"- **Citation Field Preservation**: `citation_fields_preserved={str(rep.get('citation_fields_preserved', False)).lower()}` (OOXML live citation fields detected: {live_n})",
        f"- **Hard Errors**: {len(rep['errors'])} | **Unverified External Additions**: {len(rep.get('unverified_external_additions', []))} | **Unverified DOI Completions**: {len(rep.get('unverified_doi_completions', []))} | **Style Warnings**: {rep.get('style_warnings_total', len(rep['style_warnings']))} ({rep['translationese_per_1k_chars']}/1k chars)",
        "",
    ]
    if live_n > 0:
        lines.extend([
            f"> **Honest Degradation Notice**: Source DOCX contained {live_n} live citation field(s). Exporting to Markdown plain text sets `citation_fields_preserved: false`; re-link live fields in the original DOCX/Zotero library before journal submission.",
            "",
        ])

    checks_by_p = {c["paragraph"]: c for c in rep["paragraph_checks"]}
    style_by_p: dict[int, list[dict[str, Any]]] = {}
    for sw in rep["style_warnings"]:
        style_by_p.setdefault(sw["paragraph"], []).append(sw)

    for pair in rep.get("_aligned_pairs", []):
        idx = pair["index"]
        chk = checks_by_p.get(idx, {})
        ok_all = all(chk.get(k, True) for k in ("numbers_ok", "table_ok", "pvals_ok", "citations_ok", "displays_ok", "equations_ok", "negation_parity_ok", "causal_escalation_ok", "truncation_ok"))
        badge = "PASS" if ok_all else "CHECK_FAILED"
        lines.append(f"## Paragraph {idx} [p.{pair['page']} L{pair['line']} | {badge} | numbers: `{chk.get('numbers_status', 'n/a')}`]")
        lines.append("")
        if pair["kind"] == "table":
            lines.extend(["**Source Table:**", pair["source"], "", "**Target Table:**", pair["target"], ""])
        else:
            s_sents, t_sents = pair.get("source_sentences", []), pair.get("target_sentences", [])
            n_rows = max(len(s_sents), len(t_sents))
            lines.extend(["| Sent | Source | Target | Status |", "| --- | --- | --- | --- |"])
            for si in range(n_rows):
                ss = (s_sents[si] if si < len(s_sents) else "*(merged/omitted)*").replace("|", "\\|").replace("\n", " ")
                ts = (t_sents[si] if si < len(t_sents) else "*(merged/omitted)*").replace("|", "\\|").replace("\n", " ")
                lines.append(f"| S{si + 1} | {ss} | {ts} | {badge} |")
            lines.append("")
        if idx in style_by_p:
            lines.append("**Style Notes:**")
            for sw in style_by_p[idx]:
                lines.append(f"- L{sw['line']} [{sw['category']}]: `{sw['match']}` -> {sw['suggestion']}")
            lines.append("")

    if rep.get("unaudited_paragraphs"):
        lines.extend(["## Unaudited Excess Paragraphs (Realignment Required)", ""])
        for u in rep["unaudited_paragraphs"]:
            lines.append(f"- [{u['side'].upper()} P{u['paragraph']} p.{u['page']} L{u['line']}] `UNAUDITED`: {u['excerpt']}")
        lines.append("")

    if rep.get("_src_refs") or rep.get("_tgt_refs"):
        lines.extend(["## Bilingual References Alignment (Immutable Source Ledger)", ""])
        src_by_k = {str(r["key"]): r for r in rep.get("_src_refs", [])}
        tgt_by_k = {str(r["key"]): r for r in rep.get("_tgt_refs", [])}
        all_keys = list(dict.fromkeys([str(r["key"]) for r in rep.get("_src_refs", [])] + [str(r["key"]) for r in rep.get("_tgt_refs", [])]))
        for k in all_keys:
            sr = src_by_k.get(k)
            tr = tgt_by_k.get(k)
            status_tag = "SOURCE_GROUNDED" if (sr and tr) else ("UNVERIFIED_EXTERNAL_ADDITION" if tr and not sr else "MISSING_IN_TARGET")
            src_doi = (sr.get("doi") or (f"TRUNCATED:{sr.get('truncated_doi_fragment')}" if sr.get("doi_truncated") else "none")) if sr else "absent"
            lines.append(f"- **[{k}]** (`{status_tag}` | Source DOI: `{src_doi}` | Year: `{(sr or tr or {}).get('year') or 'n/a'}`)")
            lines.append(f"  - Source: {sr['raw_text'] if sr else '*(absent in source)*'}")
            lines.append(f"  - Target: {tr['raw_text'] if tr else '*(missing in target)*'}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bilingual academic alignment guard and /input auto-extractor.")
    parser.add_argument("--extract-terms-and-refs", action="store_true", help="Auto-extract text, blocks, glossary draft, and immutable source citation ledger from --from-input or --source.")
    parser.add_argument("--from-input", default="", help="Input file or /input directory for auto-extraction mode.")
    parser.add_argument("--work-dir", default="/app/work", help="Output work dir for --extract-terms-and-refs mode.")
    parser.add_argument("--source", default="", help="Source document path (.pdf, .docx, .md, .txt).")
    parser.add_argument("--source-format", choices=("auto", "text", "pdf", "docx"), default="auto", help="Source document format (default: auto).")
    parser.add_argument("--extracted-source-out", default="", help="Optional path to persist extracted source text.")
    parser.add_argument("--target", default="", help="Translated target document path.")
    parser.add_argument("--glossary", default="", help="Optional glossary JSON lock file.")
    parser.add_argument("--references", default="", help="Optional bilingual reference metadata JSON file.")
    parser.add_argument("--reordered-notes", default="", help="Optional JSON file of attested clause reorderings.")
    parser.add_argument("--case-id", default="", help="Optional case_id embedded in contract patch.")
    parser.add_argument("--para-mismatch-policy", choices=("strict", "ratio"), default="strict", help="Paragraph count mismatch policy (default: strict).")
    parser.add_argument("--max-style-warnings", type=int, default=40, help="Maximum individual style warnings persisted in JSON (default: 40).")
    parser.add_argument("--out-json", default="", help="Output alignment audit JSON path.")
    parser.add_argument("--out-aligned", default="", help="Optional editable bilingual_aligned.md output path.")
    parser.add_argument("--out-contract-patch", default="", help="Optional result.json contract patch JSON path.")
    args = parser.parse_args(argv)

    if args.extract_terms_and_refs:
        in_target = args.from_input or args.source
        if not in_target:
            parser.error("--from-input or --source is required with --extract-terms-and-refs")
        ext_summary = extract_terms_and_refs_from_input(Path(in_target), Path(args.work_dir), source_format=args.source_format)
        if args.out_json:
            op = Path(args.out_json)
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(ext_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(ext_summary, ensure_ascii=False, indent=2))
        return 0 if ext_summary["ok"] else 1

    if not args.source or not args.target or not args.out_json:
        parser.error("--source, --target, and --out-json are required in audit mode")

    rep = audit_bilingual_pair(
        Path(args.source), Path(args.target),
        Path(args.glossary) if args.glossary else None,
        Path(args.references) if args.references else None,
        Path(args.reordered_notes) if args.reordered_notes else None,
        case_id=args.case_id, source_format=args.source_format,
        extracted_source_out=Path(args.extracted_source_out) if args.extracted_source_out else None,
        para_mismatch_policy=args.para_mismatch_policy, max_style_warnings=args.max_style_warnings,
    )
    if args.out_aligned:
        ap = Path(args.out_aligned)
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(render_bilingual_aligned_md(rep), encoding="utf-8")
    if args.out_contract_patch:
        cp = Path(args.out_contract_patch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(rep["contract_patch"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    public_rep = {k: v for k, v in rep.items() if not k.startswith("_")}
    op = Path(args.out_json)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(public_rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": public_rep["passed"], "source_engine": public_rep["source_extraction"]["engine"],
        "source_paragraphs": public_rep["source_paragraphs"], "target_paragraphs": public_rep["target_paragraphs"],
        "heading_hierarchy_aligned": public_rep["heading_hierarchy_check"]["aligned"],
        "tables_verified": public_rep["tables_verified"], "verified_numeric_ranges": public_rep["verified_numeric_ranges"],
        "reordered_clauses_verified": len(public_rep["reordered_clauses_verified"]),
        "bibliography_entries": public_rep["bibliography_check"]["target_entries"],
        "verified_dois_count": len(public_rep["verified_dois"]),
        "unverified_external_additions_count": len(public_rep["unverified_external_additions"]),
        "unverified_doi_completions_count": len(public_rep["unverified_doi_completions"]),
        "citation_fields_preserved": public_rep["citation_fields_preserved"],
        "errors": public_rep["errors"], "blocking_issues_count": len(public_rep["blocking_issues"]),
        "warnings": public_rep["warnings"], "style_warnings_total": public_rep["style_warnings_total"],
        "translationese_per_1k_chars": public_rep["translationese_per_1k_chars"],
    }, ensure_ascii=False, indent=2))
    return 0 if public_rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
