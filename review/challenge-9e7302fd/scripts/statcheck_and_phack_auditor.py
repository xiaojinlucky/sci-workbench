#!/usr/bin/env python3
"""Zero-dependency offline StatCheck, Effect-Size/CI, GRIM/Conservation, Pseudoreplication, CSV, and 5-Facet Methods Auditor.

Designed for challenge-9e7302fd in an offline Python 3.12 container (python:3.12-slim --network none).
Key guarantees:
1. Never crashes without scipy/numpy: uses stat_math_core.py (Modified Lentz Beta/Gamma < 1e-9 accuracy),
   preferring scipy.stats when available and logging backend cross-check precision.
2. Paragraph-level PDF hard-wrap merging (preserving line_start..line_end) + multi-journal statistical formats
   (APA, NEJM/JAMA/Lancet comma CI, leading-dot P = .018, and non-APA t = ..., df = ... / F = ..., df = ...).
3. Stat-rounding-aware inequality bounds: distinguishes definitive decision_error (p_lo >= p_rep) from
   rounding_ambiguous (p_lo < p_rep <= p_calc -> minor | needs_human_review). Flags invalid 'p = 0.000'.
4. Effect-size & 95% CI vs p-value internal consistency (OR/HR/RR vs null=1.0; beta/MD/d vs null=0.0),
   with anti-false-positive protection for multiplicity-adjusted p-values.
5. GRIM (discrete scales only, including natural prose), integer percentage k/n verification, mutually-exclusive
   group-N conservation, CNS-level pseudoreplication screening, basic CSV table reconciliation, and direct result.json export.
6. Five-facet methodology coverage strictly separating decision_error, method_flaw, and under_reported.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_io_core as io_core  # noqa: E402
import stat_math_core as smc  # noqa: E402
import phack_detect  # noqa: E402

# ---------------------------------------------------------------------------
# Regex patterns for statistical tests, effect sizes/CIs, proportions, & facets
# ---------------------------------------------------------------------------

P_NUM_PAT = r"(0?\.\d+(?:\s*(?:e|×\s*10\^?)\s*[+-]?\d+)?)"

T_TEST_RE = re.compile(
    rf"\bt\s*\(\s*(\d+(?:\.\d+)?)\s*\)\s*=\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
T_TEST_DF_RE = re.compile(
    rf"\bt\s*=\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*d\.?f\.?\s*=\s*(\d+(?:\.\d+)?)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
F_TEST_RE = re.compile(
    rf"\bF\s*\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)\s*=\s*(\d+(?:\.\d+)?)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
F_TEST_DF_RE = re.compile(
    rf"\bF\s*=\s*(\d+(?:\.\d+)?)\s*[,;]\s*d\.?f\.?\s*=\s*\(?\s*(\d+(?:\.\d+)?)\s*[,/]\s*(\d+(?:\.\d+)?)\s*\)?\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
CHI2_TEST_RE = re.compile(
    rf"(?:\bchi2\b|\bchi-square\b|χ²|χ2|\\chi\^2)\s*\(\s*(\d+(?:\.\d+)?)(?:\s*,\s*[Nn]\s*=\s*\d+)?\s*\)\s*=\s*(\d+(?:\.\d+)?)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
CHI2_TEST_DF_RE = re.compile(
    rf"(?:\bchi2\b|\bchi-square\b|χ²|χ2|\\chi\^2)\s*=\s*(\d+(?:\.\d+)?)\s*[,;]\s*d\.?f\.?\s*=\s*(\d+(?:\.\d+)?)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
R_TEST_RE = re.compile(
    rf"\br\s*\(\s*(\d+)\s*\)\s*=\s*(-?0?\.\d+)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)
Z_TEST_RE = re.compile(
    rf"\bz\s*=\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*p\s*([<=>≤≥]=?)\s*{P_NUM_PAT}",
    re.I,
)

BARE_P_EQ_RE = re.compile(r"\bp\s*=\s*(0?\.\d+(?:e[+-]?\d+)?)", re.I)
P_ZERO_RE = re.compile(r"\bp\s*=\s*0?\.0{2,6}\b", re.I)
ONE_SIDED_CTX_RE = re.compile(r"\b(?:one[- ]sided|one[- ]tailed|directional\s+test)\b|单尾|单侧", re.I)

# Effect size + 95% CI + optional nearby p-value (supports APA, NEJM/JAMA/Lancet comma style, and leading-dot P = .018)
EFFECT_CI_RE = re.compile(
    r"\b(?P<kind>a?OR|a?HR|a?RR|IRR|odds\s+ratio|hazard\s+ratio|risk\s+ratio|beta|β|MD|SMD|mean\s+difference|Cohen(?:'s)?\s*d|Hedges(?:'s)?\s*g)\s*"
    r"(?:=|:|,|\bof\b)?\s*(?P<est>-?\d+(?:\.\d+)?)\s*"
    r"[\(\[,;]?\s*(?:95%\s*CI\s*[:=,]?\s*)"
    r"[\[\(]?\s*(?P<lo>-?\d+(?:\.\d+)?)\s*(?:,|to|–|—|-)\s*(?P<hi>-?\d+(?:\.\d+)?)\s*[\]\)]?"
    r"(?:(?:(?!95%\s*CI)[^.\n]){0,45}?\bp\s*(?P<pop>[<=>≤≥]=?)\s*(?P<pval>0?\.\d+(?:e[+-]?\d+)?))?",
    re.I,
)
ADJ_P_CTX_RE = re.compile(r"\b(?:Bonferroni|Holm|FDR|Benjamini|Tukey|Sidak|adjusted\s+p|p_adj|q\s*[<=]|multiplicity)\b|校正", re.I)

# Integer proportion k/n (pct%)
PROP_PCT_RE = re.compile(r"\b(\d{1,5})\s*/\s*(\d{1,5})\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")

# GRIM discrete-scale mean check (strictly gated to discrete/Likert/rating/survey scores, never continuous metrics like BP/height)
DISCRETE_SCALE_CUE_RE = re.compile(
    r"\b(?:Likert(?:\s+(?:scale|score|rating))?|integer\s+scale|point\s+scale|\d+-point\s+scale|"
    r"ordinal\s+score|survey\s+score|count\s+score|rating\s+score|questionnaire\s+score|"
    r"(?:mean|average)\s+rating\b[^.;\n]{0,35}\b(?:respondents?|participants?|raters?))\b|"
    r"李克特|整数评分|量表得分|问卷评分",
    re.I,
)
MEAN_N_RE = re.compile(
    r"(?:"
    r"\b[Nn]\s*=\s*(?P<n1>\d{1,2})\b[^.;\n]{0,30}?\b(?:M|mean)\s*(?:=|:|\bwas\b|\bis\b)\s*(?P<m1>\d+\.\d{1,3})\b"
    r"|\b(?:M|mean)\s*(?:=|:|\bwas\b|\bis\b)\s*(?P<m2>\d+\.\d{1,3})\b[^.;\n]{0,30}?\b[Nn]\s*=\s*(?P<n2>\d{1,2})\b"
    r"|\b(?:mean|average)\s+(?:[A-Za-z0-9\-]+\s+){0,3}(?:in|among|of|across|for)\s+(?:[Nn]\s*=\s*)?(?P<n3>\d{1,2})\s+"
    r"(?:participants?|respondents?|subjects?|patients?|raters?|students?|individuals?)[^.;\n]{0,25}?(?:was|is|=|:|of)\s*(?P<m3>\d+\.\d{1,3})\b"
    r"|\b(?:in|among|across)\s+(?:[Nn]\s*=\s*)?(?P<n4>\d{1,2})\s+(?:participants?|respondents?|subjects?|patients?|raters?)"
    r"[^.;\n]{0,40}?\b(?:mean|average)\b[^.;\n]{0,25}?(?:was|is|=|:|of)\s*(?P<m4>\d+\.\d{1,3})\b"
    r")",
    re.I,
)

# Group conservation in a sentence: total N=... explicitly partitioned into mutually exclusive groups/arms
TOTAL_N_SENT_RE = re.compile(
    r"(?:\b(?:total(?:\s+of)?|enrolled|recruited|randomized|randomised|included|sample\s+of)\s+(?:N\s*=\s*)?(\d{2,6})|\bN\s*=\s*(\d{2,6}))",
    re.I,
)
GROUP_N_RE = re.compile(r"\bn\s*=\s*(\d{1,6})\b")
EXCLUSIVE_ALLOCATION_RE = re.compile(
    r"\b(?:divided|allocated|randomized|randomised|assigned|split|stratified|partitioned|categorized|grouped)\s+into\b|"
    r"\b(?:arms?|groups?)\s*[:(]|"
    r"\b(?:intervention|treatment|experimental|active|exposed|case)\b[^.;\n]{0,35}\b(?:control|placebo|sham|unexposed|reference)\b|"
    r"分为.{0,10}组|干预组.{0,20}对照组|实验组.{0,20}对照组",
    re.I,
)
NON_EXCLUSIVE_SUBGROUP_RE = re.compile(
    r"\b(?:including|such\s+as|among\s+whom|of\s+whom|comorbidit(?:y|ies)|overlap(?:ping)?|subgroups?\s+included|with\s+history\s+of)\b|"
    r"其中包括|包括.{0,20}(?:和|与)",
    re.I,
)
ATTRITION_CUE_RE = re.compile(
    r"\b(?:exclud(?:ed|ing)|drop(?:ped)?|lost\s+to\s+follow[- ]up|withdr(?:ew|awn)|missing|attrition|ineligible|remaining|final\s+analysis)\b|"
    r"剔除|排除|失访|脱落|缺失",
    re.I,
)
FUTURE_OR_PRIOR_DESIGN_RE = re.compile(
    r"\b(?:future\s+(?:studies|trials|rcts?|randomized|randomised|research|work)|further\s+(?:rcts?|randomized|randomised|trials)|"
    r"limitation(?:s)?|cannot\s+rule\s+out|need\s+for\s+(?:a\s+)?(?:randomized|randomised|rct)|are\s+needed|is\s+needed|warranted|"
    r"should\s+be\s+confirmed|previous\s+(?:studies|trials|rcts?)|prior\s+(?:studies|trials|rcts?)|earlier\s+(?:trials|rcts?))\b|"
    r"未来需要|未来研究|局限性|有待.{0,12}随机对照|前人研究|既往研究",
    re.I,
)

# Pseudoreplication / experimental unit cues
ANIMAL_SUBJECT_N_RE = re.compile(
    r"\b(?:N|n)\s*=\s*(\d{1,3})\s*(?:mice|mouse|rats?|animals?|rabbits?|monkeys?|primates?|patients?|subjects?|donors?|litters?|cages?)\b|"
    r"(\d{1,3})\s*(?:mice|rats|animals|patients|donors|只小鼠|只大鼠|例患者|只动物)(?:\s+per\s+group)?",
    re.I,
)
SUBUNIT_CUE_RE = re.compile(
    r"\b(?:cells?|neurons?|slices?|sections?|fields?\s+of\s+view|ROIs?|spines?|synapses?|wells?|technical\s+replicates?|images?|puncta)\b|"
    r"细胞|神经元|切片|视野|技术重复",
    re.I,
)
REPEATED_MIXED_RE = re.compile(
    r"\b(?:linear\s+mixed(?:-effects)?|LMM|GLMM|nested\s+(?:model|ANOVA)|random\s+(?:effect|intercept)|"
    r"GEE|generalized\s+estimating|cluster[- ]robust|clustered\s+by|animal\s+as\s+(?:the\s+)?(?:statistical|experimental)\s+unit|"
    r"averaged\s+per\s+(?:mouse|animal|patient)|repeated\s+measures)\b|混合效应|嵌套模型|随机效应|以动物为统计单位",
    re.I,
)

# Five-facet reporting detection patterns
VAGUE_SIG_RE = re.compile(
    r"\b(?:marginally\s+significant|trending\s+toward\s+significance|approaching\s+significance|"
    r"borderline\s+significant|near[- ]significant|trend\s+for\s+significance)\b|边缘显著|趋近显著|接近显著",
    re.I,
)
STUDY_DESIGN_RE = re.compile(
    r"\b(?:randomized|RCT|cohort|case[- ]control|cross[- ]sectional|observational|retrospective|prospective|"
    r"double[- ]blind|single[- ]blind|open[- ]label|control\s+group|placebo|primary\s+endpoint|primary\s+outcome)\b|"
    r"随机对照|队列研究|病例对照|横断面|回顾性|前瞻性|双盲|对照组|主要终点",
    re.I,
)
OBSERVATIONAL_DESIGN_RE = re.compile(
    r"\b(?:observational|cross[- ]sectional|retrospective\s+cohort|case[- ]control|correlational\s+study)\b|观察性|横断面|回顾性",
    re.I,
)
CAUSAL_CLAIM_RE = re.compile(
    r"\b(?:proves?\s+that|causes?\s+|directly\s+causes?|causally\s+drives?|establishes?\s+causality|demonstrates?\s+a\s+causal\s+effect)\b|"
    r"证明了因果|直接导致|证实引起",
    re.I,
)
VARIABLE_DEF_RE = re.compile(
    r"\b(?:primary\s+outcome|dependent\s+variable|exposure|covariate|confounder|operational(?:ly)?\s+defined|"
    r"cut[- ]?off|threshold|missing\s+data|imputation|complete[- ]case|coded\s+as|measured\s+(?:by|using))\b|"
    r"结局变量|自变量|协变量|混杂因素|切点|缺失值|插补",
    re.I,
)
STAT_METHOD_RE = re.compile(
    r"\b(?:t[- ]test|ANOVA|Mann[- ]Whitney|Wilcoxon|Kruskal[- ]Wallis|chi[- ]square|Fisher(?:'s)?\s+exact|"
    r"regression|Cox\s+proportional|Kaplan[- ]Meier|mixed[- ]effects|normality|Shapiro[- ]Wilk|Levene|"
    r"sensitivity\s+analysis|two[- ]sided|two[- ]tailed)\b|t检验|方差分析|回归|正态性|敏感性分析|双侧检验",
    re.I,
)
MULTIPLICITY_RE = re.compile(
    r"\b(?:Bonferroni|Holm|Benjamini[- ]Hochberg|FDR|false\s+discovery\s+rate|Tukey|Sidak|Dunnett|"
    r"Scheffe|adjusted\s+p|multiplicity\s+adjustment|multiple\s+comparisons?)\b|多重比较校正|Bonferroni|错误发现率",
    re.I,
)
EFFECT_SIZE_RE = re.compile(
    r"\b(?:Cohen(?:'s)?\s*d|Hedges(?:'s)?\s*g|eta(?:-squared|\^2)|η²|partial\s+η²|odds\s+ratio|\bOR\b|"
    r"hazard\s+ratio|\bHR\b|risk\s+ratio|\bRR\b|R\^2|R²|95%\s*CI|confidence\s+interval|mean\s+difference|\bMD\b|\bSMD\b)\b|"
    r"效应量|置信区间|比值比|风险比",
    re.I,
)
POWER_SAMPLE_RE = re.compile(
    r"\b(?:power\s+analysis|sample\s+size\s+(?:calculation|estimation|justification|determination)|G\*Power|"
    r"80%\s+power|90%\s+power|power\s+of\s+0\.[89]|a\s+priori\s+power|detect\s+an?\s+effect)\b|样本量估算|检验效能|把握度",
    re.I,
)
REPORTING_GUIDELINE_RE = re.compile(
    r"\b(?:CONSORT|STROBE|PRISMA|ARRIVE|STARD|TRIPOD|SPIRIT|CARE)\b",
    re.I,
)


def _parse_p_str(raw: str) -> tuple[float, int]:
    """Parse p-value string (supporting scientific notation) and return (value, decimal_places)."""
    norm = re.sub(r"\s*×\s*10\^?\s*", "e", raw.strip(), flags=re.I)
    val = float(norm)
    if "e" in norm.lower():
        return val, 6
    dec = len(norm.split(".")[1]) if "." in norm else 0
    return val, dec


def _decimal_places(num_str: str) -> int:
    s = num_str.strip()
    return len(s.split(".")[1]) if "." in s else 0


def _normalize_op(op: str) -> str:
    return op.replace("≤", "<=").replace("≥", ">=").strip()


def _classify_p_strict(
    p_calc: float,
    op_raw: str,
    p_rep: float,
    p_dec: int,
    p_bounds_from_stat: tuple[float, float],
    one_sided_ctx: bool,
    allow_one_sided: bool = True,
    alpha: float = 0.05,
) -> tuple[str, str]:
    """Strict StatCheck verdict with zero tolerance for inequality violations and rounding-interval awareness for '='."""
    op = _normalize_op(op_raw)
    p_lo, p_hi = p_bounds_from_stat
    sig_calc = p_calc < alpha

    if op == "=":
        # Exact rounding half-width from reported p decimals + stat rounding interval
        half_p = 0.5 * (10.0 ** (-max(p_dec, 2))) + 1e-9
        # Consistent if reported p lies within [p_lo - half_p, p_hi + half_p]
        if (p_lo - half_p) <= p_rep <= (p_hi + half_p):
            return "consistent", f"Recomputed p={p_calc:.6g} matches reported p={p_rep} within rounding precision."
        # Check one-sided match (half of two-sided p) only for symmetric two-tailed tests (t, r, z)
        if allow_one_sided and (p_lo / 2.0 - half_p) <= p_rep <= (p_hi / 2.0 + half_p):
            if one_sided_ctx:
                return "consistent_one_sided", f"Reported p={p_rep} matches one-sided p={p_calc/2.0:.6g} (one-sided test declared in context)."
            return "possible_one_sided", (
                f"Reported p={p_rep} matches one-sided p={p_calc/2.0:.6g} (half of two-sided p={p_calc:.6g}), "
                "but no one-sided test specification was found nearby."
            )
        sig_rep = p_rep < alpha
        if sig_calc != sig_rep:
            return "decision_error", (
                f"Significance decision flip at alpha={alpha}: reported p={p_rep} ({'sig' if sig_rep else 'ns'}) "
                f"vs recomputed p={p_calc:.6g} ({'sig' if sig_calc else 'ns'})."
            )
        return "rounding_or_mismatch", (
            f"Reported p={p_rep} disagrees with recomputed p={p_calc:.6g} "
            f"(stat-rounding range [{p_lo:.6g}, {p_hi:.6g}]) without flipping alpha={alpha} significance."
        )

    if op in ("<", "<="):
        bound_ok = (p_calc < p_rep) if op == "<" else (p_calc <= p_rep)
        if bound_ok:
            return "consistent", f"Recomputed p={p_calc:.6g} strictly satisfies p {op} {p_rep}."
        # Check stat-rounding lower bound p_lo (e.g., true t(100)=1.9845 -> reported 1.98 has p_lo=0.04988 < 0.05)
        lo_ok = (p_lo < p_rep) if op == "<" else (p_lo <= p_rep)
        if lo_ok:
            return "rounding_ambiguous", (
                f"Stat-rounding boundary ambiguity: point-value p={p_calc:.6g} exceeds p {op} {p_rep}, "
                f"but statistic rounding interval [{p_lo:.6g}, {p_hi:.6g}] satisfies p {op} {p_rep}; "
                "verify unrounded test statistic before flagging a decision error."
            )
        # Check if one-sided satisfies it (only for symmetric t/r/z, never F or chi2)
        one_ok = allow_one_sided and ((p_lo / 2.0 < p_rep) if op == "<" else (p_lo / 2.0 <= p_rep))
        if one_ok and one_sided_ctx:
            return "consistent_one_sided", f"One-sided p={p_calc/2.0:.6g} satisfies declared one-sided p {op} {p_rep}."
        if p_rep <= alpha and p_lo >= alpha:
            return "decision_error", (
                f"Decision error: reported significant bound p {op} {p_rep} is violated across the full "
                f"stat-rounding interval (p={p_calc:.6g}, p_lo={p_lo:.6g} >= {alpha})."
            )
        if one_ok:
            return "possible_one_sided", (
                f"Two-sided p={p_calc:.6g} (p_lo={p_lo:.6g}) violates p {op} {p_rep}, though one-sided "
                f"p={p_calc/2.0:.6g} satisfies it; one-sided hypothesis was not declared."
            )
        return "bound_violation", (
            f"Recomputed p={p_calc:.6g} (rounding lower bound p_lo={p_lo:.6g}) strictly violates reported upper bound p {op} {p_rep}."
        )

    if op in (">", ">="):
        bound_ok = (p_calc > p_rep) if op == ">" else (p_calc >= p_rep)
        if bound_ok:
            return "consistent", f"Recomputed p={p_calc:.6g} satisfies p {op} {p_rep}."
        # Symmetric stat-rounding upper bound p_hi check
        hi_ok = (p_hi > p_rep) if op == ">" else (p_hi >= p_rep)
        if hi_ok:
            return "rounding_ambiguous", (
                f"Stat-rounding boundary ambiguity: point-value p={p_calc:.6g} falls below p {op} {p_rep}, "
                f"but statistic rounding interval [{p_lo:.6g}, {p_hi:.6g}] satisfies p {op} {p_rep}; "
                "verify unrounded test statistic."
            )
        if p_rep >= alpha and p_hi < alpha:
            return "decision_error", (
                f"Decision error: reported non-significant bound p {op} {p_rep} is contradicted across the full "
                f"stat-rounding interval (p={p_calc:.6g}, p_hi={p_hi:.6g} < {alpha})."
            )
        return "bound_violation", (
            f"Recomputed p={p_calc:.6g} (rounding upper bound p_hi={p_hi:.6g}) strictly violates reported lower bound p {op} {p_rep}."
        )

    return "indeterminate", f"Unsupported operator '{op_raw}'."


def _compute_stat_bounds(kind: str, val: float, val_str: str, df_args: tuple[float, ...]) -> tuple[float, float]:
    """Compute [p_lo, p_hi] interval induced by rounding of the reported test statistic."""
    dec = _decimal_places(val_str)
    half = 0.5 * (10.0 ** (-dec)) if dec > 0 else 0.5
    cands: list[float] = []
    for delta in (-half, 0.0, half):
        v = val + delta
        try:
            if kind == "t":
                cands.append(smc.t_two_sided_p(v, df_args[0])[0])
            elif kind == "F":
                cands.append(smc.f_upper_p(max(v, 0.0), df_args[0], df_args[1])[0])
            elif kind == "chi2":
                cands.append(smc.chi2_upper_p(max(v, 0.0), df_args[0])[0])
            elif kind == "r":
                if abs(v) < 0.999999:
                    cands.append(smc.r_two_sided_p(v, df_args[0])[0])
            elif kind == "z":
                cands.append(smc.z_two_sided_p(v)[0])
        except Exception:
            continue
    return (min(cands), max(cands)) if cands else (0.0, 1.0)


def _iter_merged_units(sec: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge PDF hard-wrapped lines per paragraph via review_io_core, then split into isolated sentences with exact line_start..line_end."""
    units: list[dict[str, Any]] = []
    abbr_re = re.compile(r"\b(Fig|Figs|Eq|Ref|Refs|No|vs|al|Dr|Prof|Table|Tab|Suppl|Sec|Vol|e\.g|i\.e|et\s+al)\.", re.I)
    for para in io_core.merge_paragraph_lines(sec):
        text = para["text"]
        spans = para["char_spans"]
        protected = abbr_re.sub(lambda m: m.group(0).replace(".", "<DOT>"), text)
        cursor = 0
        raw_parts: list[tuple[int, int, str]] = []
        for m in re.finditer(r"(?<=[.!?。！？])\s+", protected):
            chunk = protected[cursor:m.start()].replace("<DOT>", ".").strip()
            if chunk:
                orig_start = text.find(chunk[:24], max(0, cursor - 16))
                if orig_start < 0:
                    orig_start = cursor
                raw_parts.append((orig_start, orig_start + len(chunk), chunk))
            cursor = m.end()
        tail = protected[cursor:].replace("<DOT>", ".").strip()
        if tail:
            orig_start = text.find(tail[:24], max(0, cursor - 16))
            if orig_start < 0:
                orig_start = cursor
            raw_parts.append((orig_start, orig_start + len(tail), tail))

        for cs, ce, st in raw_parts:
            if len(st) >= 4:
                ls, le = io_core._span_to_lines(cs, ce, spans, para["line_start"])
                units.append({"text": st, "line_start": ls, "line_end": le})
    return units


def _is_observational_study(sections: list[dict[str, Any]]) -> bool:
    """Detect observational design strictly from Abstract and Methods non-limitation, non-prior-literature sentences."""
    design_secs = [s for s in sections if s["role"] in {"abstract", "methods", "preamble"}]
    if not design_secs:
        design_secs = [s for s in sections if s["role"] not in {"discussion", "conclusion", "references", "declarations"}]
    filtered_sents: list[str] = []
    for sec in design_secs:
        for unit in _iter_merged_units(sec):
            st = unit["text"]
            if FUTURE_OR_PRIOR_DESIGN_RE.search(st):
                continue
            filtered_sents.append(st)
    design_blob = "\n".join(filtered_sents)
    if not design_blob:
        return False
    has_obs = bool(OBSERVATIONAL_DESIGN_RE.search(design_blob))
    has_rct = bool(re.search(r"\b(?:randomized|RCT|randomised)\b|随机对照", design_blob, re.I))
    return has_obs and not has_rct


def _audit_csv_document(doc: dict[str, Any], manuscript_total_ns: list[int]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Perform basic structural audit of a supplied CSV dataset and reconcile row count against manuscript total N."""
    missing_tokens = {"", "na", "n/a", "nan", "null", "none", ".", "-"}
    reader = csv.reader(io.StringIO(doc["text"]))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return {
            "file": doc["file"],
            "n_rows": 0,
            "n_cols": 0,
            "columns": [],
            "missing_cells_total": 0,
            "missing_by_column": {},
            "group_column": None,
            "group_counts": {},
            "manuscript_total_ns": sorted(set(manuscript_total_ns)),
            "verdict": "empty_csv",
        }, None

    headers = [h.strip() or f"col_{i+1}" for i, h in enumerate(rows[0])]
    data_rows = rows[1:]
    n_rows = len(data_rows)
    n_cols = len(headers)
    missing_by_col: dict[str, int] = {h: 0 for h in headers}
    col_values: dict[str, list[str]] = {h: [] for h in headers}

    for r in data_rows:
        for i, h in enumerate(headers):
            val = r[i].strip() if i < len(r) else ""
            if val.lower() in missing_tokens:
                missing_by_col[h] += 1
            else:
                col_values[h].append(val)

    missing_total = sum(missing_by_col.values())

    # Identify a grouping/arm column if present
    group_col_re = re.compile(r"^(?:group|arm|treatment|cohort|condition|status|allocation|intervention|class|label|stratum)$", re.I)
    group_col: str | None = None
    group_counts: dict[str, int] = {}
    for h in headers:
        if group_col_re.match(h):
            group_col = h
            break
    if group_col is None and n_rows >= 4:
        for h in headers:
            vals = col_values[h]
            uniq = set(vals)
            if 2 <= len(uniq) <= 6 and any(not re.match(r"^-?\d+(?:\.\d+)?$", v) for v in uniq):
                group_col = h
                break
    if group_col is not None:
        counts: dict[str, int] = {}
        for v in col_values[group_col]:
            counts[v] = counts.get(v, 0) + 1
        group_counts = dict(sorted(counts.items()))

    verdict = "consistent"
    mismatch_note: dict[str, Any] | None = None
    valid_ns = sorted({n for n in manuscript_total_ns if n >= 5})
    if valid_ns and n_rows >= 5:
        if n_rows not in valid_ns:
            primary_n = max(valid_ns)
            diff = n_rows - primary_n
            rel_diff = abs(diff) / max(primary_n, 1)
            if abs(diff) >= 2 and rel_diff > 0.02:
                verdict = "row_count_mismatch"
                mismatch_note = {
                    "csv_rows": n_rows,
                    "closest_manuscript_N": primary_n,
                    "manuscript_total_ns": valid_ns,
                    "diff": diff,
                    "group_col": group_col,
                    "group_counts": group_counts,
                    "missing_cells_total": missing_total,
                }

    record = {
        "file": doc["file"],
        "n_rows": n_rows,
        "n_cols": n_cols,
        "columns": headers,
        "missing_cells_total": missing_total,
        "missing_by_column": {k: v for k, v in missing_by_col.items() if v > 0},
        "group_column": group_col,
        "group_counts": group_counts,
        "manuscript_total_ns": valid_ns,
        "verdict": verdict,
    }
    return record, mismatch_note


def audit_methods_and_stats(target: Path) -> dict[str, Any]:
    docs, gaps, _ = io_core.load_documents(
        target,
        extra_exts={".csv"},
        ignore_names={"case.json", "task.md", "readme.md"},
    )
    if not docs:
        return {
            "passed": False,
            "audit_status": "failed",
            "status": "failed",
            "math_backend": smc.backend_name(),
            "documents_scanned": [],
            "findings": [],
            "gaps": gaps,
            "error": "No readable source documents found.",
        }

    statcheck_rows: list[dict[str, Any]] = []
    ci_checks: list[dict[str, Any]] = []
    proportion_checks: list[dict[str, Any]] = []
    grim_checks: list[dict[str, Any]] = []
    conservation_checks: list[dict[str, Any]] = []
    pseudoreplication_flags: list[dict[str, Any]] = []
    csv_checks: list[dict[str, Any]] = []
    methodology_coverage: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    exact_pvals: list[float] = []
    unassessable_items: list[str] = []
    uncertainty: list[str] = []
    all_manuscript_total_ns: list[int] = []
    max_crosscheck_err: float = 0.0
    fid = 0

    def add_finding(
        *,
        facet: str,
        finding_type: str,
        defect_class: str,  # decision_error | method_flaw | under_reported | norm_violation | suggestion
        severity: str,
        file: str,
        line_start: int,
        line_end: int,
        page: int,
        section: str,
        verbatim_quote: str,
        evidence_basis: str,
        actionable_revision: str,
        impact: str,
        cost: str,
        status: str = "confirmed",
        missing_inputs: list[str] | None = None,
    ) -> str:
        nonlocal fid
        fid += 1
        f_id = f"STAT-{fid:03d}"
        loc: dict[str, Any] = {"file": file, "section": section, "line_start": line_start, "line_end": line_end}
        if page:
            loc["page"] = page
        rec: dict[str, Any] = {
            "finding_id": f_id,
            "facet": facet,
            "finding_type": finding_type,
            "defect_class": defect_class,
            "severity": severity,
            "location": loc,
            "verbatim_quote": verbatim_quote[:200],
            "evidence_basis": evidence_basis,
            "actionable_revision": actionable_revision,
            "impact_cost": {"impact": impact, "cost": cost},
            "status": status,
        }
        if missing_inputs:
            rec["missing_inputs"] = missing_inputs
        findings.append(rec)
        return f_id

    csv_docs = [d for d in docs if d["ext"] == ".csv"]
    text_docs = [d for d in docs if d["ext"] != ".csv"]

    for doc in text_docs:
        sections = io_core.split_sections(doc)
        full_text = doc["text"]

        # Collect cohort N (uppercase total N / explicit cohort total) and animal/subject-level N mentions
        reported_ns = [int(x) for x in re.findall(r"\b[Nn]\s*=\s*(\d{1,6})\b", full_text) if not (1900 <= int(x) <= 2035)]
        for mt in re.finditer(
            r"(?:\b(?:total(?:\s+of)?|enrolled|recruited|randomized|randomised|included|sample\s+of)\s+(?:[Nn]\s*=\s*)?(\d{2,6}))",
            full_text,
            re.I,
        ):
            nv = int(mt.group(1))
            if not (1900 <= nv <= 2035):
                all_manuscript_total_ns.append(nv)
        for mt in re.finditer(r"\bN\s*=\s*(\d{2,6})\b", full_text):
            nv = int(mt.group(1))
            if not (1900 <= nv <= 2035):
                all_manuscript_total_ns.append(nv)
        max_n = max(reported_ns) if reported_ns else None
        animal_ns: list[int] = []
        for am in ANIMAL_SUBJECT_N_RE.finditer(full_text):
            nv = int(am.group(1) or am.group(2))
            if 2 <= nv <= 500:
                animal_ns.append(nv)
        has_subunit_cues = bool(SUBUNIT_CUE_RE.search(full_text))
        has_mixed_model_cues = bool(REPEATED_MIXED_RE.search(full_text))
        is_observational = _is_observational_study(sections)

        # -------------------------------------------------------------------
        # Paragraph-merged, sentence-isolated deterministic checks (preserves line_start..line_end across PDF wraps)
        # -------------------------------------------------------------------
        for sec in sections:
            sec_title, sec_role = sec["title"], sec["role"]
            if sec_role == "references":
                continue
            for unit in _iter_merged_units(sec):
                st = unit["text"]
                lno = unit["line_start"]
                lend = unit["line_end"]
                page = doc["page_map"].get(lno, 0)
                one_sided_ctx = bool(ONE_SIDED_CTX_RE.search(st))

                # Helper to record a StatCheck hit
                def record_stat(kind: str, df_list: list[float], val: float, val_str: str, op: str, p_str: str, raw_match: str) -> None:
                    nonlocal max_crosscheck_err
                    prep, pdec = _parse_p_str(p_str)
                    if kind == "t":
                        p_calc, cerr = smc.t_two_sided_p(val, df_list[0])
                    elif kind == "F":
                        p_calc, cerr = smc.f_upper_p(val, df_list[0], df_list[1])
                    elif kind == "chi2":
                        p_calc, cerr = smc.chi2_upper_p(val, df_list[0])
                    elif kind == "r":
                        p_calc, cerr = smc.r_two_sided_p(val, df_list[0])
                    else:
                        p_calc, cerr = smc.z_two_sided_p(val)
                    if cerr is not None and cerr > max_crosscheck_err:
                        max_crosscheck_err = cerr
                    p_bounds = _compute_stat_bounds(kind, val, val_str, tuple(df_list))
                    verdict, note = _classify_p_strict(
                        p_calc, op, prep, pdec, p_bounds, one_sided_ctx, allow_one_sided=(kind in {"t", "r", "z"})
                    )
                    row = {
                        "file": doc["file"],
                        "section": sec_title,
                        "line": lno,
                        "line_start": lno,
                        "line_end": lend,
                        "page": page or None,
                        "test": kind,
                        "df": df_list,
                        "stat": val,
                        "reported_op": _normalize_op(op),
                        "reported_p": prep,
                        "computed_p": round(p_calc, 6),
                        "stat_rounding_p_bounds": [round(p_bounds[0], 6), round(p_bounds[1], 6)],
                        "computed_one_sided_p": round(p_calc / 2.0, 6) if kind in {"t", "r", "z"} else None,
                        "verdict": verdict,
                        "note": note,
                        "raw": raw_match,
                    }
                    statcheck_rows.append(row)

                    # Promote StatCheck defects
                    if verdict == "decision_error":
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="factual_error",
                            defect_class="decision_error",
                            severity="critical",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=raw_match,
                            evidence_basis=f"StatCheck ({smc.backend_name()}): {note}",
                            actionable_revision=f"Recompute test inputs/df and correct reported p-value to match exact two-sided p={p_calc:.6g} (or verify typo in test statistic).",
                            impact="high",
                            cost="low",
                        )
                    elif verdict == "rounding_ambiguous":
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="insufficient_evidence",
                            defect_class="under_reported",
                            severity="minor",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=raw_match,
                            evidence_basis=f"StatCheck ({smc.backend_name()}): {note}",
                            actionable_revision=(
                                f"Report the test statistic to 3 decimal places and provide the exact p-value (point p={p_calc:.6g}, "
                                f"stat-rounding range [{p_bounds[0]:.5g}, {p_bounds[1]:.5g}])."
                            ),
                            impact="low",
                            cost="low",
                            status="needs_human_review",
                        )
                    elif verdict in {"bound_violation", "possible_one_sided", "rounding_or_mismatch"}:
                        is_bound = verdict == "bound_violation"
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="factual_error" if is_bound else "insufficient_evidence",
                            defect_class="decision_error" if is_bound else "under_reported",
                            severity="major" if verdict != "rounding_or_mismatch" else "minor",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=raw_match,
                            evidence_basis=f"StatCheck ({smc.backend_name()}): {note}",
                            actionable_revision=(
                                f"Fix reported bound/value to match exact p={p_calc:.6g}"
                                if verdict != "possible_one_sided"
                                else "Explicitly justify and pre-specify one-sided testing in Methods, or report two-sided p-value."
                            ),
                            impact="high" if is_bound else "medium",
                            cost="low",
                        )

                    # df vs N & Pseudoreplication screening
                    check_df = df_list[-1] if df_list else 0.0
                    if check_df > 0 and kind in {"t", "F", "r"}:
                        if (
                            animal_ns
                            and check_df >= max(60.0, 3.0 * max(animal_ns))
                            and (has_subunit_cues or SUBUNIT_CUE_RE.search(st))
                            and not has_mixed_model_cues
                        ):
                            flag = {
                                "file": doc["file"],
                                "line": lno,
                                "line_end": lend,
                                "section": sec_title,
                                "df": check_df,
                                "animal_subject_ns": sorted(set(animal_ns)),
                                "raw": raw_match,
                            }
                            pseudoreplication_flags.append(flag)
                            add_finding(
                                facet="method_design",
                                finding_type="insufficient_evidence",
                                defect_class="method_flaw",
                                severity="major",
                                file=doc["file"],
                                line_start=lno,
                                line_end=lend,
                                page=page,
                                section=sec_title,
                                verbatim_quote=raw_match,
                                evidence_basis=(
                                    f"Suspected pseudoreplication (experimental unit mismatch): df={check_df:g} vastly exceeds "
                                    f"reported biological subject/animal counts ({sorted(set(animal_ns))}) in a manuscript mentioning "
                                    "cells/slices/technical subunits without a nested/mixed-effects model."
                                ),
                                actionable_revision=(
                                    "Re-run inference using biological subject/animal as the independent experimental unit "
                                    "(e.g., per-animal summary or linear mixed-effects model with subject random intercept)."
                                ),
                                impact="high",
                                cost="medium",
                            )
                        elif max_n and check_df > max_n and not has_mixed_model_cues:
                            add_finding(
                                facet="statistical_reporting",
                                finding_type="factual_error",
                                defect_class="method_flaw",
                                severity="major",
                                file=doc["file"],
                                line_start=lno,
                                line_end=lend,
                                page=page,
                                section=sec_title,
                                verbatim_quote=raw_match,
                                evidence_basis=f"Reported degrees of freedom df={check_df:g} exceeds maximum reported cohort sample size N={max_n} without repeated-measures/mixed-model disclosure.",
                                actionable_revision="Reconcile df with independent cohort size N or clarify total repeated observations and clustering model.",
                                impact="high",
                                cost="low",
                            )

                # 1. StatCheck regex sweeps (APA + non-APA 't = ..., df = ...' / 'F = ..., df = ...' / 'chi2 = ..., df = ...')
                for m in T_TEST_RE.finditer(st):
                    df = float(m.group(1))
                    if df > 0:
                        record_stat("t", [df], float(m.group(2)), m.group(2), m.group(3), m.group(4), m.group(0))
                for m in T_TEST_DF_RE.finditer(st):
                    df = float(m.group(2))
                    if df > 0:
                        record_stat("t", [df], float(m.group(1)), m.group(1), m.group(3), m.group(4), m.group(0))
                for m in F_TEST_RE.finditer(st):
                    df1, df2 = float(m.group(1)), float(m.group(2))
                    if df1 > 0 and df2 > 0:
                        record_stat("F", [df1, df2], float(m.group(3)), m.group(3), m.group(4), m.group(5), m.group(0))
                for m in F_TEST_DF_RE.finditer(st):
                    df1, df2 = float(m.group(2)), float(m.group(3))
                    if df1 > 0 and df2 > 0:
                        record_stat("F", [df1, df2], float(m.group(1)), m.group(1), m.group(4), m.group(5), m.group(0))
                for m in CHI2_TEST_RE.finditer(st):
                    df = float(m.group(1))
                    if df > 0:
                        record_stat("chi2", [df], float(m.group(2)), m.group(2), m.group(3), m.group(4), m.group(0))
                for m in CHI2_TEST_DF_RE.finditer(st):
                    df = float(m.group(2))
                    if df > 0:
                        record_stat("chi2", [df], float(m.group(1)), m.group(1), m.group(3), m.group(4), m.group(0))
                for m in R_TEST_RE.finditer(st):
                    df, val = int(m.group(1)), float(m.group(2))
                    if df > 0 and abs(val) < 1.0:
                        record_stat("r", [float(df)], val, m.group(2), m.group(3), m.group(4), m.group(0))
                for m in Z_TEST_RE.finditer(st):
                    record_stat("z", [], float(m.group(1)), m.group(1), m.group(2), m.group(3), m.group(0))

                # 2. Invalid 'p = 0.000' notation
                for mp0 in P_ZERO_RE.finditer(st):
                    add_finding(
                        facet="statistical_reporting",
                        finding_type="norm_violation",
                        defect_class="norm_violation",
                        severity="minor",
                        file=doc["file"],
                        line_start=lno,
                        line_end=lend,
                        page=page,
                        section=sec_title,
                        verbatim_quote=mp0.group(0),
                        evidence_basis="Reporting 'p = 0.000' violates statistical reporting standards because a continuous p-value cannot equal zero.",
                        actionable_revision="Replace 'p = 0.000' with an upper bound such as 'p < 0.001' (or exact scientific notation).",
                        impact="low",
                        cost="low",
                    )

                # Collect exact p= values for optional p-curve (deduplicate identical p repeated within same sentence)
                seen_sent_p: set[float] = set()
                for mp in BARE_P_EQ_RE.finditer(st):
                    pv = float(mp.group(1))
                    if 0.0 < pv < 1.0 and pv not in seen_sent_p:
                        seen_sent_p.add(pv)
                        exact_pvals.append(pv)

                # 3. Effect size & 95% CI vs p-value consistency (supports APA + NEJM/JAMA/Lancet comma style)
                for mc in EFFECT_CI_RE.finditer(st):
                    kind_raw = mc.group("kind")
                    kind_low = kind_raw.lower()
                    est = float(mc.group("est"))
                    lo = float(mc.group("lo"))
                    hi = float(mc.group("hi"))
                    pop = _normalize_op(mc.group("pop")) if mc.group("pop") else None
                    pval = float(mc.group("pval")) if mc.group("pval") else None
                    is_ratio = any(tok in kind_low for tok in ("or", "hr", "rr", "irr", "odds", "hazard", "risk"))
                    null_val = 1.0 if is_ratio else 0.0
                    local_ci_ctx = st[max(0, mc.start() - 40):min(len(st), mc.end() + 40)]
                    adj_ctx = bool(ADJ_P_CTX_RE.search(local_ci_ctx))

                    chk: dict[str, Any] = {
                        "file": doc["file"],
                        "line": lno,
                        "line_end": lend,
                        "section": sec_title,
                        "metric": kind_raw,
                        "estimate": est,
                        "ci_95": [lo, hi],
                        "null_value": null_val,
                        "reported_p_op": pop,
                        "reported_p": pval,
                        "verdict": "consistent",
                        "raw": mc.group(0)[:180],
                    }

                    if lo > hi:
                        chk["verdict"] = "ci_inverted"
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="factual_error",
                            defect_class="decision_error",
                            severity="critical",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=mc.group(0),
                            evidence_basis=f"95% CI bounds are inverted: lower bound {lo} > upper bound {hi} for {kind_raw}={est}.",
                            actionable_revision="Verify and correct the 95% confidence interval limits from the primary statistical output.",
                            impact="high",
                            cost="low",
                        )
                    elif est < lo - 1e-9 or est > hi + 1e-9:
                        chk["verdict"] = "estimate_outside_ci"
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="factual_error",
                            defect_class="decision_error",
                            severity="critical",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=mc.group(0),
                            evidence_basis=f"Point estimate {kind_raw}={est} falls outside its own reported 95% CI [{lo}, {hi}].",
                            actionable_revision="Re-extract the point estimate and 95% CI from the model output and correct the transcription error.",
                            impact="high",
                            cost="low",
                        )
                    elif pop is not None and pval is not None:
                        strictly_crosses_null = (lo < null_val - 0.005) and (hi > null_val + 0.005)
                        strictly_excludes_null = (lo > null_val + 0.015) or (hi < null_val - 0.015)
                        rep_sig = (pop in ("<", "<=") and pval <= 0.05) or (pop == "=" and pval < 0.045)
                        rep_ns = (pop in (">", ">=") and pval >= 0.05) or (pop == "=" and pval > 0.055)

                        if strictly_crosses_null and rep_sig:
                            chk["verdict"] = "ci_p_contradiction"
                            add_finding(
                                facet="statistical_reporting",
                                finding_type="factual_error",
                                defect_class="decision_error",
                                severity="critical",
                                file=doc["file"],
                                line_start=lno,
                                line_end=lend,
                                page=page,
                                section=sec_title,
                                verbatim_quote=mc.group(0),
                                evidence_basis=(
                                    f"95% CI vs p-value contradiction: {kind_raw}={est} has 95% CI [{lo}, {hi}] "
                                    f"strictly spanning the null value ({null_val}), yet reported p {pop} {pval} (<0.05)."
                                ),
                                actionable_revision="Reconcile the 95% CI and p-value against the exact model output (check whether CI is 99%, or if p-value/CI was mis-transcribed).",
                                impact="high",
                                cost="low",
                            )
                        elif strictly_excludes_null and rep_ns and not adj_ctx:
                            chk["verdict"] = "ci_p_contradiction"
                            add_finding(
                                facet="statistical_reporting",
                                finding_type="factual_error",
                                defect_class="decision_error",
                                severity="critical",
                                file=doc["file"],
                                line_start=lno,
                                line_end=lend,
                                page=page,
                                section=sec_title,
                                verbatim_quote=mc.group(0),
                                evidence_basis=(
                                    f"95% CI vs p-value contradiction: {kind_raw}={est} has 95% CI [{lo}, {hi}] "
                                    f"excluding the null value ({null_val}), yet reported non-significant p {pop} {pval} without multiplicity-adjustment note."
                                ),
                                actionable_revision="Verify whether the p-value is multiplicity-adjusted (if so, state explicitly) or correct the mismatched CI/p-value.",
                                impact="high",
                                cost="low",
                            )
                        elif strictly_excludes_null and rep_ns and adj_ctx:
                            chk["verdict"] = "consistent_adjusted_p"
                    ci_checks.append(chk)

                # 4. Integer proportion k/n (pct%) verification
                for mpr in PROP_PCT_RE.finditer(st):
                    k_val, n_val = int(mpr.group(1)), int(mpr.group(2))
                    pct_str = mpr.group(3)
                    pct_rep = float(pct_str)
                    if n_val <= 0:
                        continue
                    if k_val > n_val:
                        proportion_checks.append({"file": doc["file"], "line": lno, "k": k_val, "n": n_val, "reported_pct": pct_rep, "verdict": "numerator_exceeds_denominator"})
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="factual_error",
                            defect_class="decision_error",
                            severity="critical",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=mpr.group(0),
                            evidence_basis=f"Impossible proportion: numerator {k_val} exceeds denominator {n_val}.",
                            actionable_revision="Verify raw counts and denominator in source dataset.",
                            impact="high",
                            cost="low",
                        )
                        continue
                    expected_pct = 100.0 * k_val / n_val
                    dec = _decimal_places(pct_str)
                    tol = 0.5 * (10.0 ** (-dec)) + 0.05
                    ok_pct = abs(pct_rep - expected_pct) <= tol
                    proportion_checks.append({
                        "file": doc["file"],
                        "line": lno,
                        "k": k_val,
                        "n": n_val,
                        "reported_pct": pct_rep,
                        "expected_pct": round(expected_pct, 2),
                        "verdict": "consistent" if ok_pct else "percentage_mismatch",
                    })
                    if not ok_pct:
                        add_finding(
                            facet="statistical_reporting",
                            finding_type="factual_error",
                            defect_class="decision_error",
                            severity="major",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=mpr.group(0),
                            evidence_basis=f"Integer percentage mismatch: {k_val}/{n_val} = {expected_pct:.2f}%, but manuscript reports {pct_rep}% (diff={abs(pct_rep - expected_pct):.2f} pp).",
                            actionable_revision=f"Correct reported percentage to {expected_pct:.{max(dec, 1)}f}% or verify numerator/denominator counts.",
                            impact="high",
                            cost="low",
                        )

                # 5. GRIM test (strictly gated to integer/Likert/rating cues, generalized for natural scientific prose)
                if DISCRETE_SCALE_CUE_RE.search(st):
                    seen_grim_pairs: set[tuple[int, str]] = set()
                    for mg in MEAN_N_RE.finditer(st):
                        n_raw = mg.group("n1") or mg.group("n2") or mg.group("n3") or mg.group("n4")
                        m_str = mg.group("m1") or mg.group("m2") or mg.group("m3") or mg.group("m4")
                        if not n_raw or not m_str:
                            continue
                        n_g = int(n_raw)
                        if (n_g, m_str) in seen_grim_pairs:
                            continue
                        seen_grim_pairs.add((n_g, m_str))
                        m_val = float(m_str)
                        dec = _decimal_places(m_str)
                        if 1 <= n_g <= 100 and dec >= 1:
                            prod = n_g * m_val
                            nearest_int = round(prod)
                            max_round_err = n_g * (0.5 * (10.0 ** (-dec))) + 1e-6
                            possible = abs(prod - nearest_int) <= max_round_err
                            grim_checks.append({
                                "file": doc["file"],
                                "line": lno,
                                "line_end": lend,
                                "n": n_g,
                                "reported_mean": m_val,
                                "decimals": dec,
                                "n_times_mean": round(prod, 4),
                                "possible": possible,
                                "raw": mg.group(0),
                            })
                            if not possible:
                                add_finding(
                                    facet="statistical_reporting",
                                    finding_type="factual_error",
                                    defect_class="decision_error",
                                    severity="major",
                                    file=doc["file"],
                                    line_start=lno,
                                    line_end=lend,
                                    page=page,
                                    section=sec_title,
                                    verbatim_quote=st,
                                    evidence_basis=(
                                        f"GRIM granularity inconsistency on discrete scale: mean={m_str} with n={n_g} "
                                        f"implies sum={prod:.3f}, which cannot be formed by any integer sum within rounding tolerance."
                                    ),
                                    actionable_revision="Verify integer scale scores, missing-item handling, or sample size n for this mean.",
                                    impact="high",
                                    cost="low",
                                )

                # 6. Group-N conservation ONLY in mutually exclusive group allocation context (spares non-exclusive subgroups like "including older adults (n=40) and smokers (n=35)")
                mtot = TOTAL_N_SENT_RE.search(st)
                grp_ns = [int(x) for x in GROUP_N_RE.findall(st)]
                if (
                    mtot
                    and len(grp_ns) >= 2
                    and EXCLUSIVE_ALLOCATION_RE.search(st)
                    and not NON_EXCLUSIVE_SUBGROUP_RE.search(st)
                ):
                    tot_n = int(mtot.group(1) or mtot.group(2))
                    if tot_n not in grp_ns and sum(grp_ns) != tot_n:
                        has_attrition = bool(ATTRITION_CUE_RE.search(st))
                        conservation_checks.append({
                            "file": doc["file"],
                            "line": lno,
                            "line_end": lend,
                            "total_N": tot_n,
                            "group_ns": grp_ns,
                            "group_sum": sum(grp_ns),
                            "diff": sum(grp_ns) - tot_n,
                            "attrition_mentioned": has_attrition,
                            "verdict": "attrition_unreconciled" if has_attrition else "sum_mismatch",
                        })
                        add_finding(
                            facet="sample_size_power",
                            finding_type="insufficient_evidence" if has_attrition else "factual_error",
                            defect_class="under_reported" if has_attrition else "decision_error",
                            severity="minor" if has_attrition else "major",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lend,
                            page=page,
                            section=sec_title,
                            verbatim_quote=st,
                            evidence_basis=(
                                f"Sample-size conservation gap: stated total N={tot_n} does not equal sum of mutually exclusive "
                                f"group counts {grp_ns} (sum={sum(grp_ns)}, diff={sum(grp_ns) - tot_n:+d})."
                            ),
                            actionable_revision=(
                                "Explicitly report exact exclusion/attrition counts bridging total N and analyzed subgroup counts."
                                if has_attrition
                                else f"Reconcile total N={tot_n} with group counts {grp_ns} (sum={sum(grp_ns)})."
                            ),
                            impact="high" if not has_attrition else "medium",
                            cost="low",
                        )

                # 7. Spin / vague significance phrasing
                if VAGUE_SIG_RE.search(st):
                    add_finding(
                        facet="statistical_reporting",
                        finding_type="norm_violation",
                        defect_class="norm_violation",
                        severity="minor",
                        file=doc["file"],
                        line_start=lno,
                        line_end=lend,
                        page=page,
                        section=sec_title,
                        verbatim_quote=st,
                        evidence_basis="Spin/vague significance phrasing ('marginally significant' / 'trending toward significance') obscures non-significant results.",
                        actionable_revision="State non-significance plainly and report the exact p-value, point estimate, and 95% CI.",
                        impact="medium",
                        cost="low",
                    )

                # 8. Observational study making strong causal claims (method_flaw, excluding limitations/hypotheses)
                if (
                    is_observational
                    and sec_role in {"abstract", "results", "discussion", "conclusion", "results_discussion"}
                    and CAUSAL_CLAIM_RE.search(st)
                    and not re.search(r"\b(?:limitation|cannot\s+prove|does\s+not\s+prove|hypothesize|causal\s+inference\s+requires)\b|局限|不能证明", st, re.I)
                ):
                    add_finding(
                        facet="method_design",
                        finding_type="insufficient_evidence",
                        defect_class="method_flaw",
                        severity="major",
                        file=doc["file"],
                        line_start=lno,
                        line_end=lend,
                        page=page,
                        section=sec_title,
                        verbatim_quote=st,
                        evidence_basis="Strong causal assertion made in a manuscript describing an observational/cross-sectional/retrospective design without randomized or quasi-experimental causal identification.",
                        actionable_revision="Downgrade causal verbs ('proves/causes') to associational language ('was associated with') and discuss residual confounding.",
                        impact="high",
                        cost="low",
                    )

        # -------------------------------------------------------------------
        # Five-Facet Methodology Coverage Matrix (strict under_reported boundary)
        # -------------------------------------------------------------------
        file_tests = [r for r in statcheck_rows if r["file"] == doc["file"]]
        has_design = bool(STUDY_DESIGN_RE.search(full_text))
        has_vars = bool(VARIABLE_DEF_RE.search(full_text))
        has_methods = bool(STAT_METHOD_RE.search(full_text))
        has_mult = bool(MULTIPLICITY_RE.search(full_text))
        has_power = bool(POWER_SAMPLE_RE.search(full_text))
        has_eff = bool(EFFECT_SIZE_RE.search(full_text)) or bool([c for c in ci_checks if c["file"] == doc["file"]])
        has_guideline = bool(REPORTING_GUIDELINE_RE.search(full_text))
        has_attrition_funnel = bool(ATTRITION_CUE_RE.search(full_text))

        methodology_coverage[doc["file"]] = {
            "1_study_design": {
                "disclosed": has_design,
                "observational_detected": is_observational,
                "status": "covered" if has_design else "under_reported",
            },
            "2_variable_definition": {
                "disclosed": has_vars,
                "status": "covered" if has_vars else "under_reported",
            },
            "3_statistical_methods": {
                "methods_disclosed": has_methods,
                "multiplicity_disclosed": has_mult,
                "nested_or_mixed_disclosed": has_mixed_model_cues,
                "n_hypothesis_tests_parsed": len(file_tests),
                "status": "covered" if (has_methods and (len(file_tests) < 3 or has_mult)) else "under_reported",
            },
            "4_sample_size_power": {
                "sample_size_reported": bool(reported_ns),
                "power_rationale_disclosed": has_power,
                "attrition_or_exclusion_disclosed": has_attrition_funnel,
                "status": "covered" if (reported_ns and has_power) else "under_reported",
            },
            "5_results_reporting": {
                "effect_size_or_ci_disclosed": has_eff,
                "reporting_guideline_cited": has_guideline,
                "statcheck_tests_count": len(file_tests),
                "ci_checks_count": len([c for c in ci_checks if c["file"] == doc["file"]]),
                "status": "covered" if (has_eff or not file_tests) else "under_reported",
            },
        }

        # Multiplicity under-reporting: strictly labeled under_reported (NOT p-hacking, NOT method_flaw without raw data)
        if len(file_tests) >= 3 and not has_mult:
            missing_list = [
                "raw_dataset_or_full_test_table",
                "prespecified_primary_vs_secondary_endpoint_protocol",
                "familywise_test_count_and_adjustment_method",
            ]
            unassessable_items.append(
                f"{doc['file']}: Family-wise Type I error inflation and selective reporting cannot be adjudicated without raw data or a pre-registered analysis plan (do NOT infer p-hacking from undisclosed multiplicity alone)."
            )
            add_finding(
                facet="method_design",
                finding_type="insufficient_evidence",
                defect_class="under_reported",
                severity="minor",
                file=doc["file"],
                line_start=1,
                line_end=1,
                page=0,
                section="Methods / Statistical Analysis",
                verbatim_quote=f"[UNDER_REPORTED: {len(file_tests)} hypothesis tests parsed with no multiplicity adjustment statement]",
                evidence_basis=(
                    f"{len(file_tests)} statistical hypothesis tests were detected with no Bonferroni/Holm/FDR/Tukey "
                    "multiplicity disclosure. Because raw data and pre-registration protocol are absent, this is classified "
                    "strictly as 'under_reported' (not proven p-hacking or analytical misconduct)."
                ),
                actionable_revision=(
                    "Clarify which test corresponds to the pre-specified primary endpoint, whether p-values are adjusted "
                    "for multiple comparisons, or label secondary tests explicitly as exploratory."
                ),
                impact="medium",
                cost="low",
                status="needs_human_review",
                missing_inputs=missing_list,
            )

        if (file_tests or exact_pvals) and not has_eff:
            add_finding(
                facet="statistical_reporting",
                finding_type="insufficient_evidence",
                defect_class="under_reported",
                severity="minor",
                file=doc["file"],
                line_start=1,
                line_end=1,
                page=0,
                section="Results",
                verbatim_quote="[UNDER_REPORTED: effect sizes and 95% confidence intervals]",
                evidence_basis="Hypothesis test p-values are reported without accompanying effect sizes (d, OR, HR, MD) or 95% CIs (reporting gap, not a calculation error).",
                actionable_revision="Supplement key p-values with point estimates of effect magnitude and 95% confidence intervals.",
                impact="medium",
                cost="low",
                status="confirmed",
            )

        if (file_tests or reported_ns) and not has_power:
            add_finding(
                facet="sample_size_power",
                finding_type="suggestion",
                defect_class="under_reported",
                severity="suggestion",
                file=doc["file"],
                line_start=1,
                line_end=1,
                page=0,
                section="Methods",
                verbatim_quote="[UNDER_REPORTED: a priori sample size / power calculation]",
                evidence_basis="No explicit sample-size calculation or power rationale was found (reporting omission; does not by itself invalidate the study design).",
                actionable_revision="State how sample size was determined (a priori power inputs or pragmatic recruitment ceiling) and discuss precision via CI widths.",
                impact="low",
                cost="low",
                status="confirmed",
            )

        if not has_vars and (file_tests or reported_ns):
            add_finding(
                facet="variable_definition",
                finding_type="insufficient_evidence",
                defect_class="under_reported",
                severity="minor",
                file=doc["file"],
                line_start=1,
                line_end=1,
                page=0,
                section="Methods",
                verbatim_quote="[UNDER_REPORTED: variable definitions / missing-data handling]",
                evidence_basis="No explicit operational variable definitions, cut-off rationales, or missing-data handling descriptions were detected.",
                actionable_revision="Define primary outcome, exposure/group coding, covariate selection, and missing-data handling explicitly in Methods.",
                impact="medium",
                cost="low",
                status="needs_human_review",
            )

    # Audit any supplied .csv tabular datasets (row/col count, missingness, group counts vs manuscript total N)
    for cdoc in csv_docs:
        crec, mismatch = _audit_csv_document(cdoc, all_manuscript_total_ns)
        csv_checks.append(crec)
        if mismatch is not None:
            add_finding(
                facet="sample_size_power",
                finding_type="insufficient_evidence",
                defect_class="under_reported",
                severity="major",
                file=cdoc["file"],
                line_start=1,
                line_end=min(cdoc["line_count"], 5),
                page=0,
                section="Supplied CSV Dataset",
                verbatim_quote=(
                    f"CSV rows={mismatch['csv_rows']}, cols={crec['n_cols']}, missing={mismatch['missing_cells_total']}, "
                    f"group_counts={mismatch['group_counts']} vs manuscript N={mismatch['manuscript_total_ns']}"
                ),
                evidence_basis=(
                    f"Supplied CSV '{cdoc['file']}' has {mismatch['csv_rows']} data rows (missing cells={mismatch['missing_cells_total']}), "
                    f"which disagrees with reported manuscript sample size N={mismatch['closest_manuscript_N']} (diff={mismatch['diff']:+d})."
                ),
                actionable_revision=(
                    "Reconcile the supplied CSV row count and grouping counts against the manuscript total N "
                    "(clarify exclusions, repeated rows per subject, or subset export)."
                ),
                impact="high",
                cost="low",
                status="needs_human_review",
            )

    # Optional distributional p-curve battery ONLY when n_significant >= 5 (never accuses single paper of misconduct)
    sig_exact = [p for p in exact_pvals if 0.0 < p < 0.05]
    if len(sig_exact) >= 5:
        phack_summary = phack_detect.report(pvals=exact_pvals)
        uncertainty.append(
            f"p-curve distributional diagnostics ran on {len(sig_exact)} exact significant p-values; results are strictly descriptive and cannot diagnose p-hacking in a single manuscript."
        )
    else:
        phack_summary = {
            "ran": False,
            "backend": smc.backend_name(),
            "n_total": len(exact_pvals),
            "n_significant": len(sig_exact),
            "note": "Skipped distributional p-curve battery because n_significant < 5 (prevents underpowered false positives).",
        }

    if not csv_docs:
        unassessable_items.append("Raw tabular data not supplied; distribution normality, homoscedasticity, and exact raw-data reanalysis remain unverified.")
    else:
        uncertainty.append(
            f"Structural CSV reconciliation completed on {len(csv_docs)} table(s); full regression refitting requires model formula and variable-coding dictionary."
        )
    uncertainty.extend(unassessable_items)

    crit_major = [f for f in findings if f["severity"] in {"critical", "major"}]
    audit_status = "partial" if gaps else "completed"

    return {
        "passed": len(crit_major) == 0,
        "audit_status": audit_status,
        "status": "partial_with_gaps" if gaps else "ok",
        "math_backend": smc.backend_name(),
        "scipy_crosscheck_max_error": round(max_crosscheck_err, 12) if smc.has_scipy() else None,
        "documents_scanned": [{"file": d["file"], "sha256": d["sha256"], "extractor": d["extractor"]} for d in docs],
        "statcheck_count": len(statcheck_rows),
        "statcheck_results": statcheck_rows,
        "effect_ci_checks_count": len(ci_checks),
        "effect_ci_checks": ci_checks,
        "proportion_checks": proportion_checks,
        "grim_checks": grim_checks,
        "sample_conservation_checks": conservation_checks,
        "pseudoreplication_flags": pseudoreplication_flags,
        "csv_checks": csv_checks,
        "methodology_coverage": methodology_coverage,
        "phack_analysis": phack_summary,
        "findings_count": len(findings),
        "findings": findings,
        "unassessable_items": unassessable_items,
        "uncertainty": uncertainty,
        "gaps": gaps,
        "verdict_note": (
            "No critical or major statistical/methodological defects detected within inspected coverage."
            if len(crit_major) == 0
            else f"Detected {len(crit_major)} critical/major statistical or methodological finding(s) requiring revision."
        ),
    }


def render_markdown_report(rep: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Research Methods & Statistical Audit Report (`{rep.get('audit_status')}`)",
        "",
        "## 1. Executive Summary",
        "",
        f"- **Audit Verdict**: `{'PASS (no P0/P1 defects)' if rep.get('passed') else 'ACTION REQUIRED'}` — {rep.get('verdict_note', '')}",
        f"- **Math Backend**: `{rep.get('math_backend')}` (pure-stdlib Lentz Beta/Gamma engine, `< 1e-9` accuracy)",
        f"- **StatCheck Parsed Tests**: `{rep.get('statcheck_count', 0)}` | **Effect-Size/95% CI Checks**: `{rep.get('effect_ci_checks_count', 0)}`",
        f"- **Proportion Checks**: `{len(rep.get('proportion_checks', []))}` | **GRIM Checks**: `{len(rep.get('grim_checks', []))}` | **Group-N Conservation**: `{len(rep.get('sample_conservation_checks', []))}`",
        f"- **Pseudoreplication Flags**: `{len(rep.get('pseudoreplication_flags', []))}` | **Total Findings**: `{rep.get('findings_count', 0)}`",
        "",
    ]

    # Five-Facet Methodology Coverage Table
    if rep.get("methodology_coverage"):
        lines.extend([
            "## 2. Five-Facet Methodology Coverage Matrix",
            "",
            "| Document | 1. Study Design | 2. Variables | 3. Statistical Methods | 4. Sample Size & Power | 5. Results Reporting |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for fname, cov in rep["methodology_coverage"].items():
            lines.append(
                f"| `{fname}` | `{cov['1_study_design']['status']}` | `{cov['2_variable_definition']['status']}` | "
                f"`{cov['3_statistical_methods']['status']}` (mult={cov['3_statistical_methods']['multiplicity_disclosed']}) | "
                f"`{cov['4_sample_size_power']['status']}` (power={cov['4_sample_size_power']['power_rationale_disclosed']}) | "
                f"`{cov['5_results_reporting']['status']}` (CI/ES={cov['5_results_reporting']['effect_size_or_ci_disclosed']}) |"
            )
        lines.append("")

    # StatCheck Table
    if rep.get("statcheck_results"):
        lines.extend([
            "## 3. StatCheck Exact Re-Computation Table",
            "",
            "| Location | Reported Expression | Computed Two-Sided p | Verdict | Note |",
            "| --- | --- | ---: | --- | --- |",
        ])
        for r in rep["statcheck_results"]:
            raw_q = r["raw"].replace("|", "\\|")
            note_q = r["note"].replace("|", "\\|")[:110]
            lines.append(f"| `{r['file']}:L{r['line']}` | `{raw_q}` | `{r['computed_p']}` | `{r['verdict']}` | {note_q} |")
        lines.append("")

    # Effect-size & 95% CI table
    if rep.get("effect_ci_checks"):
        lines.extend([
            "## 4. Effect Size & 95% CI vs p-Value Consistency",
            "",
            "| Location | Metric & Estimate | 95% CI | Null | Reported p | Verdict |",
            "| --- | --- | --- | ---: | --- | --- |",
        ])
        for c in rep["effect_ci_checks"]:
            p_disp = f"{c['reported_p_op']} {c['reported_p']}" if c.get("reported_p_op") else "*(none)*"
            lines.append(
                f"| `{c['file']}:L{c['line']}` | `{c['metric']} = {c['estimate']}` | `[{c['ci_95'][0]}, {c['ci_95'][1]}]` | "
                f"`{c['null_value']}` | `{p_disp}` | `{c['verdict']}` |"
            )
        lines.append("")

    # Supplied CSV structural reconciliation
    if rep.get("csv_checks"):
        lines.extend([
            "## 4b. Supplied CSV Structural Reconciliation",
            "",
            "| CSV File | Rows | Cols | Missing Cells | Group Counts | Manuscript N | Verdict |",
            "| --- | ---: | ---: | ---: | --- | --- | --- |",
        ])
        for cc in rep["csv_checks"]:
            gc = ", ".join(f"{k}={v}" for k, v in cc.get("group_counts", {}).items()) or "*(none)*"
            mns = ", ".join(str(x) for x in cc.get("manuscript_total_ns", [])) or "*(none)*"
            lines.append(
                f"| `{cc['file']}` | {cc['n_rows']} | {cc['n_cols']} | {cc['missing_cells_total']} | {gc} | {mns} | `{cc['verdict']}` |"
            )
        lines.append("")

    # Findings separated clearly by defect class & severity
    lines.extend(["## 5. Findings & Actionable Revision Checklist (Decision Error vs Method Flaw vs Under-Reported)", ""])
    findings = rep.get("findings", [])
    if not findings:
        lines.append("No qualifying statistical or methodological defects detected within the inspected scope.")
    else:
        sev_order = {"critical": 0, "major": 1, "minor": 2, "suggestion": 3}
        ordered = sorted(findings, key=lambda x: (sev_order.get(x["severity"], 9), x["finding_id"]))
        for f in ordered:
            loc = f["location"]
            ic = f.get("impact_cost", {})
            pg = f"#page={loc['page']}" if loc.get("page") else ""
            lines.extend([
                f"### {f['finding_id']} [{f['severity'].upper()} | {f['defect_class']} | {f['finding_type']}]",
                f"- **Location**: `{loc['file']}:L{loc['line_start']}-L{loc['line_end']}{pg}` ({loc.get('section', '')})",
                f"- **Facet**: `{f['facet']}` | **Impact × Cost**: `impact={ic.get('impact')}`, `cost={ic.get('cost')}` | **Status**: `{f['status']}`",
                f"- **Quote**: \"{f['verbatim_quote']}\"",
                f"- **Evidence**: {f['evidence_basis']}",
                f"- **Actionable Revision**: {f['actionable_revision']}",
            ])
            if f.get("missing_inputs"):
                lines.append(f"- **Missing Inputs Required to Adjudicate**: `{', '.join(f['missing_inputs'])}`")
            lines.append("")

    # Unassessable Items & Epistemic Boundaries
    lines.extend(["## 6. Unassessable Items, p-Curve Diagnostics & Honest Gaps", ""])
    ph = rep.get("phack_analysis", {})
    lines.append(f"- **Optional p-Curve Battery**: `ran={ph.get('ran')}`, `n_significant={ph.get('n_significant', 0)}`, verdict=`{ph.get('verdict', ph.get('note', 'n/a'))}`")
    if ph.get("caveat"):
        lines.append(f"  - *Caveat*: {ph['caveat']}")
    for u in rep.get("unassessable_items", []):
        lines.append(f"- **Unassessable Boundary**: {u}")
    for g in rep.get("gaps", []):
        lines.append(f"- **Gap [{g.get('code')}]** (`{g.get('source', '')}`): {g.get('message', '')}")
    lines.append("")
    return "\n".join(lines)


def _resolve_case_id(input_path: Path, explicit_case_id: str) -> str:
    if explicit_case_id.strip():
        return explicit_case_id.strip()
    if input_path.is_dir():
        cj = input_path / "case.json"
        if cj.is_file():
            try:
                obj = json.loads(cj.read_text(encoding="utf-8"))
                if isinstance(obj, dict) and str(obj.get("case_id", "")).strip():
                    return str(obj["case_id"]).strip()
            except Exception:
                pass
    return input_path.stem or "case-01"


def _build_result_contract(
    rep: dict[str, Any],
    *,
    case_id: str,
    out_result: Path,
    out_json: Path,
    out_md: Path | None,
) -> dict[str, Any]:
    """Build dual-compatible /app/output/result.json contract payload with full metric provenance."""
    out_dir = out_result.resolve().parent
    artifacts: list[str] = [out_result.name]
    for p in (out_json, out_md):
        if p is not None:
            try:
                rel = p.resolve().relative_to(out_dir).as_posix()
            except ValueError:
                rel = p.name
            if rel not in artifacts:
                artifacts.append(rel)

    metrics = {
        "documents_scanned_count": len(rep.get("documents_scanned", [])),
        "statcheck_count": int(rep.get("statcheck_count", 0)),
        "effect_ci_checks_count": int(rep.get("effect_ci_checks_count", 0)),
        "proportion_checks_count": len(rep.get("proportion_checks", [])),
        "grim_checks_count": len(rep.get("grim_checks", [])),
        "sample_conservation_checks_count": len(rep.get("sample_conservation_checks", [])),
        "pseudoreplication_flags_count": len(rep.get("pseudoreplication_flags", [])),
        "csv_checks_count": len(rep.get("csv_checks", [])),
        "findings_count": int(rep.get("findings_count", 0)),
    }
    src_ref = out_json.name
    metric_provenance = {
        "documents_scanned_count": f"{src_ref}:documents_scanned",
        "statcheck_count": f"{src_ref}:statcheck_count",
        "effect_ci_checks_count": f"{src_ref}:effect_ci_checks_count",
        "proportion_checks_count": f"{src_ref}:proportion_checks",
        "grim_checks_count": f"{src_ref}:grim_checks",
        "sample_conservation_checks_count": f"{src_ref}:sample_conservation_checks",
        "pseudoreplication_flags_count": f"{src_ref}:pseudoreplication_flags",
        "csv_checks_count": f"{src_ref}:csv_checks",
        "findings_count": f"{src_ref}:findings_count",
    }
    has_gaps = bool(rep.get("gaps"))
    top_status = "partial_with_gaps" if has_gaps else "ok"
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "task_id": "challenge-9e7302fd",
        "status": top_status,
        "audit_status": rep.get("audit_status", "partial" if has_gaps else "completed"),
        "passed": bool(rep.get("passed", False)),
        "math_backend": rep.get("math_backend"),
        "sources": rep.get("documents_scanned", []),
        "artifacts": artifacts,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "methodology_coverage": rep.get("methodology_coverage", {}),
        "findings": rep.get("findings", []),
        "uncertainty": rep.get("uncertainty", []),
        "gaps": rep.get("gaps", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pure-stdlib offline StatCheck, CI/GRIM/Pseudoreplication, CSV, and 5-facet methods auditor.")
    parser.add_argument("--input", required=True, help="Input directory or manuscript file.")
    parser.add_argument("--out-json", required=True, help="Output JSON audit path.")
    parser.add_argument("--out-md", default="", help="Optional Markdown review report path.")
    parser.add_argument("--out-result-json", default="", help="Optional dual-compatible result.json contract path.")
    parser.add_argument("--case-id", default="", help="Optional explicit case_id (auto-read from <input>/case.json if omitted).")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if critical/major findings exist.")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    rep = audit_methods_and_stats(input_path)
    out_j = Path(args.out_json)
    io_core.write_json_atomic(out_j, rep)

    out_m: Path | None = None
    if args.out_md:
        out_m = Path(args.out_md)
        out_m.parent.mkdir(parents=True, exist_ok=True)
        out_m.write_text(render_markdown_report(rep), encoding="utf-8")

    if args.out_result_json:
        out_r = Path(args.out_result_json)
        cid = _resolve_case_id(input_path, args.case_id)
        contract_payload = _build_result_contract(rep, case_id=cid, out_result=out_r, out_json=out_j, out_md=out_m)
        io_core.write_json_atomic(out_r, contract_payload)

    summary = {
        "status": rep.get("status"),
        "audit_status": rep.get("audit_status"),
        "math_backend": rep.get("math_backend"),
        "passed": rep.get("passed"),
        "statcheck_count": rep.get("statcheck_count", 0),
        "effect_ci_checks_count": rep.get("effect_ci_checks_count", 0),
        "csv_checks_count": len(rep.get("csv_checks", [])),
        "findings_count": rep.get("findings_count", 0),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if not rep.get("documents_scanned"):
        return 1
    if args.strict and not rep.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
