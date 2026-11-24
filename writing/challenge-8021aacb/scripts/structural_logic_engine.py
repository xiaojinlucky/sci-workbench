#!/usr/bin/env python3
"""Six-dimension source-grounded structural logic diagnosis and edit-action-card engine.

Target task: paper-structure-optimization (Manuscript Structural and Logical Optimization).
Zero third-party dependencies (Python 3.12+ stdlib + sibling writing_io_and_invariants.py).
Produces:
  - structural_diagnosis.json (full six-dimension audit + edit action cards)
  - editing_action_plan.md    (executable paragraph/sentence-anchored edit plan)
  - result.json               (Harbor/structured output contract with preserved_facts,
                               evidence_map, change_log, sources, gaps, verification)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writing_io_and_invariants as wio  # noqa: E402

# ---------------------------------------------------------------------------
# Regex & structural patterns (bilingual EN + ZH)
# ---------------------------------------------------------------------------

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TEX_HEADING_RE = re.compile(r"^\\((?:sub)*section)\*?\{(.+?)\}")
NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z\u4e00-\u9fff][^\n]{1,80})$")
ZH_HEADING_RE = re.compile(r"^([一二三四五六七八九十]+)[、.．]\s*([^\n]{2,40})$")
UPPER_HEADING_RE = re.compile(r"^([A-Z][A-Z0-9\s,&/\-]{2,45})$")
SENT_SPLIT_RE = re.compile(
    r"(?<!\bal\.)(?<!\bFig\.)(?<!\bFigs\.)(?<!\bEq\.)(?<!\bTab\.)(?<!\bTbl\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bNo\.)(?<!\bDr\.)(?<!\bRef\.)(?<!\bSec\.)(?<=[。！？.!?])\s+",
    re.I,
)

CANONICAL_ROLES: list[tuple[str, tuple[str, ...]]] = [
    ("abstract", ("abstract", "摘要")),
    ("introduction", ("introduction", "background", "引言", "绪论", "背景")),
    ("related_work", ("related work", "literature review", "prior work", "background and related", "相关工作", "文献综述", "研究现状")),
    ("methods", ("method", "materials", "experimental", "methodology", "protocol", "setup", "方法", "实验设计", "材料与方法")),
    ("results", ("result", "finding", "evaluation", "experiment", "结果", "实验结果")),
    ("discussion", ("discussion", "limitation", "讨论", "局限")),
    ("conclusion", ("conclusion", "concluding", "summary", "结论", "总结")),
    ("references", ("reference", "bibliography", "参考文献")),
]

ALLOWED_ACTION_VERBS = {
    "MOVE",
    "MERGE",
    "SPLIT",
    "NARROW_CLAIM",
    "ADD_BRIDGE",
    "REGROUP_BY_DIMENSION",
    "DEFER_TO_AUTHOR",
}

VAGUE_ADVICE_PATTERNS = [
    r"加强逻辑", r"逻辑不够清晰", r"建议优化结构", r"提升流畅度",
    r"\bstrengthen\s+logic\b", r"\bimprove\s+flow\b", r"\bbetter\s+structure\b",
]

# Year-anchored study opener for chronological laundry-list detection
YEAR_STUDY_RE = re.compile(
    r"(?:(?:[A-Z][A-Za-z\-]+(?:\s+et\s+al\.|\s+and\s+[A-Z][A-Za-z\-]+)?\s*\(\s*((?:19|20)\d{2})[a-z]?\s*\))"
    r"|(?:(?:In|By)\s+((?:19|20)\d{2})[, ]+\s*[A-Z][A-Za-z\-]+)"
    r"|(?:((?:19|20)\d{2})\s*年[^。！？\n]{0,20}(?:研究|学者|团队|报道|提出|发现|表明|等)))"
)
SYNTHESIS_CONNECTOR_RE = re.compile(
    r"\b(?:in\s+contrast|whereas|however|unlike|compared\s+(?:with|to)|by\s+comparison|"
    r"reconcil(?:e|ing)|across\s+these\s+studies|collectively|converg(?:e|ing)|diverg(?:e|ing)|"
    r"shared\s+limitation|common\s+bottleneck|under\s+different\s+conditions|two\s+main\s+lines)\b"
    r"|相比之下|然而|与此相反|分歧在于|共同局限|不同之处|按.{1,10}(?:维度|路线|机制)划分|归因于",
    re.I,
)

# Cross-disciplinary same-metric extraction for condition-aware contradiction clustering
METRIC_VALUE_RE = re.compile(
    r"\b(accuracy|AUC|AUROC|AUPRC|F1(?:-score)?|BLEU(?:-\d)?|ROUGE(?:-[L12])?|METEOR|Dice(?:\s+score)?|IoU|mAP|"
    r"C-index|concordance\s+index|RMSE|MSE|MAE|R2|R\^2|R²|IC50|EC50|overpotential|Tafel\s+slope|"
    r"Faradaic\s+efficiency|current\s+density|hazard\s+ratio|odds\s+ratio|perplexity|"
    r"precision|recall|sensitivity|specificity|survival\s+rate|response\s+rate|efficiency|selectivity|"
    r"yield|conversion|mortality|reduction|improvement|error\s+rate|"
    r"准确率|精度|召回率|灵敏度|特异度|效率|选择性|转化率|产率|存活率|有效率|降幅|提升幅度|相关性|相关系数|过电位|法拉第效率|均方根误差|决定系数)\b"
    r"[^.;。\n]{0,48}?"
    r"(?:(increas(?:ed|es|ing)|improv(?:ed|es|ing)|higher|outperform(?:ed|s)?|positive|elevat(?:ed|es)|gained|提升|提高|增加|正相关|优于|上升)"
    r"|(decreas(?:ed|es|ing)|reduc(?:ed|es|ing)|lower|declin(?:ed|es)|degrad(?:ed|es)|negative|drop(?:ped|s)?|failed\s+to\s+improve|no\s+significant|下降|降低|减少|负相关|劣于|未显著|无明显变化))?"
    r"[^.;。\n]{0,28}?([+-]?\d+(?:\.\d+)?)\s*(%|pp|percentage\s+points?|mV/dec|mV|V|mA/cm2|mA\s*cm-2|mA|MPa|kPa|µM|uM|nM|mg/kg|fold|ms|s|K|°C)?",
    re.I,
)
POS_DIR_RE = re.compile(r"\b(?:increas|improv|higher|outperform|positive|elevat|gain|better|superior|enhanc)\w*\b|提升|提高|增加|正相关|优于|上升|显著高于", re.I)
NEG_DIR_RE = re.compile(r"\b(?:decreas|reduc|lower|declin|degrad|negative|drop|worse|inferior|failed\s+to|no\s+significant|null|ineffective)\w*\b|下降|降低|减少|负相关|劣于|未显著|无明显|未能改善", re.I)
CONCESSION_RE = re.compile(
    r"\b(?:whereas|however|although|though|while|in\s+contrast|unlike|depending\s+on|under\s+different|when\s+tested\s+at|"
    r"compared\s+with|at\s+higher|at\s+lower|on\s+the\s+other\s+hand|nevertheless|yet)\b"
    r"|相比之下|然而|尽管|虽然|取决于|在不同条件下|而在.{1,15}(?:条件|组|温度|剂量)下",
    re.I,
)
EXPLANATION_RE = re.compile(
    r"\b(?:attributed\s+to|explained\s+by|because\s+of|due\s+to\s+(?:differences?|variations?|changes?|different|lower|higher)|"
    r"stemming\s+from|accounted\s+for\s+by|driven\s+by\s+(?:differences?|different)|reflects?\s+(?:differences?|variations?)|"
    r"reconciled\s+by|can\s+be\s+explained|this\s+(?:discrepancy|difference|divergence)\s+(?:arises?|results?|stems?|is\s+due))\b"
    r"|归因于|原因在于|这是因为|差异(?:主要)?源于|可由.{1,18}解释|受.{1,15}差异影响|解释了(?:该|上述)?差异",
    re.I,
)

# Condition context extractors (temperature, current density/potential, dose, time window, cohort/group, dataset, p-value)
COND_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°C|℃|\bK\b)", re.I)
COND_CURR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mA(?:/cm2|\s*cm-2)?|A/cm2|mV|V\s+vs\.?\s*(?:RHE|SHE|Ag/AgCl))", re.I)
COND_DOSE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mg/kg|mg/mL|µg/mL|ug/mL|ng/mL|mM|µM|uM|nM|wt%|mol%)", re.I)
COND_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?|min|days?|weeks?|months?)\b", re.I)
COND_PH_RE = re.compile(r"\bpH\s*[:=]?\s*(\d+(?:\.\d+)?)", re.I)
COND_GROUP_RE = re.compile(
    r"\b(high-dose|low-dose|placebo|control\s+group|treatment\s+group|intervention|subgroup|"
    r"pediatric|adult|elderly|male|female|in\s+vitro|in\s+vivo|ex\s+vivo|mouse|murine|human|"
    r"training\s+set|validation\s+set|test\s+set|external\s+cohort|internal\s+cohort|baseline|ablation|"
    r"alkaline|acidic|neutral|ambient|elevated\s+temperature|low\s+temperature|high\s+temperature|"
    r"acute|chronic|early-stage|late-stage|zero-shot|few-shot|fine-tuned|noisy|clean)\b"
    r"|(高剂量组?|低剂量组?|对照组|干预组|实验组|儿童组?|成人组?|老年组?|训练集|验证集|测试集|外部队列|内部队列|体外|体内|碱性|酸性|中性|低温|高温|急性|慢性)",
    re.I,
)
COND_DATASET_RE = re.compile(
    r"\b(?:on|in|for|across|using)\s+(?:the\s+)?([A-Z][A-Za-z0-9\-_]{2,24}(?:\s+(?:dataset|benchmark|cohort|corpus|split))?)\b"
)
COND_SIG_RE = re.compile(r"(?:\bp\s*[<=>≤≥]\s*0?\.\d+(?:e-?\d+)?|\b95%\s*CI\b|\bn\s*=\s*\d+)", re.I)

# Research Question & Contribution extractors (explicit commitment vs background/literature comparison)
RQ_PATTERNS = [
    re.compile(r"(?:research\s+question|we\s+(?:investigate|ask|examine|address|test|aim\s+to|aimed\s+to|seek\s+to|explore)\s+(?:whether|how|why|the)|whether\s+[^.?!]{15,180}\?|how\s+[^.?!]{15,180}\?)", re.I),
    re.compile(r"(?:this\s+(?:study|paper|work|review|meta-analysis)\s+(?:aims?\s+to|aimed\s+to|investigates?|examines?|evaluates?|addresses?)|our\s+(?:primary\s+)?(?:objective|goal|aim)\s+is\s+to)\s+[^.?!]{12,200}", re.I),
    re.compile(r"(?:研究问题|本文(?:旨在|试图|探究|验证|回答|探讨)|本研究(?:旨在|聚焦|考察)|核心问题(?:在于|是))[^。！？\n]{8,180}[。！？?]?"),
]
CONTRIB_PATTERNS = [
    re.compile(r"(?:our\s+(?:main\s+|key\s+|primary\s+)?contributions?\s+(?:are|include|can\s+be\s+summarized)|in\s+this\s+(?:work|study|paper),?\s+we\s+(?:propose|introduce|present|develop|demonstrate|design|construct|validate|establish|evaluate)|here,?\s+we\s+(?:propose|present|develop|demonstrate|show|report)\s+[^.?!\n]{12,200}|we\s+(?:propose|introduce|present|develop|demonstrate|design|construct|validate)\s+[^.?!\n]{15,200})", re.I),
    re.compile(r"(?:本文(?:的)?(?:主要)?(?:贡献|创新点)(?:如下|包括|在于|有)?|我们(?:提出|构建|设计|开发|验证|建立)了)[^。！？\n]{8,200}"),
]
EXPLICIT_COMMITMENT_RE = re.compile(
    r"(?:\b(?:this\s+(?:study|paper|work|review|meta-analysis)\s+(?:aims?\s+to|aimed\s+to|investigates?|examines?|evaluates?|addresses?|proposes?|introduces?|presents?|develops?|assesses?|explores?)|"
    r"our\s+(?:main\s+|key\s+|primary\s+)?(?:contributions?|objective|goal|aim)\b|"
    r"in\s+this\s+(?:work|study|paper),?\s+we\b|"
    r"here,?\s+we\s+(?:propose|present|develop|demonstrate|evaluate|investigate|assess|examine)\b|"
    r"we\s+(?:propose|introduce|develop|construct|design|validate|establish|aim(?:ed)?\s+to|investigate\s+whether|conducted\s+this\s+(?:study|meta-analysis)))\b"
    r"|本文(?:旨在|提出|构建|设计|开发|的?(?:主要)?(?:贡献|创新点))|本研究(?:旨在|聚焦|提出|验证|评估)|我们(?:提出|构建|设计|开发|验证|建立)了)",
    re.I,
)
BACKGROUND_LIT_COMPARISON_RE = re.compile(
    r"(?:\b(?:previous\s+(?:stud(?:y|ies)|work|research|reports?)|prior\s+(?:stud(?:y|ies)|work|research|literature)|"
    r"existing\s+(?:methods?|approaches?|literature|biomarkers?|markers?)|currently,?|conventionally,?|traditional(?:ly)?|"
    r"in\s+the\s+literature|has\s+been\s+(?:widely\s+)?(?:used|reported|shown|studied)|have\s+been\s+(?:widely\s+)?(?:used|reported|shown)|"
    r"are\s+(?:commonly|widely|typically)\s+used|et\s+al\.|compared\s+(?:with|to)\s+(?:conventional|traditional|standard|existing))\b"
    r"|\[\d+(?:\s*[-–,]\s*\d+)*\]|既往研究|先前研究|已有研究|现有方法|传统(?:方法|指标)?|目前(?:常用|主要))",
    re.I,
)
CONTRIB_INTRO_CUE_RE = re.compile(
    r"(?:contributions?\s+(?:are|include|as\s+follows)|summarized\s+as\s+follows|key\s+findings\s+(?:are|include)|"
    r"本文(?:的)?(?:主要)?(?:贡献|创新点)(?:如下|包括|主要体现在)|具体如下)",
    re.I,
)
LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*•]\s+|\d+[.)、]\s*|\(\d+\)\s*|（\d+）\s*|\([ivxIVX]+\)\s*|[一二三四五六七八九十]+[、.．]\s*|第[一二三四五六七八九十]+[，,、：:]\s*)(.+)$"
)
INLINE_ENUM_SPLIT_RE = re.compile(
    r"(?:(?:^|\s)(?:\(\d+\)|（\d+）|\([ivx]+\)|first(?:ly)?,|second(?:ly)?,|third(?:ly)?,|finally,|首先[，,]?|其次[，,]?|最后[，,]?|第一[，,、]?|第二[，,、]?|第三[，,、]?))",
    re.I,
)

# Inference leap & evidence anchors
LEAP_MARKER_RE = re.compile(
    r"\b(?:therefore|thus|hence|consequently|this\s+proves|this\s+demonstrates\s+that|clearly\s+shows\s+that|"
    r"it\s+follows\s+that|undoubtedly|inevitably)\b|因此|由此可见|这证明了|充分说明|必然表明|显然",
    re.I,
)
EVIDENCE_ANCHOR_RE = re.compile(
    r"(?:\bp\s*[<=>≤≥]\s*0?\.\d+|\b[nN]\s*=\s*\d+|\b(?:Fig(?:ure)?\.?|Table|Tbl\.?|Eq\.?|Section|§)\s*S?\d+|"
    r"\[\d+(?:\s*[-–,]\s*\d+)*\]|\\cite[a-z]*\{[^}]+\}|\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?,\s*(?:19|20)\d{2}[a-z]?\)|"
    r"10\.\d{4,9}/|图\s*S?\d+|表\s*S?\d+|\d+(?:\.\d+)?%)",
    re.I,
)
QUANT_CLAIM_RE = re.compile(
    r"(?:\b(?:outperform(?:s|ed)?|surpass(?:es|ed)?|superior\s+to|significant(?:ly)?\s+(?:higher|lower|better|worse|improv|reduc)|"
    r"state-of-the-art|SOTA|dramatically|substantially|doubles?|triples?)\b|"
    r"显著(?:高于|低于|优于|提升|降低)|大幅(?:提升|降低)|领先于|达到最优|翻倍)",
    re.I,
)

# Observational vs Causal & Scope Extrapolation
OBSERVATIONAL_DESIGN_RE = re.compile(
    r"\b(?:observational|retrospective|cross-sectional|correlational|cohort\s+study|case-control|"
    r"association\s+study|spearman|pearson\s+r|non-randomized|uncontrolled|single-center|in\s+vitro|cell\s+line|mouse\s+model)\b"
    r"|观察性|回顾性|横断面|相关性分析|队列研究|病例对照|单中心|体外细胞|小鼠模型|未随机",
    re.I,
)
UNIVERSAL_EXTRAPOLATION_RE = re.compile(
    r"\b(?:universally|in\s+all\s+(?:cases|patients|domains|settings|species)|across\s+all\s+(?:populations|datasets|tasks)|"
    r"completely\s+solves?|definitively\s+eliminates?|guarantees?\s+(?:clinical|general)|ready\s+for\s+(?:immediate\s+)?clinical\s+deployment)\b"
    r"|适用于所有|普遍适用|在一切条件下|彻底解决|完全消除|可直接用于临床|放之四海",
    re.I,
)
LIMITATION_SIGNAL_RE = re.compile(
    r"\b(?:limitation|caveat|constraint|shortcoming|restricted\s+to|scoped\s+to|cannot\s+establish\s+causality|"
    r"further\s+(?:prospective|external|independent)\s+validation|small\s+sample\s+size|single-center)\b"
    r"|局限性?|不足之处|仅适用于|受限于|尚不能确立因果|仍需(?:外部|前瞻性|独立)验证|样本量有限",
    re.I,
)

# Misplaced content checks (e.g., results/speculation in wrong section)
METHOD_IN_RESULTS_RE = re.compile(
    r"\b(?:we\s+configured|hyperparameters?\s+were\s+set|learning\s+rate\s+was|samples?\s+were\s+centrifuged|buffer\s+contained)\b"
    r"|实验参数设置为|离心条件为|缓冲液配方为",
    re.I,
)
BG_IN_RESULTS_RE = re.compile(
    r"\b(?:historically|for\s+decades|in\s+the\s+literature,\s+many\s+studies\s+have\s+explored)\b"
    r"|长期以来，文献中已有大量研究",
    re.I,
)
TRIAL_ERROR_LOG_RE = re.compile(
    r"\b(?:first\s+we\s+tried|initially\s+we\s+attempted.*?(?:but|however)\s+(?:it\s+)?failed|then\s+we\s+tried)\b"
    r"|起初我们尝试.{1,30}但失败|随后我们又试了",
    re.I,
)


def _classify_role(title: str) -> str:
    low = title.lower().strip()
    if any(k in low for k in ("supplemental", "supplementary", "acknowledgment", "declaration", "footnote", "associated data")):
        return "other"
    for role, kws in CANONICAL_ROLES:
        if any(k in low for k in kws):
            return role
    return "other"


SYNONYM_MAP = {
    "propose": "introduce", "proposed": "introduce", "present": "introduce", "presented": "introduce",
    "develop": "build", "developed": "build", "construct": "build", "constructed": "build", "design": "build", "designed": "build",
    "evaluate": "assess", "evaluated": "assess", "validate": "assess", "validated": "assess", "benchmark": "assess", "test": "assess", "tested": "assess",
    "improve": "enhance", "improved": "enhance", "boost": "enhance", "outperform": "enhance", "outperformed": "enhance",
    "predict": "estimate", "predicted": "estimate", "forecast": "estimate",
    "accuracy": "performance", "auroc": "auc", "f1-score": "f1",
    "catalyst": "catalytic", "electrocatalyst": "catalytic", "overpotential": "catalytic",
    "microbiome": "microbiota", "gut": "intestinal",
    "提出": "构建", "设计": "构建", "开发": "构建", "验证": "评估", "测试": "评估", "提高": "提升", "改善": "提升",
}


def _canonicalize_token(tok: str) -> str:
    if tok in SYNONYM_MAP:
        return SYNONYM_MAP[tok]
    if tok.isascii() and len(tok) > 5:
        for suf in ("ation", "ition", "ment", "ness", "ity", "ing", "ed", "es"):
            if tok.endswith(suf) and len(tok) - len(suf) >= 4:
                stem = tok[: -len(suf)]
                return SYNONYM_MAP.get(stem, stem)
        if tok.endswith("ies") and len(tok) > 5:
            stem = tok[:-3] + "y"
            return SYNONYM_MAP.get(stem, stem)
        if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 4:
            stem = tok[:-1]
            return SYNONYM_MAP.get(stem, stem)
    return tok


def _tokenize(text: str, *, canonical: bool = False) -> list[str]:
    en = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    zh_bigrams: list[str] = []
    for chunk in zh_chunks:
        if len(chunk) == 2:
            zh_bigrams.append(chunk)
        else:
            for k in range(len(chunk) - 1):
                zh_bigrams.append(chunk[k : k + 2])
    stop = {
        "the", "and", "that", "this", "with", "from", "for", "are", "was", "were",
        "have", "has", "been", "into", "which", "their", "there", "these", "those",
        "also", "such", "than", "more", "most", "can", "may", "our", "not", "but",
        "using", "used", "study", "paper", "results", "method", "methods", "data",
        "我们", "本文", "研究", "结果", "方法", "表明", "显示", "进行", "通过", "以及",
    }
    raw = [t for t in en + zh_bigrams if t not in stop]
    if canonical:
        return [_canonicalize_token(t) for t in raw]
    return raw


def _word_ngrams(tokens: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _parse_manuscript(text: str) -> list[dict[str, Any]]:
    """Parse manuscript into a parent-child section tree with indexed paragraphs and line ranges.

    Handles both true nested heading levels (`## Methods` -> `### 2.1 Study Design`) AND
    flattened PMC/PDF-converted canonical containers (`## Methods` with 0 direct lines followed by
    `## Search strategy`, `## Statistical analysis` before `## Results`). Parent sections aggregate
    descendant paragraphs and character counts so container parents are never falsely flagged as empty.
    """
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    cur_title = "(Preamble)"
    cur_level = 0
    cur_start = 1
    buf: list[tuple[int, str]] = []
    sec_counter = 0

    def flush(end_line: int) -> None:
        nonlocal sec_counter
        non_empty = [x for _, x in buf if x.strip()]
        if not non_empty and cur_title == "(Preamble)":
            return
        sec_counter += 1
        sec_id = f"S{sec_counter}"
        raw_role = _classify_role(cur_title)
        paras: list[dict[str, Any]] = []
        p_buf: list[tuple[int, str]] = []
        p_idx = 0

        def flush_para() -> None:
            nonlocal p_idx
            if not p_buf:
                return
            p_idx += 1
            joined = " ".join(s for _, s in p_buf)
            paras.append({
                "paragraph_id": f"{sec_id}.P{p_idx}",
                "section_id": sec_id,
                "section": cur_title,
                "role": raw_role,
                "start_line": p_buf[0][0],
                "end_line": p_buf[-1][0],
                "line_range": f"L{p_buf[0][0]}-L{p_buf[-1][0]}",
                "text": joined,
                "raw_lines": [s for _, s in p_buf],
                "char_len": len(joined),
            })

        for lno, ltxt in buf:
            st = ltxt.strip()
            if not st:
                flush_para()
                p_buf = []
            else:
                p_buf.append((lno, st))
        flush_para()

        direct_chars = sum(p["char_len"] for p in paras)
        sections.append({
            "section_id": sec_id,
            "title": cur_title,
            "level": cur_level,
            "raw_role": raw_role,
            "role": raw_role,
            "start_line": cur_start,
            "end_line": max(cur_start, end_line),
            "parent_section_id": None,
            "children_section_ids": [],
            "descendant_section_ids": [],
            "is_container_parent": False,
            "direct_paragraph_count": len(paras),
            "direct_char_len": direct_chars,
            "paragraph_count": len(paras),
            "char_len": direct_chars,
            "has_content": len(paras) > 0,
            "paragraphs": paras,
        })

    for lineno, raw in enumerate(lines, start=1):
        st = raw.strip()
        m_md = MD_HEADING_RE.match(st)
        m_tex = TEX_HEADING_RE.match(st)
        m_num = NUM_HEADING_RE.match(st) if not m_md and not m_tex and len(st) < 75 and not st.endswith((".", "。")) else None
        m_zh = ZH_HEADING_RE.match(st) if not m_md and not m_tex and not m_num and len(st) < 42 and not st.endswith(("。", ".", "；", ";")) else None
        m_up = UPPER_HEADING_RE.match(st) if not m_md and not m_tex and not m_num and not m_zh and len(st) < 45 else None
        if m_md:
            flush(lineno - 1)
            cur_level = len(m_md.group(1))
            cur_title = m_md.group(2).strip()
            cur_start = lineno
            buf = []
        elif m_tex:
            flush(lineno - 1)
            cmd = m_tex.group(1)
            cur_level = 1 + cmd.count("sub")
            cur_title = m_tex.group(2).strip()
            cur_start = lineno
            buf = []
        elif m_num and (_classify_role(m_num.group(2)) != "other" or "." in m_num.group(1)):
            flush(lineno - 1)
            cur_level = 1 + m_num.group(1).count(".")
            cur_title = f"{m_num.group(1)} {m_num.group(2).strip()}"
            cur_start = lineno
            buf = []
        elif m_zh and _classify_role(m_zh.group(2)) != "other":
            flush(lineno - 1)
            cur_level = 1
            cur_title = f"{m_zh.group(1)}、{m_zh.group(2).strip()}"
            cur_start = lineno
            buf = []
        elif m_up and _classify_role(m_up.group(1)) != "other":
            flush(lineno - 1)
            cur_level = 1
            cur_title = m_up.group(1).strip().title()
            cur_start = lineno
            buf = []
        else:
            buf.append((lineno, raw))
    flush(len(lines))

    # Build parent-child section tree
    by_id = {s["section_id"]: s for s in sections}
    stack: list[dict[str, Any]] = []
    # Treat single top-level H1 manuscript title (level 1 with 0 direct paragraphs) as document root, not a section container
    for sec in sections:
        lvl = sec["level"]
        if lvl <= 0:
            continue
        if lvl == 1 and sec["raw_role"] == "other" and sec["direct_paragraph_count"] == 0 and sec["section_id"] == "S1":
            continue

        # Pop ancestors that cannot be parents of `sec`
        while stack:
            top = stack[-1]
            # Standard hierarchical nesting (e.g., ## Methods -> ### 2.1 Study Design)
            if top["level"] < lvl:
                break
            # Flattened PMC/PDF canonical container: empty canonical parent (`## Methods`, `## Results`)
            # followed by same-level subsections (`## Search strategy`, `## Characteristics of the results...`)
            if (
                top["level"] == lvl
                and top["direct_paragraph_count"] == 0
                and top["raw_role"] != "other"
                and (
                    sec["raw_role"] == "other"
                    or (sec["raw_role"] == top["raw_role"] and len(sec["title"]) > len(top["title"]) + 4)
                )
            ):
                break
            stack.pop()

        if stack:
            parent = stack[-1]
            sec["parent_section_id"] = parent["section_id"]
            parent["children_section_ids"].append(sec["section_id"])
            parent["is_container_parent"] = True
            # Propagate canonical role from parent to non-canonical subsections
            if sec["role"] == "other" and parent["role"] != "other":
                sec["role"] = parent["role"]
                for p in sec["paragraphs"]:
                    p["role"] = sec["role"]

        # Only push onto stack if `sec` could itself be a parent
        stack.append(sec)

    # Bottom-up aggregation of descendant paragraphs, char_len, and end_line
    for sec in reversed(sections):
        desc_ids: list[str] = []
        agg_paras = sec["direct_paragraph_count"]
        agg_chars = sec["direct_char_len"]
        agg_end = sec["end_line"]
        for cid in sec["children_section_ids"]:
            child = by_id[cid]
            desc_ids.append(cid)
            desc_ids.extend(child["descendant_section_ids"])
            agg_paras += child["paragraph_count"]
            agg_chars += child["char_len"]
            agg_end = max(agg_end, child["end_line"])
        sec["descendant_section_ids"] = desc_ids
        sec["paragraph_count"] = agg_paras
        sec["char_len"] = agg_chars
        sec["end_line"] = agg_end
        sec["has_content"] = agg_paras > 0

    return sections


def _make_card(
    *,
    card_num: int,
    dimension: str,
    severity: str,
    action_verb: str,
    section: str,
    paragraph_id: str,
    line_range: str,
    quote_excerpt: str,
    diagnosis: str,
    evidence_basis: str,
    executable_instruction: str,
    target_location: str = "",
    author_decision_required: bool = False,
) -> dict[str, Any]:
    if action_verb not in ALLOWED_ACTION_VERBS:
        raise ValueError(f"Disallowed action verb: {action_verb}")
    return {
        "card_id": f"EDIT-{card_num:03d}",
        "dimension": dimension,
        "severity": severity,
        "action_verb": action_verb,
        "anchor": {
            "section": section,
            "paragraph_id": paragraph_id,
            "line_range": line_range,
            "quote_excerpt": quote_excerpt[:160].strip(),
        },
        "diagnosis": diagnosis,
        "evidence_basis": evidence_basis,
        "executable_instruction": executable_instruction,
        "target_location": target_location or f"{section} ({paragraph_id}, {line_range})",
        "author_decision_required": author_decision_required,
        "author_decision_options": ["Accept", "Partially Accept", "Reject"],
    }


def _extract_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _extract_condition_context(sent: str) -> dict[str, Any]:
    """Extract experimental condition dimensions & statistical significance markers around a metric value."""
    dims: dict[str, set[str]] = {}
    for key, pat in (
        ("temperature", COND_TEMP_RE),
        ("electrochem", COND_CURR_RE),
        ("dose", COND_DOSE_RE),
        ("time_window", COND_TIME_RE),
        ("ph", COND_PH_RE),
    ):
        hits = {m.group(0).strip().lower() for m in pat.finditer(sent)}
        if hits:
            dims[key] = hits
    grp_hits = {(m.group(1) or m.group(2)).strip().lower() for m in COND_GROUP_RE.finditer(sent)}
    if grp_hits:
        dims["group"] = grp_hits
    ds_stop = {"the", "this", "our", "all", "table", "fig", "figure", "section", "results", "methods"}
    ds_hits = {
        m.group(1).strip().lower()
        for m in COND_DATASET_RE.finditer(sent)
        if m.group(1).strip().lower() not in ds_stop
    }
    if ds_hits:
        dims["dataset"] = ds_hits
    sigs = [m.group(0).strip() for m in COND_SIG_RE.finditer(sent)]
    has_concession = bool(CONCESSION_RE.search(sent))
    flat_labels = sorted(f"{k}={v}" for k, vs in dims.items() for v in sorted(vs))
    return {
        "dimensions": {k: sorted(v) for k, v in dims.items()},
        "labels": flat_labels,
        "significance": sigs[:4],
        "has_concession": has_concession,
    }


def _compare_conditions(ca: dict[str, Any], cb: dict[str, Any]) -> str:
    """Classify condition comparability: 'distinct_conditions' | 'comparable_conditions' | 'unspecified_context'."""
    da, db = ca.get("dimensions", {}), cb.get("dimensions", {})
    if not da or not db:
        return "unspecified_context"
    shared_dims = set(da) & set(db)
    if not shared_dims:
        return "unspecified_context"
    for dim in shared_dims:
        sa, sb = set(da[dim]), set(db[dim])
        if sa and sb and sa.isdisjoint(sb):
            return "distinct_conditions"
    return "comparable_conditions"


def _detect_same_metric_conflicts(all_paras: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Condition-aware, deduplicated 4-class detector for cross-paragraph same-metric comparisons.

    Explicitly classifies every divergent same-metric pair into 4 exact categories:
      1) distinct_conditions_justified  — distinct experimental/cohort conditions on a shared dimension (not a contradiction)
      2) explained_in_text              — divergence explicitly reconciled/explained in manuscript prose (not an unresolved contradiction)
      3) unexplained_true_contradiction — comparable conditions without explanation (blocking DEFER_TO_AUTHOR)
      4) missing_comparison_dimension   — unspecified/non-overlapping condition context without explanation (major DEFER_TO_AUTHOR)

    Returns (unresolved_clusters, full_comparison_audit).
    """
    metric_mentions: dict[str, list[dict[str, Any]]] = {}
    seen_mention_keys: set[tuple[str, str, str, str]] = set()
    for p in all_paras:
        if p["role"] in ("references",):
            continue
        for sent in _extract_sentences(p["text"]):
            cond = _extract_condition_context(sent)
            has_reconcile = (
                bool(SYNTHESIS_CONNECTOR_RE.search(p["text"]))
                or bool(EXPLANATION_RE.search(p["text"]))
                or cond["has_concession"]
            )
            for m in METRIC_VALUE_RE.finditer(sent):
                metric_raw = m.group(1).lower().strip()
                metric_norm = re.sub(r"\s+", " ", metric_raw)
                val_str = m.group(4)
                unit = (m.group(5) or "").strip()
                pos_hit = bool(m.group(2)) or bool(POS_DIR_RE.search(sent))
                neg_hit = bool(m.group(3)) or bool(NEG_DIR_RE.search(sent))
                direction = "ambiguous"
                if pos_hit and not neg_hit:
                    direction = "positive"
                elif neg_hit and not pos_hit:
                    direction = "negative"
                elif m.group(2):
                    direction = "positive"
                elif m.group(3):
                    direction = "negative"
                mkey = (metric_norm, p["paragraph_id"], val_str, direction)
                if mkey in seen_mention_keys:
                    continue
                seen_mention_keys.add(mkey)
                try:
                    val_num = float(val_str)
                except ValueError:
                    val_num = None
                metric_mentions.setdefault(metric_norm, []).append({
                    "metric": metric_norm,
                    "value": val_str,
                    "val_num": val_num,
                    "unit": unit,
                    "direction": direction,
                    "section": p["section"],
                    "paragraph_id": p["paragraph_id"],
                    "line_range": p["line_range"],
                    "sentence": sent[:180],
                    "conditions": cond,
                    "has_reconcile": has_reconcile,
                })

    clusters: list[dict[str, Any]] = []
    comparison_audit: list[dict[str, Any]] = []
    for metric, items in sorted(metric_mentions.items()):
        if len(items) < 2:
            continue
        comparable_entries: dict[str, dict[str, Any]] = {}
        unspecified_entries: dict[str, dict[str, Any]] = {}
        distinct_skipped = 0
        explained_skipped = 0
        conflict_types: set[str] = set()
        any_reconciled = False

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                if a["paragraph_id"] == b["paragraph_id"]:
                    continue
                opposite_dir = (
                    a["direction"] in ("positive", "negative")
                    and b["direction"] in ("positive", "negative")
                    and a["direction"] != b["direction"]
                )
                large_pct_gap = (
                    a["unit"] == "%"
                    and b["unit"] == "%"
                    and a["val_num"] is not None
                    and b["val_num"] is not None
                    and abs(a["val_num"] - b["val_num"]) >= 15.0
                )
                if not (opposite_dir or large_pct_gap):
                    continue
                comp = _compare_conditions(a["conditions"], b["conditions"])
                ctype = "opposite_direction" if opposite_dir else "large_magnitude_divergence"
                if comp == "distinct_conditions":
                    distinct_skipped += 1
                    comparison_audit.append({
                        "metric": metric,
                        "conflict_type": ctype,
                        "classification": "distinct_conditions_justified",
                        "condition_comparability": comp,
                        "reconciled_in_text": bool(a["has_reconcile"] or b["has_reconcile"]),
                        "para_a": a["paragraph_id"],
                        "para_b": b["paragraph_id"],
                        "conditions_a": a["conditions"]["labels"],
                        "conditions_b": b["conditions"]["labels"],
                    })
                    continue
                if a["has_reconcile"] or b["has_reconcile"]:
                    any_reconciled = True
                    explained_skipped += 1
                    comparison_audit.append({
                        "metric": metric,
                        "conflict_type": ctype,
                        "classification": "explained_in_text",
                        "condition_comparability": comp,
                        "reconciled_in_text": True,
                        "para_a": a["paragraph_id"],
                        "para_b": b["paragraph_id"],
                        "conditions_a": a["conditions"]["labels"],
                        "conditions_b": b["conditions"]["labels"],
                    })
                    continue
                conflict_types.add(ctype)
                cls_label = (
                    "unexplained_true_contradiction"
                    if comp == "comparable_conditions"
                    else "missing_comparison_dimension"
                )
                comparison_audit.append({
                    "metric": metric,
                    "conflict_type": ctype,
                    "classification": cls_label,
                    "condition_comparability": comp,
                    "reconciled_in_text": False,
                    "para_a": a["paragraph_id"],
                    "para_b": b["paragraph_id"],
                    "conditions_a": a["conditions"]["labels"],
                    "conditions_b": b["conditions"]["labels"],
                })
                bucket = comparable_entries if comp == "comparable_conditions" else unspecified_entries
                for entry in (a, b):
                    ek = f"{entry['paragraph_id']}:{entry['value']}{entry['unit']}"
                    bucket[ek] = {
                        "section": entry["section"],
                        "paragraph_id": entry["paragraph_id"],
                        "line_range": entry["line_range"],
                        "value": entry["value"],
                        "unit": entry["unit"],
                        "direction": entry["direction"],
                        "condition_labels": entry["conditions"]["labels"],
                        "significance": entry["conditions"]["significance"],
                        "sentence": entry["sentence"],
                    }

        chosen_entries = list(comparable_entries.values()) or list(unspecified_entries.values())
        if len(chosen_entries) < 2:
            continue
        chosen_entries.sort(key=lambda e: (e["paragraph_id"], e["value"]))
        comp_status = "comparable_conditions" if comparable_entries else "unspecified_context"
        classification = (
            "unexplained_true_contradiction"
            if comp_status == "comparable_conditions"
            else "missing_comparison_dimension"
        )
        clusters.append({
            "cluster_id": f"MCLUSTER-{len(clusters) + 1:02d}",
            "metric": metric,
            "conflict_type": "opposite_direction" if "opposite_direction" in conflict_types else "large_magnitude_divergence",
            "classification": classification,
            "condition_comparability": comp_status,
            "distinct_condition_pairs_filtered": distinct_skipped,
            "explained_in_text_pairs_filtered": explained_skipped,
            "reconciled_in_text": any_reconciled,
            "entries": chosen_entries[:6],
            "entry_a": chosen_entries[0],
            "entry_b": chosen_entries[-1],
        })
    return clusters, comparison_audit


def _classify_commitment_type(txt: str, kind: str) -> str:
    """Distinguish explicit study commitments from background/prior-literature comparison statements."""
    if kind in ("itemized_contribution", "inline_enumerated_contribution"):
        return "explicit_study_commitment"
    has_explicit = bool(EXPLICIT_COMMITMENT_RE.search(txt))
    has_bg = bool(BACKGROUND_LIT_COMPARISON_RE.search(txt))
    if has_bg and not has_explicit:
        return "background_or_literature_comparison"
    if has_explicit:
        return "explicit_study_commitment"
    if has_bg:
        return "background_or_literature_comparison"
    return "background_or_literature_comparison"


def _extract_rq_and_contributions(intro_secs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract explicit study commitments vs background/literature comparison statements."""
    rq_claims: list[dict[str, Any]] = []
    contrib_claims: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    def _add(target: list[dict[str, Any]], p: dict[str, Any], txt: str, kind: str) -> None:
        clean = re.sub(r"\s+", " ", txt).strip()
        if len(clean) < 12 or clean.lower() in seen_texts:
            return
        seen_texts.add(clean.lower())
        target.append({
            "paragraph_id": p["paragraph_id"],
            "section": p["section"],
            "line_range": p["line_range"],
            "kind": kind,
            "commitment_type": _classify_commitment_type(clean, kind),
            "text": clean,
        })

    for sec in intro_secs:
        in_contrib_list = False
        for p in sec["paragraphs"]:
            raw_lines = p.get("raw_lines") or [p["text"]]
            if CONTRIB_INTRO_CUE_RE.search(p["text"]):
                in_contrib_list = True
            # 1) Line-level Markdown / Chinese list items following a contribution cue
            matched_list_lines = False
            for rl in raw_lines:
                ml = LIST_ITEM_RE.match(rl)
                if ml and (in_contrib_list or CONTRIB_INTRO_CUE_RE.search(p["text"])):
                    _add(contrib_claims, p, ml.group(1), "itemized_contribution")
                    matched_list_lines = True
            if matched_list_lines:
                continue
            # 2) Inline enumerated contributions like "(1) ... (2) ..." or "首先...其次..."
            if CONTRIB_INTRO_CUE_RE.search(p["text"]):
                parts = [seg.strip(" ;；:：") for seg in INLINE_ENUM_SPLIT_RE.split(p["text"]) if seg and len(seg.strip()) >= 15]
                if len(parts) >= 2:
                    for seg in parts[1:]:
                        _add(contrib_claims, p, seg, "inline_enumerated_contribution")
                    continue
            # 3) Sentence-level explicit / implicit RQ & contribution extraction
            for sent in _extract_sentences(p["text"]):
                if any(pat.search(sent) for pat in RQ_PATTERNS):
                    _add(rq_claims, p, sent, "research_question")
                if any(pat.search(sent) for pat in CONTRIB_PATTERNS):
                    _add(contrib_claims, p, sent, "contribution_statement")
    return rq_claims, contrib_claims


def diagnose_manuscript(
    manuscript_path: Path,
    *,
    case_id: str = "case-01",
    task_id: str = "paper-structure-optimization",
    case_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run full six-dimension source-grounded structural diagnosis."""
    read_res = wio.read_document_text(manuscript_path)
    if not read_res["ok"]:
        raise RuntimeError(f"Failed to read manuscript {manuscript_path}: {read_res.get('meta')}")
    raw_text = read_res["text"]
    read_meta = read_res.get("meta") or {}
    lines = raw_text.splitlines()
    sections = _parse_manuscript(raw_text)
    all_paras = [p for s in sections for p in s["paragraphs"]]

    by_role: dict[str, list[dict[str, Any]]] = {}
    for s in sections:
        by_role.setdefault(s["role"], []).append(s)

    cards: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if read_meta.get("degraded_mode"):
        gaps.append({
            "code": "PDF_STDLIB_FALLBACK_EXTRACTION",
            "severity": "minor",
            "detail": f"Document extracted via fallback method ({read_meta.get('method')}); verify line wraps and equation symbols.",
        })

    def add_card(**kwargs: Any) -> dict[str, Any]:
        card = _make_card(card_num=len(cards) + 1, **kwargs)
        cards.append(card)
        evidence_map.append({
            "card_id": card["card_id"],
            "dimension": card["dimension"],
            "source_file": manuscript_path.name,
            "section": card["anchor"]["section"],
            "paragraph_id": card["anchor"]["paragraph_id"],
            "line_range": card["anchor"]["line_range"],
            "quote_excerpt": card["anchor"]["quote_excerpt"],
        })
        return card

    # =========================================================================
    # Dimension 1: Research Question & Contribution Cross-Chapter Alignment
    # =========================================================================
    intro_secs = by_role.get("introduction", []) + by_role.get("abstract", [])
    method_secs = by_role.get("methods", [])
    result_secs = by_role.get("results", [])
    concl_secs = by_role.get("conclusion", []) + by_role.get("discussion", [])

    rq_claims, contrib_claims = _extract_rq_and_contributions(intro_secs)

    methods_toks = set(_tokenize(" ".join(p["text"] for s in method_secs for p in s["paragraphs"]), canonical=True))
    results_toks = set(_tokenize(" ".join(p["text"] for s in result_secs for p in s["paragraphs"]), canonical=True))
    concl_toks_all = set(_tokenize(" ".join(p["text"] for s in concl_secs for p in s["paragraphs"]), canonical=True))
    heading_toks = set(_tokenize(" ".join(s["title"] for s in (method_secs + result_secs + concl_secs)), canonical=True))
    downstream_toks = methods_toks | results_toks | concl_toks_all
    rq_alignment_trace: list[dict[str, Any]] = []

    for claim in contrib_claims + rq_claims:
        ctoks = set(_tokenize(claim["text"], canonical=True))
        if len(ctoks) < 3 or not downstream_toks:
            continue
        overall_cov = len(ctoks & downstream_toks) / max(1, len(ctoks))
        m_cov = len(ctoks & methods_toks) / max(1, len(ctoks)) if methods_toks else 0.0
        r_cov = len(ctoks & results_toks) / max(1, len(ctoks)) if results_toks else 0.0
        c_cov = len(ctoks & concl_toks_all) / max(1, len(ctoks)) if concl_toks_all else 0.0
        heading_hit = len(ctoks & heading_toks) >= 1

        if overall_cov >= 0.32 or (heading_hit and r_cov >= 0.15):
            tier = "fulfilled"
        elif overall_cov < 0.14 and not heading_hit:
            tier = "blocking"
        elif overall_cov < 0.25 and (m_cov < 0.10 or r_cov < 0.10):
            tier = "major"
        elif concl_toks_all and c_cov < 0.08:
            tier = "minor"
        else:
            tier = "fulfilled"

        commitment_type = claim.get("commitment_type", "explicit_study_commitment")
        rq_alignment_trace.append({
            "paragraph_id": claim["paragraph_id"],
            "section": claim["section"],
            "line_range": claim["line_range"],
            "kind": claim["kind"],
            "commitment_type": commitment_type,
            "claim_excerpt": claim["text"][:150],
            "downstream_concept_coverage": round(overall_cov, 3),
            "methods_coverage": round(m_cov, 3),
            "results_coverage": round(r_cov, 3),
            "conclusion_coverage": round(c_cov, 3),
            "section_heading_hit": heading_hit,
            "confidence_tier": tier,
            "fulfilled": tier == "fulfilled",
        })
        if tier != "fulfilled":
            if commitment_type == "background_or_literature_comparison":
                add_card(
                    dimension="D1_rq_contribution_alignment",
                    severity="minor",
                    action_verb="DEFER_TO_AUTHOR",
                    section=claim["section"],
                    paragraph_id=claim["paragraph_id"],
                    line_range=claim["line_range"],
                    quote_excerpt=claim["text"],
                    diagnosis=(
                        f"Background/literature comparison statement in {claim['section']} has low downstream empirical overlap "
                        f"(coverage={overall_cov:.2f}); treated as contextual framing rather than an unfulfilled study commitment."
                    ),
                    evidence_basis="Commitment classifier distinguished background/literature comparison from explicit study promise.",
                    executable_instruction=(
                        "DEFER_TO_AUTHOR: confirm this sentence is contextual background only (no new experiment needed), "
                        "or explicitly declare it as a study objective if empirical evaluation was intended."
                    ),
                    author_decision_required=True,
                )
            else:
                verb = "NARROW_CLAIM" if tier == "blocking" else "ADD_BRIDGE"
                add_card(
                    dimension="D1_rq_contribution_alignment",
                    severity=tier,
                    action_verb=verb,
                    section=claim["section"],
                    paragraph_id=claim["paragraph_id"],
                    line_range=claim["line_range"],
                    quote_excerpt=claim["text"],
                    diagnosis=(
                        f"Explicit study commitment ({claim['kind']}) in {claim['section']} shows {tier}-level downstream closure gap "
                        f"(normalized concept coverage={overall_cov:.2f}: Methods={m_cov:.2f}, Results={r_cov:.2f}, Conclusion={c_cov:.2f})."
                    ),
                    evidence_basis="Canonicalized cross-chapter explicit RQ/Contribution -> Methods/Results/Conclusion mapping table.",
                    executable_instruction=(
                        "Either add the missing empirical verification/bridge in Methods & Results or narrow this explicit contribution/RQ "
                        "statement in Introduction so every promised deliverable maps 1:1 to presented evidence."
                    ),
                    author_decision_required=True,
                )

    if by_role.get("introduction") and not rq_claims and not contrib_claims:
        s0 = by_role["introduction"][0]
        p0 = s0["paragraphs"][-1] if s0["paragraphs"] else {"paragraph_id": f"{s0['section_id']}.P0", "line_range": f"L{s0['start_line']}-L{s0['end_line']}", "text": s0["title"]}
        add_card(
            dimension="D1_rq_contribution_alignment",
            severity="major",
            action_verb="ADD_BRIDGE",
            section=s0["title"],
            paragraph_id=p0["paragraph_id"],
            line_range=p0["line_range"],
            quote_excerpt=p0["text"],
            diagnosis="Introduction lacks an explicit, falsifiable research question or itemized contribution statement linking problem to Methods/Results.",
            evidence_basis="No explicit RQ/hypothesis or contribution anchor matched in Introduction paragraphs.",
            executable_instruction="Append a concise closing paragraph to Introduction stating the concrete research question, approach, and bounded contributions.",
            author_decision_required=True,
        )

    # Check Intro <-> Conclusion overall conceptual closure
    if by_role.get("introduction") and concl_secs:
        intro_toks = set(_tokenize(" ".join(p["text"] for s in by_role["introduction"] for p in s["paragraphs"])))
        concl_toks = set(_tokenize(" ".join(p["text"] for s in concl_secs for p in s["paragraphs"])))
        if intro_toks and concl_toks:
            closure = len(intro_toks & concl_toks) / max(1, min(len(intro_toks), len(concl_toks)))
            if closure < 0.16:
                s0 = by_role["introduction"][0]
                c0 = concl_secs[0]
                cp0 = c0["paragraphs"][0] if c0["paragraphs"] else {"paragraph_id": f"{c0['section_id']}.P0", "line_range": f"L{c0['start_line']}-L{c0['end_line']}", "text": c0["title"]}
                add_card(
                    dimension="D1_rq_contribution_alignment",
                    severity="blocking",
                    action_verb="ADD_BRIDGE",
                    section=c0["title"],
                    paragraph_id=cp0["paragraph_id"],
                    line_range=cp0["line_range"],
                    quote_excerpt=cp0["text"],
                    diagnosis=f"Low conceptual closure ({closure:.2f}) between Introduction ({s0['title']}) and {c0['title']}; conclusion drifts from posed problem.",
                    evidence_basis=f"Shared domain concept overlap={closure:.2f} (<0.16 threshold) between Intro and Conclusion/Discussion.",
                    executable_instruction=f"Rewrite the opening sentence of {c0['title']} ({cp0['paragraph_id']}) to directly answer the research question posed in {s0['title']}.",
                    author_decision_required=False,
                )

    # =========================================================================
    # Dimension 2: Section Structure, Narrative Order & Chronological Laundry List
    # =========================================================================
    chronological_clusters: list[dict[str, Any]] = []
    for sec in sections:
        if sec["role"] in ("references", "methods"):
            continue
        # Detect consecutive paragraphs ordered chronologically by study year without comparative synthesis
        year_paras: list[tuple[int, dict[str, Any], bool]] = []
        for p in sec["paragraphs"]:
            ym = YEAR_STUDY_RE.search(p["text"])
            if ym:
                yr_str = ym.group(1) or ym.group(2) or ym.group(3)
                if yr_str and yr_str.isdigit():
                    has_synth = bool(SYNTHESIS_CONNECTOR_RE.search(p["text"]))
                    year_paras.append((int(yr_str), p, has_synth))

        # Check within a single paragraph too (multi-sentence chronological list)
        for p in sec["paragraphs"]:
            sents = _extract_sentences(p["text"])
            sent_years: list[int] = []
            for st in sents:
                ym = YEAR_STUDY_RE.search(st)
                if ym:
                    yr_str = ym.group(1) or ym.group(2) or ym.group(3)
                    if yr_str and yr_str.isdigit():
                        sent_years.append(int(yr_str))
            if len(sent_years) >= 3 and sent_years == sorted(sent_years) and not SYNTHESIS_CONNECTOR_RE.search(p["text"]):
                chronological_clusters.append({
                    "section": sec["title"],
                    "scope": "single_paragraph",
                    "paragraph_ids": [p["paragraph_id"]],
                    "years": sent_years,
                })
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="blocking",
                    action_verb="REGROUP_BY_DIMENSION",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis=(
                        f"Chronological study-by-study laundry list detected ({sent_years}) without shared comparative dimension "
                        "or synthesis connecting prior studies to the current research question."
                    ),
                    evidence_basis=f"Detected {len(sent_years)} monotonically ordered year-anchored study summaries ({sent_years}) with 0 comparative synthesis connectors.",
                    executable_instruction=(
                        "Split and regroup these studies by shared comparison axes (problem setting, experimental condition, "
                        "evaluation metric, or mechanism) and add an explicit topic sentence stating the central dispute."
                    ),
                    author_decision_required=False,
                )

        if len(year_paras) >= 3:
            yrs = [x[0] for x in year_paras]
            non_synth = sum(1 for x in year_paras if not x[2])
            is_mono = yrs == sorted(yrs)
            if is_mono and non_synth >= max(2, len(year_paras) - 1):
                pids = [x[1]["paragraph_id"] for x in year_paras]
                lstart = year_paras[0][1]["start_line"]
                lend = year_paras[-1][1]["end_line"]
                chronological_clusters.append({
                    "section": sec["title"],
                    "scope": "multi_paragraph",
                    "paragraph_ids": pids,
                    "years": yrs,
                })
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="blocking",
                    action_verb="REGROUP_BY_DIMENSION",
                    section=sec["title"],
                    paragraph_id=",".join(pids),
                    line_range=f"L{lstart}-L{lend}",
                    quote_excerpt=year_paras[0][1]["text"],
                    diagnosis=(
                        f"{len(year_paras)} consecutive paragraphs ({', '.join(pids)}) in '{sec['title']}' are arranged "
                        f"chronologically by year ({yrs}) as isolated per-paper summaries without comparative synthesis."
                    ),
                    evidence_basis=f"Monotonic year progression {yrs} across {len(year_paras)} paragraphs with {non_synth}/{len(year_paras)} lacking comparative synthesis connectors.",
                    executable_instruction=(
                        f"Regroup paragraphs {', '.join(pids)} into thematic comparison blocks by experimental condition/method axis, "
                        "open each block with an author-owned claim, and reconcile divergent metric outcomes explicitly."
                    ),
                    author_decision_required=False,
                )

    # Check canonical section order inversions (e.g., Results before Methods, Conclusion before Results)
    role_order_rank = {"abstract": 1, "introduction": 2, "related_work": 3, "methods": 4, "results": 5, "discussion": 6, "conclusion": 7, "references": 8}
    seen_ranks: list[tuple[int, dict[str, Any]]] = []
    for s in sections:
        rk = role_order_rank.get(s["role"])
        if rk is None:
            continue
        if seen_ranks and rk < seen_ranks[-1][0] and not (seen_ranks[-1][1]["role"] == "results" and s["role"] == "methods"):
            # Note: Nature/Cell puts Methods after Discussion, so only flag severe inversions like Conclusion before Results or Intro after Methods
            if (s["role"] in ("introduction", "abstract") and seen_ranks[-1][0] >= 4) or (seen_ranks[-1][1]["role"] == "conclusion" and s["role"] in ("methods", "results")):
                p0 = s["paragraphs"][0] if s["paragraphs"] else {"paragraph_id": f"{s['section_id']}.P0", "line_range": f"L{s['start_line']}-L{s['end_line']}", "text": s["title"]}
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="major",
                    action_verb="MOVE",
                    section=s["title"],
                    paragraph_id=p0["paragraph_id"],
                    line_range=p0["line_range"],
                    quote_excerpt=p0["text"],
                    diagnosis=f"Section '{s['title']}' (role={s['role']}) appears after '{seen_ranks[-1][1]['title']}' (role={seen_ranks[-1][1]['role']}), breaking standard argument progression.",
                    evidence_basis=f"Section order trace: '{seen_ranks[-1][1]['title']}' at L{seen_ranks[-1][1]['start_line']} precedes '{s['title']}' at L{s['start_line']}.",
                    executable_instruction=f"MOVE section '{s['title']}' before '{seen_ranks[-1][1]['title']}' (unless journal explicitly mandates Methods-at-end).",
                    target_location=f"Before {seen_ranks[-1][1]['title']} (L{seen_ranks[-1][1]['start_line']})",
                    author_decision_required=False,
                )
        seen_ranks.append((rk, s))

    # Misplaced content inside Results (procedural config details or generic background or trial-and-error log)
    for sec in result_secs:
        for p in sec["paragraphs"]:
            if METHOD_IN_RESULTS_RE.search(p["text"]):
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="major",
                    action_verb="MOVE",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Procedural setup / parameter configuration detail is embedded inside Results instead of Methods.",
                    evidence_basis="Matched procedural configuration wording inside a Results paragraph.",
                    executable_instruction=f"MOVE procedural setup sentences from {p['paragraph_id']} into Methods, leaving only a concise condition reference in Results.",
                    target_location="Methods section",
                )
            if BG_IN_RESULTS_RE.search(p["text"]):
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="minor",
                    action_verb="MOVE",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="General literature background appears inside Results, diluting empirical focus.",
                    evidence_basis="Matched generic literature history phrase inside Results.",
                    executable_instruction=f"MOVE background framing from {p['paragraph_id']} to Introduction or Discussion.",
                    target_location="Introduction or Discussion",
                )
            if TRIAL_ERROR_LOG_RE.search(p["text"]):
                add_card(
                    dimension="D2_section_structure_narrative_order",
                    severity="major",
                    action_verb="SPLIT",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Chronological trial-and-error worklog narration ('first we tried... failed') violates press-release argument-first narrative.",
                    evidence_basis="Matched trial-and-error chronological narration inside Results.",
                    executable_instruction=f"SPLIT {p['paragraph_id']}: lead with the core empirical finding and relocate exploratory negative trials to a structured Ablation/Limitations table.",
                )

    # =========================================================================
    # Dimension 3: Argument Chain (Claim -> Evidence -> Warrant) & Conflicts
    # =========================================================================
    same_metric_conflicts, same_metric_comparison_audit = _detect_same_metric_conflicts(all_paras)
    for cf in same_metric_conflicts:
        if not cf["reconciled_in_text"]:
            ea, eb = cf["entry_a"], cf["entry_b"]
            is_comparable = cf["condition_comparability"] == "comparable_conditions"
            cond_summary = "; ".join(
                f"{e['paragraph_id']}: {e['value']}{e['unit']} [{e['direction']}, cond={','.join(e['condition_labels']) or 'unspecified'}]"
                for e in cf["entries"]
            )
            add_card(
                dimension="D3_argument_evidence_chain",
                severity="blocking" if is_comparable else "major",
                action_verb="DEFER_TO_AUTHOR",
                section=f"{ea['section']} <-> {eb['section']}",
                paragraph_id=",".join(dict.fromkeys(e["paragraph_id"] for e in cf["entries"])),
                line_range=f"{ea['line_range']} & {eb['line_range']}",
                quote_excerpt=f"{ea['sentence'][:75]} || {eb['sentence'][:75]}",
                diagnosis=(
                    f"Clustered same-metric {cf['classification']} "
                    f"({cf['cluster_id']}) on '{cf['metric']}' ({cf['conflict_type']}, {len(cf['entries'])} unique entries; "
                    f"{cf['distinct_condition_pairs_filtered']} distinct-condition and {cf.get('explained_in_text_pairs_filtered', 0)} explained pair(s) filtered): "
                    f"{ea['paragraph_id']} reports {ea['value']}{ea['unit']} ({ea['direction']}) vs "
                    f"{eb['paragraph_id']} reports {eb['value']}{eb['unit']} ({eb['direction']})."
                ),
                evidence_basis=f"Condition-bound cluster comparison [{cf['condition_comparability']} / {cf['classification']}]: {cond_summary}",
                executable_instruction=(
                    "Insert an explicit condition-comparison warrant stating whether these divergent values arise from "
                    "different experimental conditions/cohorts/datasets or represent an unreconciled discrepancy; author confirms governing condition."
                ),
                author_decision_required=True,
            )

    for sec in sections:
        if sec["role"] in ("abstract", "references"):
            continue
        for p in sec["paragraphs"]:
            sents = _extract_sentences(p["text"])
            # 3a. Reasoning leap: inferential conclusion sentence lacking evidence anchor in paragraph
            for st in sents:
                if LEAP_MARKER_RE.search(st) and not EVIDENCE_ANCHOR_RE.search(p["text"]):
                    add_card(
                        dimension="D3_argument_evidence_chain",
                        severity="blocking",
                        action_verb="ADD_BRIDGE",
                        section=sec["title"],
                        paragraph_id=p["paragraph_id"],
                        line_range=p["line_range"],
                        quote_excerpt=st,
                        diagnosis="Inferential conclusion/warrant ('therefore/thus/因此/证明了') lacks any evidence anchor (data, citation, Fig/Table, or statistic) in the supporting paragraph.",
                        evidence_basis=f"Inference marker matched in {p['paragraph_id']} with 0 numeric/citation/figure anchors across {p['char_len']} chars.",
                        executable_instruction=(
                            f"ADD_BRIDGE in {p['paragraph_id']}: insert the explicit premise (cited source, quantitative result, or Table/Fig reference) "
                            "and intermediate warrant before drawing this conclusion, or downgrade the claim."
                        ),
                        author_decision_required=True,
                    )
                    break
            # 3b. Quantitative/comparative superiority assertion without numeric/citation anchor
            if QUANT_CLAIM_RE.search(p["text"]) and not EVIDENCE_ANCHOR_RE.search(p["text"]):
                add_card(
                    dimension="D3_argument_evidence_chain",
                    severity="major",
                    action_verb="ADD_BRIDGE",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Quantitative superiority or statistical significance claim has no supporting number, p-value, citation, or Fig/Table pointer.",
                    evidence_basis="Matched comparative/significance assertion with 0 verifiable evidence anchors in paragraph.",
                    executable_instruction=f"Attach exact source metric, sample size, statistical test, or citation to {p['paragraph_id']}, or remove unsubstantiated superlative wording.",
                    author_decision_required=True,
                )
            # 3c. Circular reasoning detector (premise restates conclusion with high canonical overlap and zero external anchor)
            for st in sents:
                cm = re.search(r"\b(because|since|therefore|thus|hence)\b|(?:因为|由于|因此|所以)", st, re.I)
                if cm and not EVIDENCE_ANCHOR_RE.search(st):
                    lhs, rhs = st[: cm.start()].strip(), st[cm.end() :].strip()
                    ltoks = set(_tokenize(lhs, canonical=True))
                    rtoks = set(_tokenize(rhs, canonical=True))
                    if len(ltoks) >= 3 and len(rtoks) >= 3:
                        circ_ov = len(ltoks & rtoks) / max(1, min(len(ltoks), len(rtoks)))
                        if circ_ov >= 0.65:
                            add_card(
                                dimension="D3_argument_evidence_chain",
                                severity="major",
                                action_verb="ADD_BRIDGE",
                                section=sec["title"],
                                paragraph_id=p["paragraph_id"],
                                line_range=p["line_range"],
                                quote_excerpt=st,
                                diagnosis=f"Potential circular reasoning (premise-conclusion canonical concept overlap={circ_ov:.2f}) without independent external evidence anchor.",
                                evidence_basis=f"Clause split at '{cm.group(0)}' shares {sorted(ltoks & rtoks)[:6]} with 0 numeric/citation/figure anchors.",
                                executable_instruction=f"ADD_BRIDGE in {p['paragraph_id']}: replace tautological restatement with an independent empirical mechanism, statistical test, or cited control.",
                                author_decision_required=True,
                            )
                            break
            # 3d. Treatment vs control/baseline condition mismatch detector
            for st in sents:
                if re.search(r"\b(?:baseline|control\s+group|compared\s+(?:with|to)|versus|vs\.?)\b|对照组|基线|相比", st, re.I):
                    clauses = re.split(
                        r"\b(?:compared\s+(?:with|to)|versus|vs\.?|whereas|while|outperform(?:s|ed)?|surpass(?:es|ed)?|better\s+than)\b|相比|优于|而对照组|而基线",
                        st,
                        maxsplit=1,
                        flags=re.I,
                    )
                    if len(clauses) == 2:
                        ca = _extract_condition_context(clauses[0])
                        cb = _extract_condition_context(clauses[1])
                        if _compare_conditions(ca, cb) == "distinct_conditions" and QUANT_CLAIM_RE.search(st):
                            add_card(
                                dimension="D3_argument_evidence_chain",
                                severity="blocking",
                                action_verb="DEFER_TO_AUTHOR",
                                section=sec["title"],
                                paragraph_id=p["paragraph_id"],
                                line_range=p["line_range"],
                                quote_excerpt=st,
                                diagnosis="Treatment vs control/baseline comparison asserts superiority across mismatched experimental conditions.",
                                evidence_basis=f"Condition mismatch between compared arms: LHS={ca['labels']} vs RHS={cb['labels']}.",
                                executable_instruction=f"DEFER_TO_AUTHOR at {p['paragraph_id']}: verify matched-condition baseline evaluation or narrow the comparative claim to acknowledge condition asymmetry.",
                                author_decision_required=True,
                            )
                            break

    # =========================================================================
    # Dimension 4: Paragraph Relations & Cross-Section Redundancy
    # =========================================================================
    redundancy_pairs: list[dict[str, Any]] = []
    candidate_paras = [
        p for p in all_paras
        if p["role"] != "references" and p["char_len"] >= 70 and not p["text"].lstrip().startswith("|")
    ]
    para_tok_cache = [
        (p, set(_tokenize(p["text"], canonical=True)), _word_ngrams(_tokenize(p["text"], canonical=True), 3))
        for p in candidate_paras
    ]
    raw_red_hits: list[tuple[float, dict[str, Any], dict[str, Any], float, float, bool]] = []
    for i in range(len(para_tok_cache)):
        pa, ta, nga = para_tok_cache[i]
        if len(ta) < 6:
            continue
        for j in range(i + 1, len(para_tok_cache)):
            pb, tb, ngb = para_tok_cache[j]
            if len(tb) < 6:
                continue
            jacc = len(ta & tb) / max(1, len(ta | tb))
            ng_jacc = len(nga & ngb) / max(1, len(nga | ngb)) if (nga and ngb) else 0.0
            if jacc >= 0.48 or ng_jacc >= 0.30:
                is_legit_summary = (
                    (pa["role"] == "abstract" and pb["role"] in ("results", "conclusion", "introduction"))
                    or (pb["role"] == "abstract" and pa["role"] in ("results", "conclusion", "introduction"))
                ) and jacc < 0.80
                raw_red_hits.append((max(jacc, ng_jacc), pa, pb, jacc, ng_jacc, is_legit_summary))

    raw_red_hits.sort(key=lambda x: x[0], reverse=True)
    emitted_red_cards = 0
    for _, pa, pb, jacc, ng_jacc, is_legit_summary in raw_red_hits:
        pair_info = {
            "para_a": f"{pa['section']} ({pa['paragraph_id']}, {pa['line_range']})",
            "para_b": f"{pb['section']} ({pb['paragraph_id']}, {pb['line_range']})",
            "token_jaccard": round(jacc, 3),
            "trigram_jaccard": round(ng_jacc, 3),
            "legitimate_abstract_restatement": is_legit_summary,
        }
        redundancy_pairs.append(pair_info)
        if not is_legit_summary and emitted_red_cards < 8:
            emitted_red_cards += 1
            sev = "major" if jacc >= 0.60 or ng_jacc >= 0.40 else "minor"
            add_card(
                dimension="D4_paragraph_relations_redundancy",
                severity=sev,
                action_verb="MERGE",
                section=pb["section"],
                paragraph_id=f"{pa['paragraph_id']} <-> {pb['paragraph_id']}",
                line_range=f"{pa['line_range']} & {pb['line_range']}",
                quote_excerpt=pb["text"],
                diagnosis=(
                    f"Cross-paragraph/cross-section near-duplicate content between {pa['section']} ({pa['paragraph_id']}) "
                    f"and {pb['section']} ({pb['paragraph_id']}) (canonical Jaccard={jacc:.2f}, 3-gram Jaccard={ng_jacc:.2f})."
                ),
                evidence_basis="Canonicalized lexical/3-gram overlap exceeds redundancy threshold and is not a legitimate Abstract restatement.",
                executable_instruction=(
                    f"MERGE or deduplicate: retain the primary quantitative/methodological statement in {pa['section']} ({pa['paragraph_id']}) "
                    f"and condense {pb['paragraph_id']} into a concise interpretive cross-reference."
                ),
                target_location=f"{pa['section']} ({pa['paragraph_id']})",
            )

    # Flag genuinely overloaded monolithic paragraphs (>= 1800 chars or >= 11 sentences)
    split_cards_emitted = 0
    for sec in sections:
        if sec["role"] in ("abstract", "references"):
            continue
        for p in sec["paragraphs"]:
            if p["text"].lstrip().startswith("|"):
                continue
            sents = _extract_sentences(p["text"])
            if (p["char_len"] >= 1800 or len(sents) >= 11) and split_cards_emitted < 5:
                split_cards_emitted += 1
                add_card(
                    dimension="D4_paragraph_relations_redundancy",
                    severity="minor",
                    action_verb="SPLIT",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis=f"Overloaded paragraph ({p['char_len']} chars, {len(sents)} sentences) bundles multiple argumentative units without structural separation.",
                    evidence_basis="Paragraph length/sentence-count exceeds single-claim readability threshold (>=1800 chars or >=11 sentences).",
                    executable_instruction=f"SPLIT {p['paragraph_id']} at the topic pivot so each paragraph carries one claim + evidence + transition.",
                )

    # =========================================================================
    # Dimension 5: Conclusion Scope & Epistemic Scale
    # =========================================================================
    full_methods_results = " ".join(p["text"] for s in (method_secs + result_secs) for p in s["paragraphs"])
    is_observational_study = bool(OBSERVATIONAL_DESIGN_RE.search(full_methods_results or raw_text))

    for sec in concl_secs + by_role.get("abstract", []) + result_secs:
        for p in sec["paragraphs"]:
            epi = wio.classify_epistemic_strength(p["text"])
            if epi["strong_causal_count"] > 0 and (is_observational_study or epi["hedged_count"] == 0):
                add_card(
                    dimension="D5_conclusion_boundary_epistemic_scale",
                    severity="blocking",
                    action_verb="NARROW_CLAIM",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis=(
                        "Causal overclaim: strong causal verb ('proves/causes/drives/直接导致/证明了因果') used "
                        + ("despite observational/correlational/non-interventional design cues in manuscript." if is_observational_study else "without explicit experimental boundary or epistemic hedge.")
                    ),
                    evidence_basis=f"Epistemic classifier={epi['level']} (strong_causal={epi['strong_causal_count']}, hedged={epi['hedged_count']}, observational_design={is_observational_study}).",
                    executable_instruction=(
                        f"NARROW_CLAIM in {p['paragraph_id']}: replace causal verb with associative/conditional wording "
                        "('is associated with', 'suggests under tested conditions') and bind to the actual study cohort/model."
                    ),
                    author_decision_required=True,
                )
            if UNIVERSAL_EXTRAPOLATION_RE.search(p["text"]):
                add_card(
                    dimension="D5_conclusion_boundary_epistemic_scale",
                    severity="blocking",
                    action_verb="NARROW_CLAIM",
                    section=sec["title"],
                    paragraph_id=p["paragraph_id"],
                    line_range=p["line_range"],
                    quote_excerpt=p["text"],
                    diagnosis="Out-of-sample universal extrapolation extends findings beyond the tested cohort, dataset, or experimental domain.",
                    evidence_basis="Matched universal generalization phrase ('universally/all patients/适用于所有/彻底解决').",
                    executable_instruction=f"NARROW_CLAIM in {p['paragraph_id']}: explicitly qualify scope to the evaluated dataset, species/cohort, and experimental parameters.",
                    author_decision_required=True,
                )

    # Check missing Limitations in Discussion/Conclusion
    concl_blob = " ".join(p["text"] for s in concl_secs for p in s["paragraphs"])
    if concl_secs and not LIMITATION_SIGNAL_RE.search(concl_blob):
        c_last = concl_secs[-1]
        p_last = c_last["paragraphs"][-1] if c_last["paragraphs"] else {"paragraph_id": f"{c_last['section_id']}.P0", "line_range": f"L{c_last['start_line']}-L{c_last['end_line']}", "text": c_last["title"]}
        add_card(
            dimension="D5_conclusion_boundary_epistemic_scale",
            severity="major",
            action_verb="ADD_BRIDGE",
            section=c_last["title"],
            paragraph_id=p_last["paragraph_id"],
            line_range=p_last["line_range"],
            quote_excerpt=p_last["text"],
            diagnosis="Discussion/Conclusion contains no explicit scope limitations, failure modes, or boundary conditions.",
            evidence_basis="Zero limitation/caveat signals found across Discussion and Conclusion sections.",
            executable_instruction=f"Add a concise, concrete Limitations paragraph in {c_last['title']} specifying sample/dataset bounds, potential confounders, and conditions where conclusions do not hold.",
            author_decision_required=True,
        )

    # =========================================================================
    # Dimension 6: Structural Integrity, Truncation & Missing Elements
    # =========================================================================
    required_core_roles = ["introduction", "methods", "results", "conclusion"]
    missing_roles = [r for r in required_core_roles if r not in by_role and not (r == "conclusion" and "discussion" in by_role)]
    for mr in missing_roles:
        gaps.append({"code": f"MISSING_SECTION_{mr.upper()}", "severity": "major", "detail": f"Canonical section '{mr}' not found in manuscript headings."})
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="major",
            action_verb="DEFER_TO_AUTHOR",
            section="[MISSING]",
            paragraph_id="MISSING_SECTION",
            line_range="L0-L0",
            quote_excerpt=f"[MISSING: {mr}]",
            diagnosis=f"Canonical section role '{mr}' is absent from the supplied manuscript.",
            evidence_basis=f"Parsed {len(sections)} section heading(s); none mapped to role '{mr}'.",
            executable_instruction=f"Confirm whether '{mr}' is intentionally omitted by target venue/excerpt scope or supply the missing section before final structural sign-off.",
            author_decision_required=True,
        )

    # Check genuinely empty leaf canonical sections (container parents with non-empty subsections are NEVER empty)
    empty_leaf_sections: list[str] = []
    for s in sections:
        if (
            s["role"] in ("introduction", "methods", "results", "discussion", "conclusion")
            and not s["is_container_parent"]
            and s["paragraph_count"] == 0
        ):
            empty_leaf_sections.append(f"{s['section_id']}:{s['title']}")
            gaps.append({
                "code": f"EMPTY_LEAF_SECTION_{s['role'].upper()}",
                "severity": "major",
                "detail": f"Leaf section '{s['title']}' ({s['section_id']}) has 0 paragraphs and 0 child subsections.",
            })
            add_card(
                dimension="D6_structural_integrity_anomalies",
                severity="major",
                action_verb="DEFER_TO_AUTHOR",
                section=s["title"],
                paragraph_id=f"{s['section_id']}.P0",
                line_range=f"L{s['start_line']}-L{s['end_line']}",
                quote_excerpt=f"[EMPTY LEAF SECTION: {s['title']}]",
                diagnosis=f"Leaf canonical section '{s['title']}' contains neither direct text nor child subsections.",
                evidence_basis=f"Section tree verified direct_paragraphs=0 and children=0 for {s['section_id']}.",
                executable_instruction=f"Supply missing body content for '{s['title']}' or confirm intentional omission.",
                author_decision_required=True,
            )

    # Check inline citations vs References section
    invariants = wio.extract_invariants(raw_text)
    total_inline_cites = (
        len(invariants["bracket_citations"])
        + len(invariants["latex_citations"])
        + len(invariants["author_year_citations"])
    )
    if total_inline_cites > 0 and "references" not in by_role:
        gaps.append({"code": "MISSING_REFERENCES_SECTION", "severity": "major", "detail": f"Detected {total_inline_cites} inline citation marker(s) but no References/Bibliography section."})
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="major",
            action_verb="DEFER_TO_AUTHOR",
            section="[MISSING]",
            paragraph_id="MISSING_REFERENCES",
            line_range="L0-L0",
            quote_excerpt=f"[MISSING: References for {total_inline_cites} inline citations]",
            diagnosis=f"Manuscript contains {total_inline_cites} inline citation marker(s) but lacks a References/Bibliography section.",
            evidence_basis="Extracted inline citation markers via writing_io_and_invariants without a corresponding References heading.",
            executable_instruction="Attach the complete References section or bibliography file so citation coverage and claim-source grounding can be verified.",
            author_decision_required=True,
        )

    # Check headingless / disordered formatting & missing figure/table/equation definitions
    if len(sections) == 1 and sections[0]["title"] == "(Preamble)" and len(all_paras) >= 3:
        gaps.append({
            "code": "MISSING_SECTION_HEADINGS",
            "severity": "major",
            "detail": f"Manuscript contains {len(all_paras)} paragraphs with zero recognizable section headings.",
        })
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="major",
            action_verb="SPLIT",
            section="(Preamble)",
            paragraph_id=all_paras[0]["paragraph_id"],
            line_range=f"L1-L{len(lines)}",
            quote_excerpt=all_paras[0]["text"],
            diagnosis=f"Manuscript lacks explicit section headings across {len(all_paras)} paragraphs, obscuring IMRaD hierarchy.",
            evidence_basis="Heading parser detected 0 Markdown/LaTeX/numbered/Chinese/uppercase canonical section titles.",
            executable_instruction="Insert explicit IMRaD section headings (Introduction, Methods, Results, Discussion/Conclusion) at each argumentative phase boundary.",
            author_decision_required=True,
        )

    referenced_figs = {
        re.sub(r"\s+", " ", f).strip()
        for f in invariants.get("figure_table_refs", [])
        if re.match(r"^(?:Fig|Table|Eq|图|表)", f, re.I)
    }
    has_fig_caption = bool(re.search(r"(?:^|\n)\s*(?:\*{0,2}(?:Figure|Fig\.|Table|图|表)\s*S?\d+\s*[:.：]|!\[|\|[^|\n]+\|)", raw_text, re.I))
    has_eq_block = bool(re.search(r"\$\$|\\begin\{(?:equation|align)\}|(?:^|\n)\s*(?:Eq\.?|Equation)\s*\(?\d+\)?\s*[:=]", raw_text, re.I))
    if referenced_figs and not has_fig_caption and any(re.match(r"^(?:Fig|Table|图|表)", f, re.I) for f in referenced_figs):
        gaps.append({
            "code": "MISSING_FIGURE_OR_TABLE_CAPTIONS",
            "severity": "minor",
            "detail": f"Body references {sorted(referenced_figs)[:6]} but no embedded Figure/Table captions or Markdown tables were detected in text.",
        })
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="minor",
            action_verb="DEFER_TO_AUTHOR",
            section="[MISSING_MEDIA]",
            paragraph_id="MISSING_FIG_TABLE",
            line_range="L0-L0",
            quote_excerpt=f"Referenced visual anchors without captions: {', '.join(sorted(referenced_figs)[:6])}",
            diagnosis=f"Manuscript references {len(referenced_figs)} figure/table anchor(s) without corresponding embedded captions or table blocks.",
            evidence_basis="Cross-check of inline Fig/Table references against caption/table definitions in received text.",
            executable_instruction="Confirm whether figures/tables are supplied in separate files; attach captions/values before final claim-evidence alignment.",
            author_decision_required=True,
        )
    if any(re.match(r"^(?:Eq|Equation)", f, re.I) for f in referenced_figs) and not has_eq_block:
        gaps.append({
            "code": "MISSING_EQUATION_DEFINITIONS",
            "severity": "minor",
            "detail": "Body references numbered equations but no equation blocks were found in text.",
        })

    # Check truncation / abrupt ending
    stripped_tail = raw_text.rstrip()
    truncated = False
    trunc_reasons: list[str] = []
    if stripped_tail:
        last_line = lines[-1].strip() if lines else ""
        if last_line and not re.search(r"[.!?。！？\"'”’\)\]\}]|^\s*\|", last_line) and not MD_HEADING_RE.match(last_line):
            truncated = True
            trunc_reasons.append(f"Final line ends abruptly without terminal punctuation: '{last_line[-60:]}'")
        if raw_text.count("(") - raw_text.count(")") >= 2 or raw_text.count("[") - raw_text.count("]") >= 2:
            truncated = True
            trunc_reasons.append("Unbalanced parentheses/brackets indicate possible mid-text truncation.")
    if truncated:
        last_sec = sections[-1]["title"] if sections else "(Preamble)"
        last_pid = sections[-1]["paragraphs"][-1]["paragraph_id"] if sections and sections[-1]["paragraphs"] else "S0.P0"
        gaps.append({"code": "POSSIBLE_TRUNCATION", "severity": "blocking", "detail": "; ".join(trunc_reasons)})
        add_card(
            dimension="D6_structural_integrity_anomalies",
            severity="blocking",
            action_verb="DEFER_TO_AUTHOR",
            section=last_sec,
            paragraph_id=last_pid,
            line_range=f"L{len(lines)}-L{len(lines)}",
            quote_excerpt=stripped_tail[-120:],
            diagnosis="Manuscript appears truncated or cut off before completion.",
            evidence_basis="; ".join(trunc_reasons),
            executable_instruction="Provide the un-truncated manuscript tail before applying final structural edits; current diagnosis covers only the received lines.",
            author_decision_required=True,
        )

    # =========================================================================
    # Bilingual Academic De-AI & Press-Release Prose Audit (advisory, non-style-bias)
    # =========================================================================
    prose_audit = wio.audit_academic_prose(raw_text)
    for pf in prose_audit["findings"]:
        if pf["category"] == "defensive_self_undermining":
            # Map line to paragraph
            matched_p = next((p for p in all_paras if p["start_line"] <= pf["line"] <= p["end_line"]), None)
            sec_t = matched_p["section"] if matched_p else "(Body)"
            pid_t = matched_p["paragraph_id"] if matched_p else f"L{pf['line']}"
            lr_t = matched_p["line_range"] if matched_p else f"L{pf['line']}-L{pf['line']}"
            add_card(
                dimension="D5_conclusion_boundary_epistemic_scale",
                severity="minor",
                action_verb="MOVE",
                section=sec_t,
                paragraph_id=pid_t,
                line_range=lr_t,
                quote_excerpt=matched_p["text"] if matched_p else pf["matched"],
                diagnosis=f"Defensive self-undermining phrasing ('{pf['matched']}') weakens main argument instead of stating objective scope boundaries.",
                evidence_basis="Press-release narrative principle: move genuine scope caveats to Limitations; avoid apologetic framing in primary claims.",
                executable_instruction=f"Remove apologetic qualifier '{pf['matched']}' from {pid_t}; if it reflects a real scientific constraint, relocate it as an explicit boundary condition in Limitations.",
            )

    # Verify anti-vague-advice firewall
    vague_leaks = [
        c["card_id"]
        for c in cards
        if any(re.search(vp, c["executable_instruction"], re.I) for vp in VAGUE_ADVICE_PATTERNS)
    ]

    dim_counts = {
        dim: sum(1 for c in cards if c["dimension"] == dim)
        for dim in (
            "D1_rq_contribution_alignment",
            "D2_section_structure_narrative_order",
            "D3_argument_evidence_chain",
            "D4_paragraph_relations_redundancy",
            "D5_conclusion_boundary_epistemic_scale",
            "D6_structural_integrity_anomalies",
        )
    }

    diagnosis_payload = {
        "case_id": case_id,
        "task_id": task_id,
        "manuscript": manuscript_path.name,
        "read_method": read_meta.get("method", "plain_text"),
        "degraded_mode": bool(read_meta.get("degraded_mode", False)),
        "task_constraints_applied": (case_contract or {}).get("task_constraints", {}),
        "line_count": len(lines),
        "section_count": len(sections),
        "paragraph_count": len(all_paras),
        "sections_overview": [
            {
                "section_id": s["section_id"],
                "title": s["title"],
                "level": s["level"],
                "role": s["role"],
                "parent_section_id": s["parent_section_id"],
                "children_section_ids": s["children_section_ids"],
                "is_container_parent": s["is_container_parent"],
                "lines": f"L{s['start_line']}-L{s['end_line']}",
                "direct_paragraphs": s["direct_paragraph_count"],
                "paragraphs": s["paragraph_count"],
                "chars": s["char_len"],
                "has_content": s["has_content"],
            }
            for s in sections
        ],
        "empty_leaf_sections": empty_leaf_sections,
        "six_dimension_counts": dim_counts,
        "rq_and_contribution_trace": rq_alignment_trace,
        "chronological_laundry_lists": chronological_clusters,
        "same_metric_conflicts": same_metric_conflicts,
        "same_metric_comparison_audit": same_metric_comparison_audit,
        "redundancy_pairs": redundancy_pairs,
        "prose_audit_summary": {
            "clean": prose_audit["clean"],
            "high_count": prose_audit["high_count"],
            "warning_count": prose_audit["warning_count"],
            "info_count": prose_audit["info_count"],
        },
        "edit_action_cards": cards,
        "firewall_verification": {
            "all_cards_anchored": all(bool(c["anchor"]["paragraph_id"]) and bool(c["anchor"]["line_range"]) for c in cards),
            "all_verbs_in_closed_set": all(c["action_verb"] in ALLOWED_ACTION_VERBS for c in cards),
            "vague_advice_leaks": vague_leaks,
            "acl_h1_h17_anti_style_bias": "enforced",
            "unsolicited_full_rewrite": False,
        },
    }

    return {
        "diagnosis": diagnosis_payload,
        "invariants": invariants,
        "evidence_map": evidence_map,
        "gaps": gaps,
        "prose_audit": prose_audit,
    }


def render_editing_action_plan_md(diag: dict[str, Any]) -> str:
    """Render an actionable, author-ready Markdown editing plan with exact anchors."""
    lines = [
        f"# Structural and Logical Editing Action Plan ({diag['case_id']})",
        "",
        f"- **Source Manuscript**: `{diag['manuscript']}` ({diag['line_count']} lines, {diag['section_count']} sections, {diag['paragraph_count']} paragraphs)",
        f"- **Review Scope**: Six-dimension source-grounded structural diagnosis (editable plan only; no unsolicited full rewrite; author retains all scientific stance decisions).",
        f"- **Total Action Cards**: {len(diag['edit_action_cards'])} "
        f"(Blocking: {sum(1 for c in diag['edit_action_cards'] if c['severity'] == 'blocking')}, "
        f"Major: {sum(1 for c in diag['edit_action_cards'] if c['severity'] == 'major')}, "
        f"Minor: {sum(1 for c in diag['edit_action_cards'] if c['severity'] == 'minor')})",
        "",
        "## 1. Six-Dimension Coverage Summary",
        "",
        "| Dimension | Findings | Key Mechanism Checked |",
        "| --- | ---: | --- |",
        f"| D1. RQ & Contribution Alignment | {diag['six_dimension_counts']['D1_rq_contribution_alignment']} | Cross-chapter closure from Intro RQ/contributions to Methods/Results/Conclusion |",
        f"| D2. Section Structure & Narrative Order | {diag['six_dimension_counts']['D2_section_structure_narrative_order']} | Chronological literature laundry lists, IMRaD ordering, misplaced content, press-release flow |",
        f"| D3. Argument Chain (Claim -> Evidence -> Warrant) | {diag['six_dimension_counts']['D3_argument_evidence_chain']} | Same-metric contradictions, unsupported numerical claims, missing inferential bridges |",
        f"| D4. Paragraph Relations & Redundancy | {diag['six_dimension_counts']['D4_paragraph_relations_redundancy']} | Cross-section duplication (excluding legitimate Abstract restatement), overloaded paragraphs |",
        f"| D5. Conclusion Scope & Epistemic Scale | {diag['six_dimension_counts']['D5_conclusion_boundary_epistemic_scale']} | Observational-to-causal escalation, universal extrapolation, missing limitations |",
        f"| D6. Structural Integrity & Anomalies | {diag['six_dimension_counts']['D6_structural_integrity_anomalies']} | Missing canonical sections, missing References, truncation/cutoff |",
        "",
        "## 2. Executable Editing Action Cards",
        "",
    ]

    sev_rank = {"blocking": 0, "major": 1, "minor": 2}
    sorted_cards = sorted(diag["edit_action_cards"], key=lambda c: (sev_rank.get(c["severity"], 3), c["card_id"]))
    if not sorted_cards:
        lines.append("No structural defects triggered automated rules; perform manual verification of argument transitions against domain evidence.")
    else:
        for c in sorted_cards:
            anc = c["anchor"]
            lines.extend([
                f"### [{c['severity'].upper()}] {c['card_id']} — `{c['action_verb']}` ({c['dimension']})",
                f"- **Location**: Section `{anc['section']}` | Paragraph `{anc['paragraph_id']}` | Lines `{anc['line_range']}`",
                f"- **Quote Excerpt**: \"{anc['quote_excerpt']}\"",
                f"- **Diagnosis**: {c['diagnosis']}",
                f"- **Evidence Basis**: {c['evidence_basis']}",
                f"- **Executable Edit Action**: {c['executable_instruction']}",
                f"- **Target Location**: {c['target_location']}",
                f"- **Author Decision**: `{'REQUIRED (do not override author stance)' if c['author_decision_required'] else 'Optional confirmation'}` — Options: `Accept` / `Partially Accept` / `Reject`",
                "",
            ])

    if diag.get("task_constraints_applied"):
        tc_str = ", ".join(f"{k}={v}" for k, v in diag["task_constraints_applied"].items())
        lines.insert(4, f"- **Task Constraints Consumed (`/input/task.md` & `case.json`)**: {tc_str}")

    if diag["same_metric_conflicts"]:
        lines.extend([
            "## 3. Clustered Same-Metric Contradictions & Condition Comparison",
            "",
            "| Cluster | Metric | Conflict Type | Condition Comparability | Entries (Paragraph, Value, Conditions) | Distinct-Cond Filtered |",
            "| --- | --- | --- | --- | --- | ---: |",
        ])
        for cf in diag["same_metric_conflicts"]:
            entry_str = " ; ".join(
                f"{e['paragraph_id']} ({e['line_range']}): {e['value']}{e['unit']} [{e['direction']}, cond={','.join(e['condition_labels']) or 'unspecified'}]"
                for e in cf.get("entries", [cf["entry_a"], cf["entry_b"]])
            )
            lines.append(
                f"| {cf.get('cluster_id', 'MCLUSTER')} | {cf['metric']} | {cf['conflict_type']} | "
                f"{cf.get('condition_comparability', 'unspecified_context')} | {entry_str} | "
                f"{cf.get('distinct_condition_pairs_filtered', 0)} |"
            )
        lines.append("")

    if diag["chronological_laundry_lists"]:
        lines.extend([
            "## 4. Chronological Literature Laundry Lists Detected",
            "",
        ])
        for cl in diag["chronological_laundry_lists"]:
            lines.append(
                f"- Section `{cl['section']}` ({cl['scope']}): paragraphs `{', '.join(cl['paragraph_ids'])}` ordered strictly by year `{cl['years']}` without comparative synthesis -> apply `REGROUP_BY_DIMENSION`."
            )
        lines.append("")

    lines.extend([
        "## 5. Preservation & Fairness Boundaries",
        "- All numeric results, p-values, sample sizes, citations, and domain facts from the original manuscript remain untouched.",
        "- No stylistic preferences (vocabulary taste, passive vs active voice) were flagged as structural defects (ACL H1-H17 firewall).",
        "- Full unsolicited manuscript rewrite was intentionally omitted per Concrete Structural Review Scope.",
        "",
    ])
    return "\n".join(lines)


def _discover_manuscript(input_dir: Path) -> tuple[Path, str]:
    case_id = "case-01"
    declared_file: Path | None = None
    case_json = input_dir / "case.json"
    if case_json.is_file():
        try:
            cobj = json.loads(case_json.read_text(encoding="utf-8"))
            case_id = str(cobj.get("case_id") or case_id)
            for key in ("manuscript", "primary_manuscript", "manuscript_file", "draft", "input_file"):
                val = cobj.get(key)
                if isinstance(val, str) and val.strip():
                    cand = input_dir / val.strip()
                    if cand.is_file():
                        declared_file = cand
                        break
            if declared_file is None and isinstance(cobj.get("files"), list):
                for item in cobj["files"]:
                    fn = item.get("path") if isinstance(item, dict) else (item if isinstance(item, str) else "")
                    if fn and (input_dir / fn).is_file():
                        declared_file = input_dir / fn
                        break
        except Exception:
            pass
    if declared_file is not None:
        return declared_file, case_id
    exts = (".md", ".tex", ".docx", ".txt", ".pdf", ".xml", ".html", ".htm")
    candidates = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in exts
        and p.name.lower() not in {"task.md", "case.json", "readme.md"}
    ]
    if not candidates:
        raise FileNotFoundError(f"No manuscript file found under {input_dir}")
    # Prefer files explicitly named manuscript/paper/draft over supplements, then largest size
    candidates.sort(
        key=lambda p: (
            0 if any(k in p.name.lower() for k in ("manuscript", "paper", "draft", "article")) else (2 if "supp" in p.name.lower() else 1),
            -p.stat().st_size,
        )
    )
    return candidates[0], case_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Six-dimension source-grounded structural logic diagnosis engine.")
    parser.add_argument("--manuscript", default="", help="Path to manuscript (.md/.tex/.txt/.docx/.pdf/.xml/.html).")
    parser.add_argument("--input-dir", default="", help="Alternative input directory containing case.json, task.md, and manuscript.")
    parser.add_argument("--out-dir", required=True, help="Output directory for structural_diagnosis.json, editing_action_plan.md, and result.json.")
    parser.add_argument("--case-id", default="", help="Override case_id.")
    args = parser.parse_args(argv)

    input_dir_path: Path | None = Path(args.input_dir) if args.input_dir else None
    if args.manuscript:
        ms_path = Path(args.manuscript)
        if input_dir_path is None:
            input_dir_path = ms_path.parent
        case_contract = wio.load_case_contract(input_dir_path)
        case_id = args.case_id or case_contract.get("case_id") or ms_path.stem
    elif input_dir_path:
        ms_path, detected_cid = _discover_manuscript(input_dir_path)
        case_contract = wio.load_case_contract(input_dir_path)
        case_id = args.case_id or case_contract.get("case_id") or detected_cid
    else:
        parser.error("Provide either --manuscript or --input-dir.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    res = diagnose_manuscript(ms_path, case_id=case_id, case_contract=case_contract)
    diag = res["diagnosis"]
    plan_md = render_editing_action_plan_md(diag)

    diag_path = out_dir / "structural_diagnosis.json"
    plan_path = out_dir / "editing_action_plan.md"
    result_path = out_dir / "result.json"

    diag_path.write_text(json.dumps(diag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path.write_text(plan_md, encoding="utf-8")

    change_log = [
        {
            "card_id": c["card_id"],
            "action_verb": c["action_verb"],
            "dimension": c["dimension"],
            "location": f"{c['anchor']['section']} {c['anchor']['paragraph_id']} ({c['anchor']['line_range']})",
            "status": "proposed_for_author_review",
        }
        for c in diag["edit_action_cards"]
    ]
    limitations = [
        "Automated structural diagnostics rely on explicit textual markers, section headers, and condition-bound metric patterns; nuanced domain-specific logical warrants require human scholar confirmation.",
        "No unsolicited full manuscript rewrite is produced; author retains authority to Accept, Partially Accept, or Reject each action card.",
    ]
    if diag.get("degraded_mode"):
        limitations.append(f"Document text was extracted via degraded fallback ({diag.get('read_method')}); inspect PDF layout and formula spans manually.")
    result_payload = wio.build_writing_result(
        case_id=case_id,
        task_id="paper-structure-optimization",
        deliverable={
            "type": "six_dimension_structural_diagnosis_and_editing_action_plan",
            "diagnosis_json": "structural_diagnosis.json",
            "editing_plan_md": "editing_action_plan.md",
            "total_action_cards": len(diag["edit_action_cards"]),
            "six_dimension_counts": diag["six_dimension_counts"],
        },
        preserved_facts={
            "source_manuscript": ms_path.name,
            "numbers_with_units_count": len(res["invariants"]["numbers_with_units"]),
            "pvalues_count": len(res["invariants"]["pvalues"]),
            "sample_sizes": res["invariants"]["sample_sizes"],
            "dois": res["invariants"]["dois"],
            "figure_table_refs": res["invariants"]["figure_table_refs"],
            "manuscript_mutated": False,
        },
        evidence_map=res["evidence_map"],
        change_log=change_log,
        artifacts=["result.json", "structural_diagnosis.json", "editing_action_plan.md"],
        sources=[{"file": ms_path.name, "role": "primary_manuscript", "lines": diag["line_count"], "read_method": diag.get("read_method")}],
        gaps=res["gaps"],
        limitations=limitations,
        verification={
            "engine": "structural_logic_engine.py",
            "firewall": diag["firewall_verification"],
            "blocking_cards": sum(1 for c in diag["edit_action_cards"] if c["severity"] == "blocking"),
            "chronological_laundry_lists_found": len(diag["chronological_laundry_lists"]),
            "same_metric_conflict_clusters_found": len(diag["same_metric_conflicts"]),
            "cross_section_redundancy_pairs": sum(1 for r in diag["redundancy_pairs"] if not r["legitimate_abstract_restatement"]),
            "task_constraints_consumed": bool(case_contract.get("task_constraints")),
        },
        case_contract=case_contract,
    )
    result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "total_action_cards": len(diag["edit_action_cards"]),
        "six_dimension_counts": diag["six_dimension_counts"],
        "chronological_laundry_lists": len(diag["chronological_laundry_lists"]),
        "same_metric_conflicts": len(diag["same_metric_conflicts"]),
        "gaps_count": len(res["gaps"]),
        "artifacts": ["result.json", "structural_diagnosis.json", "editing_action_plan.md"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
