#!/usr/bin/env python3
"""Translational science writing fidelity, 4-tier evidence ladder, numeric/magnitude
conservation, glossary lock, natural non-AI prose, and paragraph-trace auditor.

Zero external dependencies (Python 3.12 standard library only).
"""

from __future__ import annotations

import argparse
import json
import math
import re
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
            ("limitations_scope", r"局限|适用边界|注意|尚待|未验证|limitation|caveat|what\s+this\s+does\s+not|next\s+step"),
        ),
        "require_model_boundary": True,
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
            ("value_or_application", r"应用场景|转化价值|产业|潜在应用|application|value\s+proposition|translation"),
            ("readiness_boundary", r"成熟度|局限|风险|临床前|尚待验证|TRL|readiness|risk|limitation|boundary"),
        ),
        "require_model_boundary": True,
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
            ("recommendation_or_boundary", r"建议|政策|边界|不确定性|recommendation|policy\s+implication|uncertainty|limitation"),
        ),
        "require_model_boundary": True,
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
            ("next_plan_and_boundary", r"下一步|拟开展|局限|风险|next\s+step|proposed|limitation|risk"),
        ),
        "require_model_boundary": True,
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
            ("methods_and_metrics", r"方法|指标|对照|样本|method|metric|benchmark|control|design"),
            ("limitations_and_next", r"局限|边界|风险|下一步|limitation|boundary|risk|caveat"),
        ),
        "require_model_boundary": True,
        "require_baseline_if_present": True,
        "require_sample_size_if_present": True,
        "prose_strict": False,
    },
}

# Numeric extraction with bilingual magnitude awareness
NUM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_\\-])(?P<pct_pre>百分之\s*)?"
    r"(?P<val>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"(?P<mag>\s*(?:万|亿|千|thousand|million|billion|(?<=\d)[kMB]\b))?"
    r"(?P<pct_suf>\s*[%％])?"
    r"(?P<fold>\s*(?:倍|x|×))?",
    re.I,
)
BIO_OR_ID_TOKEN_RE = re.compile(
    r"\b(?:IL-\d+|TNF-\w+|NF-κB|NF-kB|TGF-β\d*|CD\d+|TP\d+|KRAS|BRCA\d|A549|HEK293T?|HeLa|"
    r"C57BL/6|BALB/c|COVID-19|SARS-CoV-2|Fig(?:ure)?\.?\s*\d+(?:\.\d+)?|Table\s*\d+(?:\.\d+)?|"
    r"Phase\s*(?:I{1,3}|IV|[1-4])|Tier\s*[1-4]|Section\s*\d+(?:\.\d+)?|p\s*[<≤=]\s*0\.\d+)\b",
    re.I,
)
LINE_LIST_PREFIX_RE = re.compile(r"^\s*(?:#+\s*)?\d{1,2}[.、)]\s+", re.MULTILINE)
P_VALUE_RE = re.compile(r"\b[pP]\s*(?P<op><=|≤|<|=)\s*(?P<val>0?\.\d+(?:[eE]-?\d+)?|\d+(?:\.\d+)?[eE]-\d+)")
SAMPLE_SIZE_RE = re.compile(
    r"\b[nN]\s*=\s*\d+|\d+\s*(?:patients|subjects|participants|mice|rats|samples|replicates|cohorts|"
    r"例患者|名受试者|例样本|只小鼠|只大鼠|个样本|次重复)",
    re.I,
)
BASELINE_RE = re.compile(
    r"\b(?:control\s+group|vehicle\s+control|solvent\s+control|placebo|standard\s+of\s+care|"
    r"baseline|DMSO|untreated\s+control|sham\s+control)\b|"
    r"对照组|溶剂对照|空白对照|阴性对照|阳性对照|安慰剂|基线(?:水平|值)?|常规(?:疗法|治疗)",
    re.I,
)

# 4-tier evidence ladder patterns
TIER4_PATTERNS = re.compile(
    r"\b(?:randomized\s+controlled\s+trial|double-blind\s+placebo-controlled|phase\s*(?:III|3)\s+clinical\s+trial|"
    r"multicenter\s+RCT)\b|随机双盲对照临床试验|III\s*期临床试验|多中心随机对照试验",
    re.I,
)
TIER3_PATTERNS = re.compile(
    r"\b(?:retrospective|cross-sectional|observational\s+cohort|case-control|correlat(?:ed|ion|es)|"
    r"associat(?:ed|ion)|single-center|prospective\s+cohort)\b|"
    r"回顾性|横断面|观察性队列|病例对照|相关性|相关关系|单中心|关联性",
    re.I,
)
TIER2_PATTERNS = re.compile(
    r"\b(?:in\s+mice|mouse\s+model|murine|xenograft|rat\s+model|zebrafish|organoid|preclinical|in\s+vivo\s+animal)\b|"
    r"小鼠|大鼠|动物模型|动物实验|异种移植|类器官|临床前",
    re.I,
)
TIER1_PATTERNS = re.compile(
    r"\b(?:in\s+vitro|cell\s+line|cultured\s+cells|A549|HeLa|HEK293|molecular\s+docking|in\s+silico|"
    r"computational\s+simulation|bioinformatics\s+prediction)\b|"
    r"体外(?:细胞|实验)?|细胞系|培养细胞|分子对接|计算模拟|生信预测",
    re.I,
)

# Escalation patterns by target claim level
CLAIM_CLINICAL_CURE_RE = re.compile(
    r"\b(?:cures?\s+(?:human|patients|cancer|disease)|clinically\s+proven|ready\s+for\s+clinical\s+use|"
    r"replaces?\s+standard\s+therapy|guaranteed\s+efficacy|effective\s+treatment\s+for\s+patients|"
    r"breakthrough\s+cure)\b|"
    r"彻底治愈|有效治愈|治愈患者|已成熟临床应用|可直接用于临床(?:治疗)?|替代现有(?:临床)?疗法|"
    r"攻克(?:癌症|肿瘤|疾病)|惠及广大患者临床治愈|确保疗效|临床疗效确凿|改写医学史",
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

# Model boundary preservation markers expected in target text
BOUNDARY_MARKERS = {
    "in_vitro_cell": re.compile(r"\b(?:in\s+vitro|cell\s+line|cell\s+culture|cultured\s+cells|cellular\s+model)\b|体外|细胞系|细胞实验|细胞模型|培养细胞", re.I),
    "mouse_or_animal": re.compile(r"\b(?:mice|mouse|murine|rat|animal\s+model|preclinical|xenograft|not\s+yet\s+tested\s+in\s+humans)\b|小鼠|大鼠|动物模型|动物实验|临床前|尚未(?:进入|开展)人体", re.I),
    "observational_correlational": re.compile(r"\b(?:correlat|associat|observational|retrospective|not\s+causation|cannot\s+establish\s+causality|suggests?)\b|相关(?:性|关系)?|关联|观察性|回顾性|并非因果|不能证明因果|提示", re.I),
}

# Acronyms (supports scientific alphanumeric acronyms such as STAT3, HER2, PD-L1, JAK, TME)
ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,8}(?:-[A-Z0-9]{1,4}|\d{1,2})?)(?![A-Za-z0-9])")
COMMON_ACRONYMS = {
    "DNA", "RNA", "AI", "CT", "MRI", "WHO", "FDA", "GDP", "USA", "UK", "EU", "UN",
    "PDF", "HTML", "JSON", "CSV", "CLI", "API", "CI", "OR", "HR", "SD", "SE", "IQR",
    "II", "III", "IV", "VI", "PH", "UV", "IR", "LED", "LCD", "CPU", "GPU", "TRL",
}

# Prose hard blocks (adapted from human-writing + expression-core with scientific whitelist)
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
FALSE_PIVOT_PATTERNS = (
    re.compile(r"(?:并)?不是[^。！？\n]{1,80}而是"),
    re.compile(r"并非[^。！？\n]{1,80}而是"),
    re.compile(r"不在于[^。！？\n]{1,80}而在于"),
    re.compile(r"与其说[^。！？\n]{1,80}(?:不如|毋宁|倒不如)"),
    re.compile(r"表面(?:上)?[^。！？\n]{1,80}(?:其实|实际|实则)"),
    re.compile(r"看似[^。！？\n]{1,80}(?:其实|实际|实则)"),
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
    r"correlat|observational|retrospective|single-center|limitation|caveat|control\s+group|"
    r"vehicle|placebo|adverse|negative\s+result|not\s+significant)\b|"
    r"样本量|体外|细胞系|小鼠|大鼠|动物模型|临床前|相关性|回顾性|单中心|局限|不足|对照组|阴性结果|未显著|未验证",
    re.I,
)


def _mask_non_prose(text: str) -> str:
    masked = text
    for pat in (
        re.compile(r"```.*?```", re.DOTALL),
        re.compile(r"`[^`\n]*`"),
        re.compile(r"https?://[^\s)>]+"),
    ):
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
    if m == "千" or m in {"thousand", "k"}:
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


def extract_numeric_facts(text: str) -> list[dict[str, Any]]:
    """Extract normalized numeric tokens distinguishing counts, percentages, and magnitudes."""
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
        mult = _mag_multiplier(mag)
        scaled = base_num * mult
        # Skip plain 4-digit calendar years (1900-2099) if bare integer
        if not is_pct and not is_fold and not mag and "." not in raw_val and 1900 <= int(base_num) <= 2099:
            continue
        kind = "percent" if is_pct else ("fold" if is_fold else "value")
        facts.append({
            "raw": m.group(0).strip(),
            "kind": kind,
            "base": base_num,
            "multiplier": mult,
            "scaled": scaled,
            "start": m.start(),
            "end": m.end(),
        })
    return facts


def _numbers_match(a: float, b: float, tol_rel: float = 1e-4) -> bool:
    if a == b:
        return True
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom <= tol_rel


def _verify_numbers(src_txt: str, tgt_txt: str) -> dict[str, Any]:
    src_facts = extract_numeric_facts(src_txt)
    tgt_facts = extract_numeric_facts(tgt_txt)

    src_values = [f["scaled"] for f in src_facts if f["kind"] == "value"]
    src_pcts = [f["scaled"] for f in src_facts if f["kind"] == "percent"]
    src_folds = [f["scaled"] for f in src_facts if f["kind"] == "fold"]

    ungrounded: list[str] = []
    bilingual_equiv: list[dict[str, Any]] = []
    verified_derived: list[dict[str, Any]] = []

    for tf in tgt_facts:
        matched = False
        if tf["kind"] == "percent":
            if any(_numbers_match(tf["scaled"], sv) for sv in src_pcts):
                matched = True
            elif any(_numbers_match(tf["scaled"] / 100.0, sv) for sv in src_values):
                matched = True
                bilingual_equiv.append({"target": tf["raw"], "matched_decimal_ratio": round(tf["scaled"] / 100.0, 6)})
        elif tf["kind"] == "fold":
            if any(_numbers_match(tf["scaled"], sf) for sf in src_folds) or any(_numbers_match(tf["scaled"], sv) for sv in src_values):
                matched = True
        else:
            if any(_numbers_match(tf["scaled"], sv) for sv in src_values):
                matched = True
                if tf["multiplier"] != 1.0:
                    bilingual_equiv.append({"target": tf["raw"], "normalized_value": tf["scaled"]})

        # Check derived ratio/fold if nearby window gives traceable source numerator & denominator
        if not matched and tf["kind"] in {"percent", "fold"}:
            win = tgt_txt[max(0, tf["start"] - 70) : min(len(tgt_txt), tf["end"] + 70)]
            win_nums = [
                f["scaled"] for f in extract_numeric_facts(win)
                if f["kind"] == "value" and any(_numbers_match(f["scaled"], sv) for sv in src_values)
            ]
            for i in range(len(win_nums)):
                for j in range(len(win_nums)):
                    if i == j or win_nums[j] == 0:
                        continue
                    ratio = win_nums[i] / win_nums[j]
                    if tf["kind"] == "percent" and abs(ratio * 100.0 - tf["scaled"]) <= 0.6:
                        matched = True
                        verified_derived.append({
                            "target": tf["raw"],
                            "numerator": win_nums[i],
                            "denominator": win_nums[j],
                        })
                        break
                    if tf["kind"] == "fold" and abs(ratio - tf["scaled"]) <= 0.08:
                        matched = True
                        verified_derived.append({
                            "target": tf["raw"],
                            "numerator": win_nums[i],
                            "denominator": win_nums[j],
                        })
                        break
                if matched:
                    break

        if not matched:
            # Ignore small non-metric structural ordinals like 1, 2, 3 if bare single digit
            if tf["kind"] == "value" and tf["multiplier"] == 1.0 and tf["raw"] in {"1", "2", "3", "4", "5"}:
                continue
            ungrounded.append(tf["raw"])

    # Statistical p-value strengthening check
    src_pvals = [float(m.group("val")) for m in P_VALUE_RE.finditer(src_txt)]
    tgt_pvals = [float(m.group("val")) for m in P_VALUE_RE.finditer(tgt_txt)]
    p_value_strengthened = False
    p_detail = None
    if src_pvals and tgt_pvals:
        min_src_p = min(src_pvals)
        min_tgt_p = min(tgt_pvals)
        if min_tgt_p < min_src_p * 0.8:
            p_value_strengthened = True
            p_detail = {"source_min_p": min_src_p, "target_min_p": min_tgt_p}

    return {
        "ungrounded_target_numbers": sorted(set(ungrounded)),
        "bilingual_magnitude_matches": bilingual_equiv,
        "verified_derived_ratios": verified_derived,
        "p_value_strengthened": p_value_strengthened,
        "p_value_detail": p_detail,
        "source_has_sample_size": bool(SAMPLE_SIZE_RE.search(src_txt)),
        "target_has_sample_size": bool(SAMPLE_SIZE_RE.search(tgt_txt)),
        "source_has_baseline": bool(BASELINE_RE.search(src_txt)),
        "target_has_baseline": bool(BASELINE_RE.search(tgt_txt)),
    }


def classify_evidence_tier(src_txt: str) -> dict[str, Any]:
    """Classify highest source evidence tier (1..4) and active model boundaries."""
    boundaries: list[str] = []
    if TIER1_PATTERNS.search(src_txt):
        boundaries.append("in_vitro_cell")
    if TIER2_PATTERNS.search(src_txt):
        boundaries.append("mouse_or_animal")
    if TIER3_PATTERNS.search(src_txt):
        boundaries.append("observational_correlational")

    if TIER4_PATTERNS.search(src_txt):
        tier, label = 4, "rct_or_clinical_causal"
    elif "observational_correlational" in boundaries and "mouse_or_animal" not in boundaries and "in_vitro_cell" not in boundaries:
        tier, label = 3, "observational_correlational"
    elif "mouse_or_animal" in boundaries:
        tier, label = 2, "animal_or_preclinical"
    elif "in_vitro_cell" in boundaries:
        tier, label = 1, "in_vitro_or_computational"
    elif "observational_correlational" in boundaries:
        tier, label = 3, "observational_correlational"
    else:
        tier, label = 3, "observational_correlational"

    return {"tier": tier, "label": label, "model_boundaries": boundaries}


def audit_evidence_ladder(src_info: dict[str, Any], tgt_txt: str, require_boundary: bool) -> dict[str, Any]:
    tier = src_info["tier"]
    boundaries = src_info["model_boundaries"]
    escalations: list[dict[str, str]] = []

    if tier <= 3:
        for m in CLAIM_CLINICAL_CURE_RE.finditer(tgt_txt):
            escalations.append({"rule": "CLINICAL_CURE_OVERCLAIM", "match": m.group(0)})
        for m in CLAIM_CAUSAL_PROOF_RE.finditer(tgt_txt):
            escalations.append({"rule": "CAUSAL_PROOF_OVERCLAIM", "match": m.group(0)})
    if tier == 1:
        for m in CLAIM_ANIMAL_OR_HUMAN_FROM_CELL_RE.finditer(tgt_txt):
            escalations.append({"rule": "IN_VITRO_TO_HUMAN_OVERCLAIM", "match": m.group(0)})

    omitted_boundaries: list[str] = []
    if require_boundary and tier <= 3 and boundaries:
        for b in boundaries:
            pat = BOUNDARY_MARKERS.get(b)
            if pat and not pat.search(tgt_txt):
                omitted_boundaries.append(b)

    return {
        "claim_escalation_hits": escalations,
        "omitted_model_boundaries": omitted_boundaries,
        "model_boundary_preserved": len(omitted_boundaries) == 0,
    }


def audit_acronyms_and_glossary(
    tgt_txt: str,
    *,
    require_expansion: bool,
    glossary_path: Path | None = None,
) -> dict[str, Any]:
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
                disallowed = entry.get("disallowed_synonyms", []) or []
                for bad in disallowed:
                    if isinstance(bad, str) and bad.strip() and bad.strip() in prose:
                        glossary_violations.append({
                            "type": "disallowed_synonym_used",
                            "source_term": entry.get("source_term", ""),
                            "canonical_term": entry.get("canonical_term", ""),
                            "found": bad.strip(),
                        })

    return {
        "unexpanded_acronyms": unexpanded,
        "glossary_terms_checked": glossary_checked,
        "glossary_violations": glossary_violations,
    }


def audit_prose_naturalness(tgt_txt: str, *, strict: bool = True) -> dict[str, Any]:
    """Check for AI hype, corporate buzzwords, false-pivot formulas, road signs, and readability."""
    prose = _mask_non_prose(tgt_txt)
    # Mask scientific whitelist terms so they never trigger false-positive prose alerts
    safe_prose = prose
    for term in SCIENTIFIC_WHITELIST:
        safe_prose = safe_prose.replace(term, " " * len(term))

    hard_blocks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def _line_no(pos: int) -> int:
        return tgt_txt.count("\n", 0, pos) + 1

    for category, terms in (
        ("promotional_hype", HYPE_WORDS),
        ("corporate_jargon", CORP_JARGON),
        ("hard_stop_phrase", HARD_STOPS),
        ("ai_road_sign", AI_ROAD_SIGNS),
    ):
        for term in terms:
            for m in re.finditer(re.escape(term), safe_prose, re.I):
                item = {"category": category, "line": _line_no(m.start()), "match": m.group(0)}
                if strict or category in {"promotional_hype", "corporate_jargon"}:
                    hard_blocks.append(item)
                else:
                    warnings.append(item)

    for pat in FALSE_PIVOT_PATTERNS:
        for m in pat.finditer(safe_prose):
            item = {"category": "false_pivot_cliche", "line": _line_no(m.start()), "match": m.group(0)[:48]}
            if strict:
                hard_blocks.append(item)
            else:
                warnings.append(item)

    em_dashes = list(re.finditer(r"——", safe_prose))
    if len(em_dashes) >= 2:
        hard_blocks.append({
            "category": "em_dash_overuse",
            "line": _line_no(em_dashes[0].start()),
            "match": f"{len(em_dashes)} occurrences of '——'",
        })

    for pat in NOMINALIZATION_PATTERNS:
        for m in pat.finditer(safe_prose):
            warnings.append({"category": "heavy_nominalization", "line": _line_no(m.start()), "match": m.group(0)[:36]})

    # Sentence readability metrics
    zh_sents = [len(re.findall(r"[\u4e00-\u9fff]", s)) for s in re.split(r"[。！？!?]+", prose) if len(re.findall(r"[\u4e00-\u9fff]", s)) >= 4]
    en_sents = [len(re.findall(r"[A-Za-z]+", s)) for s in re.split(r"[.!?]+", prose) if len(re.findall(r"[A-Za-z]+", s)) >= 4]
    avg_zh = round(sum(zh_sents) / len(zh_sents), 1) if zh_sents else 0.0
    avg_en = round(sum(en_sents) / len(en_sents), 1) if en_sents else 0.0

    sent_cv = None
    sample_lens = zh_sents if len(zh_sents) >= len(en_sents) else en_sents
    if len(sample_lens) >= 6:
        mean_l = sum(sample_lens) / len(sample_lens)
        if mean_l > 0:
            var = sum((x - mean_l) ** 2 for x in sample_lens) / len(sample_lens)
            sent_cv = round(math.sqrt(var) / mean_l, 3)
            if sent_cv < 0.30:
                warnings.append({
                    "category": "uniform_sentence_cadence",
                    "line": 1,
                    "match": f"sentence_length_cv={sent_cv} (<0.30)",
                })

    return {
        "prose_hard_blocks": hard_blocks,
        "prose_warnings": warnings,
        "readability": {
            "zh_sentence_count": len(zh_sents),
            "avg_zh_sentence_chars": avg_zh,
            "en_sentence_count": len(en_sents),
            "avg_en_sentence_words": avg_en,
            "sentence_length_cv": sent_cv,
        },
    }


def audit_paragraph_trace(trace_path: Path | None) -> dict[str, Any]:
    if not trace_path or not trace_path.is_file():
        return {
            "provided": False,
            "trace_ok": True,
            "trace_entries": 0,
            "action_counts": {},
            "schema_errors": [],
            "critical_omissions": [],
            "entries": [],
        }

    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    items = raw.get("paragraphs", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        return {
            "provided": True,
            "trace_ok": False,
            "trace_entries": 0,
            "action_counts": {},
            "schema_errors": ["paragraph_trace.json contains no paragraph entries."],
            "critical_omissions": [],
            "entries": [],
        }

    schema_errors: list[str] = []
    critical_omissions: list[dict[str, Any]] = []
    action_counts = {"KEEP": 0, "COMPRESS": 0, "OMIT_REDUNDANCY": 0}
    valid_actions = set(action_counts.keys())

    # Collect all retained facts across KEEP/COMPRESS entries to check cross-paragraph preservation
    all_retained_blob = " ".join(
        " ".join(str(x) for x in (it.get("retained_facts") or []))
        for it in items
        if isinstance(it, dict) and it.get("action") in {"KEEP", "COMPRESS"}
    )

    for idx, it in enumerate(items, 1):
        if not isinstance(it, dict):
            schema_errors.append(f"Entry #{idx} is not a JSON object.")
            continue
        pid = it.get("paragraph_id") or f"P{idx:02d}"
        sec = it.get("source_section")
        act = it.get("action")
        reason = it.get("keep_or_omit_reason")
        if not sec or not reason:
            schema_errors.append(f"{pid}: missing source_section or keep_or_omit_reason.")
        if act not in valid_actions:
            schema_errors.append(f"{pid}: invalid action {act!r}; expected one of {sorted(valid_actions)}.")
        else:
            action_counts[act] += 1

        if act == "OMIT_REDUNDANCY":
            omitted_blob = " ".join(str(x) for x in (it.get("omitted_items") or [])) + " " + str(it.get("source_excerpt") or "")
            m_crit = CRITICAL_LIMITATION_RE.search(omitted_blob)
            if m_crit:
                # Allow omission only if the exact critical fact token is preserved in retained_facts elsewhere
                token = m_crit.group(0)
                if token.lower() not in all_retained_blob.lower():
                    critical_omissions.append({
                        "paragraph_id": pid,
                        "source_section": sec or "",
                        "critical_signal": token,
                        "reason_given": reason or "",
                    })

    return {
        "provided": True,
        "trace_ok": len(schema_errors) == 0 and len(critical_omissions) == 0,
        "trace_entries": len(items),
        "action_counts": action_counts,
        "schema_errors": schema_errors,
        "critical_omissions": critical_omissions,
        "entries": items,
    }


def render_side_by_side_review(report: dict[str, Any], trace_entries: list[dict[str, Any]]) -> str:
    ev = report["source_evidence_tier"]
    lines = [
        "# Translational Science Writing — Side-by-Side Author Review",
        "",
        "## 1. Audit Summary",
        "",
        "| Dimension | Status | Detail |",
        "| --- | --- | --- |",
        f"| Overall Gate | {'PASS' if report['passed'] else 'FAIL'} | {len(report['errors'])} blocking error(s), {len(report['warnings'])} warning(s) |",
        f"| Target Audience | `{report['audience']}` | {report['audience_label']} |",
        f"| Source Evidence Tier | Tier {ev['tier']} (`{ev['label']}`) | Boundaries: {', '.join(ev['model_boundaries']) or 'none detected'} |",
        f"| Claim Escalation & Boundary | {'PASS' if (not report['claim_escalation_hits'] and report['model_boundary_preserved']) else 'FAIL'} | Escalations: {len(report['claim_escalation_hits'])}; Omitted boundaries: {report['omitted_model_boundaries']} |",
        f"| Numeric & Benchmark Fidelity | {'PASS' if (not report['ungrounded_target_numbers'] and not report['p_value_strengthened']) else 'FAIL'} | Ungrounded: {report['ungrounded_target_numbers']}; Bilingual equiv: {len(report['bilingual_magnitude_matches'])}; Verified derived: {len(report['verified_derived_ratios'])} |",
        f"| Acronyms & Glossary | {'PASS' if (not report['unexpanded_acronyms'] and not report['glossary_violations']) else 'FAIL'} | Unexpanded: {report['unexpanded_acronyms']}; Glossary issues: {len(report['glossary_violations'])} |",
        f"| Natural Prose (Non-AI) | {'PASS' if not report['prose_hard_blocks'] else 'FAIL'} | Hard blocks: {len(report['prose_hard_blocks'])}; Soft warnings: {len(report['prose_warnings'])} |",
        "",
    ]

    if report["errors"]:
        lines.append("## 2. Blocking Issues to Fix Before Delivery")
        lines.append("")
        for err in report["errors"]:
            lines.append(f"- {err}")
        lines.append("")

    lines.append("## 3. Paragraph-by-Paragraph Keep / Compress / Omit Review")
    lines.append("")
    if trace_entries:
        lines.append("| ID | Source Section | Action | Retained Facts | Omitted Items | Rationale | Author Check |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for it in trace_entries:
            pid = str(it.get("paragraph_id", "")).replace("|", "/")
            sec = str(it.get("source_section", "")).replace("|", "/")
            act = str(it.get("action", "")).replace("|", "/")
            ret = "; ".join(str(x) for x in (it.get("retained_facts") or [])).replace("|", "/")
            omi = "; ".join(str(x) for x in (it.get("omitted_items") or [])).replace("|", "/")
            rsn = str(it.get("keep_or_omit_reason", "")).replace("|", "/")
            chk = "YES" if it.get("author_check_needed") else "No"
            lines.append(f"| {pid} | {sec} | `{act}` | {ret or '-'} | {omi or '-'} | {rsn} | {chk} |")
    else:
        lines.append("No `paragraph_trace.json` entries supplied.")
    lines.append("")
    return "\n".join(lines)


def audit_genre_length_and_structure(
    tgt_txt: str,
    profile: dict[str, Any],
    *,
    max_words: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Deterministic length ceiling & required structural section check for 5 audience genres."""
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", tgt_txt))
    en_words = len(re.findall(r"[A-Za-z0-9_'-]+", tgt_txt))
    is_zh = zh_chars >= en_words
    eff_max_chars = max_chars if max_chars is not None else int(profile.get("default_max_zh_chars", 3000))
    eff_max_words = max_words if max_words is not None else int(profile.get("default_max_en_words", 1500))

    length_exceeded = False
    length_detail = ""
    if max_chars is not None and zh_chars > max_chars:
        length_exceeded = True
        length_detail = f"Chinese char count {zh_chars} exceeds explicit max_chars {max_chars}"
    elif max_words is not None and en_words > max_words:
        length_exceeded = True
        length_detail = f"English word count {en_words} exceeds explicit max_words {max_words}"
    elif is_zh and zh_chars > eff_max_chars:
        length_detail = f"Chinese char count {zh_chars} exceeds genre ceiling {eff_max_chars}"
    elif (not is_zh) and en_words > eff_max_words:
        length_detail = f"English word count {en_words} exceeds genre ceiling {eff_max_words}"

    missing_sections: list[str] = []
    present_sections: list[str] = []
    for sec_name, sec_pat in profile.get("required_sections", ()):
        if re.search(sec_pat, tgt_txt, re.I):
            present_sections.append(sec_name)
        else:
            missing_sections.append(sec_name)

    return {
        "zh_chars": zh_chars,
        "en_words": en_words,
        "dominant_language": "zh" if is_zh else "en",
        "max_chars_limit": eff_max_chars,
        "max_words_limit": eff_max_words,
        "explicit_ceiling_exceeded": length_exceeded,
        "length_detail": length_detail,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
    }


def audit_translation_fidelity(
    source_path: Path,
    target_path: Path,
    *,
    audience: str = "public_science",
    glossary_path: Path | None = None,
    trace_json_path: Path | None = None,
    review_md_path: Path | None = None,
    max_words: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    profile = AUDIENCE_PROFILES[audience]
    src_txt = source_path.read_text(encoding="utf-8", errors="replace")
    tgt_txt = target_path.read_text(encoding="utf-8", errors="replace")

    num_res = _verify_numbers(src_txt, tgt_txt)
    src_ev = classify_evidence_tier(src_txt)
    ladder_res = audit_evidence_ladder(src_ev, tgt_txt, require_boundary=profile["require_model_boundary"])
    term_res = audit_acronyms_and_glossary(
        tgt_txt,
        require_expansion=profile["require_acronym_expansion"],
        glossary_path=glossary_path,
    )
    prose_res = audit_prose_naturalness(tgt_txt, strict=profile["prose_strict"])
    trace_res = audit_paragraph_trace(trace_json_path)
    genre_res = audit_genre_length_and_structure(tgt_txt, profile, max_words=max_words, max_chars=max_chars)

    errors: list[str] = []
    warnings: list[str] = []

    if num_res["ungrounded_target_numbers"]:
        errors.append(f"Target introduces ungrounded numbers: {num_res['ungrounded_target_numbers']}")
    if num_res["p_value_strengthened"]:
        errors.append(f"Statistical p-value strengthened beyond source: {num_res['p_value_detail']}")
    if profile["require_baseline_if_present"] and num_res["source_has_baseline"] and not num_res["target_has_baseline"]:
        errors.append("COMPARISON_BASELINE_OMITTED: source reports a comparison baseline/control arm absent from adapted draft.")
    if profile["require_sample_size_if_present"] and num_res["source_has_sample_size"] and not num_res["target_has_sample_size"]:
        warnings.append("SAMPLE_SIZE_OMITTED: source reports sample size absent from adapted draft for technical/grant/industry audience.")

    if ladder_res["claim_escalation_hits"]:
        errors.append(
            f"CLAIM_LEVEL_ESCALATION over Tier {src_ev['tier']} ({src_ev['label']}) source: "
            f"{[h['match'] for h in ladder_res['claim_escalation_hits']]}"
        )
    if ladder_res["omitted_model_boundaries"]:
        errors.append(
            f"MODEL_BOUNDARY_OMITTED: adapted draft dropped source evidence boundary {ladder_res['omitted_model_boundaries']}"
        )

    if term_res["unexpanded_acronyms"]:
        errors.append(f"Acronyms unexpanded on first use for {audience}: {term_res['unexpanded_acronyms']}")
    if term_res["glossary_violations"]:
        errors.append(f"Glossary lock violated: {[v['found'] for v in term_res['glossary_violations']]}")

    if prose_res["prose_hard_blocks"]:
        errors.append(
            f"Prose hard-block violations ({len(prose_res['prose_hard_blocks'])}): "
            f"{[b['category'] + ':' + b['match'] for b in prose_res['prose_hard_blocks'][:6]]}"
        )

    rd = prose_res["readability"]
    if rd["avg_zh_sentence_chars"] > profile["max_avg_zh_sent_len"]:
        warnings.append(
            f"Average Chinese sentence length {rd['avg_zh_sentence_chars']} exceeds {audience} target {profile['max_avg_zh_sent_len']}."
        )
    if rd["avg_en_sentence_words"] > profile["max_avg_en_sent_words"]:
        warnings.append(
            f"Average English sentence length {rd['avg_en_sentence_words']} exceeds {audience} target {profile['max_avg_en_sent_words']}."
        )

    if trace_res["schema_errors"]:
        errors.extend(trace_res["schema_errors"])
    if trace_res["critical_omissions"]:
        errors.append(
            f"CRITICAL_LIMITATION_OMITTED in paragraph_trace.json: "
            f"{[c['paragraph_id'] + ':' + c['critical_signal'] for c in trace_res['critical_omissions']]}"
        )
    if genre_res["explicit_ceiling_exceeded"]:
        errors.append(f"LENGTH_CEILING_EXCEEDED: {genre_res['length_detail']}")
    elif genre_res["length_detail"]:
        warnings.append(f"GENRE_LENGTH_ADVISORY: {genre_res['length_detail']}")
    if genre_res["missing_sections"]:
        warnings.append(f"GENRE_REQUIRED_SECTIONS_MISSING ({audience}): {genre_res['missing_sections']}")

    report = {
        "passed": len(errors) == 0,
        "audience": audience,
        "audience_label": profile["label"],
        "genre_structure_checks": genre_res,
        "source_evidence_tier": src_ev,
        "claim_escalation_hits": ladder_res["claim_escalation_hits"],
        "omitted_model_boundaries": ladder_res["omitted_model_boundaries"],
        "model_boundary_preserved": ladder_res["model_boundary_preserved"],
        "ungrounded_target_numbers": num_res["ungrounded_target_numbers"],
        "bilingual_magnitude_matches": num_res["bilingual_magnitude_matches"],
        "verified_derived_ratios": num_res["verified_derived_ratios"],
        "p_value_strengthened": num_res["p_value_strengthened"],
        "p_value_detail": num_res["p_value_detail"],
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
    parser = argparse.ArgumentParser(description="Audit translational science writing fidelity, evidence boundaries, prose, and paragraph trace.")
    parser.add_argument("--source", required=True, type=Path, help="Source scientific manuscript.")
    parser.add_argument("--target", required=True, type=Path, help="Audience-adapted deliverable.")
    parser.add_argument("--audience", default="public_science", choices=sorted(AUDIENCE_PROFILES.keys()))
    parser.add_argument("--glossary-json", type=Path, default=None, help="Optional glossary lock JSON.")
    parser.add_argument("--trace-json", type=Path, default=None, help="Optional paragraph_trace.json.")
    parser.add_argument("--max-words", type=int, default=None, help="Optional explicit English word ceiling.")
    parser.add_argument("--max-chars", type=int, default=None, help="Optional explicit Chinese character ceiling.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output fidelity audit JSON.")
    parser.add_argument("--review-md", type=Path, default=None, help="Optional side-by-side author review Markdown output.")
    args = parser.parse_args(argv)

    rep = audit_translation_fidelity(
        args.source,
        args.target,
        audience=args.audience,
        glossary_path=args.glossary_json,
        trace_json_path=args.trace_json,
        review_md_path=args.review_md,
        max_words=args.max_words,
        max_chars=args.max_chars,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "passed": rep["passed"],
                "audience": rep["audience"],
                "source_evidence_tier": rep["source_evidence_tier"],
                "errors": rep["errors"],
                "warnings": rep["warnings"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
