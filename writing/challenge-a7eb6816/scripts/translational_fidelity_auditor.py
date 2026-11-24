#!/usr/bin/env python3
"""Translational science writing fidelity, native .pdf/.docx/.md reader, automated
--extract-source-brief (with trace skeleton & glossary draft export), figure/table/citation
preservation, per-value p-value & unit-context verification, cross-disciplinary 4-tier
evidence ladder with proximity hedging, and calibrated natural prose auditor.

Zero mandatory external dependencies (Python 3.12 standard library; optional pdftotext/
pdfplumber/pypdf acceleration with stdlib zlib FlateDecode and OOXML fallbacks).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import Any

AUDIENCE_PROFILES: dict[str, dict[str, Any]] = {
    "public_science": {
        "label": "Public Science / Lay Communication",
        "require_acronym_expansion": True,
        "max_avg_zh_sent_len": 38.0,
        "max_avg_en_sent_words": 24.0,
        "default_max_zh_chars": 2500,
        "default_max_en_words": 1200,
        "required_sections": (
            ("core_finding", r"核心发现|研究发现|主要结果|发现了什么|key\s+finding|what\s+was\s+found|summary"),
            ("limitations_scope", r"局限|适用边界|注意|尚待|未验证|边界|limitation|caveat|what\s+this\s+does\s+not|next\s+step"),
        ),
        "require_model_boundary": True,
        "default_boundary_policy": "primary",
        "require_figure_refs_strict": False,
        "require_baseline_if_present": True,
        "require_sample_size_if_present": False,
        "prose_strict": True,
    },
    "industry_pitch": {
        "label": "Industry / R&D Translation Pitch",
        "require_acronym_expansion": True,
        "max_avg_zh_sent_len": 42.0,
        "max_avg_en_sent_words": 26.0,
        "default_max_zh_chars": 3000,
        "default_max_en_words": 1500,
        "required_sections": (
            ("value_or_application", r"应用场景|转化价值|产业|潜在应用|技术优势|application|value\s+proposition|translation"),
            ("readiness_boundary", r"成熟度|局限|风险|临床前|实验室|尚待验证|边界|TRL|readiness|risk|limitation|boundary"),
        ),
        "require_model_boundary": True,
        "default_boundary_policy": "primary",
        "require_figure_refs_strict": False,
        "require_baseline_if_present": True,
        "require_sample_size_if_present": True,
        "prose_strict": True,
    },
    "policy_memo": {
        "label": "Policy / Governance Brief",
        "require_acronym_expansion": True,
        "max_avg_zh_sent_len": 42.0,
        "max_avg_en_sent_words": 26.0,
        "default_max_zh_chars": 2500,
        "default_max_en_words": 1200,
        "required_sections": (
            ("evidence_summary", r"证据|核心结论|主要发现|evidence|key\s+takeaway|executive\s+summary"),
            ("recommendation_or_boundary", r"建议|政策|边界|不确定性|局限|recommendation|policy\s+implication|uncertainty|limitation"),
        ),
        "require_model_boundary": True,
        "default_boundary_policy": "all",
        "require_figure_refs_strict": True,
        "require_baseline_if_present": True,
        "require_sample_size_if_present": True,
        "prose_strict": True,
    },
    "grant_brief": {
        "label": "Grant / Funding Proposal Brief",
        "require_acronym_expansion": True,
        "max_avg_zh_sent_len": 48.0,
        "max_avg_en_sent_words": 30.0,
        "default_max_zh_chars": 3500,
        "default_max_en_words": 1800,
        "required_sections": (
            ("problem_and_evidence", r"背景|问题|前期|研究发现|background|rationale|preliminary|evidence"),
            ("next_plan_and_boundary", r"下一步|拟开展|局限|风险|边界|next\s+step|proposed|limitation|risk"),
        ),
        "require_model_boundary": True,
        "default_boundary_policy": "all",
        "require_figure_refs_strict": True,
        "require_baseline_if_present": True,
        "require_sample_size_if_present": True,
        "prose_strict": True,
    },
    "tech_report": {
        "label": "Cross-Team Technical Report",
        "require_acronym_expansion": False,
        "max_avg_zh_sent_len": 52.0,
        "max_avg_en_sent_words": 32.0,
        "default_max_zh_chars": 5000,
        "default_max_en_words": 2500,
        "required_sections": (
            ("methods_and_metrics", r"方法|指标|对照|样本|实施例|method|metric|benchmark|control|design"),
            ("limitations_and_next", r"局限|边界|风险|下一步|limitation|boundary|risk|caveat"),
        ),
        "require_model_boundary": True,
        "default_boundary_policy": "all",
        "require_figure_refs_strict": True,
        "require_baseline_if_present": True,
        "require_sample_size_if_present": True,
        "prose_strict": False,
    },
}

UNIT_RE_PART = (
    r"mg/kg|mg/mL|μg/mL|ug/mL|g/L|mol/L|mmol/L|mA/cm2|mW/cm2|kg/m3|m/s|/\s*10,?000|"
    r"GPa|MPa|kPa|Pa|kN|N|kJ|J|kW|mW|W|mV|V|mA|A|GHz|MHz|kHz|Hz|Ω|"
    r"km|cm|mm|μm|um|nm|kg|mg|μg|ug|ng|mL|μL|uL|mmol|μmol|mol|mM|μM|"
    r"°C|℃|K|ms|min|hr|h|days?|weeks?|months?|years?|%|％|ppm|ppb|"
    r"例|名|岁|次|小时|分钟|天|年|月|周|重量份|质量份|wt%|vol%"
)

NUM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<pct_pre>百分之\s*)?"
    r"(?P<val>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?P<mag>\s*(?:万|亿|千|thousand|million|billion|(?<=\d)[kMB]\b))?"
    r"(?P<pct_suf>\s*[%％])?"
    r"(?P<fold>\s*(?:倍|x|×))?"
    rf"(?:\s*(?P<unit>{UNIT_RE_PART})(?![A-Za-z0-9_]))?",
    re.I,
)
# Extract Fig/Table/Citation anchors explicitly before masking IDs in numeric verification
FIG_ANCHOR_RE = re.compile(r"(?<![A-Za-z])(?:Fig(?:ure)?\.?|图)\s*(\d+(?:\.\d+)?[A-Za-z]?)\b", re.I)
TAB_ANCHOR_RE = re.compile(r"(?<![A-Za-z])(?:Table|Tab\.?|表)\s*(\d+(?:\.\d+)?[A-Za-z]?)\b", re.I)
ANONYMOUS_FIG_TAB_RE = re.compile(r"(?:如图所示|见附图|如下表|表中显示|as\s+shown\s+in\s+the\s+(?:figure|table))", re.I)
CITE_NUM_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")
CITE_AY_RE = re.compile(r"\([A-Z][A-Za-z-]+(?:\s+et\s+al\.?)?,\s*(?:19|20)\d{2}[a-z]?\)")

BIO_OR_ID_TOKEN_RE = re.compile(
    r"\b(?:IL-\d+|TNF-\w+|NF-κB|NF-kB|TGF-β\d*|CD\d+|TP\d+|KRAS|BRCA\d|A549|HEK293T?|HeLa|"
    r"C57BL/6|BALB/c|COVID-19|SARS-CoV-2|CN\d+[A-Z]?|H01M\s*\d+/\d+|"
    r"Fig(?:ure)?\.?\s*\d+(?:\.\d+)?[A-Za-z]?|Table\s*\d+(?:\.\d+)?[A-Za-z]?|"
    r"Phase\s*(?:I{1,3}|IV|[1-4])|Tier\s*[1-4]|Section\s*\d+(?:\.\d+)?|p\s*[<≤=]\s*0\.\d+)\b|"
    r"(?:图|表|实施例|对比例|附图)\s*\d+(?:\.\d+)?[A-Za-z]?",
    re.I,
)
LINE_LIST_PREFIX_RE = re.compile(r"^\s*(?:#+\s*)?\d{1,2}[.、)]\s+", re.MULTILINE)
P_VALUE_RE = re.compile(r"\b[pP]\s*(?P<op><=|≤|>=|≥|<|>|=)\s*(?P<val>0?\.\d+(?:[eE]-?\d+)?|\d+(?:\.\d+)?[eE]-\d+)")
SAMPLE_SIZE_RE = re.compile(
    r"\b[nN]\s*=\s*\d+(?:,\d{3})*|\d+(?:,\d{3})*\s*(?:patients|subjects|participants|cases|mice|rats|samples|replicates|cohorts|specimens|cells|"
    r"例患者|名受试者|例样本|例病例|只小鼠|只大鼠|个样本|次重复|组实施例|组对比例)",
    re.I,
)
BASELINE_RE = re.compile(
    r"\b(?:control\s+group|vehicle\s+control|solvent\s+control|placebo|standard\s+of\s+care|"
    r"baseline|DMSO|untreated\s+control|sham\s+control|comparative\s+example|prior\s+art|"
    r"reference\s+sample|conventional\s+(?:method|process|material)|benchmark)\b|"
    r"对照组|溶剂对照|空白对照|阴性对照|阳性对照|安慰剂|基线(?:水平|值)?|常规(?:疗法|治疗|方法)|"
    r"对比例|现有技术|传统工艺|参比(?:样品|材料)?|基准(?:方案|水平)",
    re.I,
)

# Cross-disciplinary 4-tier evidence ladder (biomedical + clinical + engineering/materials/patent)
TIER4_PATTERNS = re.compile(
    r"\b(?:randomized\s+controlled\s+trial|double-blind\s+placebo-controlled|phase\s*(?:III|3)\s+clinical\s+trial|"
    r"multicenter\s+RCT|industrial\s+mass-production\s+validation|multi-year\s+field\s+deployment)\b|"
    r"随机双盲对照临床试验|III\s*期临床试验|多中心随机对照试验|大规模工业量产验证",
    re.I,
)
TIER3_PATTERNS = re.compile(
    r"\b(?:retrospective|cross-sectional|observational(?:\s+study|\s+cohort)?|ecological\s+study|case-control|"
    r"correlat(?:ed|ion|es)|associat(?:ed|ion)|single-center|prospective\s+cohort|time-series|surveillance\s+data|"
    r"pilot-scale|pilot\s+plant)\b|"
    r"回顾性|横断面|观察性(?:研究|队列)|生态学研究|病例对照|相关性|相关关系|单中心|关联性|监测数据|中试(?:规模|阶段)",
    re.I,
)
TIER2_PATTERNS = re.compile(
    r"\b(?:in\s+mice|mouse\s+model|murine|xenograft|rat\s+model|zebrafish|organoid|preclinical|in\s+vivo\s+animal|"
    r"bench-scale|laboratory-scale|lab-scale|prototype|patent\s+example|comparative\s+example|single-cell\s+test)\b|"
    r"小鼠|大鼠|动物模型|动物实验|异种移植|类器官|临床前|实施例|对比例|实验室(?:测试|阶段|规模|制备)|小试|样机",
    re.I,
)
TIER1_PATTERNS = re.compile(
    r"\b(?:in\s+vitro|cell\s+line|cultured\s+cells|A549|HeLa|HEK293|molecular\s+docking|in\s+silico|"
    r"computational\s+simulation|bioinformatics\s+prediction|DFT\s+calculation)\b|"
    r"体外(?:细胞|实验)?|细胞系|培养细胞|分子对接|计算模拟|生信预测|理论计算",
    re.I,
)

CLAIM_CLINICAL_CURE_RE = re.compile(
    r"\b(?:cures?\s+(?:human|patients|cancer|disease)|clinically\s+proven|ready\s+for\s+clinical\s+use|"
    r"replaces?\s+standard\s+therapy|guaranteed\s+efficacy|effective\s+treatment\s+for\s+patients|"
    r"breakthrough\s+cure|mass-production\s+proven|proven\s+for\s+commercial\s+mass\s+production)\b|"
    r"彻底治愈|有效治愈|治愈患者|已成熟临床应用|可直接用于临床(?:治疗)?|替代现有(?:临床)?疗法|"
    r"攻克(?:癌症|肿瘤|疾病)|惠及广大患者临床治愈|确保疗效|临床疗效确凿|改写医学史|"
    r"已实现大规模量产验证|全面替代现有工业产线|零衰减商用",
    re.I,
)
CLAIM_CAUSAL_PROOF_RE = re.compile(
    r"\b(?:definitively\s+proves?|conclusively\s+proves?|proves?\s+causation|guarantees?\s+that|"
    r"eliminates?\s+all\s+risk)\b|"
    r"完全证实(?:了)?因果|确证因果|必然导致|百分百预防|彻底消除风险|直接证明.{0,12}治愈",
    re.I,
)
CLAIM_ANIMAL_OR_HUMAN_FROM_CELL_RE = re.compile(
    r"\b(?:improves?\s+patient\s+survival|reduces?\s+human\s+mortality|safe\s+for\s+patients)\b|"
    r"显著改善患者生存|降低人类死亡率|患者服用后|对人体安全有效",
    re.I,
)
# Paraphrased translational promise requiring explicit proximity hedge when source Tier <= 3
PROMISE_RE = re.compile(
    r"有望.{0,14}(?:用于|应用于|进入)(?:临床|患者|病人|大规模量产|工业量产)|"
    r"为.{0,14}(?:临床治疗|患者治疗)带来(?:新)?希望|"
    r"\b(?:promising\s+for\s+(?:patients|clinical\s+use|commercial\s+mass\s+production)|"
    r"direct\s+clinical\s+benefit|ready\s+for\s+mass\s+production)\b",
    re.I,
)
HEDGE_QUALIFIER_RE = re.compile(
    r"尚未|未验证|仍需|有待|临床前|动物|体外|实验室|小试|实施例|观察性|回顾性|生态学|初步|仅在|"
    r"\b(?:not\s+yet|preliminary|further\s+(?:study|validation|testing|trial)|preclinical|"
    r"in\s+vitro|animal|bench-scale|lab-scale|observational|retrospective|ecological|limited\s+to)\b",
    re.I,
)

BOUNDARY_MARKERS = {
    "in_vitro_cell": re.compile(r"\b(?:in\s+vitro|cell\s+line|cell\s+culture|cultured\s+cells|cellular\s+model)\b|体外|细胞系|细胞实验|细胞模型|培养细胞", re.I),
    "mouse_or_animal": re.compile(r"\b(?:mice|mouse|murine|rat|animal\s+model|preclinical|xenograft|not\s+yet\s+tested\s+in\s+humans)\b|小鼠|大鼠|动物模型|动物实验|临床前|尚未(?:进入|开展)人体", re.I),
    "bench_or_patent_example": re.compile(r"\b(?:bench-scale|lab-scale|laboratory-scale|prototype|patent\s+example|not\s+yet\s+validated\s+at\s+industrial\s+scale)\b|实验室|实施例|小试|样机|专利实施例|尚未(?:完成|经)量产|工程放大", re.I),
    "observational_correlational": re.compile(r"\b(?:correlat|associat|observational|retrospective|ecological|surveillance|not\s+causation|cannot\s+establish\s+causality|suggests?|underreporting)\b|相关(?:性|关系)?|关联|观察性|回顾性|生态学|监测数据|并非因果|不能证明因果|提示|漏报", re.I),
}

ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,8}(?:-[A-Z0-9]{1,4}|\d{1,2})?)(?![A-Za-z0-9])")
COMMON_ACRONYMS = {
    "DNA", "RNA", "AI", "CT", "MRI", "WHO", "FDA", "GDP", "USA", "UK", "EU", "UN",
    "PDF", "HTML", "JSON", "CSV", "CLI", "API", "CI", "OR", "HR", "SD", "SE", "IQR",
    "II", "III", "IV", "VI", "PH", "UV", "IR", "LED", "LCD", "CPU", "GPU", "TRL",
    "CN", "EN", "ZH", "SI", "ID", "VS", "OK", "NO", "YES",
}

HYPE_WORDS = (
    "颠覆", "炸裂", "重磅", "改写医学史", "史无前例", "彻底攻克", "堪称奇迹",
    "令人振奋", "开启新时代", "开启新的篇章", "革命性治愈",
    "breakthrough cure", "game-changing", "miracle cure", "revolutionizes medicine",
)
CORP_JARGON = (
    "赋能", "抓手", "商业闭环", "价值闭环", "能力沉淀", "拉通", "底层逻辑", "顶层设计",
    "认知跃迁", "价值释放", "降本增效", "内容矩阵", "全链路", "组合拳", "打开想象空间",
    "结构性机会", "技术底座", "迭代闭环",
)
HARD_STOPS = ("说白了", "说穿了", "先说结论")
AI_ROAD_SIGNS = (
    "值得注意的是", "需要指出的是", "更微妙的是", "从某种意义上说", "综上所述",
    "不难发现", "众所周知", "由此可见", "总的来说",
)
# Calibrated: rhetorical clichés warn by default, with explicit scientific contrast whitelist
FALSE_PIVOT_PATTERNS = (
    re.compile(r"(?:并)?不是[^。！？\n]{1,70}而是"),
    re.compile(r"并非[^。！？\n]{1,70}而是"),
    re.compile(r"不在于[^。！？\n]{1,70}而在于"),
    re.compile(r"与其说[^。！？\n]{1,70}(?:不如|毋宁|倒不如)"),
    re.compile(r"表面(?:上)?[^。！？\n]{1,70}(?:其实|实际|实则)"),
    re.compile(r"看似[^。！？\n]{1,70}(?:其实|实际|实则)"),
)
SCIENTIFIC_CONTRAST_WHITELIST = re.compile(
    r"(?:并非|不是).{0,25}(?:因果|临床试验|人体试验|量产|治愈|定论).{0,25}而是.{0,25}(?:相关|关联|观察性|动物|细胞|临床前|实验室|初步|实施例)"
)
NOMINALIZATION_PATTERNS = (
    re.compile(r"进行(?:了|一次|一场)?[^。，！？\n]{0,10}(?:调整|优化|升级|分析|梳理|探索|尝试)"),
    re.compile(r"实现了?[^。，！？\n]{0,14}的?[^。，！？\n]{0,6}(?:提升|增长|突破|转变|跃升|落地)"),
    re.compile(r"完成了?对[^。，！？\n]{0,16}的"),
)
SCIENTIFIC_WHITELIST = (
    "细胞凋亡", "肿瘤杀伤", "基因敲除", "温度升高", "温度降低", "冷却速率", "血管生成",
    "沉淀反应", "分子骨架", "细胞骨架", "信号通路", "蛋白沉淀", "抗体沉淀", "免疫沉淀",
)
CRITICAL_LIMITATION_RE = re.compile(
    r"\b(?:n\s*=\s*\d+|sample\s+size|in\s+vitro|cell\s+line|mouse|mice|animal|preclinical|"
    r"correlat|observational|retrospective|ecological|underreport|single-center|limitation|caveat|"
    r"control\s+group|vehicle|placebo|comparative\s+example|adverse|negative\s+result|not\s+significant)\b|"
    r"样本量|体外|细胞系|小鼠|大鼠|动物模型|临床前|相关性|回顾性|观察性|生态学|漏报|单中心|局限|不足|"
    r"对照组|对比例|实验室阶段|尚未量产|阴性结果|未显著|未验证",
    re.I,
)


def _extract_docx_text(docx_path: Path) -> tuple[str, dict[str, Any]]:
    paras: list[str] = []
    with zipfile.ZipFile(docx_path, "r") as zf:
        for xml_name in ("word/document.xml", "word/footnotes.xml"):
            if xml_name not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(xml_name))
            for p_node in root.iter():
                if p_node.tag.split("}")[-1] != "p":
                    continue
                pieces = [c.text for c in p_node.iter() if c.tag.split("}")[-1] == "t" and c.text]
                line = "".join(pieces).strip()
                if line:
                    paras.append(line)
    return "\n\n".join(paras), {"format": "docx", "extractor": "stdlib_ooxml", "paragraphs": len(paras)}


def _extract_pdf_stdlib_zlib(pdf_path: Path) -> tuple[str, int]:
    raw = pdf_path.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL):
        data = m.group(1)
        dec = None
        for wb in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                dec = zlib.decompress(data, wb)
                break
            except Exception:
                pass
        s = (dec or data).decode("latin-1", errors="ignore")
        if "Tj" not in s and "TJ" not in s:
            continue
        for tm in re.finditer(r"\(((?:\\.|[^\\)])*)\)\s*Tj", s):
            t = tm.group(1).replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\").strip()
            if t:
                chunks.append(t)
        for am in re.finditer(r"\[(.*?)\]\s*TJ", s, re.DOTALL):
            parts = [pm.group(1).replace(r"\(", "(").replace(r"\)", ")") for pm in re.finditer(r"\(((?:\\.|[^\\)])*)\)", am.group(1))]
            joined = "".join(parts).strip()
            if joined:
                chunks.append(joined)
    return "\n".join(chunks), max(pages, 1 if chunks else 0)


def read_document_text(path: Path) -> tuple[str, dict[str, Any]]:
    """Native .pdf / .docx / UTF-8 text loader with zero mandatory third-party deps."""
    raw = path.read_bytes()
    suf = path.suffix.lower()
    if suf == ".docx":
        txt, meta = _extract_docx_text(path)
    elif suf == ".pdf":
        approx_pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", raw))
        txt, meta = "", {"format": "pdf", "extractor": "stdlib_zlib_fallback", "pages": approx_pages}
        bin_p = shutil.which("pdftotext")
        if bin_p:
            try:
                pr = subprocess.run([bin_p, "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                if pr.returncode == 0 and pr.stdout.strip():
                    txt, meta = pr.stdout, {"format": "pdf", "extractor": "pdftotext", "pages": approx_pages or 1}
            except Exception:
                pass
        if not txt:
            try:
                import pdfplumber  # type: ignore
                with pdfplumber.open(str(path)) as doc:
                    pages_t = [(pg.extract_text() or "") for pg in doc.pages]
                    joined = "\n\n".join(f"<!-- Page {i} -->\n\n{t}" for i, t in enumerate(pages_t, 1) if t.strip())
                    if joined.strip():
                        txt, meta = joined, {"format": "pdf", "extractor": "pdfplumber", "pages": len(doc.pages)}
            except Exception:
                pass
        if not txt:
            try:
                from pypdf import PdfReader  # type: ignore
                rd = PdfReader(str(path))
                pages_t = [(pg.extract_text() or "") for pg in rd.pages]
                joined = "\n\n".join(f"<!-- Page {i} -->\n\n{t}" for i, t in enumerate(pages_t, 1) if t.strip())
                if joined.strip():
                    txt, meta = joined, {"format": "pdf", "extractor": "pypdf", "pages": len(rd.pages)}
            except Exception:
                pass
        if not txt:
            txt, pages = _extract_pdf_stdlib_zlib(path)
            meta = {"format": "pdf", "extractor": "stdlib_zlib_fallback", "pages": pages}
    else:
        txt = raw.decode("utf-8", errors="replace")
        meta = {"format": suf.lstrip(".") or "txt", "extractor": "utf8_text"}
    meta["bytes"] = len(raw)
    meta["chars"] = len(txt)
    meta["sha256"] = hashlib.sha256(raw).hexdigest()
    return txt, meta


def _mask_non_prose(text: str) -> str:
    masked = text
    for pat in (re.compile(r"```.*?```", re.DOTALL), re.compile(r"`[^`\n]*`"), re.compile(r"https?://[^\s)>]+")):
        masked = pat.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def _mask_bio_ids(text: str) -> str:
    cleaned = LINE_LIST_PREFIX_RE.sub(" ", text)
    cleaned = BIO_OR_ID_TOKEN_RE.sub(lambda m: " " * len(m.group(0)), cleaned)
    return cleaned


def _mag_multiplier(mag_raw: str | None) -> float:
    if not mag_raw:
        return 1.0
    m = mag_raw.strip().lower()
    if m in {"千", "thousand", "k"}:
        return 1e3
    if m == "万":
        return 1e4
    if m == "亿":
        return 1e8
    if m in {"million", "m"}:
        return 1e6
    if m in {"billion", "b"}:
        return 1e9
    return 1.0


def _norm_unit(u: str | None) -> str:
    if not u:
        return ""
    s = re.sub(r"\s+", " ", u.strip()).replace("％", "%").replace("℃", "°C").replace("um", "μm").replace("ug", "μg")
    return s.lower()


def _extract_clause_context(text: str, start: int, end: int) -> tuple[str, list[str], list[str]]:
    """Extract tight clause around a numeric token and bind canonical group & metric tokens."""
    lo = max(0, start - 55)
    hi = min(len(text), end + 35)
    left_part = text[lo:start]
    right_part = text[end:hi]
    # Bound by clause delimiters so paired "治疗组 61.5%，对照组 53.1%" stays strictly separated
    m_l = list(re.finditer(r"[,;，；。！？!?\n]|(?<!\d)\.(?!\d)|(?:\band\b|\bvs\.?\b|\bversus\b|而|以及|相比)", left_part, re.I))
    c_start = (lo + m_l[-1].end()) if m_l else lo
    m_r = re.search(r"[,;，；。！？!?\n]|(?<!\d)\.(?!\d)|(?:\band\b|\bvs\.?\b|\bversus\b|而|以及|相比)", right_part, re.I)
    c_end = (end + m_r.start()) if m_r else hi
    clause = text[c_start:c_end]

    group_rules = (
        ("treatment_arm", r"\b(?:treatment|intervention|treated|drug|active|experimental|therapy)\s*(?:group|arm|cohort)?\b|治疗组|干预组|实验组|给药组|观察组"),
        ("control_arm", r"\b(?:control|placebo|vehicle|sham|untreated|standard\s+of\s+care)\s*(?:group|arm|cohort)?\b|对照组|安慰剂组|空白组|溶剂对照"),
        ("female_girls", r"\b(?:girls?|females?|women)\b|女童|女性|女生"),
        ("male_boys", r"\b(?:boys?|males?|men)\b|男童|男性|男生"),
        ("children_0_9", r"\b(?:0\s*[–-]\s*9|0\s*[–-]\s*4|preschoolers?)\b|0\s*[–-]\s*9\s*岁|学龄前"),
        ("adolescents_10_19", r"\b(?:10\s*[–-]\s*19|adolescents?)\b|10\s*[–-]\s*19\s*岁|青少年"),
        ("patent_example", r"\b(?:example\s*\d+|inventive\s+example)\b|实施例\s*\d*"),
        ("comparative_example", r"\b(?:comparative\s+example\s*\d*|prior\s+art)\b|对比例\s*\d*|现有技术"),
    )
    metric_rules = (
        ("survival", r"\b(?:survival|os|pfs)\b|生存率|存活率"),
        ("response_efficacy", r"\b(?:response\s+rate|efficacy|remission|orr|cure\s+rate)\b|有效率|缓解率|疗效"),
        ("incidence", r"\b(?:incidence|prevalence|rate)\b|发病率|发生率|检出率"),
        ("mortality", r"\b(?:mortality|death\s+rate|fatality)\b|死亡率|病死率"),
        ("efficiency", r"\b(?:energy\s+efficiency|coulombic\s+efficiency|voltage\s+efficiency|efficiency)\b|能量效率|库仑效率|电压效率|效率"),
        ("trend_aapc", r"\b(?:aapc|apc|annual\s+percent\s+change)\b|年均变化百分比"),
    )
    groups = [name for name, pat in group_rules if re.search(pat, clause, re.I)]
    metrics = [name for name, pat in metric_rules if re.search(pat, clause, re.I)]
    return clause.strip(), groups, metrics


def extract_numeric_facts(text: str) -> list[dict[str, Any]]:
    scan = _mask_bio_ids(_mask_non_prose(text))
    facts: list[dict[str, Any]] = []
    for m in NUM_TOKEN_RE.finditer(scan):
        raw_val = m.group("val").replace(",", "")
        try:
            base_num = float(raw_val)
        except ValueError:
            continue
        is_pct = bool(m.group("pct_pre") or m.group("pct_suf"))
        is_fold = bool(m.group("fold"))
        mag = (m.group("mag") or "").strip()
        unit = _norm_unit(m.group("unit"))
        if unit == "%":
            is_pct = True
            unit = ""
        mult = _mag_multiplier(mag)
        scaled = base_num * mult
        if not is_pct and not is_fold and not mag and unit in {"", "年"} and "." not in raw_val and 1900 <= int(base_num) <= 2099:
            continue
        kind = "percent" if is_pct else ("fold" if is_fold else "value")
        line_no = text.count("\n", 0, m.start()) + 1
        sent = _enclosing_sentence(text, m.start(), m.end()).strip()
        clause, g_toks, m_toks = _extract_clause_context(text, m.start(), m.end())
        facts.append({
            "raw": m.group(0).strip(), "kind": kind, "base": base_num,
            "multiplier": mult, "scaled": scaled, "unit": unit,
            "start": m.start(), "end": m.end(), "line": line_no,
            "sentence": sent[:220], "clause": clause[:140],
            "group_tokens": g_toks, "metric_tokens": m_toks,
        })
    return facts


def _numbers_match(a: float, b: float, tol_rel: float = 1e-5) -> bool:
    if a == b:
        return True
    if a.is_integer() and b.is_integer():
        return False
    return abs(a - b) / max(abs(a), abs(b), 1e-12) <= tol_rel


def _enclosing_sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(sep, 0, start) for sep in (".", "!", "?", "。", "！", "？", "\n"))
    right_cands = [p for sep in (".", "!", "?", "。", "！", "？", "\n") if (p := text.find(sep, end)) != -1]
    right = min(right_cands) if right_cands else len(text)
    return text[left + 1 : right]


def _norm_p_op(op: str) -> str:
    return op.replace("≤", "<=").replace("≥", ">=")


def _p_entails(s_op: str, s_val: float, t_op: str, t_val: float) -> bool:
    """Strict mathematical interval entailment: does source (s_op, s_val) imply target (t_op, t_val)?"""
    so = _norm_p_op(s_op)
    to = _norm_p_op(t_op)
    eps = 1e-9
    if to == "=":
        return so == "=" and _numbers_match(t_val, s_val, 1e-4)
    if to == "<":
        if so == "=":
            return s_val < t_val - eps
        if so == "<":
            return s_val <= t_val * (1.0 + 1e-5)
        if so == "<=":
            return s_val < t_val - eps
        return False
    if to == "<=":
        if so in {"=", "<", "<="}:
            return s_val <= t_val * (1.0 + 1e-5)
        return False
    if to == ">":
        if so == "=":
            return s_val > t_val + eps
        if so == ">":
            return s_val >= t_val * (1.0 - 1e-5)
        if so == ">=":
            return s_val > t_val + eps
        return False
    if to == ">=":
        if so in {"=", ">", ">="}:
            return s_val >= t_val * (1.0 - 1e-5)
        return False
    return False


def _verify_p_values_multiset(src_txt: str, tgt_txt: str) -> tuple[bool, list[dict[str, Any]]]:
    """Strict interval-entailment p-value verification: blocks fabricated p-values and operator/bound drift."""
    src_ps = [(_norm_p_op(m.group("op")), float(m.group("val")), m.group(0)) for m in P_VALUE_RE.finditer(src_txt)]
    tgt_ps = [(_norm_p_op(m.group("op")), float(m.group("val")), m.group(0)) for m in P_VALUE_RE.finditer(tgt_txt)]
    violations: list[dict[str, Any]] = []
    if not tgt_ps:
        return False, violations
    if not src_ps:
        for t_op, t_val, t_raw in tgt_ps:
            violations.append({
                "code": "UNVERIFIED_P_VALUE",
                "target_raw": t_raw,
                "target_op": t_op,
                "target_val": t_val,
                "source_p_values": [],
                "reason": "Source contains no p-values; target introduces unverified p-value.",
            })
        return True, violations

    used_src: set[int] = set()
    for t_op, t_val, t_raw in tgt_ps:
        matched_idx = None
        # 1) Prefer unused source p-value with exact operator & value match
        for idx, (s_op, s_val, _) in enumerate(src_ps):
            if idx not in used_src and s_op == t_op and _numbers_match(t_val, s_val, 1e-4):
                matched_idx = idx
                break
        # 2) Unused source p-value satisfying strict interval entailment (e.g. source p=0.042 -> target p<0.05)
        if matched_idx is None:
            for idx, (s_op, s_val, _) in enumerate(src_ps):
                if idx not in used_src and _p_entails(s_op, s_val, t_op, t_val):
                    matched_idx = idx
                    break
        # 3) Repeated valid mention of an already-used source p-value STILL requires strict entailment!
        if matched_idx is None:
            for idx, (s_op, s_val, _) in enumerate(src_ps):
                if _p_entails(s_op, s_val, t_op, t_val):
                    matched_idx = idx
                    break

        if matched_idx is not None:
            used_src.add(matched_idx)
        else:
            same_num_diff_op = any(_numbers_match(t_val, s_val, 1e-4) and s_op != t_op for s_op, s_val, _ in src_ps)
            tighter_bound = t_op in {"<", "<="} and any(s_op in {"<", "<=", "="} and t_val < s_val for s_op, s_val, _ in src_ps)
            if same_num_diff_op or tighter_bound:
                code = "P_VALUE_OPERATOR_OR_BOUND_DRIFT"
                reason = (
                    f"Operator drift ({t_raw} not entailed by source {[r for _, _, r in src_ps[:6]]})"
                    if same_num_diff_op
                    else f"Bound strengthened beyond source ({t_raw} vs source {[r for _, _, r in src_ps[:6]]})"
                )
            else:
                code = "UNVERIFIED_P_VALUE"
                reason = f"Target p-value {t_raw} has no entailing source p-value in {[r for _, _, r in src_ps[:6]]}."
            violations.append({
                "code": code,
                "target_raw": t_raw,
                "target_op": t_op,
                "target_val": t_val,
                "source_p_values": [r for _, _, r in src_ps[:6]],
                "reason": reason,
            })
    return len(violations) > 0, violations


def _scan_source_internal_contradictions(src_txt: str) -> list[dict[str, Any]]:
    """Detect obvious internal contradictions (same named metric + unit with conflicting values across source)."""
    pat = re.compile(
        r"(?P<metric>(?:sample\s+size|incidence\s+rate|mean\s+age|efficiency|capacity|conductivity|tensile\s+strength|survival\s+rate|"
        r"总样本量|平均年龄|能量效率|库仑效率|抗拉强度|电导率))\s*(?:of|was|is|=|为|达)\s*"
        r"(?P<val>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>%|％|MPa|mA/cm2|°C|℃|years?|岁|例)?",
        re.I,
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for m in pat.finditer(src_txt):
        k = (" ".join(m.group("metric").lower().split()), _norm_unit(m.group("unit")))
        grouped.setdefault(k, []).append({"value": float(m.group("val").replace(",", "")), "raw": m.group(0), "line": src_txt.count("\n", 0, m.start()) + 1})
    out: list[dict[str, Any]] = []
    for (metric, unit), obs in grouped.items():
        vals = sorted({o["value"] for o in obs})
        if len(vals) > 1 and (max(vals) - min(vals)) / max(abs(max(vals)), 1e-9) > 1e-3:
            out.append({"metric": metric, "unit": unit, "values": vals, "occurrences": obs[:6]})
    return out


def _verify_numbers(src_txt: str, tgt_txt: str) -> dict[str, Any]:
    src_facts = extract_numeric_facts(src_txt)
    tgt_facts = extract_numeric_facts(tgt_txt)

    src_values = [f["scaled"] for f in src_facts if f["kind"] == "value"]
    src_pcts = [f["scaled"] for f in src_facts if f["kind"] == "percent"]
    src_folds = [f["scaled"] for f in src_facts if f["kind"] == "fold"]
    # Build value -> observed units map for unit-mismatch detection
    src_units_by_val: list[tuple[float, str]] = [(f["scaled"], f["unit"]) for f in src_facts if f["unit"]]

    ungrounded: list[str] = []
    unit_mismatches: list[dict[str, Any]] = []
    group_metric_swaps: list[dict[str, Any]] = []
    bilingual_equiv: list[dict[str, Any]] = []
    verified_derived: list[dict[str, Any]] = []

    OPPOSING_GROUPS = {
        "treatment_arm": {"control_arm"},
        "control_arm": {"treatment_arm"},
        "female_girls": {"male_boys"},
        "male_boys": {"female_girls"},
        "children_0_9": {"adolescents_10_19"},
        "adolescents_10_19": {"children_0_9"},
        "patent_example": {"comparative_example"},
        "comparative_example": {"patent_example"},
    }

    for tf in tgt_facts:
        matched = False
        matched_src_facts: list[dict[str, Any]] = []
        if tf["kind"] == "percent":
            matched_src_facts = [sf for sf in src_facts if sf["kind"] == "percent" and _numbers_match(tf["scaled"], sf["scaled"])]
            if matched_src_facts:
                matched = True
            else:
                dec_hits = [sf for sf in src_facts if sf["kind"] == "value" and _numbers_match(tf["scaled"] / 100.0, sf["scaled"])]
                if dec_hits:
                    matched = True
                    matched_src_facts = dec_hits
                    bilingual_equiv.append({"target": tf["raw"], "matched_decimal_ratio": round(tf["scaled"] / 100.0, 6)})
        elif tf["kind"] == "fold":
            matched_src_facts = [
                sf for sf in src_facts
                if sf["kind"] in {"fold", "value"} and _numbers_match(tf["scaled"], sf["scaled"])
            ]
            if matched_src_facts:
                matched = True
        else:
            matched_src_facts = [sf for sf in src_facts if sf["kind"] == "value" and _numbers_match(tf["scaled"], sf["scaled"])]
            if matched_src_facts:
                matched = True
                if tf["multiplier"] != 1.0:
                    bilingual_equiv.append({"target": tf["raw"], "normalized_value": tf["scaled"]})
                # Unit context check if target attaches an explicit SI/physical unit
                if tf["unit"] and tf["unit"] not in {"年", "月", "天", "例", "名", "岁", "次", "years", "months", "days"}:
                    same_val_units = {u for sv, u in src_units_by_val if _numbers_match(tf["scaled"], sv)}
                    if same_val_units and tf["unit"] not in same_val_units:
                        unit_mismatches.append({"target": tf["raw"], "target_unit": tf["unit"], "source_units": sorted(same_val_units)})

        # Group / metric binding verification (intercepts swapped arms/subgroups sharing same numeric set!)
        if matched and matched_src_facts and tf.get("group_tokens"):
            t_groups = set(tf["group_tokens"])
            src_with_groups = [sf for sf in matched_src_facts if sf.get("group_tokens")]
            if src_with_groups:
                has_compatible = any(t_groups & set(sf["group_tokens"]) for sf in src_with_groups)
                if not has_compatible:
                    src_g_union = sorted({g for sf in src_with_groups for g in sf["group_tokens"]})
                    has_opposing = any(
                        sg in OPPOSING_GROUPS.get(tg, set())
                        for tg in t_groups for sg in src_g_union
                    )
                    if has_opposing:
                        group_metric_swaps.append({
                            "code": "GROUP_OR_METRIC_SWAP_MISMATCH",
                            "target_raw": tf["raw"],
                            "target_line": tf["line"],
                            "target_groups": sorted(t_groups),
                            "target_clause": tf["clause"],
                            "source_groups": src_g_union,
                            "source_lines": sorted({sf["line"] for sf in src_with_groups})[:4],
                            "source_clause_sample": src_with_groups[0]["clause"],
                        })

        # Sentence-scoped derived ratio/fold verification (tightened relative tolerance)
        if not matched and tf["kind"] in {"percent", "fold"}:
            sent = _enclosing_sentence(tgt_txt, tf["start"], tf["end"])
            sent_nums = [
                f["scaled"] for f in extract_numeric_facts(sent)
                if f["kind"] == "value" and any(_numbers_match(f["scaled"], sv) for sv in src_values)
            ]
            for i in range(len(sent_nums)):
                for j in range(len(sent_nums)):
                    if i == j or sent_nums[j] == 0:
                        continue
                    ratio = sent_nums[i] / sent_nums[j]
                    if tf["kind"] == "percent":
                        pct_val = ratio * 100.0
                        if abs(pct_val - tf["scaled"]) <= max(0.15, tf["scaled"] * 0.005):
                            matched = True
                            verified_derived.append({"target": tf["raw"], "numerator": sent_nums[i], "denominator": sent_nums[j]})
                            break
                    elif tf["kind"] == "fold":
                        if abs(ratio - tf["scaled"]) <= max(0.02, tf["scaled"] * 0.02):
                            matched = True
                            verified_derived.append({"target": tf["raw"], "numerator": sent_nums[i], "denominator": sent_nums[j]})
                            break
                if matched:
                    break

        if not matched:
            if tf["kind"] == "value" and tf["multiplier"] == 1.0 and not tf["unit"] and tf["raw"] in {"1", "2", "3", "4", "5"}:
                continue
            ungrounded.append(tf["raw"])

    p_violated, p_violations = _verify_p_values_multiset(src_txt, tgt_txt)
    return {
        "ungrounded_target_numbers": sorted(set(ungrounded)),
        "numeric_unit_mismatches": unit_mismatches,
        "group_or_metric_swaps": group_metric_swaps,
        "bilingual_magnitude_matches": bilingual_equiv,
        "verified_derived_ratios": verified_derived,
        "p_value_strengthened": p_violated,
        "p_value_violations": p_violations,
        "source_has_sample_size": bool(SAMPLE_SIZE_RE.search(src_txt)),
        "target_has_sample_size": bool(SAMPLE_SIZE_RE.search(tgt_txt)),
        "source_has_baseline": bool(BASELINE_RE.search(src_txt)),
        "target_has_baseline": bool(BASELINE_RE.search(tgt_txt)),
    }


def _extract_numeric_citation_ids(text: str) -> set[int]:
    ids: set[int] = set()
    for m in CITE_NUM_RE.finditer(text):
        # Ignore markdown hyperlinks [1](...)
        if m.end() < len(text) and text[m.end() : m.end() + 1] == "(":
            continue
        for part in m.group(1).split(","):
            part = part.strip()
            rng = re.match(r"^(\d+)\s*[–-]\s*(\d+)$", part)
            if rng:
                a, b = int(rng.group(1)), int(rng.group(2))
                if a <= b and b - a <= 50:
                    ids.update(range(a, b + 1))
            elif part.isdigit():
                ids.add(int(part))
    return ids


def audit_figure_table_citation_preservation(
    src_txt: str,
    tgt_txt: str,
    *,
    strict_refs: bool,
    trace_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify exact source Fig/Table/citation IDs; blocks fabricated displays or ungrounded citation numbers even when source has none."""
    src_figs = sorted({m.group(1).upper() for m in FIG_ANCHOR_RE.finditer(src_txt)})
    tgt_figs = sorted({m.group(1).upper() for m in FIG_ANCHOR_RE.finditer(tgt_txt)})
    src_tabs = sorted({m.group(1).upper() for m in TAB_ANCHOR_RE.finditer(src_txt)})
    tgt_tabs = sorted({m.group(1).upper() for m in TAB_ANCHOR_RE.finditer(tgt_txt)})

    # Always check exact set membership even when source has 0 figures/tables!
    hallucinated_figs = [f for f in tgt_figs if f not in src_figs]
    hallucinated_tabs = [t for t in tgt_tabs if t not in src_tabs]
    missing_figs = [f for f in src_figs if f not in tgt_figs]
    missing_tabs = [t for t in src_tabs if t not in tgt_tabs]

    src_cite_ids = _extract_numeric_citation_ids(src_txt)
    # Also recognize numbered reference list entries "12. Author..." in source
    for rm in re.finditer(r"(?:^|\n)\s*(\d{1,3})\.\s+[A-Z\u4e00-\u9fff]", src_txt):
        src_cite_ids.add(int(rm.group(1)))
    tgt_cite_ids = _extract_numeric_citation_ids(tgt_txt)
    hallucinated_cites = sorted(c for c in tgt_cite_ids if c not in src_cite_ids)

    trace_blob = " ".join(
        " ".join(str(x) for x in (it.get("omitted_items") or []) + (it.get("retained_facts") or []))
        + " " + str(it.get("keep_or_omit_reason") or "")
        for it in trace_entries if isinstance(it, dict)
    ).lower()
    trace_accounts_displays = bool(re.search(r"fig|table|chart|graph|图|表|附图", trace_blob))

    anon_refs = [m.group(0) for m in ANONYMOUS_FIG_TAB_RE.finditer(tgt_txt)]
    blocking_errors: list[str] = []
    advisory_warnings: list[str] = []

    if hallucinated_figs or hallucinated_tabs or hallucinated_cites:
        blocking_errors.append(
            f"FABRICATED_FIGURE_TABLE_OR_CITATION: target references displays/citations absent from source "
            f"(figs={hallucinated_figs}, tables={hallucinated_tabs}, citations={[f'[{c}]' for c in hallucinated_cites]})."
        )
    if anon_refs and not (tgt_figs or tgt_tabs):
        blocking_errors.append(
            f"ANONYMOUS_FIGURE_TABLE_REF: target uses vague display reference {anon_refs[:3]} without preserving source Fig/Table numbers."
        )
    if (src_figs or src_tabs) and not (tgt_figs or tgt_tabs):
        if strict_refs:
            blocking_errors.append(
                f"FIGURE_TABLE_CITATION_DROPPED: source defines displays (figs={src_figs[:6]}, tables={src_tabs[:6]}) but target retained none."
            )
        elif trace_entries and not trace_accounts_displays:
            blocking_errors.append(
                "FIGURE_TABLE_OMISSION_UNTRACED: target dropped all source Fig/Table references without logging display omission in paragraph_trace.json."
            )
        else:
            advisory_warnings.append(
                f"Source displays omitted for lay audience (figs={src_figs[:6]}, tables={src_tabs[:6]}); confirm in side_by_side_review.md."
            )
    elif strict_refs and (missing_figs or missing_tabs):
        advisory_warnings.append(
            f"Partial display preservation: omitted figs={missing_figs[:6]}, tables={missing_tabs[:6]}."
        )

    return {
        "source_figures": src_figs,
        "target_figures": tgt_figs,
        "missing_figures": missing_figs,
        "hallucinated_figures": hallucinated_figs,
        "source_tables": src_tabs,
        "target_tables": tgt_tabs,
        "missing_tables": missing_tabs,
        "hallucinated_tables": hallucinated_tabs,
        "source_citation_ids": sorted(src_cite_ids)[:40],
        "target_citation_ids": sorted(tgt_cite_ids)[:40],
        "hallucinated_citations": [f"[{c}]" for c in hallucinated_cites],
        "source_citations_count": len(CITE_NUM_RE.findall(src_txt)) + len(CITE_AY_RE.findall(src_txt)),
        "target_citations_count": len(CITE_NUM_RE.findall(tgt_txt)) + len(CITE_AY_RE.findall(tgt_txt)),
        "blocking_errors": blocking_errors,
        "warnings": advisory_warnings,
    }


BACKGROUND_SECTION_RE = re.compile(r"^(?:#+\s*)?(?:introduction|background|literature\s+review|引言|背景|文献综述)\b", re.I)
EMPIRICAL_SECTION_RE = re.compile(r"^(?:#+\s*)?(?:methods?|materials?\s+and\s+methods?|results?|patients?|design|experiments?|实施例|方法|结果|研究设计)\b", re.I)
BACKGROUND_SENT_CUE_RE = re.compile(
    r"\b(?:previous(?:ly)?\s+(?:rct|randomized|trial|study|studies|reported|shown)|prior\s+(?:rct|randomized|trial|study|studies)|"
    r"earlier\s+(?:rct|trial|study|studies)|in\s+the\s+literature|published\s+(?:rct|trial|studies)|meta-analysis\s+showed|"
    r"other\s+studies\s+have\s+shown)\b|"
    r"既往(?:随机对照|临床试验|研究)|此前(?:随机对照|试验|研究)|以往(?:临床试验|研究)|文献报道|已有研究",
    re.I,
)
PRESENT_STUDY_CUE_RE = re.compile(
    r"\b(?:in\s+this\s+study|the\s+present\s+study|we\s+(?:conducted|performed|evaluated|tested|treated|cultured|enrolled|analyzed)|"
    r"here\s+we|our\s+(?:study|experiment|assay|cohort|trial|model))\b|"
    r"本研究|本文|本实验|我们(?:开展|采用|评估|检测|培养|纳入)",
    re.I,
)
EXTRAPOLATION_UNIVERSAL_RE = re.compile(
    r"\b(?:all\s+patients|all\s+populations|all\s+age\s+groups|general\s+population\s+without\s+restriction|"
    r"universal(?:ly)?\s+(?:effective|applicable|safe)|adults\s+and\s+children\s+alike|every\s+patient|"
    r"any\s+indication|broad\s+off-label\s+use|eliminates?\s+all\s+risk)\b|"
    r"适用于所有人群|全人群普适|任何患者(?:均|都)(?:适用|有效)|成人与儿童同样有效|普遍适用于各类人群|"
    r"无需区分人群|适应症无限制|所有年龄段均可直接使用",
    re.I,
)
SOURCE_SCOPE_LIMIT_RE = re.compile(
    r"\b(?:children|pediatric|adolescents?|0\s*[–-]\s*19|single-center|elderly|geriatric|pregnant|"
    r"sickle\s+cell|brazil|municipality|subgroup|specific\s+cohort|restricted\s+to|excluding)\b|"
    r"儿童|青少年|单中心|老年|特定人群|局限|地区|亚组|排除",
    re.I,
)


def classify_evidence_tier(src_txt: str) -> dict[str, Any]:
    """Dual-axis (study_design_axis + translational_maturity_axis) classification isolating background citations from present-study evidence."""
    in_bg_sec = False
    present_sents: list[str] = []
    background_sents: list[str] = []

    for ln in src_txt.splitlines():
        st = ln.strip()
        if not st:
            continue
        if BACKGROUND_SECTION_RE.match(st):
            in_bg_sec = True
            continue
        if EMPIRICAL_SECTION_RE.match(st):
            in_bg_sec = False
            continue
        for sent in re.split(r"(?<=[.!?。！？])\s+", st):
            s = sent.strip()
            if not s:
                continue
            is_bg_cue = bool(BACKGROUND_SENT_CUE_RE.search(s))
            is_present_cue = bool(PRESENT_STUDY_CUE_RE.search(s))
            if (is_bg_cue and not is_present_cue) or (in_bg_sec and not is_present_cue):
                background_sents.append(s)
            else:
                present_sents.append(s)

    present_blob = "\n".join(present_sents) if present_sents else src_txt
    bg_blob = "\n".join(background_sents)

    background_tier_mentions: list[str] = []
    if TIER4_PATTERNS.search(bg_blob):
        background_tier_mentions.append("tier4_rct_in_background_ignored")

    boundaries: list[str] = []
    if TIER1_PATTERNS.search(present_blob):
        boundaries.append("in_vitro_cell")
    if TIER2_PATTERNS.search(present_blob):
        is_patent_or_bench = bool(re.search(r"实施例|对比例|bench-scale|lab-scale|laboratory-scale|prototype|patent\s+example", present_blob, re.I))
        boundaries.append("bench_or_patent_example" if is_patent_or_bench else "mouse_or_animal")
    if TIER3_PATTERNS.search(present_blob):
        boundaries.append("observational_correlational")

    unclassified = False
    primary_boundary: str | None = None
    if TIER4_PATTERNS.search(present_blob):
        tier, label = 4, "rct_or_clinical_causal"
        study_design_axis = "rct_or_validated_deployment"
        maturity_axis = "TRL7-9_clinical_or_industrial_validated"
    elif "observational_correlational" in boundaries and not any(b in boundaries for b in ("mouse_or_animal", "bench_or_patent_example", "in_vitro_cell")):
        tier, label, primary_boundary = 3, "observational_correlational", "observational_correlational"
        study_design_axis = "observational_epidemiology_or_pilot"
        maturity_axis = "TRL5-6_observational_or_pilot"
    elif "mouse_or_animal" in boundaries:
        tier, label, primary_boundary = 2, "animal_or_preclinical", "mouse_or_animal"
        study_design_axis = "animal_preclinical_model"
        maturity_axis = "TRL3-4_preclinical_animal"
    elif "bench_or_patent_example" in boundaries:
        tier, label, primary_boundary = 2, "bench_or_preclinical", "bench_or_patent_example"
        study_design_axis = "bench_prototype_or_patent_example"
        maturity_axis = "TRL3-4_laboratory_prototype"
    elif "in_vitro_cell" in boundaries:
        tier, label, primary_boundary = 1, "in_vitro_or_computational", "in_vitro_cell"
        is_comp = bool(re.search(r"in\s+silico|computational\s+simulation|DFT|molecular\s+docking|计算模拟|分子对接|生信预测", present_blob, re.I))
        study_design_axis = "computational_in_silico" if is_comp else "in_vitro_cell_assay"
        maturity_axis = "TRL1-2_mechanistic_or_in_vitro"
    elif "observational_correlational" in boundaries:
        tier, label, primary_boundary = 3, "observational_correlational", "observational_correlational"
        study_design_axis = "observational_epidemiology_or_pilot"
        maturity_axis = "TRL5-6_observational_or_pilot"
    else:
        tier, label, unclassified = 3, "general_empirical_unclassified", True
        study_design_axis = "empirical_unclassified"
        maturity_axis = "TRL_unverified_conservative_tier3"

    scope_limits_detected = sorted({m.group(0).lower() for m in SOURCE_SCOPE_LIMIT_RE.finditer(present_blob)})[:10]
    return {
        "tier": tier,
        "label": label,
        "study_design_axis": study_design_axis,
        "translational_maturity_axis": maturity_axis,
        "primary_boundary": primary_boundary,
        "model_boundaries": boundaries,
        "background_tier_mentions": background_tier_mentions,
        "source_scope_limits": scope_limits_detected,
        "unclassified_evidence_warning": unclassified,
    }


def audit_evidence_ladder(
    src_info: dict[str, Any],
    tgt_txt: str,
    *,
    require_boundary: bool,
    boundary_policy: str = "primary",
) -> dict[str, Any]:
    tier = src_info["tier"]
    boundaries = src_info["model_boundaries"]
    primary_b = src_info.get("primary_boundary")
    escalations: list[dict[str, str]] = []

    if tier <= 3:
        for m in CLAIM_CLINICAL_CURE_RE.finditer(tgt_txt):
            escalations.append({"rule": "CLINICAL_OR_COMMERCIAL_OVERCLAIM", "match": m.group(0)})
        for m in CLAIM_CAUSAL_PROOF_RE.finditer(tgt_txt):
            escalations.append({"rule": "CAUSAL_PROOF_OVERCLAIM", "match": m.group(0)})
        for m in PROMISE_RE.finditer(tgt_txt):
            win = tgt_txt[max(0, m.start() - 85) : min(len(tgt_txt), m.end() + 85)]
            if not HEDGE_QUALIFIER_RE.search(win):
                escalations.append({"rule": "UNHEDGED_TRANSLATIONAL_PROMISE", "match": m.group(0)})
    if tier == 1:
        for m in CLAIM_ANIMAL_OR_HUMAN_FROM_CELL_RE.finditer(tgt_txt):
            escalations.append({"rule": "IN_VITRO_TO_HUMAN_OVERCLAIM", "match": m.group(0)})

    # ALL tiers (INCLUDING Tier 4 RCTs!) retain population / indication extrapolation checks!
    for m in EXTRAPOLATION_UNIVERSAL_RE.finditer(tgt_txt):
        escalations.append({
            "rule": "POPULATION_OR_INDICATION_EXTRAPOLATION",
            "match": m.group(0),
            "tier": str(tier),
        })

    omitted_boundaries: list[str] = []
    if require_boundary and tier <= 3 and boundaries:
        check_list = [primary_b] if (boundary_policy == "primary" and primary_b) else boundaries
        for b in check_list:
            pat = BOUNDARY_MARKERS.get(b)
            if pat and not pat.search(tgt_txt):
                omitted_boundaries.append(b)

    return {
        "boundary_policy": boundary_policy,
        "claim_escalation_hits": escalations,
        "omitted_model_boundaries": omitted_boundaries,
        "model_boundary_preserved": len(omitted_boundaries) == 0,
    }


def audit_acronyms_and_glossary(tgt_txt: str, *, require_expansion: bool, glossary_path: Path | None = None) -> dict[str, Any]:
    prose = _mask_non_prose(tgt_txt)
    unexpanded: list[str] = []
    seen: set[str] = set()
    if require_expansion:
        for m in ACRONYM_RE.finditer(prose):
            acro = m.group(1)
            if acro in seen or acro in COMMON_ACRONYMS:
                seen.add(acro)
                continue
            seen.add(acro)
            win = prose[max(0, m.start() - 80) : min(len(prose), m.end() + 80)]
            pat_exp = re.compile(
                rf"(?:[\u4e00-\u9fff][\u4e00-\u9fff0-9\-·\s]{{1,28}}|[A-Za-z][A-Za-z0-9 -]{{4,60}})\s*[（(]\s*{re.escape(acro)}\s*[)）]|"
                rf"{re.escape(acro)}\s*[（(][^)）]{{3,60}}[)）]"
            )
            if not pat_exp.search(win):
                unexpanded.append(acro)

    glossary_violations: list[dict[str, Any]] = []
    glossary_checked = 0
    if glossary_path and glossary_path.is_file():
        gdata = json.loads(glossary_path.read_text(encoding="utf-8"))
        terms = gdata.get("terms", gdata) if isinstance(gdata, dict) else gdata
        if isinstance(terms, list):
            for entry in terms:
                if not isinstance(entry, dict):
                    continue
                glossary_checked += 1
                for bad in (entry.get("disallowed_synonyms") or []):
                    if isinstance(bad, str) and bad.strip() and bad.strip() in prose:
                        glossary_violations.append({
                            "type": "disallowed_synonym_used",
                            "source_term": entry.get("source_term", ""),
                            "canonical_term": entry.get("canonical_term", ""),
                            "found": bad.strip(),
                        })
    return {"unexpanded_acronyms": unexpanded, "glossary_terms_checked": glossary_checked, "glossary_violations": glossary_violations}


def audit_prose_naturalness(tgt_txt: str, *, strict: bool = True) -> dict[str, Any]:
    prose = _mask_non_prose(tgt_txt)
    safe_prose = prose
    for term in SCIENTIFIC_WHITELIST:
        safe_prose = safe_prose.replace(term, " " * len(term))

    hard_blocks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    _line_no = lambda pos: tgt_txt.count("\n", 0, pos) + 1

    for category, terms in (("promotional_hype", HYPE_WORDS), ("corporate_jargon", CORP_JARGON), ("hard_stop_phrase", HARD_STOPS), ("ai_road_sign", AI_ROAD_SIGNS)):
        for term in terms:
            for m in re.finditer(re.escape(term), safe_prose, re.I):
                item = {"category": category, "line": _line_no(m.start()), "match": m.group(0)}
                if strict or category in {"promotional_hype", "corporate_jargon"}:
                    hard_blocks.append(item)
                else:
                    warnings.append(item)

    # Calibrated: warn on false-pivot clichés & whitelist legitimate Chinese scientific contrasts
    for pat in FALSE_PIVOT_PATTERNS:
        for m in pat.finditer(safe_prose):
            frag = m.group(0)
            if SCIENTIFIC_CONTRAST_WHITELIST.search(frag):
                continue
            warnings.append({"category": "false_pivot_style_warning", "line": _line_no(m.start()), "match": frag[:48]})

    em_dashes = list(re.finditer(r"——", safe_prose))
    if len(em_dashes) >= 2:
        hard_blocks.append({"category": "em_dash_overuse", "line": _line_no(em_dashes[0].start()), "match": f"{len(em_dashes)} occurrences of '——'"})

    for pat in NOMINALIZATION_PATTERNS:
        for m in pat.finditer(safe_prose):
            warnings.append({"category": "heavy_nominalization", "line": _line_no(m.start()), "match": m.group(0)[:36]})

    zh_sents = [len(re.findall(r"[\u4e00-\u9fff]", s)) for s in re.split(r"[。！？!?]+", prose) if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 4]
    en_sents = [len(re.findall(r"[A-Za-z]+", s)) for s in re.split(r"[.!?]+", prose) if len(re.findall(r"[A-Za-z]+", s)) >= 4]
    avg_zh = round(sum(zh_sents) / len(zh_sents), 1) if zh_sents else 0.0
    avg_en = round(sum(en_sents) / len(en_sents), 1) if en_sents else 0.0
    sent_cv = None
    sample_lens = zh_sents if len(zh_sents) >= len(en_sents) else en_sents
    if len(sample_lens) >= 6:
        mean_l = sum(sample_lens) / len(sample_lens)
        if mean_l > 0:
            sent_cv = round(math.sqrt(sum((x - mean_l) ** 2 for x in sample_lens) / len(sample_lens)) / mean_l, 3)
            if sent_cv < 0.30:
                warnings.append({"category": "uniform_sentence_cadence", "line": 1, "match": f"sentence_length_cv={sent_cv} (<0.30)"})

    return {
        "prose_hard_blocks": hard_blocks, "prose_warnings": warnings,
        "readability": {"zh_sentence_count": len(zh_sents), "avg_zh_sentence_chars": avg_zh, "en_sentence_count": len(en_sents), "avg_en_sentence_words": avg_en, "sentence_length_cv": sent_cv},
    }


def audit_paragraph_trace(trace_path: Path | None) -> dict[str, Any]:
    if not trace_path or not trace_path.is_file():
        return {"provided": False, "trace_ok": True, "trace_entries": 0, "action_counts": {}, "schema_errors": [], "critical_omissions": [], "entries": []}
    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    items = raw.get("paragraphs", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        return {"provided": True, "trace_ok": False, "trace_entries": 0, "action_counts": {}, "schema_errors": ["paragraph_trace.json contains no paragraph entries."], "critical_omissions": [], "entries": []}

    schema_errors: list[str] = []
    critical_omissions: list[dict[str, Any]] = []
    action_counts = {"KEEP": 0, "COMPRESS": 0, "OMIT_REDUNDANCY": 0}
    valid_actions = set(action_counts.keys())
    all_retained_blob = " ".join(
        " ".join(str(x) for x in (it.get("retained_facts") or []))
        for it in items if isinstance(it, dict) and it.get("action") in {"KEEP", "COMPRESS"}
    ).lower()

    for idx, it in enumerate(items, 1):
        if not isinstance(it, dict):
            schema_errors.append(f"Entry #{idx} is not a JSON object.")
            continue
        pid = it.get("paragraph_id") or f"P{idx:02d}"
        sec, act, reason = it.get("source_section"), it.get("action"), it.get("keep_or_omit_reason")
        if not sec or not reason:
            schema_errors.append(f"{pid}: missing source_section or keep_or_omit_reason.")
        if act not in valid_actions:
            schema_errors.append(f"{pid}: invalid action {act!r}.")
        else:
            action_counts[act] += 1
        if act == "OMIT_REDUNDANCY":
            omitted_blob = " ".join(str(x) for x in (it.get("omitted_items") or [])) + " " + str(it.get("source_excerpt") or "")
            m_crit = CRITICAL_LIMITATION_RE.search(omitted_blob)
            if m_crit and m_crit.group(0).lower() not in all_retained_blob:
                critical_omissions.append({"paragraph_id": pid, "source_section": sec or "", "critical_signal": m_crit.group(0), "reason_given": reason or ""})

    return {
        "provided": True, "trace_ok": len(schema_errors) == 0 and len(critical_omissions) == 0,
        "trace_entries": len(items), "action_counts": action_counts,
        "schema_errors": schema_errors, "critical_omissions": critical_omissions, "entries": items,
    }


def extract_source_brief(
    source_path: Path,
    *,
    audience: str = "public_science",
    out_json: Path | None = None,
    emit_trace_skeleton: Path | None = None,
    emit_glossary_draft: Path | None = None,
    extracted_source_out: Path | None = None,
) -> dict[str, Any]:
    """Automated /input extractor generating source_brief.json, paragraph_trace skeleton, and glossary draft."""
    src_txt, rmeta = read_document_text(source_path)
    if extracted_source_out:
        extracted_source_out.parent.mkdir(parents=True, exist_ok=True)
        extracted_source_out.write_text(src_txt, encoding="utf-8", newline="\n")

    ev = classify_evidence_tier(src_txt)
    figs = sorted({m.group(1).upper() for m in FIG_ANCHOR_RE.finditer(src_txt)})
    tabs = sorted({m.group(1).upper() for m in TAB_ANCHOR_RE.finditer(src_txt)})
    pvals = [m.group(0) for m in P_VALUE_RE.finditer(src_txt)][:20]
    samples = [m.group(0) for m in SAMPLE_SIZE_RE.finditer(src_txt)][:15]
    baselines = sorted({m.group(0) for m in BASELINE_RE.finditer(src_txt)})[:15]
    contradictions = _scan_source_internal_contradictions(src_txt)

    # Extract candidate domain acronyms (excluding common acronyms)
    acros: list[str] = []
    for m in ACRONYM_RE.finditer(_mask_non_prose(src_txt)):
        a = m.group(1)
        if a not in COMMON_ACRONYMS and a not in acros and len(acros) < 25:
            acros.append(a)

    # Segment source into logical sections & paragraphs for automatic trace skeleton
    cur_sec = "Document Opening"
    para_entries: list[dict[str, Any]] = []
    limitation_excerpts: list[str] = []
    raw_blocks = [b.strip() for b in re.split(r"\n\s*\n+", src_txt) if b.strip()]
    # Sub-split long single-newline PDF page blocks into ~600-char logical chunks
    expanded_blocks: list[str] = []
    for blk in raw_blocks:
        if blk.startswith("<!-- Page") and len(blk) < 40:
            expanded_blocks.append(blk)
        elif len(blk) > 900:
            cur_chunk: list[str] = []
            cur_len = 0
            for ln in blk.splitlines():
                s_ln = ln.strip()
                if not s_ln:
                    continue
                if cur_len >= 550 and re.search(r"[.!?。！？]$", cur_chunk[-1] if cur_chunk else ""):
                    expanded_blocks.append("\n".join(cur_chunk))
                    cur_chunk, cur_len = [s_ln], len(s_ln)
                else:
                    cur_chunk.append(s_ln)
                    cur_len += len(s_ln) + 1
            if cur_chunk:
                expanded_blocks.append("\n".join(cur_chunk))
        else:
            expanded_blocks.append(blk)

    p_counter = 0
    for blk in expanded_blocks:
        if re.match(r"^#+\s+\S+", blk) and len(blk) < 120:
            cur_sec = blk.lstrip("# ").strip()
            continue
        if blk.startswith("<!-- Page") and len(blk) < 40:
            m_pg = re.search(r"Page\s+(\d+)", blk)
            if m_pg:
                cur_sec = f"Page {m_pg.group(1)}"
            continue
        if len(blk) < 35:
            continue
        p_counter += 1
        if p_counter > 45:
            break
        has_lim = bool(CRITICAL_LIMITATION_RE.search(blk))
        has_num = bool(extract_numeric_facts(blk))
        has_disp = bool(FIG_ANCHOR_RE.search(blk) or TAB_ANCHOR_RE.search(blk))
        if has_lim and len(limitation_excerpts) < 10:
            limitation_excerpts.append(blk[:220])
        if has_lim or has_num or has_disp:
            act = "KEEP" if (has_lim or (has_num and has_disp)) else "COMPRESS"
            reason = "Auto-flagged for retention/compression: contains primary data, display anchor, or scope limitation."
        else:
            act = "COMPRESS" if len(blk) > 120 else "OMIT_REDUNDANCY"
            reason = "Auto-classified background/transitional block; confirm during drafting."
        nums_found = [f["raw"] for f in extract_numeric_facts(blk)[:5]]
        para_entries.append({
            "paragraph_id": f"SRC-P{p_counter:02d}",
            "source_section": cur_sec,
            "source_excerpt": " ".join(blk.split())[:220],
            "action": act,
            "target_location": "adapted_article.md#section-mapped",
            "retained_facts": nums_found + ([f"Display ref present"] if has_disp else []),
            "omitted_items": [],
            "keep_or_omit_reason": reason,
            "author_check_needed": has_lim,
        })

    brief = {
        "schema_version": "source_brief/v1",
        "source_file": source_path.name,
        "audience": audience,
        "source_read_metadata": rmeta,
        "source_evidence_tier": ev,
        "displays_and_citations": {
            "figures": figs, "tables": tabs,
            "numeric_citations": len(CITE_NUM_RE.findall(src_txt)),
            "author_year_citations": len(CITE_AY_RE.findall(src_txt)),
        },
        "key_statistics_preview": {"p_values": pvals, "sample_sizes": samples, "baselines": baselines},
        "source_internal_contradictions": contradictions,
        "candidate_acronyms": acros,
        "limitation_excerpts": limitation_excerpts,
        "segmented_paragraphs_count": len(para_entries),
    }
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if emit_trace_skeleton:
        emit_trace_skeleton.parent.mkdir(parents=True, exist_ok=True)
        emit_trace_skeleton.write_text(
            json.dumps({"source_file": source_path.name, "audience": audience, "paragraphs": para_entries}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if emit_glossary_draft:
        emit_glossary_draft.parent.mkdir(parents=True, exist_ok=True)
        g_terms = [
            {"source_term": a, "canonical_term": f"{a} (expand on first use)", "disallowed_synonyms": []}
            for a in acros[:15]
        ]
        emit_glossary_draft.write_text(json.dumps({"source_file": source_path.name, "terms": g_terms}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return brief


def audit_genre_length_and_structure(tgt_txt: str, profile: dict[str, Any], *, max_words: int | None = None, max_chars: int | None = None) -> dict[str, Any]:
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", tgt_txt))
    en_words = len(re.findall(r"[A-Za-z0-9_'-]+", tgt_txt))
    is_zh = zh_chars >= en_words
    eff_max_chars = max_chars if max_chars is not None else int(profile.get("default_max_zh_chars", 3000))
    eff_max_words = max_words if max_words is not None else int(profile.get("default_max_en_words", 1500))

    length_exceeded = False
    length_detail = ""
    if max_chars is not None and zh_chars > max_chars:
        length_exceeded, length_detail = True, f"Chinese char count {zh_chars} exceeds explicit max_chars {max_chars}"
    elif max_words is not None and en_words > max_words:
        length_exceeded, length_detail = True, f"English word count {en_words} exceeds explicit max_words {max_words}"
    elif is_zh and zh_chars > eff_max_chars:
        length_detail = f"Chinese char count {zh_chars} exceeds genre ceiling {eff_max_chars}"
    elif (not is_zh) and en_words > eff_max_words:
        length_detail = f"English word count {en_words} exceeds genre ceiling {eff_max_words}"

    present_sections = [n for n, p in profile.get("required_sections", ()) if re.search(p, tgt_txt, re.I)]
    missing_sections = [n for n, p in profile.get("required_sections", ()) if not re.search(p, tgt_txt, re.I)]
    return {
        "zh_chars": zh_chars, "en_words": en_words, "dominant_language": "zh" if is_zh else "en",
        "max_chars_limit": eff_max_chars, "max_words_limit": eff_max_words,
        "explicit_ceiling_exceeded": length_exceeded, "length_detail": length_detail,
        "present_sections": present_sections, "missing_sections": missing_sections,
    }


def render_side_by_side_review(report: dict[str, Any], trace_entries: list[dict[str, Any]]) -> str:
    ev = report["source_evidence_tier"]
    disp = report["figure_table_citation_preservation"]
    lines = [
        "# Translational Science Writing — Side-by-Side Author Review", "",
        "## 1. Audit Summary", "",
        "| Dimension | Status | Detail |", "| --- | --- | --- |",
        f"| Overall Gate | {'PASS' if report['passed'] else 'FAIL'} | {len(report['errors'])} blocking error(s), {len(report['warnings'])} warning(s) |",
        f"| Source Reader | `{report['source_read_metadata'].get('format')}` | extractor=`{report['source_read_metadata'].get('extractor')}`, chars={report['source_read_metadata'].get('chars')} |",
        f"| Target Audience | `{report['audience']}` | {report['audience_label']} |",
        f"| Source Evidence Tier | Tier {ev['tier']} (`{ev['label']}`) | Primary boundary: `{ev.get('primary_boundary')}` |",
        f"| Claim Escalation & Boundary | {'PASS' if (not report['claim_escalation_hits'] and report['model_boundary_preserved']) else 'FAIL'} | Escalations: {len(report['claim_escalation_hits'])}; Omitted boundaries: {report['omitted_model_boundaries']} |",
        f"| Numeric, Unit & p-Value Fidelity | {'PASS' if (not report['ungrounded_target_numbers'] and not report['p_value_strengthened'] and not report['numeric_unit_mismatches']) else 'FAIL'} | Ungrounded: {report['ungrounded_target_numbers']}; Unit mismatches: {len(report['numeric_unit_mismatches'])}; p-issues: {len(report['p_value_violations'])} |",
        f"| Figure / Table / Citation Preservation | {'PASS' if not disp['blocking_errors'] else 'FAIL'} | Source figs/tabs: {len(disp['source_figures'])}/{len(disp['source_tables'])}; Target figs/tabs: {len(disp['target_figures'])}/{len(disp['target_tables'])} |",
        f"| Acronyms & Glossary | {'PASS' if (not report['unexpanded_acronyms'] and not report['glossary_violations']) else 'FAIL'} | Unexpanded: {report['unexpanded_acronyms']}; Glossary issues: {len(report['glossary_violations'])} |",
        f"| Natural Prose (Non-AI) | {'PASS' if not report['prose_hard_blocks'] else 'FAIL'} | Hard blocks: {len(report['prose_hard_blocks'])}; Style warnings: {len(report['prose_warnings'])} |", "",
    ]
    if report["errors"]:
        lines += ["## 2. Blocking Issues to Fix Before Delivery", ""] + [f"- {e}" for e in report["errors"]] + [""]
    if report.get("source_internal_contradictions"):
        lines += ["## 2b. Source Internal Contradictions (Flagged for Author Confirmation)", ""]
        for c in report["source_internal_contradictions"]:
            lines.append(f"- `{c['metric']}` ({c['unit']}): conflicting source values `{c['values']}`")
        lines.append("")
    lines += ["## 3. Paragraph-by-Paragraph Keep / Compress / Omit Review", ""]
    if trace_entries:
        lines += ["| ID | Source Section | Action | Retained Facts | Omitted Items | Rationale | Author Check |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for it in trace_entries:
            esc_c = lambda v: str(v).replace("|", "/")
            ret = "; ".join(str(x) for x in (it.get("retained_facts") or []))
            omi = "; ".join(str(x) for x in (it.get("omitted_items") or []))
            lines.append(f"| {esc_c(it.get('paragraph_id',''))} | {esc_c(it.get('source_section',''))} | `{esc_c(it.get('action',''))}` | {esc_c(ret) or '-'} | {esc_c(omi) or '-'} | {esc_c(it.get('keep_or_omit_reason',''))} | {'YES' if it.get('author_check_needed') else 'No'} |")
    else:
        lines.append("No `paragraph_trace.json` entries supplied.")
    return "\n".join(lines) + "\n"


def audit_translation_fidelity(
    source_path: Path,
    target_path: Path,
    *,
    audience: str = "public_science",
    glossary_path: Path | None = None,
    trace_json_path: Path | None = None,
    review_md_path: Path | None = None,
    extracted_source_out: Path | None = None,
    max_words: int | None = None,
    max_chars: int | None = None,
    boundary_policy: str | None = None,
    require_figure_refs: bool = False,
    strict_structure: bool = False,
) -> dict[str, Any]:
    profile = AUDIENCE_PROFILES[audience]
    src_txt, src_meta = read_document_text(source_path)
    tgt_txt, tgt_meta = read_document_text(target_path)
    if extracted_source_out:
        extracted_source_out.parent.mkdir(parents=True, exist_ok=True)
        extracted_source_out.write_text(src_txt, encoding="utf-8", newline="\n")

    eff_boundary_policy = boundary_policy or profile.get("default_boundary_policy", "primary")
    num_res = _verify_numbers(src_txt, tgt_txt)
    src_ev = classify_evidence_tier(src_txt)
    src_contradictions = _scan_source_internal_contradictions(src_txt)
    ladder_res = audit_evidence_ladder(src_ev, tgt_txt, require_boundary=profile["require_model_boundary"], boundary_policy=eff_boundary_policy)
    term_res = audit_acronyms_and_glossary(tgt_txt, require_expansion=profile["require_acronym_expansion"], glossary_path=glossary_path)
    prose_res = audit_prose_naturalness(tgt_txt, strict=profile["prose_strict"])
    trace_res = audit_paragraph_trace(trace_json_path)
    disp_res = audit_figure_table_citation_preservation(
        src_txt, tgt_txt, strict_refs=(require_figure_refs or profile.get("require_figure_refs_strict", False)), trace_entries=trace_res["entries"]
    )
    genre_res = audit_genre_length_and_structure(tgt_txt, profile, max_words=max_words, max_chars=max_chars)

    errors: list[str] = []
    warnings: list[str] = []
    if not src_txt.strip() or not tgt_txt.strip():
        errors.append("EMPTY_SOURCE_OR_TARGET: source or target text is empty after extraction.")
    if num_res["ungrounded_target_numbers"]:
        errors.append(f"Target introduces ungrounded numbers: {num_res['ungrounded_target_numbers']}")
    if num_res["numeric_unit_mismatches"]:
        errors.append(f"NUMERIC_UNIT_MISMATCH: {num_res['numeric_unit_mismatches']}")
    if num_res.get("group_or_metric_swaps"):
        errors.append(f"GROUP_OR_METRIC_SWAP_MISMATCH: {num_res['group_or_metric_swaps']}")
    if num_res["p_value_strengthened"]:
        codes = sorted({v.get("code", "P_VALUE_VIOLATION") for v in num_res["p_value_violations"]})
        errors.append(f"{'/'.join(codes)}: {num_res['p_value_violations']}")
    if profile["require_baseline_if_present"] and num_res["source_has_baseline"] and not num_res["target_has_baseline"]:
        errors.append("COMPARISON_BASELINE_OMITTED: source reports a comparison baseline/control arm absent from adapted draft.")
    if profile["require_sample_size_if_present"] and num_res["source_has_sample_size"] and not num_res["target_has_sample_size"]:
        warnings.append("SAMPLE_SIZE_OMITTED: source reports sample size absent from adapted draft.")

    errors.extend(disp_res["blocking_errors"])
    warnings.extend(disp_res["warnings"])

    if ladder_res["claim_escalation_hits"]:
        errors.append(f"CLAIM_LEVEL_ESCALATION over Tier {src_ev['tier']} ({src_ev['label']}): {[h['rule'] + ':' + h['match'] for h in ladder_res['claim_escalation_hits']]}")
    if ladder_res["omitted_model_boundaries"]:
        errors.append(f"MODEL_BOUNDARY_OMITTED: adapted draft dropped required source boundary {ladder_res['omitted_model_boundaries']}")
    if src_ev.get("unclassified_evidence_warning"):
        warnings.append("Source did not match explicit Tier 1-4 biomedical/engineering keywords; defaulted conservatively to Tier 3.")
    if src_contradictions:
        warnings.append(f"Source contains {len(src_contradictions)} potential internal numeric contradiction(s); surface both values for author confirmation.")

    if term_res["unexpanded_acronyms"]:
        errors.append(f"Acronyms unexpanded on first use for {audience}: {term_res['unexpanded_acronyms']}")
    if term_res["glossary_violations"]:
        errors.append(f"Glossary lock violated: {[v['found'] for v in term_res['glossary_violations']]}")
    if prose_res["prose_hard_blocks"]:
        errors.append(f"Prose hard-block violations ({len(prose_res['prose_hard_blocks'])}): {[b['category'] + ':' + b['match'] for b in prose_res['prose_hard_blocks'][:6]]}")

    rd = prose_res["readability"]
    if rd["avg_zh_sentence_chars"] > profile["max_avg_zh_sent_len"]:
        warnings.append(f"Average Chinese sentence length {rd['avg_zh_sentence_chars']} exceeds {audience} target {profile['max_avg_zh_sent_len']}.")
    if rd["avg_en_sentence_words"] > profile["max_avg_en_sent_words"]:
        warnings.append(f"Average English sentence length {rd['avg_en_sentence_words']} exceeds {audience} target {profile['max_avg_en_sent_words']}.")

    if trace_res["schema_errors"]:
        errors.extend(trace_res["schema_errors"])
    if trace_res["critical_omissions"]:
        errors.append(f"CRITICAL_LIMITATION_OMITTED in paragraph_trace.json: {[c['paragraph_id'] + ':' + c['critical_signal'] for c in trace_res['critical_omissions']]}")
    if genre_res["explicit_ceiling_exceeded"]:
        errors.append(f"LENGTH_CEILING_EXCEEDED: {genre_res['length_detail']}")
    elif genre_res["length_detail"]:
        warnings.append(f"GENRE_LENGTH_ADVISORY: {genre_res['length_detail']}")
    if genre_res["missing_sections"]:
        msg = f"GENRE_REQUIRED_SECTIONS_MISSING ({audience}): {genre_res['missing_sections']}"
        (errors if strict_structure else warnings).append(msg)

    report = {
        "passed": len(errors) == 0,
        "audience": audience,
        "audience_label": profile["label"],
        "source_read_metadata": src_meta,
        "target_read_metadata": tgt_meta,
        "genre_structure_checks": genre_res,
        "source_evidence_tier": src_ev,
        "source_internal_contradictions": src_contradictions,
        "claim_escalation_hits": ladder_res["claim_escalation_hits"],
        "omitted_model_boundaries": ladder_res["omitted_model_boundaries"],
        "model_boundary_preserved": ladder_res["model_boundary_preserved"],
        "figure_table_citation_preservation": disp_res,
        "ungrounded_target_numbers": num_res["ungrounded_target_numbers"],
        "numeric_unit_mismatches": num_res["numeric_unit_mismatches"],
        "group_or_metric_swaps": num_res.get("group_or_metric_swaps", []),
        "bilingual_magnitude_matches": num_res["bilingual_magnitude_matches"],
        "verified_derived_ratios": num_res["verified_derived_ratios"],
        "p_value_strengthened": num_res["p_value_strengthened"],
        "p_value_violations": num_res["p_value_violations"],
        "comparison_baseline_preserved": (not num_res["source_has_baseline"]) or num_res["target_has_baseline"],
        "sample_size_preserved": (not num_res["source_has_sample_size"]) or num_res["target_has_sample_size"],
        "unexpanded_acronyms": term_res["unexpanded_acronyms"],
        "glossary_terms_checked": term_res["glossary_terms_checked"],
        "glossary_violations": term_res["glossary_violations"],
        "prose_hard_blocks": prose_res["prose_hard_blocks"],
        "prose_warnings": prose_res["prose_warnings"],
        "readability": rd,
        "trace_entries": trace_res["trace_entries"],
        "trace_action_counts": trace_res["action_counts"],
        "critical_omissions": trace_res["critical_omissions"],
        "errors": errors,
        "warnings": warnings,
    }
    if review_md_path:
        review_md_path.parent.mkdir(parents=True, exist_ok=True)
        review_md_path.write_text(render_side_by_side_review(report, trace_res["entries"]), encoding="utf-8", newline="\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit translational science writing fidelity or extract /input source brief & skeletons.")
    parser.add_argument("--source", required=True, type=Path, help="Source scientific manuscript (.pdf/.docx/.md/.txt).")
    parser.add_argument("--target", type=Path, default=None, help="Audience-adapted deliverable (required unless --extract-source-brief).")
    parser.add_argument("--audience", default="public_science", choices=sorted(AUDIENCE_PROFILES.keys()))
    parser.add_argument("--extract-source-brief", action="store_true", help="Extract facts, evidence tier, displays, and optional trace/glossary skeletons from --source.")
    parser.add_argument("--emit-trace-skeleton", type=Path, default=None, help="Write auto-segmented paragraph_trace.json skeleton.")
    parser.add_argument("--emit-glossary-draft", type=Path, default=None, help="Write candidate glossary_lock.json draft.")
    parser.add_argument("--extracted-source-out", type=Path, default=None, help="Optional cache path for extracted UTF-8 text from PDF/DOCX source.")
    parser.add_argument("--glossary-json", type=Path, default=None, help="Optional glossary lock JSON.")
    parser.add_argument("--trace-json", type=Path, default=None, help="Optional paragraph_trace.json.")
    parser.add_argument("--max-words", type=int, default=None)
    parser.add_argument("--max-chars", type=int, default=None)
    parser.add_argument("--boundary-policy", choices=["primary", "all"], default=None)
    parser.add_argument("--require-figure-refs", action="store_true")
    parser.add_argument("--strict-structure", action="store_true")
    parser.add_argument("--out-json", required=True, type=Path, help="Output JSON report or source brief.")
    parser.add_argument("--review-md", type=Path, default=None, help="Optional side-by-side author review Markdown output.")
    args = parser.parse_args(argv)

    if args.extract_source_brief:
        brief = extract_source_brief(
            args.source, audience=args.audience, out_json=args.out_json,
            emit_trace_skeleton=args.emit_trace_skeleton, emit_glossary_draft=args.emit_glossary_draft,
            extracted_source_out=args.extracted_source_out,
        )
        print(json.dumps({"ok": True, "mode": "extract_source_brief", "source_format": brief["source_read_metadata"]["format"], "extractor": brief["source_read_metadata"]["extractor"], "evidence_tier": brief["source_evidence_tier"], "paragraphs_segmented": brief["segmented_paragraphs_count"]}, ensure_ascii=False, indent=2))
        return 0

    if not args.target:
        parser.error("--target is required unless --extract-source-brief is set.")

    rep = audit_translation_fidelity(
        args.source, args.target, audience=args.audience, glossary_path=args.glossary_json,
        trace_json_path=args.trace_json, review_md_path=args.review_md,
        extracted_source_out=args.extracted_source_out, max_words=args.max_words,
        max_chars=args.max_chars, boundary_policy=args.boundary_policy,
        require_figure_refs=args.require_figure_refs, strict_structure=args.strict_structure,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "passed": rep["passed"], "audience": rep["audience"],
        "source_extractor": rep["source_read_metadata"]["extractor"],
        "source_evidence_tier": rep["source_evidence_tier"],
        "errors": rep["errors"], "warnings": rep["warnings"],
    }, ensure_ascii=False, indent=2))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
