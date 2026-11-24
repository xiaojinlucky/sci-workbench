#!/usr/bin/env python3
"""Introduction & Related Work four-beat, taxonomy, bounded-gap, and 1:1 contribution auditor.

Target task: introduction-related-work-writing (Writing 08 | Introduction & Related Work Drafting).
Zero third-party dependencies (Python 3.12+ stdlib + sibling writing_io_and_invariants.py).
Produces:
  - related_work_taxonomy.json       (dimension-grouped literature matrix, conflict analysis,
                                      four-beat check, gap audit, contribution alignment)
  - gap_contribution_alignment.md    (human-readable comparative taxonomy + 1:1 alignment table)
  - result.json                      (Harbor/structured output contract)
"""

from __future__ import annotations

import argparse
import csv
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
# Patterns (bilingual EN + ZH)
# ---------------------------------------------------------------------------

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SENT_SPLIT_RE = re.compile(
    r"(?<!\bal\.)(?<!\bFig\.)(?<!\bFigs\.)(?<!\bEq\.)(?<!\bTab\.)(?<!\bTbl\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bNo\.)(?<!\bDr\.)(?<!\bRef\.)(?<!\bSec\.)(?<=[。！？.!?])\s+",
    re.I,
)

HOLLOW_OPENER_RE = re.compile(
    r"(?:近年来[，,]?[^。！？\n]{0,28}(?:受到(?:了)?广泛关注|成为研究热点|飞速发展|蓬勃发展|日新月异)"
    r"|随着(?:人工|科学|信息|现代)?技术(?:与社会)?的(?:不断|快速|飞速)发展"
    r"|\bin\s+recent\s+years,?\s+[^.?!]{0,60}\b(?:attracted\s+(?:widespread|increasing|growing)\s+attention|become\s+a\s+hot\s+topic|rapidly\s+developed|gained\s+momentum)\b"
    r"|\bwith\s+the\s+rapid\s+development\s+of\b)",
    re.I,
)

# Four-beat press-release opening detectors
BEAT_PROBLEM_RE = re.compile(
    r"\b(?:bottleneck|challenge|problem|failure\s+mode|trade-?off|constraint|critical\s+barrier|clinical\s+need|remains\s+difficult)\b"
    r"|核心问题|技术瓶颈|主要挑战|关键难题|约束条件|实际痛点|尚难解决",
    re.I,
)
BEAT_GAP_RE = re.compile(
    r"\b(?:however|yet|nevertheless|remain(?:s)?\s+(?:unclear|unresolved|unaddressed|limited|contradictory)|"
    r"limitation|fail(?:s|ed)?\s+to|diverg(?:e|ent|ing)|conflict(?:s|ing)?|gap)\b"
    r"|然而|但是|仍存在|局限在于|分歧|冲突|未能解决|尚不清楚|研究空白|缺口",
    re.I,
)
BEAT_APPROACH_RE = re.compile(
    r"\b(?:in\s+this\s+(?:paper|study|work),?\s+we\s+(?:propose|introduce|present|develop|design|construct|formulate)|"
    r"to\s+address\s+this\s+(?:gap|limitation|challenge),?\s+we|our\s+(?:method|framework|approach|model))\b"
    r"|为此，?本文(?:提出|构建|设计|开发)|针对上述(?:缺口|局限|问题)，?我们",
    re.I,
)
BEAT_FINDING_RE = re.compile(
    r"\b(?:experiment(?:s|al\s+results)?\s+(?:show|demonstrate|indicate|confirm)|"
    r"outperform(?:s|ed)?|achiev(?:e|es|ed)|reduc(?:e|es|ed)\s+[^.]{0,40}\bby\b|"
    r"improv(?:e|es|ed)\s+[^.]{0,40}\bby\b|\d+(?:\.\d+)?%\s+(?:higher|lower|improvement|reduction|gain|accuracy|AUC|F1))\b"
    r"|实验结果表明|在.{1,25}上(?:提升|降低|达到|优于)[^。]{0,30}\d|核心发现",
    re.I,
)

# Unsubstantiated / unbounded "first" or "no prior work" claims
ABSOLUTE_GAP_RE = re.compile(
    r"(?:尚无(?:任何)?(?:相关)?研究|从未(?:被)?研究|国内外尚未(?:见|有)报道|首次(?:系统)?(?:提出|探讨|研究|证明)|"
    r"填补(?:了)?(?:领域|国内|国际)?空白|前所未有|目前没有任何研究|"
    r"\bfirst\s+(?:ever|study\s+to|work\s+to|attempt\s+to|to\s+the\s+best\s+of\s+our\s+knowledge,?\s+no)\b|"
    r"\bno\s+(?:prior|previous|existing)\s+(?:work|stud(?:y|ies)|research|method(?:s)?)\b|"
    r"\bhas\s+never\s+been\s+(?:studied|explored|investigated|addressed)\b|"
    r"\bunprecedented\b|\bfills?\s+this\s+(?:critical\s+)?gap\s+for\s+the\s+first\s+time\b)",
    re.I,
)
SCOPE_BOUNDARY_RE = re.compile(
    r"\b(?:under\s+[^,.;]{3,50}|within\s+[^,.;]{3,50}|in\s+(?:the\s+context\s+of|high-|low-|small-|clinical|in\s+vitro|multi-center|non-stationary)[^,.;]{0,50}|"
    r"specifically\s+for|scoped\s+to|restricted\s+to|when\s+[^,.;]{5,60}|compared\s+(?:with|to)|unlike\s+[A-Z])\b"
    r"|在.{2,25}(?:条件下|场景下|范围内| regime|队列中)|针对.{2,25}(?:场景|人群|样本|条件)|受限于|局限于|相较于",
    re.I,
)
LIMITATION_OR_CONFLICT_CUE_RE = re.compile(
    r"\b(?:limit(?:ed|ation)|conflict(?:ing|s)?|diverg(?:e|ent|ence)|inconsistent|contradict(?:ory|s)?|"
    r"assume(?:s|d)?|fail(?:s|ed)?\s+when|degrade(?:s|d)?|restricted\s+to|narrow\s+(?:cohort|regime|scope)|only\s+evaluated)\b"
    r"|局限|分歧|冲突|不一致|矛盾|假设|仅在.{1,20}验证|适用范围窄|失效|未覆盖",
    re.I,
)

# Chronological author-year stitching detection
YEAR_SENT_RE = re.compile(
    r"(?:^|[.!?。！？]\s*)(?:(?:[A-Z][A-Za-z\-]+(?:\s+et\s+al\.|\s+and\s+[A-Z][A-Za-z\-]+)?\s*\(\s*((?:19|20)\d{2})[a-z]?\s*\))"
    r"|(?:In\s+((?:19|20)\d{2}),?\s+[A-Z][A-Za-z\-]+)"
    r"|(?:((?:19|20)\d{2})\s*年[^。！？\n]{0,18}(?:等|团队|学者|研究|报道|提出)))"
)
DIMENSION_AXIS_RE = re.compile(
    r"\b(?:categor(?:y|ized|ies)|taxonom(?:y|ies)|group(?:ed|s)?\s+(?:by|into)|"
    r"paradigm|route|axis|axes|dimension|trade-?off|in\s+contrast|whereas|unlike|"
    r"first\s+line\s+of\s+work|second\s+line\s+of\s+work|on\s+the\s+other\s+hand|compar(?:ed|ing)\s+across)\b"
    r"|按.{1,12}(?:维度|路线|范式|机制|设定)划分|第一类|第二类|相比之下|与之相对|共同局限|横向比较|核心取舍",
    re.I,
)

# Citation extractors
CITE_AY_RE = re.compile(
    r"\(([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+|&\s+[A-Z][A-Za-z\-]+))?,?\s*(?:19|20)\d{2}[a-z]?"
    r"(?:\s*;\s*[A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+|&\s+[A-Z][A-Za-z\-]+))?,?\s*(?:19|20)\d{2}[a-z]?)*)\)"
)
CITE_NARRATIVE_AY_RE = re.compile(r"\b([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?)\s*\(\s*((?:19|20)\d{2}[a-z]?)\s*\)")
CITE_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
CITE_TEX_RE = re.compile(r"\\cite[a-z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.I)

CONTRIB_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\(\d+\)|[（(][一二三四五1-5][）)])\s+(.+)$")
CONTRIB_HEADER_RE = re.compile(r"\b(?:our\s+(?:main\s+|key\s+|primary\s+)?contributions?\s+(?:are|include|can\s+be\s+summarized)|in\s+summary,?\s+we\s+make)\b|本文(?:的)?主要贡献|核心贡献(?:如下|包括)", re.I)

POS_OUTCOME_RE = re.compile(r"\b(?:improv|increas|higher|positive|effective|outperform|gain|beneficial|reduces?\s+error)\w*\b|提升|提高|正相关|有效|优于|显著改善", re.I)
NEG_OUTCOME_RE = re.compile(r"\b(?:degrad|decreas|lower|negative|ineffective|no\s+significant|fail(?:s|ed)?|harmful|worse|null)\w*\b|下降|降低|负相关|无效|无显著|劣于|未能改善", re.I)
NARROW_SCOPE_CUE_RE = re.compile(
    r"\b(?:in\s+vitro|mouse|murine|rat\s+model|animal\s+model|cell\s+line|single-center|small\s+cohort|pilot\s+study|"
    r"pediatric|healthy\s+adults?|alkaline|acidic|limited\s+to|restricted\s+to|only\s+evaluated|only\s+tested|retrospective)\b"
    r"|体外|小鼠|动物模型|单中心|小样本|儿童|健康成人|碱性|酸性|仅在|受限于|回顾性",
    re.I,
)
OVERGEN_PARAPHRASE_RE = re.compile(
    r"\b(?:universally|in\s+all\s+(?:patients|populations|conditions|settings|regimes|domains)|across\s+all\s+(?:patients|populations|settings|tasks)|"
    r"clinically\s+proven|definitively\s+establishes?|generalizes?\s+to\s+all|broadly\s+effective\s+in\s+all)\b"
    r"|适用于所有|普遍有效|在一切条件下|临床已证实|全面适用",
    re.I,
)
CONTRAST_CLAUSE_RE = re.compile(r"\b(?:unlike|contrary\s+to|in\s+contrast\s+to|whereas|however|rather\s+than)\b|不同于|与.{1,15}相反|相比之下", re.I)

# Publication-ready manuscript cleanliness detectors (strip process commentary, script paths, audit jargon)
META_COMMENTARY_LINE_RE = re.compile(
    r"^\s*(?:[-*•>\s]*)"
    r"(?:(?:我会先|我先|接下来我|下面我|首先我|本步骤|根据离线脚本|脚本输出显示|经核对工作区|核对工作区|"
    r"注[:：]\s*以上|说明[:：]\s*以上|以下为重构|以下是改写|审计结论如下|本回复基于)|"
    r"(?:I\s+will\s+first|Let\s+me\s+first|First,\s+I\s+will|Based\s+on\s+the\s+(?:offline\s+)?script|"
    r"According\s+to\s+the\s+(?:auditor|script)|Note:\s+the\s+above|Here\s+is\s+the\s+revised|Step\s+\d+\s*[:：]))",
    re.I,
)
SCRIPT_PATH_LEAK_RE = re.compile(
    r"(?:/skills/[^\s`'\"]+|/app/(?:output|work|input)/[^\s`'\"]+|\bscripts/[a-z0-9_]+\.py\b|"
    r"\bpython3?\s+(?:-B\s+)?[^\n]*\.py\b|\brelated_work_taxonomy\.(?:json|md)\b|\bgap_contribution_alignment\.md\b|"
    r"\bresult\.json\b|\bSKILL\.md\b|\breal_case_(?:input|out)\b)",
    re.I,
)
AUDIT_JARGON_LEAK_RE = re.compile(
    r"\b(?:IRW-\d{3}|blocking_count|four_beat_complete|MANUSCRIPT_META_POLLUTION|"
    r"UNSUBSTANTIATED_GAP_CLAIM|OVERCLAIMED_CONTRIBUTION|CHRONOLOGICAL_SUMMARY_STITCHING|"
    r"UNRECONCILED_PRIOR_WORK_DIVERGENCE|Condition\s+[AB]\s*[（(])\b",
)

BODY_SECTION_START_RE = re.compile(
    r"^(?:#{1,3}\s*)?(?:\d+\.?\s*)?(?:abstract|introduction|background|methods?|materials?|results?|discussion|conclusion|references|摘要|引言|绪论|方法|结果|讨论|结论|参考文献)\b",
    re.I,
)


def _extract_publication_year(raw: str) -> tuple[str, str]:
    """Strictly extract publication year from JATS XML, BibTeX, or top-25-line header metadata only.

    NEVER grabs the first 4-digit year from body prose or references; returns ('unknown', 'unknown') when absent.
    """
    # 1) JATS XML <pub-date> / <article-meta>
    jats_pub = re.search(r"<pub-date\b[^>]*>[\s\S]{0,400}?<year>\s*((?:19|20)\d{2})\s*</year>", raw[:12000], re.I)
    if jats_pub:
        y = int(jats_pub.group(1))
        if 1950 <= y <= 2027:
            return str(y), "jats_pub_date"
    jats_meta = re.search(r"<article-meta\b[^>]*>[\s\S]{0,2500}?<year>\s*((?:19|20)\d{2})\s*</year>", raw[:12000], re.I)
    if jats_meta:
        y = int(jats_meta.group(1))
        if 1950 <= y <= 2027:
            return str(y), "jats_article_meta"

    # 2) BibTeX year field
    bib_y = re.search(r"(?:^|\n)\s*year\s*=\s*[\{\"']?((?:19|20)\d{2})[\}\"']?", raw[:4000], re.I)
    if bib_y:
        y = int(bib_y.group(1))
        if 1950 <= y <= 2027:
            return str(y), "bibtex_year"

    # 3) Header metadata zone ONLY (first 25 non-empty lines, strictly BEFORE Abstract/Introduction body)
    header_lines: list[str] = []
    for ln in raw.splitlines():
        st = ln.strip()
        if not st:
            continue
        if len(header_lines) >= 1 and BODY_SECTION_START_RE.match(st):
            break
        header_lines.append(st)
        if len(header_lines) >= 25:
            break

    header_text = "\n".join(header_lines)
    explicit_lbl = re.search(
        r"(?i)(?:^|\n)\s*(?:year|pub(?:lished|lication)?\s*(?:date|year)?|date|发表年份|出版年份)\s*[:：]\s*[^\n]{0,40}?\b((?:19|20)\d{2})\b",
        header_text,
    )
    if explicit_lbl:
        y = int(explicit_lbl.group(1))
        if 1950 <= y <= 2027:
            return str(y), "header_metadata_label"

    copy_m = re.search(r"(?:©|copyright)\s*(?:\(c\)\s*)?((?:19|20)\d{2})\b", header_text, re.I)
    if copy_m:
        y = int(copy_m.group(1))
        if 1950 <= y <= 2027:
            return str(y), "header_copyright"

    jcit_m = re.search(r"\b((?:19|20)\d{2})\s*;\s*\d+\s*(?:\([^)]+\))?\s*:\s*\d+", header_text)
    if jcit_m:
        y = int(jcit_m.group(1))
        if 1950 <= y <= 2027:
            return str(y), "header_journal_citation"

    doi_m = DOI_RE.search(header_text)
    if doi_m:
        doi_str = doi_m.group(1)
        doi_yr = re.search(r"[./_-]((?:19|20)\d{2})[./_-]", doi_str)
        if doi_yr:
            y = int(doi_yr.group(1))
            if 1950 <= y <= 2027:
                return str(y), "header_doi_year_segment"

    return "unknown", "unknown"


def _extract_conclusion_direction(raw: str, route: str) -> tuple[str, str]:
    """Conservatively determine conclusion_direction; never infer polarity from generic body prose.

    Reviews/editorials or full-text papers without an explicit, unambiguous Conclusion direction
    honestly return ('unknown', 'unknown') rather than fabricating positive/negative conflicts.
    """
    if "review" in route.lower():
        return "unknown", "review_or_synthesis_no_single_polarity"
    concl_m = re.search(
        r"(?:^|\n)(?:#{1,3}\s*Conclusions?\b|\bConclusions?\s*[:：])\s*\n?([\s\S]{30,900}?)(?:\n#{1,3}\s|\n\n|$)",
        raw,
        re.I,
    )
    if not concl_m:
        return "unknown", "no_explicit_conclusion_block"
    concl_txt = re.sub(r"\s+", " ", concl_m.group(1)).strip()
    has_pos = bool(re.search(r"\b(?:significantly\s+(?:improv|increas|enhanc|reduc(?:ed|es)\s+(?:risk|mortality|error))|beneficial\s+effect|positively\s+associated|protective\s+effect)\b", concl_txt, re.I))
    has_neg = bool(re.search(r"\b(?:no\s+significant\s+(?:effect|association|difference|benefit)|failed\s+to\s+improve|ineffective|adverse\s+effect|negatively\s+associated|harmful|null\s+effect)\b", concl_txt, re.I))
    if has_pos and not has_neg:
        return "positive", "explicit_conclusion_block"
    if has_neg and not has_pos:
        return "negative", "explicit_conclusion_block"
    return "unknown", "ambiguous_or_multidirectional_conclusion"


def check_and_clean_manuscript(draft_text: str) -> dict[str, Any]:
    """Manuscript cleanliness gate: detect and strip meta-commentary, script paths, and audit jargon."""
    flagged: list[dict[str, Any]] = []
    clean_lines: list[str] = []
    meta_n = 0
    path_n = 0
    jargon_n = 0

    for lineno, raw_line in enumerate(draft_text.splitlines(), 1):
        st = raw_line.strip()
        is_meta = bool(META_COMMENTARY_LINE_RE.search(st))
        is_path = bool(SCRIPT_PATH_LEAK_RE.search(st))
        is_jargon = bool(AUDIT_JARGON_LEAK_RE.search(st))

        if is_meta or is_path or is_jargon:
            cats: list[str] = []
            if is_meta:
                meta_n += 1
                cats.append("process_meta_commentary")
            if is_path:
                path_n += 1
                cats.append("script_or_workspace_path_leak")
            if is_jargon:
                jargon_n += 1
                cats.append("audit_jargon_leak")
            flagged.append({
                "line": lineno,
                "categories": cats,
                "excerpt": st[:160],
            })
            # Drop pure meta-commentary lines; sanitize inline path/jargon leaks if substantive prose remains
            if is_meta:
                continue
            sanitized = SCRIPT_PATH_LEAK_RE.sub("", raw_line)
            sanitized = AUDIT_JARGON_LEAK_RE.sub("", sanitized)
            sanitized = re.sub(r"\s{2,}", " ", sanitized).strip()
            if len(sanitized) >= 20 and not re.match(r"^[-*•:：,，;；()\s]+$", sanitized):
                clean_lines.append(sanitized)
        else:
            clean_lines.append(raw_line)

    cleaned = "\n".join(clean_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"
    return {
        "clean": len(flagged) == 0,
        "flagged_count": len(flagged),
        "meta_commentary_lines_found": meta_n,
        "script_path_leaks_found": path_n,
        "audit_jargon_leaks_found": jargon_n,
        "flagged_lines": flagged[:30],
        "cleaned_text": cleaned,
    }


def _tokenize(text: str) -> set[str]:
    en = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    zh_bi: list[str] = []
    for chunk in zh:
        if len(chunk) == 2:
            zh_bi.append(chunk)
        else:
            for k in range(len(chunk) - 1):
                zh_bi.append(chunk[k : k + 2])
    stop = {
        "the", "and", "that", "this", "with", "from", "for", "are", "was", "were",
        "have", "has", "been", "into", "which", "their", "our", "not", "but", "can",
        "also", "more", "than", "using", "used", "paper", "study", "work", "method",
        "results", "propose", "present", "introduce", "show", "demonstrate", "based",
        "我们", "本文", "研究", "方法", "结果", "提出", "通过", "进行", "以及", "可以",
    }
    return {t for t in en + zh_bi if t not in stop}


def _extract_cites(text: str) -> list[str]:
    found: list[str] = []
    for m in CITE_AY_RE.finditer(text):
        for part in m.group(1).split(";"):
            p = part.strip()
            if p:
                found.append(p)
    for m in CITE_NARRATIVE_AY_RE.finditer(text):
        found.append(f"{m.group(1).strip()}, {m.group(2).strip()}")
    for m in CITE_BRACKET_RE.finditer(text):
        found.append(f"[{m.group(1).strip()}]")
    for m in CITE_TEX_RE.finditer(text):
        for k in m.group(1).split(","):
            if k.strip():
                found.append(k.strip())
    for m in DOI_RE.finditer(text):
        found.append(m.group(1).rstrip(".,;)").lower())
    return list(dict.fromkeys(found))


def _parse_fulltext_article_card(raw: str, fp: Path, read_method: str) -> dict[str, Any]:
    """Extract one structured literature card from a full-text paper (.md/.xml/.html/.pdf/.docx).

    Never guesses publication year from body/references text or conclusion_direction from raw word polarity;
    explicitly outputs 'unknown' and metadata_gaps whenever header/JATS/BibTeX metadata is absent.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    title = ""
    for ln in lines[:12]:
        m = re.match(r"^#{1,3}\s+(.+)$", ln)
        if m:
            title = m.group(1).strip()
            break
    if not title and lines:
        title = lines[0][:140]
    doi_m = DOI_RE.search(raw[:4000])
    doi = doi_m.group(1).rstrip(".,;)").lower() if doi_m else ""
    year, year_source = _extract_publication_year(raw)
    # Extract author only from top-25-line header metadata
    header_zone = "\n".join(lines[:25])
    auth_m = re.search(r"(?:^|\n)(?:authors?|by)\s*[:：]\s*([^\n]{3,120})", header_zone, re.I)
    author = auth_m.group(1).strip() if auth_m else "unknown"
    # Extract abstract/results/limitations snippets
    abs_m = re.search(r"(?:#{1,3}\s*Abstract|\bAbstract\b)[:\s]*\n([\s\S]{80,1500}?)(?:\n#{1,3}\s|\n\n[A-Z])", raw, re.I)
    finding = re.sub(r"\s+", " ", abs_m.group(1)).strip()[:420] if abs_m else re.sub(r"\s+", " ", " ".join(lines[1:12]))[:420]
    lim_m = re.search(r"([^.\n]{0,180}(?:limitation|caveat|restricted\s+to|small\s+sample|single-center|future\s+work|in\s+vitro|mouse\s+model)[^.\n]{0,180}\.?)", raw, re.I)
    limitation = re.sub(r"\s+", " ", lim_m.group(1)).strip()[:240] if lim_m else ""
    scope_hits = list(dict.fromkeys(m.group(0).strip() for m in NARROW_SCOPE_CUE_RE.finditer(raw[:12000])))
    scope_cond = "; ".join(scope_hits[:5]) if scope_hits else "see_fulltext_methods"
    # Infer coarse thematic/method route from title + abstract
    blob = f"{title} {finding}".lower()
    if any(k in blob for k in ("meta-analysis", "systematic review", "narrative review", "review", "survey")):
        route = "Review / Synthesis"
    elif any(k in blob for k in ("clinical", "patient", "cohort", "randomized", "trial", "pediatric", "nhanes")):
        route = "Clinical / Cohort Study"
    elif any(k in blob for k in ("mouse", "murine", "in vivo", "microbiome", "scfa", "gut", "metabol")):
        route = "Mechanistic / Biomedical"
    elif any(k in blob for k in ("graph", "neural", "transformer", "learning", "algorithm", "model")):
        route = "Computational / ML Modeling"
    elif any(k in blob for k in ("catalyst", "oer", "overpotential", "electro", "material", "dft")):
        route = "Materials / Experimental"
    else:
        route = f"Full-Text Study ({read_method})"

    conclusion_dir, dir_source = _extract_conclusion_direction(raw, route)
    metadata_gaps: list[str] = []
    if year == "unknown":
        metadata_gaps.append("year_missing_in_header_or_jats_or_bibtex")
    if conclusion_dir == "unknown":
        metadata_gaps.append(f"conclusion_direction_unknown:{dir_source}")
    if author == "unknown":
        metadata_gaps.append("author_missing_in_header_metadata")

    key = fp.stem
    if auth_m and year != "unknown":
        first_sur = re.split(r"[\s,;]+", author)[0]
        if first_sur:
            key = f"{first_sur}, {year}"
    return {
        "cite_key": key,
        "author": author[:80],
        "year": year,
        "year_source": year_source,
        "doi": doi,
        "title": title[:160],
        "method_route": route,
        "scope_condition": scope_cond,
        "finding": finding,
        "source_full_excerpt": raw[:16000],
        "direction": conclusion_dir,
        "conclusion_direction": conclusion_dir,
        "direction_source": dir_source,
        "metadata_gaps": metadata_gaps,
        "limitation": limitation,
        "source_file": fp.name,
    }


def _load_literature_entries(
    bib_path: Path | None,
    corpus_dir: Path | None,
    *,
    draft_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load structured literature cards from JSON/CSV/BIB/MD/XML/HTML/DOCX/PDF files."""
    entries: list[dict[str, Any]] = []
    degraded_notes: list[dict[str, Any]] = []
    files_to_read: list[Path] = []
    if bib_path and bib_path.is_file():
        files_to_read.append(bib_path)
    if corpus_dir and corpus_dir.is_dir():
        for fp in sorted(corpus_dir.rglob("*")):
            if not fp.is_file():
                continue
            if draft_path and fp.resolve() == draft_path.resolve():
                continue
            low = fp.name.lower()
            if low in {"case.json", "task.md", "readme.md"}:
                continue
            if any(k in low for k in ("methods_results", "own_results", "author_evidence")) and not any(k in low for k in ("bib", "ref", "lit", "prior")):
                continue
            if (
                any(k in low for k in ("bib", "ref", "lit", "paper", "prior", "source", "pmc", "scfa", "oer", "gnn", "work_", "scielo"))
                or fp.suffix.lower() in {".bib", ".pdf", ".docx", ".xml", ".html", ".htm"}
            ):
                if fp not in files_to_read:
                    files_to_read.append(fp)

    for fp in files_to_read:
        suf = fp.suffix.lower()
        read_method = "plain_text"
        if suf in {".docx", ".pdf", ".xml", ".html", ".htm"}:
            doc = wio.read_document_text(fp)
            read_method = str(doc.get("meta", {}).get("method", suf.lstrip(".")))
            if doc.get("meta", {}).get("degraded_mode"):
                degraded_notes.append({
                    "code": "SOURCE_DEGRADED_EXTRACTION",
                    "severity": "minor",
                    "detail": f"{fp.name} extracted via degraded fallback ({read_method}).",
                })
            if not doc.get("ok"):
                entries.append({
                    "cite_key": fp.stem,
                    "author": fp.stem,
                    "year": "",
                    "doi": "",
                    "title": "",
                    "method_route": "unreadable_degraded",
                    "scope_condition": "degraded_extractor_unavailable",
                    "finding": f"UNREADABLE_{suf.lstrip('.').upper()}: {doc.get('meta', {}).get('error', 'extractor unavailable')}",
                    "source_full_excerpt": "",
                    "direction": "neutral",
                    "limitation": "Extractor unavailable in offline container",
                    "source_file": fp.name,
                })
                continue
            raw = doc.get("text", "")
        else:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        if suf == ".json":
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if isinstance(obj, dict) and "DOI" in obj and ("title" in obj or "author" in obj):
                # Crossref single-work JSON record
                items = [{
                    "author": (obj.get("author", [{}])[0].get("family") or fp.stem) if isinstance(obj.get("author"), list) and obj.get("author") else fp.stem,
                    "year": str((obj.get("issued", {}).get("date-parts", [[""]])[0][0]) or ""),
                    "doi": obj.get("DOI", ""),
                    "title": (obj.get("title", [""])[0] if isinstance(obj.get("title"), list) else str(obj.get("title") or "")),
                    "finding": str(obj.get("abstract") or obj.get("title", [""])[0] if isinstance(obj.get("title"), list) else ""),
                    "method_route": "Crossref Metadata Record",
                }]
            else:
                items = obj if isinstance(obj, list) else obj.get("references", obj.get("papers", obj.get("literature", [])))
            if isinstance(items, list):
                for idx, it in enumerate(items, 1):
                    if not isinstance(it, dict):
                        continue
                    author = str(it.get("author") or it.get("authors") or it.get("first_author") or f"Author{idx}").strip()
                    year = str(it.get("year") or "").strip()
                    key = str(it.get("key") or it.get("id") or f"{author.split()[0]}, {year}").strip()
                    finding = str(it.get("finding") or it.get("conclusion") or it.get("result") or it.get("abstract") or "").strip()
                    scope = str(it.get("scope") or it.get("condition") or it.get("dataset") or it.get("applicability") or "unspecified").strip()
                    route = str(it.get("method_route") or it.get("method") or it.get("category") or "general").strip()
                    lim = str(it.get("limitation") or it.get("limitations") or "").strip()
                    direction = "positive" if (POS_OUTCOME_RE.search(finding) and not NEG_OUTCOME_RE.search(finding)) else (
                        "negative" if NEG_OUTCOME_RE.search(finding) else "neutral"
                    )
                    entries.append({
                        "cite_key": key,
                        "author": author,
                        "year": year,
                        "doi": str(it.get("doi") or "").strip().lower(),
                        "title": str(it.get("title") or "").strip(),
                        "method_route": route,
                        "scope_condition": scope,
                        "finding": finding,
                        "source_full_excerpt": f"{finding} {scope} {lim}",
                        "direction": direction,
                        "limitation": lim,
                        "source_file": fp.name,
                    })
        elif suf == ".csv":
            rows = list(csv.DictReader(raw.splitlines()))
            for idx, r in enumerate(rows, 1):
                author = (r.get("author") or r.get("authors") or f"Author{idx}").strip()
                year = (r.get("year") or "").strip()
                key = (r.get("key") or r.get("cite_key") or r.get("id") or f"{author.split()[0]}, {year}").strip()
                finding = (r.get("finding") or r.get("conclusion") or r.get("result") or "").strip()
                scope = (r.get("scope") or r.get("condition") or r.get("dataset") or "unspecified").strip()
                route = (r.get("method_route") or r.get("method") or r.get("category") or "general").strip()
                lim = (r.get("limitation") or "").strip()
                direction = "positive" if (POS_OUTCOME_RE.search(finding) and not NEG_OUTCOME_RE.search(finding)) else (
                    "negative" if NEG_OUTCOME_RE.search(finding) else "neutral"
                )
                entries.append({
                    "cite_key": key, "author": author, "year": year, "doi": (r.get("doi") or "").strip().lower(),
                    "title": (r.get("title") or "").strip(), "method_route": route, "scope_condition": scope,
                    "finding": finding, "source_full_excerpt": f"{finding} {scope} {lim}",
                    "direction": direction, "limitation": lim, "source_file": fp.name,
                })
        elif suf == ".bib":
            for m in re.finditer(r"@\w+\{([^,\s]+),([\s\S]*?)\n\}", raw):
                key, body = m.group(1).strip(), m.group(2)
                def _f(name: str) -> str:
                    fm = re.search(rf"{name}\s*=\s*[\{{\"]([^}}\"]+)[\}}\"]", body, re.I)
                    return fm.group(1).strip() if fm else ""
                fnd = _f("abstract") or _f("note")
                entries.append({
                    "cite_key": key, "author": _f("author"), "year": _f("year"), "doi": _f("doi").lower(),
                    "title": _f("title"), "method_route": "from_bibtex", "scope_condition": "unspecified",
                    "finding": fnd, "source_full_excerpt": fnd, "direction": "neutral", "limitation": "", "source_file": fp.name,
                })
        elif suf in {".md", ".txt", ".docx", ".pdf", ".xml", ".html", ".htm"}:
            # Distinguish full-text scientific articles (>2500 chars) from short literature note lists
            if len(raw) >= 2500:
                card = _parse_fulltext_article_card(raw, fp, read_method)
                entries.append(card)
                if card.get("metadata_gaps"):
                    degraded_notes.append({
                        "code": "LITERATURE_METADATA_UNKNOWN",
                        "severity": "minor",
                        "detail": f"{fp.name}: {', '.join(card['metadata_gaps'])} (year={card['year']}, conclusion_direction={card['conclusion_direction']}).",
                    })
            else:
                for idx, ln in enumerate(raw.splitlines(), 1):
                    st = ln.strip()
                    if not st or st.startswith("#"):
                        continue
                    cites = _extract_cites(st)
                    ym = re.search(r"\b((?:19|20)\d{2})\b", st)
                    if cites or ym or (suf in {".docx", ".pdf", ".xml", ".html"} and DOI_RE.search(st)):
                        key = cites[0] if cites else f"{fp.stem}:L{idx}"
                        direction = "positive" if (POS_OUTCOME_RE.search(st) and not NEG_OUTCOME_RE.search(st)) else (
                            "negative" if NEG_OUTCOME_RE.search(st) else "unknown"
                        )
                        entries.append({
                            "cite_key": key, "author": key.split(",")[0], "year": ym.group(1) if ym else "unknown",
                            "doi": (DOI_RE.search(st).group(1).lower() if DOI_RE.search(st) else ""),
                            "title": "", "method_route": f"extracted_{suf.lstrip('.')}", "scope_condition": "see_excerpt",
                            "finding": st[:220], "source_full_excerpt": raw[:4000],
                            "direction": direction, "conclusion_direction": direction, "limitation": "", "source_file": f"{fp.name}:L{idx}",
                        })
    return entries, degraded_notes


def _load_own_evidence(evidence_dir: Path | None, draft_path: Path) -> dict[str, Any]:
    """Load the author's own methods/results/figures/tables artifacts (including DOCX/PDF) to verify 1:1 contribution alignment."""
    evidence_texts: list[str] = []
    evidence_files: list[str] = []
    degraded_files: list[dict[str, str]] = []
    figure_table_ids: set[str] = set()
    if evidence_dir and evidence_dir.is_dir():
        for fp in sorted(evidence_dir.rglob("*")):
            if not fp.is_file() or fp.resolve() == draft_path.resolve():
                continue
            low = fp.name.lower()
            if low in {"case.json", "task.md"}:
                continue
            if any(k in low for k in ("bib", "ref", "lit", "prior")) and not any(k in low for k in ("method", "result", "table", "fig", "exp")):
                continue
            evidence_files.append(fp.name)
            # Detect figure/table filenames directly
            for fm in re.finditer(r"(fig(?:ure)?|table|tab)[_\-\s]*s?(\d+[a-z]?)", low):
                kind = "figure" if fm.group(1).startswith("fig") else "table"
                figure_table_ids.add(f"{kind}_{fm.group(2)}")
            suf = fp.suffix.lower()
            txt = ""
            if suf in {".docx", ".pdf", ".xml", ".html", ".htm"}:
                doc = wio.read_document_text(fp)
                if doc.get("ok"):
                    txt = doc.get("text", "")
                else:
                    degraded_files.append({"file": fp.name, "reason": str(doc.get("meta", {}).get("error", "unreadable"))})
            elif suf in {".md", ".txt", ".json", ".csv", ".tex", ".py", ".yaml", ".yml"}:
                txt = fp.read_text(encoding="utf-8", errors="replace")
            if txt:
                evidence_texts.append(txt)
                for m in wio.RE_FIG_REF.finditer(txt):
                    ref = (m.group(1) or m.group(2)).strip().lower()
                    num_m = re.search(r"s?\d+[a-z]?", ref)
                    if num_m:
                        kind = "figure" if ("fig" in ref or "图" in ref) else "table"
                        figure_table_ids.add(f"{kind}_{num_m.group(0)}")
    joined = "\n".join(evidence_texts)
    return {
        "files": evidence_files,
        "degraded_files": degraded_files,
        "text": joined,
        "tokens": _tokenize(joined),
        "figure_table_ids": sorted(figure_table_ids),
        "invariants": wio.extract_invariants(joined) if joined else {"bare_numbers": []},
    }


def _split_draft_sections(text: str) -> dict[str, list[tuple[int, str]]]:
    sections: dict[str, list[tuple[int, str]]] = {"introduction": [], "related_work": [], "other": []}
    cur = "introduction"
    for lineno, raw in enumerate(text.splitlines(), 1):
        st = raw.strip()
        hm = MD_HEADING_RE.match(st)
        if hm:
            title = hm.group(2).lower()
            if any(k in title for k in ("related work", "literature", "prior work", "相关工作", "文献综述", "研究现状")):
                cur = "related_work"
            elif any(k in title for k in ("intro", "background", "引言", "绪论", "背景")):
                cur = "introduction"
            else:
                cur = "other"
            continue
        if st:
            sections[cur].append((lineno, st))
    return sections


def _entry_is_cited(entry: dict[str, Any], draft_text: str, draft_cites: list[str]) -> bool:
    low_draft = draft_text.lower()
    if entry["doi"] and entry["doi"] in low_draft:
        return True
    key = entry["cite_key"].lower()
    if key and (key in low_draft or any(key in c.lower() for c in draft_cites)):
        return True
    surname = re.split(r"[\s,;]+", entry["author"].strip())[0].lower() if entry["author"] else ""
    year = entry["year"].strip()
    if surname and len(surname) >= 3 and year and surname in low_draft and year in low_draft:
        return True
    return False


def _match_cited_entries(sent: str, lit_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return supplied literature entries explicitly referenced inside a draft sentence."""
    low = sent.lower()
    sent_cites = _extract_cites(sent)
    matched: list[dict[str, Any]] = []
    for e in lit_entries:
        hit = False
        if e.get("doi") and e["doi"] in low:
            hit = True
        elif e.get("cite_key") and (e["cite_key"].lower() in low or any(e["cite_key"].lower() in c.lower() for c in sent_cites)):
            hit = True
        else:
            sur = re.split(r"[\s,;]+", (e.get("author") or "").strip())[0].lower()
            yr = (e.get("year") or "").strip()
            if sur and len(sur) >= 3 and yr and sur in low and yr in low:
                hit = True
        if hit:
            matched.append(e)
    return matched


def _audit_citation_paraphrase_fidelity(
    all_lines: list[tuple[int, str, str]],
    lit_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare numerical values, outcome direction, and scope conditions of cited paraphrases against supplied source texts."""
    items: list[dict[str, Any]] = []
    distorted = 0
    if not lit_entries:
        return {"audited_pairs": 0, "faithful_count": 0, "distorted_count": 0, "items": []}

    for ln, txt, sec in all_lines:
        for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
            matched_entries = _match_cited_entries(sent, lit_entries)
            if not matched_entries:
                continue
            sent_pos = bool(POS_OUTCOME_RE.search(sent))
            sent_neg = bool(NEG_OUTCOME_RE.search(sent))
            sent_dir = "positive" if (sent_pos and not sent_neg) else ("negative" if (sent_neg and not sent_pos) else "neutral")
            has_contrast = bool(CONTRAST_CLAUSE_RE.search(sent))
            sent_inv = wio.extract_invariants(sent)
            sent_nums = {
                n["value"] for n in sent_inv.get("numbers_with_units", [])
                if n.get("unit") in {"%", "fold", "mV", "ms", "mg/kg", "mM", "uM", "µM", "nM"}
            }
            sent_overgen = bool(OVERGEN_PARAPHRASE_RE.search(sent))

            for e in matched_entries:
                src_blob = f"{e.get('finding', '')} {e.get('scope_condition', '')} {e.get('limitation', '')} {e.get('source_full_excerpt', '')}"
                issues: list[dict[str, str]] = []
                # 1) Direction reversal check
                src_dir = e.get("direction", "neutral")
                if not has_contrast and src_dir in ("positive", "negative") and sent_dir in ("positive", "negative") and src_dir != sent_dir:
                    issues.append({
                        "code": "PARAPHRASE_DIRECTION_REVERSAL",
                        "detail": f"Draft attributes '{sent_dir}' conclusion to {e['cite_key']}, contradicting source '{src_dir}' finding.",
                    })
                # 2) Quantitative drift check (only when both draft sentence and source carry quantitative metrics)
                if sent_nums:
                    src_inv = wio.extract_invariants(src_blob)
                    src_all_nums = set(src_inv.get("bare_numbers", []))
                    if src_all_nums:
                        drifted = sorted(sent_nums - src_all_nums)
                        if drifted:
                            issues.append({
                                "code": "PARAPHRASE_NUMERIC_DISTORTION",
                                "detail": f"Numeric value(s) {drifted} attributed to {e['cite_key']} do not appear in supplied source excerpt ({e['source_file']}).",
                            })
                # 3) Applicability scope overgeneralization check
                src_has_narrow_scope = bool(NARROW_SCOPE_CUE_RE.search(f"{e.get('scope_condition', '')} {e.get('finding', '')} {e.get('limitation', '')}"))
                if sent_overgen and src_has_narrow_scope:
                    issues.append({
                        "code": "PARAPHRASE_SCOPE_OVERGENERALIZATION",
                        "detail": f"Draft overgeneralizes {e['cite_key']} universally, stripping source scope boundary ('{e.get('scope_condition', '')[:80]}').",
                    })

                status = "DISTORTED" if issues else "FAITHFUL"
                if issues:
                    distorted += 1
                items.append({
                    "cite_key": e["cite_key"],
                    "source_file": e["source_file"],
                    "section": sec,
                    "line": ln,
                    "draft_sentence": sent[:180],
                    "source_direction": src_dir,
                    "draft_direction": sent_dir,
                    "source_scope": e.get("scope_condition", "")[:100],
                    "fidelity_status": status,
                    "issues": issues,
                })
    return {
        "audited_pairs": len(items),
        "faithful_count": len(items) - distorted,
        "distorted_count": distorted,
        "items": items[:40],
    }


def audit_intro_and_related_work(
    draft_path: Path,
    *,
    bib_path: Path | None = None,
    corpus_dir: Path | None = None,
    evidence_dir: Path | None = None,
    case_id: str = "case-01",
    task_id: str = "introduction-related-work-writing",
    case_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    read_res = wio.read_document_text(draft_path)
    if not read_res["ok"]:
        raise RuntimeError(f"Failed to read draft {draft_path}: {read_res.get('meta')}")
    draft_text = read_res["text"]
    read_meta = read_res.get("meta") or {}
    sec_lines = _split_draft_sections(draft_text)
    intro_text = "\n".join(t for _, t in sec_lines["introduction"])
    rw_text = "\n".join(t for _, t in sec_lines["related_work"])
    all_lines = [(ln, t, sec) for sec, items in sec_lines.items() for ln, t in items]

    lit_entries, lit_degraded = _load_literature_entries(bib_path, corpus_dir, draft_path=draft_path)
    own_ev = _load_own_evidence(evidence_dir or corpus_dir, draft_path)
    draft_cites = _extract_cites(draft_text)

    findings: list[dict[str, Any]] = []
    evidence_map: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = list(lit_degraded)
    if read_meta.get("degraded_mode"):
        gaps.append({
            "code": "DRAFT_DEGRADED_EXTRACTION",
            "severity": "minor",
            "detail": f"Draft {draft_path.name} extracted via fallback ({read_meta.get('method')}).",
        })

    def add_finding(code: str, severity: str, section: str, line: int, quote: str, message: str, remediation: str) -> None:
        fid = f"IRW-{len(findings) + 1:03d}"
        findings.append({
            "finding_id": fid,
            "code": code,
            "severity": severity,
            "section": section,
            "line": line,
            "quote": quote[:180],
            "message": message,
            "remediation": remediation,
        })
        evidence_map.append({
            "finding_id": fid,
            "code": code,
            "source_file": draft_path.name,
            "section": section,
            "line": line,
            "quote": quote[:140],
        })

    # =========================================================================
    # 1. Four-Beat Press-Release Opening Check + Hollow Opener Interception
    # =========================================================================
    four_beats = {
        "problem": bool(BEAT_PROBLEM_RE.search(intro_text or draft_text)),
        "gap": bool(BEAT_GAP_RE.search(intro_text or draft_text)),
        "approach": bool(BEAT_APPROACH_RE.search(intro_text or draft_text)),
        "key_finding": bool(BEAT_FINDING_RE.search(intro_text or draft_text)),
    }
    for beat_name, present in four_beats.items():
        if not present:
            add_finding(
                code=f"MISSING_FOUR_BEAT_{beat_name.upper()}",
                severity="major",
                section="introduction",
                line=sec_lines["introduction"][0][0] if sec_lines["introduction"] else 1,
                quote=(sec_lines["introduction"][0][1] if sec_lines["introduction"] else ""),
                message=f"Introduction is missing press-release opening beat '{beat_name}' (required arc: Problem -> Gap -> Approach -> Key Finding).",
                remediation=f"Add an explicit, source-grounded '{beat_name}' statement in Introduction anchored to concrete evidence.",
            )

    for ln, txt, sec in all_lines:
        hm = HOLLOW_OPENER_RE.search(txt)
        if hm:
            add_finding(
                code="HOLLOW_GENERIC_OPENER",
                severity="major",
                section=sec,
                line=ln,
                quote=txt,
                message=f"Generic hollow opening cliché detected ('{hm.group(0)}'); fails to establish a concrete technical constraint or problem.",
                remediation="Replace the generic importance opener with the specific unresolved scientific/technical constraint and a cited baseline.",
            )

    # =========================================================================
    # 2. Related Work Grouping vs Chronological Summary Stitching & Divergence
    # =========================================================================
    rw_scan_lines = sec_lines["related_work"] if sec_lines["related_work"] else sec_lines["introduction"]
    rw_years: list[tuple[int, int, str]] = []
    for ln, txt in rw_scan_lines:
        for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
            ym = YEAR_SENT_RE.search(sent)
            if ym:
                yr_s = ym.group(1) or ym.group(2) or ym.group(3)
                if yr_s and yr_s.isdigit():
                    rw_years.append((int(yr_s), ln, sent))
    has_dimension_grouping = bool(DIMENSION_AXIS_RE.search(rw_text or intro_text))
    chronological_stitching = False
    if len(rw_years) >= 3:
        yrs = [x[0] for x in rw_years]
        if yrs == sorted(yrs) and not has_dimension_grouping:
            chronological_stitching = True
            add_finding(
                code="CHRONOLOGICAL_SUMMARY_STITCHING",
                severity="blocking",
                section="related_work" if sec_lines["related_work"] else "introduction",
                line=rw_years[0][1],
                quote=rw_years[0][2],
                message=f"Related Work lists {len(rw_years)} studies in chronological order ({yrs}) as isolated per-paper summaries without shared comparison dimensions.",
                remediation="Reorganize Related Work by shared comparison axes (problem setting, method route, applicability regime, evaluation metric) and contrast trade-offs explicitly.",
            )

    # Analyze supplied literature for divergent findings across narrow scopes
    pos_lit = [e for e in lit_entries if e["direction"] == "positive"]
    neg_lit = [e for e in lit_entries if e["direction"] == "negative"]
    supplied_lit_conflict = bool(pos_lit and neg_lit)
    draft_acknowledges_conflict = bool(re.search(
        r"\b(?:conflict|diverg|contradict|inconsistent|differ(?:ing|ent)\s+(?:condition|cohort|regime|scope|setting)|whereas|in\s+contrast)\b|分歧|冲突|不一致|矛盾|条件差异|适用范围",
        draft_text,
        re.I,
    ))
    if supplied_lit_conflict and not draft_acknowledges_conflict:
        add_finding(
            code="UNRECONCILED_PRIOR_WORK_DIVERGENCE",
            severity="blocking",
            section="related_work",
            line=rw_scan_lines[0][0] if rw_scan_lines else 1,
            quote=(rw_scan_lines[0][1] if rw_scan_lines else ""),
            message=(
                f"Supplied literature contains conflicting conclusions ({len(pos_lit)} positive vs {len(neg_lit)} negative/null studies across narrow conditions), "
                "but the draft does not explain their differing scope conditions."
            ),
            remediation="Explicitly contrast the experimental conditions/cohorts of conflicting studies and derive a scope-bounded research gap from that divergence.",
        )

    # Build comparative taxonomy matrix grouped by method_route
    taxonomy_groups: dict[str, list[dict[str, Any]]] = {}
    for e in lit_entries:
        taxonomy_groups.setdefault(e["method_route"] or "general", []).append({
            "cite_key": e["cite_key"],
            "year": e["year"],
            "scope_condition": e["scope_condition"],
            "direction": e["direction"],
            "finding_excerpt": e["finding"][:160],
            "limitation": e["limitation"] or "not_explicitly_stated_in_card",
            "cited_in_draft": _entry_is_cited(e, draft_text, draft_cites),
        })

    # =========================================================================
    # 3. Unsubstantiated Gap / Baseless "First Ever / No Prior Work" Interceptor
    # =========================================================================
    unsubstantiated_gap_count = 0
    for ln, txt, sec in all_lines:
        for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
            gm = ABSOLUTE_GAP_RE.search(sent)
            if not gm:
                continue
            para_cites = _extract_cites(txt)
            has_lim_cue = bool(LIMITATION_OR_CONFLICT_CUE_RE.search(txt))
            has_scope_bound = bool(SCOPE_BOUNDARY_RE.search(sent)) or bool(SCOPE_BOUNDARY_RE.search(txt))
            missing_parts = []
            if not para_cites:
                missing_parts.append("no cited prior work anchoring the gap")
            if not has_lim_cue:
                missing_parts.append("no concrete limitation/conflict of prior work")
            if not has_scope_bound:
                missing_parts.append("no explicit applicability scope boundary")
            if missing_parts:
                unsubstantiated_gap_count += 1
                add_finding(
                    code="UNSUBSTANTIATED_GAP_CLAIM",
                    severity="blocking",
                    section=sec,
                    line=ln,
                    quote=sent,
                    message=f"Unbounded/unsubstantiated gap or novelty claim ('{gm.group(0)}'): {'; '.join(missing_parts)}.",
                    remediation=(
                        "Remove absolute 'first ever / no prior work / 尚无研究' phrasing; derive the gap explicitly "
                        "from concrete cited limitations or condition conflicts and state the exact applicability scope."
                    ),
                )

    # =========================================================================
    # 4. Contribution Items <-> Methods/Figures/Tables/Results 1:1 Alignment
    # =========================================================================
    contribution_items: list[dict[str, Any]] = []
    in_contrib_block = False
    for ln, txt in sec_lines["introduction"]:
        if CONTRIB_HEADER_RE.search(txt):
            in_contrib_block = True
        bm = CONTRIB_BULLET_RE.match(txt)
        if bm and (in_contrib_block or re.search(r"\b(?:propose|develop|introduce|design|construct|validate|demonstrate|framework|dataset|benchmark|algorithm)\b|提出|构建|设计|验证|方法|实验|框架", txt, re.I)):
            contribution_items.append({"line": ln, "text": bm.group(1).strip()})
        elif in_contrib_block and not bm and len(contribution_items) > 0:
            in_contrib_block = False

    # Fallback: extract explicit contribution sentences if not bulleted
    if not contribution_items:
        for ln, txt in sec_lines["introduction"]:
            for sent in [s.strip() for s in SENT_SPLIT_RE.split(txt) if s.strip()]:
                if re.search(r"\b(?:we\s+(?:propose|introduce|develop|construct|validate|demonstrate)|our\s+(?:first|second|third|main)\s+contribution)\b|我们(?:提出|构建|验证)了", sent, re.I):
                    contribution_items.append({"line": ln, "text": sent})

    contribution_alignment: list[dict[str, Any]] = []
    overclaimed_count = 0
    for idx, item in enumerate(contribution_items, 1):
        cid = f"CONTRIB-{idx:02d}"
        ctoks = _tokenize(item["text"])
        ev_toks = own_ev["tokens"]
        overlap = sorted(ctoks & ev_toks)
        coverage = len(overlap) / max(1, len(ctoks)) if ctoks else 0.0

        # Check figure/table references cited inside the contribution
        cited_fts = []
        missing_fts = []
        for m in wio.RE_FIG_REF.finditer(item["text"]):
            ref = (m.group(1) or m.group(2)).strip()
            num_m = re.search(r"s?\d+[a-z]?", ref.lower())
            if num_m:
                kind = "figure" if ("fig" in ref.lower() or "图" in ref) else "table"
                norm_id = f"{kind}_{num_m.group(0)}"
                cited_fts.append(ref)
                if own_ev["figure_table_ids"] and norm_id not in own_ev["figure_table_ids"]:
                    missing_fts.append(ref)

        # Check numeric claims in contribution against own evidence numbers (excluding Fig/Table label digits)
        text_no_fig_labels = wio.RE_FIG_REF.sub(" ", item["text"])
        item_nums = set(wio.extract_invariants(text_no_fig_labels)["bare_numbers"])
        ev_nums = set(own_ev["invariants"]["bare_numbers"])
        ungrounded_nums = sorted(item_nums - ev_nums) if ev_nums else []

        status = "VERIFIED_1_TO_1"
        reasons: list[str] = []
        if not own_ev["files"]:
            status = "UNVERIFIABLE_NO_EVIDENCE_FILES"
            reasons.append("No author Methods/Results/Figure/Table files supplied to verify contribution.")
        else:
            if coverage < 0.20:
                status = "OVERCLAIMED_CONTRIBUTION"
                reasons.append(f"Low conceptual overlap ({coverage:.2f}) with supplied Methods/Results files.")
            if missing_fts:
                status = "OVERCLAIMED_CONTRIBUTION"
                reasons.append(f"Referenced Fig/Table not found in supplied evidence: {missing_fts}.")
            if ungrounded_nums:
                status = "OVERCLAIMED_CONTRIBUTION"
                reasons.append(f"Quantitative value(s) {ungrounded_nums} in contribution absent from supplied results.")

        contribution_alignment.append({
            "contribution_id": cid,
            "line": item["line"],
            "text": item["text"][:220],
            "token_coverage": round(coverage, 3),
            "matched_tokens_sample": overlap[:8],
            "cited_fig_tables": cited_fts,
            "missing_fig_tables": missing_fts,
            "ungrounded_numbers": ungrounded_nums,
            "alignment_status": status,
            "reasons": reasons,
        })
        if status == "OVERCLAIMED_CONTRIBUTION":
            overclaimed_count += 1
            add_finding(
                code="OVERCLAIMED_CONTRIBUTION",
                severity="blocking",
                section="introduction",
                line=item["line"],
                quote=item["text"],
                message=f"Contribution {cid} is not substantiated by supplied Methods/Results/Figure/Table evidence: {'; '.join(reasons)}",
                remediation=f"Either supply the supporting Methods/Results artifact or narrow/remove {cid} so contributions match verified evidence 1:1.",
            )

    if not contribution_items:
        add_finding(
            code="MISSING_ITEMIZED_CONTRIBUTIONS",
            severity="major",
            section="introduction",
            line=sec_lines["introduction"][-1][0] if sec_lines["introduction"] else 1,
            quote=(sec_lines["introduction"][-1][1] if sec_lines["introduction"] else ""),
            message="Introduction lacks explicit, verifiable itemized contributions.",
            remediation="Add 2-4 concise contribution bullets anchored 1:1 to supplied Methods, Figures, Tables, and Results.",
        )

    # =========================================================================
    # 5. Citation Coverage & Verification against Supplied Bibliography
    # =========================================================================
    uncovered_refs: list[str] = []
    for e in lit_entries:
        if not _entry_is_cited(e, draft_text, draft_cites):
            uncovered_refs.append(e["cite_key"])
    if uncovered_refs:
        add_finding(
            code="UNCOVERED_SUPPLIED_REFERENCES",
            severity="major",
            section="related_work",
            line=rw_scan_lines[0][0] if rw_scan_lines else 1,
            quote=f"Uncited supplied references: {', '.join(uncovered_refs[:6])}",
            message=f"{len(uncovered_refs)} supplied literature source(s) are not cited in the draft: {', '.join(uncovered_refs[:6])}.",
            remediation="Integrate missing key supplied references into the comparative taxonomy or document explicit exclusion scope.",
        )

    # Verify draft citations exist in supplied corpus when corpus is supplied
    unverified_cites: list[str] = []
    if lit_entries:
        lit_blob = " ".join(f"{e['cite_key']} {e['author']} {e['year']} {e['doi']} {e['title']}" for e in lit_entries).lower()
        for c in draft_cites:
            cl = c.lower()
            parts = [p for p in re.split(r"[\s,;.\[\]]+", cl) if len(p) >= 3]
            if cl not in lit_blob and not (parts and all(p in lit_blob for p in parts[:2])):
                unverified_cites.append(c)
        if unverified_cites:
            add_finding(
                code="UNVERIFIED_CITATION",
                severity="blocking",
                section="related_work",
                line=1,
                quote=", ".join(unverified_cites[:6]),
                message=f"Draft cites {len(unverified_cites)} source(s) absent from supplied bibliography/corpus: {', '.join(unverified_cites[:6])}.",
                remediation="Remove fabricated/unverified citations or attach exact verified bibliography records from supplied sources.",
            )

    # =========================================================================
    # 6. Citation Paraphrase Semantic Fidelity Audit
    # =========================================================================
    paraphrase_fidelity = _audit_citation_paraphrase_fidelity(all_lines, lit_entries)
    for pf in paraphrase_fidelity["items"]:
        for iss in pf["issues"]:
            add_finding(
                code=iss["code"],
                severity="blocking",
                section=pf["section"],
                line=pf["line"],
                quote=pf["draft_sentence"],
                message=iss["detail"],
                remediation=(
                    f"Revise the paraphrase of {pf['cite_key']} to match the exact direction, quantitative value, "
                    f"and scope condition ({pf['source_scope'] or 'stated in source'}) in {pf['source_file']}."
                ),
            )

    if not lit_entries:
        gaps.append({"code": "NO_BIBLIOGRAPHY_CORPUS_SUPPLIED", "severity": "major", "detail": "No structured bibliography/literature files found; citation grounding could not be verified externally."})
    if not own_ev["files"]:
        gaps.append({"code": "NO_AUTHOR_EVIDENCE_FILES_SUPPLIED", "severity": "major", "detail": "No author Methods/Results/Figure/Table files supplied for 1:1 contribution verification."})

    # =========================================================================
    # 7. Manuscript Cleanliness Gate (strip meta-commentary, script paths, audit jargon)
    # =========================================================================
    cleanliness = check_and_clean_manuscript(draft_text)
    for fl in cleanliness["flagged_lines"]:
        add_finding(
            code="MANUSCRIPT_META_POLLUTION",
            severity="blocking",
            section="manuscript_cleanliness",
            line=fl["line"],
            quote=fl["excerpt"],
            message=f"Submission-facing manuscript contains non-manuscript artifact ({', '.join(fl['categories'])}).",
            remediation="Use the cleaned submission-ready prose (introduction_and_related_work_clean.md) with all process commentary, script paths, and audit jargon stripped.",
        )

    prose_audit = wio.audit_academic_prose(cleanliness["cleaned_text"])
    blocking_n = sum(1 for f in findings if f["severity"] == "blocking")

    taxonomy_payload = {
        "case_id": case_id,
        "task_id": task_id,
        "draft_file": draft_path.name,
        "read_method": read_meta.get("method", "plain_text"),
        "task_constraints_applied": (case_contract or {}).get("task_constraints", {}),
        "four_beat_opening": four_beats,
        "four_beat_complete": all(four_beats.values()),
        "chronological_stitching_detected": chronological_stitching,
        "supplied_literature_count": len(lit_entries),
        "supplied_literature_conflict_detected": supplied_lit_conflict,
        "draft_acknowledges_conflict": draft_acknowledges_conflict,
        "taxonomy_by_method_route": taxonomy_groups,
        "unsubstantiated_gap_claims_count": unsubstantiated_gap_count,
        "contribution_alignment": contribution_alignment,
        "overclaimed_contributions_count": overclaimed_count,
        "citation_paraphrase_fidelity": paraphrase_fidelity,
        "manuscript_cleanliness": {
            "clean": cleanliness["clean"],
            "flagged_count": cleanliness["flagged_count"],
            "meta_commentary_lines_found": cleanliness["meta_commentary_lines_found"],
            "script_path_leaks_found": cleanliness["script_path_leaks_found"],
            "audit_jargon_leaks_found": cleanliness["audit_jargon_leaks_found"],
            "flagged_lines": cleanliness["flagged_lines"],
        },
        "citation_coverage": {
            "draft_citations_detected": draft_cites,
            "uncovered_supplied_references": uncovered_refs,
            "unverified_citations": unverified_cites,
            "coverage_ratio": round((len(lit_entries) - len(uncovered_refs)) / max(1, len(lit_entries)), 3) if lit_entries else None,
        },
        "prose_audit_summary": {
            "clean": prose_audit["clean"],
            "high_count": prose_audit["high_count"],
            "warning_count": prose_audit["warning_count"],
        },
        "blocking_count": blocking_n,
        "findings_count": len(findings),
        "findings": findings,
    }

    return {
        "taxonomy": taxonomy_payload,
        "cleaned_manuscript_text": cleanliness["cleaned_text"],
        "invariants": wio.extract_invariants(draft_text),
        "evidence_map": evidence_map,
        "gaps": gaps,
        "own_evidence_files": own_ev["files"],
        "lit_sources": sorted({e["source_file"].split(":")[0] for e in lit_entries}),
    }


def render_alignment_md(tax: dict[str, Any]) -> str:
    lines = [
        f"# Introduction & Related Work Taxonomy and Contribution Alignment ({tax['case_id']})",
        "",
        f"- **Draft Audited**: `{tax['draft_file']}`",
        f"- **Press-Release Four-Beat Opening**: Problem=`{tax['four_beat_opening']['problem']}`, Gap=`{tax['four_beat_opening']['gap']}`, Approach=`{tax['four_beat_opening']['approach']}`, Key Finding=`{tax['four_beat_opening']['key_finding']}`",
        f"- **Chronological Laundry List Detected**: `{tax['chronological_stitching_detected']}`",
        f"- **Unsubstantiated Gap Claims**: `{tax['unsubstantiated_gap_claims_count']}` | **Overclaimed Contributions**: `{tax['overclaimed_contributions_count']}`",
        f"- **Total Findings**: `{tax['findings_count']}` (Blocking: `{tax['blocking_count']}`)",
        "",
        "## 1. Related Work Comparative Taxonomy Matrix",
        "",
        "| Comparison Axis (Method Route) | Cite Key | Year | Scope / Condition | Outcome Direction | Limitation | Cited in Draft? |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not tax["taxonomy_by_method_route"]:
        lines.append("| (none) | - | - | No structured literature entries supplied | - | - | - |")
    else:
        for route, items in tax["taxonomy_by_method_route"].items():
            for it in items:
                lines.append(
                    f"| {route} | {it['cite_key']} | {it['year'] or '-'} | {it['scope_condition'][:60]} | "
                    f"{it['direction']} | {it['limitation'][:60]} | {'Yes' if it['cited_in_draft'] else 'NO'} |"
                )

    lines.extend([
        "",
        "## 2. Contribution-to-Evidence 1:1 Alignment Matrix",
        "",
        "| ID | Line | Alignment Status | Token Coverage | Cited Fig/Table | Contribution Excerpt | Diagnostics |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
    ])
    if not tax["contribution_alignment"]:
        lines.append("| - | - | MISSING | 0.0 | - | No itemized contributions extracted | Add explicit verifiable contribution bullets |")
    else:
        for c in tax["contribution_alignment"]:
            fts = ", ".join(c["cited_fig_tables"]) if c["cited_fig_tables"] else "-"
            diag_s = "; ".join(c["reasons"]) if c["reasons"] else "Aligned with supplied evidence"
            lines.append(
                f"| {c['contribution_id']} | L{c['line']} | `{c['alignment_status']}` | {c['token_coverage']:.2f} | "
                f"{fts} | {c['text'].replace('|', '/')[:90]} | {diag_s.replace('|', '/')} |"
            )

    lines.extend([
        "",
        "## 3. Audit Findings & Required Revisions",
        "",
    ])
    if not tax["findings"]:
        lines.append("All automated four-beat, dimension-grouping, bounded-gap, citation-coverage, and 1:1 contribution checks passed.")
    else:
        for f in tax["findings"]:
            lines.extend([
                f"### [{f['severity'].upper()}] {f['finding_id']} — `{f['code']}` (`{f['section']}` L{f['line']})",
                f"- **Excerpt**: \"{f['quote']}\"",
                f"- **Issue**: {f['message']}",
                f"- **Remediation**: {f['remediation']}",
                "",
            ])
    return "\n".join(lines)


def render_related_work_taxonomy_md(tax: dict[str, Any]) -> str:
    """Render an embeddable Related Work comparative taxonomy & paraphrase-fidelity Markdown sheet."""
    lines = [
        f"# Related Work Comparative Taxonomy & Paraphrase Fidelity ({tax['case_id']})",
        "",
        "Ready-to-embed dimension-grouped comparison matrix derived strictly from supplied literature files.",
        "",
    ]
    if tax.get("task_constraints_applied"):
        tc = ", ".join(f"{k}={v}" for k, v in tax["task_constraints_applied"].items())
        lines.append(f"- **Task Constraints Consumed**: {tc}")
    lines.extend([
        f"- **Press-Release Four-Beat Status**: Problem=`{tax['four_beat_opening']['problem']}` | Gap=`{tax['four_beat_opening']['gap']}` | Approach=`{tax['four_beat_opening']['approach']}` | Key Finding=`{tax['four_beat_opening']['key_finding']}`",
        f"- **Supplied Literature**: `{tax['supplied_literature_count']}` entries across `{len(tax['taxonomy_by_method_route'])}` method/theme axes",
        f"- **Paraphrase Semantic Fidelity**: `{tax['citation_paraphrase_fidelity']['faithful_count']}/{tax['citation_paraphrase_fidelity']['audited_pairs']}` faithful (`{tax['citation_paraphrase_fidelity']['distorted_count']}` distortion(s) intercepted)",
        "",
        "## Table 1. Dimension-Grouped Related Work Comparison (Embeddable)",
        "",
        "| Method / Theme Route | Reference | Year | Applicability Scope / Regime | Reported Finding (Direction) | Documented Limitation | Cited? |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    if not tax["taxonomy_by_method_route"]:
        lines.append("| (none) | - | - | No structured literature supplied | - | - | - |")
    else:
        for route, items in tax["taxonomy_by_method_route"].items():
            for it in items:
                fnd = it["finding_excerpt"].replace("|", "/").replace("\n", " ")[:95]
                lim = it["limitation"].replace("|", "/").replace("\n", " ")[:70]
                scp = it["scope_condition"].replace("|", "/").replace("\n", " ")[:60]
                lines.append(
                    f"| {route} | {it['cite_key']} | {it['year'] or '-'} | {scp} | {fnd} ({it['direction']}) | {lim} | {'Yes' if it['cited_in_draft'] else 'NO'} |"
                )

    lines.extend([
        "",
        "## Table 2. Citation Paraphrase Semantic Fidelity Audit",
        "",
        "| Cite Key | Draft Line | Status | Source Direction vs Draft | Source Scope Boundary | Diagnostic |",
        "| --- | ---: | --- | --- | --- | --- |",
    ])
    pf_items = tax["citation_paraphrase_fidelity"]["items"]
    if not pf_items:
        lines.append("| - | - | N/A | - | - | No matched citation-source pairs found to audit |")
    else:
        for it in pf_items:
            diag = "; ".join(x["code"] + ": " + x["detail"] for x in it["issues"]) if it["issues"] else "Consistent in direction, value, and scope"
            lines.append(
                f"| {it['cite_key']} | L{it['line']} | `{it['fidelity_status']}` | {it['source_direction']} vs {it['draft_direction']} | "
                f"{(it['source_scope'] or 'unspecified').replace('|', '/')[:55]} | {diag.replace('|', '/')[:120]} |"
            )

    lines.extend([
        "",
        "## Bounded Research Gap Synthesis Template",
        "- **Divergence / Shared Bottleneck**: " + (
            "Supplied literature exhibits conflicting outcomes across narrow experimental scopes; explicitly reconcile scope boundaries before stating the gap."
            if tax["supplied_literature_conflict_detected"]
            else "Derive the gap strictly from the shared limitations tabulated in Column 6 above, avoiding absolute 'first ever / no prior work' claims."
        ),
        "- **Four-Beat Opening Checklist**: Ensure Introduction opens with concrete Problem -> Cited Gap -> Proposed Approach -> Quantitative Key Finding.",
        "",
    ])
    return "\n".join(lines)


def _discover_inputs(input_dir: Path) -> tuple[Path, str]:
    case_id = "case-01"
    cj = input_dir / "case.json"
    if cj.is_file():
        try:
            cobj = json.loads(cj.read_text(encoding="utf-8"))
            case_id = str(cobj.get("case_id") or case_id)
            for key in ("draft", "manuscript", "primary_manuscript", "input_file"):
                val = cobj.get(key)
                if isinstance(val, str) and (input_dir / val.strip()).is_file():
                    return input_dir / val.strip(), case_id
        except Exception:
            pass
    cands = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in {".md", ".txt", ".tex", ".docx", ".pdf", ".xml", ".html", ".htm"}
        and p.name.lower() not in {"task.md", "readme.md"}
        and not any(k in p.name.lower() for k in ("bib", "ref", "lit"))
    ]
    if not cands:
        raise FileNotFoundError(f"No draft file found under {input_dir}")
    # Prefer file explicitly named intro / draft / manuscript
    cands.sort(key=lambda p: (0 if any(k in p.name.lower() for k in ("intro", "draft", "manuscript", "related")) else 1, -p.stat().st_size))
    return cands[0], case_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Introduction & Related Work four-beat, taxonomy, gap, paraphrase-fidelity, and contribution auditor.")
    parser.add_argument("--draft", default="", help="Introduction/Related Work draft path (.md/.tex/.txt/.docx/.pdf/.xml/.html).")
    parser.add_argument("--bib", default="", help="Optional structured literature/bibliography file (.json/.csv/.bib/.md/.xml/.html/.pdf).")
    parser.add_argument("--corpus-dir", default="", help="Directory containing literature sources and/or author evidence files.")
    parser.add_argument("--evidence-dir", default="", help="Directory containing author Methods/Results/Figure/Table files.")
    parser.add_argument("--input-dir", default="", help="Auto-discover draft, literature, and evidence under /input.")
    parser.add_argument("--out-dir", required=True, help="Output directory for taxonomy JSON/MD, alignment MD, and result.json.")
    parser.add_argument("--case-id", default="", help="Override case_id.")
    args = parser.parse_args(argv)

    in_dir: Path | None = Path(args.input_dir) if args.input_dir else None
    if in_dir:
        draft_p, det_cid = _discover_inputs(in_dir)
        case_contract = wio.load_case_contract(in_dir)
        case_id = args.case_id or case_contract.get("case_id") or det_cid
        corpus_p = Path(args.corpus_dir) if args.corpus_dir else in_dir
        ev_p = Path(args.evidence_dir) if args.evidence_dir else in_dir
    elif args.draft:
        draft_p = Path(args.draft)
        corpus_p = Path(args.corpus_dir) if args.corpus_dir else None
        ev_p = Path(args.evidence_dir) if args.evidence_dir else corpus_p
        contract_dir = corpus_p if (corpus_p and ((corpus_p / "case.json").is_file() or (corpus_p / "task.md").is_file())) else draft_p.parent
        case_contract = wio.load_case_contract(contract_dir)
        case_id = args.case_id or case_contract.get("case_id") or draft_p.stem
    else:
        parser.error("Provide --draft or --input-dir.")

    bib_p = Path(args.bib) if args.bib else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    res = audit_intro_and_related_work(
        draft_p,
        bib_path=bib_p,
        corpus_dir=corpus_p,
        evidence_dir=ev_p,
        case_id=case_id,
        case_contract=case_contract,
    )
    tax = res["taxonomy"]
    align_md = render_alignment_md(tax)
    tax_md = render_related_work_taxonomy_md(tax)

    (out_dir / "related_work_taxonomy.json").write_text(json.dumps(tax, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "related_work_taxonomy.md").write_text(tax_md, encoding="utf-8")
    (out_dir / "gap_contribution_alignment.md").write_text(align_md, encoding="utf-8")
    clean_ms_name = "introduction_and_related_work_clean.md"
    (out_dir / clean_ms_name).write_text(res["cleaned_manuscript_text"], encoding="utf-8")

    sources_list = [{"file": draft_p.name, "role": "draft_manuscript", "read_method": tax.get("read_method")}]
    for s in res["lit_sources"]:
        sources_list.append({"file": s, "role": "supplied_literature"})
    for s in res["own_evidence_files"]:
        sources_list.append({"file": s, "role": "author_methods_results_evidence"})

    artifacts_list = [
        "result.json",
        clean_ms_name,
        "related_work_taxonomy.json",
        "related_work_taxonomy.md",
        "gap_contribution_alignment.md",
    ]
    result_payload = wio.build_writing_result(
        case_id=case_id,
        task_id="introduction-related-work-writing",
        deliverable={
            "type": "introduction_related_work_taxonomy_and_gap_alignment",
            "manuscript_clean_md": clean_ms_name,
            "manuscript_cleanliness_passed": tax["manuscript_cleanliness"]["clean"],
            "taxonomy_json": "related_work_taxonomy.json",
            "taxonomy_md": "related_work_taxonomy.md",
            "alignment_md": "gap_contribution_alignment.md",
            "four_beat_complete": tax["four_beat_complete"],
            "paraphrase_distorted_count": tax["citation_paraphrase_fidelity"]["distorted_count"],
            "blocking_count": tax["blocking_count"],
        },
        preserved_facts={
            "draft_file": draft_p.name,
            "numbers_with_units_count": len(res["invariants"]["numbers_with_units"]),
            "dois": res["invariants"]["dois"],
            "figure_table_refs": res["invariants"]["figure_table_refs"],
            "draft_citations_count": len(tax["citation_coverage"]["draft_citations_detected"]),
        },
        evidence_map=res["evidence_map"],
        change_log=[
            {"finding_id": f["finding_id"], "code": f["code"], "line": f["line"], "status": "audit_flagged"}
            for f in tax["findings"]
        ],
        artifacts=artifacts_list,
        sources=sources_list,
        gaps=res["gaps"],
        limitations=[
            "Automated checks verify lexical/structural grounding of gaps, citations, paraphrase fidelity, and contributions against supplied files; deep domain novelty remains bounded strictly by the supplied literature corpus.",
        ],
        verification={
            "engine": "intro_related_work_auditor.py",
            "four_beat_complete": tax["four_beat_complete"],
            "chronological_stitching_detected": tax["chronological_stitching_detected"],
            "unsubstantiated_gap_claims_count": tax["unsubstantiated_gap_claims_count"],
            "overclaimed_contributions_count": tax["overclaimed_contributions_count"],
            "paraphrase_audited_pairs": tax["citation_paraphrase_fidelity"]["audited_pairs"],
            "paraphrase_distorted_count": tax["citation_paraphrase_fidelity"]["distorted_count"],
            "manuscript_cleanliness_passed": tax["manuscript_cleanliness"]["clean"],
            "manuscript_cleanliness_flagged_count": tax["manuscript_cleanliness"]["flagged_count"],
            "unverified_citations_count": len(tax["citation_coverage"]["unverified_citations"]),
            "blocking_count": tax["blocking_count"],
            "task_constraints_consumed": bool(case_contract.get("task_constraints")),
        },
        case_contract=case_contract,
    )
    (out_dir / "result.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "four_beat_complete": tax["four_beat_complete"],
        "chronological_stitching_detected": tax["chronological_stitching_detected"],
        "unsubstantiated_gap_claims": tax["unsubstantiated_gap_claims_count"],
        "overclaimed_contributions": tax["overclaimed_contributions_count"],
        "paraphrase_audited_pairs": tax["citation_paraphrase_fidelity"]["audited_pairs"],
        "paraphrase_distorted": tax["citation_paraphrase_fidelity"]["distorted_count"],
        "manuscript_cleanliness_passed": tax["manuscript_cleanliness"]["clean"],
        "blocking_count": tax["blocking_count"],
        "findings_count": tax["findings_count"],
        "artifacts": artifacts_list,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
