#!/usr/bin/env python3
"""Zero-dependency offline citation verification, three-way reconciliation, and atomic claim fact-checker.

Designed for challenge-cbe96f01 in an offline Python 3.12 container (python:3.12-slim --network none).
Capabilities:
1. Multi-system citation extraction: numeric [n]/[1-3, 5], LaTeX \\cite{k}, Author-Year, DOI/PMID/arXiv.
2. Three-way reconciliation: in-text marker counts vs bibliography entries vs actual resolved source objects
   (captures duplicate bibliography numbers, dangling citations, uncited entries, and many-to-one source collapse).
3. Multi-line bibliography reconstruction for wrapped PDF references (preserves DOIs and retraction markers).
4. Atomic sub-claim decomposition of compound cited sentences + clinical-polarity-aware offline alignment.
5. Five-state semantic support grading:
   - supported_by_source
   - partially_supported
   - contradicted_by_source
   - source_not_provided
   - doi_or_metadata_only_unsupported
6. Retraction / erratum / preprint version alerts, bare prior-literature numeric claims, cross-section
   Abstract vs Methods/Results numerical reconciliation, and direct dual-compatible result.json export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_io_core as io_core  # noqa: E402

# ---------------------------------------------------------------------------
# Regex definitions for multi-system citations, identifiers, and evidence cues
# ---------------------------------------------------------------------------

NUM_BRACKET_RE = re.compile(r"(?<![A-Za-z0-9_])\[(\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*)\](?!\()")
TEX_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
# Parenthetical Author-Year: (Smith et al., 2021; Jones & Lee, 2020a) or (张伟等, 2022)
PAREN_AY_GROUP_RE = re.compile(
    r"\(([^()]{4,240}?(?:19|20)\d{2}[a-z]?(?:\s*;\s*[^()]{4,160}?(?:19|20)\d{2}[a-z]?)*)\)"
)
AY_ITEM_EN_RE = re.compile(
    r"(?:(?:see|e\.g\.|cf\.|reviewed\s+in)\s+)?"
    r"([A-Z][A-Za-z'\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+|\s+et\s+al\.?)?)\s*,?\s*((?:19|20)\d{2}[a-z]?)"
)
AY_ITEM_ZH_RE = re.compile(r"([\u4e00-\u9fff]{2,4}(?:等|和[\u4e00-\u9fff]{2,4})?)\s*[,，]\s*((?:19|20)\d{2}[a-z]?)")
NARRATIVE_AY_RE = re.compile(
    r"\b([A-Z][A-Za-z'\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+|\s+et\s+al\.?))\s*\(((?:19|20)\d{2}[a-z]?)\)"
)

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
PMID_RE = re.compile(r"\bPMID\s*[:#]?\s*(\d{6,9})\b", re.I)
ARXIV_RE = re.compile(r"\barXiv\s*[:/]?\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)

BIB_NUM_ENTRY_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")
BIBTEX_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.I)
BIBITEM_RE = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}\s*(.*)")

RETRACTION_RE = re.compile(
    r"\b(?:retracted|retraction\s+notice|withdrawn\s+(?:article|paper|from\s+publication))\b|"
    r"已撤稿|撤稿声明|撤回稿件",
    re.I,
)
ERRATUM_RE = re.compile(
    r"\b(?:erratum|corrigendum|author\s+correction|publisher\s+correction|expression\s+of\s+concern)\b|勘误|更正声明|关切声明",
    re.I,
)
PREPRINT_RE = re.compile(r"\b(?:arXiv|bioRxiv|medRxiv|chemRxiv|SSRN|Research\s+Square|preprint)\b|预印本", re.I)
PEER_REVIEWED_CLAIM_RE = re.compile(
    r"\b(?:peer[- ]reviewed|published\s+in\s+(?:Nature|Science|Cell|Lancet|NEJM|JAMA)|established\s+clinical\s+standard)\b|"
    r"同行评审|已正式发表",
    re.I,
)

PRIOR_LIT_CUE_RE = re.compile(
    r"\b(?:previous\s+stud(?:y|ies)|prior\s+(?:work|stud(?:y|ies)|research|reports?)|earlier\s+(?:work|stud(?:y|ies)|reports?)|"
    r"literature\s+(?:shows?|reports?|indicates?|suggests?)|it\s+has\s+been\s+(?:reported|shown|demonstrated)\s+that)\b|"
    r"前人研究|既往研究|先前研究|已有文献|文献报道|此前研究表明",
    re.I,
)
QUANT_EMPIRICAL_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?[- ]fold\b|\b(?:OR|HR|RR|AUC|r)\s*=\s*\d+(?:\.\d+)?|\b\d{2,6}\s+(?:patients|samples|participants|cases|mice)\b|"
    r"\d+(?:\.\d+)?%|提高\d+|降低\d+|约\d+例",
    re.I,
)
LOCAL_STUDY_RE = re.compile(
    r"\b(?:our\s+(?:study|data|results?|experiments?|analysis|cohort|model|findings?)|we\s+(?:found|observed|measured|show|report|enrolled|recruited)|"
    r"in\s+the\s+present\s+study|in\s+this\s+(?:study|paper|work)|Table\s+\d+|Fig(?:ure)?\.?\s*\d+)\b|"
    r"本研究|本文|我们的(?:研究|实验|结果|数据)|表\s*\d+|图\s*\d+",
    re.I,
)
LIMITATION_HYPOTHESIS_RE = re.compile(
    r"\b(?:limitation|we\s+hypothesize|hypothetical|future\s+work\s+(?:should|will)|assuming\s+that|contrary\s+to\s+our\s+expectation|"
    r"negative\s+control|ablation\s+study)\b|局限性|我们假设|未来工作|阴性对照|消融实验",
    re.I,
)

STRONG_CAUSAL_RE = re.compile(
    r"\b(?:proves?|proved|proven|causes?|caused|causing|directly\s+(?:causes?|drives?|induces?|prevents?|reduces?|increases?)|"
    r"eliminates?|cures?|establishes?\s+causality|causally\s+(?:drives?|determines?|reduces?|increases?)|guarantees?)\b|"
    r"直接导致|证实引起|证明了.*因果|必然引起|根治|消除",
    re.I,
)
ASSOC_CUE_RE = re.compile(
    r"\b(?:associat(?:ed|ion)|correlat(?:ed|ion)|link(?:ed)?\s+to|observational|cross[- ]sectional|retrospective|"
    r"cannot\s+establish\s+causality|no\s+causal\s+(?:effect|inference)|suggests?\s+a\s+possible|linked\s+with)\b|"
    r"相关|关联|伴随|观察性|横断面|回顾性|不能确定因果",
    re.I,
)
INCREASE_RE = re.compile(
    r"\b(?:increas(?:e[sd]?|ing)|improv(?:e[sd]?|ing)|elevat(?:e[sd]?|ing)|enhanc(?:e[sd]?|ing)|boost(?:ed|s|ing)?|"
    r"higher|greater|outperform(?:ed|s)?|upregulat(?:ed|ion)|prolong(?:ed|s)?)\b|升高|增加|提高|增强|改善|上调|优于",
    re.I,
)
DECREASE_RE = re.compile(
    r"\b(?:decreas(?:e[sd]?|ing)|reduc(?:e[sd]?|ing|tion)|declin(?:e[sd]?|ing)|lower(?:ed|s)?|suppress(?:ed|es|ing)?|"
    r"inhibit(?:ed|s|ing)?|downregulat(?:ed|ion)|worsen(?:ed|s)?|impair(?:ed|s)?)\b|下降|降低|减少|抑制|下调|恶化|削弱",
    re.I,
)
NULL_NEG_RE = re.compile(
    r"\b(?:no\s+(?:statistically\s+)?significant\s+(?:difference|effect|association|improvement|reduction|benefit)|"
    r"did\s+not\s+(?:significantly\s+)?(?:increase|decrease|reduce|improve|differ|affect)|"
    r"failed\s+to\s+(?:find|show|demonstrate|improve|reduce)|null\s+effect|not\s+associated\s+with)\b|"
    r"无显著差异|没有显著(?:差异|影响|改善|降低)|未发现显著|无统计学意义",
    re.I,
)
CONDITION_ANIMAL_VITRO_RE = re.compile(
    r"\b(?:in\s+vitro|cell\s+lines?|cultured\s+cells?|in\s+mice|murine|mouse\s+model|rat\s+model|animal\s+model|"
    r"xenograft|preclinical|pilot\s+(?:study|cohort)|preliminary|phase\s*I\b|subgroup\s+only|older\s+adults\s+only|"
    r"retrospective\s+single[- ]center)\b|体外|细胞系|小鼠|大鼠|动物模型|临床前|预实验|亚组",
    re.I,
)
HUMAN_GENERAL_RE = re.compile(
    r"\b(?:human\s+patients?|clinical\s+patients?|in\s+patients|clinical\s+practice|general\s+population|all\s+adults|"
    r"routinely\s+prevents|universally)\b|患者|临床病人|人类临床|普通人群",
    re.I,
)

STOPWORDS = {
    "the", "and", "for", "that", "with", "from", "this", "were", "have", "has", "been", "are", "was", "their",
    "they", "them", "these", "those", "which", "while", "where", "when", "into", "over", "under", "between",
    "among", "after", "before", "during", "using", "used", "based", "study", "studies", "results", "result",
    "showed", "shown", "reported", "found", "data", "analysis", "method", "methods", "also", "such", "than",
    "more", "less", "most", "both", "each", "other", "some", "many", "only", "very", "well", "can", "may",
    "might", "could", "would", "should", "not", "but", "our", "we", "et", "al", "doi", "http", "https", "org",
    "fig", "figure", "table", "section", "page", "ref", "refs", "pp", "vol", "no",
}

AY_NON_AUTHOR_WORDS = {
    "fig", "figure", "table", "eq", "equation", "section", "chapter", "model", "models", "group", "groups",
    "phase", "stage", "cohort", "cohorts", "trial", "trials", "version", "april", "may", "june", "july",
    "august", "march", "january", "february", "september", "october", "november", "december", "covid",
    "sars", "hiv", "dna", "rna", "usa", "who", "fda", "nih", "iso", "ieee", "acm", "total", "mean",
    "median", "range", "sd", "iqr", "ci", "p", "n",
    # Journals, publishers, repositories, and common scientific section/entity nouns
    "nature", "lancet", "science", "cell", "nejm", "jama", "bmj", "pnas", "plos", "elife", "bmc",
    "frontiers", "springer", "elsevier", "wiley", "oxford", "cambridge", "journal", "annals", "archives",
    "proceedings", "medicine", "oncology", "neurology", "cardiology", "surgery", "clinical", "medical",
    "biological", "molecular", "biochemical", "communications", "reports", "letters", "review", "reviews",
    "baseline", "dataset", "datasets", "data", "control", "controls", "study", "studies", "method",
    "methods", "result", "results", "analysis", "analyses", "experiment", "experiments", "sample", "samples",
    "patient", "patients", "subject", "subjects", "case", "cases", "hospital", "center", "centre",
    "university", "institute", "department", "national", "international", "global", "world", "health",
    "cancer", "tumor", "tumour", "disease", "syndrome", "therapy", "treatment", "drug", "placebo",
    "score", "scores", "scale", "index", "rate", "ratio", "risk", "survival", "mortality", "outcome",
    "outcomes", "endpoint", "endpoints", "summary", "abstract", "introduction", "discussion", "conclusion",
    "appendix", "supplement", "supplementary", "protocol", "guideline", "guidelines", "criteria", "standard",
    "release", "update", "edition", "series", "part", "type", "class", "grade", "level", "tier", "step",
    "test", "assay", "panel", "array", "matrix", "network", "system", "platform", "pipeline", "framework",
    "software", "package", "library", "database", "repository", "registry", "archive", "preprint", "arxiv",
    "biorxiv", "medrxiv", "ssrn", "pubmed", "medline", "embase", "cochrane", "scopus", "google", "scholar",
    "american", "british", "european", "new", "york", "london", "beijing", "shanghai", "boston", "united",
    "states", "kingdom", "china", "europe", "america", "africa", "asia",
}

BRACKET_NON_CITE_CTX_RE = re.compile(
    r"\b(?:scale|scores?|rang(?:e|ing)|ci|confidence\s+interval|interval|iqr|interquartile|likert|"
    r"point(?:s)?\s+scale|rated|scored|grade|tier|level|bin(?:s)?|quartile(?:s)?|decile(?:s)?|"
    r"values?|limits?|bounds?|from|between)\b|量表|评分|区间|范围|置信区间",
    re.I,
)

# Clinical outcome polarity tables to avoid false contradictions on equivalent antonyms
# e.g., "improved survival" (increase + positive) == "reduced mortality" (decrease + negative) -> beneficial_outcome
POS_TARGET_METRIC_RE = re.compile(
    r"\b(?:survival|overall\s+survival|progression[- ]free\s+survival|response(?:\s+rate)?|accuracy|"
    r"sensitivity|specificity|remission|recovery|efficacy|benefit(?:s)?|adherence|precision|recall|"
    r"auc|f1|viability|clearance|protection|tolerability|cure|function(?:al)?)\b|"
    r"存活|生存(?:率|期)?|缓解|有效率|准确率|灵敏度|特异度|恢复|获益|疗效|依从性",
    re.I,
)
NEG_TARGET_METRIC_RE = re.compile(
    r"\b(?:mortality|death(?:s)?|relapse|recurrence|error(?:s)?|loss|risk(?:s)?|toxicity|"
    r"adverse(?:\s+events?)?|incidence|progression|failure|fatality|lethality|morbidity|"
    r"complication(?:s)?|hospitalization|readmission|infection|inflammation|burden|hazard|bleeding)\b|"
    r"死亡(?:率)?|病死(?:率)?|复发|误差|损失|风险|毒性|不良反应|发病率|进展|失败|并发症|炎症",
    re.I,
)

STAT_PCT_CONST_RE = re.compile(
    r"(?:\b(?:90|95|99)\s*%\s*(?:ci\b|confidence\s+interval|credible\s+interval|prediction\s+interval)[^);.\n]{0,42}|"
    r"\b(?:80|85|90|95)\s*%\s*(?:statistical\s+)?power\b|"
    r"\b(?:1|5)\s*%\s*(?:significance|alpha|false\s+positive))",
    re.I,
)

METRIC_SHORT_TOKENS = {
    "n", "or", "hr", "rr", "md", "sd", "se", "ci", "iqr", "auc", "roc", "f1", "pfs", "os", "orr",
    "age", "bmi", "bp", "sbp", "dbp", "ic50", "ec50", "ld50", "rmse", "mae", "r2",
}


def _clean_doi(raw: str) -> str:
    return raw.strip().lower().rstrip(".,;)]}")


def _norm_surname(name: str) -> str:
    s = re.sub(r"\bet\s+al\.?", "", name, flags=re.I)
    s = re.split(r"\s+(?:and|&)\s+|[,，]|等|和", s)[0].strip()
    s = re.sub(r"[^A-Za-z\u4e00-\u9fff'\-]", "", s).lower()
    return s


def _make_ay_key(author_chunk: str, year: str) -> str:
    return f"{_norm_surname(author_chunk)}_{year.lower()}"


def _expand_num_list(spec: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,;]", spec):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-–—]\s*(\d+)$", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if 1 <= lo <= hi and (hi - lo) <= 60:
                out.extend(str(k) for k in range(lo, hi + 1))
        elif part.isdigit() and int(part) >= 1:
            out.append(str(int(part)))
    return out


def _content_tokens(text: str) -> set[str]:
    en = {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text) if t.lower() not in STOPWORDS}
    zh = {t for t in re.findall(r"[\u4e00-\u9fff]{2,4}", text)}
    return en | zh


def _metric_context_tokens(text: str, start: int, end: int, window: int = 42) -> set[str]:
    """Extract adjacent domain/metric indicator tokens around a numeric span for same-metric alignment."""
    ctx = text[max(0, start - window):min(len(text), end + window)]
    toks = _content_tokens(ctx)
    for w in re.findall(r"\b[A-Za-z][A-Za-z0-9]{0,5}\b", ctx):
        wl = w.lower()
        if wl in METRIC_SHORT_TOKENS:
            toks.add(wl)
    return toks


def _extract_claim_numbers(text: str) -> list[dict[str, Any]]:
    """Extract percentages, fold-changes, effect metrics, and counts while excluding years, citation indices, and CI/power constants."""
    cleaned = NUM_BRACKET_RE.sub(" ", text)
    cleaned = TEX_CITE_RE.sub(" ", cleaned)
    cleaned = DOI_RE.sub(" ", cleaned)
    cleaned = PMID_RE.sub(" ", cleaned)
    cleaned = ARXIV_RE.sub(" ", cleaned)
    cleaned = STAT_PCT_CONST_RE.sub(" ", cleaned)
    nums: list[dict[str, Any]] = []
    for m in re.finditer(r"(?<![A-Za-z0-9_./])(-?\d+(?:\.\d+)?)\s*(%|percent|fold|-fold|x|mg|kg|mm|ml|μm|um|ms|years?|months?|weeks?|days?)?(?![A-Za-z0-9_])", cleaned, re.I):
        val = float(m.group(1))
        unit = (m.group(2) or "").lower().lstrip("-")
        # Skip likely publication years unless explicitly attached to a unit
        if not unit and 1900 <= val <= 2035 and "." not in m.group(1):
            continue
        # Skip tiny bare integers without units (e.g., "2 groups", "1 study") except percentages or decimals
        if not unit and "." not in m.group(1) and abs(val) < 10:
            continue
        nums.append({
            "val": val,
            "unit": unit,
            "raw": m.group(0).strip(),
            "ctx_tokens": _metric_context_tokens(cleaned, m.start(), m.end()),
        })
    return nums


def _detect_direction_profile(text: str) -> dict[str, Any]:
    """Classify raw directional verb and clinical outcome polarity so equivalent antonyms align cleanly."""
    if NULL_NEG_RE.search(text):
        return {"raw_dir": "null", "target_polarity": "none", "clinical_dir": "null", "targets": []}
    inc_m = INCREASE_RE.search(text)
    dec_m = DECREASE_RE.search(text)
    if inc_m and not dec_m:
        raw_dir = "increase"
        verb_pos = inc_m.start()
    elif dec_m and not inc_m:
        raw_dir = "decrease"
        verb_pos = dec_m.start()
    else:
        return {"raw_dir": "neutral", "target_polarity": "none", "clinical_dir": "neutral", "targets": []}

    pos_hits = [(abs(m.start() - verb_pos), m.group(0).lower()) for m in POS_TARGET_METRIC_RE.finditer(text)]
    neg_hits = [(abs(m.start() - verb_pos), m.group(0).lower()) for m in NEG_TARGET_METRIC_RE.finditer(text)]
    target_polarity = "unknown"
    targets: list[str] = []
    if pos_hits and not neg_hits:
        target_polarity = "positive"
        targets = sorted({t for _, t in pos_hits})
    elif neg_hits and not pos_hits:
        target_polarity = "negative"
        targets = sorted({t for _, t in neg_hits})
    elif pos_hits and neg_hits:
        best_pos = min(pos_hits, key=lambda x: x[0])
        best_neg = min(neg_hits, key=lambda x: x[0])
        if best_pos[0] + 4 < best_neg[0]:
            target_polarity = "positive"
            targets = [best_pos[1]]
        elif best_neg[0] + 4 < best_pos[0]:
            target_polarity = "negative"
            targets = [best_neg[1]]

    if target_polarity == "positive":
        clinical_dir = "beneficial_outcome" if raw_dir == "increase" else "adverse_outcome"
    elif target_polarity == "negative":
        clinical_dir = "beneficial_outcome" if raw_dir == "decrease" else "adverse_outcome"
    else:
        clinical_dir = f"ambiguous_{raw_dir}"

    return {
        "raw_dir": raw_dir,
        "target_polarity": target_polarity,
        "clinical_dir": clinical_dir,
        "targets": targets,
    }


def _detect_direction(text: str) -> str:
    return _detect_direction_profile(text)["clinical_dir"]


def _detect_causal_strength(text: str) -> str:
    if STRONG_CAUSAL_RE.search(text):
        return "causal"
    if ASSOC_CUE_RE.search(text):
        return "associational"
    return "neutral"


def _is_source_document(rel_path: str, text: str, *, has_primary_manuscript: bool = False) -> bool:
    low = rel_path.lower()
    if any(k in low for k in ("manuscript", "draft", "paper_main", "submission", "review_target")):
        return False
    parts = [p.lower() for p in Path(rel_path).parts]
    if any(p in {"sources", "references", "refs", "cited", "literature", "corpus_refs"} for p in parts[:-1]):
        return True
    name = Path(rel_path).stem.lower()
    if any(k in name for k in ("source", "ref_", "ref-", "cited", "reference_", "abstract_", "paper_ref", "literature_")):
        return True
    if re.match(r"^(?:ref|src|source|s|r)\d+", name):
        return True
    # Detect standalone source card header or DOI/metadata-headed short source outside sources/
    head_lines = [ln.strip() for ln in text.splitlines()[:15] if ln.strip()]
    head_low = "\n".join(head_lines).lower()
    if head_low.startswith(("source_id:", "reference_source:", "cited_source:", "source_record:")):
        return True
    if has_primary_manuscript:
        meta_hdr_hits = sum(
            1 for ln in head_lines[:10]
            if re.match(r"^(?:title|authors?|year|journal|venue|doi|pmid|arxiv|status|abstract)\s*:", ln, re.I)
        )
        has_ref_heading = bool(re.search(r"(?m)^(?:#{1,4}\s+|\\section\*?\{)?(?:references|bibliography|参考文献)\b", text, re.I))
        has_methods_results = (
            bool(re.search(r"(?m)^(?:#{1,4}\s+)?(?:methods|materials\s+and\s+methods)\b", text, re.I))
            and bool(re.search(r"(?m)^(?:#{1,4}\s+)?(?:results|findings)\b", text, re.I))
        )
        if not has_ref_heading and not has_methods_results:
            if meta_hdr_hits >= 2:
                return True
            if meta_hdr_hits >= 1 and (DOI_RE.search(head_low) or PMID_RE.search(head_low) or ARXIV_RE.search(head_low)):
                return True
            if len(text) <= 2200 and (DOI_RE.search(head_low) or PMID_RE.search(head_low) or ARXIV_RE.search(head_low)):
                return True
    return False


def _parse_bibtex(text: str, filename: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    matches = list(BIBTEX_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        key = m.group(2).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start():end]
        lno = text[:m.start()].count("\n") + 1
        title_m = re.search(r"\btitle\s*=\s*[\{\"](.+?)[\}\"]\s*,?", block, re.I | re.S)
        author_m = re.search(r"\bauthor\s*=\s*[\{\"](.+?)[\}\"]\s*,?", block, re.I | re.S)
        year_m = re.search(r"\byear\s*=\s*[\{\"]?((?:19|20)\d{2}[a-z]?)[\}\"]?", block, re.I)
        doi_m = DOI_RE.search(block)
        pmid_m = PMID_RE.search(block)
        arxiv_m = ARXIV_RE.search(block)
        raw_clean = re.sub(r"\s+", " ", block).strip()
        rec = {
            "key": key,
            "style": "bibtex",
            "file": filename,
            "line": lno,
            "raw": raw_clean[:400],
            "title": re.sub(r"[{}]", "", title_m.group(1)).strip() if title_m else "",
            "author": author_m.group(1).strip() if author_m else "",
            "year": year_m.group(1).strip() if year_m else "",
            "dois": [_clean_doi(doi_m.group(0))] if doi_m else [],
            "pmids": [pmid_m.group(1)] if pmid_m else [],
            "arxivs": [arxiv_m.group(1).lower()] if arxiv_m else [],
        }
        entries[key] = rec
        if rec["author"] and rec["year"]:
            ayk = _make_ay_key(rec["author"], rec["year"])
            entries.setdefault(ayk, rec)
    return entries


def _index_source_doc(doc: dict[str, Any]) -> dict[str, Any]:
    text = doc["text"]
    lines = doc["lines"]
    head = "\n".join(lines[:15])
    dois = sorted({_clean_doi(x) for x in DOI_RE.findall(head) or DOI_RE.findall(text[:3000])})
    pmids = sorted(set(PMID_RE.findall(head) or PMID_RE.findall(text[:3000])))
    arxivs = sorted({x.lower() for x in (ARXIV_RE.findall(head) or ARXIV_RE.findall(text[:3000]))})

    # Extract title / authors / year heuristics from header
    title = ""
    authors: list[str] = []
    years: list[str] = []
    for raw in lines[:12]:
        st = raw.strip()
        if not st or st.startswith("[Page "):
            continue
        if st.lower().startswith("title:"):
            title = st.split(":", 1)[1].strip()
        elif st.startswith("#") and not title:
            title = st.lstrip("#").strip()
        elif st.lower().startswith("authors:") or st.lower().startswith("author:"):
            achunk = st.split(":", 1)[1]
            for piece in re.split(r"[,;]| and | & ", achunk):
                s = _norm_surname(piece.strip())
                if len(s) >= 2:
                    authors.append(s)
        elif not title and len(st) > 15 and not re.match(r"^(?:doi|pmid|arxiv|year|journal|status)\s*:", st, re.I):
            title = st
        for ym in re.findall(r"\b((?:19|20)\d{2})\b", st):
            years.append(ym)
    if not authors:
        for raw in lines[:6]:
            for am in re.findall(r"\b([A-Z][A-Za-z'\-]{2,})\s+(?:et\s+al\.|[A-Z]\.)", raw):
                s = _norm_surname(am)
                if s and s not in AY_NON_AUTHOR_WORDS:
                    authors.append(s)

    # Check if this source file is metadata-only (no substantive body paragraphs)
    body_lines = []
    meta_prefix_re = re.compile(
        r"^(?:title|author|authors|year|journal|venue|doi|pmid|arxiv|url|status|source_id|type|publisher|volume|pages)\s*:",
        re.I,
    )
    for lno, raw in enumerate(lines, 1):
        st = raw.strip()
        if not st or st.startswith("[Page ") or st.startswith("#") or meta_prefix_re.match(st):
            continue
        body_lines.append((lno, st))
    substantive_chars = sum(len(s) for _, s in body_lines)
    metadata_only = substantive_chars < 90

    # Sentence index for evidence retrieval (merge wrapped PDF lines within body paragraphs)
    sentences: list[dict[str, Any]] = []
    body_sec = {
        "file": doc["file"],
        "title": "body",
        "role": "other",
        "start_line": body_lines[0][0] if body_lines else 1,
        "end_line": body_lines[-1][0] if body_lines else 1,
        "lines": body_lines,
        "text": "\n".join(st for _, st in body_lines),
    }
    for sent in io_core.split_sentences(body_sec):
        c = sent["text"]
        lno = sent["line"]
        if len(c) >= 15:
            dprof = _detect_direction_profile(c)
            sentences.append({
                "line": lno,
                "line_start": sent.get("line_start", lno),
                "line_end": sent.get("line_end", lno),
                "page": doc["page_map"].get(lno, 0),
                "text": c,
                "tokens": _content_tokens(c),
                "numbers": _extract_claim_numbers(c),
                "direction": dprof["clinical_dir"],
                "direction_profile": dprof,
                "causal": _detect_causal_strength(c),
            })

    low_text_pdf = bool(doc["pages"]) and sum(p["chars"] for p in doc["pages"]) < 100
    return {
        "file": doc["file"],
        "sha256": doc["sha256"],
        "dois": dois,
        "pmids": pmids,
        "arxivs": arxivs,
        "title": title,
        "title_tokens": _content_tokens(title),
        "authors": list(dict.fromkeys(authors)),
        "years": list(dict.fromkeys(years)),
        "metadata_only": metadata_only,
        "low_text_pdf": low_text_pdf,
        "substantive_chars": substantive_chars,
        "sentences": sentences,
        "all_tokens": _content_tokens(text),
        "retraction_marked": bool(RETRACTION_RE.search(text[:1500])),
        "erratum_marked": bool(ERRATUM_RE.search(text[:1500])),
        "preprint_marked": bool(PREPRINT_RE.search(text[:1500])),
    }


def _extract_citations_from_sentence(sentence: str, *, allow_numeric_brackets: bool = True) -> list[dict[str, str]]:
    """Extract all citation markers from a sentence across numeric, LaTeX, Author-Year, and explicit IDs."""
    cites: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    # 1. Numeric brackets [1], [1-3, 5] (skip scale/score/range/CI/interval expressions & non-numeric-bib ranges)
    for m in NUM_BRACKET_RE.finditer(sentence):
        spec = m.group(1)
        ctx = sentence[max(0, m.start() - 32):min(len(sentence), m.end() + 32)]
        if BRACKET_NON_CITE_CTX_RE.search(ctx):
            continue
        is_range = bool(re.search(r"[-–—]", spec))
        if not allow_numeric_brackets and (is_range or not re.search(r"\b(?:ref|refs|citation|cited)\b", ctx, re.I)):
            continue
        keys = _expand_num_list(spec)
        for k in keys:
            cites.append({"raw": m.group(0), "key": k, "style": "numeric", "start": m.start(), "end": m.end()})
            seen_keys.add(k)

    # 2. LaTeX \cite{k1, k2}
    for m in TEX_CITE_RE.finditer(sentence):
        for k in m.group(1).split(","):
            ks = k.strip()
            if ks:
                cites.append({"raw": m.group(0), "key": ks, "style": "latex", "start": m.start(), "end": m.end()})
                seen_keys.add(ks)

    # 3. Parenthetical Author-Year: (Smith et al., 2021; Jones & Lee, 2020)
    for m in PAREN_AY_GROUP_RE.finditer(sentence):
        inner = m.group(1)
        for seg in inner.split(";"):
            seg = seg.strip()
            hit = False
            for am in AY_ITEM_EN_RE.finditer(seg):
                raw_author = am.group(1).strip()
                author_words = [w.lower() for w in re.findall(r"[A-Za-z]+", raw_author)]
                surname = _norm_surname(raw_author)
                # Reject journals / generic scientific nouns such as "(Nature, 2022)", "(Baseline, 2021)", "(Table, 2020)"
                if (
                    surname
                    and len(surname) >= 2
                    and surname not in AY_NON_AUTHOR_WORDS
                    and not any(w in AY_NON_AUTHOR_WORDS for w in author_words if w not in {"and", "et", "al"})
                ):
                    k = f"{surname}_{am.group(2).lower()}"
                    cites.append({"raw": f"({seg})", "key": k, "style": "author_year", "start": m.start(), "end": m.end()})
                    seen_keys.add(k)
                    hit = True
            if not hit:
                for am in AY_ITEM_ZH_RE.finditer(seg):
                    k = _make_ay_key(am.group(1), am.group(2))
                    cites.append({"raw": f"({seg})", "key": k, "style": "author_year", "start": m.start(), "end": m.end()})
                    seen_keys.add(k)

    # 4. Narrative Author-Year: Smith et al. (2021)
    for m in NARRATIVE_AY_RE.finditer(sentence):
        raw_author = m.group(1).strip()
        author_words = [w.lower() for w in re.findall(r"[A-Za-z]+", raw_author)]
        surname = _norm_surname(raw_author)
        if (
            surname
            and len(surname) >= 2
            and surname not in AY_NON_AUTHOR_WORDS
            and not any(w in AY_NON_AUTHOR_WORDS for w in author_words if w not in {"and", "et", "al"})
        ):
            k = f"{surname}_{m.group(2).lower()}"
            if k not in seen_keys:
                cites.append({"raw": m.group(0), "key": k, "style": "author_year", "start": m.start(), "end": m.end()})
                seen_keys.add(k)

    # 5. Inline DOI / PMID / arXiv when not already covered
    for m in DOI_RE.finditer(sentence):
        d = _clean_doi(m.group(0))
        if d not in seen_keys:
            cites.append({"raw": m.group(0), "key": f"doi:{d}", "style": "doi", "start": m.start(), "end": m.end()})
            seen_keys.add(d)
    for m in PMID_RE.finditer(sentence):
        pk = f"pmid:{m.group(1)}"
        if pk not in seen_keys:
            cites.append({"raw": m.group(0), "key": pk, "style": "pmid", "start": m.start(), "end": m.end()})
            seen_keys.add(pk)
    for m in ARXIV_RE.finditer(sentence):
        ak = f"arxiv:{m.group(1).lower()}"
        if ak not in seen_keys:
            cites.append({"raw": m.group(0), "key": ak, "style": "arxiv", "start": m.start(), "end": m.end()})
            seen_keys.add(ak)

    return cites


def _decompose_atomic_subclaims(sentence: str, *, allow_numeric_brackets: bool = True) -> list[dict[str, Any]]:
    """Split compound cited sentences into atomic sub-claims bound to their local citation keys."""
    # Protect parentheses when splitting on semicolons so "(Smith, 2020; Lee, 2021)" is not split mid-parenthesis.
    protected_chars: list[str] = []
    depth = 0
    for ch in sentence:
        if ch in "([":
            depth += 1
        elif ch in ")]" and depth > 0:
            depth -= 1
        if ch in ";；" and depth > 0:
            protected_chars.append("<SEMI>")
        else:
            protected_chars.append(ch)
    prot = "".join(protected_chars)

    # Split on top-level semicolons and explicit contrast/compound conjunctions
    raw_clauses = re.split(
        r"\s*[;；]\s+|\s*,\s+(?=(?:whereas|while|although|however|in\s+contrast|furthermore|moreover)\b)|"
        r"\s+(?=whereas\b|while\b)|[，,]\s*(?=而|但|同时|此外|相比之下)",
        prot,
        flags=re.I,
    )

    # Further split ", and " only when the left clause already carries a citation marker (distinct attributed sub-claims)
    refined: list[str] = []
    for cl in raw_clauses:
        cl_restored = cl.replace("<SEMI>", ";").strip(" ,;；")
        if not cl_restored:
            continue
        subparts = re.split(r",\s+and\s+", cl_restored)
        if len(subparts) > 1 and any(_extract_citations_from_sentence(p, allow_numeric_brackets=allow_numeric_brackets) for p in subparts[:-1]):
            refined.extend(p.strip() for p in subparts if p.strip())
        else:
            refined.append(cl_restored)

    sentence_cites = _extract_citations_from_sentence(sentence, allow_numeric_brackets=allow_numeric_brackets)
    subclaims: list[dict[str, Any]] = []
    for idx, cl in enumerate(refined, 1):
        local_cites = _extract_citations_from_sentence(cl, allow_numeric_brackets=allow_numeric_brackets)
        inherited = False
        if not local_cites and sentence_cites:
            # If clause has no own citation, inherit sentence-trailing citations only if this clause shares the sentence's attribution
            local_cites = sentence_cites
            inherited = True
        if not local_cites:
            continue
        dprof = _detect_direction_profile(cl)
        subclaims.append({
            "subclaim_idx": idx,
            "text": cl,
            "citations": local_cites,
            "inherited_citations": inherited,
            "numbers": _extract_claim_numbers(cl),
            "direction": dprof["clinical_dir"],
            "direction_profile": dprof,
            "causal_strength": _detect_causal_strength(cl),
            "tokens": _content_tokens(cl),
        })
    return subclaims


def _match_bib_to_source(key: str, bib: dict[str, Any] | None, sources: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """Align a bibliography entry or citation key to an offline supplied source document."""
    if not sources:
        return None, "none"

    key_low = key.lower()
    # 1. Exact DOI / PMID / arXiv match
    bib_dois = set(bib.get("dois", [])) if bib else set()
    bib_pmids = set(bib.get("pmids", [])) if bib else set()
    bib_arxivs = set(bib.get("arxivs", [])) if bib else set()
    if key_low.startswith("doi:"):
        bib_dois.add(key_low[4:])
    if key_low.startswith("pmid:"):
        bib_pmids.add(key_low[5:])
    if key_low.startswith("arxiv:"):
        bib_arxivs.add(key_low[6:])

    for src in sources:
        if bib_dois and (bib_dois & set(src["dois"])):
            return src, "exact_doi"
        if bib_pmids and (bib_pmids & set(src["pmids"])):
            return src, "exact_pmid"
        if bib_arxivs and (bib_arxivs & set(src["arxivs"])):
            return src, "exact_arxiv"

    # 2. Author + Year match
    ay_surname = ""
    ay_year = ""
    if "_" in key_low and re.search(r"(?:19|20)\d{2}", key_low):
        parts = key_low.rsplit("_", 1)
        ay_surname, ay_year = parts[0], re.sub(r"[a-z]$", "", parts[1])
    elif bib:
        ay_surname = _norm_surname(bib.get("author", "") or bib.get("raw", ""))
        ym = re.search(r"\b((?:19|20)\d{2})\b", bib.get("year", "") or bib.get("raw", ""))
        ay_year = ym.group(1) if ym else ""

    if ay_surname and ay_year:
        candidates = [s for s in sources if ay_surname in s["authors"] and ay_year in s["years"]]
        if len(candidates) == 1:
            return candidates[0], "author_year"
        if len(candidates) > 1 and bib:
            # Disambiguate by title token overlap
            btok = _content_tokens(bib.get("title") or bib.get("raw", ""))
            best = max(candidates, key=lambda s: len(btok & s["title_tokens"]))
            return best, "author_year_title"

    # 3. Title overlap match
    if bib:
        btok = _content_tokens(bib.get("title") or bib.get("raw", ""))
        if len(btok) >= 3:
            best_src = None
            best_ratio = 0.0
            for src in sources:
                st = src["title_tokens"]
                if not st:
                    continue
                inter = len(btok & st)
                ratio = inter / max(min(len(btok), len(st)), 1)
                if inter >= 3 and ratio >= 0.50 and ratio > best_ratio:
                    best_ratio = ratio
                    best_src = src
            if best_src is not None:
                return best_src, "title_overlap"

    # 4. Filename explicit key match (e.g., ref_1.md, source_3.txt, smith2021.md)
    norm_key_file = re.sub(r"[^a-z0-9]", "", key_low)
    for src in sources:
        stem = Path(src["file"]).stem.lower()
        if key.isdigit() and re.match(rf"^(?:ref|src|source|paper|cited|r|s)[_\-]?0*{re.escape(key)}(?:$|[^0-9])", stem):
            return src, "filename_index"
        if not key.isdigit() and norm_key_file and norm_key_file == re.sub(r"[^a-z0-9]", "", stem):
            return src, "filename_key"

    return None, "none"


def _evaluate_subclaim_against_source(
    subclaim: dict[str, Any],
    bib: dict[str, Any] | None,
    src: dict[str, Any] | None,
    match_method: str,
) -> dict[str, Any]:
    """Assign exact five-state support verdict with source sentence provenance."""
    if src is None or src.get("low_text_pdf"):
        reason = (
            f"Matched source '{src['file']}' has unreadable low-text scanned PDF pages; cannot verify claim semantics."
            if (src and src.get("low_text_pdf"))
            else "No supplied source full text or abstract matched this citation; absence of source file is not evidence of fabrication."
        )
        return {
            "support_status": "source_not_provided",
            "needs_human_review": True,
            "source_file": src["file"] if src else None,
            "source_location": None,
            "source_quote": "",
            "match_method": match_method,
            "rationale": reason,
        }

    if src.get("metadata_only") or not src.get("sentences"):
        return {
            "support_status": "doi_or_metadata_only_unsupported",
            "needs_human_review": True,
            "source_file": src["file"],
            "source_location": f"{src['file']}:L1",
            "source_quote": src.get("title", "")[:180],
            "match_method": match_method,
            "rationale": (
                "Supplied source record contains only bibliographic metadata (DOI/author/title) without substantive "
                "abstract or body passages; metadata matching alone cannot substantiate the attributed claim."
            ),
        }

    claim_toks = subclaim["tokens"]
    # Rank candidate source sentences by content-token overlap + numeric bonus
    scored: list[tuple[float, dict[str, Any]]] = []
    for sent in src["sentences"]:
        inter = claim_toks & sent["tokens"]
        overlap = len(inter)
        denom = max(min(len(claim_toks), len(sent["tokens"])), 1)
        score = overlap / denom + 0.08 * overlap
        # Slight boost if both carry percentages or units
        if subclaim["numbers"] and sent["numbers"]:
            score += 0.15
        scored.append((score, sent))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_sent = scored[0]
    doc_overlap = len(claim_toks & src["all_tokens"])

    loc_str = f"{src['file']}:L{best_sent['line']}" + (f"#page={best_sent['page']}" if best_sent.get("page") else "")

    # Topic mismatch: DOI/author matched, but source content does not discuss the claimed topic at all
    if doc_overlap < 2 or (best_score < 0.14 and len(claim_toks & best_sent["tokens"]) < 2):
        return {
            "support_status": "doi_or_metadata_only_unsupported",
            "needs_human_review": False,
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": (
                f"Citation metadata aligned via {match_method} to '{src['file']}', but topical overlap with the "
                f"sub-claim is near zero (shared terms={doc_overlap}); source content does not support this claim."
            ),
        }

    # Gather top-2 sentences to handle multi-sentence source context (e.g., limitation in adjacent sentence)
    top_sents = [s for sc, s in scored[:2] if sc >= max(0.14, best_score * 0.65)]
    combined_src_text = " ".join(s["text"] for s in top_sents)
    combined_src_nums = [n for s in top_sents for n in s["numbers"]]
    src_dprof = best_sent.get("direction_profile") or _detect_direction_profile(best_sent["text"])
    claim_dprof = subclaim.get("direction_profile") or _detect_direction_profile(subclaim["text"])
    src_dir = src_dprof["clinical_dir"]
    claim_dir = claim_dprof["clinical_dir"]

    # 1. Check direct numerical contradiction (prevent blind cross-metric comparison on unitless numbers)
    num_mismatches: list[str] = []
    num_matched = 0
    comparable_claim_nums = 0
    for cn in subclaim["numbers"]:
        same_unit = [sn for sn in combined_src_nums if sn["unit"] == cn["unit"]]
        if not same_unit:
            continue
        cn_ctx = cn.get("ctx_tokens", set())
        if cn["unit"] == "":
            # Unitless numbers MUST share at least one adjacent metric/entity token (e.g. 'n', 'auc', 'age', 'patients')
            candidates = [sn for sn in same_unit if cn_ctx & sn.get("ctx_tokens", set())]
            if not candidates:
                continue
        else:
            # For percentages/explicit units, prefer same-metric candidates when multiple exist
            shared_ctx = [sn for sn in same_unit if cn_ctx & sn.get("ctx_tokens", set())]
            candidates = shared_ctx if shared_ctx else same_unit

        comparable_claim_nums += 1
        diffs = [abs(cn["val"] - sn["val"]) for sn in candidates]
        min_idx = min(range(len(candidates)), key=lambda i: diffs[i])
        closest = candidates[min_idx]
        diff = diffs[min_idx]
        rel_diff = diff / max(abs(closest["val"]), 1e-6)
        if diff <= 0.6 or rel_diff <= 0.04:
            num_matched += 1
        elif cn["unit"] in {"%", "percent", "fold"} or (diff >= 1.5 and rel_diff >= 0.12):
            num_mismatches.append(f"manuscript reports '{cn['raw']}' vs source '{closest['raw']}'")

    if num_mismatches:
        return {
            "support_status": "contradicted_by_source",
            "needs_human_review": False,
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": "Quantitative mismatch against aligned source passage: " + "; ".join(num_mismatches[:3]) + ".",
        }

    # 2. Check clinical-polarity-aware directional or null-vs-effect contradiction
    # Equivalent clinical antonyms ("improved survival" vs "reduced mortality") both map to 'beneficial_outcome'.
    is_null_conflict = (
        (claim_dir == "null" and src_dir not in {"null", "neutral"})
        or (src_dir == "null" and claim_dir not in {"null", "neutral"})
    )
    is_clinical_polarity_conflict = (
        (claim_dir == "beneficial_outcome" and src_dir == "adverse_outcome")
        or (claim_dir == "adverse_outcome" and src_dir == "beneficial_outcome")
    )
    if is_null_conflict or is_clinical_polarity_conflict:
        return {
            "support_status": "contradicted_by_source",
            "needs_human_review": False,
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": (
                f"Clinical/effect direction contradiction: manuscript asserts '{claim_dir}' "
                f"(raw={claim_dprof['raw_dir']}, target={claim_dprof['targets'] or 'unspecified'}) "
                f"whereas aligned source reports '{src_dir}' "
                f"(raw={src_dprof['raw_dir']}, target={src_dprof['targets'] or 'unspecified'})."
            ),
        }

    # 2b. Complementary clinical endpoints (e.g., "survival +25%" vs "mortality -25%"):
    # Same beneficial polarity prevents a false CRITICAL contradiction, but baselines differ so they are NOT numerically equivalent.
    if (
        claim_dir in {"beneficial_outcome", "adverse_outcome"}
        and claim_dir == src_dir
        and claim_dprof["raw_dir"] != src_dprof["raw_dir"]
        and claim_dprof["target_polarity"] != src_dprof["target_polarity"]
        and claim_dprof["target_polarity"] in {"positive", "negative"}
        and src_dprof["target_polarity"] in {"positive", "negative"}
    ):
        return {
            "support_status": "partially_supported",
            "needs_human_review": True,
            "downgraded_severity": "minor",
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": (
                f"Complementary clinical endpoints ({claim_dprof['raw_dir']} {claim_dprof['targets']} vs "
                f"{src_dprof['raw_dir']} {src_dprof['targets']}): directionally aligned in polarity (not a reversal), "
                "but distinct endpoint baselines are NOT numerically equivalent (e.g., survival +25% != mortality -25%); verify exact endpoint conversion."
            ),
        }

    # If raw directional verbs oppose but target metric polarity is uncertain on either side,
    # downgrade to needs_human_review instead of emitting a false CRITICAL contradiction.
    if (
        (claim_dprof["raw_dir"] == "increase" and src_dprof["raw_dir"] == "decrease")
        or (claim_dprof["raw_dir"] == "decrease" and src_dprof["raw_dir"] == "increase")
    ) and (claim_dprof["target_polarity"] == "unknown" or src_dprof["target_polarity"] == "unknown"):
        return {
            "support_status": "partially_supported",
            "needs_human_review": True,
            "downgraded_severity": "minor",
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": (
                f"Opposing directional verbs ('{claim_dprof['raw_dir']}' vs '{src_dprof['raw_dir']}') with "
                "unspecified or non-canonical target metric polarity; flagged for human review rather than hard contradiction."
            ),
        }

    # 3. Check partial support: causal inflation (observational/associational source -> causal claim)
    claim_causal = subclaim["causal_strength"]
    src_assoc = bool(ASSOC_CUE_RE.search(combined_src_text))
    src_causal = bool(STRONG_CAUSAL_RE.search(combined_src_text))
    if claim_causal == "causal" and src_assoc and not src_causal:
        return {
            "support_status": "partially_supported",
            "needs_human_review": False,
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": (
                "Causal overclaim: manuscript uses strong causal wording, whereas the aligned source reports "
                "an associational or observational finding without establishing causality."
            ),
        }

    # 4. Check partial support: omitted experimental condition / species / model boundary
    src_cond = CONDITION_ANIMAL_VITRO_RE.search(combined_src_text)
    claim_cond = CONDITION_ANIMAL_VITRO_RE.search(subclaim["text"])
    if src_cond and not claim_cond:
        if HUMAN_GENERAL_RE.search(subclaim["text"]) or claim_causal == "causal" or subclaim["numbers"]:
            return {
                "support_status": "partially_supported",
                "needs_human_review": False,
                "source_file": src["file"],
                "source_location": loc_str,
                "source_quote": best_sent["text"][:190],
                "match_method": match_method,
                "rationale": (
                    f"Condition/scope omission: source explicitly qualifies the result with '{src_cond.group(0)}', "
                    "which is omitted or generalized in the manuscript sub-claim."
                ),
            }

    # 5. Check partial support: multiple comparable claimed numbers but only subset corroborated
    if comparable_claim_nums >= 2 and 0 < num_matched < comparable_claim_nums:
        return {
            "support_status": "partially_supported",
            "needs_human_review": True,
            "source_file": src["file"],
            "source_location": loc_str,
            "source_quote": best_sent["text"][:190],
            "match_method": match_method,
            "rationale": (
                f"Only {num_matched}/{comparable_claim_nums} comparable numerical values were corroborated in the "
                "aligned source passage; verify remaining figures manually."
            ),
        }

    return {
        "support_status": "supported_by_source",
        "needs_human_review": False,
        "source_file": src["file"],
        "source_location": loc_str,
        "source_quote": best_sent["text"][:190],
        "match_method": match_method,
        "rationale": (
            f"Aligned source passage ({match_method}) corroborates topic, clinical direction ({claim_dir}), "
            f"and quantitative figures without causal inflation or scope distortion."
        ),
    }


def _inspect_structured_json_sources(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Detect structured citation/history JSON exports to reconcile in-text marker counts vs reference objects."""
    try:
        obj = json.loads(doc["text"])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    body_text = ""
    for k in ("response", "answer", "text", "content", "manuscript", "summary"):
        if isinstance(obj.get(k), str) and len(obj[k]) > 40:
            body_text = obj[k]
            break
    ref_list = None
    for k in ("references", "citations", "sources", "history_references", "citation_objects"):
        if isinstance(obj.get(k), list):
            ref_list = obj[k]
            break
    if not body_text or ref_list is None:
        return None
    return {
        "file": doc["file"],
        "body_text": body_text,
        "reference_objects": ref_list,
    }


def _reconcile_cross_section_facts(doc: dict[str, Any], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare Abstract vs Methods/Results total sample sizes and anchored percentages."""
    checks: list[dict[str, Any]] = []
    abs_secs = [s for s in sections if s["role"] == "abstract"]
    body_secs = [s for s in sections if s["role"] in {"methods", "results", "results_discussion", "conclusion"}]
    if not abs_secs or not body_secs:
        return checks

    total_n_re = re.compile(
        r"(?:\btotal(?:\s+of)?\s+|\benrolled\s+|\brecruited\s+|\bincluded\s+|\bcohort\s+of\s+|\b[Nn]\s*=\s*)"
        r"(\d{2,6})\b(?!\s*(?:%|mg|ml|mm|days?|weeks?|months?|years?))",
        re.I,
    )
    subgroup_cue_re = re.compile(r"\b(?:subgroup|group\s+[A-Za-z0-9]|arm\s+[A-Za-z0-9]|control|treatment|placebo|cohort\s+\d)\b|亚组|对照组|实验组", re.I)

    def extract_totals(sec_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        vals: list[dict[str, Any]] = []
        for s in sec_list:
            for sent in io_core.split_sentences(s):
                txt = sent["text"]
                for m in total_n_re.finditer(txt):
                    val = int(m.group(1))
                    if 1900 <= val <= 2035:
                        continue
                    # Ignore subgroup-qualified counts unless explicitly labeled "total"
                    span_ctx = txt[max(0, m.start() - 35):min(len(txt), m.end() + 35)]
                    if subgroup_cue_re.search(span_ctx) and "total" not in span_ctx.lower() and "总" not in span_ctx:
                        continue
                    vals.append({"val": val, "line": sent["line"], "section": s["title"], "quote": txt[:170]})
        return vals

    abs_ns = extract_totals(abs_secs)
    body_ns = extract_totals(body_secs)
    if abs_ns and body_ns:
        abs_max = max(abs_ns, key=lambda x: x["val"])
        body_vals = {x["val"] for x in body_ns}
        if abs_max["val"] not in body_vals:
            body_max = max(body_ns, key=lambda x: x["val"])
            checks.append({
                "check_type": "abstract_vs_body_total_N",
                "conflict": True,
                "abstract_value": abs_max["val"],
                "abstract_line": abs_max["line"],
                "abstract_quote": abs_max["quote"],
                "body_value": body_max["val"],
                "body_line": body_max["line"],
                "body_section": body_max["section"],
                "body_quote": body_max["quote"],
            })

    # Anchored metric percentage comparison (e.g. accuracy/sensitivity/specificity/survival/reduction)
    metric_pct_re = re.compile(
        r"\b(accuracy|sensitivity|specificity|precision|recall|f1|auc|survival\s+rate|response\s+rate|mortality|"
        r"reduction|efficacy|efficiency|selectivity)\b[^.;\n]{0,32}?(\d+(?:\.\d+)?)\s*%",
        re.I,
    )

    def extract_metrics(sec_list: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in sec_list:
            for sent in io_core.split_sentences(s):
                for m in metric_pct_re.finditer(sent["text"]):
                    mname = re.sub(r"\s+", " ", m.group(1).lower())
                    out[mname].append({
                        "val": float(m.group(2)),
                        "line": sent["line"],
                        "section": s["title"],
                        "quote": sent["text"][:170],
                    })
        return out

    abs_m = extract_metrics(abs_secs)
    body_m = extract_metrics(body_secs)
    for mname, a_items in abs_m.items():
        if mname not in body_m:
            continue
        b_items = body_m[mname]
        for ai in a_items:
            if not any(abs(ai["val"] - bi["val"]) <= 0.15 for bi in b_items):
                bi = b_items[0]
                checks.append({
                    "check_type": "abstract_vs_body_metric_pct",
                    "metric": mname,
                    "conflict": True,
                    "abstract_value": ai["val"],
                    "abstract_line": ai["line"],
                    "abstract_quote": ai["quote"],
                    "body_value": bi["val"],
                    "body_line": bi["line"],
                    "body_section": bi["section"],
                    "body_quote": bi["quote"],
                })
                break
    return checks


AY_BIB_START_EN_RE = re.compile(
    r"^(?:"
    r"[A-Z][A-Za-z'\-]+,\s+[A-Z](?:\.|[a-z'\-]+)"  # Garcia, R., ... / Smith, John
    r"|[A-Z][A-Za-z'\-]+\s+[A-Z]{1,2}(?:\.|,|\s+et\s+al\.)"  # Smith J, ... / Smith JA.
    r"|[A-Z][A-Za-z'\-]+\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z'\-]+|&\s+[A-Z][A-Za-z'\-]+)\s*\(?(?:19|20)\d{2}"
    r")"
)
AY_BIB_START_ZH_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}(?:[，,、]|等)")
BIB_CONT_PREFIX_RE = re.compile(
    r"^(?:doi\s*:|https?://|pmid\s*:|arxiv\s*:|pp?\.|vol\.|\(|\[|\d+[:;]\d+)",
    re.I,
)


def _is_new_ay_bib_start(raw: str, st: str, *, in_numbered_entry: bool) -> bool:
    """Return True only when a non-numbered line genuinely begins a new Author-Year reference entry."""
    if not st or len(st) < 20:
        return False
    # Indented lines are wrapped continuations of the previous reference entry
    if (len(raw) - len(raw.lstrip(" \t"))) >= 2:
        return False
    if st[0].islower() or BIB_CONT_PREFIX_RE.match(st):
        return False
    first_word_m = re.match(r"^([A-Za-z'\-]+|[\u4e00-\u9fff]{2,4})", st)
    if not first_word_m:
        return False
    first_word = first_word_m.group(1)
    if first_word.isascii() and first_word.lower() in AY_NON_AUTHOR_WORDS:
        return False
    # Must match an explicit author-list opening pattern (rejects journal continuation lines like "Nature Medicine. 2022;28:100-110")
    if AY_BIB_START_EN_RE.match(st) or AY_BIB_START_ZH_RE.match(st):
        # In a numbered bibliography, require explicit comma-initials or "(YYYY)" to break out into unnumbered Author-Year
        if in_numbered_entry:
            has_comma_init = bool(re.match(r"^[A-Z][A-Za-z'\-]+,\s+[A-Z]\.", st))
            has_paren_year = bool(re.search(r"\((?:19|20)\d{2}[a-z]?\)", st))
            return has_comma_init or has_paren_year or bool(AY_BIB_START_ZH_RE.match(st))
        return True
    return False


def _join_wrapped_bib_lines(parts: list[str]) -> str:
    merged = ""
    for piece in parts:
        p = piece.strip()
        if not p:
            continue
        if not merged:
            merged = p
        elif merged.endswith("-") and len(merged) >= 2 and merged[-2].isalpha() and p[0].islower():
            merged = merged[:-1] + p
        else:
            merged += " " + p
    return merged


def _parse_bibliography_section(doc: dict[str, Any], sections: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Extract multi-line merged bibliography entries from References sections and detect duplicate numbering."""
    bib_entries: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    seen_num_lines: dict[str, list[tuple[int, str]]] = defaultdict(list)

    ref_sections = [s for s in sections if s["role"] == "references"]
    bib_lines: list[tuple[int, str]] = []
    if ref_sections:
        for rs in ref_sections:
            bib_lines.extend(rs["lines"])
    else:
        in_bib = False
        for lno, raw in enumerate(doc["lines"], 1):
            st = raw.strip()
            if "\\begin{thebibliography}" in st:
                in_bib = True
                continue
            if "\\end{thebibliography}" in st:
                in_bib = False
                continue
            if in_bib:
                bib_lines.append((lno, raw))

    # Pass 1: group wrapped PDF continuation lines (including indented or blank-line-separated DOI/journal lines) into logical entries
    grouped: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    saw_blank = False

    def flush_cur() -> None:
        nonlocal cur
        if cur is not None:
            cur["body"] = _join_wrapped_bib_lines(cur["parts"])
            grouped.append(cur)
            cur = None

    for lno, raw in bib_lines:
        st = raw.strip()
        if not st or st.startswith("[Page "):
            saw_blank = True
            continue
        m_num = BIB_NUM_ENTRY_RE.match(st)
        m_tex = BIBITEM_RE.match(st)
        if m_num:
            flush_cur()
            saw_blank = False
            key = str(int(m_num.group(1) or m_num.group(2)))
            cur = {
                "style": "numeric",
                "key": key,
                "line_start": lno,
                "line_end": lno,
                "raw_first": st,
                "parts": [m_num.group(3).strip()],
            }
        elif m_tex:
            flush_cur()
            saw_blank = False
            cur = {
                "style": "latex",
                "key": m_tex.group(1).strip(),
                "line_start": lno,
                "line_end": lno,
                "raw_first": st,
                "parts": [m_tex.group(2).strip()],
            }
        else:
            in_num_or_tex = cur is not None and cur["style"] in {"numeric", "latex"}
            if _is_new_ay_bib_start(raw, st, in_numbered_entry=in_num_or_tex):
                flush_cur()
                saw_blank = False
                cur = {
                    "style": "author_year",
                    "key": "",
                    "line_start": lno,
                    "line_end": lno,
                    "raw_first": st,
                    "parts": [st],
                }
            elif cur is not None:
                # Continuation of current reference entry (handles wrapped title/journal/DOI/retraction lines, even across blank line/page break)
                cur["parts"].append(st)
                cur["line_end"] = lno
                saw_blank = False
            else:
                # Leading unnumbered line with no prior entry: accept only if it looks like an Author-Year reference
                ym = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", st)
                am = re.match(r"^([A-Z][A-Za-z'\-]+|[\u4e00-\u9fff]{2,4})", st)
                if ym and am and len(st) >= 25:
                    surname = _norm_surname(am.group(1))
                    if surname and surname not in AY_NON_AUTHOR_WORDS:
                        cur = {
                            "style": "author_year",
                            "key": "",
                            "line_start": lno,
                            "line_end": lno,
                            "raw_first": st,
                            "parts": [st],
                        }
                        saw_blank = False
    flush_cur()

    # Pass 2: build canonical bibliography records and detect duplicate numbering
    for item in grouped:
        style = item["style"]
        lno = item["line_start"]
        lend = item["line_end"]
        body = item["body"]
        if style == "numeric":
            key = item["key"]
            seen_num_lines[key].append((lno, item["raw_first"]))
            if len(seen_num_lines[key]) > 1:
                duplicates.append({
                    "key": key,
                    "file": doc["file"],
                    "first_line": seen_num_lines[key][0][0],
                    "duplicate_line": lno,
                    "first_entry": seen_num_lines[key][0][1][:160],
                    "duplicate_entry": body[:160],
                })
            rec = _build_bib_record(key, "numeric", doc["file"], lno, body, line_end=lend)
            bib_entries[key] = rec
            if rec["author_surname"] and rec["year"] and rec["author_surname"] not in AY_NON_AUTHOR_WORDS:
                bib_entries.setdefault(f"{rec['author_surname']}_{rec['year'].lower()}", rec)
        elif style == "latex":
            key = item["key"]
            rec = _build_bib_record(key, "latex", doc["file"], lno, body, line_end=lend)
            bib_entries[key] = rec
        else:
            ym = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", body)
            am = re.match(r"^([A-Z][A-Za-z'\-]+|[\u4e00-\u9fff]{2,4})", body)
            if ym and am and len(body) >= 25:
                surname = _norm_surname(am.group(1))
                if surname and surname not in AY_NON_AUTHOR_WORDS:
                    key = f"{surname}_{ym.group(1).lower()}"
                    rec = _build_bib_record(key, "author_year", doc["file"], lno, body, line_end=lend)
                    bib_entries[key] = rec
    return bib_entries, duplicates


def _build_bib_record(key: str, style: str, filename: str, lno: int, body: str, *, line_end: int | None = None) -> dict[str, Any]:
    dois = sorted({_clean_doi(d) for d in DOI_RE.findall(body)})
    pmids = sorted(set(PMID_RE.findall(body)))
    arxivs = sorted({a.lower() for a in ARXIV_RE.findall(body)})
    ym = re.search(r"\b((?:19|20)\d{2}[a-z]?)\b", body)
    year = ym.group(1) if ym else ""
    author_m = re.match(r"^([A-Z][A-Za-z'\-]+|[\u4e00-\u9fff]{2,4})", body)
    surname = _norm_surname(author_m.group(1)) if author_m else ""
    # Heuristic title extraction (quoted or between year and journal)
    title = ""
    qm = re.search(r"[\"“]([^\"”]{10,200})[\"”]", body)
    if qm:
        title = qm.group(1).strip()
    elif ym:
        after = body[ym.end():].lstrip(" ).,:;")
        title = after.split(".")[0].strip()[:180]
    return {
        "key": key,
        "style": style,
        "file": filename,
        "line": lno,
        "line_end": line_end or lno,
        "raw": body[:500],
        "title": title,
        "author": author_m.group(1) if author_m else "",
        "author_surname": surname,
        "year": year,
        "dois": dois,
        "pmids": pmids,
        "arxivs": arxivs,
        "retraction_marked": bool(RETRACTION_RE.search(body)),
        "erratum_marked": bool(ERRATUM_RE.search(body)),
        "preprint_marked": bool(PREPRINT_RE.search(body)),
    }


def verify_citations_and_facts(target: Path) -> dict[str, Any]:
    docs, gaps, _ = io_core.load_documents(
        target,
        extra_exts={".bib", ".json"},
        ignore_names={"case.json", "task.md", "readme.md"},
    )
    if not docs:
        return {
            "passed": False,
            "audit_status": "failed",
            "status": "failed",
            "documents_scanned": [],
            "findings": [],
            "gaps": gaps,
            "error": "No readable manuscript or reference files found.",
        }

    external_bib: dict[str, dict[str, Any]] = {}
    source_docs: list[dict[str, Any]] = []
    manuscripts: list[dict[str, Any]] = []
    structured_json_docs: list[dict[str, Any]] = []

    # Two-pass document classification: detect primary manuscripts first so DOI/abstract source cards
    # placed outside sources/ are still accurately indexed as source documents.
    remaining_docs: list[dict[str, Any]] = []
    for d in docs:
        if d["ext"] == ".bib":
            external_bib.update(_parse_bibtex(d["text"], d["file"]))
        elif d["ext"] == ".json":
            sj = _inspect_structured_json_sources(d)
            if sj is not None:
                structured_json_docs.append(sj)
            else:
                remaining_docs.append(d)
        else:
            remaining_docs.append(d)

    has_primary_ms = any(
        any(k in d["file"].lower() for k in ("manuscript", "draft", "paper_main", "submission", "review_target"))
        or len({s["role"] for s in io_core.split_sections(d) if s["role"] in {"abstract", "introduction", "methods", "results", "discussion", "references"}}) >= 2
        for d in remaining_docs
    )

    for d in remaining_docs:
        if len(remaining_docs) > 1 and _is_source_document(d["file"], d["text"], has_primary_manuscript=has_primary_ms):
            source_docs.append(_index_source_doc(d))
        else:
            manuscripts.append(d)

    if not manuscripts and not structured_json_docs:
        # Fallback: treat largest non-bib doc as manuscript
        non_bib = [d for d in docs if d["ext"] != ".bib"]
        if non_bib:
            biggest = max(non_bib, key=lambda x: len(x["text"]))
            manuscripts.append(biggest)
            source_docs = [s for s in source_docs if s["file"] != biggest["file"]]

    findings: list[dict[str, Any]] = []
    citation_matrix: list[dict[str, Any]] = []
    cross_section_all: list[dict[str, Any]] = []
    uncertainty: list[str] = []
    reconciliation_records: list[dict[str, Any]] = []
    fid = 0

    def add_finding(
        *,
        facet: str,
        finding_type: str,
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
    ) -> str:
        nonlocal fid
        fid += 1
        f_id = f"CIT-{fid:03d}"
        loc: dict[str, Any] = {"file": file, "section": section, "line_start": line_start, "line_end": line_end}
        if page:
            loc["page"] = page
        findings.append({
            "finding_id": f_id,
            "facet": facet,
            "finding_type": finding_type,
            "severity": severity,
            "location": loc,
            "verbatim_quote": verbatim_quote[:200],
            "evidence_basis": evidence_basis,
            "actionable_revision": actionable_revision,
            "impact_cost": {"impact": impact, "cost": cost},
            "status": status,
        })
        return f_id

    total_bib_entries = len({id(v) for v in external_bib.values()})
    total_markers = 0
    unique_cited_all: set[str] = set()
    six_facet_coverage_docs: list[dict[str, Any]] = []
    semantic_checklists: list[dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # A. Process primary manuscripts
    # -----------------------------------------------------------------------
    for doc in manuscripts:
        sections = io_core.split_sections(doc)
        local_bib, dup_bibs = _parse_bibliography_section(doc, sections)
        bib_entries = dict(external_bib)
        bib_entries.update(local_bib)
        local_unique_bib = {v["key"]: v for v in local_bib.values() if v["key"] == v.get("key")}
        total_bib_entries += len(local_unique_bib)

        # Report duplicate bibliography numbering immediately
        for dup in dup_bibs:
            add_finding(
                facet="citation_support",
                finding_type="factual_error",
                severity="major",
                file=doc["file"],
                line_start=dup["duplicate_line"],
                line_end=dup["duplicate_line"],
                page=doc["page_map"].get(dup["duplicate_line"], 0),
                section="References",
                verbatim_quote=dup["duplicate_entry"],
                evidence_basis=(
                    f"Duplicate bibliography index [{dup['key']}] appears at L{dup['first_line']} and L{dup['duplicate_line']}; "
                    "in-text markers pointing to this number are ambiguous."
                ),
                actionable_revision=f"Renumber the duplicate reference at L{dup['duplicate_line']} and reconcile all in-text [{dup['key']}] markers.",
                impact="high",
                cost="low",
            )

        body_sections = [s for s in sections if s["role"] not in {"references"}]
        has_numeric_bib = any(v.get("style") == "numeric" for v in bib_entries.values())
        has_any_bib = bool(bib_entries)
        allow_num_brackets = has_numeric_bib or (not has_any_bib)
        body_non_ref_pre = "\n".join(s["text"] for s in body_sections)
        num_bib_count_pre = sum(1 for v in local_unique_bib.values() if str(v.get("key", "")).isdigit())
        is_superscript_doc, _ = io_core.detect_superscript_numeric_style(body_non_ref_pre, num_bib_count_pre)
        max_num_bib_key = max([int(v["key"]) for v in local_unique_bib.values() if str(v.get("key", "")).isdigit()] + [num_bib_count_pre])
        cited_marker_occurrences: dict[str, list[int]] = defaultdict(list)
        doc_marker_instances = 0
        key_to_source_file: dict[str, str] = {}

        for sec in body_sections:
            for sent in io_core.split_sentences(sec):
                st = sent["text"]
                lno = sent["line"]
                lend = sent.get("line_end", lno)
                page = doc["page_map"].get(lno, 0)
                cites = _extract_citations_from_sentence(st, allow_numeric_brackets=allow_num_brackets)
                sup_cites: list[dict[str, Any]] = []
                if is_superscript_doc and not cites:
                    sup_cites = io_core.extract_superscript_citations(st, max_num_bib_key)
                    cites = sup_cites
                if cites:
                    doc_marker_instances += len(cites)
                    total_markers += len(cites)
                    for c in cites:
                        cited_marker_occurrences[c["key"]].append(lno)
                        unique_cited_all.add(c["key"])

                    # Atomic sub-claim decomposition & five-state evaluation
                    is_lim_or_hyp = bool(LIMITATION_HYPOTHESIS_RE.search(st))
                    if sup_cites:
                        dprof_sup = _detect_direction_profile(st)
                        subclaims = [{
                            "subclaim_idx": 1,
                            "text": st,
                            "citations": sup_cites,
                            "tokens": _content_tokens(st),
                            "numbers": _extract_claim_numbers(st),
                            "direction": dprof_sup["clinical_dir"],
                            "direction_profile": dprof_sup,
                            "causal": _detect_causal_strength(st),
                        }]
                    else:
                        subclaims = _decompose_atomic_subclaims(st, allow_numeric_brackets=allow_num_brackets)
                    for sc in subclaims:
                        for c in sc["citations"]:
                            k = c["key"]
                            bib = bib_entries.get(k)
                            src, match_method = _match_bib_to_source(k, bib, source_docs)
                            if src is not None:
                                key_to_source_file[k] = src["file"]

                            eval_res = _evaluate_subclaim_against_source(sc, bib, src, match_method)
                            status_5 = eval_res["support_status"]

                            # Check publication status markers on bibliography / source
                            pub_flags: list[str] = []
                            bib_txt = bib["raw"] if bib else ""
                            if (bib and bib.get("retraction_marked")) or (bib_txt and RETRACTION_RE.search(bib_txt)) or (src and src["retraction_marked"]):
                                pub_flags.append("RETRACTED_OR_WITHDRAWN")
                            if (bib and bib.get("erratum_marked")) or (bib_txt and ERRATUM_RE.search(bib_txt)) or (src and src["erratum_marked"]):
                                pub_flags.append("ERRATUM_OR_CORRECTION")
                            if (bib and bib.get("preprint_marked")) or (bib_txt and PREPRINT_RE.search(bib_txt)) or (src and src["preprint_marked"]):
                                pub_flags.append("PREPRINT_UNREFEREED")

                            citation_matrix.append({
                                "manuscript": doc["file"],
                                "section": sec["title"],
                                "line": lno,
                                "line_end": lend,
                                "page": page or None,
                                "key": k,
                                "citation_style": c["style"],
                                "subclaim_idx": sc["subclaim_idx"],
                                "atomic_subclaim": sc["text"][:220],
                                "sentence_role": "limitation_or_hypothesis" if is_lim_or_hyp else "cited_claim",
                                "bibliography_found": bib is not None,
                                "bibliography_entry": bib_txt[:220] if bib_txt else None,
                                "publication_flags": pub_flags,
                                **eval_res,
                            })

                            # Promote substantive semantic defects to findings (respecting anti-FP gate for limitations/hypotheses)
                            if status_5 == "contradicted_by_source" and not is_lim_or_hyp:
                                add_finding(
                                    facet="citation_support",
                                    finding_type="factual_error",
                                    severity="critical",
                                    file=doc["file"],
                                    line_start=lno,
                                    line_end=lend,
                                    page=page,
                                    section=sec["title"],
                                    verbatim_quote=sc["text"],
                                    evidence_basis=f"[{k}] -> {eval_res['source_location']}: {eval_res['rationale']} Source: \"{eval_res['source_quote']}\"",
                                    actionable_revision=(
                                        f"Correct the claimed value/direction for [{k}] to match {eval_res['source_location']} "
                                        "or replace with an accurately supporting reference."
                                    ),
                                    impact="high",
                                    cost="low",
                                )
                            elif status_5 == "partially_supported" and not is_lim_or_hyp:
                                is_downgraded = eval_res.get("downgraded_severity") == "minor"
                                add_finding(
                                    facet="citation_support",
                                    finding_type="insufficient_evidence",
                                    severity="minor" if is_downgraded else "major",
                                    file=doc["file"],
                                    line_start=lno,
                                    line_end=lend,
                                    page=page,
                                    section=sec["title"],
                                    verbatim_quote=sc["text"],
                                    evidence_basis=f"[{k}] -> {eval_res['source_location']}: {eval_res['rationale']} Source: \"{eval_res['source_quote']}\"",
                                    actionable_revision=(
                                        f"Manually verify directional alignment for [{k}] against {eval_res['source_location']}."
                                        if is_downgraded
                                        else (
                                            f"Calibrate claim strength for [{k}] (downgrade causal verbs to associational "
                                            "and/or restore the source's species/model/subgroup boundary)."
                                        )
                                    ),
                                    impact="medium" if is_downgraded else "high",
                                    cost="low",
                                    status="needs_human_review" if (is_downgraded or eval_res.get("needs_human_review")) else "confirmed",
                                )
                            elif status_5 == "doi_or_metadata_only_unsupported" and not is_lim_or_hyp:
                                is_meta_only = bool(src and src.get("metadata_only"))
                                add_finding(
                                    facet="citation_support",
                                    finding_type="insufficient_evidence" if is_meta_only else "factual_error",
                                    severity="minor" if is_meta_only else "major",
                                    file=doc["file"],
                                    line_start=lno,
                                    line_end=lno,
                                    page=page,
                                    section=sec["title"],
                                    verbatim_quote=sc["text"],
                                    evidence_basis=f"[{k}] ({eval_res['source_file']}): {eval_res['rationale']}",
                                    actionable_revision=(
                                        f"Supply full text/abstract excerpt for [{k}] to verify semantic support."
                                        if is_meta_only
                                        else f"Verify whether [{k}] is mis-cited (topical mismatch with '{eval_res['source_file']}') and replace with the intended source."
                                    ),
                                    impact="medium" if is_meta_only else "high",
                                    cost="low",
                                    status="needs_human_review" if is_meta_only else "confirmed",
                                )
                            if "PREPRINT_UNREFEREED" in pub_flags and PEER_REVIEWED_CLAIM_RE.search(st):
                                add_finding(
                                    facet="fact_verification",
                                    finding_type="insufficient_evidence",
                                    severity="major",
                                    file=doc["file"],
                                    line_start=lno,
                                    line_end=lno,
                                    page=page,
                                    section=sec["title"],
                                    verbatim_quote=st,
                                    evidence_basis=f"Citation [{k}] is flagged as a preprint but described as peer-reviewed or established in text.",
                                    actionable_revision=f"Check whether [{k}] has a peer-reviewed published version; otherwise qualify it explicitly as a preprint.",
                                    impact="medium",
                                    cost="low",
                                )
                else:
                    # Bare prior-literature quantitative assertion lacking citation (anti-FP gated)
                    if (
                        sec["role"] not in {"declarations", "appendix"}
                        and PRIOR_LIT_CUE_RE.search(st)
                        and QUANT_EMPIRICAL_RE.search(st)
                        and not LOCAL_STUDY_RE.search(st)
                        and not LIMITATION_HYPOTHESIS_RE.search(st)
                    ):
                        add_finding(
                            facet="citation_support",
                            finding_type="insufficient_evidence",
                            severity="major",
                            file=doc["file"],
                            line_start=lno,
                            line_end=lno,
                            page=page,
                            section=sec["title"],
                            verbatim_quote=st,
                            evidence_basis="Quantitative assertion attributed to prior studies/literature without any citation marker, DOI, or local experimental anchor.",
                            actionable_revision="Attach primary citation(s) supporting the reported prior-literature figure, or clarify if it refers to current-study results.",
                            impact="high",
                            cost="low",
                        )

        # Bibliography status alerts & dangling/uncited reconciliation
        dangling_keys: list[str] = []
        retracted_flagged: set[str] = set()
        erratum_flagged: set[str] = set()
        preprint_keys: list[str] = []

        for k, line_nums in sorted(cited_marker_occurrences.items()):
            bib = bib_entries.get(k)
            src, _ = _match_bib_to_source(k, bib, source_docs)
            if bib is None and src is None and not k.startswith(("doi:", "pmid:", "arxiv:")):
                dangling_keys.append(k)
                add_finding(
                    facet="citation_support",
                    finding_type="factual_error",
                    severity="major",
                    file=doc["file"],
                    line_start=line_nums[0],
                    line_end=line_nums[0],
                    page=doc["page_map"].get(line_nums[0], 0),
                    section="Body",
                    verbatim_quote=f"Citation [{k}] at line(s) {line_nums[:6]}",
                    evidence_basis=f"In-text citation key '{k}' has no corresponding entry in the bibliography/References or supplied sources (dangling citation).",
                    actionable_revision=f"Add complete bibliographic record for '{k}' or correct the citation key/numbering.",
                    impact="high",
                    cost="low",
                )
                continue

            bib_txt = bib["raw"] if bib else ""
            is_ret = (bib and bib.get("retraction_marked")) or (bib_txt and RETRACTION_RE.search(bib_txt)) or (src and src["retraction_marked"])
            is_err = (bib and bib.get("erratum_marked")) or (bib_txt and ERRATUM_RE.search(bib_txt)) or (src and src["erratum_marked"])
            is_pre = (bib and bib.get("preprint_marked")) or (bib_txt and PREPRINT_RE.search(bib_txt)) or (src and src["preprint_marked"])
            if is_ret and k not in retracted_flagged:
                retracted_flagged.add(k)
                add_finding(
                    facet="fact_verification",
                    finding_type="factual_error",
                    severity="critical",
                    file=doc["file"],
                    line_start=bib["line"] if bib else line_nums[0],
                    line_end=bib.get("line_end", bib["line"]) if bib else line_nums[0],
                    page=doc["page_map"].get(bib["line"] if bib else line_nums[0], 0),
                    section="References",
                    verbatim_quote=bib_txt or f"Source {src['file'] if src else k}",
                    evidence_basis=f"Cited reference [{k}] (used at line(s) {line_nums[:5]}) carries an explicit retraction/withdrawal/expression-of-concern marker.",
                    actionable_revision=f"Remove reliance on retracted reference [{k}] and re-evaluate dependent claims.",
                    impact="high",
                    cost="medium",
                )
            if is_err and k not in erratum_flagged:
                erratum_flagged.add(k)
                add_finding(
                    facet="fact_verification",
                    finding_type="insufficient_evidence",
                    severity="major",
                    file=doc["file"],
                    line_start=bib["line"] if bib else line_nums[0],
                    line_end=bib.get("line_end", bib["line"]) if bib else line_nums[0],
                    page=doc["page_map"].get(bib["line"] if bib else line_nums[0], 0),
                    section="References",
                    verbatim_quote=bib_txt or f"Source {src['file'] if src else k}",
                    evidence_basis=f"Cited reference [{k}] carries an erratum/correction marker; verify whether cited numbers were altered in the correction.",
                    actionable_revision=f"Check the published erratum/corrigendum for [{k}] and confirm cited values reflect the corrected version.",
                    impact="medium",
                    cost="low",
                    status="needs_human_review",
                )
            if is_pre:
                preprint_keys.append(k)

        if preprint_keys:
            uncertainty.append(
                f"Preprint references detected in {doc['file']}: {sorted(set(preprint_keys))}. Verify peer-reviewed publication status and version drift."
            )

        # Uncited bibliography entries (deduplicated across key, author_year alias, shared DOI, and Vancouver superscript style)
        cited_canonical_ids: set[int] = {id(bib_entries[k]) for k in cited_marker_occurrences if k in bib_entries}
        cited_keys_set: set[str] = set(cited_marker_occurrences.keys())
        cited_dois_set: set[str] = {
            d for k in cited_marker_occurrences if k in bib_entries for d in bib_entries[k].get("dois", [])
        }
        dup_keys_set: set[str] = {d["key"] for d in dup_bibs}
        body_non_ref = "\n".join(s["text"] for s in sections if s["role"] != "references")
        num_bib_count = sum(1 for v in local_unique_bib.values() if str(v.get("key", "")).isdigit())
        is_superscript_style, superscript_cited_keys = io_core.detect_superscript_numeric_style(body_non_ref, num_bib_count)
        if is_superscript_style:
            cited_keys_set.update(superscript_cited_keys)
            uncertainty.append(
                f"Detected Vancouver/superscript numeric citation style in {doc['file']} ({len(superscript_cited_keys)} superscript keys parsed); "
                "orphan-reference alerts suppressed where PDF plain-text extraction strips superscript tags."
            )
        uncited_keys: list[str] = []
        seen_bib_obj: set[int] = set()
        for bk, rec in sorted(bib_entries.items(), key=lambda x: (x[1]["line"], x[0])):
            if id(rec) in seen_bib_obj:
                continue
            seen_bib_obj.add(id(rec))
            if (
                id(rec) in cited_canonical_ids
                or rec["key"] in cited_keys_set
                or rec["key"] in dup_keys_set
                or (rec.get("dois") and (set(rec["dois"]) & cited_dois_set))
            ):
                continue
            if is_superscript_style and str(rec["key"]).isdigit():
                continue
            uncited_keys.append(rec["key"])
            add_finding(
                facet="academic_norms",
                finding_type="norm_violation",
                severity="minor",
                file=rec["file"],
                line_start=rec["line"],
                line_end=rec["line"],
                page=doc["page_map"].get(rec["line"], 0) if rec["file"] == doc["file"] else 0,
                section="References",
                verbatim_quote=rec["raw"],
                evidence_basis=f"Bibliography entry '{rec['key']}' is listed in References/.bib but never cited in the manuscript body.",
                actionable_revision=f"Cite '{rec['key']}' where relevant in the text or remove the orphan bibliography entry.",
                impact="low",
                cost="low",
            )

        # Many-to-one source collapse detection (e.g., multiple distinct cited keys mapping to 1 physical source object)
        source_to_keys: dict[str, list[str]] = defaultdict(list)
        for k, sf in key_to_source_file.items():
            source_to_keys[sf].append(k)
        collapsed_sources = {sf: sorted(ks) for sf, ks in source_to_keys.items() if len(ks) >= 2}
        for sf, ks in collapsed_sources.items():
            add_finding(
                facet="citation_support",
                finding_type="factual_error",
                severity="major",
                file=doc["file"],
                line_start=cited_marker_occurrences[ks[0]][0],
                line_end=cited_marker_occurrences[ks[0]][0],
                page=doc["page_map"].get(cited_marker_occurrences[ks[0]][0], 0),
                section="References",
                verbatim_quote=f"Distinct citation keys {ks} all resolved to single source object '{sf}'",
                evidence_basis=(
                    f"Three-way reconciliation mismatch: {len(ks)} distinct citation keys ({', '.join(ks)}) collapse "
                    f"onto the same source object '{sf}'. Verify duplicate numbering or reference-export persistence loss."
                ),
                actionable_revision=f"Consolidate duplicate citation keys {ks} or verify whether distinct source files were dropped during export.",
                impact="high",
                cost="low",
            )

        reconciliation_records.append({
            "document": doc["file"],
            "in_text_marker_instances": doc_marker_instances,
            "unique_cited_keys": len(cited_marker_occurrences),
            "bibliography_unique_entries": len(seen_bib_obj),
            "resolved_unique_source_objects": len(set(key_to_source_file.values())),
            "duplicate_bib_numbers": len(dup_bibs),
            "dangling_citation_keys": dangling_keys,
            "uncited_bib_keys": uncited_keys,
            "many_to_one_source_collapses": collapsed_sources,
        })

        # Cross-section numerical & sample-size reconciliation
        cs_checks = _reconcile_cross_section_facts(doc, sections)
        for chk in cs_checks:
            chk["file"] = doc["file"]
            cross_section_all.append(chk)
            if chk["conflict"]:
                if chk["check_type"] == "abstract_vs_body_total_N":
                    add_finding(
                        facet="numerical_consistency",
                        finding_type="factual_error",
                        severity="major",
                        file=doc["file"],
                        line_start=chk["abstract_line"],
                        line_end=chk["body_line"],
                        page=doc["page_map"].get(chk["abstract_line"], 0),
                        section=f"Abstract vs {chk['body_section']}",
                        verbatim_quote=f"Abstract (L{chk['abstract_line']}): {chk['abstract_quote'][:95]} | {chk['body_section']} (L{chk['body_line']}): {chk['body_quote'][:95]}",
                        evidence_basis=(
                            f"Cross-section sample-size conflict: Abstract reports total N={chk['abstract_value']} "
                            f"whereas {chk['body_section']} reports N={chk['body_value']}."
                        ),
                        actionable_revision="Reconcile total cohort N across Abstract, Methods, Results, and tables, explicitly noting any excluded/lost-to-follow-up cases.",
                        impact="high",
                        cost="low",
                    )
                elif chk["check_type"] == "abstract_vs_body_metric_pct":
                    add_finding(
                        facet="numerical_consistency",
                        finding_type="factual_error",
                        severity="major",
                        file=doc["file"],
                        line_start=chk["abstract_line"],
                        line_end=chk["body_line"],
                        page=doc["page_map"].get(chk["abstract_line"], 0),
                        section=f"Abstract vs {chk['body_section']}",
                        verbatim_quote=f"Abstract {chk['metric']}={chk['abstract_value']}% (L{chk['abstract_line']}) vs {chk['body_section']} {chk['metric']}={chk['body_value']}% (L{chk['body_line']})",
                        evidence_basis=(
                            f"Cross-section metric discrepancy: Abstract reports '{chk['metric']}' as {chk['abstract_value']}% "
                            f"while {chk['body_section']} reports {chk['body_value']}%."
                        ),
                        actionable_revision=f"Verify the authoritative '{chk['metric']}' percentage and synchronize Abstract and {chk['body_section']}.",
                        impact="high",
                        cost="low",
                    )

        # Six-facet companion coverage (method_design, statistical_reporting, argument_chain, academic_norms)
        comp_f, comp_cov, comp_chk = io_core.audit_six_facet_companion(
            doc, sections, skip_facets={"citation_support", "fact_verification"}, id_prefix="CIT-AUX",
        )
        for cf in comp_f:
            add_finding(
                facet=cf["facet"],
                finding_type=cf["finding_type"],
                severity=cf["severity"],
                file=cf["location"]["file"],
                line_start=cf["location"]["line_start"],
                line_end=cf["location"]["line_end"],
                page=int(cf["location"].get("page") or 0),
                section=cf["location"]["section"],
                verbatim_quote=cf["verbatim_quote"],
                evidence_basis=cf["evidence_basis"],
                actionable_revision=cf["actionable_revision"],
                impact="high" if cf["severity"] in ("critical", "major") else "medium",
                cost="low",
                status=cf["status"],
            )
        six_facet_coverage_docs.append({"file": doc["file"], "facets": comp_cov})
        semantic_checklists.append({"file": doc["file"], "items": comp_chk})

    # -----------------------------------------------------------------------
    # B. Process structured JSON citation exports (marker-to-object reconciliation)
    # -----------------------------------------------------------------------
    for sj in structured_json_docs:
        cites = _extract_citations_from_sentence(sj["body_text"])
        unique_keys = sorted({c["key"] for c in cites})
        ref_objs = sj["reference_objects"]
        total_markers += len(cites)
        unique_cited_all.update(unique_keys)
        collapse_detected = len(unique_keys) >= 2 and len(ref_objs) < len(unique_keys)
        reconciliation_records.append({
            "document": sj["file"],
            "in_text_marker_instances": len(cites),
            "unique_cited_keys": len(unique_keys),
            "bibliography_unique_entries": len(ref_objs),
            "resolved_unique_source_objects": len(ref_objs),
            "duplicate_bib_numbers": 0,
            "dangling_citation_keys": [k for k in unique_keys if k.isdigit() and int(k) > len(ref_objs)],
            "uncited_bib_keys": [],
            "many_to_one_source_collapses": {"structured_export_mismatch": unique_keys} if collapse_detected else {},
        })
        if collapse_detected:
            unmapped = [k for k in unique_keys if k.isdigit() and int(k) > len(ref_objs)]
            add_finding(
                facet="citation_support",
                finding_type="insufficient_evidence",
                severity="major",
                file=sj["file"],
                line_start=1,
                line_end=1,
                page=0,
                section="StructuredExport",
                verbatim_quote=f"{len(cites)} in-text markers ({len(unique_keys)} unique keys) vs {len(ref_objs)} persisted reference object(s)",
                evidence_basis=(
                    f"Three-way marker-to-object collapse: text contains {len(cites)} citation markers across "
                    f"{len(unique_keys)} unique keys, but structured history/references retains only {len(ref_objs)} object(s). "
                    "This reflects a parsing/persistence mapping gap rather than proven fabrication; unmapped markers remain unverified."
                ),
                actionable_revision=(
                    f"Re-export raw source legend or inspect upstream response/history separately to resolve unmapped marker(s) {unmapped[:12]}."
                ),
                impact="high",
                cost="low",
                status="needs_human_review",
            )
            for uk in unmapped:
                citation_matrix.append({
                    "manuscript": sj["file"],
                    "section": "StructuredExport",
                    "line": 1,
                    "page": None,
                    "key": uk,
                    "citation_style": "numeric",
                    "subclaim_idx": 1,
                    "atomic_subclaim": f"Unmapped marker [{uk}] in structured export",
                    "sentence_role": "cited_claim",
                    "bibliography_found": False,
                    "bibliography_entry": None,
                    "publication_flags": [],
                    "support_status": "source_not_provided",
                    "needs_human_review": True,
                    "source_file": None,
                    "source_location": None,
                    "source_quote": "",
                    "match_method": "none",
                    "rationale": "Marker count exceeds persisted reference objects; cannot verify without raw source legend (do not classify as fabricated).",
                })

    # Five-state distribution summary
    five_states = [
        "supported_by_source",
        "partially_supported",
        "contradicted_by_source",
        "source_not_provided",
        "doi_or_metadata_only_unsupported",
    ]
    state_counts = {st: 0 for st in five_states}
    for row in citation_matrix:
        sv = row.get("support_status")
        if sv in state_counts:
            state_counts[sv] += 1

    if state_counts["source_not_provided"] > 0:
        uncertainty.append(
            f"{state_counts['source_not_provided']} atomic cited sub-claim(s) have status 'source_not_provided' because full source text was not supplied or had low-text scanned PDF pages; they are NOT flagged as fabricated."
        )
    if not source_docs:
        uncertainty.append("No standalone source full-text files were supplied in the input directory; semantic claim verification against external papers requires supplied source excerpts.")

    crit_major = [f for f in findings if f["severity"] in {"critical", "major"}]
    has_gaps = bool(gaps)
    audit_status = "partial" if has_gaps else "completed"

    return {
        "passed": len(crit_major) == 0,
        "audit_status": audit_status,
        "status": "partial_with_gaps" if has_gaps else "ok",
        "documents_scanned": [{"file": d["file"], "sha256": d["sha256"], "extractor": d["extractor"]} for d in docs],
        "source_documents_indexed": [
            {
                "file": s["file"],
                "dois": s["dois"],
                "metadata_only": s["metadata_only"],
                "low_text_pdf": s["low_text_pdf"],
                "substantive_chars": s["substantive_chars"],
            }
            for s in source_docs
        ],
        "reconciliation_summary": reconciliation_records,
        "bibliography_count": total_bib_entries,
        "in_text_markers_total": total_markers,
        "unique_cited_keys_count": len(unique_cited_all),
        "atomic_claims_count": len(citation_matrix),
        "five_state_counts": state_counts,
        "citation_matrix": citation_matrix,
        "cross_section_checks": cross_section_all,
        "six_facet_coverage": six_facet_coverage_docs,
        "semantic_review_checklist": semantic_checklists,
        "findings_count": len(findings),
        "findings": findings,
        "uncertainty": uncertainty,
        "gaps": gaps,
        "verdict_note": (
            "No critical or major citation/factual defects detected within inspected coverage."
            if len(crit_major) == 0
            else f"Detected {len(crit_major)} critical/major finding(s) requiring author reconciliation before publication."
        ),
    }


def render_markdown_report(rep: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Citation & Fact Verification Report (`{rep.get('audit_status')}`)",
        "",
        "## 1. Executive Summary & Three-Way Reconciliation",
        "",
        f"- **Audit Verdict**: `{'PASS (no P0/P1 defects)' if rep.get('passed') else 'ACTION REQUIRED'}` — {rep.get('verdict_note', '')}",
        f"- **Documents Scanned**: `{len(rep.get('documents_scanned', []))}` | **Indexed Source Texts**: `{len(rep.get('source_documents_indexed', []))}`",
        f"- **In-Text Markers**: `{rep.get('in_text_markers_total', 0)}` across `{rep.get('unique_cited_keys_count', 0)}` unique keys | **Bibliography Entries**: `{rep.get('bibliography_count', 0)}`",
        f"- **Atomic Sub-Claims Evaluated**: `{rep.get('atomic_claims_count', 0)}` | **Total Findings**: `{rep.get('findings_count', 0)}`",
        "",
    ]

    if rep.get("reconciliation_summary"):
        lines.extend([
            "| Document | In-Text Markers | Unique Keys | Bib Entries | Resolved Sources | Dup Bib # | Dangling | Uncited | Collapse |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ])
        for rec in rep["reconciliation_summary"]:
            col_flag = "YES" if rec.get("many_to_one_source_collapses") else "no"
            lines.append(
                f"| `{rec['document']}` | {rec['in_text_marker_instances']} | {rec['unique_cited_keys']} | "
                f"{rec['bibliography_unique_entries']} | {rec['resolved_unique_source_objects']} | "
                f"{rec['duplicate_bib_numbers']} | {len(rec['dangling_citation_keys'])} | "
                f"{len(rec['uncited_bib_keys'])} | {col_flag} |"
            )
        lines.append("")

    # Five-state summary
    sc = rep.get("five_state_counts", {})
    lines.extend([
        "## 2. Five-State Atomic Claim Evidence Matrix",
        "",
        "| Support State | Count | Meaning |",
        "| --- | ---: | --- |",
        f"| `supported_by_source` | {sc.get('supported_by_source', 0)} | Aligned source passage corroborates topic, direction, and figures |",
        f"| `partially_supported` | {sc.get('partially_supported', 0)} | Core topic matches, but causal overclaim or scope/condition omission detected |",
        f"| `contradicted_by_source` | {sc.get('contradicted_by_source', 0)} | Aligned source contradicts claimed numbers, direction, or significance |",
        f"| `source_not_provided` | {sc.get('source_not_provided', 0)} | Full source text unavailable or low-text scanned PDF (NOT flagged as fabricated) |",
        f"| `doi_or_metadata_only_unsupported` | {sc.get('doi_or_metadata_only_unsupported', 0)} | Metadata-only record or topical mismatch against aligned source |",
        "",
    ])

    if rep.get("citation_matrix"):
        lines.extend([
            "| Location | Key | Five-State Verdict | Source Location | Sub-Claim Excerpt | Rationale |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for row in rep["citation_matrix"][:80]:
            loc = f"`{row['manuscript']}:L{row['line']}`"
            sloc = f"`{row['source_location']}`" if row.get("source_location") else "*(none)*"
            claim_q = row["atomic_subclaim"].replace("|", "\\|")[:95]
            rat = row["rationale"].replace("|", "\\|")[:120]
            lines.append(f"| {loc} | `{row['key']}` | `{row['support_status']}` | {sloc} | {claim_q} | {rat} |")
        lines.append("")

    # Findings by severity
    lines.extend(["## 3. Findings & Author Revision Checklist (Impact × Cost)", ""])
    findings = rep.get("findings", [])
    if not findings:
        lines.append("No qualifying critical, major, or minor citation defects were detected in the inspected material.")
    else:
        sev_order = {"critical": 0, "major": 1, "minor": 2, "suggestion": 3}
        ordered = sorted(findings, key=lambda x: (sev_order.get(x["severity"], 9), x["finding_id"]))
        for f in ordered:
            loc = f["location"]
            ic = f.get("impact_cost", {})
            pg = f"#page={loc['page']}" if loc.get("page") else ""
            lines.extend([
                f"### {f['finding_id']} [{f['severity'].upper()} | {f['finding_type']} | {f['facet']}]",
                f"- **Location**: `{loc['file']}:L{loc['line_start']}-L{loc['line_end']}{pg}` ({loc.get('section', '')})",
                f"- **Impact × Cost**: `impact={ic.get('impact', 'medium')}`, `cost={ic.get('cost', 'low')}` | **Status**: `{f['status']}`",
                f"- **Quote**: \"{f['verbatim_quote']}\"",
                f"- **Evidence**: {f['evidence_basis']}",
                f"- **Actionable Revision**: {f['actionable_revision']}",
                "",
            ])

    # Unverified boundaries & gaps
    lines.extend(["## 4. Unverified Boundaries & Honest Gaps", ""])
    for u in rep.get("uncertainty", []):
        lines.append(f"- **Uncertainty**: {u}")
    for g in rep.get("gaps", []):
        lines.append(f"- **Gap [{g.get('code')}]** (`{g.get('source', '')}`): {g.get('message', '')}")
    if not rep.get("uncertainty") and not rep.get("gaps"):
        lines.append("- None recorded within supplied corpus scope.")
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

    sc = rep.get("five_state_counts", {})
    metrics = {
        "documents_scanned_count": len(rep.get("documents_scanned", [])),
        "source_documents_indexed_count": len(rep.get("source_documents_indexed", [])),
        "in_text_markers_total": int(rep.get("in_text_markers_total", 0)),
        "unique_cited_keys_count": int(rep.get("unique_cited_keys_count", 0)),
        "bibliography_count": int(rep.get("bibliography_count", 0)),
        "atomic_claims_count": int(rep.get("atomic_claims_count", 0)),
        "findings_count": int(rep.get("findings_count", 0)),
        "supported_by_source_count": int(sc.get("supported_by_source", 0)),
        "partially_supported_count": int(sc.get("partially_supported", 0)),
        "contradicted_by_source_count": int(sc.get("contradicted_by_source", 0)),
        "source_not_provided_count": int(sc.get("source_not_provided", 0)),
        "doi_or_metadata_only_unsupported_count": int(sc.get("doi_or_metadata_only_unsupported", 0)),
    }
    src_ref = out_json.name
    metric_provenance = {
        "documents_scanned_count": f"{src_ref}:documents_scanned",
        "source_documents_indexed_count": f"{src_ref}:source_documents_indexed",
        "in_text_markers_total": f"{src_ref}:in_text_markers_total",
        "unique_cited_keys_count": f"{src_ref}:unique_cited_keys_count",
        "bibliography_count": f"{src_ref}:bibliography_count",
        "atomic_claims_count": f"{src_ref}:atomic_claims_count",
        "findings_count": f"{src_ref}:findings_count",
        "supported_by_source_count": f"{src_ref}:five_state_counts.supported_by_source",
        "partially_supported_count": f"{src_ref}:five_state_counts.partially_supported",
        "contradicted_by_source_count": f"{src_ref}:five_state_counts.contradicted_by_source",
        "source_not_provided_count": f"{src_ref}:five_state_counts.source_not_provided",
        "doi_or_metadata_only_unsupported_count": f"{src_ref}:five_state_counts.doi_or_metadata_only_unsupported",
    }
    has_gaps = bool(rep.get("gaps"))
    top_status = "partial_with_gaps" if has_gaps else "ok"
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "task_id": "challenge-cbe96f01",
        "status": top_status,
        "audit_status": rep.get("audit_status", "partial" if has_gaps else "completed"),
        "passed": bool(rep.get("passed", False)),
        "sources": rep.get("documents_scanned", []),
        "artifacts": artifacts,
        "metrics": metrics,
        "metric_provenance": metric_provenance,
        "five_state_counts": sc,
        "reconciliation_summary": rep.get("reconciliation_summary", []),
        "six_facet_coverage": rep.get("six_facet_coverage", []),
        "semantic_review_checklist": rep.get("semantic_review_checklist", []),
        "findings": rep.get("findings", []),
        "uncertainty": rep.get("uncertainty", []),
        "gaps": rep.get("gaps", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline citation reconciliation & atomic claim fact verifier.")
    parser.add_argument("--input", required=True, help="Input directory or manuscript file.")
    parser.add_argument("--out-json", required=True, help="Output verification JSON path.")
    parser.add_argument("--out-md", default="", help="Optional Markdown report path.")
    parser.add_argument("--out-result-json", default="", help="Optional dual-compatible result.json contract path.")
    parser.add_argument("--case-id", default="", help="Optional explicit case_id (auto-read from <input>/case.json if omitted).")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if critical/major findings exist.")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    rep = verify_citations_and_facts(input_path)
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
        "passed": rep.get("passed"),
        "atomic_claims_count": rep.get("atomic_claims_count", 0),
        "five_state_counts": rep.get("five_state_counts", {}),
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
