#!/usr/bin/env python3
"""Zero-dependency scientific argument-chain, causal-boundary, cross-section consistency,
and multi-round revision-closure auditor for challenge-16d4414d.

Runs purely on Python 3.10+ stdlib (offline Linux Docker --network none) using local review_io_core.py.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_io_core as io_core  # noqa: E402

# --- Sentence identity & domain pattern definitions ---

CITATION_RE = re.compile(
    r"\\cite[pt]?\*?\{[^}]+\}|\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]|"
    r"\([A-Z][A-Za-z'-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'-]+)?(?:\s+et\s+al\.)?,?\s*(?:19|20)\d{2}[a-z]?\)"
)

PRIOR_ATTRIBUTION_RE = re.compile(
    r"\b(?:prior\s+(?:studies|work|research|reports?|literature)|"
    r"previous(?:ly)?\s+(?:studies|work|research|reports?|literature|reported|claimed|argued|suggested|found|showed)|"
    r"earlier\s+(?:studies|work|reports?|findings)|"
    r"according\s+to\s+(?:[A-Z][A-Za-z'-]+|\[\d+\]|prior|previous|earlier)|"
    r"et\s+al\.\s*(?:\(\d{4}[a-z]?\))?\s*(?:reported|claimed|argued|suggested|found|showed|demonstrated|concluded|proposed|proved|established|asserted)|"
    r"[A-Z][A-Za-z'-]+\s+(?:and|&)\s+[A-Z][A-Za-z'-]+\s*\(\d{4}[a-z]?\)\s*(?:reported|claimed|argued|suggested|found|showed|demonstrated|concluded|proposed|proved|established|asserted)|"
    r"in\s+the\s+literature|other\s+authors\s+(?:have\s+)?(?:argued|claimed|reported|suggested)|"
    r"some\s+(?:studies|authors|researchers)\s+(?:argue|claim|suggest|report))\b|"
    r"前人(?:研究|工作|文献)?(?:认为|指出|报道|声称|提出|发现)|"
    r"已有(?:研究|文献|报道)(?:认为|指出|表明|声称|发现)|"
    r"既往(?:研究|文献|报道)(?:认为|指出|显示|声称)|"
    r"文献\s*\[\d+\]\s*(?:认为|指出|报道|声称)",
    re.I,
)

AUTHOR_FIRST_PERSON_RE = re.compile(
    r"\b(?:we|our\s+(?:results?|findings?|data|study|analysis|experiments?|model|work|cohort|retrospective|observational)|"
    r"this\s+(?:study|paper|work|analysis|cohort)|the\s+present\s+study|here\s+we)\b|"
    r"本研究|本文|我们的(?:结果|数据|研究|实验|分析|队列)",
    re.I,
)

# Split genuine non-author-result controls/hypotheses/limitations from comparative author results
# ("compared with the control/placebo group, our retrospective cohort proves..." MUST remain author_claim!)
HYPOTHESIS_OR_LIMITATION_RE = re.compile(
    r"\b(?:hypothesi[sz](?:e|ed|ing)|hypothesis|"
    r"suppose\s+that|assuming\s+that|assume\s+that|under\s+the\s+(?:null|assumption)|"
    r"if\s+true|hypothetically|counterfactual\s+scenario|"
    r"as\s+a\s+limitation|limitation(?:s)?\s+of\s+(?:this|our)\s+study|a\s+key\s+limitation|caveat|"
    r"exploratory\s+(?:analysis|finding)|preliminary\s+(?:evidence|finding)|"
    r"future\s+(?:work|studies|trials?|randomized|RCTs?)\s+(?:should|will|are\s+needed|is\s+needed))\b|"
    r"假设|假定|倘若|作为局限|本研究(?:的)?局限|探索性分析|初步发现|未来研究",
    re.I,
)

PURE_QC_CONTROL_RE = re.compile(
    r"\b(?:negative\s+control|positive\s+control|sham\s+control|"
    r"vehicle\s+control|blank\s+control|"
    r"ablation(?:\s+study|\s+experiment|\s+setting))\b|"
    r"阴性对照|阳性对照|空白对照|假手术组|消融(?:实验|研究|组)",
    re.I,
)

COMPARATIVE_RESULT_CUE_RE = re.compile(
    r"\b(?:compared\s+(?:with|to)|versus|vs\.?|relative\s+to)\b|与.{0,12}相比|相较于",
    re.I,
)

FUTURE_RCT_RE = re.compile(
    r"\b(?:future|prospective|upcoming|further|planned)\s+(?:randomized|RCT|interventional|clinical\s+trials?)\b|"
    r"未来(?:需要|应开展)?随机对照",
    re.I,
)

NEGATED_CAUSAL_RE = re.compile(
    r"\b(?:does\s+not\s+(?:prove|establish|confirm|imply)\s+(?:a\s+)?caus|"
    r"cannot\s+(?:prove|establish|confirm|infer|conclude)\s+(?:a\s+)?caus|"
    r"not\s+(?:establish|prove|imply)\s+caus|cannot\s+prove\s+that|"
    r"does\s+not\s+prove\s+that|non-?causal|association\s+rather\s+than\s+caus|"
    r"correlation\s+does\s+not\s+imply\s+caus|observational\s+nature\s+precludes\s+caus)\b|"
    r"(?:尚)?不能(?:证明|确立|推断|得出)(?:直接)?因果|并非因果|仅表明相关(?:而非因果)?|不构成因果证据",
    re.I,
)

HEDGED_MODALITY_RE = re.compile(
    r"\b(?:may|might|could|suggests?|suggesting|appears?\s+to|is\s+consistent\s+with|"
    r"is\s+associated\s+with|correlates?\s+with|potentially|tentatively|plausibly)\b|"
    r"可能|提示|倾向于|与.{0,12}相关|关联",
    re.I,
)

CAUSAL_STRONG_RE = re.compile(
    r"\b(?:proves?|proved|proven\s+to\s+cause|directly\s+causes?|causes?|caused|"
    r"causally\s+drives?|drives?\s+the|triggers?\s+the|guarantees?|guaranteed|"
    r"establishes?\s+(?:a\s+)?(?:direct\s+)?caus(?:al|ality)|"
    r"confirms?\s+(?:a\s+)?caus(?:al|ality)|conclusively\s+(?:proves?|demonstrates?)|"
    r"definitively\s+(?:proves?|establishes?)|always\s+leads\s+to|solely\s+responsible\s+for)\b|"
    r"证明了|完全证明|确证了|确立了(?:直接)?因果|证实了.{0,10}因果|直接导致|必然导致|普遍成立|决定了.{0,8}发生",
    re.I,
)

OBSERVATIONAL_DESIGN_RE = re.compile(
    r"\b(?:observational|correlational|cross-sectional|retrospective|case-control|"
    r"ecological\s+study|survey-based|questionnaire\s+survey|non-randomized|"
    r"prospective\s+cohort|observational\s+cohort|electronic\s+health\s+records?|"
    r"registry\s+data|association\s+study|descriptive\s+cohort)\b|"
    r"观察性|相关性研究|横断面|回顾性|病例对照|生态学研究|问卷调查|非随机|队列研究|真实世界登记",
    re.I,
)

INTERVENTIONAL_DESIGN_RE = re.compile(
    r"\b(?:randomized\s+controlled(?:\s+trial)?|\bRCT\b|double-blind\s+randomized|"
    r"placebo-controlled\s+trial|gene\s+knockout|CRISPR(?:/Cas9)?\s+knockout|"
    r"mendelian\s+randomization|instrumental\s+variable|regression\s+discontinuity|"
    r"difference-in-differences|interventional\s+trial|controlled\s+perturbation|"
    r"causal\s+identification\s+strategy|natural\s+experiment)\b|"
    r"随机对照(?:试验)?|双盲随机|基因敲除|CRISPR敲除|孟德尔随机化|工具变量|断点回归|双重差分|干预试验",
    re.I,
)

INTRO_PROMISE_RE = re.compile(
    r"\b(?:we\s+(?:aim|seek|intend|propose)\s+to|"
    r"we\s+(?:test|investigate|examine|evaluate|quantify|assess|compare|analyze|analyse)\s+(?:whether|how|if|the)|"
    r"our\s+(?:primary\s+|secondary\s+|main\s+|specific\s+)?(?:aim|objective|goal|hypothesis|question)\s+(?:is|was|are)|"
    r"this\s+(?:study|paper|work)\s+(?:aims|seeks|investigates|evaluates|tests|examines)|"
    r"(?:first|second|third|finally),?\s+we\s+(?:investigate|test|evaluate|examine|measure|analyze|assess))\b|"
    r"本研究(?:旨在|拟|将)?(?:检验|探讨|评估|验证|分析|比较|考察)|"
    r"本文(?:旨在|拟|主要)?(?:研究问题|检验|评估|验证|探讨|考察)|"
    r"具体(?:而言|来说)，我们(?:将|拟)?(?:评估|检验|分析|探讨|考察)",
    re.I,
)

LIMITATION_RE = re.compile(
    r"\b(?:limitations?|caveats?|shortcomings?|threats?\s+to\s+validity|"
    r"boundary\s+conditions?|generalizability\s+(?:is\s+limited|caveat|remains)|"
    r"cannot\s+be\s+generalized|should\s+be\s+interpreted\s+with\s+caution|"
    r"future\s+(?:work|studies|validation)\s+(?:is|are)\s+needed)\b|"
    r"局限(?:性)?|不足之处|边界条件|适用范围|外推受限|谨慎解读|尚需(?:进一步)?验证|未来工作",
    re.I,
)

CONSTRAINED_SCALE_RE = re.compile(
    r"\b(?:in\s+vitro|cell\s+lines?|HeLa|HEK293|MCF-?7|A549|organoids?|"
    r"mouse\s+model|murine\s+model|mice\s*\(|C57BL/6|BALB/c|rats?\s*\(|zebrafish|"
    r"preclinical\s+model|animal\s+model|single-?center|single\s+institution|"
    r"pilot\s+(?:study|cohort)|small\s+(?:sample|cohort)|ex\s+vivo|cultured\s+cells?)\b|"
    r"体外(?:实验|细胞)|细胞系|类器官|小鼠(?:模型)?|大鼠(?:模型)?|动物模型|临床前模型|单中心|小样本|预实验",
    re.I,
)

MULTICENTER_CLINICAL_RE = re.compile(
    r"\b(?:phase\s+(?:II|III|2|3)\s+(?:clinical\s+)?trial|multi-?center\s+(?:randomized|clinical)|"
    r"human\s+clinical\s+trial|randomized\s+clinical\s+trial|enrolled\s+\d{3,}\s+patients\s+across\s+\d+\s+centers)\b|"
    r"多中心(?:随机对照)?临床试验|II/III期临床|III期临床试验",
    re.I,
)

OVER_EXTRAPOLATION_RE = re.compile(
    r"\b(?:clinically\s+proven|proven\s+clinical\s+efficacy|ready\s+for\s+(?:immediate\s+)?clinical\s+(?:use|deployment|adoption)|"
    r"universal(?:ly)?\s+(?:effective|applicable|therapeutic|therapy)|"
    r"cures?\s+(?:patients|human|cancer|disease)|definitive\s+(?:clinical\s+)?treatment|"
    r"applies?\s+to\s+all\s+patients|generalizes?\s+across\s+all\s+(?:populations|patients|clinical\s+settings)|"
    r"new\s+standard\s+of\s+care|establishes?\s+clinical\s+efficacy\s+in\s+humans?)\b|"
    r"临床普适(?:疗效|有效)|普适性临床|可直接用于临床(?:治疗|应用)|适用于所有患者|"
    r"确立了.{0,8}临床疗效|取代现有临床标准|普遍适用于人类|治愈人类",
    re.I,
)

EVIDENCE_ANCHOR_RE = re.compile(
    r"\b(?:Table|Tab\.?|Fig(?:ure)?\.?|Suppl(?:ementary)?\.?\s*(?:Fig|Table))\s*\d+[A-Za-z]?|"
    r"\bp\s*[<=>]\s*0?\.\d+|\b95%\s*CI|\b(?:OR|HR|RR|AUC|F1|RMSE|R2|R\^2)\s*[=:]\s*[\d.]+|"
    r"\bn\s*=\s*\d+|\d+(?:\.\d+)?\s*[%％]|(?:图|表)\s*\d+[A-Za-z]?",
    re.I,
)

PERCENT_RE = re.compile(r"(?<![\d.,])([+−-]?\d+(?:\.\d+)?)\s*[%％]")

DIR_UP_RE = re.compile(
    r"\b(?:increas(?:e|ed|es|ing)|higher|elevat(?:ed|ion)|improv(?:e|ed|es|ement)|"
    r"gain(?:ed|s)?|rose|risen|grew|growth|upregulat(?:ed|ion)|boost(?:ed)?|outperform(?:ed|s)?|longer\s+survival)\b|"
    r"增加|上升|提高|升高|改善|上调|增长|优于|显著高于",
    re.I,
)

DIR_DOWN_RE = re.compile(
    r"\b(?:decreas(?:e|ed|es|ing)|lower|reduc(?:e|ed|es|tion)|declin(?:e|ed|es|ing)|"
    r"drop(?:ped|s)?|fell|fallen|worsen(?:ed|s|ing)?|downregulat(?:ed|ion)|underperform(?:ed|s)?|shorter\s+survival)\b|"
    r"下降|降低|减少|恶化|下调|减弱|劣于|显著低于",
    re.I,
)

SIG_POS_RE = re.compile(
    r"\b(?:statistically\s+significant|significantly\s+(?:higher|lower|increased|decreased|improved|reduced|associated|different))\b|"
    r"统计学显著|显著(?:提高|降低|增加|减少|改善|相关|差异)",
    re.I,
)

SIG_NEG_RE = re.compile(
    r"\b(?:not\s+statistically\s+significant|non-?significant|no\s+significant\s+(?:difference|effect|association|change|correlation|reduction|improvement)|"
    r"failed\s+to\s+reach\s+(?:statistical\s+)?significance|lacked\s+statistical\s+significance)\b|"
    r"无统计学(?:意义|显著)|不显著|未达到显著",
    re.I,
)

P_VALUE_RE = re.compile(r"\bp\s*([<=>≤≥])\s*(0?\.\d+|\.\d+)(?!\d)", re.I)

# Target-metric semantic polarity mapping so equivalent inverse-polarity phrasings
# (e.g., "improved survival" vs "reduced mortality", "higher accuracy" vs "lower error") never trigger false reversals.
COMPOUND_POS_METRICS_RE = re.compile(
    r"\b(?:progression-?free\s+survival|relapse-?free\s+survival|disease-?free\s+survival|"
    r"recurrence-?free\s+survival|event-?free\s+survival|overall\s+survival)\b",
    re.I,
)
POS_POLARITY_METRICS: dict[str, set[str]] = {
    "clinical_benefit": {
        "survival", "os", "pfs", "rfs", "dfs", "remission", "response",
        "recovery", "cure", "efficacy", "benefit", "生存", "生存率", "缓解", "缓解率", "疗效",
    },
    "model_performance": {
        "accuracy", "precision", "recall", "sensitivity", "specificity",
        "auc", "auroc", "f1", "concordance", "准确率", "精确率", "灵敏度", "特异度",
    },
}
NEG_POLARITY_METRICS: dict[str, set[str]] = {
    "clinical_benefit": {
        "mortality", "death", "deaths", "fatality", "lethality", "relapse",
        "recurrence", "progression", "risk", "hazard", "complication",
        "complications", "toxicity", "adverse", "failure", "incidence",
        "死亡", "死亡率", "病死率", "复发", "复发率", "进展", "风险", "毒性", "不良反应",
    },
    "model_performance": {
        "error", "errors", "loss", "rmse", "mae", "mse", "fpr", "fnr",
        "misclassification", "错误率", "误差", "损失",
    },
}

# ACL H1-H17 fairness firewall patterns (active both internally and via --audit-findings CLI gate)
ACL_FIREWALL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("H1", re.compile(r"\b(?:unsurprising|obvious\s+result|already\s+expected|intuitive\s+only)\b|结果不令人惊讶", re.I)),
    ("H2", re.compile(r"\b(?:not\s+my\s+preferred\s+framing|should\s+have\s+studied\s+a\s+different\s+problem)\b|应该换个问题研究", re.I)),
    ("H3", re.compile(r"\b(?:lacks?\s+novelty|not\s+novel\s+enough|incremental\s+only|limited\s+novelty)\b|缺乏新颖性", re.I)),
    ("H4", re.compile(r"\b(?:fails?\s+to\s+cite\s+my\s+paper|missing\s+uncited\s+recent\s+preprint)\b|未引用最新预印本", re.I)),
    ("H5", re.compile(r"\b(?:does\s+not\s+beat\s+SOTA|not\s+state-of-the-art|fails?\s+to\s+outperform\s+SOTA|below\s+SOTA)\b|未超过最新\s*SOTA", re.I)),
    ("H6", re.compile(r"\b(?:negative\s+result|null\s+result\s+is\s+uninteresting|non-?significant\s+finding\s+has\s+no\s+value)\b|负面结果没有价值", re.I)),
    ("H7", re.compile(r"\b(?:method\s+is\s+too\s+simple|overly\s+simple\s+approach|not\s+complex\s+enough)\b|方法太简单", re.I)),
    ("H8", re.compile(r"\b(?:not\s+using\s+deep\s+learning|should\s+use\s+LLM\s+instead)\b|未使用大模型", re.I)),
    ("H9", re.compile(r"\b(?:topic\s+is\s+too\s+narrow|niche\s+domain|narrow\s+application)\b|领域太小众", re.I)),
    ("H10", re.compile(r"\b(?:non-?native\s+English|awkward\s+phrasing\s+warrants\s+rejection)\b|英语非母语", re.I)),
    ("H11", re.compile(r"\b(?:small\s+compute\s+budget|not\s+trained\s+at\s+billions\s+of\s+parameters)\b|算力规模不够大", re.I)),
    ("H12", re.compile(r"\b(?:dataset\s+is\s+not\s+English|only\s+evaluated\s+on\s+non-?English)\b|非英语数据集", re.I)),
    ("H13", re.compile(r"\b(?:authors?\s+could\s+(?:also\s+)?(?:run|add|perform)\s+additional\s+experiments?|nice\s+to\s+have\s+extra\s+experiments?)\b|可以顺便再多做实验", re.I)),
    ("H14", re.compile(r"\b(?:demands?\s+completely\s+new\s+dataset\s+collection\s+outside\s+scope)\b|要求额外采集全新数据集", re.I)),
    ("H15", re.compile(r"\b(?:reject\s+because\s+of\s+minor\s+formatting|typo\s+warrants\s+rejection)\b|因轻微排版拒稿", re.I)),
    ("H16", re.compile(r"\b(?:admitting\s+limitations?\s+makes?\s+it\s+weak|disclosed\s+limitation\s+is\s+a\s+fatal\s+flaw)\b|承认局限说明论文有硬伤", re.I)),
    ("H17", re.compile(r"\b(?:speculative\s+unverified\s+concern|might\s+possibly\s+be\s+wrong\s+without\s+evidence)\b|无证据猜测", re.I)),
]

STOPWORDS = {
    "that", "this", "with", "from", "whether", "study", "using", "based", "between",
    "their", "these", "which", "have", "were", "been", "into", "such", "also", "more",
    "than", "over", "under", "across", "through", "about", "after", "before", "during",
    "results", "method", "methods", "analysis", "model", "models", "data", "paper",
    "work", "present", "current", "first", "second", "third", "finally", "examine",
    "investigate", "evaluate", "test", "assess", "compare", "quantify", "objective",
    "aim", "goal", "question", "hypothesis", "effect", "effects", "impact", "role",
    "show", "shown", "showed", "found", "observed", "reported", "significant",
    "significantly", "increased", "decreased", "higher", "lower", "reduced", "improved",
    "我们", "本研究", "本文", "研究", "结果", "方法", "分析", "数据", "模型", "实验",
    "评估", "检验", "探讨", "考察", "验证", "比较", "影响", "作用", "表明", "显示",
    "显著", "提高", "降低", "增加", "减少", "改善", "恶化", "上升", "下降",
}


def _extract_keywords(text: str) -> set[str]:
    low = text.lower()
    en_toks = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", low)
    zh_toks = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    out: set[str] = set()
    for t in en_toks:
        t_clean = t.strip("_-")
        if len(t_clean) >= 3 and t_clean not in STOPWORDS and not t_clean.isdigit():
            out.add(t_clean)
    # Preserve named entities like "drug x", "biomarker y", "gene a"
    for m in re.finditer(r"\b(?:drug|agent|compound|biomarker|protein|gene|pathway|treatment|therapy|cohort)\s+[a-z0-9]{1,5}\b", low):
        out.add(m.group(0))
    for z in zh_toks:
        if z not in STOPWORDS:
            out.add(z)
    return out


def _page_of(doc: dict[str, Any], line: int) -> int | None:
    return doc.get("page_map", {}).get(line)


def classify_sentence_identity(text: str, section_role: str) -> tuple[str, str]:
    """Classify a sentence into author_claim, cited_prior_view, or explicit_control_or_hypothesis."""
    if NEGATED_CAUSAL_RE.search(text):
        return "explicit_control_or_hypothesis", "negated_or_delimited_causal_disclaimer"
    if HYPOTHESIS_OR_LIMITATION_RE.search(text):
        return "explicit_control_or_hypothesis", "hypothesis_or_author_limitation"
    if PURE_QC_CONTROL_RE.search(text):
        # Preserve comparative author causal claims (e.g., "Compared with the negative control, our cohort proves...")
        if not (COMPARATIVE_RESULT_CUE_RE.search(text) and AUTHOR_FIRST_PERSON_RE.search(text) and CAUSAL_STRONG_RE.search(text)):
            return "explicit_control_or_hypothesis", "control_qc_or_ablation_description"

    has_cite = bool(CITATION_RE.search(text))
    has_prior_attr = bool(PRIOR_ATTRIBUTION_RE.search(text))
    has_first_person = bool(AUTHOR_FIRST_PERSON_RE.search(text))

    if has_prior_attr and not has_first_person:
        return "cited_prior_view", "prior_literature_attribution"
    if has_prior_attr and has_cite and not re.search(
        r"\b(?:our\s+(?:results?|data|findings?)\s+(?:prove|confirm|cause|show)|we\s+(?:prove|confirm|demonstrate))\b|本研究证明|我们的结果证明",
        text,
        re.I,
    ):
        return "cited_prior_view", "cited_prior_attribution"
    if section_role == "related_work" and has_cite and not has_first_person:
        return "cited_prior_view", "related_work_citation"
    if has_cite and not has_first_person:
        # Check if sentence attributes a claim to the cited source rather than reporting current measurements
        if not re.search(r"\b(?:Table|Fig(?:ure)?\.?)\s*\d+|图\s*\d+|表\s*\d+", text, re.I):
            return "cited_prior_view", "cited_non_first_person_statement"
    return "author_claim", "direct_author_assertion"


def _classify_doc_roles(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Classify input documents into manuscripts, v1/v2 revision pairs, prior reviews, and author responses."""
    roles: dict[str, list[dict[str, Any]]] = {
        "prior_review": [],
        "author_response": [],
        "manuscript_v1": [],
        "manuscript_v2": [],
        "manuscript": [],
    }
    untyped_manuscripts: list[dict[str, Any]] = []

    for doc in docs:
        name = Path(doc["file"]).name.lower()
        stem = Path(doc["file"]).stem.lower()
        if any(k in stem for k in ("rebuttal", "response", "reply", "point_by_point", "point-by-point", "答复", "回复")):
            roles["author_response"].append(doc)
        elif any(k in stem for k in ("review", "comment", "referee", "critique", "审稿意见", "评审意见")) and not any(
            k in stem for k in ("manuscript", "paper", "draft")
        ):
            roles["prior_review"].append(doc)
        elif re.search(
            r"(?:^|[_\-\s])(v1|r0|r1|round[_\-]?1|rev[_\-]?1|draft[_\-]?1|old|orig|original|initial|prev|previous)(?:$|[_\-\s.])",
            name,
        ):
            roles["manuscript_v1"].append(doc)
        elif re.search(
            r"(?:^|[_\-\s])(v2|v3|r2|r3|round[_\-]?[23]|rev[_\-]?[23]|draft[_\-]?[23]|new|revised|revision|final|updated|current)(?:$|[_\-\s.])",
            name,
        ):
            roles["manuscript_v2"].append(doc)
        else:
            untyped_manuscripts.append(doc)

    if roles["manuscript_v1"] and roles["manuscript_v2"]:
        roles["manuscript"] = list(roles["manuscript_v2"]) + untyped_manuscripts
    elif roles["manuscript_v2"]:
        roles["manuscript"] = list(roles["manuscript_v2"]) + untyped_manuscripts
    elif roles["manuscript_v1"]:
        roles["manuscript"] = list(roles["manuscript_v1"]) + untyped_manuscripts
    else:
        roles["manuscript"] = untyped_manuscripts

    return roles


def _detect_direction(text: str) -> str | None:
    up = bool(DIR_UP_RE.search(text))
    down = bool(DIR_DOWN_RE.search(text))
    if up and not down:
        return "increase"
    if down and not up:
        return "decrease"
    return None


def _extract_metric_polarity(text: str) -> tuple[str | None, int | None, set[str]]:
    """Return (domain_family, polarity +1/-1, matched_terms) for target outcome metrics."""
    low = text.lower()
    matched_pos: dict[str, set[str]] = {}
    matched_neg: dict[str, set[str]] = {}

    # Handle compound positive survival metrics first so "progression-free survival" is strictly positive
    masked = low
    for m in COMPOUND_POS_METRICS_RE.finditer(low):
        matched_pos.setdefault("clinical_benefit", set()).add(m.group(0).strip())
        masked = masked.replace(m.group(0), " ")

    toks = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,4}", masked))
    for fam, vocab in POS_POLARITY_METRICS.items():
        hits = {w for w in vocab if (w in toks if w.isascii() else w in masked)}
        if hits:
            matched_pos.setdefault(fam, set()).update(hits)
    for fam, vocab in NEG_POLARITY_METRICS.items():
        hits = {w for w in vocab if (w in toks if w.isascii() else w in masked)}
        if hits:
            matched_neg.setdefault(fam, set()).update(hits)

    for fam in ("clinical_benefit", "model_performance"):
        pos_h = matched_pos.get(fam, set())
        neg_h = matched_neg.get(fam, set())
        if pos_h and not neg_h:
            return fam, 1, pos_h
        if neg_h and not pos_h:
            return fam, -1, neg_h
    return None, None, set()


def _detect_significance(text: str) -> str | None:
    neg = bool(SIG_NEG_RE.search(text))
    pos = bool(SIG_POS_RE.search(text))
    if neg:
        return "not_significant"

    p_vals: list[tuple[str, float]] = []
    for m in P_VALUE_RE.finditer(text):
        op = m.group(1)
        try:
            val = float(m.group(2))
            p_vals.append((op, val))
        except ValueError:
            continue

    has_numeric_sig = any(
        (op == "<" and val <= 0.05) or (op in ("=", "<=", "≤") and val < 0.05)
        for op, val in p_vals
    )
    has_numeric_nonsig = any(
        (op in ("=", ">", ">=", "≥") and val > 0.05) or (op == ">" and val >= 0.05)
        for op, val in p_vals
    )

    if has_numeric_nonsig and not has_numeric_sig and not pos:
        return "not_significant"
    if pos or (has_numeric_sig and not has_numeric_nonsig):
        return "significant"
    return None


def _extract_filtered_percents(text: str) -> list[tuple[str, Decimal]]:
    """Extract outcome percentages while excluding 95%/99%/90% CI, 5% alpha, and 80%/90% statistical power."""
    pcts: list[tuple[str, Decimal]] = []
    for m in PERCENT_RE.finditer(text):
        raw_num = m.group(1).replace("−", "-")
        try:
            val = Decimal(raw_num)
        except InvalidOperation:
            continue
        after = text[m.end():m.end() + 28].lower()
        before = text[max(0, m.start() - 24):m.start()].lower()
        # Exclude confidence/credible/prediction interval levels (e.g., 95% CI, 99% confidence interval)
        if val in (Decimal("90"), Decimal("95"), Decimal("99")) and re.match(
            r"^\s*(?:ci\b|cri\b|pi\b|confidence|credible|prediction)", after
        ):
            continue
        # Exclude statistical power (e.g., 80% power, 90% statistical power)
        if val in (Decimal("80"), Decimal("85"), Decimal("90"), Decimal("95")) and (
            re.match(r"^\s*(?:statistical\s+)?power\b", after)
            or re.search(r"power\s*(?:of|=|:)?\s*$", before)
        ):
            continue
        # Exclude significance level alpha (e.g., 5% significance level, alpha = 5%)
        if val in (Decimal("1"), Decimal("5"), Decimal("10")) and (
            re.match(r"^\s*(?:significance|alpha|level|type\s+i)", after)
            or re.search(r"(?:alpha\s*=|at\s+the\s+|significance\s+level\s+of\s+)$", before)
        ):
            continue
        pcts.append((m.group(0), val))
    return pcts


def _extract_quant_records(
    sections: list[dict[str, Any]],
    doc: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for sec in sections:
        for sent in io_core.split_sentences(sec):
            st = sent["text"]
            identity, _ = classify_sentence_identity(st, sec["role"])
            if identity != "author_claim":
                continue
            pcts = _extract_filtered_percents(st)
            direction = _detect_direction(st)
            sig = _detect_significance(st)
            fam, pol, metric_terms = _extract_metric_polarity(st)
            if pcts or direction or sig:
                ls = sent.get("line_start", sent["line"])
                le = sent.get("line_end", ls)
                records.append({
                    "file": doc["file"],
                    "section": sec["title"],
                    "role": sec["role"],
                    "line": ls,
                    "line_start": ls,
                    "line_end": le,
                    "page": _page_of(doc, ls),
                    "text": st,
                    "percents": pcts,
                    "direction": direction,
                    "significance": sig,
                    "metric_family": fam,
                    "metric_polarity": pol,
                    "metric_terms": metric_terms,
                    "keywords": _extract_keywords(st),
                })
    return records


def _split_objectives(sent_text: str) -> list[str]:
    """Split compound objective sentences like '(1) ...; (2) ...; and (3) ...' into individual clauses."""
    # Check numbered clauses (1)... (2)... or (a)... (b)...
    if re.search(r"\([1-5]\)|[①②③④⑤]", sent_text):
        parts = re.split(r"(?=\([1-5]\)|[①②③④⑤])", sent_text)
        cleaned = [p.strip(" ;,.") for p in parts if len(p.strip()) >= 15]
        if len(cleaned) >= 2:
            return cleaned
    if ";" in sent_text or "；" in sent_text:
        parts = [p.strip(" ;,.") for p in re.split(r"[;；]", sent_text) if len(p.strip()) >= 15]
        if len(parts) >= 2:
            return parts
    return [sent_text]


def _extract_numbered_items(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured reviewer comments or author responses from markdown/plain text."""
    items: list[dict[str, Any]] = []
    item_header_re = re.compile(
        r"^(?:#{1,6}\s*)?(?:[-*]\s*)?"
        r"(?:(?:Major|Minor|General)?\s*(?:Reviewer\s*\d*\s*)?(?:Comment|Point|Issue|Response|Reply|Rebuttal|R\d+)\s*#?\s*([A-Za-z0-9.\-]+)"
        r"|意见\s*([0-9一二三四五六七八九十]+)|回复\s*([0-9一二三四五六七八九十]+)"
        r"|(\d+)[\.\)）、])\s*[:：\-]?\s*(.*)$",
        re.I,
    )
    cur_id: str | None = None
    cur_line = 1
    buf: list[str] = []

    def flush() -> None:
        if cur_id is not None and buf:
            txt = " ".join(x.strip() for x in buf if x.strip()).strip()
            if len(txt) >= 12:
                items.append({
                    "item_id": cur_id,
                    "file": doc["file"],
                    "line": cur_line,
                    "text": txt,
                    "keywords": _extract_keywords(txt),
                })

    for lno, raw in enumerate(doc["lines"], 1):
        st = raw.strip()
        if not st:
            continue
        m = item_header_re.match(st)
        if m:
            flush()
            cid = next((g for g in m.groups()[:4] if g), str(len(items) + 1))
            cur_id = cid.strip(".:：")
            cur_line = lno
            rest = (m.group(5) or "").strip()
            buf = [rest] if rest else []
        elif cur_id is not None:
            # Stop collecting if top-level unrelated heading appears
            if re.match(r"^#\s+", st):
                flush()
                cur_id = None
                buf = []
            else:
                buf.append(st)
    flush()
    return items


def _compute_diff_additions(v1_doc: dict[str, Any], v2_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute exact added/modified lines in v2 compared to v1 using stdlib difflib."""
    v1_lines = v1_doc["lines"]
    v2_lines = v2_doc["lines"]
    matcher = difflib.SequenceMatcher(None, v1_lines, v2_lines)
    # Map v2 line numbers to section titles
    sec_by_line: dict[int, str] = {}
    for sec in io_core.split_sections(v2_doc):
        for ln, _ in sec["lines"]:
            sec_by_line[ln] = sec["title"]
        sec_by_line[sec["start_line"]] = sec["title"]

    additions: list[dict[str, Any]] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            for idx in range(j1, j2):
                line_text = v2_lines[idx].strip()
                if len(line_text) >= 8 and not line_text.startswith("#"):
                    additions.append({
                        "file": v2_doc["file"],
                        "line": idx + 1,
                        "section": sec_by_line.get(idx + 1, "(preamble)"),
                        "op": tag,
                        "text": line_text,
                        "keywords": _extract_keywords(line_text),
                    })
    return additions


def _run_revision_closure(
    prior_reviews: list[dict[str, Any]],
    author_responses: list[dict[str, Any]],
    v1_docs: list[dict[str, Any]],
    v2_docs: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], set[int]]:
    """Map prior comments -> author rebuttal -> v1/v2 diff verification."""
    comments: list[dict[str, Any]] = []
    for d in prior_reviews:
        comments.extend(_extract_numbered_items(d))

    responses: list[dict[str, Any]] = []
    for d in author_responses:
        responses.extend(_extract_numbered_items(d))

    additions: list[dict[str, Any]] = []
    added_v2_lines: set[int] = set()
    diff_available = bool(v1_docs and v2_docs)
    if diff_available:
        additions = _compute_diff_additions(v1_docs[0], v2_docs[0])
        added_v2_lines = {a["line"] for a in additions}

    v2_full_lower = "\n".join(d["text"] for d in v2_docs).lower()
    v1_full_lower = "\n".join(d["text"] for d in v1_docs).lower()

    claimed_fixed_re = re.compile(
        r"\b(?:we\s+(?:have\s+)?(?:added|revised|updated|corrected|included|performed|conducted|clarified|reanalyzed|re-?analyzed|replaced)|"
        r"now\s+(?:reported|included|shown|clarified|corrected)|has\s+been\s+(?:added|revised|updated|corrected|addressed)|"
        r"as\s+requested,\s+we)\b|"
        r"已(?:补充|修改|更新|更正|加入|重新分析|替换|完善|回应并在正文)",
        re.I,
    )
    declined_re = re.compile(
        r"\b(?:beyond\s+the\s+scope|outside\s+the\s+scope|not\s+feasible|cannot\s+be\s+performed|"
        r"we\s+respectfully\s+(?:disagree|decline)|left\s+for\s+future\s+work)\b|"
        r"超出(?:本研究)?范围|当前不可行|难以开展|留待未来研究",
        re.I,
    )

    mappings: list[dict[str, Any]] = []
    rev_findings: list[dict[str, Any]] = []

    for idx, comm in enumerate(comments, 1):
        # Match response by item_id first, then keyword overlap
        matched_resp = next((r for r in responses if r["item_id"].lower() == comm["item_id"].lower()), None)
        if matched_resp is None and responses:
            scored = sorted(
                ((len(comm["keywords"] & r["keywords"]), r) for r in responses),
                key=lambda x: x[0],
                reverse=True,
            )
            if scored and scored[0][0] >= 2:
                matched_resp = scored[0][1]

        resp_text = matched_resp["text"] if matched_resp else ""
        author_claimed_fix = bool(claimed_fixed_re.search(resp_text))
        author_declined = bool(declined_re.search(resp_text))

        # Combine comment + response keywords for diff verification
        query_kws = comm["keywords"] | (matched_resp["keywords"] if matched_resp else set())
        # Focus on newly mentioned concrete terms from comment (and any numbers/terms promised in response)
        comment_kws = comm["keywords"]
        resp_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?", resp_text))

        best_diff_hits: list[dict[str, Any]] = []
        if diff_available:
            for add in additions:
                overlap = comment_kws & add["keywords"]
                resp_overlap = (matched_resp["keywords"] & add["keywords"]) if matched_resp else set()
                num_overlap = resp_numbers & set(re.findall(r"\b\d+(?:\.\d+)?%?", add["text"]))
                score = len(overlap) * 2 + len(resp_overlap) + len(num_overlap) * 2
                if score >= 3 or (len(overlap) >= 2):
                    best_diff_hits.append({
                        "file": add["file"],
                        "section": add["section"],
                        "line": add["line"],
                        "score": score,
                        "matched_terms": sorted(overlap | resp_overlap)[:8],
                        "snippet": add["text"][:180],
                    })
            best_diff_hits.sort(key=lambda x: x["score"], reverse=True)

        # Determine closure state
        if diff_available:
            if best_diff_hits:
                top_score = best_diff_hits[0]["score"]
                if top_score >= 4 or len(best_diff_hits) >= 2:
                    closure_state = "closed"
                else:
                    closure_state = "partially_closed"
            elif author_declined and not author_claimed_fix:
                closure_state = "declined_with_rationale"
            elif author_claimed_fix:
                closure_state = "unclosed_claimed_fixed"
            else:
                # Check if terms were already absent from v1 and also absent from v2
                closure_state = "unclosed_unaddressed"
        else:
            # No v1/v2 pair available; verify against v2 text honestly
            hits_in_v2 = [k for k in comment_kws if k in v2_full_lower]
            if author_declined and not author_claimed_fix:
                closure_state = "declined_with_rationale"
            elif len(hits_in_v2) >= max(2, int(len(comment_kws) * 0.4)):
                closure_state = "partially_closed"
            elif author_claimed_fix:
                closure_state = "unclosed_claimed_fixed"
            else:
                closure_state = "unclosed_unaddressed"

        mapping = {
            "comment_id": comm["item_id"],
            "comment_location": {"file": comm["file"], "line": comm["line"]},
            "comment_excerpt": comm["text"][:200],
            "rebuttal_location": (
                {"file": matched_resp["file"], "line": matched_resp["line"]} if matched_resp else None
            ),
            "rebuttal_excerpt": resp_text[:200] if resp_text else "[MISSING: no matched author response]",
            "author_claimed_fixed": author_claimed_fix,
            "diff_verified_edits": best_diff_hits[:3],
            "closure_status": closure_state,
        }
        mappings.append(mapping)

        if closure_state == "unclosed_claimed_fixed":
            rev_findings.append({
                "facet": "argument_chain",
                "subtype": "revision_unclosed_claimed_fixed",
                "finding_type": "factual_error",
                "severity": "major",
                "severity_rationale": "Author rebuttal claims a prior reviewer comment was fixed in the revised manuscript, but the v1->v2 diff shows no substantiating edit.",
                "location": {
                    "file": matched_resp["file"] if matched_resp else comm["file"],
                    "section": "Revision Closure",
                    "line_start": matched_resp["line"] if matched_resp else comm["line"],
                    "line_end": matched_resp["line"] if matched_resp else comm["line"],
                },
                "verbatim_quote": (resp_text or comm["text"])[:200],
                "evidence_basis": (
                    f"Prior comment {comm['item_id']} ({comm['file']}:L{comm['line']}) received a rebuttal claiming revision, "
                    f"yet diff across {len(additions)} added/modified lines in {v2_docs[0]['file'] if v2_docs else 'revised draft'} "
                    "yielded 0 substantiating edits."
                ),
                "actionable_revision": f"Either implement the edit promised for Comment {comm['item_id']} directly in the revised manuscript with exact line/section pointers, or correct the rebuttal letter to explain why no change was made.",
                "firewall_check": "PASS (revision closure accountability; ACL H1-H17 clear)",
                "status": "confirmed",
                "revision_scope_tag": "prior_comment_closure",
            })
        elif closure_state == "unclosed_unaddressed":
            rev_findings.append({
                "facet": "argument_chain",
                "subtype": "revision_unclosed_unaddressed",
                "finding_type": "insufficient_evidence",
                "severity": "major",
                "severity_rationale": "Prior reviewer comment was neither resolved in the v1->v2 diff nor answered with a rationale in the rebuttal.",
                "location": {
                    "file": comm["file"],
                    "section": "Prior Reviewer Comments",
                    "line_start": comm["line"],
                    "line_end": comm["line"],
                },
                "verbatim_quote": comm["text"][:200],
                "evidence_basis": f"Comment {comm['item_id']} lacks both a point-by-point rebuttal rationale and verifiable v1->v2 manuscript edits.",
                "actionable_revision": f"Provide an explicit point-by-point response to Comment {comm['item_id']} and commit the corresponding manuscript revision or documented scope justification.",
                "firewall_check": "PASS (prior reviewer comment unaddressed; ACL H1-H17 clear)",
                "status": "confirmed",
                "revision_scope_tag": "prior_comment_closure",
            })
        elif closure_state == "partially_closed":
            rev_findings.append({
                "facet": "argument_chain",
                "subtype": "revision_partially_closed",
                "finding_type": "insufficient_evidence",
                "severity": "minor",
                "severity_rationale": "Only partial textual alignment found between prior reviewer comment and v1->v2 edits; requires reviewer spot-check.",
                "location": {
                    "file": best_diff_hits[0]["file"] if best_diff_hits else comm["file"],
                    "section": best_diff_hits[0]["section"] if best_diff_hits else "Revision Closure",
                    "line_start": best_diff_hits[0]["line"] if best_diff_hits else comm["line"],
                    "line_end": best_diff_hits[0]["line"] if best_diff_hits else comm["line"],
                },
                "verbatim_quote": (best_diff_hits[0]["snippet"] if best_diff_hits else comm["text"])[:200],
                "evidence_basis": f"Comment {comm['item_id']} matched limited diff evidence; confirm full substance of the request was satisfied.",
                "actionable_revision": f"Verify whether all sub-parts of Comment {comm['item_id']} were resolved in the revised text.",
                "firewall_check": "PASS (partial revision verification cue)",
                "status": "needs_human_review",
                "revision_scope_tag": "prior_comment_closure",
            })

    summary = {
        "enabled": True,
        "diff_available": diff_available,
        "v1_file": v1_docs[0]["file"] if v1_docs else None,
        "v2_file": v2_docs[0]["file"] if v2_docs else None,
        "diff_added_lines_count": len(additions),
        "total_prior_comments": len(mappings),
        "closure_counts": {
            "closed": sum(1 for m in mappings if m["closure_status"] == "closed"),
            "partially_closed": sum(1 for m in mappings if m["closure_status"] == "partially_closed"),
            "unclosed_claimed_fixed": sum(1 for m in mappings if m["closure_status"] == "unclosed_claimed_fixed"),
            "unclosed_unaddressed": sum(1 for m in mappings if m["closure_status"] == "unclosed_unaddressed"),
            "declined_with_rationale": sum(1 for m in mappings if m["closure_status"] == "declined_with_rationale"),
        },
        "comment_mappings": mappings,
        "new_issues_isolated": [],  # Populated after main audit
        "replacement_reviewer_scope_note": (
            "Findings discovered outside prior reviewer comments are isolated in `new_issues_isolated` "
            "so editors and replacement reviewers can distinguish prior-comment accountability from late-round new issues."
        ),
    }
    return summary, rev_findings, added_v2_lines


def apply_acl_firewall(finding: dict[str, Any]) -> dict[str, Any]:
    """Ensure no candidate finding violates ACL H1-H17 improper critique rules."""
    blob = " ".join([
        str(finding.get("title", "")),
        str(finding.get("summary", "")),
        str(finding.get("message", "")),
        str(finding.get("critique", "")),
        str(finding.get("comment", "")),
        str(finding.get("severity_rationale", "")),
        str(finding.get("evidence_basis", "")),
        str(finding.get("actionable_revision", "")),
    ])
    for code, pat in ACL_FIREWALL_PATTERNS:
        if pat.search(blob):
            if finding.get("severity") not in (None, "suggestion"):
                finding.setdefault("original_severity", finding.get("severity"))
            finding["finding_type"] = "suggestion"
            finding["severity"] = "suggestion"
            finding["firewall_check"] = f"DOWNGRADED_BY_ACL_{code}"
            return finding
    if not finding.get("firewall_check"):
        finding["firewall_check"] = "PASS (ACL H1-H17 clear; five-gate admissible)"
    return finding


def audit_external_findings_file(findings_path: Path) -> tuple[dict[str, Any], dict[str, Any] | list[dict[str, Any]]]:
    """Executable ACL H1-H17 gate for auditing supplementary semantic findings produced by an Agent."""
    raw_obj = json.loads(findings_path.read_text(encoding="utf-8"))
    if isinstance(raw_obj, list):
        items = raw_obj
        is_list_root = True
    elif isinstance(raw_obj, dict) and isinstance(raw_obj.get("findings"), list):
        items = raw_obj["findings"]
        is_list_root = False
    else:
        raise ValueError(f"Expected JSON list of findings or object with 'findings' list in {findings_path}")

    audited: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []
    rules_triggered: set[str] = set()

    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        fcopy = dict(item)
        orig_sev = fcopy.get("severity")
        fcopy = apply_acl_firewall(fcopy)
        fw = str(fcopy.get("firewall_check", ""))
        if fw.startswith("DOWNGRADED_BY_ACL_"):
            rule = fw.replace("DOWNGRADED_BY_ACL_", "")
            rules_triggered.add(rule)
            downgraded.append({
                "finding_id": fcopy.get("finding_id", f"EXT-{idx:03d}"),
                "original_severity": orig_sev,
                "new_severity": fcopy.get("severity"),
                "rule_triggered": rule,
                "firewall_check": fw,
            })
        audited.append(fcopy)

    summary = {
        "mode": "acl_h1_h17_findings_gate",
        "source_file": str(findings_path),
        "total_findings": len(audited),
        "downgraded_count": len(downgraded),
        "rules_triggered": sorted(rules_triggered),
        "passed": len(downgraded) == 0,
        "downgraded_findings": downgraded,
    }
    if is_list_root:
        out_payload: dict[str, Any] | list[dict[str, Any]] = {
            **summary,
            "findings": audited,
        }
    else:
        updated_dict = dict(raw_obj)
        updated_dict["findings"] = audited
        updated_dict["acl_firewall_audit"] = summary
        if isinstance(updated_dict.get("severity_counts"), dict):
            updated_dict["severity_counts"] = {
                "critical": sum(1 for f in audited if f.get("severity") == "critical"),
                "major": sum(1 for f in audited if f.get("severity") == "major"),
                "minor": sum(1 for f in audited if f.get("severity") == "minor"),
                "suggestion": sum(1 for f in audited if f.get("severity") == "suggestion"),
            }
        out_payload = updated_dict
    return summary, out_payload


def audit_arguments(target: Path) -> dict[str, Any]:
    docs, gaps, _images = io_core.load_documents(target)
    if not docs:
        return {
            "passed": False,
            "result_status": "partial_with_gaps",
            "audit_status": "failed",
            "status": "failed",
            "verdict": "incomplete_inputs",
            "documents_scanned": [],
            "sentence_identity_breakdown": {"author_claim": 0, "cited_prior_view": 0, "explicit_control_or_hypothesis": 0},
            "argument_graph": [],
            "claim_inventory": [],
            "findings_count": 0,
            "findings": [],
            "gaps": gaps or [{"code": "MANUSCRIPT_NOT_FOUND", "severity": "error", "source": str(target), "message": "No readable manuscript found."}],
            "error": "No readable manuscript documents found.",
        }

    doc_roles = _classify_doc_roles(docs)
    revision_mode = bool(doc_roles["prior_review"] and (doc_roles["author_response"] or (doc_roles["manuscript_v1"] and doc_roles["manuscript_v2"])))

    findings: list[dict[str, Any]] = []
    argument_graph: list[dict[str, Any]] = []
    identity_counts = {"author_claim": 0, "cited_prior_view": 0, "explicit_control_or_hypothesis": 0}
    suppressed_examples: list[dict[str, Any]] = []
    revision_closure: dict[str, Any] = {"enabled": False}
    added_v2_lines: set[int] = set()

    if revision_mode:
        revision_closure, rev_findings, added_v2_lines = _run_revision_closure(
            doc_roles["prior_review"],
            doc_roles["author_response"],
            doc_roles["manuscript_v1"],
            doc_roles["manuscript_v2"] or doc_roles["manuscript"],
        )
        findings.extend(rev_findings)

    # Audit active manuscripts (v2 in revision mode, else all manuscript files)
    active_manuscripts = doc_roles["manuscript"]
    if not active_manuscripts:
        active_manuscripts = [d for d in docs if d not in doc_roles["prior_review"] and d not in doc_roles["author_response"]]

    manuscript_findings: list[dict[str, Any]] = []
    claim_seq = 0

    for doc in active_manuscripts:
        secs = io_core.split_sections(doc)
        by_role: dict[str, list[dict[str, Any]]] = {}
        for s in secs:
            by_role.setdefault(s["role"], []).append(s)

        # Determine study design strictly from non-cited, non-limitation author sentences in Methods + Abstract
        # (prevents Introduction/Discussion mentions of prior or future RCTs from breaking observational detection).
        design_sents: list[str] = []
        for rname in ("methods", "abstract"):
            for s in by_role.get(rname, []):
                for sent in io_core.split_sentences(s):
                    ident, _ = classify_sentence_identity(sent["text"], s["role"])
                    if ident == "author_claim":
                        design_sents.append(sent["text"])
        design_blob = "\n".join(design_sents) if design_sents else doc["text"]

        has_obs = bool(OBSERVATIONAL_DESIGN_RE.search(design_blob))
        interv_sents = [st for st in design_sents if not FUTURE_RCT_RE.search(st)]
        interv_blob = "\n".join(interv_sents) if interv_sents else ("" if design_sents else doc["text"])
        has_interv = bool(INTERVENTIONAL_DESIGN_RE.search(interv_blob))
        is_observational_only = has_obs and not has_interv

        # Check constrained experimental scale (cell/animal/single-center without multicenter clinical trial)
        scale_anchor: dict[str, Any] | None = None
        has_multicenter = bool(MULTICENTER_CLINICAL_RE.search(design_blob))
        for rname in ("methods", "abstract", "results", "results_discussion"):
            if scale_anchor:
                break
            for s in by_role.get(rname, []):
                for sent in io_core.split_sentences(s):
                    ident, _ = classify_sentence_identity(sent["text"], s["role"])
                    if ident != "cited_prior_view" and CONSTRAINED_SCALE_RE.search(sent["text"]):
                        scale_anchor = {
                            "file": doc["file"],
                            "section": s["title"],
                            "line": sent["line"],
                            "match": CONSTRAINED_SCALE_RE.search(sent["text"]).group(0),  # type: ignore[union-attr]
                            "excerpt": sent["text"][:140],
                        }
                        break

        # --- Pass 1: Sentence identity classification + Argument Graph + B1 (Causal Overreach) + B4 (Scale Over-Extrapolation) ---
        for sec in secs:
            if sec["role"] in ("references", "declarations"):
                continue
            for sent in io_core.split_sentences(sec):
                st = sent["text"]
                ident, ident_reason = classify_sentence_identity(st, sec["role"])
                identity_counts[ident] += 1

                if ident != "author_claim":
                    if CAUSAL_STRONG_RE.search(st) or OVER_EXTRAPOLATION_RE.search(st):
                        if len(suppressed_examples) < 15:
                            suppressed_examples.append({
                                "file": doc["file"],
                                "section": sec["title"],
                                "line": sent["line"],
                                "identity": ident,
                                "reason": ident_reason,
                                "excerpt": st[:160],
                            })
                    continue

                is_strong_causal = bool(CAUSAL_STRONG_RE.search(st))
                is_hedged = bool(HEDGED_MODALITY_RE.search(st)) and not is_strong_causal
                is_over_extrap = bool(OVER_EXTRAPOLATION_RE.search(st))
                is_promise = sec["role"] == "introduction" and bool(INTRO_PROMISE_RE.search(st))
                ev_matches = [m.group(0) for m in EVIDENCE_ANCHOR_RE.finditer(st)]
                has_cite = bool(CITATION_RE.search(st))

                # Build structured Claim -> Evidence -> Warrant -> Boundary node for substantive claims
                if (
                    is_strong_causal
                    or is_over_extrap
                    or is_promise
                    or (sec["role"] in ("abstract", "results", "results_discussion", "conclusion") and (ev_matches or _detect_direction(st)))
                ):
                    claim_seq += 1
                    warrant_risk = "none_detected"
                    if is_strong_causal and is_observational_only:
                        warrant_risk = "observational_to_causal_leap"
                    elif is_over_extrap and scale_anchor and not has_multicenter:
                        warrant_risk = "scale_or_sample_extrapolation"

                    ev_source = (
                        "internal_statistical_or_figure" if ev_matches
                        else ("cited_literature" if has_cite else "unanchored_in_sentence")
                    )
                    boundary = (
                        scale_anchor["match"] if scale_anchor
                        else ("observational_design" if is_observational_only else ("hedged" if is_hedged else "unspecified"))
                    )
                    if len(argument_graph) < 60:
                        argument_graph.append({
                            "claim_id": f"CLM-{claim_seq:03d}",
                            "file": doc["file"],
                            "section": sec["title"],
                            "section_role": sec["role"],
                            "line": sent["line"],
                            "page": _page_of(doc, sent["line"]),
                            "identity": ident,
                            "claim_strength": (
                                "causal" if is_strong_causal
                                else ("universal_extrapolation" if is_over_extrap
                                      else ("objective_promise" if is_promise
                                            else ("hedged_associative" if is_hedged else "empirical_assertion")))
                            ),
                            "claim_text": st[:200],
                            "evidence_anchor": ev_matches[:6] if ev_matches else ["unanchored_in_sentence"],
                            "evidence_source_type": ev_source,
                            "warrant_risk": warrant_risk,
                            "boundary_condition": boundary,
                        })

                # B1: Observational causal overreach
                if is_strong_causal and is_observational_only:
                    ls = sent.get("line_start", sent["line"])
                    le = sent.get("line_end", ls)
                    manuscript_findings.append({
                        "facet": "argument_chain",
                        "subtype": "causal_overreach",
                        "finding_type": "insufficient_evidence",
                        "severity": "major",
                        "severity_rationale": "Author claim uses strong causal language despite an observational/correlational study design without causal identification.",
                        "location": {
                            "file": doc["file"],
                            "section": sec["title"],
                            "page": _page_of(doc, ls),
                            "line_start": ls,
                            "line_end": le,
                        },
                        "verbatim_quote": st[:200],
                        "evidence_basis": "Study design declares observational/correlational indicators with no interventional/RCT/causal-identification method; sentence is classified as an unhedged author_claim.",
                        "actionable_revision": "Downgrade causal phrasing ('proves'/'causes'/'直接导致'/'必然导致') to associative wording ('is associated with'/'correlates with'/'提示存在关联') or add an explicit causal identification analysis and its assumptions.",
                        "firewall_check": "PASS (author_claim verified; cited views and controls/hypotheses excluded; ACL H1-H17 clear)",
                        "status": "confirmed",
                    })

                # B4: Scale / sample over-extrapolation
                if (
                    is_over_extrap
                    and scale_anchor
                    and not has_multicenter
                    and sec["role"] in ("abstract", "discussion", "conclusion", "results_discussion")
                ):
                    ls = sent.get("line_start", sent["line"])
                    le = sent.get("line_end", ls)
                    manuscript_findings.append({
                        "facet": "argument_chain",
                        "subtype": "scale_over_extrapolation",
                        "finding_type": "insufficient_evidence",
                        "severity": "major",
                        "severity_rationale": "Conclusion extrapolates preclinical, animal, cell-line, or single-center results directly into universal clinical efficacy.",
                        "location": {
                            "file": doc["file"],
                            "section": sec["title"],
                            "page": _page_of(doc, ls),
                            "line_start": ls,
                            "line_end": le,
                        },
                        "verbatim_quote": st[:200],
                        "evidence_basis": (
                            f"Study scope is constrained to '{scale_anchor['match']}' "
                            f"({scale_anchor['file']} §{scale_anchor['section']} L{scale_anchor['line']}) with no multicenter clinical trial evidence."
                        ),
                        "actionable_revision": (
                            f"Restrict the claim to the tested '{scale_anchor['match']}' setting and state explicitly that "
                            "clinical efficacy and generalizability require prospective human/multicenter validation."
                        ),
                        "firewall_check": "PASS (claim exceeds experimental scale boundary; ACL H1-H17 clear)",
                        "status": "confirmed",
                    })

        # --- B2: Introduction promises vs Methods/Results delivery fracture ---
        # Lexical term mismatch is emitted as a minor | needs_human_review cue so legitimate synonym paraphrasing
        # (e.g., "biomarker stratification/recurrence" vs "sPD-L1/relapse-free survival") is never misclassified as MAJOR | confirmed.
        intro_secs = by_role.get("introduction", [])
        delivery_secs = by_role.get("methods", []) + by_role.get("results", []) + by_role.get("results_discussion", [])
        delivery_text = "\n".join(s["text"] for s in delivery_secs).lower()
        if intro_secs and delivery_text:
            for sec in intro_secs:
                for sent in io_core.split_sentences(sec):
                    ident, _ = classify_sentence_identity(sent["text"], sec["role"])
                    if ident != "author_claim" or not INTRO_PROMISE_RE.search(sent["text"]):
                        continue
                    ls = sent.get("line_start", sent["line"])
                    le = sent.get("line_end", ls)
                    for clause in _split_objectives(sent["text"]):
                        kws = _extract_keywords(clause)
                        if len(kws) < 3:
                            continue
                        hits = [k for k in kws if k in delivery_text]
                        missing = sorted(kws - set(hits))
                        if len(hits) / len(kws) < 0.30:
                            manuscript_findings.append({
                                "facet": "argument_chain",
                                "subtype": "intro_delivery_fracture",
                                "finding_type": "insufficient_evidence",
                                "severity": "minor",
                                "severity_rationale": "An Introduction objective has low literal term overlap in Methods/Results; verify manually whether delivered under synonymous terminology or genuinely omitted.",
                                "location": {
                                    "file": doc["file"],
                                    "section": sec["title"],
                                    "page": _page_of(doc, ls),
                                    "line_start": ls,
                                    "line_end": le,
                                },
                                "verbatim_quote": clause[:200],
                                "evidence_basis": f"Only {len(hits)}/{len(kws)} literal objective terms appear in Methods+Results (missing: {missing[:6]}); check for valid domain synonyms/abbreviations before escalating.",
                                "actionable_revision": (
                                    f"Confirm whether Methods/Results addresses {', '.join(missing[:4])} under synonymous terms; "
                                    "if genuinely omitted, add the analysis or narrow the Introduction objective."
                                ),
                                "firewall_check": "PASS (lexical review cue downgraded to minor/needs_human_review to prevent synonym false positives; ACL H1-H17 clear)",
                                "status": "needs_human_review",
                            })

        # --- B3: Cross-section numeric & effect-direction/significance reversal conflicts (Abstract/Conclusion vs Results) ---
        res_secs = by_role.get("results", []) + by_role.get("results_discussion", [])
        abs_secs = by_role.get("abstract", [])
        conc_secs = by_role.get("conclusion", [])

        if res_secs and (abs_secs or conc_secs):
            res_q = _extract_quant_records(res_secs, doc)
            all_res_pcts = [val for r in res_q for _, val in r["percents"]]
            summary_q = _extract_quant_records(abs_secs + conc_secs, doc)

            for sq in summary_q:
                # Find best-matching Results sentence by shared domain keywords
                best_match: dict[str, Any] | None = None
                best_overlap: set[str] = set()
                for rq in res_q:
                    ov = sq["keywords"] & rq["keywords"]
                    if len(ov) > len(best_overlap):
                        best_overlap = ov
                        best_match = rq

                direction_conflict = False
                if best_match and len(best_overlap) >= 2:
                    # 1. Check statistical significance reversal first (including numeric p > 0.05 such as r=0.12, p=0.41)
                    if (
                        sq["significance"]
                        and best_match["significance"]
                        and sq["significance"] != best_match["significance"]
                    ):
                        direction_conflict = True
                        manuscript_findings.append({
                            "facet": "fact_verification",
                            "subtype": "cross_section_significance_reversal",
                            "finding_type": "factual_error",
                            "severity": "critical",
                            "severity_rationale": f"{sq['section']} contradicts Results on statistical significance for {sorted(best_overlap)[:4]}.",
                            "location": {
                                "file": doc["file"],
                                "section": sq["section"],
                                "page": sq["page"],
                                "line_start": sq["line_start"],
                                "line_end": sq["line_end"],
                            },
                            "verbatim_quote": sq["text"][:200],
                            "evidence_basis": (
                                f"{sq['section']} L{sq['line_start']} claims '{sq['significance']}' whereas "
                                f"Results (§{best_match['section']} L{best_match['line_start']}) reports '{best_match['significance']}': "
                                f"\"{best_match['text'][:140]}\"."
                            ),
                            "actionable_revision": (
                                f"Align significance claims in {sq['section']} L{sq['line_start']} with exact statistical results at L{best_match['line_start']}."
                            ),
                            "firewall_check": "PASS (verifiable internal significance contradiction; ACL H1-H17 clear)",
                            "status": "confirmed",
                        })
                    # 2. Check polarity-aware effect-direction reversal
                    elif (
                        sq["direction"]
                        and best_match["direction"]
                    ):
                        sq_sign = 1 if sq["direction"] == "increase" else -1
                        rq_sign = 1 if best_match["direction"] == "increase" else -1
                        sq_fam, sq_pol, sq_mterms = sq["metric_family"], sq["metric_polarity"], sq["metric_terms"]
                        rq_fam, rq_pol, rq_mterms = best_match["metric_family"], best_match["metric_polarity"], best_match["metric_terms"]

                        equivalent_inverse = False
                        confirmed_same_polarity_reversal = False
                        if sq_fam and rq_fam and sq_fam == rq_fam and sq_pol is not None and rq_pol is not None:
                            sq_eff = sq_sign * sq_pol
                            rq_eff = rq_sign * rq_pol
                            if sq_eff == rq_eff:
                                # e.g., "improved survival" (+1 * +1 = +1) vs "reduced mortality" (-1 * -1 = +1)
                                equivalent_inverse = True
                            else:
                                confirmed_same_polarity_reversal = True
                        elif sq["direction"] != best_match["direction"]:
                            # Unknown polarity family: require shared concrete outcome words and no metric mismatch
                            if (sq_mterms and rq_mterms and bool(sq_mterms & rq_mterms)) or (not sq_mterms and not rq_mterms):
                                confirmed_same_polarity_reversal = True

                        if confirmed_same_polarity_reversal and not equivalent_inverse:
                            direction_conflict = True
                            manuscript_findings.append({
                                "facet": "fact_verification",
                                "subtype": "cross_section_direction_reversal",
                                "finding_type": "factual_error",
                                "severity": "critical",
                                "severity_rationale": f"{sq['section']} reverses the effect direction reported in Results for the same outcome ({sorted(best_overlap)[:4]}).",
                                "location": {
                                    "file": doc["file"],
                                    "section": sq["section"],
                                    "page": sq["page"],
                                    "line_start": sq["line_start"],
                                    "line_end": sq["line_end"],
                                },
                                "verbatim_quote": sq["text"][:200],
                                "evidence_basis": (
                                    f"{sq['section']} L{sq['line_start']} reports '{sq['direction']}' while "
                                    f"Results (§{best_match['section']} L{best_match['line_start']}) reports '{best_match['direction']}' "
                                    f"on shared target outcome {sorted(best_overlap)[:5]}: \"{best_match['text'][:120]}\"."
                                ),
                                "actionable_revision": (
                                    f"Reconcile the effect direction between {sq['section']} L{sq['line_start']} and "
                                    f"Results L{best_match['line_start']} against the underlying table/figure so both report the true direction."
                                ),
                                "firewall_check": "PASS (polarity-verified internal contradiction; ACL H1-H17 clear)",
                                "status": "confirmed",
                            })

                # Check percentage discrepancy with rounding tolerance (0.5 percentage points)
                if sq["percents"] and not direction_conflict:
                    unmatched_pcts: list[tuple[str, Decimal]] = []
                    for raw_tok, val in sq["percents"]:
                        if not any(abs(val - rv) <= Decimal("0.5") for rv in all_res_pcts):
                            unmatched_pcts.append((raw_tok, val))

                    if unmatched_pcts:
                        if best_match and len(best_overlap) >= 2 and best_match["percents"]:
                            res_toks = [t for t, _ in best_match["percents"]]
                            abs_toks = [t for t, _ in unmatched_pcts]
                            manuscript_findings.append({
                                "facet": "fact_verification",
                                "subtype": "cross_section_numeric_conflict",
                                "finding_type": "factual_error",
                                "severity": "major",
                                "severity_rationale": f"{sq['section']} reports a different percentage ({abs_toks}) than Results ({res_toks}) for the same metric.",
                                "location": {
                                    "file": doc["file"],
                                    "section": sq["section"],
                                    "page": sq["page"],
                                    "line_start": sq["line"],
                                    "line_end": sq["line"],
                                },
                                "verbatim_quote": sq["text"][:200],
                                "evidence_basis": (
                                    f"{sq['section']} L{sq['line']} gives {abs_toks} while Results L{best_match['line']} "
                                    f"(shared terms {sorted(best_overlap)[:5]}) gives {res_toks}: \"{best_match['text'][:120]}\"."
                                ),
                                "actionable_revision": (
                                    f"Verify the source table/computation and harmonize {abs_toks} in {sq['section']} L{sq['line']} "
                                    f"with {res_toks} in Results L{best_match['line']}."
                                ),
                                "firewall_check": "PASS (numeric cross-section discrepancy beyond rounding tolerance; ACL H1-H17 clear)",
                                "status": "confirmed",
                            })
                        else:
                            abs_toks = [t for t, _ in unmatched_pcts]
                            manuscript_findings.append({
                                "facet": "fact_verification",
                                "subtype": "unmatched_summary_percentage",
                                "finding_type": "insufficient_evidence",
                                "severity": "minor",
                                "severity_rationale": f"{sq['section']} percentage {abs_toks} is not matched in readable Results text (review cue).",
                                "location": {
                                    "file": doc["file"],
                                    "section": sq["section"],
                                    "page": sq["page"],
                                    "line_start": sq["line"],
                                    "line_end": sq["line"],
                                },
                                "verbatim_quote": sq["text"][:200],
                                "evidence_basis": f"Percentage(s) {abs_toks} not found within +/-0.5pp in extracted Results text; may reside in a figure/table or reflect a mismatch.",
                                "actionable_revision": "Cross-check denominator, subgroup, table, and rounding before modifying either figure.",
                                "firewall_check": "PASS (marked needs_human_review; never escalated without matched partner)",
                                "status": "needs_human_review",
                            })

        # --- B5: Missing scope boundaries / limitations (suggestion ONLY, never blocks passed) ---
        disc_secs = by_role.get("discussion", []) + by_role.get("conclusion", []) + by_role.get("results_discussion", [])
        disc_blob = "\n".join(s["text"] for s in disc_secs)
        if disc_secs and not LIMITATION_RE.search(disc_blob):
            last_sec = disc_secs[-1]
            manuscript_findings.append({
                "facet": "argument_chain",
                "subtype": "missing_limitations",
                "finding_type": "suggestion",
                "severity": "suggestion",
                "severity_rationale": "Discussion/Conclusion omits explicit study limitations or applicability boundaries (constructive suggestion, not a fatal flaw).",
                "location": {
                    "file": doc["file"],
                    "section": last_sec["title"],
                    "page": _page_of(doc, last_sec["start_line"]),
                    "line_start": last_sec["start_line"],
                    "line_end": last_sec["end_line"],
                },
                "verbatim_quote": "[MISSING: explicit limitation or scope-boundary paragraph]",
                "evidence_basis": "No limitation, caveat, generalizability, or boundary-condition statements detected in Discussion/Conclusion.",
                "actionable_revision": "Add a brief Limitations paragraph specifying sample/model boundaries and conditions under which the conclusion may not generalize.",
                "firewall_check": "PASS (strictly suggestion P3; compliant with ACL H16)",
                "status": "confirmed",
            })

    # --- Scope isolation in Revision Closure Mode ---
    if revision_mode:
        isolated_new: list[dict[str, Any]] = []
        for mf in manuscript_findings:
            ln = mf["location"].get("line_start", 0)
            if ln in added_v2_lines:
                mf["revision_scope_tag"] = "introduced_in_revision_diff"
            else:
                mf["revision_scope_tag"] = "new_issue_isolated_outside_prior_comments"
            isolated_new.append({
                "subtype": mf["subtype"],
                "severity": mf["severity"],
                "location": mf["location"],
                "revision_scope_tag": mf["revision_scope_tag"],
                "verbatim_quote": mf["verbatim_quote"],
            })
        revision_closure["new_issues_isolated"] = isolated_new
        findings.extend(manuscript_findings)
    else:
        findings.extend(manuscript_findings)

    # Apply ACL H1-H17 firewall and assign sequential finding IDs
    final_findings: list[dict[str, Any]] = []
    for idx, f in enumerate(findings, 1):
        f_checked = apply_acl_firewall(f)
        f_checked["finding_id"] = f"LOG-{idx:03d}"
        final_findings.append(f_checked)

    crit_major = [f for f in final_findings if f["severity"] in ("critical", "major")]
    has_gaps = bool(gaps)
    result_status = "partial_with_gaps" if has_gaps else "ok"
    audit_status = "partial" if has_gaps else "completed"
    verdict = "no_major_defects_detected" if not crit_major else "defects_detected"

    return {
        "schema_version": "3.0",
        "passed": len(crit_major) == 0 and not has_gaps,
        "result_status": result_status,
        "audit_status": audit_status,
        "status": audit_status,
        "verdict": verdict,
        "documents_scanned": [
            {
                "file": d["file"],
                "sha256": d["sha256"],
                "extractor": d["extractor"],
                "lines": d["line_count"],
                "pages": len(d["pages"]),
            }
            for d in docs
        ],
        "sentence_identity_breakdown": identity_counts,
        "suppressed_non_author_candidates": suppressed_examples,
        "argument_graph_count": len(argument_graph),
        "argument_graph": argument_graph,
        "claim_inventory": argument_graph[:40],  # Backward-compatible alias
        "revision_closure": revision_closure,
        "findings_count": len(final_findings),
        "severity_counts": {
            "critical": sum(1 for f in final_findings if f["severity"] == "critical"),
            "major": sum(1 for f in final_findings if f["severity"] == "major"),
            "minor": sum(1 for f in final_findings if f["severity"] == "minor"),
            "suggestion": sum(1 for f in final_findings if f["severity"] == "suggestion"),
        },
        "findings": final_findings,
        "gaps": gaps,
        "uncertainty": [
            "Automated lexical/structural audit verifies explicit causal phrasing, numeric/direction contradictions, scale bounds, and v1->v2 diff alignment; implicit domain-specific confounders require human expert review."
        ] + ([f"{len(gaps)} input parsing gap(s) logged in `gaps`."] if gaps else []),
    }


def render_markdown_report(rep: dict[str, Any]) -> str:
    lines = [
        f"# Argument Chain & Logic Audit Report (`{rep.get('verdict')}`)",
        "",
        f"- **Result Status**: `{rep.get('result_status')}` | **Audit Status**: `{rep.get('audit_status')}` | **Passed**: `{rep.get('passed')}`",
        f"- **Documents Scanned**: `{len(rep.get('documents_scanned', []))}` | **Argument Graph Nodes**: `{rep.get('argument_graph_count', 0)}`",
        f"- **Sentence Identity Separation**: `{json.dumps(rep.get('sentence_identity_breakdown', {}), ensure_ascii=False)}`",
        f"- **Findings**: `{rep.get('findings_count', 0)}` (`{json.dumps(rep.get('severity_counts', {}), ensure_ascii=False)}`)",
        "",
    ]

    rc = rep.get("revision_closure", {})
    if rc.get("enabled"):
        lines.extend([
            "## Revision Closure Summary",
            f"- **Diff Pair**: `{rc.get('v1_file')}` -> `{rc.get('v2_file')}` (`{rc.get('diff_added_lines_count', 0)}` added/modified lines)",
            f"- **Closure Counts**: `{json.dumps(rc.get('closure_counts', {}), ensure_ascii=False)}`",
            f"- **Isolated New Issues**: `{len(rc.get('new_issues_isolated', []))}` ({rc.get('replacement_reviewer_scope_note')})",
            "",
        ])
        for m in rc.get("comment_mappings", []):
            lines.append(
                f"- **Comment {m['comment_id']}** -> `{m['closure_status']}` "
                f"(claimed_fixed=`{m['author_claimed_fixed']}`, diff_hits=`{len(m['diff_verified_edits'])}`)"
            )
        lines.append("")

    if not rep.get("findings"):
        lines.extend([
            "## Findings",
            "No qualifying logical or argument-chain defects detected across inspected sections.",
            "",
        ])
    else:
        lines.append("## Findings")
        lines.append("")
        for f in rep["findings"]:
            loc = f["location"]
            pg = f" p.{loc['page']}" if loc.get("page") else ""
            scope = f" [{f['revision_scope_tag']}]" if f.get("revision_scope_tag") else ""
            lines.append(
                f"### {f['finding_id']} [{f['severity'].upper()} | {f['finding_type']} | {f.get('subtype', '')}]{scope} "
                f"`{loc['file']}` §{loc.get('section', '')}{pg} L{loc.get('line_start')}"
            )
            lines.append(f"- **Quote**: \"{f['verbatim_quote']}\"")
            lines.append(f"- **Evidence Basis**: {f['evidence_basis']}")
            lines.append(f"- **Actionable Revision**: {f['actionable_revision']}")
            lines.append(f"- **Firewall & Status**: `{f.get('firewall_check')}` | `{f.get('status')}`")
            lines.append("")

    if rep.get("gaps"):
        lines.append("## Input & Coverage Gaps")
        for g in rep["gaps"]:
            lines.append(f"- `{g.get('code')}` ({g.get('source', '')}): {g.get('message')}")
        lines.append("")

    return "\n".join(lines)


def _load_case_id(target: Path, explicit: str) -> str:
    if explicit:
        return explicit
    cj = target / "case.json" if target.is_dir() else target.parent / "case.json"
    if cj.is_file():
        try:
            obj = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict) and obj.get("case_id"):
                return str(obj["case_id"])
        except Exception:
            pass
    return "case-01"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Source-bound logical, argument-chain, revision-closure, and ACL H1-H17 firewall auditor.")
    parser.add_argument("--input", default="", help="Input directory or manuscript file (.md/.tex/.pdf/.docx/.txt).")
    parser.add_argument("--out-json", default="", help="Output JSON audit report path.")
    parser.add_argument("--out-md", default="", help="Optional Markdown summary report path.")
    parser.add_argument("--out-result-json", default="", help="Optional dual-compatible /app/output/result.json path.")
    parser.add_argument("--case-id", default="", help="Optional explicit case_id override for result.json.")
    parser.add_argument("--audit-findings", default="", help="Executable ACL H1-H17 gate: audit an external findings JSON or result.json file and downgrade improper critiques.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if critical/major findings, input gaps, or ACL downgrades exist.")
    args = parser.parse_args(argv)

    # Mode A: Executable ACL H1-H17 findings firewall gate
    if args.audit_findings:
        src_p = Path(args.audit_findings)
        summary, out_payload = audit_external_findings_file(src_p)
        if args.out_json:
            io_core.write_json_atomic(Path(args.out_json), out_payload)  # type: ignore[arg-type]
        print(json.dumps(summary, ensure_ascii=False))
        if args.strict and summary["downgraded_count"] > 0:
            return 1
        return 0

    if not args.input or not args.out_json:
        parser.error("--input and --out-json are required unless --audit-findings is provided.")

    target = Path(args.input)
    rep = audit_arguments(target)
    io_core.write_json_atomic(Path(args.out_json), rep)

    artifacts = [Path(args.out_json).name]
    if args.out_md:
        out_m = Path(args.out_md)
        out_m.parent.mkdir(parents=True, exist_ok=True)
        out_m.write_text(render_markdown_report(rep), encoding="utf-8")
        artifacts.append(out_m.name)

    if args.out_result_json:
        cid = _load_case_id(target, args.case_id)
        out_name = Path(args.out_json).name
        sev_counts = rep.get("severity_counts", {})
        ident_counts = rep.get("sentence_identity_breakdown", {})
        metrics = {
            "documents_scanned_count": len(rep.get("documents_scanned", [])),
            "argument_graph_count": rep.get("argument_graph_count", 0),
            "findings_count": rep.get("findings_count", 0),
            "critical_count": sev_counts.get("critical", 0),
            "major_count": sev_counts.get("major", 0),
            "minor_count": sev_counts.get("minor", 0),
            "suggestion_count": sev_counts.get("suggestion", 0),
            "author_claim_count": ident_counts.get("author_claim", 0),
            "cited_prior_view_count": ident_counts.get("cited_prior_view", 0),
            "explicit_control_or_hypothesis_count": ident_counts.get("explicit_control_or_hypothesis", 0),
            "gaps_count": len(rep.get("gaps", [])),
        }
        metric_provenance = {
            k: f"{out_name}::argument_chain_auditor.py" for k in metrics
        }
        res_payload = {
            "schema_version": "3.0",
            "case_id": cid,
            "task_id": "challenge-16d4414d",
            "status": rep.get("result_status", "partial_with_gaps" if rep.get("gaps") else "ok"),
            "audit_status": rep.get("audit_status"),
            "passed": rep.get("passed"),
            "verdict": rep.get("verdict"),
            "sources": rep.get("documents_scanned", []),
            "artifacts": ["result.json"] + artifacts,
            "metrics": metrics,
            "metric_provenance": metric_provenance,
            "sentence_identity_breakdown": ident_counts,
            "suppressed_non_author_candidates": rep.get("suppressed_non_author_candidates", []),
            "argument_graph": rep.get("argument_graph", []),
            "revision_closure": rep.get("revision_closure", {"enabled": False}),
            "severity_counts": sev_counts,
            "findings": rep.get("findings", []),
            "uncertainty": rep.get("uncertainty", []),
            "gaps": rep.get("gaps", []),
        }
        io_core.write_json_atomic(Path(args.out_result_json), res_payload)

    print(
        json.dumps(
            {
                "result_status": rep.get("result_status"),
                "audit_status": rep.get("audit_status"),
                "verdict": rep.get("verdict"),
                "passed": rep.get("passed"),
                "findings_count": rep.get("findings_count", 0),
                "revision_closure_enabled": rep.get("revision_closure", {}).get("enabled", False),
            },
            ensure_ascii=False,
        )
    )
    if not rep.get("documents_scanned"):
        return 1
    if args.strict and not rep.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
