#!/usr/bin/env python3
"""Evidence-driven scientific writing guard: atomic claim decomposer, multi-source
verbatim excerpt locator, strict 5-state verdict engine, conflict-synthesis auditor,
and structured 5-field result.json emitter.

Zero third-party dependencies (Python 3.12+ stdlib). Integrates `writing_io_and_invariants.py`.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from writing_io_and_invariants import (  # noqa: E402
    audit_academic_prose,
    build_writing_result,
    extract_invariants,
    load_case_contract,
    read_document_text,
)

# ---------------------------------------------------------------------------
# 1. Regex Patterns & Taxonomies
# ---------------------------------------------------------------------------

ABBREV_PROT = r"\b(?:Fig|Figs|Eq|Eqs|Ref|Refs|Tab|Tbl|No|Vol|Dr|Prof|vs|etc|al|e\.g|i\.e)\."
RE_CITE_BRACKET = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
RE_CITE_PAREN = re.compile(
    r"\(([A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?,?\s*(?:19|20)\d{2}[a-z]?"
    r"(?:;\s*[A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?,?\s*(?:19|20)\d{2}[a-z]?)*)\)"
)
RE_CITE_LATEX = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
RE_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)

RE_CAUSAL = re.compile(
    r"\b(?:proves?|proved|proven|causes?|caused|causing|drives?|drove|determines?|determined|"
    r"directly\s+triggers?|establishes?\s+(?:a\s+)?causal|definitively\s+shows?|universally\s+causes?|"
    r"induces?\s+directly|is\s+caused\s+by)\b|证明了因果|直接导致|引发了|决定性地证明|普遍导致",
    re.I,
)
RE_CONSENSUS = re.compile(
    r"\b(?:universally|consistently\s+across\s+all|well-?established\s+consensus|all\s+studies\s+agree|"
    r"unanimously|in\s+all\s+settings|always\s+improves?|conclusively\s+established)\b|"
    r"学界公认|一致认为|所有研究均表明|普遍适用|已成定论",
    re.I,
)
RE_CORRELATIONAL = re.compile(
    r"\b(?:may\s+be\s+associated\s+with|associated\s+with|correlat(?:es?|ed|ion)\s+with|linked\s+to|"
    r"co-?occurs?\s+with|observational|suggests?|may|might|could|appears?\s+to|trended\s+toward)\b|"
    r"相关|伴随|可能|提示|倾向于|观察性",
    re.I,
)
RE_CONDITIONAL = re.compile(
    r"\b(?:under\s+[a-z0-9\-\s]{3,40}|only\s+(?:in|under|when|at)\s+[a-z0-9\-\s]{3,40}|"
    r"in\s+(?:acidic|alkaline|high-?temperature|low-?temperature|hypoxic|normoxic|in\s+vitro|in\s+vivo|"
    r"pediatric|elderly|early-?stage|late-?stage|high-?dose|low-?dose|preclinical)[a-z0-9\-\s]{0,25}|"
    r"dependent\s+on|contingent\s+upon|within\s+the\s+tested|whereas|in\s+contrast\s+to|unlike|"
    r"divergent|conflicting|inconsistent)\b|"
    r"(?:仅在[\u4e00-\u9fffA-Za-z0-9]{2,18}条件下|取决于|适用条件|相比之下|与之相反|存在分歧|并不一致)",
    re.I,
)

RE_POSITIVE_POLARITY = re.compile(
    r"\b(?:increas(?:es?|ed|ing)|improv(?:es?|ed|ing)|enhanc(?:es?|ed|ing)|elevat(?:es?|ed)|"
    r"higher|accelerat(?:es?|ed)|promot(?:es?|ed)|boost(?:s|ed)|outperform(?:s|ed)|benefit(?:s|ed)?)\b|"
    r"提高|增加|增强|上升|改善|促进|优于|正向",
    re.I,
)
RE_NEGATIVE_OR_NULL_POLARITY = re.compile(
    r"\b(?:no\s+significant\s+(?:effect|difference|improvement|benefit|change)|failed\s+to\s+(?:improve|increase|reduce|show)|"
    r"did\s+not\s+(?:improve|increase|differ|change)|decreas(?:es?|ed)|reduc(?:es?|ed)|impair(?:s|ed)|"
    r"worsen(?:s|ed)|inhibit(?:s|ed)|suppress(?:es|ed)|lower|null\s+effect|no\s+benefit|adverse)\b|"
    r"无显著(?:差异|改善|影响|效果)|未能(?:提高|改善|降低)|降低|下降|抑制|恶化|未见明显变化",
    re.I,
)

RE_DESIGN_RCT = re.compile(
    r"\b(?:randomized|randomised|RCT|double-?blind|placebo-?controlled|knockout|CRISPR|perturbation|"
    r"controlled\s+intervention|causal\s+experiment|isogenic\s+rescue)\b|随机对照|双盲|敲除|干预实验",
    re.I,
)
RE_DESIGN_OBSERVATIONAL = re.compile(
    r"\b(?:observational|cross-?sectional|retrospective|correlational|correlation\s+only|"
    r"cohort\s+association|associative|cannot\s+establish\s+causality|no\s+causal\s+inference)\b|"
    r"观察性|横断面|回顾性|相关性研究|仅支持相关|无法推断因果",
    re.I,
)
RE_DESIGN_META = re.compile(r"\b(?:meta-?analysis|systematic\s+review)\b|荟萃分析|系统综述", re.I)

RE_TYPE_PREPRINT = re.compile(r"\b(?:preprint|biorxiv|medrxiv|arxiv|chemrxiv|ssrn|not\s+peer-?reviewed)\b|预印本", re.I)
RE_TYPE_GUIDELINE = re.compile(r"\b(?:clinical\s+guideline|consensus\s+statement|practice\s+guideline|WHO\s+guideline)\b|指南|专家共识", re.I)
RE_TYPE_REVIEW = re.compile(
    r"\b(?:narrative\s+review|systematic\s+review|literature\s+review|review\s+article|"
    r"this\s+review\s+(?:organizes|summarizes|systematically|provides|focuses|centers|proposes)|"
    r"this\s+is\s+a\s+narrative\s+review)\b|综述",
    re.I,
)
RE_ABSTRACT_ONLY = re.compile(r"\b(?:abstract\s+only|abstract-only|full\s*text\s+(?:not\s+available|unavailable|absent))\b|仅有摘要|无全文", re.I)

RE_CONFLICT_SYNTHESIS = re.compile(
    r"\b(?:divergen(?:t|ce)|conflict(?:ing|s)?|discrepan(?:cy|cies)|inconsisten(?:t|cy)|"
    r"depend(?:s|ing)?\s+on|boundary\s+condition|applicability\s+condition|whereas|in\s+contrast|"
    r"reconcil(?:e|ed|ing)|context-?dependent|heterogeneity)\b|"
    r"(?:分歧|差异|矛盾|适用条件|边界条件|取决于|相反|异质性|条件差异)",
    re.I,
)
RE_PAPER_BY_PAPER_START = re.compile(
    r"^(?:(?:Study|Paper|Reference|Author)\s*\d+|First,\s+[A-Z][a-z]+\s+et\s+al\.|Second,\s+[A-Z][a-z]+\s+et\s+al\.|"
    r"Third,\s+[A-Z][a-z]+\s+et\s+al\.|[A-Z][a-z]+\s+et\s+al\.\s*\(\d{4}\)\s+(?:reported|found|showed|studied)|"
    r"文献\s*\d+|第一篇|第二篇|第三篇)",
    re.I,
)

STOPWORDS = {
    "that", "this", "with", "from", "were", "have", "been", "also", "into", "their", "these",
    "those", "which", "where", "while", "when", "than", "then", "such", "more", "most", "some",
    "only", "both", "each", "other", "over", "under", "study", "studies", "paper", "result",
    "results", "data", "showed", "reported", "found", "using", "based", "analysis", "between",
    "within", "across", "however", "therefore", "although", "because", "through", "about",
}


# Outcome-aware polarity equivalence, double-negation, and concessive-clause protection
RE_UNDESIRABLE_OUTCOME = re.compile(
    r"\b(?:mortality|death|fatality|error|mae|rmse|loss|overpotential|risk|toxicity|inflammation|"
    r"neuroinflammation|permeability|failure\s+rate|relapse|dissolution|degradation|agglomeration|"
    r"complication|pathology|impairment|deficit|dysfunction|burden|latency|cost|resistance|"
    r"translocation|dysbiosis|pyroptosis|hyperarousal|fragmentation|endotoxemia|leakage)\b|"
    r"死亡率|误差|过电位|风险|毒性|炎症|渗透性|降解|病理|损伤|障碍|易位|失调|失效",
    re.I,
)
RE_DESIRABLE_OUTCOME = re.compile(
    r"\b(?:survival|accuracy|auroc|auc|f1|efficiency|capacity|stability|retention|recovery|"
    r"activity|selectivity|performance|integrity|durability|tolerance|butyrate|treg|efficacy|homeostasis)\b|"
    r"生存率|准确率|效率|容量|稳定性|恢复|活性|选择性|完整性|稳态|疗效",
    re.I,
)
RE_DEC_VERB = re.compile(
    r"\b(?:reduc(?:es?|ed|ing)|decreas(?:es?|ed|ing)|lower(?:s|ed|ing)?|suppress(?:es?|ed|ing)|"
    r"mitigat(?:es?|ed|ing)|alleviat(?:es?|ed|ing)|prevent(?:s|ed|ing)|inhibit(?:s|ed|ing)|"
    r"attenuat(?:es?|ed|ing)|shorten(?:s|ed|ing)|cut(?:s|ting)?)\b|降低|减少|抑制|缓解|减轻|缩短",
    re.I,
)
RE_INC_VERB = re.compile(
    r"\b(?:increas(?:es?|ed|ing)|improv(?:es?|ed|ing)|enhanc(?:es?|ed|ing)|elevat(?:es?|ed|ing)|"
    r"higher|boost(?:s|ed|ing)|promot(?:es?|ed|ing)|outperform(?:s|ed|ing)|accelerat(?:es?|ed)|"
    r"aggravat(?:es?|ed)|worsen(?:s|ed)|exacerbat(?:es?|ed))\b|提高|增加|增强|改善|促进|优于|加重|恶化",
    re.I,
)
RE_DOUBLE_NEGATION = re.compile(
    r"\b(?:not\s+unlikely|cannot\s+be\s+excluded|could\s+not\s+be\s+excluded|cannot\s+be\s+ruled\s+out|"
    r"may\s+not\s+be\s+excluded|not\s+without\s+(?:benefit|effect|merit|value)|"
    r"not\s+insignificant|never\s+failed\s+to|no\s+absence\s+of|not\s+uncommon)\b|"
    r"并非没有|不无可能|未尝不能|并非无益|不可排除|无法排除",
    re.I,
)
RE_HEDGED_UNCERTAIN = re.compile(
    r"\b(?:cannot\s+be\s+excluded|could\s+not\s+be\s+excluded|cannot\s+be\s+ruled\s+out|"
    r"may\s+not\s+be\s+excluded|not\s+unlikely|inconclusive|remains?\s+(?:unclear|uncertain|unresolved|elusive)|"
    r"not\s+yet\s+(?:established|confirmed|proven|validated)|insufficient\s+evidence|uncertain)\b|"
    r"不可排除|无法排除|不无可能|尚无定论|尚不明确|有待进一步验证",
    re.I,
)
RE_NULL_NON_SIG = re.compile(
    r"\b(?:no\s+(?:statistically\s+)?significant\s+(?:effect|difference|improvement|benefit|change|association|reduction|increase)|"
    r"non-?significant|not\s+statistically\s+significant|failed\s+to\s+(?:improve|increase|reduce|show|reach\s+significance)|"
    r"did\s+not\s+(?:improve|increase|differ|change|reach\s+significance)|"
    r"null\s+effect|no\s+benefit|no\s+difference\s+(?:was\s+)?observed|p\s*>\s*0\.05)\b|"
    r"无显著(?:差异|改善|影响|效果)|未达(?:到)?统计学显著|未能(?:提高|改善|降低)|未见明显变化",
    re.I,
)
RE_NEGATIVE_DIRECTION = re.compile(
    r"\b(?:decreas(?:es?|ed|ing)|reduc(?:es?|ed|ing)|impair(?:s|ed|ing)|"
    r"worsen(?:s|ed|ing)|inhibit(?:s|ed|ing)|suppress(?:es?|ed|ing)|lower(?:s|ed)?|"
    r"adverse|aggravat(?:es?|ed)|exacerbat(?:es?|ed))\b|"
    r"降低|下降|抑制|恶化|受损|减少|加重",
    re.I,
)
RE_CONCESSIVE_SPLIT = re.compile(
    r"^\s*(?:Although|Though|Even\s+though|While|Whereas)\s+([^;]{4,180}?),\s+(.+)$",
    re.I,
)
RE_CONCESSIVE_SPLIT_ZH = re.compile(r"^\s*(?:尽管|虽然)([^。；]{3,90}?)[，,]\s*(?:但(?:是)?|然而|不过)?(.+)$")
RE_CONCESSIVE_QUALIFIERS = re.compile(
    r"\b(?:observational|cross-?sectional|retrospective|small\s+sample|preliminary|correlational|"
    r"not\s+yet\s+validated|remains?\s+limited|causal\s+validation|lacks?\s+direct|limited\s+to|"
    r"in\s+mice|murine|in\s+vitro|preclinical|uncontrolled|non-?randomized|short-?term|"
    r"single-?center|heterogeneous)\b|"
    r"观察性|横断面|回顾性|样本量有限|初步|尚未完全验证|直接证据有限|动物模型|体外|单中心",
    re.I,
)
SEMANTIC_CONCEPT_MAP = {
    "mortality": "survival_axis", "death": "survival_axis", "survival": "survival_axis", "fatality": "survival_axis",
    "error": "accuracy_axis", "rmse": "accuracy_axis", "mae": "accuracy_axis", "accuracy": "accuracy_axis",
    "auroc": "accuracy_axis", "auc": "accuracy_axis", "performance": "accuracy_axis",
    "overpotential": "activity_axis", "activity": "activity_axis",
    "stability": "durability_axis", "durability": "durability_axis", "dissolution": "durability_axis",
    "degradation": "durability_axis", "inflammation": "inflam_axis", "neuroinflammation": "inflam_axis",
    "cytokines": "inflam_axis", "barrier": "barrier_axis", "permeability": "barrier_axis", "bbb": "barrier_axis",
    "microbiota": "gut_axis", "dysbiosis": "gut_axis", "intestinal": "gut_axis", "gut": "gut_axis",
    "scfa": "scfa_axis", "scfas": "scfa_axis", "butyrate": "scfa_axis",
    "mice": "murine_axis", "mouse": "murine_axis", "murine": "murine_axis",
    "septic": "sepsis_axis", "sepsis": "sepsis_axis",
    "electrocatalyst": "catalyst_axis", "electrocatalysts": "catalyst_axis", "catalyst": "catalyst_axis", "catalysts": "catalyst_axis",
    "gnn": "gnn_axis", "gnns": "gnn_axis", "substructure": "substruct_axis", "substructures": "substruct_axis",
}


def _keywords(text: str) -> set[str]:
    en = {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower()) if w not in STOPWORDS}
    zh = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    mapped = {SEMANTIC_CONCEPT_MAP.get(w, w) for w in en}
    return en | mapped | zh


def _split_concessive(text: str) -> tuple[str, str]:
    """Return (concessive_clause, main_clause); returns ('', text) if not a concessive structure."""
    m = RE_CONCESSIVE_SPLIT.match(text.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    mz = RE_CONCESSIVE_SPLIT_ZH.match(text.strip())
    if mz:
        return mz.group(1).strip(), mz.group(2).strip()
    return "", text


def _detect_polarity(text: str) -> str:
    """Outcome-aware polarity classifier strictly separating:
    - positive (beneficial / directional increase of desirable or decrease of undesirable)
    - negative (directional decrease/impairment/worsening/adverse)
    - null_non_significant (explicit no statistically significant difference / null effect)
    - hedged_uncertain (double-negation uncertainty like 'cannot be excluded' / 'inconclusive')
    - mixed_conditional / neutral
    """
    concession, main_clause = _split_concessive(text)
    target = main_clause if concession and len(main_clause) >= 15 else text

    has_hedged_uncertain = bool(RE_HEDGED_UNCERTAIN.search(target))
    has_double_neg = bool(RE_DOUBLE_NEGATION.search(target))
    # Neutralize hedged-uncertain / double-negation phrases before checking single-negation triggers
    target_clean = RE_HEDGED_UNCERTAIN.sub(" uncertain_possibility ", target) if has_hedged_uncertain else target
    target_clean = RE_DOUBLE_NEGATION.sub(" uncertain_possibility ", target_clean) if has_double_neg else target_clean

    # 1. Explicit non-significant / null-effect phrases -> strictly `null_non_significant` (never conflated with negative direction)
    if RE_NULL_NON_SIG.search(target_clean):
        return "null_non_significant"

    # 2. Outcome-aware directional equivalence: "reduced mortality" / "lower overpotential" -> positive; "increased mortality" -> negative
    gap = r"(?:(?!(?:and|or|but|while|whereas)\b)[a-z0-9\-]+\s+){0,2}"
    dec_undesir = bool(re.search(
        rf"(?:{RE_DEC_VERB.pattern})\s+{gap}(?:{RE_UNDESIRABLE_OUTCOME.pattern})",
        target_clean, re.I,
    ))
    inc_desir = bool(re.search(
        rf"(?:{RE_INC_VERB.pattern})\s+{gap}(?:{RE_DESIRABLE_OUTCOME.pattern})",
        target_clean, re.I,
    ))
    inc_undesir = bool(re.search(
        rf"(?:{RE_INC_VERB.pattern})\s+{gap}(?:{RE_UNDESIRABLE_OUTCOME.pattern})",
        target_clean, re.I,
    ))
    dec_desir = bool(re.search(
        rf"(?:{RE_DEC_VERB.pattern})\s+{gap}(?:{RE_DESIRABLE_OUTCOME.pattern})",
        target_clean, re.I,
    ))

    if (dec_undesir or inc_desir) and not (inc_undesir or dec_desir):
        return "hedged_uncertain" if has_hedged_uncertain else "positive"
    if (inc_undesir or dec_desir) and not (dec_undesir or inc_desir):
        return "negative"
    if (dec_undesir or inc_desir) and (inc_undesir or dec_desir):
        return "mixed_conditional"

    # 3. Direct directional terms (negative direction separated from null/non-significant)
    has_neg_dir = bool(RE_NEGATIVE_DIRECTION.search(target_clean))
    has_pos = bool(RE_POSITIVE_POLARITY.search(target_clean))
    if has_neg_dir and not has_pos:
        return "negative"
    if has_pos and not has_neg_dir:
        return "hedged_uncertain" if has_hedged_uncertain else "positive"
    if has_pos and has_neg_dir:
        return "mixed_conditional"

    # 4. Double-negation / 'cannot be excluded' MUST NEVER be labeled positive -> `hedged_uncertain`
    if has_hedged_uncertain or has_double_neg:
        return "hedged_uncertain"
    return "neutral"


MODEL_CONDITION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sleep_deprivation", re.compile(r"\bsleep\s+deprivation\b|\bSD\s+paradigm|睡眠剥夺", re.I)),
    ("chronic_insomnia", re.compile(r"\b(?:chronic\s+)?insomnia\b|失眠", re.I)),
    ("spinal_cord_injury", re.compile(r"\bspinal\s+cord\s+injury\b|\bSCI\b|脊髓损伤", re.I)),
    ("acidic_electrolyte", re.compile(r"\bacidic\b|酸性", re.I)),
    ("alkaline_electrolyte", re.compile(r"\balkaline\b|碱性", re.I)),
    ("neutral_electrolyte", re.compile(r"\bneutral\s+(?:ph|medium|electrolyte)\b|中性", re.I)),
    ("in_vitro_model", re.compile(r"\bin\s+vitro\b|\bcell\s+line\b|体外", re.I)),
    ("in_vivo_model", re.compile(r"\bin\s+vivo\b|体内", re.I)),
]

SPECIES_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("human_clinical", re.compile(r"\b(?:human|humans|clinical|patients?|cohort)\b|临床|患者|人群", re.I)),
    ("murine_rodent", re.compile(r"\b(?:mice|mouse|murine|rats?|rodents?)\b|小鼠|大鼠|啮齿", re.I)),
]

ENDPOINT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sleep_fragmentation_insomnia", re.compile(r"\bsleep\s+fragmentation\b|\binsomnia\b|\bsleep\s+loss\b|睡眠", re.I)),
    ("depressive_cognitive", re.compile(r"\b(?:depressive|depression|cognitive\s+decline|memory)\b|抑郁|认知", re.I)),
    ("tight_junction_claudin", re.compile(r"\b(?:claudin-?23|tight-?junction|sp1/ampk|barrier\s+integrity|barrier\s+vulnerability)\b|紧密连接|屏障", re.I)),
    ("nlrp3_pyroptosis", re.compile(r"\b(?:nlrp3|pyroptosis|caspase-?1|microglial)\b|焦亡", re.I)),
    ("lps_tlr4_nfkb", re.compile(r"\b(?:lps|lipopolysaccharide|tlr4|nf-?kappab|nf-?κb|nf-?kb)\b", re.I)),
    ("colonic_transit_scfa", re.compile(r"\b(?:colonic\s+transit|scfa|short-?chain\s+fatty\s+acid|butyrate)\b|短链脂肪酸|丁酸", re.I)),
    ("overpotential_oer", re.compile(r"\b(?:overpotential|electrocatalys|water\s+splitting|oer|her)\b|过电位|电催化", re.I)),
    ("mortality_survival", re.compile(r"\b(?:mortality|survival|fatality)\b|死亡率|生存率", re.I)),
]

RE_COMPARATIVE_NON_EQUIV = re.compile(
    r"\b(?:do\s+not\s+(?:fully\s+)?recapitulate|not\s+equivalent\s+to|differs?\s+(?:fundamentally\s+)?from|"
    r"diverges?\s+(?:substantially\s+)?across|cannot\s+be\s+equated\s+(?:to|with)|distinct\s+from)\b|"
    r"不等同于|不能完全模拟|存在显著差异",
    re.I,
)


def _extract_feature_tags(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> set[str]:
    return {name for name, pat in patterns if pat.search(text)}


def _check_claim_excerpt_alignment(claim_text: str, excerpt_text: str, source_context: str = "") -> dict[str, Any]:
    """Verify structural alignment across experimental model/condition, species/population, endpoint, and comparative boundary."""
    ctx_for_model = f"{source_context} {excerpt_text}" if source_context else excerpt_text
    c_models = _extract_feature_tags(claim_text, MODEL_CONDITION_PATTERNS)
    e_models = _extract_feature_tags(ctx_for_model, MODEL_CONDITION_PATTERNS)
    c_species = _extract_feature_tags(claim_text, SPECIES_PATTERNS)
    e_species = _extract_feature_tags(ctx_for_model, SPECIES_PATTERNS)
    c_endpoints = _extract_feature_tags(claim_text, ENDPOINT_PATTERNS)
    e_endpoints = _extract_feature_tags(excerpt_text, ENDPOINT_PATTERNS)

    mismatch_fields: list[dict[str, Any]] = []

    # 1. Experimental model / disease condition mismatch
    missing_models = sorted(c_models - e_models)
    if c_models and missing_models and not (c_models & e_models):
        mismatch_fields.append({
            "field": "experimental_model_or_condition",
            "claim_values": sorted(c_models),
            "excerpt_values": sorted(e_models),
            "detail": f"Claim specifies model/condition {sorted(c_models)}, not supported by excerpt ({sorted(e_models) or 'none'}).",
        })
    elif len(c_models) >= 2 and len(missing_models) >= 1 and RE_COMPARATIVE_NON_EQUIV.search(claim_text):
        mismatch_fields.append({
            "field": "comparative_model_boundary",
            "claim_values": sorted(c_models),
            "excerpt_values": sorted(e_models),
            "detail": f"Claim compares/contrasts models {sorted(c_models)}, but excerpt omits {missing_models}.",
        })

    # 2. Species / population mismatch (e.g., claim asserts human/clinical while excerpt only covers murine/rodent)
    if c_species and e_species and not (c_species & e_species):
        mismatch_fields.append({
            "field": "species_or_population",
            "claim_values": sorted(c_species),
            "excerpt_values": sorted(e_species),
            "detail": f"Claim targets {sorted(c_species)}, whereas excerpt reports {sorted(e_species)}.",
        })

    # 3. Endpoint / outcome mismatch
    if c_endpoints and not (c_endpoints & e_endpoints):
        mismatch_fields.append({
            "field": "endpoint_or_outcome",
            "claim_values": sorted(c_endpoints),
            "excerpt_values": sorted(e_endpoints),
            "detail": f"Claim asserts endpoint(s) {sorted(c_endpoints)}, absent from matched excerpt ({sorted(e_endpoints) or 'none'}).",
        })

    # 4. Comparative / non-equivalence boundary claim requires excerpt to contain contrasting/model-limitation language
    if RE_COMPARATIVE_NON_EQUIV.search(claim_text) and not (
        RE_COMPARATIVE_NON_EQUIV.search(excerpt_text)
        or re.search(r"\b(?:recapitulate|sleep\s+deprivation|chronic\s+insomnia|model\s+limitation|acute\s+vs\.?\s+chronic|cannot\s+establish\s+causality)\b", excerpt_text, re.I)
    ):
        if not any(m["field"] == "comparative_model_boundary" for m in mismatch_fields):
            mismatch_fields.append({
                "field": "comparative_boundary_unsupported",
                "claim_values": ["non_equivalence_or_design_divergence"],
                "excerpt_values": [],
                "detail": "Claim asserts experimental-model non-equivalence or cross-design divergence, but matched excerpt does not state this boundary.",
            })

    return {
        "aligned": len(mismatch_fields) == 0,
        "mismatch_fields": mismatch_fields,
        "claim_models": sorted(c_models),
        "excerpt_models": sorted(e_models),
        "claim_endpoints": sorted(c_endpoints),
        "excerpt_endpoints": sorted(e_endpoints),
    }


def _extract_conditions(text: str) -> list[str]:
    conds = {m.group(0).strip().lower() for m in RE_CONDITIONAL.finditer(text)}
    concession, _ = _split_concessive(text)
    if concession and RE_CONCESSIVE_QUALIFIERS.search(concession):
        conds.add(f"concessive_scope:{concession[:60].lower()}")
    return sorted(conds)


RE_CITE_NARRATIVE = re.compile(
    r"\b([A-Z][A-Za-z\-']+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-']+|&\s+[A-Z][A-Za-z\-']+))?)\s*\(((?:19|20)\d{2}[a-z]?)\)"
)
RE_STEM_AY = re.compile(r"^([A-Za-z][A-Za-z\-']{1,30})[_\-\s]*((?:19|20)\d{2}[a-z]?)$")


def _extract_citations(text: str) -> list[str]:
    out: list[str] = []
    for m in RE_CITE_BRACKET.finditer(text):
        for part in m.group(1).split(","):
            part = part.strip()
            rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
            if rng:
                lo, hi = int(rng.group(1)), int(rng.group(2))
                if hi >= lo and hi - lo <= 20:
                    out.extend(f"[{k}]" for k in range(lo, hi + 1))
            elif part.isdigit():
                out.append(f"[{part}]")
    for m in RE_CITE_PAREN.finditer(text):
        for p in m.group(1).split(";"):
            ps = p.strip()
            if ps:
                out.append(ps)
    for m in RE_CITE_NARRATIVE.finditer(text):
        out.append(f"{m.group(1).strip()}, {m.group(2).strip()}")
    for m in RE_CITE_LATEX.finditer(text):
        for k in m.group(1).split(","):
            ks = k.strip()
            if ks:
                out.append(ks)
    for m in RE_DOI.finditer(text):
        out.append(m.group(1).rstrip(".,;)").lower())
    return list(dict.fromkeys(out))


def _parse_draft_references_bridge(draft_text: str) -> dict[str, dict[str, Any]]:
    """Parse draft's ## References / Bibliography section to bridge [n] -> DOI, author-year, and title keywords."""
    bridge: dict[str, dict[str, Any]] = {}
    in_refs = False
    cur_num: str | None = None
    cur_buf: list[str] = []

    def _flush() -> None:
        if not cur_num or not cur_buf:
            return
        entry_text = " ".join(cur_buf).strip()
        dois = [d.rstrip(".,;)").lower() for d in RE_DOI.findall(entry_text)]
        ay_keys: list[str] = []
        for m in RE_CITE_NARRATIVE.finditer(entry_text):
            first = m.group(1).split()[0].rstrip(",.").lower()
            yr = m.group(2).lower()
            ay_keys.extend([f"{first} {yr}", f"{first}{yr}", f"{m.group(1).strip().lower()}, {yr}"])
        if not ay_keys:
            m_lead = re.match(r"^([A-Z][A-Za-z\-']+)", entry_text)
            m_yr = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", entry_text)
            if m_lead and m_yr:
                first = m_lead.group(1).lower()
                yr = m_yr.group(1).lower()
                ay_keys.extend([f"{first} {yr}", f"{first}{yr}"])
        bridge[f"[{cur_num}]"] = {
            "raw": entry_text,
            "dois": list(dict.fromkeys(dois)),
            "author_years": list(dict.fromkeys(ay_keys)),
            "keywords": _keywords(entry_text),
        }

    for raw in draft_text.splitlines():
        st = raw.strip()
        hm = re.match(r"^(#{1,4})\s+(.+?)\s*$", st)
        if hm:
            heading = hm.group(2).strip()
            if in_refs:
                _flush()
                cur_num, cur_buf = None, []
            in_refs = bool(re.match(r"^(?:References|Bibliography|参考文献)\b", heading, re.I))
            continue
        if not in_refs or not st:
            continue
        m_item = re.match(r"^(?:\[(\d+)\]|(\d+)[.)])\s+(.+)$", st)
        if m_item:
            _flush()
            cur_num = m_item.group(1) or m_item.group(2)
            cur_buf = [m_item.group(3).strip()]
        elif cur_num:
            cur_buf.append(st)
    _flush()
    return bridge


# ---------------------------------------------------------------------------
# 2. Multi-Source Corpus Indexer (File/Page/Section/Line/Type/Design)
# ---------------------------------------------------------------------------

def _classify_source_type(text: str, filename: str, meta_scope: str = "") -> str:
    blob = f"{filename} {meta_scope} {text[:3200]}"
    if meta_scope == "abstract_only" or RE_ABSTRACT_ONLY.search(blob):
        return "abstract_only"
    if RE_TYPE_PREPRINT.search(blob):
        return "preprint"
    if RE_TYPE_GUIDELINE.search(blob):
        return "guideline"
    if RE_TYPE_REVIEW.search(blob):
        return "review"
    return "primary_research"


def _classify_study_design(text: str, meta_design: str = "", source_type: str = "") -> str:
    # Prioritize title/abstract/methods header (first 3600 chars) so cited papers in long reviews do not mislabel study design
    head_blob = f"{meta_design} {text[:3600]}"
    if RE_DESIGN_META.search(head_blob):
        return "meta_analysis"
    if source_type == "review":
        return "review_synthesis"
    if RE_DESIGN_OBSERVATIONAL.search(head_blob) and not RE_DESIGN_RCT.search(head_blob):
        return "observational_correlational"
    if RE_DESIGN_RCT.search(head_blob):
        return "rct_or_intervention"
    if re.search(r"\bin\s+vitro\b|\bcell\s+line\b|\bmouse\s+model\b|细胞实验|动物模型", head_blob, re.I):
        return "in_vitro_preclinical"
    return "unknown"


def _source_aliases(doc_id: str, filename: str, text: str, meta: dict[str, Any] | None = None) -> set[str]:
    stem_low = Path(filename).stem.lower()
    aliases: set[str] = {doc_id.lower(), stem_low}
    m_stem_ay = RE_STEM_AY.match(Path(filename).stem)
    if m_stem_ay:
        first, yr = m_stem_ay.group(1).lower(), m_stem_ay.group(2).lower()
        aliases.update({f"{first} {yr}", f"{first}, {yr}", f"{first} et al., {yr}", f"{first}{yr}"})
    if meta:
        for k in ("id", "cite_key", "key", "citation_label", "ref_num", "doi"):
            v = meta.get(k)
            if v:
                sv = str(v).strip().lower()
                aliases.add(sv)
                if sv.isdigit():
                    aliases.add(f"[{sv}]")
        if meta.get("author") and meta.get("year"):
            first = str(meta["author"]).split(",")[0].split()[0].strip().lower()
            yr = str(meta["year"]).strip()
            aliases.add(f"{first} et al., {yr}")
            aliases.add(f"{first}, {yr}")
            aliases.add(f"{first} {yr}")
    # Extract DOIs, PMCIDs & bracket numbers from header lines / filename
    for d in RE_DOI.findall(text[:1600]):
        aliases.add(d.rstrip(".,;)").lower())
    for pmc in re.findall(r"\b(PMC\d{5,9})\b", filename + " " + text[:1600], re.I):
        aliases.add(pmc.lower())
    m_num = re.search(r"(?:ref|paper|source)[_\-\s]*(\d+)", filename, re.I)
    if m_num:
        aliases.add(f"[{m_num.group(1)}]")
        aliases.add(m_num.group(1))
    # Extract Author (Year) from header lines
    for m_ay in re.finditer(r"\b([A-Z][A-Za-z\-']+)\s+et\s+al\.,?\s*\(?((?:19|20)\d{2}[a-z]?)\)?", text[:800]):
        aliases.add(f"{m_ay.group(1).lower()} et al., {m_ay.group(2).lower()}")
        aliases.add(f"{m_ay.group(1).lower()} {m_ay.group(2).lower()}")
    for m_ay2 in re.finditer(r"\b([A-Z][A-Za-z\-']+)\s*\(((?:19|20)\d{2}[a-z]?)\)", text[:800]):
        aliases.add(f"{m_ay2.group(1).lower()}, {m_ay2.group(2).lower()}")
        aliases.add(f"{m_ay2.group(1).lower()} {m_ay2.group(2).lower()}")
    return {a for a in aliases if a}


def index_literature_corpus(corpus_dir: Path, draft_path: Path | None = None) -> list[dict[str, Any]]:
    """Scan corpus_dir for MD/TXT/JSON/DOCX/XML/HTML/PDF full texts & excerpts and build page/section/line-indexed sources."""
    sources: list[dict[str, Any]] = []
    if not corpus_dir.is_dir():
        return sources

    draft_resolved = draft_path.resolve() if draft_path else None
    skip_names = {"case.json", "task.md", "readme.md", "result.json", "public-manifest.json"}

    for fp in sorted(corpus_dir.rglob("*")):
        if not fp.is_file():
            continue
        if draft_resolved and fp.resolve() == draft_resolved:
            continue
        if fp.name.lower() in skip_names:
            continue
        suf = fp.suffix.lower()
        if suf not in {".md", ".txt", ".json", ".docx", ".pdf", ".tex", ".xml", ".html", ".htm"}:
            continue

        rel = fp.relative_to(corpus_dir).as_posix()

        # Structured JSON library of sources/excerpts
        if suf == ".json":
            try:
                data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else data.get("references", data.get("sources", data.get("papers", [])))
            if isinstance(items, list) and items and isinstance(items[0], dict):
                for idx, item in enumerate(items, start=1):
                    sid = str(item.get("id") or item.get("key") or f"{fp.stem}_{idx}")
                    body = str(item.get("full_text") or item.get("text") or item.get("excerpt") or item.get("abstract") or "")
                    scope = str(item.get("scope") or ("abstract_only" if not item.get("full_text") and item.get("abstract") else "full_text"))
                    stype = _classify_source_type(body, rel, scope)
                    sdesign = _classify_study_design(body, str(item.get("study_design") or ""))
                    chunks = [{
                        "section": str(item.get("section") or ("Abstract" if stype == "abstract_only" else "Main")),
                        "page": item.get("page"),
                        "line_start": 1,
                        "line_end": max(1, len(body.splitlines())),
                        "text": body,
                        "polarity": _detect_polarity(body),
                        "conditions": _extract_conditions(body),
                        "keywords": _keywords(body),
                    }]
                    sources.append({
                        "source_id": sid,
                        "file": rel,
                        "title": str(item.get("title") or sid),
                        "source_type": stype,
                        "study_design": sdesign,
                        "extractor": "structured_json",
                        "is_abstract_only": stype == "abstract_only",
                        "aliases": _source_aliases(sid, rel, body, item),
                        "chunks": chunks,
                        "overall_polarity": _detect_polarity(body),
                        "overall_conditions": _extract_conditions(body),
                        "full_text": body,
                    })
                continue

        doc = read_document_text(fp)
        if not doc["ok"] or not doc["text"].strip():
            continue
        text = doc["text"]
        extractor = str((doc.get("meta") or {}).get("extractor") or doc.get("format") or suf.lstrip("."))
        stype = _classify_source_type(text, rel)
        sdesign = _classify_study_design(text, source_type=stype)

        # Parse paragraphs with section, page (supports [Page N] and PDF form-feed \f), and line locators
        chunks: list[dict[str, Any]] = []
        section = "Document Header"
        page_num: int | None = 1 if suf == ".pdf" else None
        buf: list[tuple[int, str]] = []

        def _flush_buf() -> None:
            nonlocal buf
            if not buf:
                return
            p_text = " ".join(x[1] for x in buf).strip()
            if len(p_text) >= 15:
                chunks.append({
                    "section": section,
                    "page": page_num,
                    "line_start": buf[0][0],
                    "line_end": buf[-1][0],
                    "text": p_text,
                    "polarity": _detect_polarity(p_text),
                    "conditions": _extract_conditions(p_text),
                    "keywords": _keywords(p_text),
                })
            buf = []

        for lno, raw in enumerate(text.splitlines(), start=1):
            if "\f" in raw:
                _flush_buf()
                page_num = (page_num or 1) + raw.count("\f")
                raw = raw.replace("\f", " ")
            st = raw.strip()
            m_pg = re.match(r"^\[?Page\s+(\d+)\]?$", st, re.I)
            if m_pg:
                _flush_buf()
                page_num = int(m_pg.group(1))
                continue
            if not st:
                _flush_buf()
                continue
            hm = re.match(r"^(#{1,6})\s+(.+)$", st)
            if hm:
                _flush_buf()
                section = hm.group(2).strip()
                continue
            # EuropePMC/JATS Markdown puts each full paragraph on a single long line: flush per paragraph line
            if len(st) >= 180 and buf:
                _flush_buf()
            buf.append((lno, st))
            if len(st) >= 180:
                _flush_buf()
        _flush_buf()

        sources.append({
            "source_id": fp.stem,
            "file": rel,
            "title": (text.splitlines()[0].lstrip("# ").strip()[:160] if text.splitlines() else fp.stem),
            "source_type": stype,
            "study_design": sdesign,
            "extractor": extractor,
            "is_abstract_only": stype == "abstract_only",
            "aliases": _source_aliases(fp.stem, rel, text),
            "chunks": chunks,
            "overall_polarity": _detect_polarity(text),
            "overall_conditions": _extract_conditions(text),
            "full_text": text,
        })

    return sources


# ---------------------------------------------------------------------------
# 3. Atomic Claim Decomposer & Strict 5-State Verdict Engine
# ---------------------------------------------------------------------------

def decompose_atomic_claims(draft_text: str) -> list[dict[str, Any]]:
    """Decompose draft into atomic claim units C01, C02... with strength, polarity, citations, and conditions."""
    claims: list[dict[str, Any]] = []
    section = "Preamble"
    cid = 0
    in_refs = False

    for lno, raw in enumerate(draft_text.splitlines(), start=1):
        st = raw.strip()
        if not st or st.startswith("```") or (st.startswith("|") and st.endswith("|")):
            continue
        hm = re.match(r"^(#{1,6})\s+(.+)$", st)
        if hm:
            section = hm.group(2).strip()
            in_refs = bool(re.match(r"^(?:References|Bibliography|参考文献)\b", section, re.I))
            continue
        if in_refs:
            continue

        prot = re.sub(ABBREV_PROT, lambda m: m.group(0).replace(".", "<DOT>"), st)
        prot = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", prot)
        prot = re.sub(r"(10\.\d{4,9}/[^\s,;)]+)", lambda m: m.group(0).replace(".", "<DOT>"), prot)
        sents = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])(?=[^\s”’\"')\]])", prot)

        for part in sents:
            sent = part.replace("<DOT>", ".").strip()
            if len(sent) < 12:
                continue
            cites = _extract_citations(sent)
            has_substantive_assertion = bool(
                cites
                or RE_CAUSAL.search(sent)
                or RE_CORRELATIONAL.search(sent)
                or RE_CONSENSUS.search(sent)
                or RE_POSITIVE_POLARITY.search(sent)
                or RE_NEGATIVE_OR_NULL_POLARITY.search(sent)
                or re.search(r"\d+(?:\.\d+)?%|\bp\s*[<=>]|n\s*=\s*\d+", sent, re.I)
            )
            if not has_substantive_assertion:
                continue

            cid += 1
            concession, main_part = _split_concessive(sent)
            has_concessive_qual = bool(concession and RE_CONCESSIVE_QUALIFIERS.search(concession))
            has_double_neg = bool(RE_DOUBLE_NEGATION.search(sent))
            # Check causal verb only on main clause when a concessive qualifier is explicitly present
            causal_target = main_part if has_concessive_qual else sent
            if RE_CAUSAL.search(causal_target):
                strength = "causal"
            elif RE_CONSENSUS.search(causal_target):
                strength = "consensus_broad"
            elif (RE_CONDITIONAL.search(sent) or has_concessive_qual) and RE_CORRELATIONAL.search(sent):
                strength = "conditional_associative"
            elif RE_CONDITIONAL.search(sent) or has_concessive_qual:
                strength = "conditional_scoped"
            elif RE_CORRELATIONAL.search(sent):
                strength = "associative_correlational"
            else:
                strength = "descriptive"

            claims.append({
                "claim_id": f"C{cid:02d}",
                "section": section,
                "line": lno,
                "claim_text": sent,
                "claim_strength": strength,
                "polarity": _detect_polarity(sent),
                "conditions": _extract_conditions(sent),
                "concessive_qualification": has_concessive_qual,
                "double_negation_detected": has_double_neg,
                "citations": cites,
                "invariants": extract_invariants(sent),
            })
    return claims


def _match_citation_to_sources(
    cite: str,
    sources: list[dict[str, Any]],
    ref_bridge: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    cl = cite.strip().lower()
    cl_clean = re.sub(r"\s+", " ", cl.replace("(", "").replace(")", ""))
    matched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(src: dict[str, Any]) -> None:
        if src["source_id"] not in seen_ids:
            seen_ids.add(src["source_id"])
            matched.append(src)

    # Also extract surname + year if author-year format
    m_ay = re.match(r"([a-z\-']+).*?((?:19|20)\d{2}[a-z]?)", cl_clean)
    ay_key = f"{m_ay.group(1)} {m_ay.group(2)}" if m_ay else ""
    ay_compact = f"{m_ay.group(1)}{m_ay.group(2)}" if m_ay else ""

    for src in sources:
        if (
            cl in src["aliases"]
            or cl_clean in src["aliases"]
            or (ay_key and ay_key in src["aliases"])
            or (ay_compact and ay_compact in src["aliases"])
        ):
            _add(src)
            continue
        # Fallback: exact author surname + year check in full text header
        if m_ay and m_ay.group(1) in src["full_text"][:1000].lower() and m_ay.group(2) in src["full_text"][:1000].lower():
            _add(src)

    # Bridge numeric citations like [1] via draft's ## References section (DOI / Author+Year / Title keywords)
    if not matched and ref_bridge and cl in ref_bridge:
        entry = ref_bridge[cl]
        for d in entry.get("dois", []):
            for src in sources:
                if d in src["aliases"] or d in src["full_text"][:2000].lower():
                    _add(src)
        if not matched:
            for ay in entry.get("author_years", []):
                for src in sources:
                    if ay in src["aliases"]:
                        _add(src)
                    else:
                        parts = ay.split()
                        if len(parts) == 2 and parts[0] in src["full_text"][:1200].lower() and parts[1] in src["full_text"][:1200].lower():
                            _add(src)
        if not matched and len(entry.get("keywords", set())) >= 3:
            ekw = entry["keywords"]
            for src in sources:
                header_kw = _keywords(src["full_text"][:1000])
                if len(ekw & header_kw) >= 3:
                    _add(src)
    return matched


def _focus_sentence_in_chunk(claim_kw: set[str], chunk_text: str) -> str:
    """Select the single highest-overlap sentence inside a long paragraph so polarity/condition checks stay strictly on-target."""
    if len(chunk_text) <= 260:
        return chunk_text
    prot = re.sub(ABBREV_PROT, lambda m: m.group(0).replace(".", "<DOT>"), chunk_text)
    prot = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", prot)
    sents = [p.replace("<DOT>", ".").strip() for p in re.split(r"(?<=[.!?。！？])\s+", prot) if len(p.strip()) >= 15]
    if len(sents) <= 1:
        return chunk_text
    scored = sorted(
        ((len(claim_kw & _keywords(s)), -len(s) / 1000.0, i, s) for i, s in enumerate(sents)),
        key=lambda x: (-x[0], -x[1], x[2]),
    )
    best_ov, _, _, best_s = scored[0]
    return best_s if best_ov > 0 else chunk_text


def _best_chunk_for_claim(claim_kw: set[str], source: dict[str, Any]) -> tuple[dict[str, Any] | None, float, int]:
    """IDF-weighted chunk retrieval prioritizing rare domain entities (e.g., claudin-23, NLRP3) and body evidence over generic abstract summaries."""
    valid_chunks = [
        ch for ch in source["chunks"]
        if not re.match(r"^(?:References|Bibliography|参考文献)\b", ch.get("section", ""), re.I)
    ]
    if not valid_chunks:
        return None, 0.0, 0

    n_chunks = len(valid_chunks)
    df: dict[str, int] = {}
    for ch in valid_chunks:
        for w in ch["keywords"]:
            df[w] = df.get(w, 0) + 1

    best_chunk = None
    best_idf_score = -1.0
    best_ratio = 0.0
    best_overlap = 0

    for ch in valid_chunks:
        shared = claim_kw & ch["keywords"]
        ov = len(shared)
        if ov == 0:
            continue
        idf_sum = sum(math.log((n_chunks + 1.0) / (df.get(w, 1) + 0.5)) + 1.0 for w in shared)
        # Prefer specific body sections over generic abstract/title when evidence exists in full text
        sec_low = (ch.get("section") or "").lower()
        if n_chunks > 3 and sec_low in ("abstract", "document header"):
            idf_sum *= 0.82
        ratio = ov / max(1, len(claim_kw))
        if idf_sum > best_idf_score or (abs(idf_sum - best_idf_score) < 1e-6 and ov > best_overlap):
            best_idf_score = idf_sum
            best_ratio = ratio
            best_overlap = ov
            best_chunk = ch

    return best_chunk, round(max(0.0, best_ratio), 3), best_overlap


def verify_claims_against_corpus(
    claims: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    draft_text: str,
) -> dict[str, Any]:
    """Evaluate every claim under the strict 5-state rubric + check conflict synthesis quality."""
    evaluated: list[dict[str, Any]] = []
    verdict_counts = {
        "SUPPORTED": 0,
        "PARTIALLY_SUPPORTED": 0,
        "CONTRADICTED": 0,
        "UNVERIFIABLE_ABSENT": 0,
        "EXISTENCE_ONLY_NOT_SUPPORTING": 0,
    }

    ref_bridge = _parse_draft_references_bridge(draft_text)
    for cl in claims:
        cites = cl["citations"]
        ckw = _keywords(cl["claim_text"])

        if not cites:
            verdict = "UNVERIFIABLE_ABSENT"
            reason = "Substantive claim carries no inline citation anchor; cannot trace to supplied literature (not labeled fabricated)."
            evaluated.append({
                **cl,
                "verdict": verdict,
                "matched_sources": [],
                "conflicting_sources": [],
                "mismatch_fields": [],
                "verbatim_excerpts": [],
                "rationale": reason,
                "required_action": "Attach verifiable source citation from /input or mark [EVIDENCE_GAP].",
            })
            verdict_counts[verdict] += 1
            continue

        matched_sources: list[dict[str, Any]] = []
        missing_cites: list[str] = []
        for c in cites:
            hits = _match_citation_to_sources(c, sources, ref_bridge=ref_bridge)
            if hits:
                for h in hits:
                    if h["source_id"] not in {m["source_id"] for m in matched_sources}:
                        matched_sources.append(h)
            else:
                missing_cites.append(c)

        if not matched_sources:
            verdict = "UNVERIFIABLE_ABSENT"
            reason = f"Cited source(s) {missing_cites} are absent from /input corpus; marked unverifiable rather than fabricated."
            evaluated.append({
                **cl,
                "verdict": verdict,
                "matched_sources": [],
                "missing_citations": missing_cites,
                "conflicting_sources": [],
                "mismatch_fields": [],
                "verbatim_excerpts": [],
                "rationale": reason,
                "required_action": f"Supply full text for {missing_cites} or replace ungrounded citation with [EVIDENCE_GAP].",
            })
            verdict_counts[verdict] += 1
            continue

        # Locate best verbatim excerpt per matched source
        excerpts: list[dict[str, Any]] = []
        max_overlap_ratio = 0.0
        max_overlap_cnt = 0
        all_abstract_only = True
        any_rct = False
        any_obs_only = False
        any_preprint = False
        contradiction_hits: list[str] = []
        conflicting_sources: list[dict[str, Any]] = []
        condition_gaps: list[str] = []
        alignment_results: list[dict[str, Any]] = []

        for src in matched_sources:
            if not src["is_abstract_only"]:
                all_abstract_only = False
            if src["study_design"] == "rct_or_intervention":
                any_rct = True
            if src["study_design"] == "observational_correlational":
                any_obs_only = True
            if src["source_type"] == "preprint":
                any_preprint = True

            ch, ratio, ov = _best_chunk_for_claim(ckw, src)
            if ratio > max_overlap_ratio:
                max_overlap_ratio = ratio
            if ov > max_overlap_cnt:
                max_overlap_cnt = ov

            loc_str = f"{src['file']}"
            if ch:
                if ch["page"] is not None:
                    loc_str += f"#page={ch['page']}"
                loc_str += f":{ch['section']}:L{ch['line_start']}-{ch['line_end']}"
                focused_text = _focus_sentence_in_chunk(ckw, ch["text"])
                focused_pol = _detect_polarity(focused_text)
                focused_conds = _extract_conditions(focused_text)
                align_info = _check_claim_excerpt_alignment(
                    cl["claim_text"],
                    focused_text,
                    source_context=f"{src.get('title', '')} {ch.get('section', '')}",
                )
                alignment_results.append(align_info)
                excerpts.append({
                    "source_id": src["source_id"],
                    "source_type": src["source_type"],
                    "study_design": src["study_design"],
                    "extractor": src.get("extractor", "text"),
                    "locator": loc_str,
                    "overlap_ratio": ratio,
                    "overlap_terms": ov,
                    "excerpt_polarity": focused_pol,
                    "alignment_check": align_info,
                    "verbatim_excerpt": focused_text[:320],
                })
                # Polarity contradiction check on focused matching excerpt (strictly separating negative vs null_non_significant, never confusing hedged_uncertain with positive)
                if ov >= 2:
                    if cl["polarity"] == "positive" and focused_pol in ("negative", "null_non_significant"):
                        msg = f"{src['source_id']} ({loc_str}) reports `{focused_pol}` outcome while claim asserts `positive` effect"
                        contradiction_hits.append(msg)
                        conflicting_sources.append({
                            "source_id": src["source_id"],
                            "file": src["file"],
                            "source_polarity": focused_pol,
                            "locator": loc_str,
                            "reason": msg,
                        })
                    elif cl["polarity"] in ("negative", "null_non_significant") and focused_pol == "positive":
                        msg = f"{src['source_id']} ({loc_str}) reports `positive` outcome while claim asserts `{cl['polarity']}` effect"
                        contradiction_hits.append(msg)
                        conflicting_sources.append({
                            "source_id": src["source_id"],
                            "file": src["file"],
                            "source_polarity": focused_pol,
                            "locator": loc_str,
                            "reason": msg,
                        })
                # Applicability condition scope check
                if focused_conds and not cl["conditions"] and cl["claim_strength"] in ("causal", "consensus_broad", "descriptive"):
                    condition_gaps.append(f"{src['source_id']} is bounded by condition(s) {focused_conds[:3]}, omitted in claim")

        # Bind corpus-level opposing/negative/null sources back to this claim if topically overlapping (>=2 shared terms)
        bound_conflict_ids = {cs["source_id"] for cs in conflicting_sources}
        for src in sources:
            if src["source_id"] in bound_conflict_ids:
                continue
            spol = src["overall_polarity"]
            is_opposing = (
                (cl["polarity"] == "positive" and spol in ("negative", "null_non_significant"))
                or (cl["polarity"] in ("negative", "null_non_significant") and spol == "positive")
            )
            if not is_opposing:
                continue
            ch_c, _, ov_c = _best_chunk_for_claim(ckw, src)
            if ch_c and ov_c >= 2:
                loc_c = f"{src['file']}:{ch_c['section']}:L{ch_c['line_start']}-{ch_c['line_end']}"
                focused_c = _focus_sentence_in_chunk(ckw, ch_c["text"])
                conflicting_sources.append({
                    "source_id": src["source_id"],
                    "file": src["file"],
                    "source_polarity": spol,
                    "locator": loc_c,
                    "overlap_terms": ov_c,
                    "reason": f"Corpus source `{src['source_id']}` exhibits opposing polarity (`{spol}`) on shared topic terms (overlap={ov_c}).",
                    "excerpt_preview": focused_c[:200],
                })
                bound_conflict_ids.add(src["source_id"])

        # Collect structural alignment mismatches (model/species/endpoint/comparative boundary)
        # SUPPORTED requires at least one matched excerpt to pass structural alignment!
        any_structurally_aligned = any(a["aligned"] for a in alignment_results) if alignment_results else False
        mismatch_fields: list[dict[str, Any]] = []
        if alignment_results and not any_structurally_aligned:
            for a in alignment_results:
                for mf in a["mismatch_fields"]:
                    if mf["field"] not in {x["field"] for x in mismatch_fields}:
                        mismatch_fields.append(mf)

        # Determine strict 5-state verdict
        issues: list[str] = []
        if all_abstract_only:
            verdict = "UNVERIFIABLE_ABSENT"
            issues.append("Only abstract/stub supplied without full text; cannot verify full methodological support (not labeled fabricated).")
        elif max_overlap_cnt < 2 and max_overlap_ratio < 0.15:
            verdict = "EXISTENCE_ONLY_NOT_SUPPORTING"
            issues.append(f"Cited source(s) exist in corpus, but best lexical/topical alignment is too weak (overlap={max_overlap_cnt}, ratio={max_overlap_ratio}) to support this specific claim.")
        elif contradiction_hits and len(matched_sources) == 1:
            verdict = "CONTRADICTED"
            issues.extend(contradiction_hits)
        elif contradiction_hits and not RE_CONFLICT_SYNTHESIS.search(cl["claim_text"]):
            verdict = "CONTRADICTED"
            issues.extend(contradiction_hits)
            issues.append("Claim cites conflicting evidence without explicitly stating the divergence or boundary conditions.")
        else:
            # Check for PARTIALLY_SUPPORTED causes: causal overclaim, consensus overclaim, missing conditions, model/species/endpoint mismatch, missing partial cites, preprint-only consensus
            partial_reasons: list[str] = []
            if cl["claim_strength"] == "causal" and any_obs_only and not any_rct:
                partial_reasons.append("Causal overclaim: cited source(s) are observational/correlational only; downgrade to associative/hedged phrasing.")
            if cl["claim_strength"] == "consensus_broad" and (len(matched_sources) == 1 or any_preprint):
                partial_reasons.append("Consensus overclaim: single or preprint source cannot support universal/field-consensus wording.")
            if mismatch_fields:
                partial_reasons.extend(mf["detail"] for mf in mismatch_fields)
            if condition_gaps:
                partial_reasons.extend(condition_gaps)
            if missing_cites:
                partial_reasons.append(f"Secondary cited source(s) absent from corpus: {missing_cites}.")

            if partial_reasons:
                verdict = "PARTIALLY_SUPPORTED"
                issues.extend(partial_reasons)
            else:
                verdict = "SUPPORTED"
                issues.append("Claim topic, experimental model/endpoint alignment, epistemic strength, polarity, and scope align with supplied verbatim source excerpt(s).")

        verdict_counts[verdict] += 1
        evaluated.append({
            **cl,
            "verdict": verdict,
            "matched_sources": [
                {"source_id": s["source_id"], "file": s["file"], "source_type": s["source_type"], "study_design": s["study_design"]}
                for s in matched_sources
            ],
            "missing_citations": missing_cites,
            "conflicting_sources": conflicting_sources,
            "mismatch_fields": mismatch_fields,
            "verbatim_excerpts": excerpts,
            "rationale": " ".join(issues),
            "required_action": "None" if verdict == "SUPPORTED" else f"Revise claim to resolve {verdict}: {' '.join(issues)}",
        })

    # Corpus-level conflict & synthesis audit (strictly separating negative direction vs null_non_significant vs hedged_uncertain)
    pos_sources = [s["source_id"] for s in sources if s["overall_polarity"] == "positive"]
    neg_dir_sources = [s["source_id"] for s in sources if s["overall_polarity"] == "negative"]
    null_nonsig_sources = [s["source_id"] for s in sources if s["overall_polarity"] == "null_non_significant"]
    hedged_unc_sources = [s["source_id"] for s in sources if s["overall_polarity"] == "hedged_uncertain"]
    neg_or_null_sources = sorted(set(neg_dir_sources + null_nonsig_sources))
    obs_sources = [s["source_id"] for s in sources if s["study_design"] == "observational_correlational"]
    corpus_has_conflict = bool(pos_sources and neg_or_null_sources)

    # Ensure every detected negative/null source in conflict_audit is traceable to claim-level bindings
    bound_conflict_map = {
        c["claim_id"]: [cs["source_id"] for cs in c.get("conflicting_sources", [])]
        for c in evaluated
        if c.get("conflicting_sources")
    }

    draft_acknowledges_conflict = bool(RE_CONFLICT_SYNTHESIS.search(draft_text))
    draft_explains_conditions = bool(RE_CONDITIONAL.search(draft_text))

    # Detect mechanical paper-by-paper recitation (high proportion of sentences opening with "Study 1 / Author et al. reported..." without synthesis)
    non_empty_paras = [ln.strip() for ln in draft_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    recitation_hits = sum(1 for p in non_empty_paras if RE_PAPER_BY_PAPER_START.match(p))
    paper_by_paper_warning = recitation_hits >= 2 and not draft_acknowledges_conflict

    conflict_audit = {
        "corpus_has_divergent_evidence": corpus_has_conflict,
        "positive_polarity_sources": pos_sources,
        "negative_direction_sources": neg_dir_sources,
        "null_non_significant_sources": null_nonsig_sources,
        "hedged_uncertain_sources": hedged_unc_sources,
        "null_or_negative_polarity_sources": neg_or_null_sources,
        "observational_only_sources": obs_sources,
        "claim_conflicting_source_bindings": bound_conflict_map,
        "draft_acknowledges_divergence": draft_acknowledges_conflict,
        "draft_explains_applicability_conditions": draft_explains_conditions,
        "paper_by_paper_recitation_warning": paper_by_paper_warning,
        "conflict_synthesis_passed": (not corpus_has_conflict) or (draft_acknowledges_conflict and draft_explains_conditions and not paper_by_paper_warning),
    }

    prose_audit = audit_academic_prose(draft_text)

    passed = (
        len(evaluated) > 0
        and verdict_counts["CONTRADICTED"] == 0
        and verdict_counts["EXISTENCE_ONLY_NOT_SUPPORTING"] == 0
        and verdict_counts["PARTIALLY_SUPPORTED"] == 0
        and conflict_audit["conflict_synthesis_passed"]
    )

    return {
        "passed": passed,
        "summary": {
            "total_claims": len(evaluated),
            "verdict_counts": verdict_counts,
            "sources_indexed": len(sources),
            "conflict_synthesis_passed": conflict_audit["conflict_synthesis_passed"],
            "prose_high_issues": prose_audit["high_count"],
            "prose_warning_issues": prose_audit["warning_count"],
        },
        "conflict_synthesis_audit": conflict_audit,
        "prose_audit": prose_audit,
        "claims": evaluated,
    }


def render_evidence_matrix_md(audit: dict[str, Any]) -> str:
    s = audit["summary"]
    vc = s["verdict_counts"]
    ca = audit["conflict_synthesis_audit"]
    lines = [
        "# Literature Evidence Matrix & Synthesis Audit",
        "",
        f"- **Gate Passed**: `{audit['passed']}` | **Total Claims**: `{s['total_claims']}` | **Sources Indexed**: `{s['sources_indexed']}`",
        f"- **5-State Verdicts**: `SUPPORTED={vc['SUPPORTED']}`, `PARTIALLY_SUPPORTED={vc['PARTIALLY_SUPPORTED']}`, `CONTRADICTED={vc['CONTRADICTED']}`, `UNVERIFIABLE_ABSENT={vc['UNVERIFIABLE_ABSENT']}`, `EXISTENCE_ONLY_NOT_SUPPORTING={vc['EXISTENCE_ONLY_NOT_SUPPORTING']}`",
        f"- **Conflict & Condition Synthesis**: divergent_corpus=`{ca['corpus_has_divergent_evidence']}`, acknowledges_divergence=`{ca['draft_acknowledges_divergence']}`, explains_conditions=`{ca['draft_explains_applicability_conditions']}`, paper_by_paper_warning=`{ca['paper_by_paper_recitation_warning']}`",
        "",
        "## Claim-by-Claim Evidence Table",
        "",
        "| Claim ID | Loc | Strength | Citations | Verdict | Conflicting Sources | Source Type / Design | Locator | Verbatim Excerpt | Rationale |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in audit["claims"]:
        cites = ", ".join(c["citations"]) if c["citations"] else "NONE"
        conf_s = ", ".join(cs["source_id"] for cs in c.get("conflicting_sources", [])) or "NONE"
        if c["verbatim_excerpts"]:
            ex0 = c["verbatim_excerpts"][0]
            st_des = f"{ex0['source_type']} / {ex0['study_design']}"
            loc = ex0["locator"]
            quote = ex0["verbatim_excerpt"].replace("|", "/").replace("\n", " ")[:130]
        else:
            st_des = "N/A"
            loc = "ABSENT"
            quote = "—"
        rat = c["rationale"].replace("|", "/").replace("\n", " ")[:140]
        lines.append(
            f"| {c['claim_id']} | L{c['line']} ({c['section']}) | `{c['claim_strength']}` | {cites} | `{c['verdict']}` | {conf_s} | {st_des} | `{loc}` | {quote} | {rat} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence-driven scientific writing guard (5-state verdict + polarity/concessive/double-negation guard + XML/PDF fulltext + dynamic output_contract).")
    parser.add_argument("--draft", required=True, help="Draft manuscript path (.md/.txt/.docx).")
    parser.add_argument("--corpus-dir", required=True, help="Directory of supplied literature full texts (.md/.xml/.html/.pdf/.docx/.txt/.json).")
    parser.add_argument("--input-dir", default="", help="Optional /input directory containing case.json and task.md for dynamic output_contract merging.")
    parser.add_argument("--out-dir", required=True, help="Output directory for claims_list.json, evidence_matrix.md, evidence_audit.json, result.json.")
    parser.add_argument("--case-id", default="CASE-UNSPECIFIED")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any claim fails or conflict synthesis fails.")
    args = parser.parse_args(argv)

    draft_path = Path(args.draft)
    doc = read_document_text(draft_path)
    if not doc["ok"]:
        print(json.dumps({"passed": False, "error": f"Failed to read draft: {doc['meta']}"}, ensure_ascii=False))
        return 2

    corpus_dir = Path(args.corpus_dir)
    in_dir = Path(args.input_dir) if args.input_dir else (corpus_dir if (corpus_dir / "case.json").is_file() else corpus_dir.parent)
    case_contract = load_case_contract(in_dir if in_dir.is_dir() else None)
    effective_case_id = case_contract.get("case_id") or args.case_id

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = index_literature_corpus(corpus_dir, draft_path=draft_path)
    claims = decompose_atomic_claims(doc["text"])
    audit = verify_claims_against_corpus(claims, sources, doc["text"])
    audit["case_constraints_applied"] = case_contract.get("task_constraints", {})

    # Deliverable 1: claims_list.json
    claims_list_path = out_dir / "claims_list.json"
    claims_list_path.write_text(json.dumps({"total_claims": len(claims), "claims": claims}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Deliverable 2: evidence_matrix.md
    matrix_md_path = out_dir / "evidence_matrix.md"
    matrix_md_path.write_text(render_evidence_matrix_md(audit), encoding="utf-8")

    # Deliverable 3: evidence_audit.json
    audit_json_path = out_dir / "evidence_audit.json"
    audit_json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Deliverable 4: structured result.json (merging dynamic case.json output_contract fields)
    gaps: list[dict[str, Any]] = []
    for c in audit["claims"]:
        if c["verdict"] != "SUPPORTED":
            gaps.append({"claim_id": c["claim_id"], "verdict": c["verdict"], "rationale": c["rationale"]})
    if not audit["conflict_synthesis_audit"]["conflict_synthesis_passed"]:
        gaps.append({"code": "CONFLICT_SYNTHESIS_INCOMPLETE", "detail": audit["conflict_synthesis_audit"]})

    res = build_writing_result(
        case_id=effective_case_id,
        task_id="evidence-driven-writing",
        deliverable={
            "draft_file": draft_path.name,
            "claims_list_json": claims_list_path.name,
            "evidence_matrix_md": matrix_md_path.name,
            "evidence_audit_json": audit_json_path.name,
        },
        preserved_facts={
            "draft_invariants": extract_invariants(doc["text"]),
            "sources_indexed_count": len(sources),
            "verdict_counts": audit["summary"]["verdict_counts"],
            "conflict_synthesis_passed": audit["conflict_synthesis_audit"]["conflict_synthesis_passed"],
        },
        evidence_map=[
            {
                "claim_id": c["claim_id"],
                "section": c["section"],
                "line": c["line"],
                "claim_strength": c["claim_strength"],
                "citations": c["citations"],
                "verdict": c["verdict"],
                "conflicting_sources": c.get("conflicting_sources", []),
                "mismatch_fields": c.get("mismatch_fields", []),
                "locators": [e["locator"] for e in c["verbatim_excerpts"]],
                "verbatim_excerpts": [e["verbatim_excerpt"] for e in c["verbatim_excerpts"]],
            }
            for c in audit["claims"]
        ],
        change_log=[
            {
                "claim_id": c["claim_id"],
                "verdict": c["verdict"],
                "action": c["required_action"],
            }
            for c in audit["claims"]
            if c["verdict"] != "SUPPORTED"
        ],
        artifacts=[draft_path.name, claims_list_path.name, matrix_md_path.name, audit_json_path.name, "result.json"],
        sources=[{"source_id": s["source_id"], "file": s["file"], "source_type": s["source_type"], "study_design": s["study_design"], "extractor": s.get("extractor", "text")} for s in sources],
        gaps=gaps,
        limitations=[
            "Offline verification inspects only files supplied under /input (.md/.xml/.html/.pdf/.docx/.txt/.json); absent or abstract-only sources are marked UNVERIFIABLE_ABSENT rather than fabricated.",
        ],
        verification={
            "evidence_guard_passed": audit["passed"],
            "summary": audit["summary"],
        },
        status="ok" if audit["passed"] and not gaps else "partial_with_gaps",
        case_contract=case_contract,
    )
    (out_dir / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"passed": audit["passed"], **audit["summary"]}, ensure_ascii=False))
    if args.strict and not audit["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
